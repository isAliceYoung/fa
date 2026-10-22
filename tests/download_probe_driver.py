"""Dedicated disposable-VM baseline experiment; no production config changes."""
import json
import shutil
import subprocess
import shlex

def prepare(root,remote,server='download_probe_server.py'):
    remote('sudo apt-get install -y --no-install-recommends libnss3-tools x11-xserver-utils',timeout=180)
    with (root/'tests/staging_ui_probe.py').open('rb') as f:remote('sudo tee /root/staging-ui-probe.py >/dev/null',stdin=f)
    with (root/'tests'/server).open('rb') as f:remote('sudo tee /root/download-probe-server.py >/dev/null',stdin=f)
    remote("sudo mkdir -m 700 /root/d1-origin && sudo openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=D1-LAB -addext subjectAltName=IP:203.0.113.2 -addext basicConstraints=critical,CA:TRUE -keyout /root/d1-origin/key.pem -out /root/d1-origin/cert.pem",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    remote('sudo ip netns add d1-origin && sudo ip link add d1-host type veth peer name d1-peer && sudo ip link set d1-peer netns d1-origin && sudo ip addr add 203.0.113.1/30 dev d1-host && sudo ip link set d1-host up && sudo ip -n d1-origin addr add 203.0.113.2/30 dev d1-peer && sudo ip -n d1-origin link set d1-peer up && sudo ip -n d1-origin link set lo up')
    # Trust only this test CA using the GUI identity's ordinary NSS database.
    remote('sudo install -m 644 /root/d1-origin/cert.pem /var/tmp/d1-origin-cert.pem')
    # Earlier GUI regression legitimately creates NSS. Preserve it, rather
    # than resetting a profile or requiring that no browser ever ran before.
    code="""import os,stat,subprocess
p='/home/wdguest/.pki'
for d in (p,p+'/nssdb'):
 if not os.path.lexists(d):os.mkdir(d,0o700)
 s=os.lstat(d)
 if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o002:raise RuntimeError('Unsafe lab NSS directory')
db='sql:'+p+'/nssdb'
if not os.path.exists(p+'/nssdb/cert9.db'):subprocess.run(['certutil','-N','-d',db,'--empty-password'],check=True)
subprocess.run(['certutil','-A','-d',db,'-n','d1-test-origin','-t','C,,','-i','/var/tmp/d1-origin-cert.pem'],check=True)
"""
    remote('sudo runuser -u wdgui -g wdguest -- python3 -I -c '+shlex.quote(code))
    remote('sudo systemd-run --unit=d1-origin --property=KillMode=control-group /usr/bin/ip netns exec d1-origin /usr/bin/python3 -I /root/download-probe-server.py --disposable-vm')

def finish(out,remote):
    log=remote('sudo journalctl --no-pager -u d1-origin.service',capture_output=True,text=True).stdout;(out/'download-origin.log').write_text(log)
    remote('sudo systemctl stop d1-origin.service && sudo ip link delete d1-host && sudo ip netns delete d1-origin')

def run(root,out,ssh,remote,state):
    prepare(root,remote)
    chrome=shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:raise RuntimeError('Host Chrome required')
    password=remote("sudo python3 -c 'import json;print(json.load(open(\""+state+"/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip();print('::add-mask::'+password,flush=True)
    tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:subprocess.run(['node',str(root/'tests/download_probe_ui.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out))),text=True,check=True,timeout=150)
    finally:
        tunnel.terminate();tunnel.wait(timeout=10)
        finish(out,remote)
