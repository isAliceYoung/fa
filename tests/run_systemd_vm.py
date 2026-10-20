"""Create, boot, reboot and destroy a dedicated Debian 12 QEMU VM in CI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
DESKTOP='--desktop' in sys.argv
GUI='--gui' in sys.argv or DESKTOP
SWAP=int(next((x.split('=',1)[1] for x in sys.argv if x.startswith('--swap-mib=')),'0'))
RAM=int(next((x.split('=',1)[1] for x in sys.argv if x.startswith('--ram-mib=')),'1024'))
assert SWAP in (0,1024) and RAM in (1024,2048)
PTY='--pty' in sys.argv or GUI
OUT=ROOT/(('.test-results/desktop-vm-'+str(RAM)+'-'+str(SWAP)) if DESKTOP else '.test-results/gui-vm' if GUI else '.test-results/pty-vm' if PTY else '.test-results/systemd-vm')
OUT.mkdir(parents=True,exist_ok=True)
def call(args, **kwargs):
    kwargs.setdefault('timeout',300)
    return subprocess.run(args,check=True,**kwargs)

with tempfile.TemporaryDirectory(prefix='wd-disposable-vm-') as temp:
    work=Path(temp)
    base='https://cloudfront.debian.net/cdimage/cloud/bookworm/latest/'
    image='debian-12-genericcloud-amd64.qcow2'
    def download(url, destination):
        print('Downloading '+url,flush=True)
        call(['curl','-fL','--retry','1','--connect-timeout','15','--max-time','120','-o',str(destination),url])
    download(base+'SHA512SUMS',work/'SHA512SUMS')
    sums=(work/'SHA512SUMS').read_text()
    digest=next(line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*')==image)
    download(base+image,work/'base.qcow2')
    with (work/'base.qcow2').open('rb') as stream:
        h=hashlib.sha512()
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    assert h.hexdigest()==digest,'Debian image checksum mismatch'
    (OUT/'image.json').write_text(json.dumps(dict(url=base+image,sha512=digest,ram_mib=RAM,swap_mib=SWAP,disk_gib=10,vcpus=1 if GUI else 2),indent=2))
    for name in ('admin','host'):
        call(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(work/name)])
    # Test-only administrator and host identities, confined to this VM.
    config={'users':[{'name':'wdlabadmin','groups':['sudo'],'sudo':'ALL=(ALL) NOPASSWD:ALL','shell':'/bin/bash','lock_passwd':True,'ssh_authorized_keys':[(work/'admin.pub').read_text().strip()]}],
        'ssh_pwauth':False,'disable_root':True,
        'ssh_keys':{'ed25519_private':(work/'host').read_text(),'ed25519_public':(work/'host.pub').read_text()},
        'packages':['python3','sudo'],'runcmd':[['touch','/var/tmp/wd-lab-ready']]}
    (work/'user-data').write_text('#cloud-config\n'+json.dumps(config))
    (work/'meta-data').write_text('instance-id: wd-throwaway-3aplus\nlocal-hostname: wd-lab\n')
    (work/'known_hosts').write_text('[127.0.0.1]:22222 '+(work/'host.pub').read_text())
    call(['genisoimage','-quiet','-output',str(work/'seed.iso'),'-volid','cidata','-joliet','-rock',str(work/'user-data'),str(work/'meta-data')])
    call(['qemu-img','create','-f','qcow2','-F','qcow2','-b',str(work/'base.qcow2'),str(work/'disk.qcow2'),'10G'])
    acceleration=['-accel','kvm','-cpu','host'] if os.access('/dev/kvm',os.R_OK|os.W_OK) else ['-accel','tcg','-cpu','max']
    serial=(OUT/'serial.log').open('wb')
    vm=subprocess.Popen(['qemu-system-x86_64',*acceleration,'-m',str(RAM),'-smp','1' if GUI else '2','-display','none','-serial','stdio','-nodefaults',
        '-drive','file='+str(work/'disk.qcow2')+',if=virtio','-drive','file='+str(work/'seed.iso')+',media=cdrom,readonly=on',
        '-netdev','user,id=n0,hostfwd=tcp:127.0.0.1:22222-:22','-device','virtio-net-pci,netdev=n0'],stdout=serial,stderr=subprocess.STDOUT)
    ssh=['ssh','-i',str(work/'admin'),'-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(work/'known_hosts'),'-o','ConnectTimeout=3','-o','BatchMode=yes','-p','22222','wdlabadmin@127.0.0.1']
    def remote(command,**kwargs):return call(ssh+[command],**kwargs)
    def ready(old=None):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if vm.poll() is not None:raise RuntimeError('VM exited; inspect serial log')
            try:
                r=subprocess.run(ssh+['test -f /var/tmp/wd-lab-ready && cat /proc/sys/kernel/random/boot_id'],capture_output=True,text=True,timeout=8)
            except subprocess.TimeoutExpired:
                (OUT/'ssh-readiness.txt').write_text('SSH authentication/command timed out\n')
                continue
            (OUT/'ssh-readiness.txt').write_text(r.stderr+'\nexit='+str(r.returncode)+'\n')
            if r.returncode==0 and (old is None or r.stdout.strip()!=old):return r.stdout.strip()
            time.sleep(2)
        raise RuntimeError('Booted Debian SSH readiness timeout')
    try:
        boot=ready()
        with tarfile.open(work/'lab.tar','w') as archive:
            for name in ('guest-runtime','webdesktop-deploy/scripts/setup-guest-layer.sh','webdesktop-deploy/scripts/setup-guest-volume.sh','webdesktop-deploy/scripts/setup-guest-ssh-deny.sh','webdesktop-deploy/scripts/check-guest-ssh.sh','webdesktop-deploy/templates','tests/debian_systemd_vm.sh','tests/linux_systemd_runtime.py'):
                archive.add(ROOT/name,arcname=name,filter=lambda info: None if '__pycache__' in info.name else info)
            if PTY:
                for name in ('pty-runtime','webdesktop-deploy/scripts/setup-pty-layer.sh','webdesktop-deploy/scripts/bootstrap.cjs','tests/debian_pty_vm.sh','tests/linux_pty_runtime.mjs'):
                    archive.add(ROOT/name,arcname=name,filter=lambda info: None if '__pycache__' in info.name else info)
            if GUI:
                for name in ('gui-runtime','webdesktop-deploy/scripts/setup-gui-layer.sh','tests/debian_gui_vm.sh','tests/linux_gui_runtime.mjs','tests/prepare_gui_probes.sh','tests/gui_probe_network.nft','tests/gui_probe_worker.py','tests/linux_gui_probes.mjs','tests/cdp_gui_probe.mjs'):
                    archive.add(ROOT/name,arcname=name,filter=lambda info: None if '__pycache__' in info.name else info)
            if DESKTOP:
                for name in ('webdesktop-deploy/scripts/setup-browser-layer.sh','tests/prepare_desktop.sh','tests/desktop_lab.mjs','tests/linux_desktop_runtime.mjs','tests/desktop_capture.py','tests/ime_fixture.py','tests/namespace_dac_probe.py','tests/gui_resource_worker.py','tests/linux_gui_matrix.mjs'):
                    archive.add(ROOT/name,arcname=name)
        with (work/'lab.tar').open('rb') as stream:
            remote('sudo mkdir -p /root/wd-lab && sudo tar -xf - -C /root/wd-lab',stdin=stream)
        remote('sudo bash /root/wd-lab/tests/debian_systemd_vm.sh prepare')
        if SWAP:
            remote("sudo bash -eu -c 'test ! -e /var/lib/wd-lab.swap; test $(df --output=avail -B1 /var/lib | tail -n1) -gt 3221225472; install -m 600 /dev/null /var/lib/wd-lab.swap; fallocate -l 1024M /var/lib/wd-lab.swap; mkswap /var/lib/wd-lab.swap; swapon /var/lib/wd-lab.swap; printf \"/var/lib/wd-lab.swap none swap sw 0 0\\n\" >> /etc/fstab; cat /proc/swaps'")
        if PTY:
            with (ROOT/'.test-results/pty-backend.tar.gz').open('rb') as stream:
                remote('sudo tar -xzf - -C /opt/webdesktop',stdin=stream)
            remote('sudo bash /root/wd-lab/tests/debian_pty_vm.sh')
            remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_pty_runtime.mjs prepare')
            if GUI:
                remote('sudo bash /root/wd-lab/tests/debian_gui_vm.sh')
                remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_gui_runtime.mjs prepare')
            # Real browser stays on the CI host. The VM exposes only loopback,
            # reached over the same administrator SSH local-forward mechanism.
            tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                secret=remote('sudo cat /opt/webdesktop/.env',capture_output=True,text=True).stdout
                config=dict(line.split('=',1) for line in secret.splitlines() if '=' in line)
                password=config['DESKTOP_PASSWORD']
                print('::add-mask::'+password,flush=True)
                chrome=shutil.which('google-chrome') or shutil.which('chromium')
                if not chrome:raise RuntimeError('Real sandboxed Chromium required; UI tests not run')
                call(['node',str(ROOT/('tests/gui_ui_runtime.mjs' if GUI else 'tests/pty_ui_runtime.mjs'))],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,out=str(OUT))),text=True,timeout=150)
                if GUI:
                    folder=json.loads((OUT/'gui-ui.json').read_text())['created_folder']
                    assert folder.startswith('gui-ui-') and len(folder)<64 and all(c.isascii() and (c.isalnum() or c=='-') for c in folder)
                    evidence=remote('sudo stat -c "%U:%G:%a" /home/wdguest/Documents/'+folder,capture_output=True,text=True).stdout
                    assert evidence.startswith('wdgui:wdguest:'),evidence
                    (OUT/'gui-ui-folder-stat.txt').write_text(evidence)
            finally:
                tunnel.terminate();tunnel.wait(timeout=10)
        # Even with a valid authorized key, guest/service cannot authenticate or
        # obtain forwarding-only SSH. Ordinary lab administrator still works.
        ssh_checks=[]
        def denied_identity_logins(stage):
            pub=(work/'admin.pub').read_text().strip()
            for name in (('wdguest','wdsvc','wdterm','wdgui') if GUI else ('wdguest','wdsvc','wdterm') if PTY else ('wdguest','wdsvc')):
                home={'wdguest':'/home/wdguest','wdsvc':'/var/lib/webdesktop','wdterm':'/var/lib/webdesktop-terminal','wdgui':'/var/lib/webdesktop-gui'}[name]
                remote('sudo install -d -m 700 -o '+name+' -g '+name+' '+home+'/.ssh')
                remote("printf '%s\\n' '"+pub+"' | sudo tee "+home+'/.ssh/authorized_keys >/dev/null')
                remote('sudo chown '+name+':'+name+' '+home+'/.ssh/authorized_keys && sudo chmod 600 '+home+'/.ssh/authorized_keys')
                login=ssh[:-1]+['-N','-L','127.0.0.1:22223:127.0.0.1:22',name+'@127.0.0.1']
                r=subprocess.run(login,capture_output=True,text=True,timeout=8)
                assert r.returncode==255 and 'Permission denied' in r.stderr,(name,r.returncode)
                ssh_checks.append({'stage':stage,'user':name,'exit':r.returncode,'denied':True})
                (OUT/'ssh-denials.json').write_text(json.dumps(ssh_checks,indent=2))
                (OUT/(name+'-ssh-denied.txt')).write_text(r.stderr+'\nexit='+str(r.returncode)+'\n')
        denied_identity_logins('prepare')
        subprocess.run(ssh+['sudo systemctl reboot'],check=False)
        reboot=ready(boot)
        assert boot!=reboot
        remote('sudo bash /root/wd-lab/tests/debian_systemd_vm.sh reboot')
        if GUI:remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_gui_runtime.mjs reboot')
        elif PTY:remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_pty_runtime.mjs reboot')
        denied_identity_logins('reboot')
        (OUT/'reboot.json').write_text(json.dumps(dict(before=boot,after=reboot,administrator_ssh_before_and_after=True),indent=2))
        # Finish mandatory basic-GUI reboot/SSH acceptance before optional-app
        # feasibility experiments, retaining those results even on probe failure.
        if GUI:
            if DESKTOP:
                outcomes={name:'not_executed' for name in ('desktop-runtime','desktop-ui','resource-matrix')}
                remote('sudo bash /root/wd-lab/tests/prepare_desktop.sh',timeout=600)
                remote('sudo bash /root/wd-lab/tests/prepare_gui_probes.sh',timeout=600)
                remote('sudo install -m 644 /root/wd-lab/tests/gui_resource_worker.py /usr/local/lib/webdesktop-gui-probe/matrix.py')
                try:
                    remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_desktop_runtime.mjs',timeout=720)
                    outcomes['desktop-runtime']='passed'
                except subprocess.CalledProcessError:outcomes['desktop-runtime']='failed'
                remote('sudo systemctl stop webdesktop-gui@1.service webdesktop-browser@1.service')
                tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    call(['node',str(ROOT/'tests/desktop_ui_runtime.mjs')],input=json.dumps(dict(password=password,chrome=chrome,out=str(OUT),ssh=ssh[1:])),text=True,timeout=240)
                    outcomes['desktop-ui']='passed'
                except subprocess.CalledProcessError:outcomes['desktop-ui']='failed'
                finally:tunnel.terminate();tunnel.wait(timeout=10)
                remote('sudo systemctl stop webdesktop-gui@1.service webdesktop-browser@1.service')
                try:
                    remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_gui_matrix.mjs',timeout=650)
                    outcomes['resource-matrix']='collected'
                except subprocess.CalledProcessError:outcomes['resource-matrix']='failed'
                (OUT/'phase-outcomes.json').write_text(json.dumps(outcomes,indent=2))
                assert 'failed' not in outcomes.values(),outcomes
            else:
                remote('sudo bash /root/wd-lab/tests/prepare_gui_probes.sh',timeout=600)
                remote('sudo /usr/local/bin/node /root/wd-lab/tests/linux_gui_probes.mjs',timeout=180)
    finally:
        try:
            r=subprocess.run(ssh+['sudo tar -cf - -C /root wd-systemd-evidence'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
            if r.returncode==0:(OUT/'evidence.tar').write_bytes(r.stdout)
            subprocess.run(ssh+['sudo journalctl --no-pager -u webdesktop-guest.socket -u webdesktop-guest.service -u wdlab-next.service -u "webdesktop-pty@*.service" -u "webdesktop-pty@*.socket" -u "webdesktop-pty-lease@*.service" -u "webdesktop-pty-lease@*.socket" -u "webdesktop-gui*" -u polkit.service'],stdout=(OUT/'journal.txt').open('w'),stderr=subprocess.STDOUT,timeout=10)
        except subprocess.TimeoutExpired:
            (OUT/'collection-timeout.txt').write_text('SSH diagnostics timed out; inspect serial.log\n')
        finally:
            vm.terminate()
            vm.wait(timeout=20)
            serial.close()
