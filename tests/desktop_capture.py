"""Root-only disposable UI instrument; not an API or visitor runtime dependency."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
assert os.getuid()==0 and len(sys.argv)==2
topic=sys.argv[1];assert topic in ['before','ready','address','external','browser-ime','profile','closed','reopened','revoked']
out=Path('/root/wd-systemd-evidence')
def cmd(args):return subprocess.check_output(args,text=True,timeout=10).strip()
result={'topic':topic,'meminfo':Path('/proc/meminfo').read_text(),'swap':Path('/proc/swaps').read_text(),'cgroups':{}}
for unit in ['webdesktop.slice','wdlab-next.service','postgresql@15-main.service','webdesktop-guest.service','webdesktop-pty.slice','webdesktop-gui.slice','webdesktop-browser@1.service']:
 group=cmd(['systemctl','show',unit,'-p','ControlGroup','--value'])
 if group:
  result['cgroups'][unit]={f:(Path('/sys/fs/cgroup'+group)/f).read_text() for f in ['memory.current','memory.peak','memory.swap.current','memory.events','pids.current','pids.peak','cpu.stat'] if (Path('/sys/fs/cgroup'+group)/f).exists()}
  result['cgroups'][unit]['limits']=cmd(['systemctl','show',unit,'-p','MemoryMax','-p','MemoryHigh','-p','MemorySwapMax','-p','TasksMax','-p','CPUQuotaPerSecUSec'])
result['gui_pids']=cmd(['pgrep','-u',str(pwd.getpwnam('wdgui').pw_uid)]) if subprocess.run(['pgrep','-u','wdgui'],stdout=subprocess.DEVNULL).returncode==0 else ''
for user,field in [('wdgui','gui_processes'),('wdterm','pty_processes')]:
 result[field]={}
 for entry in Path('/proc').iterdir():
  if not entry.name.isdigit():continue
  try:
   status=(entry/'status').read_text()
   actual_uid=int(next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:')))
   if actual_uid==pwd.getpwnam(user).pw_uid:
    # Kernel start time disambiguates a newly authorized process reusing a PID.
    result[field][entry.name]=(entry/'stat').read_text().rsplit(')',1)[1].split()[19]
  except (FileNotFoundError,ProcessLookupError):pass
if topic in ['ready','address','external','browser-ime']:
 pid=cmd(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value']);g=pwd.getpwnam('wdgui');d=pwd.getpwnam('wdguest')
 prefix=['nsenter','--target',pid,'--mount','--net','--','setpriv','--reuid',str(g.pw_uid),'--regid',str(d.pw_gid),'--groups',str(g.pw_gid),'env','-i','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority']
 if topic=='ready':result['window_visible']=bool(cmd(prefix+['xdotool','search','--sync','--onlyvisible','--class','chromium']))
 else:result['clipboard']=cmd(prefix+['/usr/bin/xclip','-selection','clipboard','-o'])
if topic in ['profile','reopened']:
 p=Path('/home/wdguest/.gui/chromium/Default/Bookmarks');result['bookmark_exists']=p.is_file() and 'https://example.com' in p.read_text()
result['disk']=cmd(['df','-PB1','/','/home/wdguest'])
(out/('desktop-ui-'+topic+'.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result))
