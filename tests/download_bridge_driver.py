"""Real visible download acceptance using the shared disposable VM fixtures."""
import json
import shutil
import subprocess
from download_probe_driver import prepare, finish
from download_bridge_origin import NAMES, expected

def run(root,out,ssh,remote,state,backend,after_reboot=False):
    chrome=shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:raise RuntimeError('Host browser required')
    if not after_reboot:
        prepare(root,remote,'download_bridge_origin.py')
        with (root/'tests/download_bridge_vm.py').open('rb') as f:remote('sudo tee /root/download-bridge-vm.py >/dev/null',stdin=f)
        # A root-private TEST secret only. Never package it or read its bytes in
        # the download observer; all protected paths are checked to exist.
        remote("sudo sh -c 'umask 077; test ! -e /root/d1-private-key.test; printf D1-test-only > /root/d1-private-key.test'")
    password=remote("sudo python3 -c 'import json;print(json.load(open(\""+state+"/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip()
    print('::add-mask::'+password,flush=True)
    tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        subprocess.run(['node',str(root/'tests/download_bridge_ui.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out),backend=backend,after_reboot=after_reboot,names=NAMES,expected={k:expected(k) for k in NAMES})),text=True,check=True,timeout=720)
    finally:
        tunnel.terminate();tunnel.wait(timeout=10)
        if not after_reboot:finish(out,remote)
