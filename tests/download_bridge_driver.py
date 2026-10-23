"""Real visible download acceptance using the shared disposable VM fixtures."""
import json
import shutil
import subprocess
from download_probe_driver import prepare, finish
from download_bridge_origin import NAMES, expected

def run(root,out,ssh,remote,state,backend,after_reboot=False,only=None,diagnose=False):
    chrome=shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:raise RuntimeError('Host browser required')
    if not after_reboot:
        prepare(root,remote,'download_bridge_origin.py')
        with (root/'tests/download_bridge_vm.py').open('rb') as f:remote('sudo tee /root/download-bridge-vm.py >/dev/null',stdin=f)
        # A root-private TEST secret only. Never package it or read its bytes in
        # the download observer; all protected paths are checked to exist.
        remote("sudo sh -c 'umask 077; test ! -e /root/d1-private-key.test; printf D1-test-only > /root/d1-private-key.test'")
        monitor=1500 if diagnose else 720
        remote('sudo systemd-run --unit=d1-metrics --property=RuntimeMaxSec='+str(monitor+20)+' --property=MemoryMax=64M --property=CPUQuota=5% --property=TasksMax=8 /usr/bin/python3 -I /root/download-bridge-vm.py monitor '+str(monitor))
    password=remote("sudo python3 -c 'import json;print(json.load(open(\""+state+"/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip()
    print('::add-mask::'+password,flush=True)
    tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        subprocess.run(['node',str(root/'tests/download_bridge_ui.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out),backend=backend,after_reboot=after_reboot,names=NAMES,expected={k:expected(k) for k in NAMES},only=only,diagnose=diagnose)),text=True,check=True,timeout=1500 if diagnose else 720)
    finally:
        tunnel.terminate();tunnel.wait(timeout=10)
        if not after_reboot:
            remote('sudo systemctl stop d1-metrics.service')
            metrics=remote('sudo cat /root/d1-download-metrics.jsonl',capture_output=True,text=True).stdout
            (out/'download-metrics.jsonl').write_text(metrics)
            finish(out,remote)

def focused(root,out,ssh,remote,state,backend,check,ready,boot):
    """Targeted first gate. Full matrix below still runs every old assertion."""
    failures=[]
    try:run(root,out,ssh,remote,state,backend)
    except (subprocess.SubprocessError,RuntimeError) as error:failures.append(dict(stage='download',kind=type(error).__name__))
    remote(check+'download-smoke-before.json',timeout=200)
    remote('true')
    subprocess.run(ssh+['sudo systemctl reboot'],check=False)
    after=ready(boot)
    if after==boot:raise RuntimeError('Actual reboot required')
    remote(check+'download-smoke-after.json',timeout=200)
    try:run(root,out,ssh,remote,state,backend,after_reboot=True)
    except (subprocess.SubprocessError,RuntimeError) as error:failures.append(dict(stage='download-reboot',kind=type(error).__name__))
    (out/'download-focus-reboot.json').write_text(json.dumps(dict(before=boot,after=after,administrator_ssh=True),indent=2))
    (out/'download-suite-errors.json').write_text(json.dumps(failures,indent=2))
    if failures:raise RuntimeError('Focused download gate failed; full matrix must remain not executed')

R2_CASES={'normal':None,'reset':'network interruption never publishes completed bytes','cancel':'native user cancellation','crash':'browser crash preserves completed data and recovers','space':'actual volume space exhaustion and download recovery','inodes':'actual inode exhaustion and download recovery'}

def r2(root,out,ssh,remote,state,backend,case):
    """D1-R2 diagnostic gate: one fault per fresh 1 GiB VM, or the original sequence."""
    if case=='paint':
        # Native downloads-tab presentation probe only: no origin, no faults.
        prepare(root,remote,'download_bridge_origin.py')
        remote('sudo apt-get install -y --no-install-recommends x11-apps',timeout=180)
        with (root/'tests/download_bridge_vm.py').open('rb') as f:remote('sudo tee /root/download-bridge-vm.py >/dev/null',stdin=f)
        remote('sudo systemd-run --unit=d1-metrics --property=RuntimeMaxSec=1220 --property=MemoryMax=64M --property=CPUQuota=5% --property=TasksMax=8 /usr/bin/python3 -I /root/download-bridge-vm.py monitor 1200')
        chrome=shutil.which('google-chrome') or shutil.which('chromium')
        password=remote("sudo python3 -c 'import json;print(json.load(open(\""+state+"/login.json\"))[\"DESKTOP_PASSWORD\"])'",capture_output=True,text=True).stdout.strip()
        print('::add-mask::'+password,flush=True)
        tunnel=subprocess.Popen(ssh[:-1]+['-N','-L','127.0.0.1:18890:127.0.0.1:3000',ssh[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:subprocess.run(['node',str(root/'tests/d1_r2_paint_probe.mjs')],input=json.dumps(dict(url='http://127.0.0.1:18890',password=password,chrome=chrome,ssh=ssh[1:],out=str(out),config=[x for x in __import__('sys').argv if x.startswith('--')])),text=True,check=True,timeout=1100)
        finally:
            tunnel.terminate();tunnel.wait(timeout=10)
            remote('sudo systemctl stop d1-metrics.service')
            (out/'download-metrics.jsonl').write_text(remote('sudo cat /root/d1-download-metrics.jsonl',capture_output=True,text=True).stdout)
            finish(out,remote)
            r=subprocess.run(ssh+['sudo journalctl --no-pager -o short-precise -u "webdesktop-browser*" -u "webdesktop-gui*"'],capture_output=True,text=True,timeout=30)
            (out/'r2-journal.txt').write_text(r.stdout[-4000000:])
        return
    if case=='sequence':only=None
    elif case in R2_CASES:only=[R2_CASES[case]] if R2_CASES[case] else ['visible Chromium HTTPS native download and Agent byte identity']
    else:raise RuntimeError('Unknown D1-R2 case')
    remote('sudo apt-get install -y --no-install-recommends x11-apps',timeout=180)  # xwd: TEST observer only
    try:run(root,out,ssh,remote,state,backend,only=only,diagnose=True)
    finally:
        r=subprocess.run(ssh+['sudo journalctl --no-pager -o short-precise -u "webdesktop-browser*" -u "webdesktop-gui*" -u webdesktop-staging.service'],capture_output=True,text=True,timeout=30)
        (out/'r2-journal.txt').write_text(r.stdout[-4000000:])
