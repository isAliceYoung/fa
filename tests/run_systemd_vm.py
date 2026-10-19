"""Create, boot, reboot and destroy a dedicated Debian 12 QEMU VM in CI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.test-results/systemd-vm'
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
    (OUT/'image.json').write_text(json.dumps(dict(url=base+image,sha512=digest,ram_mib=1024,disk_gib=10),indent=2))
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
    vm=subprocess.Popen(['qemu-system-x86_64',*acceleration,'-m','1024','-smp','2','-display','none','-serial','stdio','-nodefaults',
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
        with (work/'lab.tar').open('rb') as stream:
            remote('sudo mkdir -p /root/wd-lab && sudo tar -xf - -C /root/wd-lab',stdin=stream)
        remote('sudo bash /root/wd-lab/tests/debian_systemd_vm.sh prepare')
        # Even with a valid authorized key, guest/service cannot authenticate or
        # obtain forwarding-only SSH. Ordinary lab administrator still works.
        ssh_checks=[]
        def denied_identity_logins(stage):
            pub=(work/'admin.pub').read_text().strip()
            for name in ('wdguest','wdsvc'):
                home='/home/wdguest' if name=='wdguest' else '/var/lib/webdesktop'
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
        denied_identity_logins('reboot')
        (OUT/'reboot.json').write_text(json.dumps(dict(before=boot,after=reboot,administrator_ssh_before_and_after=True),indent=2))
    finally:
        try:
            r=subprocess.run(ssh+['sudo tar -cf - -C /root wd-systemd-evidence'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
            if r.returncode==0:(OUT/'evidence.tar').write_bytes(r.stdout)
            subprocess.run(ssh+['sudo journalctl --no-pager -u webdesktop-guest.socket -u webdesktop-guest.service'],stdout=(OUT/'journal.txt').open('w'),stderr=subprocess.STDOUT,timeout=10)
        except subprocess.TimeoutExpired:
            (OUT/'collection-timeout.txt').write_text('SSH diagnostics timed out; inspect serial.log\n')
        finally:
            vm.terminate()
            vm.wait(timeout=20)
            serial.close()
