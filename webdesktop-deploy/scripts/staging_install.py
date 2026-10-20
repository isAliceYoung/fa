#!/usr/bin/env python3
"""Fresh-server staged installation. No SSH client, remote execution or migration."""
import argparse
import fcntl
import grp
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
STATE=Path('/etc/webdesktop-staging')
DEPLOY=Path('/opt/webdesktop')
APP=DEPLOY/'app'
BRANCH='feature/private-deploy-isolation'
spec=importlib.util.spec_from_file_location('staging_bundle',HERE/'staging_bundle.py')
bundle=importlib.util.module_from_spec(spec); spec.loader.exec_module(bundle)


def command(args, timeout=180, input=None):
    r=subprocess.run([str(x) for x in args],input=input,text=True,capture_output=True,timeout=timeout)
    if r.returncode:
        raise RuntimeError('Command failed: '+str(args[0])+' (exit '+str(r.returncode)+')')
    return r.stdout.strip()


def owned(path, mode=None):
    p=Path(path); s=p.lstat()
    assert s.st_uid==0 and not p.is_symlink() and not s.st_mode & 0o022, 'Unsafe root-owned path: '+str(path)
    if mode is not None: assert stat.S_IMODE(s.st_mode)==mode


def origins(value):
    from urllib.parse import urlsplit
    result=[]
    for v in value.split(','):
        u=urlsplit(v)
        assert u.scheme=='http' and u.hostname in ('127.0.0.1','localhost') and u.port and 1<=u.port<=65535
        assert not u.username and not u.password and not u.path and not u.query and not u.fragment
        assert v==u.scheme+'://'+u.netloc
        result.append(v)
    assert 'http://127.0.0.1:3000' in result and len(result)<=8
    return ','.join(dict.fromkeys(result))


def inventory():
    users=[x.pw_name for x in pwd.getpwall() if 1000<=x.pw_uid<65534]
    return dict(host=socket.gethostname(),users=users,cpu=os.cpu_count(),meminfo=Path('/proc/meminfo').read_text(),
                disk=shutil.disk_usage('/')._asdict(),swap=Path('/proc/swaps').read_text(),
                ports=command(['ss','-lntup']),units=command(['systemctl','list-unit-files','--state=enabled','--no-pager']))


def preflight(a):
    assert os.geteuid()==0, 'root required'
    release=dict(x.split('=',1) for x in Path('/etc/os-release').read_text().splitlines() if '=' in x)
    assert release.get('ID','').strip('"')=='debian' and release.get('VERSION_ID','').strip('"')=='12'
    assert command(['dpkg','--print-architecture'])=='amd64' and '2.36' in command(['getconf','GNU_LIBC_VERSION'])
    assert Path('/proc/1/comm').read_text().strip()=='systemd' and Path('/sys/fs/cgroup/cgroup.controllers').exists()
    assert a.authorize_new_vps==socket.gethostname(), '--authorize-new-vps must equal the actual hostname'
    assert re.fullmatch('[0-9a-f]{40}',a.commit or ''), 'Full lowercase commit SHA required'
    assert command(['git','-C',REPO,'rev-parse','HEAD'])==a.commit, 'Installer checkout must match the pinned commit'
    assert not command(['git','-C',REPO,'status','--porcelain','--untracked-files=no']), 'Tracked installer checkout is dirty'
    for p in (REPO,*REPO.parents): owned(p)
    assert re.fullmatch('[0-9a-f]{64}',a.artifact_sha256 or ''), 'Independent artifact SHA256 required'
    owned(a.artifact)
    a.origins=origins(a.origins)
    assert a.admin and set(inventory()['users'])<=set(a.admin), 'Unknown interactive users: review inventory and explicitly name administrators'
    for name in a.admin:
        account=pwd.getpwnam(name)
        assert account.pw_shell in ('/bin/bash','/bin/sh','/bin/dash','/usr/bin/bash')
        assert name not in ('wdsvc','wdguest','wdterm','wdgui','wdtunnel')
    ssh=os.environ.get('SSH_CONNECTION','').split()
    assert not ssh or ':' not in ssh[0], 'IPv6-only administrator session: IPv4-only change needs independent review'
    mem=int(re.search(r'MemTotal:\s+(\d+)',Path('/proc/meminfo').read_text())[1])*1024
    assert mem>=900*1024**2 and os.cpu_count()>=1, 'At least nominal 1 GiB RAM and 1 CPU required'
    assert shutil.disk_usage('/').free>=6*1024**3, 'At least 6 GiB free before installation required'
    assert not STATE.exists() and not STATE.is_symlink(), 'Existing staging state: no second installation; inspect recovery instructions'
    for p in ('/opt/webdesktop','/etc/webdesktop','/etc/webdesktop-guest','/etc/postgresql','/var/lib/postgresql',
              '/var/lib/webdesktop','/var/lib/webdesktop-guest-volume','/usr/local/lib/webdesktop-guest',
              '/usr/local/lib/webdesktop-pty','/usr/local/lib/webdesktop-gui','/usr/local/lib/webdesktop-browser',
              '/etc/netns/wd-browser','/var/lib/webdesktop-staging.swap'):
        assert not os.path.lexists(p), 'Existing data/runtime requires review: '+p
    for name in ('wdsvc','wdguest','wdterm','wdgui','wdtunnel'):
        try: pwd.getpwnam(name)
        except KeyError: pass
        else: raise RuntimeError('Existing project identity: '+name)
        try: grp.getgrnam(name)
        except KeyError: pass
        else: raise RuntimeError('Existing project group: '+name)
    for directory in ('/etc/systemd/system','/etc/tmpfiles.d','/etc/polkit-1/rules.d','/etc/ssh/sshd_config.d'):
        if not Path(directory).exists(): continue
        for p in Path(directory).iterdir():
            assert 'webdesktop' not in p.name and not p.name.startswith('wdlab'), 'Existing project policy: '+str(p)
            if p.suffix=='.service' and p.is_file() and not p.is_symlink():
                assert subprocess.run(['dpkg-query','-S',str(p)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0, 'Unknown local service: '+str(p)
    for line in command(['ss','-H','-lnt']).splitlines():
        assert line.split()[3].rsplit(':',1)[-1]=='22', 'Unexpected TCP listener; stop for manual review'
    # Do not merge arbitrary firewall, routes or namespaces with this test model.
    if shutil.which('nft'): assert not command(['nft','list','ruleset']), 'Existing nftables policy requires review'
    assert not Path('/run/netns/wd-browser').exists()
    assert subprocess.run(['ip','link','show','wdb-host'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
    command(['/usr/sbin/sshd','-t'])
    return inventory()


class Install:
    def __init__(self,a):
        self.a=a; self.state=None; self.secrets=[]; self.phase='preflight'

    def save(self):
        p=STATE/'state.json'; temporary=STATE/'state.new'
        with temporary.open('w') as f: json.dump(self.state,f,indent=2); f.flush(); os.fsync(f.fileno())
        temporary.chmod(0o600); temporary.replace(p)

    def stage(self,name):
        self.phase=name; self.state['phase']=name; self.save(); print('STAGE '+name,flush=True)

    def run(self,args,timeout=600):
        r=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=timeout,
                         cwd=APP if APP.exists() else REPO,
                         env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','LC_ALL':'C.UTF-8'})
        text=r.stdout+'\n'+r.stderr
        for value in self.secrets: text=text.replace(value,'[REDACTED]')
        with (STATE/'install.log').open('a') as log: log.write('STAGE '+self.phase+' / '+str(args[0])+'\n'+text+'\n')
        if r.returncode: raise RuntimeError('Command failed: '+str(args[0])+' exit='+str(r.returncode))
        return r.stdout.strip()

    def write(self,path,data,mode=0o644):
        p=Path(path); assert not os.path.lexists(p), 'Unknown target exists: '+str(p)
        p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('x') as f: f.write(data)
        p.chmod(mode)

    def install(self):
        inv=preflight(self.a)
        # Validate and extract BEFORE changing accounts, sshd, DB or networks.
        work=Path('/var/cache/webdesktop-staging')
        assert not os.path.lexists(work), 'Unknown staging extraction directory'
        manifest=bundle.extract(self.a.artifact,self.a.artifact_sha256,self.a.commit,work)
        STATE.mkdir(mode=0o700)
        self.write(STATE/'install.log','',0o600)
        self.state=dict(schema=1,authorized_new_vps=True,branch=BRANCH,commit=self.a.commit,
                        artifact_sha256=self.a.artifact_sha256,phase='verified-artifact',status='installing',
                        administrators=self.a.admin,origins=self.a.origins,inventory=inv)
        self.save()
        self.stage('packages')
        self.run(['apt-get','-o','DPkg::Lock::Timeout=180','update'])
        self.run(['apt-get','-o','DPkg::Lock::Timeout=180','install','-y','--no-install-recommends',
                  'postgresql','polkitd','git','curl','ca-certificates','python3','e2fsprogs','util-linux','iproute2',
                  'nftables','tigervnc-standalone-server','openbox','pcmanfm','dbus-x11','xauth','x11-utils',
                  'xdotool','xclip','xfonts-base','fonts-dejavu-core','fonts-noto-cjk','fcitx5',
                  'fcitx5-chinese-addons','fcitx5-frontend-gtk3','fcitx5-frontend-qt5','chromium'],1200)
        self.stage('ipv4-and-swap')
        self.write('/etc/sysctl.d/90-webdesktop-staging.conf','net.ipv6.conf.all.disable_ipv6=1\nnet.ipv6.conf.default.disable_ipv6=1\n')
        self.run(['sysctl','-p','/etc/sysctl.d/90-webdesktop-staging.conf'])
        active=sum(int(x.split()[2])*1024 for x in Path('/proc/swaps').read_text().splitlines()[1:])
        if active<1024**3:
            size=((1024**3-active+1024**2-1)//1024**2)*1024**2
            assert shutil.disk_usage('/var/lib').free>size+3*1024**3, 'Swap would consume reserve'
            self.write('/var/lib/webdesktop-staging.swap','',0o600)
            self.run(['fallocate','-l',str(size),'/var/lib/webdesktop-staging.swap'])
            self.run(['mkswap','/var/lib/webdesktop-staging.swap']); self.run(['swapon','/var/lib/webdesktop-staging.swap'])
            with Path('/etc/fstab').open('a') as f: f.write('\n/var/lib/webdesktop-staging.swap none swap sw 0 0\n')
        self.stage('source-and-artifact')
        DEPLOY.mkdir(mode=0o700)
        assert command(['git','bundle','list-heads',work/'source.bundle']).split()[0]==self.a.commit
        self.run(['git','clone','--no-checkout',work/'source.bundle',DEPLOY/'source'])
        self.run(['git','-C',DEPLOY/'source','checkout','--detach',self.a.commit])
        self.run(['git','-C',DEPLOY/'source','fsck','--no-reflogs'])
        (work/'app').rename(APP)
        self.run([APP/'node','-e',"if(process.versions.node!=='22.23.2'||process.versions.modules!=='127')process.exit(1);require('sharp');require('node-pty')"])
        shutil.copy2(work/'manifest.json',STATE/'artifact-manifest.json')
        shutil.copy2(work/'source.bundle',STATE/'source.bundle')
        self.stage('identities-and-layers')
        scripts=DEPLOY/'source/webdesktop-deploy/scripts'
        for name in ('guest-layer','guest-volume','pty-layer','gui-layer','browser-layer'):
            self.run(['bash',scripts/('setup-'+name+'.sh'),'--staging'])
        self.stage('database-and-configuration')
        # Fresh cluster only. Do not copy or rewrite an old server's database.
        if subprocess.run(['pg_isready','-h','127.0.0.1'],stdout=subprocess.DEVNULL).returncode:
            self.run(['pg_ctlcluster','15','main','start'])
        self.run([APP/'node',scripts/'bootstrap.cjs','create-env',APP])
        cfg=dict(line.split('=',1) for line in (APP/'.env').read_text().splitlines() if '=' in line)
        self.secrets=[v for k,v in cfg.items() if k in ('DATABASE_URL','ADMIN_PASSWORD','DESKTOP_PASSWORD','AUTH_KEY')]
        with (APP/'.env').open('a') as f: f.write('WD_PUBLIC_ORIGINS='+self.a.origins+'\nUPSTREAM_BRANCH='+BRANCH+'\n')
        self.run([APP/'node',scripts/'bootstrap.cjs','database',APP])
        assert self.run([APP/'node',scripts/'bootstrap.cjs','schema-state',APP])=='empty'
        self.run([APP/'node',APP/'node_modules/drizzle-kit/bin.cjs','push','--force'])
        self.write(STATE/'login.json',json.dumps({k:cfg[k] for k in ('ADMIN_USER','ADMIN_PASSWORD','ADMIN_PATH','DESKTOP_PASSWORD')},indent=2)+'\n',0o600)
        self.stage('units-and-budget')
        self.run(['chown','-R','root:wdsvc',DEPLOY])
        self.run(['chmod','-R','o-rwx',DEPLOY]); self.run(['chmod','-R','g+rX',DEPLOY])
        # Source is root-only. Backend gets only its compiled application package.
        self.run(['chmod','700',DEPLOY/'source']); self.run(['chmod','640',APP/'.env'])
        self.run(['chmod','750',DEPLOY,APP])
        template=(DEPLOY/'source/webdesktop-deploy/templates/webdesktop-staging.service').read_text()
        self.write('/etc/systemd/system/webdesktop-staging.service',template.replace('@ORIGINS@',self.a.origins))
        self.write('/etc/systemd/system/webdesktop.slice','[Slice]\nMemoryAccounting=yes\nCPUAccounting=yes\nTasksAccounting=yes\nMemoryHigh=672M\nMemoryMax=768M\nMemorySwapMax=640M\nCPUQuota=90%\nTasksMax=320\n')
        for service,limits in {'webdesktop-guest':'MemorySwapMax=32M\n','postgresql@15-main':'MemoryHigh=96M\nMemoryMax=128M\nMemorySwapMax=64M\n',
                               'webdesktop-gui-lease@':'MemorySwapMax=0\n','webdesktop-pty-lease@':'MemorySwapMax=0\n'}.items():
            slice_line='' if 'lease@' in service else 'Slice=webdesktop.slice\n'
            self.write('/etc/systemd/system/'+service+'.service.d/staging-budget.conf','[Service]\n'+slice_line+limits)
        self.run(['systemctl','daemon-reload'])
        self.run(['systemd-analyze','verify','webdesktop-staging.service','webdesktop-browser@1.service','webdesktop-gui@1.service','webdesktop-pty@1.service'])
        self.run(['systemctl','enable','--now','webdesktop-guest.socket'])
        self.run(['systemctl','restart','postgresql@15-main.service'])
        # Metadata extraction directory is known and contains no live data.
        # Keep it for diagnosis; do not remove unknown directories on failure.
        self.finalize()

    def finalize(self):
        self.stage('start-service')
        self.run(['systemctl','enable','--now','webdesktop-staging.service'])
        for attempt in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:3000/api/health',timeout=2) as response:
                    if response.status==200: break
            except Exception: pass
            time.sleep(.5)
        else: raise RuntimeError('HTTP health readiness timeout')
        self.run([APP/'node',DEPLOY/'source/webdesktop-deploy/scripts/bootstrap.cjs','auth-state',APP])
        self.state['status']='ready'; self.stage('ready')
        print('READY: loopback 127.0.0.1:3000. Credentials: /etc/webdesktop-staging/login.json (root 0600).')
        print('Run check-staging.sh; follow STAGING_TEST_GUIDE.md. No Claude/updates enabled.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',action='store_true'); p.add_argument('--commit'); p.add_argument('--artifact',type=Path)
    p.add_argument('--artifact-sha256'); p.add_argument('--authorize-new-vps'); p.add_argument('--admin',action='append',default=[])
    p.add_argument('--origins',default='http://127.0.0.1:3000,http://127.0.0.1:18890')
    p.add_argument('--resume-finalize',action='store_true'); a=p.parse_args()
    assert os.geteuid()==0, 'root required'
    if a.inventory: print(json.dumps(inventory(),indent=2)); return
    with open('/run/lock/webdesktop-staging.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        install=Install(a)
        try:
            if a.resume_finalize:
                owned(STATE,0o700); owned(STATE/'state.json',0o600)
                install.state=json.loads((STATE/'state.json').read_text())
                assert install.state['phase']=='start-service' and install.state['status']=='failed'
                assert a.commit==install.state['commit']==command(['git','-C',DEPLOY/'source','rev-parse','HEAD'])
                install.state['status']='installing'; install.finalize()
            else: install.install()
        except Exception as e:
            if install.state:
                install.state['status']='failed'; install.save()
            print('FAILED phase='+install.phase+': '+str(e),file=sys.stderr)
            print('Preserved all state/data. Root diagnostics: /etc/webdesktop-staging/install.log and systemctl/journalctl. See recovery guide; do not delete/re-run blindly.',file=sys.stderr)
            raise SystemExit(1)


if __name__=='__main__': main()
