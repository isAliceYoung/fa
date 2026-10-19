"""Guest-only file transactions. All paths anchored by openat2 and guest DAC."""
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import io
import json
import os
from pathlib import PurePosixPath
import re
import stat
import threading
import time
import uuid
import zipfile
from secure_fs import Workspace, Denied, MAX_FILE

STATE = '.wd-state'
TRASH = '.local/share/Trash/files'
MAX_CHUNK = 16 * 1024 * 1024

class Conflict(Exception):
    pass

class Files(Workspace):
    def __init__(self, home, uid):
        super().__init__(home,uid)
        policy='/usr/local/lib/webdesktop-guest/ssh-denied'
        p=os.lstat(policy)
        if not stat.S_ISREG(p.st_mode) or p.st_uid!=0 or p.st_nlink!=1 or stat.S_IMODE(p.st_mode)!=0o644:
            raise RuntimeError('Root-managed SSH denial required')
        with open(policy,'r',encoding='ascii') as f:
            if f.read(64)!='wdsvc wdguest wdterm\n':raise RuntimeError('SSH denial policy required')
        v=os.fstatvfs(self.root)
        if v.f_blocks*v.f_frsize > 512*1024*1024 or not (v.f_flag & os.ST_NOSUID) or os.stat('/home').st_dev==os.fstat(self.root).st_dev:
            raise RuntimeError('Separate bounded nosuid guest filesystem required; no unbounded fallback')
        # A root-managed bind mount makes the private directory unrenameable by
        # a shell with write access to HOME. Open it once, then retain NO_XDEV
        # for all data and state walks independently. Both are on the SAME fs.
        private = self.home + '/' + STATE
        # ismount() cannot detect bind mounts on the same device.
        with open('/proc/self/mountinfo',encoding='ascii') as f:
            mounted=any(line.split()[4]==private for line in f)
        if not mounted:raise RuntimeError('Root-managed transaction mount required')
        self.state_root=os.open(private,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW)
        s=os.fstat(self.state_root)
        if s.st_uid!=self.uid or stat.S_IMODE(s.st_mode)!=0o700 or s.st_dev!=os.fstat(self.root).st_dev:
            raise RuntimeError('Unsafe private transaction mount: uid={} mode={} same_device={}'.format(s.st_uid,oct(stat.S_IMODE(s.st_mode)),s.st_dev==os.fstat(self.root).st_dev))
        self.ensure_directory(TRASH)
        self.lock=threading.RLock()
        self.lockfd=self.open(STATE+'/agent.lock',os.O_RDWR|os.O_CREAT,0o600)
        self.regular(self.lockfd)
        fcntl.flock(self.lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def relative(self,raw):
        r=super().relative(raw)
        if len(r.split('/'))>32:raise Denied('Path depth limit')
        if any(p.startswith('.wd-') for p in r.split('/')):
            raise Denied('Private transaction directory')
        return r

    @contextmanager
    def guard(self):
        if not self.lock.acquire(timeout=2):raise Conflict('Guest operation busy')
        try:yield
        finally:self.lock.release()

    @contextmanager
    def parent(self,r):
        parts=r.rsplit('/',1)
        d,name=(parts[0],parts[1]) if len(parts)==2 else ('.',parts[0])
        if name in ('','.','..'):raise Denied('Cannot mutate workspace root')
        fd=self.open(d,os.O_RDONLY|os.O_DIRECTORY)
        try:
            if os.fstat(fd).st_uid not in self.data_uids:raise Denied('Guest directory owner required')
            yield fd,name
        finally:os.close(fd)

    def rename(self,src,dst,replace=False):
        with self.parent(src) as (a,x),self.parent(dst) as (b,y):
            libc=ctypes.CDLL(None,use_errno=True)
            if libc.renameat2(a,os.fsencode(x),b,os.fsencode(y),0 if replace else 1):
                e=ctypes.get_errno()
                if e==errno.EXDEV:raise Conflict('Cross-filesystem move is unsupported; source retained')
                raise OSError(e,os.strerror(e))
            os.fsync(a)
            os.fsync(b)

    def unlink(self,r,directory=False):
        with self.parent(r) as (fd,name):
            (os.rmdir if directory else os.unlink)(name,dir_fd=fd)

    def fresh(self):return STATE+'/tmp-'+uuid.uuid4().hex

    def exists(self,r):
        try:
            fd=self.open(r,os.O_PATH)
            os.close(fd)
            return True
        except FileNotFoundError:return False

    def unique(self,parent,name):
        if not isinstance(name,str) or not name or '/' in name or '\\' in name or name in ('.','..') or name.startswith('.wd-') or '\0' in name or len(os.fsencode(name))>255:raise Denied('Invalid name')
        base=PurePosixPath(name)
        for i in range(1,1001):
            n=name if i==1 else base.stem+' ('+str(i)+')'+base.suffix
            r=n if parent=='.' else parent+'/'+n
            if not self.exists(r):return r
        raise Conflict('Too many name collisions')

    def protected(self,r):
        if r in ('.','Desktop','Documents','Downloads','Pictures','.local','.local/share','.local/share/Trash',TRASH,'.local/share/Trash/info'):raise Denied('Protected guest directory')

    def version(self,s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

    def checked(self,r):
        fd=self.open(r,os.O_RDONLY)
        s=os.fstat(fd)
        if s.st_uid not in self.data_uids or (not stat.S_ISDIR(s.st_mode) and (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1)):
            os.close(fd);raise Denied('Guest regular file or directory required')
        return fd,s

    def tree(self,r,depth=0,budget=None):
        b=budget if budget is not None else {'entries':0,'bytes':0,'deadline':time.monotonic()+15}
        if depth>32 or b['entries']>=4000 or time.monotonic()>b['deadline']:raise OverflowError('Tree/time budget exceeded')
        b['entries']+=1
        fd,s=self.checked(r)
        try:
            if not stat.S_ISDIR(s.st_mode):
                b['bytes']+=s.st_size
                if b['bytes']>MAX_FILE and not b.get('delete'):raise OverflowError('Tree exceeds 100 MiB')
            yield r,s
            if stat.S_ISDIR(s.st_mode):
                with os.scandir(fd) as entries:
                    for e in entries:
                        if e.name.startswith('.wd-'):raise Denied('Internal transaction cannot be exported')
                        yield from self.tree(r+'/'+e.name,depth+1,b)
            if self.version(os.fstat(fd))!=self.version(s):raise Conflict('Concurrent modification')
        finally:os.close(fd)

    def copy_tree(self,src,dst,deadline=None):
        deadline=deadline if deadline is not None else time.monotonic()+15
        directories=[]
        for r,s in self.tree(src):
            out=dst+r[len(src):]
            if stat.S_ISDIR(s.st_mode):
                with self.parent(out) as (fd,name):os.mkdir(name,0o2770,dir_fd=fd)
                directories.append(out)
            else:
                a=self.open(r,os.O_RDONLY)
                try:
                    self.regular(a)
                    with os.fdopen(self.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o660),'wb') as target:
                        remaining=s.st_size
                        while remaining:
                            if time.monotonic()>deadline:raise TimeoutError('Copy deadline')
                            data=os.read(a,min(65536,remaining))
                            if not data:raise Conflict('Source changed during copy')
                            target.write(data);remaining-=len(data)
                        target.flush();os.fsync(target.fileno())
                    if self.version(os.fstat(a))!=self.version(s):raise Conflict('Source changed during copy')
                finally:os.close(a)
        for r in reversed(directories):
            fd=self.open(r,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)

    def remove_tree(self,r):
        deadline=time.monotonic()+15
        rows=list(self.tree(r,budget={'entries':0,'bytes':0,'deadline':deadline,'delete':True}))
        for p,s in reversed(rows):
            if time.monotonic()>deadline:raise TimeoutError('Delete deadline')
            self.unlink(p,stat.S_ISDIR(s.st_mode))

    def cleanup(self,r):
        try:self.remove_tree(r)
        except (OSError,Denied,Conflict,OverflowError):pass # Retained within bounded volume; no startup purge.

    def atomic_bytes(self,r,data,replace=False,expected=None):
        tmp=self.fresh()
        try:
            with os.fdopen(self.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:
                f.write(data);f.flush();os.fsync(f.fileno())
            if expected is not None:
                fd,s=self.checked(r)
                os.close(fd)
                if self.version(s)!=expected:raise Conflict('Concurrent target modification')
            if not r.startswith(STATE+'/'):
                fd=self.open(tmp,os.O_RDONLY)
                try:os.fchmod(fd,0o660)
                finally:os.close(fd)
            self.rename(tmp,r,replace)
        finally:self.cleanup(tmp)

    def create(self,raw,name,kind,content=''):
        with self.guard():
            parent=self.relative(raw)
            r=self.unique(parent,name)
            if PurePosixPath(r).name!=name:raise FileExistsError()
            if kind=='folder':
                with self.parent(r) as (fd,n):os.mkdir(n,0o2770,dir_fd=fd)
            elif kind=='file':self.atomic_bytes(r,self.data(content))
            else:raise ValueError('Invalid kind')
            return {'path':self.home+'/'+r}

    def write(self,raw,content):
        with self.guard():
            r=self.relative(raw);fd=self.open(r,os.O_RDONLY)
            try:
                s=self.regular(fd);data=self.data(content)
                if self.version(os.fstat(fd))!=self.version(s):raise Conflict('Concurrent modification')
                self.atomic_bytes(r,data,True,self.version(s))
            finally:os.close(fd)
            return {'ok':True}

    def action(self,b):
        with self.guard():
            if b.get('pasteFromVnc'):raise Denied('VNC integration not migrated')
            if isinstance(b.get('copyPaths'),list):
                paths=b['copyPaths']
                if not paths or len(paths)>64:raise ValueError('1..64 sources required')
                dest=self.relative(b.get('destDir'));created=[]
                budget={'entries':0,'bytes':0,'deadline':time.monotonic()+15,'delete':bool(b.get('cut'))}
                try:
                    for raw in paths:
                        if time.monotonic()>budget['deadline']:raise TimeoutError('Batch deadline')
                        src=self.relative(raw);self.protected(src)
                        if dest==src or dest.startswith(src+'/'):raise Denied('Recursive destination')
                        if b.get('cut') and str(PurePosixPath(src).parent)==dest:
                            created.append(self.home+'/'+src);continue
                        dst=self.unique(dest,PurePosixPath(src).name)
                        list(self.tree(src,budget=budget))
                        if b.get('cut'):self.rename(src,dst)
                        else:
                            tmp=self.fresh()
                            try:self.copy_tree(src,tmp,budget['deadline']);self.rename(tmp,dst)
                            finally:self.cleanup(tmp)
                        created.append(self.home+'/'+dst)
                except Exception as e:
                    if created:return {'partial':True,'paths':created,'count':len(created),'error':type(e).__name__}
                    raise
                return {'paths':created,'count':len(created),'cut':bool(b.get('cut'))}
            src=self.relative(b.get('path'));self.protected(src)
            if b.get('restore'):
                if not src.startswith(TRASH+'/') or '/' in src[len(TRASH)+1:]:raise Denied('Invalid trash entry')
                key=PurePosixPath(src).name.split('-',1)[0]
                m=self.load(STATE+'/trash-'+key+'.json')
                dst=self.relative(m['original']);self.protected(dst)
                if src!=m['trash']:raise Denied('Trash metadata mismatch')
                list(self.tree(src));self.rename(src,dst);self.unlink(STATE+'/trash-'+key+'.json')
            else:
                parent=str(PurePosixPath(src).parent) if 'name' in b else self.relative(b.get('moveTo'))
                name=b.get('name',PurePosixPath(src).name)
                if (name if parent=='.' else parent+'/'+name)==src:return {'path':self.home+'/'+src}
                dst=self.unique(parent,name)
                if 'name' in b and PurePosixPath(dst).name!=name:raise FileExistsError()
                if src==dst or dst.startswith(src+'/'):raise Denied('Recursive move')
                list(self.tree(src));self.rename(src,dst)
            return {'path':self.home+'/'+dst}

    def load(self,r):
        fd=self.open(r,os.O_RDONLY)
        with os.fdopen(fd,'rb') as f:
            self.regular(f.fileno());data=f.read(65537)
        if len(data)>65536:raise Denied('Invalid metadata')
        m=json.loads(data)
        if not isinstance(m,dict):raise Denied('Invalid metadata')
        return m

    def save(self,r,m):self.atomic_bytes(r,json.dumps(m).encode(),True)

    def remove(self,b):
        with self.guard():
            if b.get('emptyTrash'):
                deadline=time.monotonic()+15
                fd=self.open(TRASH,os.O_RDONLY|os.O_DIRECTORY)
                try:names=[e.name for e in os.scandir(fd)]
                finally:os.close(fd)
                if len(names)>4000:raise OverflowError()
                for n in names:
                    if time.monotonic()>deadline:return {'partial':True,'error':'Trash cleanup deadline; remaining retained'}
                    self.remove({'path':self.home+'/'+TRASH+'/'+n,'permanent':True})
                return {'ok':True}
            src=self.relative(b.get('path'));self.protected(src)
            list(self.tree(src,budget={'entries':0,'bytes':0,'deadline':time.monotonic()+15,'delete':True}))
            if b.get('permanent') or src.startswith(TRASH+'/'):
                tmp=self.fresh()
                try:self.rename(src,tmp)
                except OSError as e:
                    if e.errno!=errno.ENOSPC:raise
                    tmp=src # Explicit permanent delete can reclaim a full filesystem.
                try:self.remove_tree(tmp)
                except Exception:return {'partial':True,'recovery':PurePosixPath(tmp).name,'error':'Delete interrupted; remainder retained'}
                if src.startswith(TRASH+'/'):
                    key=PurePosixPath(src).name.split('-',1)[0]
                    if re.fullmatch('[0-9a-f]{32}',key):
                        try:self.unlink(STATE+'/trash-'+key+'.json')
                        except FileNotFoundError:pass
                return {'deleted':True}
            key=uuid.uuid4().hex;dst=TRASH+'/'+key+'-'+os.fsencode(PurePosixPath(src).name)[:180].decode('utf-8',errors='ignore')
            self.save(STATE+'/trash-'+key+'.json',{'original':self.home+'/'+src,'trash':dst})
            self.rename(src,dst)
            return {'trashed':True,'path':self.home+'/'+dst}

    def list(self,raw,hidden=True):
        value=super().list(raw,hidden)
        value['entries']=[x for x in value['entries'] if not x['name'].startswith('.wd-')]
        v=os.fstatvfs(self.root)
        value['storage']={'capacity':v.f_blocks*v.f_frsize,'available':v.f_bavail*v.f_frsize,'kernelBounded':True}
        return value

    def upload_key(self,b):
        key=b.get('id','')
        if not isinstance(key,str) or not re.fullmatch('[a-z0-9]{8,40}',key):raise ValueError('Invalid upload id')
        return STATE+'/upload-'+key

    def upload(self,b,chunks=None,cancel=False):
        with self.guard():
            key=self.upload_key(b);meta=key+'.json';part=key+'.part'
            parent=self.relative(b.get('dir','~'));name=b.get('name','')
            rel=self.relative(name)
            if rel=='.':raise ValueError('Filename required')
            dest=rel if parent=='.' else parent+'/'+rel
            try:m=self.load(meta)
            except FileNotFoundError:
                if chunks is None or cancel:raise FileNotFoundError()
                if int(b.get('offset','0'))!=0:raise Conflict('Unknown resumable upload')
                fd=self.open(STATE,os.O_RDONLY|os.O_DIRECTORY)
                try:
                    with os.scandir(fd) as entries:
                        if sum(1 for e in entries if e.name.startswith('upload-') and e.name.endswith('.json'))>=64:raise Conflict('Upload slots full; cancel completed/old IDs')
                finally:os.close(fd)
                m={'dest':dest,'offset':0,'complete':False}
                with os.fdopen(self.open(part,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb'):pass
                self.save(meta,m)
            if m.get('dest')!=dest:raise Conflict('Upload identity mismatch')
            if m.get('pending') and not self.exists(part):
                published=self.relative(m['pending'])
                a,s=self.checked(published)
                os.close(a)
                if s.st_ino!=m.get('inode'):raise Conflict('Publication recovery conflict')
                m.update(complete=True,path=self.home+'/'+published)
            offset=m.get('offset')
            if not isinstance(offset,int) or not 0<=offset<=MAX_FILE:raise Denied('Invalid upload metadata')
            if cancel:
                if not m.get('complete'):self.unlink(part)
                self.unlink(meta);return {'ok':True,'completed':bool(m.get('complete')),'path':m.get('path'),'offset':offset}
            if chunks is None:return {'offset':offset,'complete':bool(m.get('complete')),'path':m.get('path')}
            if m.get('complete') or int(b.get('offset','0'))!=offset:raise Conflict('Offset conflict; query upload status')
            fd=self.open(part,os.O_RDWR)
            try:
                s=self.regular(fd)
                if s.st_size<offset:raise Conflict('Incomplete committed upload')
                os.ftruncate(fd,offset);os.lseek(fd,offset,0);total=0
                try:
                    for data in chunks:
                        total+=len(data)
                        if total>MAX_CHUNK or offset+total>MAX_FILE:raise OverflowError('Upload size limit')
                        view=memoryview(data)
                        while view:view=view[os.write(fd,view):]
                    os.fsync(fd)
                except BaseException:
                    os.ftruncate(fd,offset);raise
            finally:os.close(fd)
            m['offset']=offset+total
            if b.get('done')=='1':
                d=str(PurePosixPath(dest).parent);self.ensure_directory(d) if d!='.' else None
                dst=self.unique(d,PurePosixPath(dest).name)
                a,s=self.checked(part);os.close(a)
                m.update(pending=dst,inode=s.st_ino)
                self.save(meta,m) # Durable intent before atomic publication.
                fd=self.open(part,os.O_RDONLY)
                try:os.fchmod(fd,0o660)
                finally:os.close(fd)
                self.rename(part,dst)
                m.update(complete=True,path=self.home+'/'+dst)
            self.save(meta,m)
            return {'ok':True,'offset':m['offset'],'path':m.get('path'),'complete':m['complete']}

    def export(self,raw):
        r=self.relative(raw);fd,s=self.checked(r)
        if stat.S_ISREG(s.st_mode):
            try:return fd,self.regular(fd),PurePosixPath(r).name,False
            except BaseException:os.close(fd);raise
        os.close(fd)
        with self.guard():
            deadline=time.monotonic()+15
            tmp=self.fresh()
            out=self.open(tmp,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(os.dup(out),'w+b') as stream:
                    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,allowZip64=False) as archive:
                        base=PurePosixPath(r).name if r!='.' else 'workspace'
                        for p,s in self.tree(r):
                            name=base+p[len(r):]
                            if any(any(c in part for c in '\\:<>"|?*') or any(ord(c)<32 for c in part) or part in ('','..','.') for part in name.split('/')):
                                raise Denied('Non-portable archive member name')
                            if stat.S_ISDIR(s.st_mode):archive.writestr(name+'/',b'')
                            else:
                                a=self.open(p,os.O_RDONLY)
                                try:
                                    self.regular(a)
                                    with archive.open(name,'w') as dest:
                                        left=s.st_size
                                        while left:
                                            if time.monotonic()>deadline:raise TimeoutError('ZIP deadline')
                                            chunk=os.read(a,min(65536,left))
                                            if not chunk:raise Conflict('File changed during ZIP')
                                            dest.write(chunk);left-=len(chunk)
                                    if self.version(os.fstat(a))!=self.version(s):raise Conflict('File changed during ZIP')
                                finally:os.close(a)
                            if stream.tell()>MAX_FILE:raise OverflowError('ZIP too large')
                os.lseek(out,0,0);s=self.regular(out)
                self.unlink(tmp) # Open FD stays valid; cancelled stream leaves no archive.
                return out,s,base+'.zip',True
            except BaseException:
                os.close(out);self.cleanup(tmp);raise
