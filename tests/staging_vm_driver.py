"""Reuse the existing disposable Debian boot/SSH controller; no real VPS targets."""
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import time


def run_staging(root,out,work,ssh,remote,ready,boot):
    isolated='--isolated' in __import__('sys').argv
    state='/etc/webdesktop-isolated' if isolated else '/etc/webdesktop-staging'
    entry='install-isolated.sh' if isolated else 'install-staging.sh'
    backend='webdesktop-isolated.service' if isolated else 'webdesktop-staging.service'
    checkout='/opt/webdesktop/source' if isolated else '/root/staging-input/source'
    baseline='--baseline' in __import__('sys').argv
    commit='f195f90c01f38fcf93c20ee616f9e62a15063018' if baseline else subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    artifact=root/'.test-results/staging-runtime.tar.gz'
    h=hashlib.sha256()
    with artifact.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    checksum=h.hexdigest()
    if baseline:
        import importlib.util
        spec=importlib.util.spec_from_file_location('staging_bundle',root/'webdesktop-deploy/scripts/staging_bundle.py')
        bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
        bundle.extract(artifact,checksum,commit,work/'baseline')
        source=work/'baseline/source.bundle'
    else:
        source=work/'source.bundle'
        subprocess.run(['git','bundle','create',str(source),'HEAD'],cwd=root,check=True)
    remote('sudo install -d -m 700 /root/staging-input')
    # Minimal tools needed to obtain a trusted Git checkout, also documented for
    # an administrator preparing a stock cloud image. No application fixtures.
    remote('sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends git ca-certificates python3'+(' acl' if isolated else ''),timeout=300)
    for file,target in [(source,'source.bundle'),(artifact,'runtime.tar.gz')]:
        with file.open('rb') as f:remote('sudo tee /root/staging-input/'+target+' >/dev/null',stdin=f,timeout=300)
    remote('sudo chmod 600 /root/staging-input/runtime.tar.gz')
    remote('sudo git clone /root/staging-input/source.bundle /root/staging-input/source')
    remote('sudo git -C /root/staging-input/source checkout --detach '+commit)
    remote('sudo chown -R root:root /root/staging-input && sudo chmod -R go-w /root/staging-input')
    if isolated:
        # Test-only provenance fixture. No production Deploy Key or GitHub writes.
        # PrivateSource authorization/denial and actual keys are independently
        # exercised by test_private_repo.py and linux_private_repo.py.
        remote('sudo mkdir -m 700 /opt/webdesktop && sudo mv /root/staging-input/source /opt/webdesktop/source && sudo chmod 700 /opt/webdesktop/source /opt/webdesktop/source/.git')
        remote('sudo git -C /opt/webdesktop/source remote set-url origin git@github.com:isAliceYoung/fa.git')
        provenance=json.dumps(dict(schema=1,repository='git@github.com:isAliceYoung/fa.git',branch='feature/private-deploy-isolation',commit=commit))
        import shlex
        remote('printf %s '+shlex.quote(provenance)+' | sudo tee /opt/webdesktop/source/.git/webdesktop-source.json >/dev/null && sudo chmod 600 /opt/webdesktop/source/.git/webdesktop-source.json')
    host=remote('hostname',capture_output=True,text=True).stdout.strip()
    admins=remote("getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 {print $1}'",capture_output=True,text=True).stdout.split()
    assert all(x.replace('-','').isalnum() for x in admins)
    swap=int(next((x.split('=',1)[1] for x in __import__('sys').argv if x.startswith('--swap-mib=')),'1024'))
    args=' --commit '+commit+' --artifact /root/staging-input/runtime.tar.gz --artifact-sha256 '+checksum+' --authorize-new-vps '+host+('' if baseline else ' --swap-mib '+str(swap))+''.join(' --admin '+x for x in admins)
    if isolated:args+=' --accept-verified-subset'
    install='sudo bash '+checkout+'/webdesktop-deploy/'+entry+args
    inv=remote('sudo bash '+checkout+'/webdesktop-deploy/install-staging.sh --inventory',capture_output=True,text=True)
    (out/'preflight-inventory.json').write_text(inv.stdout)
    generated=subprocess.run(ssh+['sudo systemctl cat systemd-growfs@-.service'],capture_output=True,text=True,timeout=15)
    (out/'os-root-growfs.txt').write_text(generated.stdout)
    # Missing explicit authorization cannot activate individual staging layers.
    no_auth=subprocess.run(ssh+['sudo bash '+checkout+'/webdesktop-deploy/scripts/setup-guest-layer.sh '+('--isolated' if isolated else '--staging')],capture_output=True,text=True,timeout=15)
    assert no_auth.returncode==64
    if isolated:
        # Real CLI failures must precede accounts, mount/network and DB changes.
        # Demonstrate the bypass that merely protecting the published app would
        # miss: a root-owned but readable download is another source copy.
        remote('sudo install -m 644 /root/staging-input/runtime.tar.gz /var/tmp/phase4a-public-artifact.tar.gz')
        try:
            remote('sudo runuser -u nobody -- head -c 1 /var/tmp/phase4a-public-artifact.tar.gz >/dev/null')
            denied=subprocess.run(ssh+[install.replace('/root/staging-input/runtime.tar.gz','/var/tmp/phase4a-public-artifact.tar.gz')],capture_output=True,text=True,timeout=40)
            assert denied.returncode!=0 and 'Unexpected protected mode' in denied.stdout
            remote('sudo test ! -e /etc/webdesktop-isolated && ! getent passwd wdsvc >/dev/null')
            remote('sudo chmod 600 /var/tmp/phase4a-public-artifact.tar.gz')
            inaccessible=subprocess.run(ssh+['sudo runuser -u nobody -- head -c 1 /var/tmp/phase4a-public-artifact.tar.gz >/dev/null'],capture_output=True,text=True,timeout=10)
            assert inaccessible.returncode!=0
        finally:remote('sudo rm /var/tmp/phase4a-public-artifact.tar.gz')
        for bad in (install.replace(checksum,'0'*64),install.replace(commit,'0'*40),install.replace(' --accept-verified-subset',''),install.replace(' --authorize-new-vps '+host,'')):
            denied=subprocess.run(ssh+[bad],capture_output=True,text=True,timeout=40)
            assert denied.returncode!=0
            remote('sudo test ! -e /etc/webdesktop-isolated && ! getent passwd wdsvc >/dev/null')
        remote('sudo git -C /opt/webdesktop/source remote set-url origin git@github.com:untrusted/other.git')
        try:
            denied=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=40)
            assert denied.returncode!=0
        finally:remote('sudo git -C /opt/webdesktop/source remote set-url origin git@github.com:isAliceYoung/fa.git')
        remote("sudo python3 -c 'import os; assert \"system.posix_acl_default\" not in os.listxattr(\"/var/cache\")'")
        remote('sudo setfacl -m d:u:nobody:rx /var/cache')
        try:
            denied=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=40)
            assert denied.returncode!=0 and 'Extended ACL' in denied.stdout
        finally:remote('sudo setfacl -k /var/cache')
    remote('sudo useradd --system --no-create-home --shell /usr/sbin/nologin staging-unknown-user')
    unknown_user=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert unknown_user.returncode!=0
    remote('sudo userdel staging-unknown-user')
    remote("printf '[Service]\\nExecStart=/bin/true\\n' | sudo tee /etc/systemd/system/staging-unknown.service >/dev/null")
    unknown_unit=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert unknown_unit.returncode!=0
    remote('sudo test -f /etc/systemd/system/staging-unknown.service && sudo rm /etc/systemd/system/staging-unknown.service')
    remote("sudo install -d /usr/local/lib/systemd/system && printf '[Service]\\nType=oneshot\\nExecStart=/bin/true\\nRemainAfterExit=yes\\n' | sudo tee /usr/local/lib/systemd/system/staging-vendor-fixture.service >/dev/null")
    remote('sudo systemctl daemon-reload && sudo systemctl start staging-vendor-fixture.service')
    foreign=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert foreign.returncode!=0
    remote('sudo systemctl stop staging-vendor-fixture.service && sudo rm /usr/local/lib/systemd/system/staging-vendor-fixture.service && sudo systemctl daemon-reload')
    # Known test fixture only. Refusal must retain unknown data, not erase/adopt it.
    remote(("true" if isolated else "sudo mkdir /opt/webdesktop")+" && printf 'preserve-staging-fixture' | sudo tee /opt/webdesktop/unknown-data >/dev/null")
    rejected=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert rejected.returncode!=0
    assert remote('sudo cat /opt/webdesktop/unknown-data',capture_output=True,text=True).stdout=='preserve-staging-fixture'
    remote('sudo rm /opt/webdesktop/unknown-data'+('' if isolated else ' && sudo rmdir /opt/webdesktop'))
    if isolated:
        (out/'phase4a-preflight.json').write_text(json.dumps(dict(passed=12,failed=0,skipped=0,not_executed=0,
          tests=['readable artifact refused before deployment and mode 0600 denies actual reader','missing layer authorization','wrong independent artifact digest','wrong pinned commit','missing subset acknowledgement','missing hostname authorization',
                 'untrusted source repository','extended inherited ACL','unknown identity','unknown local unit','unregistered active vendor unit','unknown data retained']),indent=2))
    try:
        if isolated:
            remote('sudo install -d -m 700 /root/final-start-guard')
            # Match real enable --now semantics: the enable phase succeeds,
            # then inject failure of its activation phase. Recovery may start
            # the frozen unit but must not need to perform enable itself.
            wrapper='#!/bin/sh\nif [ "$*" = "enable --now webdesktop-isolated.service" ]; then /usr/bin/systemctl enable webdesktop-isolated.service || exit; exit 55; fi\nexec /usr/bin/systemctl "$@"\n'
            remote('printf %s '+shlex.quote(wrapper)+' | sudo tee /root/final-start-guard/systemctl >/dev/null && sudo chmod 700 /root/final-start-guard/systemctl')
            injected=install.replace('sudo bash ','sudo env PATH=/root/final-start-guard:/usr/sbin:/usr/bin:/sbin:/bin bash ',1)
            failure=subprocess.run(ssh+[injected],capture_output=True,text=True,timeout=1500)
            assert failure.returncode==1
            remote("sudo python3 -c 'import json; s=json.load(open(\"/etc/webdesktop-isolated/state.json\")); assert (s[\"status\"],s[\"phase\"])==(\"failed\",\"start-service\")'")
            before=remote('sudo sha256sum '+state+'/login.json /opt/webdesktop/app/.env',capture_output=True,text=True).stdout
            remote('sudo bash '+checkout+'/webdesktop-deploy/install-isolated.sh --mode service --commit '+commit,timeout=120)
            assert before==remote('sudo sha256sum '+state+'/login.json /opt/webdesktop/app/.env',capture_output=True,text=True).stdout
            (out/'phase4a-failure-recovery.json').write_text(json.dumps(dict(passed=1,failed=0,skipped=0,not_executed=0,injected_systemctl_exit=55,installer_exit=1,phase='start-service',service_recovered=True,credentials_retained=True),indent=2))
        else:remote(install,timeout=1500)
    finally:
        log=subprocess.run(ssh+['sudo cat '+state+'/install.log'],capture_output=True,text=True,timeout=20)
        if log.returncode==0:(out/'install-redacted.log').write_text(log.stdout)
    repeated=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert repeated.returncode==(0 if isolated else 1), repeated.stdout+repeated.stderr
    previous=remote('sudo sha256sum '+state+'/state.json /opt/webdesktop/app/.env',capture_output=True,text=True).stdout
    invalid_resume=subprocess.run(ssh+[('sudo bash /opt/webdesktop/source/webdesktop-deploy/install-isolated.sh --mode service --commit '+'0'*40) if isolated else 'sudo bash /opt/webdesktop/source/webdesktop-deploy/install-staging.sh --resume-finalize --commit '+commit],capture_output=True,text=True,timeout=15)
    assert invalid_resume.returncode!=0
    assert remote('sudo sha256sum '+state+'/state.json /opt/webdesktop/app/.env',capture_output=True,text=True).stdout==previous
    remote('sudo install -d -m 700 /root/staging-results')
    check='sudo bash /opt/webdesktop/source/webdesktop-deploy/'+('check-isolated.sh' if isolated else 'check-staging.sh')+' --live --allow-session-replacement --json /root/staging-results/'
    try:
        if isolated:
            with (root/'tests/phase4a_modes.py').open('rb') as stream:
                remote('sudo tee /root/phase4a-modes.py >/dev/null',stdin=stream)
            remote('sudo python3 -I /root/phase4a-modes.py --disposable-vm',timeout=480)
        if not baseline:
            # Only this disposable observer VM needs the extra startup logs.
            # Shipped unit limits are unchanged; /run drops this on reboot.
            for observed in ('webdesktop-browser@1.service','webdesktop-gui@1.service'):
                remote("sudo install -d /run/systemd/system/"+observed+".d && printf '[Service]\\nLogRateLimitBurst=128\\n' | sudo tee /run/systemd/system/"+observed+".d/lab-observation.conf >/dev/null")
            remote('sudo systemctl daemon-reload')
        with (root/'tests/staging_resource_runtime.mjs').open('rb') as stream:
            remote('sudo tee /root/staging-resource-runtime.mjs >/dev/null',stdin=stream)
        remote('sudo /opt/webdesktop/app/node /root/staging-resource-runtime.mjs'+(' --legacy' if baseline else ' --isolated' if isolated else ''),timeout=420)
        if not baseline:
            # Observer tools are disposable CI fixtures, not runtime dependencies.
            remote('sudo apt-get install -y --no-install-recommends x11-xserver-utils',timeout=120)
            with (root/'tests/staging_ui_probe.py').open('rb') as stream:
                remote('sudo tee /root/staging-ui-probe.py >/dev/null',stdin=stream)
            chrome=shutil.which('google-chrome') or shutil.which('chromium')
            assert chrome, 'Real host Chrome required; staging UI not executed'
            password=remote("sudo python3 -c 'import json;print(json.load(open(\""+state+"/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip()
            print('::add-mask::'+password,flush=True)
            tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                subprocess.run(['node',str(root/'tests/staging_ui_runtime.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out))),text=True,check=True,timeout=180)
                if isolated:
                    subprocess.run(['node',str(root/'tests/pty_ui_runtime.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,out=str(out))),text=True,check=True,timeout=150)
            finally:
                tunnel.terminate();tunnel.wait(timeout=10)
        remote(check+'before.json',timeout=200)
        def ssh_denials(stage):
            pub=(work/'admin.pub').read_text().strip()
            results=[]
            for user,home in [('wdsvc','/var/lib/webdesktop'),('wdguest','/home/wdguest'),('wdterm','/var/lib/webdesktop-terminal'),('wdgui','/var/lib/webdesktop-gui')]:
                remote('sudo install -d -m 700 -o '+user+' -g '+user+' '+home+'/.ssh')
                remote("printf '%s\\n' '"+pub+"' | sudo tee "+home+'/.ssh/authorized_keys >/dev/null')
                remote('sudo chown '+user+':'+user+' '+home+'/.ssh/authorized_keys && sudo chmod 600 '+home+'/.ssh/authorized_keys')
                r=subprocess.run(ssh[:-1]+[user+'@127.0.0.1','true'],capture_output=True,text=True,timeout=8)
                assert r.returncode==255 and 'Permission denied' in r.stderr
                results.append(dict(user=user,denied=True,exit=r.returncode))
                remote('sudo rm '+home+'/.ssh/authorized_keys && sudo rmdir '+home+'/.ssh')
            (out/('ssh-denials-'+stage+'.json')).write_text(json.dumps(results,indent=2))
        ssh_denials('before')
        # Genuine administrator SSH and a genuine reboot, not an inherited FD test.
        remote('true')
        subprocess.run(ssh+['sudo systemctl reboot'],check=False)
        after=ready(boot); assert after!=boot
        if isolated:
            # SSH starts in parallel with PostgreSQL and this backend. Its
            # readiness is not completion of the boot transaction. Wait for
            # systemd's actual settled state, without starting any unit or
            # masking a failed boot with the recovery command below.
            started=time.monotonic()
            settled=subprocess.run(ssh+['sudo systemctl is-system-running --wait'],capture_output=True,text=True,timeout=90)
            (out/'boot-settlement.json').write_text(json.dumps(dict(state=settled.stdout.strip(),exit=settled.returncode,elapsed_ms=round((time.monotonic()-started)*1000)),indent=2))
            assert settled.returncode==0 and settled.stdout.strip()=='running', 'System boot did not settle successfully'
            remote('sudo systemctl is-enabled --quiet webdesktop-isolated.service')
            remote('sudo systemctl is-active --quiet webdesktop-isolated.service')
            remote('sudo bash /opt/webdesktop/source/webdesktop-deploy/install-isolated.sh --mode service --commit '+commit,timeout=120)
        remote(check+'after.json',timeout=200)
        ssh_denials('after')
        (out/'reboot.json').write_text(json.dumps(dict(before=boot,after=after,administrator_ssh=True,missing_authorization_refused=True,unknown_user_refused=True,unknown_unit_refused=True,unknown_data_refused=True,repeat_refused=not isolated,repeat_preserved=True),indent=2))
    finally:
        result=subprocess.run(ssh+['sudo tar -cf - -C /root staging-results'],capture_output=True,timeout=30)
        if result.returncode==0:(out/'health.tar').write_bytes(result.stdout)
        # No .env, login.json, private SSH keys, DB, or source bundle in CI evidence.
        for name,args2 in [('systemd-security.txt','sudo systemd-analyze security --no-pager '+backend+' webdesktop-browser@1.service webdesktop-gui@1.service'),
                           ('units.txt','sudo systemctl --no-pager status '+backend+' webdesktop-guest.socket webdesktop-browser-network.service')]:
            r=subprocess.run(ssh+[args2],capture_output=True,text=True,timeout=30);(out/name).write_text(r.stdout+r.stderr)
