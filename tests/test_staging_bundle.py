"""Archive attacks are rejected before extraction; no Linux permission mocks."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('staging_bundle',Path(__file__).resolve().parents[1]/'webdesktop-deploy/scripts/staging_bundle.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SHA='a'*40


class StagingArchiveTests(unittest.TestCase):
    def archive(self,root,change=None):
        data=b'compiled-safe-runtime'
        manifest=dict(schema=1,commit=SHA,branch=m.BRANCH,os='debian12',arch='amd64',node=m.NODE,node_modules_abi='127',glibc='2.36',
                      files={'app/node':dict(type='file',size=len(data),sha256=hashlib.sha256(data).hexdigest(),mode=0o755)})
        names=[('app/node',data,None)]
        if change: change(manifest,names)
        p=Path(root)/'artifact.tar.gz'
        with tarfile.open(p,'w:gz') as tar:
            encoded=json.dumps(manifest).encode();info=tarfile.TarInfo('manifest.json');info.size=len(encoded);tar.addfile(info,io.BytesIO(encoded))
            for name,body,link in names:
                info=tarfile.TarInfo(name)
                if link:
                    info.type,info.linkname=link if isinstance(link,tuple) else (tarfile.SYMTYPE,link)
                    tar.addfile(info)
                else: info.size=len(body);tar.addfile(info,io.BytesIO(body))
        return p

    def reject(self,change=None,digest=None,commit=SHA):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.archive(tmp,change)
            with self.assertRaises((AssertionError,ValueError,KeyError)):
                m.extract(p,digest or m.digest(p),commit,Path(tmp)/'out')

    def test_valid_checked_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.archive(tmp);m.extract(p,m.digest(p),SHA,Path(tmp)/'out')
            self.assertEqual((Path(tmp)/'out/app/node').read_bytes(),b'compiled-safe-runtime')

    def test_independent_outer_checksum(self): self.reject(digest='0'*64)
    def test_pinned_commit(self): self.reject(commit='b'*40)
    def test_wrong_platform(self): self.reject(lambda d,n:d.update(arch='arm64'))
    def test_unlisted_file(self): self.reject(lambda d,n:n.append(('app/.env',b'unsafe',None)))
    def test_duplicate_entries(self): self.reject(lambda d,n:n.append(n[0]))
    def test_path_escape(self):
        def change(d,n): d['files']['../escape']=d['files'].pop('app/node');n[0]=('../escape',n[0][1],None)
        self.reject(change)
    def test_symlink_escape(self):
        def change(d,n): d['files']['app/node']=dict(type='symlink',target='../../outside');n[0]=('app/node',None,'../../outside')
        self.reject(change)
    def test_symlink_ancestor(self):
        def change(d,n): d['files']['app/dir']=dict(type='symlink',target='node');d['files']['app/dir/file']=d['files'].pop('app/node');n[:]=[('app/dir',None,'node'),('app/dir/file',b'compiled-safe-runtime',None)]
        self.reject(change)
    def test_file_checksum(self): self.reject(lambda d,n:d['files']['app/node'].update(sha256='0'*64))
    def test_hardlink_rejected(self):
        self.reject(lambda d,n:n.__setitem__(0,('app/node',None,(tarfile.LNKTYPE,'app/other'))))


class RuntimeInputTests(unittest.TestCase):
    def test_gateway_relative_imports_are_bundled(self):
        # server.mjs runs outside Next: every module it reaches by relative
        # import must be an explicit bundle root, or the service cannot start.
        import os
        import re
        repo=Path(__file__).resolve().parents[1]
        source=(repo/'webdesktop-deploy/scripts/staging_bundle.py').read_text()
        seen,todo=set(),['server.mjs']
        while todo:
            name=todo.pop()
            if name in seen: continue
            seen.add(name)
            for rel in re.findall(r"^import [^;]*? from '(\.{1,2}/[^']+)'",(repo/name).read_text(),re.M):
                todo.append(os.path.normpath(os.path.join(os.path.dirname(name),rel)).replace(os.sep,'/'))
        self.assertIn('src/lib/text-clipboard.mjs',seen)
        for name in seen: self.assertIn("'"+name+"'",source,name)


if __name__=='__main__': unittest.main()
