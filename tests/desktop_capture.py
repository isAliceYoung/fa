"""Root-only disposable UI instrument; not an API or visitor runtime dependency."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
assert os.getuid()==0 and len(sys.argv)==2
topic=sys.argv[1];assert topic in ['before','external','browser-ime','profile','closed','reopened','revoked']
out=Path('/root/wd-systemd-evidence')
def cmd(args):return subprocess.check_output(args,text=True,timeout=10).strip()
result={'topic':topic,'meminfo':Path('/proc/meminfo').read_text(),'swap':Path('/proc/swaps').read_text(),'cgroups':{}}
for unit in ['wdlab-next.service','postgresql@15-main.service','webdesktop-guest.service','webdesktop-pty.slice','webdesktop-gui.slice','webdesktop-browser@1.service']:
 group=cmd(['systemctl','show',unit,'-p','ControlGroup','--value'])
 if group:
  result['cgroups'][unit]={f:(Path('/sys/fs/cgroup'+group)/f).read_text() for f in ['memory.current','memory.peak','memory.swap.current','memory.events','pids.current','pids.peak','cpu.stat'] if (Path('/sys/fs/cgroup'+group)/f).exists()}
result['gui_pids']=cmd(['pgrep','-u',str(pwd.getpwnam('wdgui').pw_uid)]) if subprocess.run(['pgrep','-u','wdgui'],stdout=subprocess.DEVNULL).returncode==0 else ''
if topic in ['external','browser-ime']:
 pid=cmd(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value']);g=pwd.getpwnam('wdgui');d=pwd.getpwnam('wdguest')
 text=cmd(['nsenter','--target',pid,'--mount','--net','--','setpriv','--reuid',str(g.pw_uid),'--regid',str(d.pw_gid),'--groups',str(g.pw_gid),'env','-i','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','/usr/bin/xclip','-selection','clipboard','-o'])
 result['clipboard']=text
if topic in ['profile','reopened']:
 p=Path('/home/wdguest/.gui/chromium/Default/Bookmarks');result['bookmark_exists']=p.is_file() and 'https://example.com' in p.read_text()
result['disk']=cmd(['df','-PB1','/','/home/wdguest'])
(out/('desktop-ui-'+topic+'.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result))
