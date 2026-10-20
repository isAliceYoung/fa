#!/usr/bin/env python3
"""Debian 12 artifact creation and bounded, manifest-checked extraction.

The outer SHA256 MUST come from a trusted CI run, not from this archive itself.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

BRANCH = 'feature/private-deploy-isolation'
NODE = '22.23.2'
MAX_BYTES = 2 * 1024**3


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or name.startswith('/') or '\\' in name or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('Invalid archive path')
    if p.parts[0] not in ('app', 'source.bundle'):
        raise ValueError('Unexpected archive root')
    return p


def link_target(name, link):
    if not link or link.startswith('/') or '\\' in link:
        raise ValueError('Unsafe symbolic link')
    stack = list(PurePosixPath(name).parent.parts)
    for part in link.split('/'):
        if part == '..':
            if len(stack) <= 1:
                raise ValueError('Symbolic link escapes application')
            stack.pop()
        elif part not in ('', '.'):
            stack.append(part)
    if stack[0] != 'app':
        raise ValueError('Unexpected symbolic link domain')


def create(repo, output, commit, node):
    repo = repo.resolve()
    assert re.fullmatch('[0-9a-f]{40}', commit)
    assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip() == commit
    assert Path('/etc/os-release').read_text().find('VERSION_ID="12"') >= 0
    assert subprocess.check_output([node, '-p', 'process.versions.node'], text=True).strip() == NODE
    files = {}
    roots = ['.next', 'node_modules', 'public', 'package.json', 'package-lock.json', 'next.config.ts',
             'server.mjs', 'scripts/network-policy.mjs', 'scripts/internal-http.mjs',
             'scripts/ws-origin-policy.mjs', 'scripts/http-boundary.mjs',
             'src/lib/pty-gateway.mjs', 'src/lib/gui-gateway.mjs', 'src/db/schema.ts', 'drizzle.config.ts']
    for name in roots:
        path = repo/name
        assert path.exists(), 'Build input missing: '+name
        if path.is_dir():
            for directory, dirs, names in os.walk(path, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(directory)/d).is_symlink() and not (name == '.next' and d == 'cache')]
                for child in names:
                    p = Path(directory)/child
                    files['app/'+p.relative_to(repo).as_posix()] = p
        else:
            files['app/'+name] = path
    files['app/node'] = Path(node).resolve()
    seeds = []
    if (repo/'.env').exists():
        for line in (repo/'.env').read_text().splitlines():
            if line.partition('=')[0] in ('DATABASE_URL','ADMIN_PASSWORD','DESKTOP_PASSWORD','AUTH_KEY'):
                value = line.partition('=')[2]
                if len(value) >= 12:
                    seeds.append(value.encode())
    with tempfile.TemporaryDirectory(prefix='wd-staging-package-') as temp:
        bundle = Path(temp)/'source.bundle'
        subprocess.run(['git', '-C', str(repo), 'bundle', 'create', str(bundle), 'HEAD'], check=True)
        files['source.bundle'] = bundle
        manifest = dict(schema=1, commit=commit, branch=BRANCH, os='debian12', arch='amd64',
                        node=NODE, node_modules_abi='127', glibc='2.36', files={})
        for name, path in sorted(files.items()):
            safe_name(name)
            if path.is_symlink():
                target = os.readlink(path)
                link_target(name, target)
                item = dict(type='symlink', target=target)
            else:
                if name.startswith('app/.next/') and seeds:
                    data = path.read_bytes()
                    assert not any(seed in data for seed in seeds), 'Build contains a CI secret; refusing artifact'
                item = dict(type='file', size=path.stat().st_size, sha256=digest(path), mode=path.stat().st_mode & 0o777)
            manifest['files'][name] = item
        assert sum(x.get('size',0) for x in manifest['files'].values()) <= MAX_BYTES
        with tarfile.open(output, 'w:gz', dereference=True) as archive:
            data = json.dumps(manifest, sort_keys=True).encode()
            info = tarfile.TarInfo('manifest.json'); info.size=len(data); info.mode=0o600
            archive.addfile(info, io.BytesIO(data))
            for name, path in sorted(files.items()):
                item = manifest['files'][name]
                info = tarfile.TarInfo(name); info.uid=info.gid=0
                info.mode = 0o755 if item.get('mode',0) & 0o111 else 0o644
                if item['type'] == 'symlink':
                    info.type=tarfile.SYMTYPE; info.linkname=item['target']; archive.addfile(info)
                else:
                    info.size=item['size']
                    with path.open('rb') as stream:
                        archive.addfile(info, stream)
    print(json.dumps(dict(commit=commit, sha256=digest(output), archive=str(output), bytes=Path(output).stat().st_size)))


def extract(archive_path, expected_digest, commit, destination):
    assert re.fullmatch('[0-9a-f]{64}', expected_digest) and digest(archive_path) == expected_digest, 'Artifact checksum mismatch'
    assert Path(archive_path).stat().st_size <= 450*1024**2, 'Artifact compressed size exceeds budget'
    assert not destination.exists(), 'Extraction destination exists; no overwrite'
    with tarfile.open(archive_path, 'r:gz') as archive:
        members=archive.getmembers()
        assert len(members) <= 150000 and len({m.name for m in members}) == len(members), 'Duplicate or excessive entries'
        first=archive.getmember('manifest.json')
        assert first.isfile() and first.size <= 32*1024**2
        manifest=json.load(archive.extractfile(first))
        for key,value in dict(schema=1,commit=commit,branch=BRANCH,os='debian12',arch='amd64',node=NODE,node_modules_abi='127',glibc='2.36').items():
            assert manifest.get(key)==value, 'Artifact platform/version/commit mismatch: '+key
        entries=manifest['files']
        assert set(entries)=={m.name for m in members if m.name!='manifest.json'}, 'Manifest entries mismatch'
        assert sum(m.size for m in members) <= MAX_BYTES, 'Unpacked size exceeds budget'
        # Validate the WHOLE archive before any extraction. No hard links, devices,
        # arbitrary metadata, writable executables, or symlink parent traversal.
        symlinks={name for name,item in entries.items() if item['type']=='symlink'}
        for m in members:
            if m.name=='manifest.json': continue
            safe_name(m.name)
            assert not any(p.as_posix() in symlinks for p in PurePosixPath(m.name).parents), 'Symlink ancestor'
            item=entries[m.name]
            if m.issym():
                assert item['type']=='symlink' and item['target']==m.linkname
                link_target(m.name,m.linkname)
            else:
                assert m.isfile() and item['type']=='file' and item['size']==m.size
                assert re.fullmatch('[0-9a-f]{64}',item['sha256'])
        destination.mkdir(mode=0o700)
        for m in members:
            if m.name=='manifest.json' or m.issym(): continue
            target=destination/m.name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h=hashlib.sha256()
            with archive.extractfile(m) as source, target.open('xb') as output:
                for block in iter(lambda:source.read(1024*1024),b''):
                    h.update(block); output.write(block)
            assert h.hexdigest()==entries[m.name]['sha256'], 'File checksum mismatch'
            target.chmod(0o755 if entries[m.name].get('mode',0) & 0o111 else 0o644)
        for name in sorted(symlinks):
            target=destination/name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            target.symlink_to(entries[name]['target'])
        (destination/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--commit',required=True); p.add_argument('--node',required=True); a=p.parse_args()
    create(a.repo,a.output,a.commit,a.node)
