"""Dedicated disposable-VM baseline experiment; no production config changes."""
import json
import shutil
import subprocess

def run(root,out,ssh,remote,state):
    remote('sudo apt-get install -y --no-install-recommends libnss3-tools',timeout=180)
    with (root/'tests/download_probe_server.py').open('rb') as f:remote('sudo tee /root/download-probe-server.py >/dev/null',stdin=f)
    remote("sudo mkdir -m 700 /root/d1-origin && sudo openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=D1-LAB -addext subjectAltName=IP:203.0.113.2 -addext basicConstraints=critical,CA:TRUE -keyout /root/d1-origin/key.pem -out /root/d1-origin/cert.pem",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    remote('sudo ip netns add d1-origin && sudo ip link add d1-host type veth peer name d1-peer && sudo ip link set d1-peer netns d1-origin && sudo ip addr add 203.0.113.1/30 dev d1-host && sudo ip link set d1-host up && sudo ip -n d1-origin addr add 203.0.113.2/30 dev d1-peer && sudo ip -n d1-origin link set d1-peer up && sudo ip -n d1-origin link set lo up')
    # Trust only this test CA using the GUI identity's ordinary NSS database.
    remote('test ! -e /home/wdguest/.pki && sudo install -m 644 /root/d1-origin/cert.pem /var/tmp/d1-origin-cert.pem && sudo runuser -u wdgui -g wdguest -- mkdir -m 700 /home/wdguest/.pki && sudo runuser -u wdgui -g wdguest -- mkdir -m 700 /home/wdguest/.pki/nssdb && sudo runuser -u wdgui -g wdguest -- certutil -N -d sql:/home/wdguest/.pki/nssdb --empty-password && sudo runuser -u wdgui -g wdguest -- certutil -A -d sql:/home/wdguest/.pki/nssdb -n d1-test-origin -t C,, -i /var/tmp/d1-origin-cert.pem')
    remote('sudo systemd-run --unit=d1-origin --property=KillMode=control-group /usr/bin/ip netns exec d1-origin /usr/bin/python3 -I /root/download-probe-server.py --disposable-vm')
    chrome=shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:raise RuntimeError('Host Chrome required')
    password=remote("sudo python3 -c 'import json;print(json.load(open(\""+state+"/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip();print('::add-mask::'+password,flush=True)
    tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:subprocess.run(['node',str(root/'tests/download_probe_ui.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out))),text=True,check=True,timeout=150)
    finally:
        tunnel.terminate();tunnel.wait(timeout=10)
        log=remote('sudo journalctl --no-pager -u d1-origin.service',capture_output=True,text=True).stdout;(out/'download-origin.log').write_text(log)
        remote('sudo systemctl stop d1-origin.service && sudo ip link delete d1-host && sudo ip netns delete d1-origin')
