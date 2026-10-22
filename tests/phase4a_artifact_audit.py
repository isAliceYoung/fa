"""Real root/ACL fault injection, only in the controller's disposable VM.

Run the exact historical verifier as well as the candidate against the same
installed tree. This records a pre-fix acceptance, not a mock DAC assertion.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys

if '--disposable-vm' not in sys.argv or os.geteuid()!=0 or socket.gethostname()!='wd-lab' or not Path('/var/tmp/wd-lab-ready').is_file():
    raise SystemExit('Explicit disposable VM required')
SOURCE=Path('/opt/webdesktop/source')
script=SOURCE/'webdesktop-deploy/scripts/isolated_deploy.py'
spec=importlib.util.spec_from_file_location('audit_candidate',script)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
state=json.loads((m.L.state/'state.json').read_text())
baseline_sha='e976ead282bef74924321a7cf2fd745717e1c442'
old=subprocess.check_output(['git','-C',str(SOURCE),'show',baseline_sha+':webdesktop-deploy/scripts/isolated_deploy.py'],text=True)
fn=next(x for x in ast.parse(old).body if isinstance(x,ast.FunctionDef) and x.name=='verify_artifact')
namespace=dict(vars(m));exec(compile(ast.Module(body=[fn],type_ignores=[]),'historical-verifier','exec'),namespace)
baseline=namespace['verify_artifact']
APP=m.APP;users=('wdguest','wdterm','wdgui')
out=Path('/root/staging-results/phase4a-artifact-audit.json')
report=dict(schema=1,baseline_sha=baseline_sha,commit=state['commit'],passed=0,failed=0,skipped=0,not_executed=12,tests=[])

def command(*args):subprocess.run(args,check=True,capture_output=True,text=True,timeout=20)
def require(ok):
    if not ok:raise ValueError('Artifact audit assertion failed')
def verify(rejected=False,historical=False):
    try:(baseline if historical else m.verify_artifact)(state)
    except ValueError:
        if rejected:return
        raise
    require(not rejected)
def access(path,user):
    # An actual open, not access(2), against the host DAC, no service namespace.
    code='''import os,sys,json
r=[]
for flags in (os.O_RDONLY,os.O_WRONLY):
 try:fd=os.open(sys.argv[1],flags)
 except PermissionError:r.append(False)
 else:os.close(fd);r.append(True)
print(json.dumps(r))
'''
    p=subprocess.run(['runuser','-u',user,'--','python3','-I','-c',code,str(path)],capture_output=True,text=True,check=True,timeout=10)
    return json.loads(p.stdout)
def denied(path):
    result={u:access(path,u) for u in users};require(all(v==[False,False] for v in result.values()));return result
def test(name,fn):
    report['not_executed']-=1
    try:
        evidence=fn();report['passed']+=1;report['tests'].append(dict(name=name,result='passed',evidence=evidence))
    except Exception as e:
        report['failed']+=1;report['tests'].append(dict(name=name,result='failed',detail=type(e).__name__+': '+str(e)));raise
    finally:out.write_text(json.dumps(report,indent=2))

file=APP/'server.mjs';directory=APP/'src/lib';gid=pwd.getpwnam('wdsvc').pw_gid
def acl(path,default=False):
    before=(file.stat().st_mode,hashlib.sha256(file.read_bytes()).hexdigest())
    for u in users:command('setfacl','-m',('d:' if default else '')+'u:'+u+':r-x' if path.is_dir() else 'u:'+u+':r--',str(path))
    try:
        verify(historical=True);verify(rejected=True)
        if path==file:require(before==(file.stat().st_mode,hashlib.sha256(file.read_bytes()).hexdigest()))
        return dict(historical_accepts=True,candidate_rejects=True,guest_open=denied(file),mode_and_digest_unchanged=path==file)
    finally:command('setfacl','-k' if default else '-b',str(path))

def extra(is_dir):
    p=APP/'r1-unknown';require(not os.path.lexists(p))
    if is_dir:p.mkdir(mode=0o750)
    else:p.write_text('non-secret fault fixture');p.chmod(0o640)
    os.chown(p,0,gid)
    try:verify(historical=True);verify(rejected=True);require(p.exists());return dict(retained=True,historical_accepts=True)
    finally:p.rmdir() if is_dir else p.unlink()

try:
    test('unchanged installed release and required service access',lambda:(verify(),require(access(file,'wdsvc')==[True,False]),denied(file))[-1])
    test('named file ACL with identical mode and digest',lambda:acl(file))
    test('named nested directory ACL',lambda:acl(directory))
    test('default nested directory ACL',lambda:acl(directory,True))
    test('unknown immutable ordinary file refused and retained',lambda:extra(False))
    test('unknown immutable empty directory refused and retained',lambda:extra(True))
    def replace_link():
        saved=APP/'r1-saved-server';file.rename(saved);file.symlink_to('package.json')
        try:verify(rejected=True,historical=True);verify(rejected=True);return dict(both_reject=True)
        finally:file.unlink();saved.rename(file)
    test('ordinary file replacement with symlink refused',replace_link)
    def legitimate():
        manifest=json.loads((m.L.state/'artifact-manifest.json').read_text())
        links=[k for k,v in manifest['files'].items() if k.startswith('app/') and v['type']=='symlink']
        require(len(links)>0);verify();return dict(legitimate_links=len(links))
    test('all packaged legitimate Node links retained',legitimate)
    def mode_change():
        mode=file.stat().st_mode;file.chmod(0o644)
        try:verify(historical=True);verify(rejected=True);return dict(guest_open=denied(file),historical_accepts=True)
        finally:file.chmod(mode)
    test('unchanged content but world-readable mode refused',mode_change)
    def composed():
        parents=(m.shared.DEPLOY,APP);modes=[p.stat().st_mode for p in parents]
        for p in parents:p.chmod(0o755)
        for u in users:command('setfacl','-m','u:'+u+':r--',str(file))
        try:
            verify(historical=True)
            actual={u:access(file,u) for u in users};require(all(v==[True,False] for v in actual.values()))
            verify(rejected=True)
            return dict(historical_accepts=True,actual_guest_open=actual,candidate_rejects=True)
        finally:
            command('setfacl','-b',str(file))
            for p,mode in zip(parents,modes):p.chmod(mode)
    test('composed traversal mode drift plus file ACL actually exposes protected server code before fix',composed)
    def optimized():
        p=APP/'r1-unknown';p.write_text('retained');p.chmod(0o640);os.chown(p,0,gid)
        try:
            for flag in ('-O','-OO'):
                r=subprocess.run(['python3','-I',flag,str(script),'--mode','service','--commit',state['commit']],capture_output=True,text=True,timeout=60)
                require(r.returncode==1 and p.read_text()=='retained')
            return dict(optimizations=['-O','-OO'],both_reject=True)
        finally:p.unlink()
    test('optimized recovery refuses unknown runtime files without deleting them',optimized)
    test('all faults restored with service and guest DAC intact',lambda:(verify(),require(access(file,'wdsvc')==[True,False]),denied(file))[-1])
finally:out.write_text(json.dumps(report,indent=2))
print(json.dumps({k:report[k] for k in ('passed','failed','skipped','not_executed')}))
