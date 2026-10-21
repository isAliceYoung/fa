#!/usr/bin/env python3
"""Read-only staging audit, with explicitly requested session-replacing smoke."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import time

HERE=Path(__file__).resolve().parent
APP=Path('/opt/webdesktop/app')
STATE=Path('/etc/webdesktop-staging')


def cmd(args):
    r=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=60)
    assert r.returncode==0, 'Command failed: '+str(args[0])
    return r.stdout.strip()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--json',type=Path,required=True)
    p.add_argument('--live',action='store_true'); p.add_argument('--allow-session-replacement',action='store_true')
    a=p.parse_args(); assert os.geteuid()==0
    assert not a.live or a.allow_session_replacement, '--live replaces active login; explicit acknowledgement required'
    for f in (STATE,STATE/'state.json'):
        s=f.lstat(); assert s.st_uid==0 and not f.is_symlink() and not s.st_mode&0o077
    state=json.loads((STATE/'state.json').read_text())
    report=dict(schema=1,commit=state['commit'],at=time.time(),host=socket.gethostname(),passed=0,failed=0,skipped=0,not_executed=0,tests=[],resources={})
    def result(name,status,detail=''):
        report[status]+=1; report['tests'].append(dict(name=name,result=status,detail=detail))
        print(status.upper()+' '+name)
    def check(name,fn):
        try: fn(); result(name,'passed')
        except Exception as e: result(name,'failed',type(e).__name__+'; inspect protected service diagnostics')
    def require(value): assert value
    def code():
        require(state['status']=='ready')
        source=Path('/opt/webdesktop/source')
        require(cmd(['git','-C',source,'rev-parse','HEAD'])==state['commit'])
        require(not cmd(['git','-C',source,'status','--porcelain','--untracked-files=no']))
        spec=importlib.util.spec_from_file_location('bundle',HERE/'staging_bundle.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        manifest=json.loads((STATE/'artifact-manifest.json').read_text()); require(manifest['commit']==state['commit'])
        for name,item in manifest['files'].items():
            if not name.startswith('app/'): continue
            f=APP/name[4:]; s=f.lstat(); require(s.st_uid==0)
            if item['type']=='symlink': require(f.is_symlink() and os.readlink(f)==item['target'])
            else: require(stat.S_ISREG(s.st_mode) and not s.st_mode&0o022 and not f.is_symlink() and m.digest(f)==item['sha256'])
        require(cmd([APP/'node','-p','process.versions.node+"/"+process.versions.modules'])=='22.23.2/127')
    check('pinned source and every compiled artifact file',code)
    def identities():
        users=[pwd.getpwnam(x) for x in ('wdsvc','wdguest','wdterm','wdgui')]
        require(len({x.pw_uid for x in users})==4)
        svc=users[0].pw_gid
        for u in users[1:]: require(svc not in os.getgrouplist(u.pw_name,u.pw_gid))
        for u in users: require(u.pw_uid>0 and u.pw_shell=='/usr/sbin/nologin')
    check('distinct UIDs and no service group membership',identities)
    probe="""import os,sys
for p in sys.argv[1:]:
 for mode in (os.O_RDONLY,os.O_WRONLY):
  try: fd=os.open(p,mode)
  except PermissionError: continue
  except FileNotFoundError: raise SystemExit(2)
  else: os.close(fd);raise SystemExit(3)
"""
    for user in ('wdguest','wdterm','wdgui'):
        check(user+' kernel denies source/git/env/deployment secrets',lambda user=user:cmd(['runuser','-u',user,'--','python3','-I','-c',probe,
              '/opt/webdesktop/source/package.json','/opt/webdesktop/source/.git/config',APP/'.env',STATE/'login.json',STATE/'source.bundle',STATE/'state.json','/etc/ssh/ssh_host_ed25519_key']))
        check(user+' cannot signal service process',lambda user=user:cmd(['runuser','-u',user,'--','python3','-I','-c',
            'import os,sys\ntry:os.kill(int(sys.argv[1]),0)\nexcept PermissionError:pass\nelse:raise SystemExit(1)',
            cmd(['systemctl','show','webdesktop-staging.service','-p','MainPID','--value'])]))
        check(user+' cannot connect private service sockets',lambda user=user:cmd(['runuser','-u',user,'--','python3','-I','-c',
            "import socket,sys\nfor p in sys.argv[1:]:\n s=socket.socket(socket.AF_UNIX)\n try:s.connect(p)\n except PermissionError:pass\n else:raise SystemExit(1)\n finally:s.close()",
            '/var/lib/webdesktop/.webdesktop-http/next.sock','/run/webdesktop-guest/guest.sock','/run/webdesktop-browser/session-1.sock']))
    for unit in ('webdesktop-staging.service','postgresql@15-main.service','webdesktop-guest.socket',
                 'webdesktop-pty@1.socket','webdesktop-pty@2.socket','webdesktop-gui@1.socket','webdesktop-browser@1.socket',
                 'webdesktop-browser-network.service','home-wdguest.mount','webdesktop-guest-state.service','webdesktop-gui-state.service'):
        check('active '+unit,lambda unit=unit:cmd(['systemctl','is-active','--quiet',unit]))
    for unit,user in [('webdesktop-staging.service','wdsvc'),('webdesktop-guest.service','wdguest'),('webdesktop-pty@1.service','wdterm'),
                      ('webdesktop-gui@1.service','wdgui'),('webdesktop-browser@1.service','wdgui')]:
        def service_identity(unit=unit,user=user):
            require(cmd(['systemctl','show',unit,'-p','LoadState','--value'])=='loaded')
            require(cmd(['systemctl','show',unit,'-p','ActiveState','--value']) in ('active','inactive'))
            require(cmd(['systemctl','show',unit,'-p','User','--value'])==user)
            require(cmd(['systemctl','show',unit,'-p','NoNewPrivileges','--value'])=='yes')
            require(not cmd(['systemctl','show',unit,'-p','CapabilityBoundingSet','--value']))
        check('bounded unprivileged service/activation '+unit,service_identity)
    def listeners():
        for line in cmd(['ss','-H','-lnt']).splitlines():
            address=line.split()[3]; port=address.rsplit(':',1)[-1]
            if port=='3000': require(address=='127.0.0.1:3000')
            if port=='5432': require(address in ('127.0.0.1:5432','[::1]:5432'))
            require(not (port.isdigit() and 5900<=int(port)<=5999))
        require('127.0.0.1:3000' in cmd(['ss','-H','-lnt']))
    check('loopback Next and PostgreSQL; no TCP VNC',listeners)
    def volume():
        policy_path=Path('/etc/webdesktop-resources/policy.json')
        meta=policy_path.lstat();require(meta.st_uid==0 and not policy_path.is_symlink() and not meta.st_mode&0o022)
        policy=json.loads(policy_path.read_text()); require(policy==state['resource_policy'] and policy['policy']=='adaptive-v1')
        require(Path('/var/lib/webdesktop-guest-volume/workspace.ext4').stat().st_size==policy['workspace_mib']*1024**2)
        opts=cmd(['findmnt','-n','-o','OPTIONS','--target','/home/wdguest']).split(',')
        require(all(x in opts for x in ('nosuid','nodev','noexec')))
        require(sum(int(x.split()[2])*1024+os.sysconf('SC_PAGE_SIZE') for x in Path('/proc/swaps').read_text().splitlines()[1:])>=state['swap_target_mib']*1024**2)
        u=policy['units'];require(u['webdesktop-exec.slice']['MemoryMax']+policy['control_reserve_mib']*1024**2==u['webdesktop.slice']['MemoryMax'])
        require(u['webdesktop.slice']['MemoryMax']+policy['os_reserve_mib']*1024**2<=policy['effective']['memory_bytes'])
        for unit,settings in u.items():
            instance=unit.replace('@.service','@1.service')
            for field in ('MemoryMax','MemorySwapMax','TasksMax','CPUWeight','MemoryLow','OOMScoreAdjust','Slice'):
                if field in settings:require(cmd(['systemctl','show',instance,'-p',field,'--value'])==str(settings[field]))
        for name in ('webdesktop-pty@1.service','webdesktop-gui@1.service','webdesktop-browser@1.service'):
            require(u[name.replace('@1','@')]['Slice'].startswith('webdesktop-exec-'))
        for name in ('webdesktop.slice','webdesktop-exec.slice'):
            path=Path('/sys/fs/cgroup'+cmd(['systemctl','show',name,'-p','ControlGroup','--value']))
            require((path/'memory.max').read_text().strip()==str(u[name]['MemoryMax']))
            quota,period=(path/'cpu.max').read_text().split()
            require(quota!='max' and abs(int(quota)/int(period)*100-float(u[name]['CPUQuota'].rstrip('%')))<.1)
        require((Path('/sys/fs/cgroup')/'webdesktop.slice/memory.high').read_text().strip()=='max')
        for name in ('webdesktop-gui-lease@1.service','webdesktop-pty-lease@1.service'):
            require(not cmd(['systemctl','show',name,'-p','Slice','--value']).startswith('webdesktop-exec'))
    check('adaptive bounded volume, host reserve, execution/control separation and actual cgroup limits',volume)
    check('effective sshd rejects execution identities',lambda:cmd(['bash','/usr/local/lib/webdesktop-guest/check-ssh.sh']))
    check('sshd configuration and administrator service remain valid',lambda: (cmd(['/usr/sbin/sshd','-t']),cmd(['systemctl','is-active','--quiet','ssh'])))
    # An external login cannot be proved by a local static configuration test.
    result('external administrator SSH roundtrip','not_executed','CI driver verifies real SSH before/after installation and reboot; operator must repeat on VPS')
    if a.live:
        r=subprocess.run([str(APP/'node'),str(HERE/'staging_smoke.mjs')],capture_output=True,text=True,timeout=150)
        try:
            data=json.loads(r.stdout)
            for item in data['tests']: result(item['name'],item['result'],item.get('detail',''))
            require(r.returncode==0 or data['failed']>0)
        except Exception: result('authenticated runtime smoke execution','failed','Inspect services; no credential-bearing stderr copied')
    else:
        for name in ('file API','real PTY','PCManFM/RFB/Fcitx','visible Chromium sandbox','browser external HTTPS and management denial','GUI shutdown and restart'):
            result(name,'not_executed','Use --live --allow-session-replacement in an idle staging desktop')
    for name in ('Claude formal entry','web clipboard','file drag/drop','multi-app shared GUI','formal HTTPS domain'):
        result(name,'not_executed','Known limitation; outside this milestone')
    report['resources']['meminfo']=Path('/proc/meminfo').read_text(); report['resources']['swaps']=Path('/proc/swaps').read_text()
    report['resources']['disk']=cmd(['df','-PB1','/','/home/wdguest'])
    report['resources']['cgroups']={}
    report['resources']['policy']=state.get('resource_policy')
    for unit in ('webdesktop.slice','webdesktop-staging.service','postgresql@15-main.service','webdesktop-guest.service','webdesktop-exec.slice','webdesktop-exec-pty.slice','webdesktop-exec-gui.slice'):
        group=cmd(['systemctl','show',unit,'-p','ControlGroup','--value']); base=Path('/sys/fs/cgroup'+group)
        report['resources']['cgroups'][unit]={f:(base/f).read_text() for f in ('memory.current','memory.peak','memory.swap.current','memory.events','memory.events.local','memory.pressure','cpu.stat','pids.current') if group and (base/f).exists()}
    # Never follow an output symlink or overwrite someone else's report/data.
    with a.json.open('x') as f: json.dump(report,f,indent=2)
    a.json.chmod(0o600)
    print(json.dumps({k:report[k] for k in ('passed','failed','skipped','not_executed')}))
    return 1 if report['failed'] else 0


if __name__=='__main__': sys_exit=main(); raise SystemExit(sys_exit)
