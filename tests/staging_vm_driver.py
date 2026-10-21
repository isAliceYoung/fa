"""Reuse the existing disposable Debian boot/SSH controller; no real VPS targets."""
import hashlib
import json
from pathlib import Path
import subprocess
import shutil


def run_staging(root,out,work,ssh,remote,ready,boot):
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
    remote('sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends git ca-certificates python3',timeout=300)
    for file,target in [(source,'source.bundle'),(artifact,'runtime.tar.gz')]:
        with file.open('rb') as f:remote('sudo tee /root/staging-input/'+target+' >/dev/null',stdin=f,timeout=300)
    remote('sudo git clone /root/staging-input/source.bundle /root/staging-input/source')
    remote('sudo git -C /root/staging-input/source checkout --detach '+commit)
    remote('sudo chown -R root:root /root/staging-input && sudo chmod -R go-w /root/staging-input')
    host=remote('hostname',capture_output=True,text=True).stdout.strip()
    admins=remote("getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 {print $1}'",capture_output=True,text=True).stdout.split()
    assert all(x.replace('-','').isalnum() for x in admins)
    swap=int(next((x.split('=',1)[1] for x in __import__('sys').argv if x.startswith('--swap-mib=')),'1024'))
    args=' --commit '+commit+' --artifact /root/staging-input/runtime.tar.gz --artifact-sha256 '+checksum+' --authorize-new-vps '+host+('' if baseline else ' --swap-mib '+str(swap))+''.join(' --admin '+x for x in admins)
    install='sudo bash /root/staging-input/source/webdesktop-deploy/install-staging.sh'+args
    inv=remote('sudo bash /root/staging-input/source/webdesktop-deploy/install-staging.sh --inventory',capture_output=True,text=True)
    (out/'preflight-inventory.json').write_text(inv.stdout)
    generated=subprocess.run(ssh+['sudo systemctl cat systemd-growfs@-.service'],capture_output=True,text=True,timeout=15)
    (out/'os-root-growfs.txt').write_text(generated.stdout)
    # Missing explicit authorization cannot activate individual staging layers.
    no_auth=subprocess.run(ssh+['sudo bash /root/staging-input/source/webdesktop-deploy/scripts/setup-guest-layer.sh --staging'],capture_output=True,text=True,timeout=15)
    assert no_auth.returncode==64
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
    remote("sudo mkdir /opt/webdesktop && printf 'preserve-staging-fixture' | sudo tee /opt/webdesktop/unknown-data >/dev/null")
    rejected=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert rejected.returncode!=0
    assert remote('sudo cat /opt/webdesktop/unknown-data',capture_output=True,text=True).stdout=='preserve-staging-fixture'
    remote('sudo rm /opt/webdesktop/unknown-data && sudo rmdir /opt/webdesktop')
    try:
        remote(install,timeout=1500)
    finally:
        log=subprocess.run(ssh+['sudo cat /etc/webdesktop-staging/install.log'],capture_output=True,text=True,timeout=20)
        if log.returncode==0:(out/'install-redacted.log').write_text(log.stdout)
    repeated=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert repeated.returncode!=0
    previous=remote('sudo sha256sum /etc/webdesktop-staging/state.json /opt/webdesktop/app/.env',capture_output=True,text=True).stdout
    invalid_resume=subprocess.run(ssh+['sudo bash /opt/webdesktop/source/webdesktop-deploy/install-staging.sh --resume-finalize --commit '+commit],capture_output=True,text=True,timeout=15)
    assert invalid_resume.returncode!=0
    assert remote('sudo sha256sum /etc/webdesktop-staging/state.json /opt/webdesktop/app/.env',capture_output=True,text=True).stdout==previous
    remote('sudo install -d -m 700 /root/staging-results')
    check='sudo bash /opt/webdesktop/source/webdesktop-deploy/check-staging.sh --live --allow-session-replacement --json /root/staging-results/'
    try:
        with (root/'tests/staging_resource_runtime.mjs').open('rb') as stream:
            remote('sudo tee /root/staging-resource-runtime.mjs >/dev/null',stdin=stream)
        remote('sudo /opt/webdesktop/app/node /root/staging-resource-runtime.mjs'+(' --legacy' if baseline else ''),timeout=420)
        if not baseline:
            # Observer tools are disposable CI fixtures, not runtime dependencies.
            remote('sudo apt-get install -y --no-install-recommends x11-xserver-utils',timeout=120)
            with (root/'tests/staging_ui_probe.py').open('rb') as stream:
                remote('sudo tee /root/staging-ui-probe.py >/dev/null',stdin=stream)
            chrome=shutil.which('google-chrome') or shutil.which('chromium')
            assert chrome, 'Real host Chrome required; staging UI not executed'
            password=remote("sudo python3 -c 'import json;print(json.load(open(\"/etc/webdesktop-staging/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip()
            print('::add-mask::'+password,flush=True)
            tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                subprocess.run(['node',str(root/'tests/staging_ui_runtime.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out))),text=True,check=True,timeout=180)
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
        remote(check+'after.json',timeout=200)
        ssh_denials('after')
        (out/'reboot.json').write_text(json.dumps(dict(before=boot,after=after,administrator_ssh=True,missing_authorization_refused=True,unknown_user_refused=True,unknown_unit_refused=True,unknown_data_refused=True,repeat_refused=True),indent=2))
    finally:
        result=subprocess.run(ssh+['sudo tar -cf - -C /root staging-results'],capture_output=True,timeout=30)
        if result.returncode==0:(out/'health.tar').write_bytes(result.stdout)
        # No .env, login.json, private SSH keys, DB, or source bundle in CI evidence.
        for name,args2 in [('systemd-security.txt','sudo systemd-analyze security --no-pager webdesktop-staging.service webdesktop-browser@1.service webdesktop-gui@1.service'),
                           ('units.txt','sudo systemctl --no-pager status webdesktop-staging.service webdesktop-guest.socket webdesktop-browser-network.service')]:
            r=subprocess.run(ssh+[args2],capture_output=True,text=True,timeout=30);(out/name).write_text(r.stdout+r.stderr)
