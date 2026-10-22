"""Root observer/fault fixture only; no runtime installation or download writes."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import re

if os.getuid()!=0 or socket.gethostname()!='wd-lab' or not Path('/var/tmp/wd-lab-ready').is_file():raise SystemExit('Disposable VM only')
HOME='/home/wdguest';gui=pwd.getpwnam('wdgui');guest=pwd.getpwnam('wdguest');term=pwd.getpwnam('wdterm')
def run(args):return subprocess.check_output(args,text=True,timeout=70).strip()
def as_user(user,code):return run(['runuser','-u',user,'-g','wdguest','--','python3','-I','-c',code])
def downloads_fd():
    parent=os.open(HOME,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:return os.open('Downloads',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    finally:os.close(parent)
def metadata(s):return dict(uid=s.st_uid,gid=s.st_gid,mode=oct(stat.S_IMODE(s.st_mode)),size=s.st_size,ino=s.st_ino,device=s.st_dev)
def rows():
    fd=downloads_fd()
    try:
        names=os.listdir(fd)
        if len(names)>4000:raise ValueError('Bounded observer listing')
        files=[]
        for name in names:
            try:s=os.stat(name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:continue
            files.append(dict(name=name,regular=stat.S_ISREG(s.st_mode),**metadata(s)))
        return dict(directory=metadata(os.fstat(fd)),files=files)
    finally:os.close(fd)
def history():
    # Root-only TEST oracle. This is never an Agent endpoint/profile reader.
    path=HOME+'/.gui/chromium/Default/History'
    if not os.path.isfile(path):return []
    try:
        # Chromium owns an exclusive SQLite connection. A TEST-ONLY immutable
        # reader bypasses that lock without changing journal/profile state.
        # It can lag the browser's commit; bounded UI waits also require the
        # real directory entry/hash. Never use this as a runtime status API.
        db=sqlite3.connect('file:'+path+'?mode=ro&immutable=1',uri=True,timeout=.2)
        try:
            result=[]
            for row in db.execute('SELECT id,guid,current_path,target_path,state,interrupt_reason,received_bytes,total_bytes FROM downloads ORDER BY id DESC LIMIT 80'):
                item=dict(zip(('id','guid','current_path','target_path','state','interrupt_reason','received_bytes','total_bytes'),row))
                # Only fixed fixture URLs; never include arbitrary profile history.
                item['urls']=[url for (url,) in db.execute('SELECT url FROM downloads_url_chains WHERE id=? ORDER BY chain_index',(item['id'],)) if re.fullmatch(r'https://203\.0\.113\.2/file/[a-z]+\?task=[0-9a-f]{32}',url)]
                result.append(item)
            return result
        finally:db.close()
    except sqlite3.DatabaseError:return []
def digest(name):
    if not isinstance(name,str) or '/' in name or '\\' in name or '\0' in name or len(os.fsencode(name))>255:raise ValueError('Observer basename only')
    parent=downloads_fd()
    try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
    finally:os.close(parent)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid not in (gui.pw_uid,guest.pw_uid,term.pw_uid) or s.st_size>32*1024**2:raise ValueError('Observer regular guest file bound')
        h=hashlib.sha256()
        for b in iter(lambda:f.read(65536),b''):h.update(b)
        return dict(**metadata(s),sha256=h.hexdigest())
def state():return dict(**rows(),history=history(),accounts={u:dict(uid=pwd.getpwnam(u).pw_uid,gid=pwd.getpwnam(u).pw_gid) for u in ('wdsvc','wdguest','wdterm','wdgui')},mounts=[l.strip() for l in Path('/proc/self/mountinfo').read_text().splitlines() if ' /home/wdguest' in l])

def profile():
    try:value=json.loads(Path(HOME+'/.gui/chromium/Default/Bookmarks').read_text())
    except (FileNotFoundError,json.JSONDecodeError):return dict(bookmark=False,tree_sha256=None)
    entries=[]
    def visit(node):
        if node.get('url'):entries.append((node.get('name',''),node['url']))
        for child in node.get('children',[]):visit(child)
    for node in value.get('roots',{}).values():
        if isinstance(node,dict):visit(node)
    return dict(bookmark=any(u.startswith('https://203.0.113.2/page/normal?') for _,u in entries),tree_sha256=hashlib.sha256(json.dumps(sorted(entries),ensure_ascii=True).encode()).hexdigest())

def resources():
    v=os.statvfs(HOME)
    r=dict(monotonic=time.monotonic(),meminfo={},cgroups={},disk=dict(block_size=v.f_frsize,blocks=v.f_blocks,block_free=v.f_bavail,inodes=v.f_files,inode_free=v.f_favail))
    for line in Path('/proc/meminfo').read_text().splitlines():
        k,_,value=line.partition(':')
        if k in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):r['meminfo'][k]=value.strip()
    for unit in ('webdesktop.slice','webdesktop-exec.slice','webdesktop-staging.service','webdesktop-isolated.service','postgresql@15-main.service','webdesktop-guest.service','webdesktop-gui@1.service','webdesktop-browser@1.service','webdesktop-pty@1.service'):
        try:cg=run(['systemctl','show',unit,'-p','ControlGroup','--value'])
        except subprocess.CalledProcessError:continue
        if not cg:continue
        r['cgroups'][unit]={}
        for name in ('memory.current','memory.peak','memory.max','memory.swap.current','memory.swap.max','memory.events','cpu.stat','cpu.max','pids.current','pids.peak','pids.max'):
            p=Path('/sys/fs/cgroup'+cg)/name
            try:r['cgroups'][unit][name]=p.read_text().strip()
            except FileNotFoundError:r['cgroups'][unit][name]=None
    return r

op=sys.argv[1]
if op=='state':result=state()
elif op=='profile':result=profile()
elif op=='secret-check':result=dict(preserved=Path('/root/d1-private-key.test').read_bytes()==b'D1-test-only')
elif op=='execution-check':result=dict(not_executed=not os.path.lexists('/opt/webdesktop/app/D1-EXECUTED'))
elif op=='origin-evidence':
    request=json.loads(sys.stdin.read());task=request['task']
    if not isinstance(task,str) or not re.fullmatch('[0-9a-f]{32}',task):raise ValueError('Fixed task token required')
    output=run(['journalctl','--no-pager','-o','cat','-u','d1-origin.service','-n','500'])
    records=[]
    for line in output.splitlines():
        try:item=json.loads(line)
        except json.JSONDecodeError:continue
        if item.get('task')==task:records.append(item)
    result=dict(task=task,records=records)
elif op=='terminal-idle':
    units={}
    for name in ('webdesktop-pty@1.service','webdesktop-pty@2.service','webdesktop-pty-lease@1.service','webdesktop-pty-lease@2.service'):
        value=run(['systemctl','show',name,'-p','MainPID','-p','ActiveState'])
        units[name]=dict(line.split('=',1) for line in value.splitlines())
    result=dict(idle=all(s['MainPID']=='0' and s['ActiveState'] in ('inactive','failed') for s in units.values()),units=units)
elif op=='execution-idle':
    units={}
    for name in ('webdesktop-gui@1.service','webdesktop-browser@1.service','webdesktop-gui-lease@1.service'):
        state=run(['systemctl','show',name,'-p','MainPID','-p','ActiveState','-p','Result','-p','ControlGroup'])
        item=dict(line.split('=',1) for line in state.splitlines());cg=item.get('ControlGroup')
        processes=Path('/sys/fs/cgroup'+cg+'/cgroup.procs') if cg else None
        item['cgroup_empty']=not processes or not processes.exists() or not processes.read_text().strip()
        units[name]=item
    result=dict(idle=all(s['MainPID']=='0' and s['ActiveState'] in ('inactive','failed') and s['cgroup_empty'] for s in units.values()),units=units)
elif op=='title':
    pid=run(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value'])
    prefix=['nsenter','-t',pid,'-m','-n','--','setpriv','--reuid',str(gui.pw_uid),'--regid',str(guest.pw_gid),'--groups',str(gui.pw_gid),'--no-new-privs','env','-i','PATH=/usr/bin:/bin','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority']
    found=subprocess.run(prefix+['xdotool','search','--onlyvisible','--class','chromium'],capture_output=True,text=True,timeout=8)
    if found.stderr.strip() or found.returncode not in (0,1):raise RuntimeError('Private X11 observer failed: '+found.stderr[:300])
    result=[run(prefix+['xdotool','getwindowname',w]) for w in found.stdout.splitlines()[:8]]
elif op=='download-ui':
    request=json.loads(sys.stdin.read());name=request['name'];others=request.get('names',[])
    if not isinstance(name,str) or len(name)>255:raise ValueError('Fixed fixture name required')
    if not isinstance(others,list) or len(others)>32 or any(not isinstance(n,str) or len(n)>255 for n in others):raise ValueError('Bounded fixture names required')
    pid=run(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value'])
    prefix=['nsenter','-t',pid,'-m','-n','--','setpriv','--reuid',str(gui.pw_uid),'--regid',str(guest.pw_gid),'--groups',str(gui.pw_gid),'--no-new-privs','env','-i','PATH=/usr/bin:/bin','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority']
    selected=subprocess.run(prefix+['xclip','-selection','clipboard','-o'],capture_output=True,text=True,timeout=8)
    if selected.returncode not in (0,1):raise RuntimeError('Native clipboard observation failed')
    text=selected.stdout[:12000]
    lower=text.lower()
    result=dict(name_visible=name in text,other_names_visible=[n for n in others if n!=name and n in text],failed='failed' in lower,file_incomplete='file incomplete' in lower,permission_failure=any(s in lower for s in ('insufficient permissions','needs permission','permission denied','needs authorization')),disk_full=any(s in lower for s in ('disk full','disk is full','not enough space','insufficient disk space')),network_failure=any(s in lower for s in ('network error','network failed','network failure','network interrupted')),security_review=any(s in lower for s in ('blocked','dangerous','unverified','insecure download')),canceled='canceled' in lower or 'cancelled' in lower,selection_sha256=hashlib.sha256(text.encode()).hexdigest(),fixture_excerpt=text[:2400])
elif op=='crash':
    subprocess.run(['systemctl','kill','--kill-whom=all','--signal=SIGKILL','webdesktop-browser@1.service'],check=True)
    result=dict(killed=True)
elif op=='restart-agent':
    subprocess.run(['systemctl','restart','webdesktop-guest.service'],check=True);result=dict(restarted=True)
elif op=='restart-backend':
    unit=sys.argv[2]
    if unit not in ('webdesktop-staging.service','webdesktop-isolated.service'):raise ValueError('Fixed backend unit')
    subprocess.run(['systemctl','restart',unit],check=True);result=dict(restarted=True)
elif op=='resources':result=resources()
elif op=='monitor':
    os.umask(0o077)
    with open('/root/d1-download-metrics.jsonl','x') as output:
        end=time.monotonic()+720
        while time.monotonic()<end:
            output.write(json.dumps(resources())+'\n');output.flush();time.sleep(1)
    raise SystemExit(0)
elif op=='hash':result=digest(json.loads(sys.stdin.read())['name'])
elif op=='replacement':
    result=json.loads(as_user('wdguest',"import os,json\ntry:os.rename('/home/wdguest/Downloads','/home/wdguest/Downloads-d1-should-not-move')\nexcept OSError as e:print(json.dumps(dict(denied=True,errno=e.errno)))\nelse:os.rename('/home/wdguest/Downloads-d1-should-not-move','/home/wdguest/Downloads');print(json.dumps(dict(denied=False)))"))
elif op=='links':
    # Link creation is an auxiliary attack fixture, not a fake positive download.
    result=json.loads(as_user('wdterm',"import os,json\np='/home/wdguest/Downloads/d1-secret-link.txt'\nos.symlink('/root/d1-private-key.test',p)\nprint(json.dumps(dict(created=True)))"))
elif op=='unlink-test-link':
    as_user('wdterm',"import os; p='/home/wdguest/Downloads/d1-secret-link.txt'; assert os.path.islink(p) and os.readlink(p)=='/root/d1-private-key.test';os.unlink(p)");result=dict(removed=True)
elif op=='directory-source':
    result=json.loads(as_user('wdterm',"import os,json\np='/home/wdguest/Downloads';saved=p+'-d1-kept';os.rename(p,saved)\ntry:os.symlink('/opt/webdesktop/source',p)\nexcept BaseException:os.rename(saved,p);raise\nprint(json.dumps(dict(replaced=True)))"))
elif op=='restore-directory':
    as_user('wdterm',"import os\np='/home/wdguest/Downloads';saved=p+'-d1-kept'\nif not os.path.islink(p) or os.readlink(p)!='/opt/webdesktop/source':raise RuntimeError('Test directory changed')\nos.unlink(p);os.rename(saved,p)");result=dict(restored=True)
elif op=='hardlink':
    as_user('wdterm',"import os;os.link('/home/wdguest/Downloads/d1-normal.txt','/home/wdguest/Downloads/d1-hardlink.txt')");result=dict(created=True)
elif op=='unlink-hardlink':
    as_user('wdterm',"import os\np='/home/wdguest/Downloads/d1-hardlink.txt';s=os.lstat(p);a=os.lstat('/home/wdguest/Downloads/d1-normal.txt')\nif (s.st_dev,s.st_ino)!=(a.st_dev,a.st_ino) or s.st_nlink!=2:raise RuntimeError('Test hardlink changed')\nos.unlink(p)");result=dict(removed=True)
elif op in ('space','inodes'):
    code="""import os,json,errno,time
p='/home/wdguest/Documents/d1-fault-OP';os.mkdir(p,0o770)
v=os.statvfs('/home/wdguest');count=0;hit=False
try:
 if 'OP'=='space':
  fd=os.open(p+'/fill',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o660)
  try:os.posix_fallocate(fd,0,max(0,(v.f_bavail-16)*v.f_frsize))
  finally:os.close(fd)
  hit=os.statvfs('/home/wdguest').f_bavail<32
 else:
  deadline=time.monotonic()+45
  for n in range(min(v.f_favail+16,200000)):
   if time.monotonic()>deadline:raise TimeoutError('Inode fault budget')
   d=p+'/'+str(n//1000)
   if n%1000==0:os.mkdir(d)
   fd=os.open(d+'/'+str(n),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o660);os.close(fd);count+=1
except OSError as e:
 if e.errno!=errno.ENOSPC:raise
 hit=True
print(json.dumps(dict(enospc=hit,files=count,available=os.statvfs('/home/wdguest').f_bavail,inodes=os.statvfs('/home/wdguest').f_favail)))
""".replace('OP',op)
    result=json.loads(as_user('wdterm',code))
elif op=='clear-fault':
    code="import os,shutil\nfor kind in ('space','inodes'):\n p='/home/wdguest/Documents/d1-fault-'+kind\n if os.path.lexists(p):\n  assert not os.path.islink(p) and os.stat(p).st_uid==os.getuid();shutil.rmtree(p)"
    as_user('wdterm',code);result=dict(cleared=True)
elif op=='boundaries':
    paths=['/opt/webdesktop/source/package.json','/opt/webdesktop/source/.git/config','/opt/webdesktop/app/.env','/var/lib/postgresql/15/main/PG_VERSION','/root/d1-private-key.test','/home/wdguest/.wd-state/agent.lock']
    result={}
    result['root-test-secret-preserved']=Path('/root/d1-private-key.test').read_bytes()==b'D1-test-only'
    result['no-automatic-service-execution']=not os.path.lexists('/opt/webdesktop/app/D1-EXECUTED')
    result['protected-targets-exist']=[os.path.isfile(p) for p in paths]
    for user in ('wdgui','wdterm'):
        code="import os,json\nr=[]\nfor p in "+repr(paths)+":\n try:fd=os.open(p,os.O_RDONLY)\n except PermissionError:r.append(True)\n except FileNotFoundError:r.append(False)\n else:os.close(fd);r.append(False)\nprint(json.dumps(r))"
        result[user]=json.loads(as_user(user,code))
    result['agent-profile-denied']=json.loads(as_user('wdguest',"import json,os\ntry:fd=os.open('/home/wdguest/.gui/chromium/Default/Preferences',os.O_RDONLY)\nexcept PermissionError:print('true')\nelse:os.close(fd);print('false')"))
    # wdguest owns .wd-state, so its expected denial set excludes that own state.
    result['wdguest']=json.loads(as_user('wdguest',"import os,json\nr=[]\nfor p in "+repr(paths[:-1])+":\n try:fd=os.open(p,os.O_RDONLY)\n except PermissionError:r.append(True)\n except FileNotFoundError:r.append(False)\n else:os.close(fd);r.append(False)\nprint(json.dumps(r))"))
    direct=subprocess.run(['runuser','-u','wdsvc','--','python3','-I','-c',"import os;os.open('/home/wdguest/Downloads/d1-normal.txt',os.O_RDONLY)"],capture_output=True)
    result['service-direct-data-denied']=direct.returncode!=0
else:raise SystemExit('Unknown observer action')
print(json.dumps(result,ensure_ascii=True))
