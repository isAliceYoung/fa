"""Disposable CI root observer, never installed in a visitor runtime package."""
import json
import hashlib
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
assert os.getuid()==0 and sys.argv[1:] and sys.argv[1] in ('state','clipboard','policy','address','idle','ime')
def cmd(args):
    return subprocess.check_output(args,text=True,timeout=8,stderr=subprocess.DEVNULL).strip()
if sys.argv[1]=='idle':
    units={}
    for unit in ('webdesktop-gui@1.service','webdesktop-browser@1.service','webdesktop-gui-lease@1.service'):
        state=cmd(['systemctl','show',unit,'-p','MainPID','-p','ActiveState'])
        units[unit]=dict(line.split('=',1) for line in state.splitlines())
    print(json.dumps({'idle':all(s['MainPID']=='0' and s['ActiveState']=='inactive' for s in units.values()),'units':units}));sys.exit(0)
pid=cmd(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value'])
assert pid!='0'
g,d=pwd.getpwnam('wdgui'),pwd.getpwnam('wdguest')
prefix=['nsenter','--target',pid,'--mount','--net','--','setpriv','--reuid',str(g.pw_uid),'--regid',str(d.pw_gid),'--groups',str(g.pw_gid),'--no-new-privs','env','-i',
        'PATH=/usr/bin:/bin','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority']
if sys.argv[1]=='ime':
    state=cmd(prefix+['DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/wd-gui/bus','/usr/bin/fcitx5-remote'])
    assert state in ('0','1','2')
    print(json.dumps({'state':int(state)}));sys.exit(0)
if sys.argv[1] in ('clipboard','policy','address'):
    selection=subprocess.run(prefix+['xclip','-selection','clipboard','-o'],text=True,capture_output=True,timeout=8)
    if selection.returncode:
        if selection.returncode!=1:raise RuntimeError('Clipboard observer unavailable')
        print(json.dumps({'ready':False}));sys.exit(0)
    text=selection.stdout
    if sys.argv[1]=='clipboard':
        print(json.dumps({'ime_correct':text=='你好hello'}))
    elif sys.argv[1]=='address':
        print(json.dumps({'sha256':hashlib.sha256(text.encode()).hexdigest()}))
    else:
        print(json.dumps({'google_policy_visible':'DefaultSearchProviderName' in text and 'Google' in text and 'google.com' in text,'selection_bytes':len(text.encode())}))
else:
    result={'pid':int(pid),'windows':[]}
    screen=cmd(prefix+['xrandr','--current'])
    size=re.search(r'current (\d+) x (\d+)',screen)
    assert size
    result['framebuffer']=[int(x) for x in size.groups()]
    pointer=cmd(prefix+['xdotool','getmouselocation','--shell'])
    result['pointer']={k:int(v) for k,v in re.findall(r'^(X|Y)=(\d+)$',pointer,re.M)}
    for kind in ('[Pp]cmanfm','chromium'):
        found=subprocess.run(prefix+['xdotool','search','--onlyvisible','--class',kind],text=True,capture_output=True,timeout=5)
        for window in found.stdout.splitlines()[:8]:
            info=cmd(prefix+['xwininfo','-id',window])
            w=re.search(r'^\s*Width: (\d+)',info,re.M);h=re.search(r'^\s*Height: (\d+)',info,re.M)
            if not w or not h:continue
            state=cmd(prefix+['xprop','-id',window,'_NET_WM_STATE'])
            title=cmd(prefix+['xdotool','getwindowname',window])
            result['windows'].append({'app':kind,'width':int(w[1]),'height':int(h[1]),'maximized':'MAXIMIZED_VERT' in state and 'MAXIMIZED_HORZ' in state,
                                      'native_new_tab':bool(re.search('New Tab|新标签页',title,re.I)), 'ime_fixture': 'Staging IME' in title,
                                      'ime_focused':title.startswith('Staging IME Focused'), 'ime_committed':title.startswith('Staging IME Chinese'),
                                      'documents_open':'Documents' in title, 'downloads_open':'Downloads' in title})
    print(json.dumps(result))
