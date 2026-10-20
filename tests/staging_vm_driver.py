"""Reuse the existing disposable Debian boot/SSH controller; no real VPS targets."""
import hashlib
import json
from pathlib import Path
import subprocess


def run_staging(root,out,work,ssh,remote,ready,boot):
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    artifact=root/'.test-results/staging-runtime.tar.gz'
    h=hashlib.sha256()
    with artifact.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    checksum=h.hexdigest()
    source=work/'source.bundle'
    subprocess.run(['git','bundle','create',str(source),'HEAD'],cwd=root,check=True)
    remote('sudo install -d -m 700 /root/staging-input')
    for file,target in [(source,'source.bundle'),(artifact,'runtime.tar.gz')]:
        with file.open('rb') as f:remote('sudo tee /root/staging-input/'+target+' >/dev/null',stdin=f,timeout=300)
    remote('sudo git clone /root/staging-input/source.bundle /root/staging-input/source')
    remote('sudo git -C /root/staging-input/source checkout --detach '+commit)
    remote('sudo chown -R root:root /root/staging-input && sudo chmod -R go-w /root/staging-input')
    host=remote('hostname',capture_output=True,text=True).stdout.strip()
    admins=remote("getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 {print $1}'",capture_output=True,text=True).stdout.split()
    assert all(x.replace('-','').isalnum() for x in admins)
    args=' --commit '+commit+' --artifact /root/staging-input/runtime.tar.gz --artifact-sha256 '+checksum+' --authorize-new-vps '+host+''.join(' --admin '+x for x in admins)
    install='sudo bash /root/staging-input/source/webdesktop-deploy/install-staging.sh'+args
    # Known test fixture only. Refusal must retain unknown data, not erase/adopt it.
    remote("sudo mkdir /opt/webdesktop && printf 'preserve-staging-fixture' | sudo tee /opt/webdesktop/unknown-data >/dev/null")
    rejected=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert rejected.returncode!=0
    assert remote('sudo cat /opt/webdesktop/unknown-data',capture_output=True,text=True).stdout=='preserve-staging-fixture'
    remote('sudo rm /opt/webdesktop/unknown-data && sudo rmdir /opt/webdesktop')
    remote(install,timeout=1500)
    repeated=subprocess.run(ssh+[install],capture_output=True,text=True,timeout=30)
    assert repeated.returncode!=0
    remote('sudo install -d -m 700 /root/staging-results')
    check='sudo bash /opt/webdesktop/source/webdesktop-deploy/check-staging.sh --live --allow-session-replacement --json /root/staging-results/'
    try:
        remote(check+'before.json',timeout=200)
        # Genuine administrator SSH and a genuine reboot, not an inherited FD test.
        remote('true')
        subprocess.run(ssh+['sudo systemctl reboot'],check=False)
        after=ready(boot); assert after!=boot
        remote(check+'after.json',timeout=200)
        (out/'reboot.json').write_text(json.dumps(dict(before=boot,after=after,administrator_ssh=True,unknown_data_refused=True,repeat_refused=True),indent=2))
    finally:
        result=subprocess.run(ssh+['sudo tar -cf - -C /root staging-results'],capture_output=True,timeout=30)
        if result.returncode==0:(out/'health.tar').write_bytes(result.stdout)
        # No .env, login.json, private SSH keys, DB, or source bundle in CI evidence.
        for name,args2 in [('systemd-security.txt','sudo systemd-analyze security --no-pager webdesktop-staging.service webdesktop-browser@1.service webdesktop-gui@1.service'),
                           ('units.txt','sudo systemctl --no-pager status webdesktop-staging.service webdesktop-guest.socket webdesktop-browser-network.service')]:
            r=subprocess.run(ssh+[args2],capture_output=True,text=True,timeout=30);(out/name).write_text(r.stdout+r.stderr)
