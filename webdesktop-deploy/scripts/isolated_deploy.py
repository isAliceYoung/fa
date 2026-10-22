#!/usr/bin/env python3
"""Phase 4A opt-in deployment of the verified subset, not full-product acceptance.

Fresh installs share the 3D engine. Recovery never invokes installation, Git
acquisition, package managers, key preparation, network configuration or a build.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import time
import urllib.request

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('shared_install',HERE/'staging_install.py')
shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)
L=shared.Layout('isolated')
APP=shared.APP
require=shared.require
cmd=shared.command
SEAL=L.state/'recovery.json'


def file_record(path):
    import pwd
    p=Path(path);s=p.lstat()
    shared.reject_acl(p)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and not s.st_mode&0o022,'Unsafe configuration file: '+str(p))
    require(s.st_uid in (0,pwd.getpwnam('postgres').pw_uid),'Untrusted configuration owner')
    return dict(sha256=shared.bundle.digest(p),uid=s.st_uid,gid=s.st_gid,mode=stat.S_IMODE(s.st_mode))


def configurations():
    import pwd
    """Immutable configuration only; never snapshot DB, profile or user data."""
    paths={APP/'.env',L.state/'login.json',L.state/'artifact-manifest.json',Path('/etc/fstab'),Path('/etc/webdesktop-resources/policy.json')}
    for root in ('/usr/local/lib/webdesktop-guest','/usr/local/lib/webdesktop-pty','/usr/local/lib/webdesktop-gui','/usr/local/lib/webdesktop-browser',
                 '/etc/webdesktop-guest','/etc/postgresql/15/main'):
        base=Path(root)
        pg=pwd.getpwnam('postgres').pw_uid
        meta=base.lstat()
        shared.reject_acl(base)
        require(stat.S_ISDIR(meta.st_mode) and not meta.st_mode&0o022 and meta.st_uid in (0,pg if root.startswith('/etc/postgresql/') else 0),'Unsafe configuration directory')
        for directory,dirs,files in os.walk(base,followlinks=False):
            meta=Path(directory).lstat()
            shared.reject_acl(Path(directory))
            require(stat.S_ISDIR(meta.st_mode) and not meta.st_mode&0o022 and meta.st_uid in (0,pg if root.startswith('/etc/postgresql/') else 0),'Unsafe nested configuration directory')
            for d in dirs: require(not (Path(directory)/d).is_symlink(),'Configuration directory symlink')
            for f in files: paths.add(Path(directory)/f)
    for directory in ('/etc/systemd/system','/etc/tmpfiles.d','/etc/polkit-1/rules.d','/etc/ssh/sshd_config.d','/etc/chromium/policies/recommended'):
        for p in Path(directory).iterdir():
            if 'webdesktop' not in p.name and p.name!='home-wdguest.mount':continue
            if p.is_symlink():
                # enable links live in *.wants, not among these unit definitions.
                raise ValueError('Unexpected configuration symlink: '+str(p))
            if p.is_dir():
                shared.owned(p)
                for child in p.iterdir():paths.add(child)
            else:paths.add(p)
    return paths


def identities():
    import pwd
    records={}
    for name in ('wdsvc','wdguest','wdterm','wdgui'):
        u=pwd.getpwnam(name);groups=sorted(os.getgrouplist(name,u.pw_gid))
        records[name]=dict(uid=u.pw_uid,gid=u.pw_gid,groups=groups,home=u.pw_dir,shell=u.pw_shell)
        require(u.pw_uid>0 and u.pw_shell=='/usr/sbin/nologin','Invalid execution account')
    require(len({v['uid'] for v in records.values()})==4,'UIDs must differ')
    svc=records['wdsvc']['gid']
    for name in ('wdguest','wdterm','wdgui'):require(svc not in records[name]['groups'],'Sensitive group shared')
    return records


def fixed_state(state):
    return {k:state[k] for k in ('schema','deployment_kind','authorized_new_vps','branch','commit','artifact_sha256','administrators','origins','resource_policy','resource_inputs','swap_target_mib')}


def unit_bindings():
    units={L.unit,'postgresql@15-main.service','home-wdguest.mount','polkit.service','dbus.service'}
    for p in Path('/etc/systemd/system').glob('webdesktop*'):
        if p.suffix not in ('.service','.socket','.slice'):continue
        units.add(p.name.replace('@.service','@1.service').replace('@.socket','@1.socket'))
    result={}
    for unit in sorted(units):
        fragment=cmd(['systemctl','show',unit,'-p','FragmentPath','--value'])
        require(fragment,'Missing known unit '+unit)
        drops=cmd(['systemctl','show',unit,'-p','DropInPaths','--value']).split()
        # Each loaded override must be on the frozen, non-volatile config tree.
        for p in [fragment,*drops]:require(p.startswith(('/etc/systemd/system/','/lib/systemd/system/','/usr/lib/systemd/system/')),'Volatile/untrusted unit override')
        result[unit]=dict(fragment=fragment,dropins=drops,files={p:file_record(p) for p in [fragment,*drops]})
    return result


def write_new(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    os.fchmod(fd,mode)
    with os.fdopen(fd,'w') as f:f.write(data);f.flush();os.fsync(f.fileno())


def network_state():
    # Read only. No command here creates an interface, rule, namespace or route.
    rules=cmd(['nft','-nn','list','ruleset'])
    routes=json.loads(cmd(['ip','-n','wd-browser','-j','route','show']))
    require(cmd(['sysctl','-n','net.ipv4.ip_forward'])=='1','Browser forwarding is absent')
    require(cmd(['ip','netns','exec','wd-browser','sysctl','-n','net.ipv6.conf.all.disable_ipv6'])=='1','Browser IPv6 policy differs')
    return dict(rules_sha256=hashlib.sha256(rules.encode()).hexdigest(),routes=routes)


def seal(state):
    require(not os.path.lexists(SEAL),'Recovery seal already exists; no re-baselining')
    record=dict(schema=1,state=fixed_state(state),identities=identities(),
                files={str(p):file_record(p) for p in sorted(configurations())},bindings=unit_bindings(),network=network_state(),
                backend_text=(Path('/etc/systemd/system')/L.unit).read_text())
    write_new(SEAL,json.dumps(record,sort_keys=True,indent=2))


def verify_artifact(state):
    shared.validate_bootstrap(state['commit'],fresh=False)
    # These are immutable release trees. Runtime data belongs outside APP;
    # .env is the sole installer-created file, frozen by configurations().
    import pwd
    gid=pwd.getpwnam('wdsvc').pw_gid
    for directory in (shared.DEPLOY,APP):
        shared.owned(directory,0o750)
        require(directory.lstat().st_gid==gid,'Protected release group differs')
    manifest=json.loads((L.state/'artifact-manifest.json').read_text())
    require(manifest['commit']==state['commit'],'Artifact commit differs')
    expected_files={'.env'};expected_dirs={'.'}
    for name,item in manifest['files'].items():
        if not name.startswith('app/'):continue
        shared.bundle.safe_name(name)
        relative=Path(name[4:]);expected_files.add(relative.as_posix())
        expected_dirs.update(p.as_posix() for p in relative.parents)
        p=APP/relative;s=p.lstat()
        shared.reject_acl(p)
        require(s.st_uid==0,'Runtime is not root-managed')
        if item['type']=='symlink':
            # Linux symlink mode is always 0777 and is not access control.
            # Root ownership + exact reviewed in-package target remain required.
            shared.bundle.link_target(name,item['target'])
            require(p.is_symlink() and os.readlink(p)==item['target'],'Runtime link differs')
        else:
            mode=0o750 if item.get('mode',0)&0o111 else 0o640
            require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_gid==gid and stat.S_IMODE(s.st_mode)==mode and s.st_size==item['size'] and shared.bundle.digest(p)==item['sha256'],'Runtime differs: '+name)
    actual_files=set();actual_dirs=set()
    def walk_error(error):raise error
    for directory,dirs,files in os.walk(APP,followlinks=False,onerror=walk_error):
        base=Path(directory);s=base.lstat();shared.reject_acl(base)
        require(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and s.st_gid==gid and stat.S_IMODE(s.st_mode)==0o750,'Runtime directory permissions differ')
        actual_dirs.add(base.relative_to(APP).as_posix())
        for name in dirs+files:
            p=base/name;relative=p.relative_to(APP).as_posix()
            if p.is_symlink() or name in files:actual_files.add(relative)
        dirs[:]=[name for name in dirs if not (base/name).is_symlink()]
    require(actual_files==expected_files and actual_dirs==expected_dirs,'Unknown/missing immutable runtime entry; preserve and review')


def load_state(commit):
    shared.owned(L.state,0o700)
    for name in ('state.json','recovery.json'):
        shared.owned(L.state/name,0o600)
        require((L.state/name).lstat().st_nlink==1 and (L.state/name).is_file(),'Invalid state file')
    state=json.loads((L.state/'state.json').read_text());record=json.loads(SEAL.read_text())
    require(state['deployment_kind']=='isolated' and state['authorized_new_vps'] is True,'Not a known isolated install')
    require(state['schema']==1 and record['schema']==1 and state['branch']==shared.BRANCH,'Unknown state schema/branch')
    require(commit==state['commit'],'Use the exact installed commit; no implicit upgrade')
    require((state['status'],state['phase']) in (('ready','ready'),('failed','start-service'),('installing','start-service')),'Earlier incomplete phase requires manual recovery, not adoption')
    require(record['state']==fixed_state(state) and record['identities']==identities(),'State or identity changed')
    verify_artifact(state)
    return state,record


def validate_configuration(record,allow_missing_backend=False):
    backend=str(Path('/etc/systemd/system')/L.unit)
    missing=[]
    actual={str(p) for p in configurations()}
    for p,expected in record['files'].items():
        if allow_missing_backend and p==backend and not os.path.lexists(p):missing.append(p);continue
        require(file_record(p)==expected,'Configuration changed: '+p)
    require(actual==set(record['files'])-set(missing),'Unknown/missing configuration; refuse automatic repair')
    return missing


def recover(mode,commit):
    state,record=load_state(commit)
    missing=validate_configuration(record,allow_missing_backend=mode=='repair')
    require(network_state()==record['network'],'Network policy differs; recovery will not reconfigure it')
    if missing:
        # Only a missing exact backend unit is reconstructable. Never overwrite.
        write_new(Path(missing[0]),record['backend_text'],0o644)
        cmd(['systemctl','daemon-reload'])
    require(unit_bindings()==record['bindings'],'Loaded unit binding differs')
    # Recovery must not silently undo an administrator's disable decision or
    # label an installation ready when it would disappear on the next boot.
    cmd(['systemctl','is-enabled','--quiet',L.unit])
    for unit in record['bindings']:
        require(cmd(['systemctl','show',unit,'-p','NeedDaemonReload','--value'])=='no','Unloaded configuration change; review explicitly')
    # Debian starts polkit on demand through D-Bus. An idle, loaded unit is
    # healthy after reboot; its frozen binding/rules still must match. Do not
    # start it, ignore a failed state, or change the authorization policy here.
    require(cmd(['systemctl','show','polkit.service','-p','ActiveState','--value']) in ('active','inactive'),'Authorization service failed')
    # Do not activate network or mounts in service mode. Their absence is a
    # dependency error, not permission to rebuild namespaces/firewall/fstab.
    for unit in ('postgresql@15-main.service','home-wdguest.mount','webdesktop-browser-network.service',
                 'webdesktop-guest-state.service','webdesktop-gui-state.service','webdesktop-display.service','dbus.service'):
        cmd(['systemctl','is-active','--quiet',unit])
    cmd(['bash','/usr/local/lib/webdesktop-guest/check-ssh.sh'])
    # A pre-existing socket must retain kernel DAC even before any activation.
    for directory in ('/run/webdesktop-guest','/run/webdesktop-pty','/run/webdesktop-gui','/run/webdesktop-browser'):
        shared.owned(directory,0o750)
        gid=pwd_gid('wdsvc')
        require(Path(directory).lstat().st_gid==gid,'Private socket directory group differs')
        for p in Path(directory).glob('*.sock'):
            s=p.lstat()
            require(stat.S_ISSOCK(s.st_mode) and s.st_uid==0 and s.st_gid==gid and stat.S_IMODE(s.st_mode)==0o660,'Private IPC permissions changed')
    for unit in ('webdesktop-guest.socket','webdesktop-pty@1.socket','webdesktop-pty@2.socket',
                 'webdesktop-pty-lease@1.socket','webdesktop-pty-lease@2.socket','webdesktop-gui@1.socket',
                 'webdesktop-gui-lease@1.socket','webdesktop-browser@1.socket',L.unit):
        cmd(['systemctl','start',unit])
    shared.probe_guest()
    for attempt in range(60):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:3000/api/health',timeout=2) as response:
                if response.status==200:break
        except Exception:pass
        time.sleep(.5)
    else:raise RuntimeError('Backend readiness failed; preserve data and inspect systemd diagnostics')
    cmd(['systemctl','is-active','--quiet',L.unit])
    pid=cmd(['systemctl','show',L.unit,'-p','MainPID','--value'])
    listeners=cmd(['ss','-H','-lntp','( sport = :3000 )'])
    import re
    require(int(pid)>0 and listeners and all(line.split()[3]=='127.0.0.1:3000' and set(re.findall(r'pid=(\d+)',line))=={pid} for line in listeners.splitlines()),'Listener does not belong to the known backend')
    if state['status']!='ready':
        state.update(status='ready',phase='ready')
        write_new(L.state/'recovered.new',json.dumps(state,indent=2));(L.state/'recovered.new').replace(L.state/'state.json')
    print('VERIFIED isolated subset: existing credentials, database, volume and profile retained; no installation/network changes.')


def pwd_gid(user):
    import pwd
    return pwd.getpwnam(user).pw_gid


class IsolatedInstall(shared.Install):
    def finalize(self):
        # A seal exists even when the final backend start fails. Earlier phases
        # intentionally have no automatic resume; unknown/partial data is kept.
        seal(self.state)
        super().finalize()


def main():
    import fcntl
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('auto','install','repair','service'),default='auto')
    p.add_argument('--commit',required=True)
    p.add_argument('--artifact',type=Path);p.add_argument('--artifact-sha256')
    p.add_argument('--authorize-new-vps');p.add_argument('--accept-verified-subset',action='store_true')
    p.add_argument('--admin',action='append',default=[])
    p.add_argument('--origins',default='http://127.0.0.1:3000,http://127.0.0.1:18890')
    p.add_argument('--swap-mib',type=int,choices=(0,1024,2048,4096),default=1024)
    for n in ('workspace-mib','execution-memory-mib','browser-memory-mib'):p.add_argument('--'+n,type=int)
    a=p.parse_args()
    require(hasattr(os,'geteuid') and os.geteuid()==0,'root required')
    os.umask(0o077)
    fd=os.open('/run/lock/webdesktop-staging.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o077,'Unsafe shared deployment lock')
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if os.path.lexists('/etc/webdesktop-staging') or os.path.lexists('/etc/webdesktop-deploy.conf'):
            raise ValueError('Existing staging/legacy layout: independent reviewed migration required')
        if L.state.exists() or L.state.is_symlink():
            recover(a.mode,a.commit);return
        require(a.mode in ('auto','install'),'No known isolated install; recovery cannot install')
        require(a.accept_verified_subset,'Explicit --accept-verified-subset required: incomplete 3C remains disabled')
        require(a.artifact is not None and a.artifact_sha256 is not None,'Fixed artifact and independent digest required')
        install=IsolatedInstall(a,L)
        try:install.install()
        except Exception:
            if install.state:install.state['status']='failed';install.save()
            print('FAILED phase='+install.phase+'; state/data retained; inspect protected '+str(L.state/'install.log'))
            raise


if __name__=='__main__':
    try:main()
    except Exception as e:
        # No command stderr, environment, login seeds or credentials in output.
        print('Isolated deployment refused: '+type(e).__name__+'; '+str(e))
        raise SystemExit(1)
