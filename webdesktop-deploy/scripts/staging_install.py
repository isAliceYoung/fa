#!/usr/bin/env python3
"""Fresh-server staged installation. No SSH client, remote execution or migration."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
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

class Layout:
    """Fixed root-controlled layouts; never accept a path/unit from visitor input."""
    def __init__(self, kind='staging'):
        if kind not in ('staging','isolated'): raise ValueError('Unknown deployment profile')
        self.kind=kind
        self.state=Path('/etc/webdesktop-'+kind)
        self.unit='webdesktop-'+kind+'.service'
        self.work=Path('/var/cache/webdesktop-'+kind)
        self.swap='/var/lib/webdesktop-'+kind+'.swap'
        self.layer_flag='--'+kind

STAGING=Layout()
spec=importlib.util.spec_from_file_location('staging_bundle',HERE/'staging_bundle.py')
bundle=importlib.util.module_from_spec(spec); spec.loader.exec_module(bundle)
resource_spec=importlib.util.spec_from_file_location('resource_policy',HERE/'resource_policy.py')
resources=importlib.util.module_from_spec(resource_spec); resource_spec.loader.exec_module(resources)


def require(value, message="Deployment safety check failed"):
    # Runtime validation must survive python -O/-OO.
    if not value:
        raise ValueError(message)


def command(args, timeout=180, input=None):
    r=subprocess.run([str(x) for x in args],input=input,text=True,capture_output=True,timeout=timeout)
    if r.returncode:
        raise RuntimeError('Command failed: '+str(args[0])+' (exit '+str(r.returncode)+')')
    return r.stdout.strip()


def owned(path, mode=None):
    p=Path(path); s=p.lstat()
    require(s.st_uid==0 and not p.is_symlink() and not s.st_mode & 0o022, 'Unsafe root-owned path: '+str(path))
    reject_acl(p)
    if mode is not None: require(stat.S_IMODE(s.st_mode)==mode, 'Unexpected protected mode: '+str(path))


def reject_acl(path):
    if hasattr(os,'listxattr'):
        require(not {'system.posix_acl_access','system.posix_acl_default'} & set(os.listxattr(path,follow_symlinks=False)), 'Extended ACL requires explicit review: '+str(path))


def local_git(source, *args):
    return command(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',
                    '-c','core.untrackedCache=false','-C',source,*args])


def probe_guest(runner=command):
    # Fixed read-only protocol request, no desktop cookie, arbitrary path or
    # backend environment. IPC failure is a deployment failure, never fallback.
    code='''import socket,json,pwd
s=socket.socket(socket.AF_UNIX);s.settimeout(5);s.connect('/run/webdesktop-guest/guest.sock')
s.sendall(b'GET /v1/info HTTP/1.1\\r\\nHost: guest\\r\\nConnection: close\\r\\n\\r\\n')
data=b''
while len(data)<8192:
 block=s.recv(8192-len(data))
 if not block:break
 data+=block
s.close()
head,body=data.split(b'\\r\\n\\r\\n',1)
if head.split(b' ',2)[1]!=b'200':raise SystemExit('Guest IPC unavailable')
r=json.loads(body)
if r.get('uid')!=pwd.getpwnam('wdguest').pw_uid or r.get('protocol')!=2:raise SystemExit('Guest IPC identity mismatch')
'''
    runner(['runuser','-u','wdsvc','--','env','-i','PATH=/usr/bin:/bin','LANG=C.UTF-8','python3','-I','-c',code],timeout=15)


def validate_bootstrap(commit, fresh=True):
    """Only the exact root checkout prepared by private_repo may be adopted.

    This is offline: GitHub authorization/key/host trust remain in bootstrap.sh.
    An artifact digest is an external operator trust input, not its own signature.
    """
    source=DEPLOY/'source'
    require(REPO==source, 'Run isolated entry from the bootstrap checkout /opt/webdesktop/source')
    owned(DEPLOY); owned(source,0o700); owned(source/'.git',0o700)
    record=source/'.git/webdesktop-source.json'; owned(record,0o600)
    require(record.lstat().st_nlink==1 and record.is_file(), 'Unsafe bootstrap manifest')
    data=json.loads(record.read_text())
    require(data==dict(schema=1,repository='git@github.com:isAliceYoung/fa.git',branch=BRANCH,commit=commit), 'Bootstrap repository/branch/commit mismatch')
    require(local_git(source,'remote','get-url','origin')=='git@github.com:isAliceYoung/fa.git', 'Unexpected private repository origin')
    require(local_git(source,'rev-parse','HEAD')==commit, 'Bootstrap commit differs')
    require(not local_git(source,'status','--porcelain','--untracked-files=normal'), 'Bootstrap checkout is dirty')
    require(not os.path.lexists(source/'.git/objects/info/alternates'), 'External Git object store is not supported')
    if fresh: require({p.name for p in DEPLOY.iterdir()}=={'source'}, 'Existing deployment/data: migration is not supported')


def origins(value):
    from urllib.parse import urlsplit
    result=[]
    for v in value.split(','):
        u=urlsplit(v)
        require(u.scheme=='http' and u.hostname in ('127.0.0.1','localhost') and u.port and 1<=u.port<=65535)
        require(not u.username and not u.password and not u.path and not u.query and not u.fragment)
        require(v==u.scheme+'://'+u.netloc)
        result.append(v)
    require('http://127.0.0.1:3000' in result and len(result)<=8)
    return ','.join(dict.fromkeys(result))


def known_root_growfs(text):
    """Exact executable policy for systemd 252's completed root-fs generator.

    See upstream src/shared/generator.c:generator_hook_up_growfs. This does not
    authorize a custom service, drop-in, additional command or another mount.
    """
    if not text.startswith(('# Automatically generated by systemd-fstab-generator\n','# Automatically generated by systemd-gpt-auto-generator\n')): return False
    section=None; found=set()
    fixed={('Unit','DefaultDependencies'):'no',('Unit','BindsTo'):'%i.mount',('Unit','Conflicts'):'shutdown.target',
           ('Service','Type'):'oneshot',('Service','RemainAfterExit'):'yes',('Service','TimeoutSec'):'0'}
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        if line in ('[Unit]','[Service]'): section=line[1:-1]; continue
        if '=' not in line or section is None: return False
        key,value=line.split('=',1); pair=(section,key);found.add(pair)
        if pair in fixed:
            if value!=fixed[pair]:return False
        elif section=='Unit' and key in ('Description','Documentation'): pass
        elif pair==('Unit','After'):
            if value not in ('systemd-repart.service %i.mount','systemd-remount-fs.service'):return False
        elif pair==('Unit','Before'):
            if not value.split() or 'shutdown.target' not in value.split() or set(value.split())-{'shutdown.target','local-fs.target','initrd-root-fs.target'}:return False
        elif pair==('Service','ExecStart'):
            if value not in ('/lib/systemd/systemd-growfs /','/usr/lib/systemd/systemd-growfs /'):return False
        else:return False
    return set(fixed)|{('Service','ExecStart')} <= found


def inventory():
    import pwd
    users=[x.pw_name for x in pwd.getpwall() if 1000<=x.pw_uid<65534]
    return dict(host=socket.gethostname(),users=users,cpu=os.cpu_count(),meminfo=Path('/proc/meminfo').read_text(),
                disk=shutil.disk_usage('/')._asdict(),swap=Path('/proc/swaps').read_text(),
                ports=command(['ss','-lntup']),units=command(['systemctl','list-unit-files','--state=enabled','--no-pager']))


def preflight(a, layout=STAGING):
    STATE=layout.state
    import pwd
    import grp
    require(os.geteuid()==0, 'root required')
    release=dict(x.split('=',1) for x in Path('/etc/os-release').read_text().splitlines() if '=' in x)
    require(release.get('ID','').strip('"')=='debian' and release.get('VERSION_ID','').strip('"')=='12')
    require(command(['dpkg','--print-architecture'])=='amd64' and '2.36' in command(['getconf','GNU_LIBC_VERSION']))
    require(Path('/proc/1/comm').read_text().strip()=='systemd' and Path('/sys/fs/cgroup/cgroup.controllers').exists())
    require(a.authorize_new_vps==socket.gethostname(), '--authorize-new-vps must equal the actual hostname')
    require(re.fullmatch('[0-9a-f]{40}',a.commit or ''), 'Full lowercase commit SHA required')
    require(command(['git','-C',REPO,'rev-parse','HEAD'])==a.commit, 'Installer checkout must match the pinned commit')
    require(not command(['git','-C',REPO,'status','--porcelain','--untracked-files=no']), 'Tracked installer checkout is dirty')
    for p in (REPO,*REPO.parents): owned(p)
    for p in ('/etc','/var','/var/cache','/var/lib','/usr/local/lib'):owned(p)
    # Refuse unknown download policy before any account/package/data mutation.
    for p in ('/etc/chromium','/etc/chromium/policies','/etc/chromium/policies/managed'):
        if os.path.lexists(p):
            owned(p)
            require(Path(p).is_dir() and not Path(p).is_symlink(), 'Unknown Chromium policy directory')
    managed=Path('/etc/chromium/policies/managed')
    require(not managed.exists() or not list(managed.iterdir()), 'Existing managed Chromium policies require review')
    require(re.fullmatch('[0-9a-f]{64}',a.artifact_sha256 or ''), 'Independent artifact SHA256 required')
    require(a.artifact.is_absolute(), 'Artifact requires an absolute protected path')
    owned(a.artifact,0o600)
    for parent in a.artifact.parents: owned(parent)
    artifact_info=a.artifact.lstat()
    require(stat.S_ISREG(artifact_info.st_mode) and artifact_info.st_nlink==1, 'Artifact must be a regular root-owned file')
    a.origins=origins(a.origins)
    require(a.admin and set(inventory()['users'])<=set(a.admin), 'Unknown interactive users: review inventory and explicitly name administrators')
    baseline={'root','daemon','bin','sys','sync','games','man','lp','mail','news','uucp','proxy','www-data','backup','list','irc','_apt','nobody',
              'systemd-network','systemd-timesync','systemd-resolve','systemd-coredump','messagebus','sshd','uuidd','polkitd','chrony','ntp','tcpdump'}
    require({u.pw_name for u in pwd.getpwall()}<=baseline|set(a.admin), 'Unknown system account: review fresh-server inventory')
    for name in a.admin:
        account=pwd.getpwnam(name)
        require(account.pw_shell in ('/bin/bash','/bin/sh','/bin/dash','/usr/bin/bash'))
        require(name not in ('wdsvc','wdguest','wdterm','wdgui','wdtunnel'))
    ssh=os.environ.get('SSH_CONNECTION','').split()
    require(not ssh or ':' not in ssh[0], 'IPv6-only administrator session: IPv4-only change needs independent review')
    require(not command(['ss','-H','-6','-tn','state','established','( sport = :22 )']), 'Active IPv6 SSH session: stop before IPv4-only changes')
    mem=int(re.search(r'MemTotal:\s+(\d+)',Path('/proc/meminfo').read_text())[1])*1024
    require(mem>=900*1024**2 and os.cpu_count()>=1, 'At least nominal 1 GiB RAM and 1 CPU required')
    require(shutil.disk_usage('/').free>=6*1024**3, 'At least 6 GiB free before installation required')
    require(not STATE.exists() and not STATE.is_symlink(), 'Existing staging state: no second installation; inspect recovery instructions')
    if layout.kind=='isolated': validate_bootstrap(a.commit)
    for p in ('/etc/webdesktop-isolated','/var/cache/webdesktop-isolated','/var/lib/webdesktop-isolated.swap','/opt/webdesktop','/etc/webdesktop','/etc/webdesktop-deploy.conf','/etc/webdesktop-guest','/etc/postgresql','/var/lib/postgresql','/var/lib/webdesktop-deploy',
              '/var/lib/webdesktop','/var/lib/webdesktop-guest-volume','/usr/local/lib/webdesktop-guest',
              '/usr/local/lib/webdesktop-pty','/usr/local/lib/webdesktop-gui','/usr/local/lib/webdesktop-browser',
              '/etc/netns/wd-browser','/etc/webdesktop-resources','/var/lib/webdesktop-staging.swap','/var/cache/webdesktop-staging',
              '/home/wdguest','/var/lib/webdesktop-terminal','/var/lib/webdesktop-gui','/etc/sysctl.d/90-webdesktop-ipv4-only.conf'):
        if p=='/opt/webdesktop' and layout.kind=='isolated': continue  # exact bootstrap-only parent validated above
        require(not os.path.lexists(p), 'Existing data/runtime requires review: '+p)
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
            require('webdesktop' not in p.name and not p.name.startswith('wdlab'), 'Existing project policy: '+str(p))
            if p.suffix in ('.service','.socket','.mount','.timer','.path') and p.is_file() and not p.is_symlink():
                require(subprocess.run(['dpkg-query','-S',str(p)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0, 'Unknown local service: '+str(p))
    services=set()
    for args in (['list-units','--type=service','--state=active'],['list-unit-files','--type=service','--state=enabled']):
        for line in command(['systemctl',*args,'--no-legend','--no-pager']).splitlines():
            services.add(line.split()[0])
    for name in sorted(services):
        # show requires an instance for enabled templates such as getty@.
        # Loading metadata does not start the synthetic instance.
        query=name.replace('@.service','@staging-preflight.service')
        try: fragment=command(['systemctl','show',query,'-p','FragmentPath','--value'])
        except RuntimeError: raise RuntimeError('Cannot inspect service unit: '+name)
        require(fragment, 'Unknown transient service: '+name)
        owned(fragment)
        variants={fragment,fragment.replace('/usr/lib/','/lib/',1) if fragment.startswith('/usr/lib/') else fragment.replace('/lib/','/usr/lib/',1)}
        if name=='systemd-growfs@-.service' and fragment=='/run/systemd/generator/systemd-growfs@-.service':
            require(known_root_growfs(Path(fragment).read_text()), 'Unrecognized root growfs unit content')
            require(not command(['systemctl','show',name,'-p','DropInPaths','--value']))
            require(command(['systemctl','show',name,'-p','MainPID','--value'])=='0')
            require(command(['systemctl','show',name,'-p','Result','--value'])=='success')
            require(any(subprocess.run(['dpkg-query','-S',f],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0 for f in ('/lib/systemd/systemd-growfs','/usr/lib/systemd/systemd-growfs')))
            continue
        require(any(subprocess.run(['dpkg-query','-S',f],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0 for f in variants), 'Unregistered running/enabled service: '+name)
    for line in command(['ss','-H','-lntp']).splitlines():
        address=line.split()[3]
        if address.rsplit(':',1)[-1]=='22': continue
        # Debian cloud images may ship the OS resolver. This narrow loopback
        # exception is not an application/management service adoption.
        if address.replace('%lo:',':') in ('127.0.0.53:53','127.0.0.54:53','0.0.0.0:5355','[::]:5355'):
            require(command(['systemctl','is-active','systemd-resolved.service'])=='active')
            pid=command(['systemctl','show','systemd-resolved.service','-p','MainPID','--value'])
            require(int(pid)>0 and set(re.findall(r'pid=(\d+)',line))=={pid}, 'Resolver port belongs to an unexpected process')
            continue
        raise RuntimeError('Unexpected TCP listener '+address+'; stop for manual review')
    # Do not merge arbitrary firewall, routes or namespaces with this test model.
    if shutil.which('nft'): require(not command(['nft','list','ruleset']), 'Existing nftables policy requires review')
    for tool in ('iptables-legacy-save','ip6tables-legacy-save'):
        if shutil.which(tool): require(not command([tool]), 'Existing legacy firewall policy requires review')
    require(not command(['ip','netns','list']), 'Unknown network namespace requires review')
    require(not Path('/run/netns/wd-browser').exists())
    require(subprocess.run(['ip','link','show','wdb-host'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0)
    command(['/usr/sbin/sshd','-t'])
    return inventory()


class Install:
    def __init__(self,a,layout=STAGING):
        self.layout=layout
        self.a=a; self.state=None; self.secrets=[]; self.phase='preflight'

    def save(self):
        p=self.layout.state/'state.json'; temporary=self.layout.state/'state.new'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f: json.dump(self.state,f,indent=2); f.flush(); os.fsync(f.fileno())
        temporary.replace(p)

    def stage(self,name):
        self.phase=name; self.state['phase']=name; self.save(); print('STAGE '+name,flush=True)

    def run(self,args,timeout=600):
        r=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=timeout,
                         cwd=APP if APP.exists() else REPO,
                         env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','LC_ALL':'C.UTF-8'})
        text=r.stdout+'\n'+r.stderr
        for value in self.secrets: text=text.replace(value,'[REDACTED]')
        with (self.layout.state/'install.log').open('a') as log: log.write('STAGE '+self.phase+' / '+str(args[0])+'\n'+text+'\n')
        if r.returncode: raise RuntimeError('Command failed: '+str(args[0])+' exit='+str(r.returncode))
        return r.stdout.strip()

    def write(self,path,data,mode=0o644):
        p=Path(path); require(not os.path.lexists(p), 'Unknown target exists: '+str(p))
        p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('x') as f: f.write(data)
        p.chmod(mode)

    def install(self):
        inv=preflight(self.a,self.layout)
        # Validate and extract BEFORE changing accounts, sshd, DB or networks.
        work=self.layout.work
        require(not os.path.lexists(work), 'Unknown staging extraction directory')
        manifest=bundle.extract(self.a.artifact,self.a.artifact_sha256,self.a.commit,work)
        self.layout.state.mkdir(mode=0o700)
        self.write(self.layout.state/'install.log','',0o600)
        self.state=dict(schema=1,authorized_new_vps=True,branch=BRANCH,commit=self.a.commit,
                        deployment_kind=self.layout.kind,artifact_sha256=self.a.artifact_sha256,phase='verified-artifact',status='installing',
                        administrators=self.a.admin,origins=self.a.origins,inventory=inv,swap_target_mib=self.a.swap_mib)
        self.save()
        self.stage('ipv4-and-swap')
        self.run(['bash','-c','. "$1"; WD_IPV4_ONLY=1; apply_ipv4_only','staging',HERE/'common.sh'])
        areas=Path('/proc/swaps').read_text().splitlines()[1:]
        active=sum(int(x.split()[2])*1024+os.sysconf('SC_PAGE_SIZE') for x in areas)
        if active<self.a.swap_mib*1024**2:
            size=((self.a.swap_mib*1024**2-active+1024**2-1)//1024**2)*1024**2
            require(shutil.disk_usage('/var/lib').free>size+3*1024**3, 'Swap would consume reserve')
            self.write(self.layout.swap,'',0o600)
            self.run(['fallocate','-l',str(size),self.layout.swap])
            self.run(['mkswap',self.layout.swap]); self.run(['swapon',self.layout.swap])
            with Path('/etc/fstab').open('a') as f: f.write('\n'+self.layout.swap+' none swap sw 0 0\n')
        self.stage('packages')
        self.write('/var/lib/webdesktop-deploy/postgresql-15-main-initializing','',0o600)
        Path('/var/lib/webdesktop-deploy').chmod(0o700)
        self.run(['apt-get','-o','DPkg::Lock::Timeout=180','update'])
        gui_packages=(REPO/'gui-runtime/debian12-packages.txt').read_text().splitlines()
        require(all(re.fullmatch('[a-z0-9][a-z0-9+.-]+',p) for p in gui_packages))
        self.run(['apt-get','-o','DPkg::Lock::Timeout=180','install','-y','--no-install-recommends',
                  'postgresql','polkitd','git','curl','ca-certificates','python3','e2fsprogs','util-linux','iproute2',
                  'nftables','chromium',*gui_packages],1200)
        self.stage('source-and-artifact')
        if self.layout.kind=='staging': DEPLOY.mkdir(mode=0o700)
        require(command(['git','bundle','list-heads',work/'source.bundle']).split()[0]==self.a.commit)
        if self.layout.kind=='staging':
            self.run(['git','-c','core.hooksPath=/dev/null','-c','credential.helper=','clone','--template=','--no-checkout',work/'source.bundle',DEPLOY/'source'])
            self.run(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-C',DEPLOY/'source','checkout','--detach',self.a.commit])
        self.run(['git','-C',DEPLOY/'source','fsck','--no-reflogs'])
        (work/'app').rename(APP)
        self.run([APP/'node','-e',"if(process.versions.node!=='22.23.2'||process.versions.modules!=='127')process.exit(1);require('sharp');require('node-pty')"])
        shutil.copy2(work/'manifest.json',self.layout.state/'artifact-manifest.json')
        shutil.copy2(work/'source.bundle',self.layout.state/'source.bundle')
        self.stage('resource-policy')
        overrides={k:getattr(self.a,k) for k in ('workspace_mib','execution_memory_mib','browser_memory_mib') if getattr(self.a,k,None) is not None}
        resource_inputs=resources.detect()
        self.state['resource_inputs']=resource_inputs;self.save()
        policy=resources.calculate(resource_inputs,overrides)
        if self.layout.kind=='isolated': policy['units'][self.layout.unit]=policy['units'].pop('webdesktop-staging.service')
        resources.install(policy)
        # This newly/exclusively created directory contains only non-secret
        # budgets. Match its existing intended 0755 even under root umask 077.
        resources.POLICY.parent.chmod(0o755)
        self.state['resource_policy']=policy
        self.save()
        self.stage('identities-and-layers')
        scripts=DEPLOY/'source/webdesktop-deploy/scripts'
        for name in ('guest-layer','guest-volume','pty-layer','gui-layer','browser-layer'):
            self.run(['bash',scripts/('setup-'+name+'.sh'),self.layout.layer_flag])
        probe_guest(self.run)
        self.stage('database-and-configuration')
        # Fresh cluster only. Do not copy or rewrite an old server's database.
        self.run(['env','WD_USER=wdsvc','WD_APP_DIR=/var/lib/webdesktop/database-tools','WD_BRANCH='+BRANCH,
                  'bash',scripts/'install-postgres.sh'])
        self.run([APP/'node',scripts/'bootstrap.cjs','create-env',APP])
        cfg=dict(line.split('=',1) for line in (APP/'.env').read_text().splitlines() if '=' in line)
        self.secrets=[v for k,v in cfg.items() if k in ('DATABASE_URL','ADMIN_PASSWORD','DESKTOP_PASSWORD','AUTH_KEY')]
        from urllib.parse import urlsplit
        self.secrets.append(urlsplit(cfg['DATABASE_URL']).password)
        with (APP/'.env').open('a') as f: f.write('WD_PUBLIC_ORIGINS='+self.a.origins+'\nUPSTREAM_BRANCH='+BRANCH+'\n')
        self.run([APP/'node',scripts/'bootstrap.cjs','database',APP])
        require(self.run([APP/'node',scripts/'bootstrap.cjs','schema-state',APP])=='empty')
        self.run([APP/'node',APP/'node_modules/drizzle-kit/bin.cjs','push','--force'])
        self.write(self.layout.state/'login.json',json.dumps({k:cfg[k] for k in ('ADMIN_USER','ADMIN_PASSWORD','ADMIN_PATH','DESKTOP_PASSWORD')},indent=2)+'\n',0o600)
        self.stage('units-and-budget')
        self.run(['chown','-R','root:wdsvc',APP])
        self.run(['chmod','-R','o-rwx',APP]); self.run(['chmod','-R','g+rX',APP])
        # Source is root-only. Backend gets only its compiled application package.
        self.run(['chmod','700',DEPLOY/'source']); self.run(['chmod','640',APP/'.env'])
        self.run(['chown','root:wdsvc',DEPLOY]); self.run(['chmod','750',DEPLOY,APP])
        template=(DEPLOY/'source/webdesktop-deploy/templates/webdesktop-staging.service').read_text()
        if self.layout.kind=='isolated': template=template.replace('pinned staging backend','isolated verified-subset backend (Phase 4A candidate)')
        self.write(Path('/etc/systemd/system')/self.layout.unit,template.replace('@ORIGINS@',self.a.origins).replace('@COMMIT@',self.a.commit))
        self.run(['systemctl','daemon-reload'])
        self.run(['systemd-analyze','verify',self.layout.unit,'webdesktop-browser@1.service','webdesktop-gui@1.service','webdesktop-pty@1.service'])
        self.run(['systemctl','enable','--now','webdesktop-exec.slice'])
        self.run(['systemctl','enable','--now','webdesktop-guest.socket'])
        self.run(['systemctl','restart','postgresql@15-main.service'])
        # Metadata extraction directory is known and contains no live data.
        # Keep it for diagnosis; do not remove unknown directories on failure.
        self.finalize()

    def finalize(self):
        # Also redact when entering through the deliberately limited resume path.
        cfg=dict(line.split('=',1) for line in (APP/'.env').read_text().splitlines() if '=' in line)
        from urllib.parse import urlsplit
        self.secrets=[v for k,v in cfg.items() if k in ('DATABASE_URL','ADMIN_PASSWORD','DESKTOP_PASSWORD','AUTH_KEY') and v]
        password=urlsplit(cfg['DATABASE_URL']).password
        if password: self.secrets.append(password)
        self.stage('start-service')
        self.run(['systemctl','enable','--now',self.layout.unit])
        for attempt in range(60):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:3000/api/health',timeout=2) as response:
                    if response.status==200: break
            except Exception: pass
            time.sleep(.5)
        else:
            # Diagnose once under the SAME UID, privilege and memory boundaries.
            # Capture/redact through the root controller, never public HTTP.
            for args in [
                ['systemctl','show',self.layout.unit,'-p','Result','-p','ExecMainStatus','-p','MemoryCurrent','-p','MemoryPeak'],
                ['journalctl','--no-pager','-n','80','-u',self.layout.unit],
                ['systemctl','stop',self.layout.unit],
                ['systemd-run','--quiet','--wait','--pipe','--collect','--unit=webdesktop-staging-startup-probe',
                 '-p','User=wdsvc','-p','Group=wdsvc','-p','WorkingDirectory='+str(APP),'-p','Slice=webdesktop.slice',
                 '-p','NoNewPrivileges=yes','-p','CapabilityBoundingSet=','-p','MemoryMax='+str(self.state['resource_policy']['units'][self.layout.unit]['MemoryMax']),'-p','MemorySwapMax='+str(self.state['resource_policy']['units'][self.layout.unit]['MemorySwapMax']),
                 '-p','TasksMax=64','-p','RuntimeMaxSec=20','-p','KillMode=control-group',
                 '/usr/bin/env','-i','HOME=/var/lib/webdesktop','USER=wdsvc','LOGNAME=wdsvc','PATH=/usr/bin:/bin','NODE_ENV=production',
                 'NEXT_TELEMETRY_DISABLED=1','NEXT_PUBLIC_WD_COMMIT='+self.state['commit'],'WD_PUBLIC_ORIGINS='+self.state['origins'],
                 APP/'node','--input-type=module','-e',
                 "const {startGateway}=await import('file:///opt/webdesktop/app/server.mjs');try{const g=await startGateway();await g.close();}catch(e){console.error(e.stack);process.exitCode=1;}"]
            ]:
                try: self.run(args,45)
                except Exception: pass
            raise RuntimeError('HTTP health readiness timeout; bounded startup diagnostics preserved in root install.log')
        self.run([APP/'node',DEPLOY/'source/webdesktop-deploy/scripts/bootstrap.cjs','auth-state',APP])
        self.state['status']='ready'; self.stage('ready')
        print('READY: loopback 127.0.0.1:3000. Credentials: '+str(self.layout.state/'login.json')+' (root 0600).')
        print('Run check-staging.sh; follow STAGING_TEST_GUIDE.md. No Claude/updates enabled.')


def main():
    import fcntl
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',action='store_true'); p.add_argument('--commit'); p.add_argument('--artifact',type=Path)
    p.add_argument('--artifact-sha256'); p.add_argument('--authorize-new-vps'); p.add_argument('--admin',action='append',default=[])
    p.add_argument('--origins',default='http://127.0.0.1:3000,http://127.0.0.1:18890')
    p.add_argument('--resource-plan',action='store_true',help='Read-only hardware/cgroup calculation; does not install')
    p.add_argument('--swap-mib',type=int,choices=(0,1024,2048,4096),default=1024,help='Only add missing swap; 0 preserves existing swap without adding')
    for name in ('workspace-mib','execution-memory-mib','browser-memory-mib'): p.add_argument('--'+name,type=int)
    p.add_argument('--resume-finalize',action='store_true'); a=p.parse_args()
    require(os.geteuid()==0, 'root required')
    if a.inventory: print(json.dumps(inventory(),indent=2)); return
    if a.resource_plan:
        print(json.dumps(resources.calculate(resources.detect(),{k:getattr(a,k) for k in ('workspace_mib','execution_memory_mib','browser_memory_mib') if getattr(a,k) is not None}),indent=2)); return
    lockfd=os.open('/run/lock/webdesktop-staging.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    info=os.fstat(lockfd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o077, 'Unsafe installer lock')
    with os.fdopen(lockfd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        install=Install(a)
        try:
            if a.resume_finalize:
                owned(STATE,0o700); owned(STATE/'state.json',0o600)
                candidate=json.loads((STATE/'state.json').read_text())
                require(candidate['phase']=='start-service' and candidate['status']=='failed')
                require(a.commit==candidate['commit']==command(['git','-C',DEPLOY/'source','rev-parse','HEAD']))
                install.state=candidate
                install.state['status']='installing'; install.finalize()
            else: install.install()
        except Exception as e:
            if install.state:
                install.state['status']='failed'; install.save()
            print('FAILED phase='+install.phase+': '+str(e),file=sys.stderr)
            print('Preserved all state/data. Root diagnostics: /etc/webdesktop-staging/install.log and systemctl/journalctl. See recovery guide; do not delete/re-run blindly.',file=sys.stderr)
            raise SystemExit(1)


if __name__=='__main__': main()
