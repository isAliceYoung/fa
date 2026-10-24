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
import stat
import subprocess
import tarfile
import tempfile

# Legacy development source. Artifacts built for any other branch carry their
# real branch name and are only trusted through release_trust.py (Phase 4B-1).
BRANCH = 'feature/private-deploy-isolation'
NODE = '22.23.2'
MAX_BYTES = 2 * 1024**3
ROOT_UID = 0  # tests only replace this; runtime trees must be root-owned


def require(value, message="Deployment safety check failed"):
    # Runtime validation must survive python -O/-OO.
    if not value:
        raise ValueError(message)


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


def valid_branch(branch):
    return bool(isinstance(branch, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,99}', branch)
                and '..' not in branch and '//' not in branch and not branch.endswith(('/', '.', '.lock')))


def reject_acl(path):
    if hasattr(os, 'listxattr'):
        require(not {'system.posix_acl_access', 'system.posix_acl_default'} & set(os.listxattr(path, follow_symlinks=False)), 'Extended ACL requires explicit review: '+str(path))


def verify_tree(app, manifest, gid, extra_files=frozenset()):
    """Exact immutable runtime tree (Phase 4A-R1 rules) for an installed or prepared release.

    Every manifest file: root owner, the service group, one link, exact 0640/0750,
    no ACL, size and SHA256. Packaged links: exact reviewed in-package target.
    Directories: root:<gid> 0750 without ACL. Unknown or missing entries refuse.
    """
    expected_files = set(extra_files); expected_dirs = {'.'}
    for name, item in manifest['files'].items():
        if not name.startswith('app/'):
            continue
        safe_name(name)
        relative = Path(name[4:]); expected_files.add(relative.as_posix())
        expected_dirs.update(p.as_posix() for p in relative.parents)
        p = app/relative; s = p.lstat()
        reject_acl(p)
        require(s.st_uid == ROOT_UID, 'Runtime is not root-managed')
        if item['type'] == 'symlink':
            # Linux symlink mode is always 0777 and is not access control.
            # Root ownership + exact reviewed in-package target remain required.
            link_target(name, item['target'])
            require(p.is_symlink() and os.readlink(p) == item['target'], 'Runtime link differs')
        else:
            mode = 0o750 if item.get('mode', 0) & 0o111 else 0o640
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_gid == gid and stat.S_IMODE(s.st_mode) == mode and s.st_size == item['size'] and digest(p) == item['sha256'], 'Runtime differs: '+name)
    actual_files = set(); actual_dirs = set()
    def walk_error(error): raise error
    for directory, dirs, files in os.walk(app, followlinks=False, onerror=walk_error):
        base = Path(directory); s = base.lstat(); reject_acl(base)
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == ROOT_UID and s.st_gid == gid and stat.S_IMODE(s.st_mode) == 0o750, 'Runtime directory permissions differ')
        actual_dirs.add(base.relative_to(app).as_posix())
        for name in dirs+files:
            p = base/name; relative = p.relative_to(app).as_posix()
            if p.is_symlink() or name in files: actual_files.add(relative)
        dirs[:] = [name for name in dirs if not (base/name).is_symlink()]
    require(actual_files == expected_files and actual_dirs == expected_dirs, 'Unknown/missing immutable runtime entry; preserve and review')


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


def create(repo, output, commit, node, branch=BRANCH):
    repo = repo.resolve()
    require(re.fullmatch('[0-9a-f]{40}', commit))
    require(valid_branch(branch), 'Invalid source branch name')
    require(subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip() == commit)
    require(Path('/etc/os-release').read_text().find('VERSION_ID="12"') >= 0)
    require(subprocess.check_output([node, '-p', 'process.versions.node'], text=True).strip() == NODE)
    files = {}
    roots = ['.next', 'node_modules', 'public', 'package.json', 'package-lock.json', 'next.config.ts',
             'server.mjs', 'scripts/network-policy.mjs', 'scripts/internal-http.mjs',
             'scripts/ws-origin-policy.mjs', 'scripts/http-boundary.mjs',
             'src/lib/pty-gateway.mjs', 'src/lib/gui-gateway.mjs', 'src/lib/resource-monitor.mjs', 'src/lib/status-admission.mjs', 'src/lib/text-clipboard.mjs', 'src/lib/image-clipboard.mjs', 'src/db/schema.ts', 'drizzle.config.ts', 'drizzle/migrations']
    for name in roots:
        path = repo/name
        require(path.exists(), 'Build input missing: '+name)
        if path.is_dir():
            for directory, dirs, names in os.walk(path, followlinks=False):
                for child in dirs:
                    p=Path(directory)/child
                    if p.is_symlink():
                        files['app/'+p.relative_to(repo).as_posix()]=p
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
        manifest = dict(schema=1, commit=commit, branch=branch, os='debian12', arch='amd64',
                        node=NODE, node_modules_abi='127', glibc='2.36', files={})
        for name, path in sorted(files.items()):
            safe_name(name)
            if path.is_symlink():
                # Turbopack emits hashed package DIRECTORY links. Preserve them,
                # relocating only targets that resolve inside this exact payload.
                resolved=path.resolve(strict=True)
                relative=resolved.relative_to(repo).as_posix()
                mapped='app/'+relative
                require(any(n==mapped or n.startswith(mapped+'/') for n in files), 'Link target is not packaged')
                target=os.path.relpath(mapped,str(PurePosixPath(name).parent)).replace(os.sep,'/')
                link_target(name, target)
                item = dict(type='symlink', target=target)
            else:
                if name.startswith('app/.next/') and seeds:
                    data = path.read_bytes()
                    require(not any(seed in data for seed in seeds), 'Build contains a CI secret; refusing artifact')
                item = dict(type='file', size=path.stat().st_size, sha256=digest(path), mode=path.stat().st_mode & 0o777)
            manifest['files'][name] = item
        require(sum(x.get('size',0) for x in manifest['files'].values()) <= MAX_BYTES)
        with tarfile.open(output, 'w:gz', dereference=True) as archive:
            data = json.dumps(manifest, sort_keys=True).encode()
            inner = hashlib.sha256(data).hexdigest()
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
    print(json.dumps(dict(commit=commit, branch=branch, sha256=digest(output), inner_manifest_sha256=inner, archive=str(output), bytes=Path(output).stat().st_size)))


def extract(archive_path, expected_digest, commit, destination, branch=BRANCH, inner_sha256=None):
    """branch/inner_sha256 come from the caller's trust decision (legacy default,
    or a signed release manifest verified by release_trust.py), never from the archive."""
    require(valid_branch(branch), 'Invalid expected branch')
    require(inner_sha256 is None or re.fullmatch('[0-9a-f]{64}', inner_sha256), 'Invalid inner manifest digest')
    require(Path(archive_path).stat().st_size <= 450*1024**2, 'Artifact compressed size exceeds budget')
    require(re.fullmatch('[0-9a-f]{64}', expected_digest) and digest(archive_path) == expected_digest, 'Artifact checksum mismatch')
    require(not destination.exists(), 'Extraction destination exists; no overwrite')
    with tarfile.open(archive_path, 'r:gz') as archive:
        members=archive.getmembers()
        require(len(members) <= 150000 and len({m.name for m in members}) == len(members), 'Duplicate or excessive entries')
        first=archive.getmember('manifest.json')
        require(first.isfile() and first.size <= 32*1024**2)
        raw=archive.extractfile(first).read()
        require(inner_sha256 is None or hashlib.sha256(raw).hexdigest()==inner_sha256, 'Inner manifest differs from the signed release')
        manifest=json.loads(raw)
        for key,value in dict(schema=1,commit=commit,branch=branch,os='debian12',arch='amd64',node=NODE,node_modules_abi='127',glibc='2.36').items():
            require(manifest.get(key)==value, 'Artifact platform/version/commit mismatch: '+key)
        entries=manifest['files']
        require(set(entries)=={m.name for m in members if m.name!='manifest.json'}, 'Manifest entries mismatch')
        require(sum(m.size for m in members) <= MAX_BYTES, 'Unpacked size exceeds budget')
        # Validate the WHOLE archive before any extraction. No hard links, devices,
        # arbitrary metadata, writable executables, or symlink parent traversal.
        symlinks={name for name,item in entries.items() if item['type']=='symlink'}
        for m in members:
            if m.name=='manifest.json': continue
            safe_name(m.name)
            require(not any(p.as_posix() in symlinks for p in PurePosixPath(m.name).parents), 'Symlink ancestor')
            item=entries[m.name]
            if m.issym():
                require(item['type']=='symlink' and item['target']==m.linkname)
                link_target(m.name,m.linkname)
            else:
                require(m.isfile() and item['type']=='file' and item['size']==m.size)
                require(re.fullmatch('[0-9a-f]{64}',item['sha256']))
        destination.mkdir(mode=0o700)
        for m in members:
            if m.name=='manifest.json' or m.issym(): continue
            target=destination/m.name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            h=hashlib.sha256()
            with archive.extractfile(m) as source, target.open('xb') as output:
                for block in iter(lambda:source.read(1024*1024),b''):
                    h.update(block); output.write(block)
            require(h.hexdigest()==entries[m.name]['sha256'], 'File checksum mismatch')
            target.chmod(0o755 if entries[m.name].get('mode',0) & 0o111 else 0o644)
        for name in sorted(symlinks):
            target=destination/name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            target.symlink_to(entries[name]['target'])
        # Exact archive bytes, so a prepared release can re-prove its signed digest.
        (destination/'manifest.json').write_bytes(raw)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--commit',required=True); p.add_argument('--node',required=True)
    p.add_argument('--branch',default=BRANCH,help='Actual source branch recorded in the artifact (trust comes only from release_trust.py)'); a=p.parse_args()
    create(a.repo,a.output,a.commit,a.node,a.branch)
