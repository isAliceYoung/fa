"""Root fault injection ONLY in the controller's disposable Debian VM."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import subprocess
import sys

if '--disposable-vm' not in sys.argv or os.geteuid()!=0 or socket.gethostname()!='wd-lab' or not Path('/var/tmp/wd-lab-ready').is_file():
    raise SystemExit('Explicit disposable VM required')

STATE=Path('/etc/webdesktop-isolated')
source=Path('/opt/webdesktop/source')
commit=json.loads((STATE/'state.json').read_text())['commit']
entry=['bash',str(source/'webdesktop-deploy/install-isolated.sh'),'--commit',commit]
backend='webdesktop-isolated.service'
unit=Path('/etc/systemd/system')/backend
report=dict(passed=0,failed=0,skipped=0,not_executed=19,tests=[])


def cmd(args,env=None):
    return subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=150,env=env)


def run(args):
    r=cmd(args)
    if r.returncode:raise RuntimeError('Fixture command failed: '+str(args[0]))
    return r.stdout.strip()


def test(name,fn):
    report['not_executed']-=1
    try:fn();report['passed']+=1;report['tests'].append(dict(name=name,result='passed'))
    except Exception as e:
        report['failed']+=1;report['tests'].append(dict(name=name,result='failed',detail=(type(e).__name__+': '+str(e))[:1600]))
        raise
    finally:(Path('/root/staging-results')/'phase4a-modes.json').write_text(json.dumps(report,indent=2))


def require(ok):
    if not ok:raise ValueError('Mode/boundary assertion failed')


value=secrets.token_hex(16)
file=Path('/home/wdguest/Documents/phase4a-preserve.txt')
if not file.parent.exists():
    file.parent.mkdir();os.chown(file.parent,pwd.getpwnam('wdguest').pw_uid,pwd.getpwnam('wdguest').pw_gid);file.parent.chmod(0o2770)
file.write_text(value);os.chown(file,pwd.getpwnam('wdterm').pw_uid,pwd.getpwnam('wdguest').pw_gid)
profile=Path('/home/wdguest/.gui/chromium');profile.mkdir(mode=0o700,exist_ok=True)
os.chown(profile,pwd.getpwnam('wdgui').pw_uid,pwd.getpwnam('wdguest').pw_gid)
marker=profile/'phase4a-preserve.txt';marker.write_text(value);os.chown(marker,pwd.getpwnam('wdgui').pw_uid,pwd.getpwnam('wdguest').pw_gid)
from urllib.parse import urlsplit
cfg=dict(l.split('=',1) for l in Path('/opt/webdesktop/app/.env').read_text().splitlines() if '=' in l)
database=urlsplit(cfg['DATABASE_URL']).path[1:]
run(['runuser','-u','postgres','--','psql','-d',database,'-v','ON_ERROR_STOP=1','-c',"CREATE TABLE phase4a_fixture(marker text); INSERT INTO phase4a_fixture VALUES ('"+value+"');"])


def snapshot():
    paths=[Path('/opt/webdesktop/app/.env'),STATE/'login.json',STATE/'recovery.json',file,marker]
    return dict(files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                uuid=run(['blkid','-s','UUID','-o','value','/var/lib/webdesktop-guest-volume/workspace.ext4']),
                row=hashlib.sha256(run(['runuser','-u','postgres','--','psql','-At','-d',database,'-c','SELECT marker FROM phase4a_fixture']).encode()).hexdigest())


saved=snapshot()
def mode(name,expected=0,env=None):
    before=snapshot()
    r=cmd([*entry,'--mode',name],env)
    if r.returncode!=expected:raise RuntimeError('mode='+name+' expected='+str(expected)+' actual='+str(r.returncode)+'; '+r.stdout[-1200:])
    require(snapshot()==before)
    return r


try:
    for name in ('auto','install','repair','service'):
        before=run(['systemctl','show',backend,'-p','MainPID','--value'])
        test('healthy '+name+' preserves credentials DB files volume profile and PID',lambda name=name,before=before:(mode(name),require(run(['systemctl','show',backend,'-p','MainPID','--value'])==before)))

    def stopped():
        run(['systemctl','stop',backend]);mode('service')
        require(run(['systemctl','is-active',backend])=='active')
    test('service restores stopped backend without installation or regeneration',stopped)

    original=unit.read_bytes()
    def missing():
        run(['systemctl','stop',backend]);unit.unlink();run(['systemctl','daemon-reload'])
        try:
            mode('service',1);require(not unit.exists())
            mode('repair');require(unit.read_bytes()==original)
        finally:
            if not unit.exists():unit.write_bytes(original);unit.chmod(0o644);run(['systemctl','daemon-reload']);run(['systemctl','start',backend])
    test('service refuses missing unit; repair restores only exact frozen backend unit',missing)

    def corrupt(path,data):
        old=path.read_bytes();path.write_bytes(data)
        try:mode('repair',1);require(path.read_bytes()==data)
        finally:path.write_bytes(old)
    test('modified unit is refused and not overwritten',lambda:corrupt(unit,original+b'\n# unknown change\n'))
    test('modified environment is refused without reseeding',lambda:corrupt(Path('/opt/webdesktop/app/.env'),Path('/opt/webdesktop/app/.env').read_bytes()+b'\n# unknown change\n'))
    test('damaged executable artifact is refused',lambda:corrupt(Path('/opt/webdesktop/app/server.mjs'),Path('/opt/webdesktop/app/server.mjs').read_bytes()+b'\n// unknown change\n'))

    def unknown_drop():
        p=Path('/etc/systemd/system')/(backend+'.d')/'unreviewed.conf'
        p.write_text('[Service]\nUser=root\n');p.chmod(0o644)
        try:mode('service',1);require(p.exists())
        finally:p.unlink()
    test('unknown service override cannot be loaded or adopted',unknown_drop)

    def acl():
        old=unit.stat().st_mode;run(['setfacl','-m','u:nobody:r',unit])
        try:require(unit.stat().st_mode==old);mode('service',1)
        finally:run(['setfacl','-b',unit])
    test('extended ACL is rejected even with unchanged mode and file digest',acl)

    def incomplete():
        p=STATE/'state.json';old=p.read_bytes();d=json.loads(old);d.update(status='failed',phase='packages');p.write_text(json.dumps(d))
        try:mode('install',1);require(json.loads(p.read_text())['phase']=='packages')
        finally:p.write_bytes(old)
    test('early partial install remains blocked and data is retained',incomplete)

    def failed_start():
        p=STATE/'state.json';old=p.read_bytes();d=json.loads(old);d.update(status='failed',phase='start-service');p.write_text(json.dumps(d))
        run(['systemctl','stop',backend])
        try:mode('service');require(json.loads(p.read_text())['status']=='ready')
        finally:p.write_bytes(old)
    test('sealed final-start failure is resumable without reinstall',failed_start)

    def legacy():
        p=Path('/etc/webdesktop-deploy.conf');p.write_text('test-old-layout\n');p.chmod(0o600)
        try:mode('auto',1);require(p.read_text()=='test-old-layout\n')
        finally:p.unlink()
    test('legacy single-account record is rejected and retained',legacy)

    def network():
        run(['nft','delete','table','inet','wd_browser'])
        try:mode('service',1);require(cmd(['nft','list','table','inet','wd_browser']).returncode!=0)
        finally:
            run(['nft','delete','table','ip','wd_browser_nat']);run(['nft','-f','/usr/local/lib/webdesktop-browser/network.nft'])
    test('service detects missing network policy and never rebuilds it',network)

    def optimized():
        for flag in ('-O','-OO'):
            r=cmd(['python3','-I',flag,str(source/'webdesktop-deploy/scripts/isolated_deploy.py'),'--mode','service','--commit','0'*40])
            require(r.returncode==1);require(snapshot()==saved)
    test('optimizing Python cannot bypass commit and recovery checks',optimized)

    def privileged():
        paths=['/opt/webdesktop/source/package.json','/opt/webdesktop/source/.git/config','/opt/webdesktop/app/.env','/etc/webdesktop-isolated/login.json','/root/phase4a-test-deploy-key']
        key=Path(paths[-1]);key.write_text('ONLY-TEST-KEY');key.chmod(0o600)
        try:
            pids=[run(['systemctl','show',u,'-p','MainPID','--value']) for u in (backend,'postgresql@15-main.service')]
            probe='''import os,sys
for p in sys.argv[1:6]:
 for mode in (os.O_RDONLY,os.O_WRONLY):
  try:fd=os.open(p,mode)
  except PermissionError:continue
  else:os.close(fd);raise SystemExit(1)
for pid in sys.argv[6:]:
 try:os.kill(int(pid),0)
 except PermissionError:continue
 else:raise SystemExit(1)
'''
            for user in ('wdguest','wdterm','wdgui'):require(cmd(['runuser','-u',user,'--','python3','-I','-c',probe,*paths,*pids]).returncode==0)
        finally:key.unlink()
    test('actual three guest UIDs cannot open secrets or signal Next/PostgreSQL',privileged)

    def no_tools():
        guard=Path('/root/phase4a-tool-guard');guard.mkdir(mode=0o700)
        denial='#!/bin/sh\nprintf forbidden >> /root/phase4a-forbidden-tools\nexit 97\n'
        for tool in ('apt','apt-get','npm','npx','ssh-keygen','curl','wget'):
            p=guard/tool;p.write_text(denial);p.chmod(0o700)
        p=guard/'git';p.write_text('#!/bin/sh\ncase " $* " in *" fetch "*|*" clone "*|*" pull "*)printf forbidden >> /root/phase4a-forbidden-tools;exit 97;; esac\nexec /usr/bin/git "$@"\n');p.chmod(0o700)
        env={**os.environ,'PATH':str(guard)+':/usr/sbin:/usr/bin:/sbin:/bin'}
        mode('service',env=env);require(not Path('/root/phase4a-forbidden-tools').exists())
    test('service actually executes with package build fetch and key tools denied',no_tools)

    test('all mode/fault tests preserve original DB row credentials UUID and guest markers',lambda:require(snapshot()==saved))
finally:
    Path('/root/staging-results/phase4a-modes.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:report[k] for k in ('passed','failed','skipped','not_executed')}))
