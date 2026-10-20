"""Fixed unprivileged probe, not a desktop launch API. No backend dependencies."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

assert len(sys.argv)==4 and sys.argv[1] in ('chromium','claude') and sys.argv[2] in ('320','384','448') and sys.argv[3]=='90' and os.getuid()!=0
app=sys.argv[1];os.environ.clear();os.umask(0o007)
run=Path('/tmp/wdgui-probe');run.mkdir(mode=0o700)
profile=Path('/home/wdguest/.gui')/('matrix-'+app+'-'+sys.argv[2]);profile.mkdir(mode=0o700,exist_ok=True)
env={'HOME':'/home/wdguest','USER':'wdgui','LOGNAME':'wdgui','PATH':'/usr/bin:/bin','LANG':'C.UTF-8','NO_AT_BRIDGE':'1',
 'DISPLAY':':101','XAUTHORITY':str(run/'Xauthority'),'XDG_RUNTIME_DIR':str(run),
 'XDG_CONFIG_HOME':str(profile/'config'),'XDG_CACHE_HOME':str(profile/'cache'),'XDG_DATA_HOME':str(profile/'data'),
 'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(run/'bus'),'GTK_IM_MODULE':'fcitx','QT_IM_MODULE':'fcitx','XMODIFIERS':'@im=fcitx'}
for path in ['/run/postgresql/.s.PGSQL.5432','/var/lib/webdesktop/.webdesktop-http/next.sock','/run/dbus/system_bus_socket']:
 try:s=socket.socket(socket.AF_UNIX);s.connect(path);raise AssertionError('Host socket accessible')
 except (PermissionError,FileNotFoundError):pass
subprocess.run(['/usr/bin/xauth','-f',env['XAUTHORITY'],'add',':101','.',secrets.token_hex(16)],env=env,check=True,timeout=5)
x=subprocess.Popen(['/usr/bin/Xtigervnc',':101','-geometry','800x600','-depth','24','-rfbport','-1','-rfbunixpath',str(run/'vnc.sock'),'-SecurityTypes','None','-nolisten','tcp','-auth',env['XAUTHORITY'],'-AllowOverride='],env=env,stdin=subprocess.DEVNULL)
end=time.monotonic()+5
while not Path('/tmp/.X11-unix/X101').exists():
 assert x.poll() is None and time.monotonic()<end;time.sleep(.05)
bus=subprocess.Popen(['/usr/bin/dbus-daemon','--session','--nofork','--address='+env['DBUS_SESSION_BUS_ADDRESS']],env=env,stdin=subprocess.DEVNULL)
time.sleep(.2)
wm=subprocess.Popen(['/usr/bin/openbox','--config-file','/usr/local/lib/webdesktop-gui/openbox.xml'],env=env,stdin=subprocess.DEVNULL)
fcitx=subprocess.Popen(['/usr/bin/fcitx5','-D','--replace'],env=env,stdin=subprocess.DEVNULL)
end=time.monotonic()+5
while True:
 owner=subprocess.run(['/usr/bin/dbus-send','--session','--print-reply','--dest=org.freedesktop.DBus','/','org.freedesktop.DBus.NameHasOwner','string:org.fcitx.Fcitx5'],env=env,capture_output=True,text=True,timeout=3)
 if owner.returncode==0 and 'boolean true' in owner.stdout or time.monotonic()>end:break
 time.sleep(.1)
fcitx_owned=fcitx.poll() is None and owner.returncode==0 and 'boolean true' in owner.stdout
# Query only after name ownership; an early remote call can D-Bus-activate a
# competing daemon. State 0 is legitimate before a focused input context.
remote=subprocess.run(['/usr/bin/fcitx5-remote'],env=env,capture_output=True,text=True,timeout=3) if fcitx_owned else None
if remote and remote.returncode==0 and remote.stdout.strip() in ('0','1','2'):
 print('FCITX_PRIVATE_STATUS '+str(remote.returncode)+' '+remote.stdout.strip(),flush=True)
else:
 print('FCITX_PRIVATE_FAILED: independent IME feasibility check failed',flush=True)
# An IME failure is reported by the controller but must not prevent independent
# Chromium/Electron sandbox observations in the same constrained test service.
if app=='chromium':
 args=['/usr/bin/chromium','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(profile/'browser'),'--disable-component-update','--disk-cache-size=16777216','--remote-debugging-port=9222','chrome://sandbox']
else:
 executable=Path('/usr/local/lib/webdesktop-gui-probe/claude-path').read_text().strip()
 # Stock Claude rejects debugger switches. Observe its real renderer via root
 # /proc instrumentation, without changing vendor code or enabling a debug API.
 args=[executable,'--disable-gpu','--user-data-dir='+str(profile/'browser')]
child=subprocess.Popen(args,env=env,stdin=subprocess.DEVNULL)
print('BROWSER_PROBE_STARTED '+app,flush=True)
end=time.monotonic()+90
while time.monotonic()<end and child.poll() is None:time.sleep(.1)
print('BROWSER_PROBE_RESULT '+app+' '+str(child.poll()),flush=True)
# Unit exit causes PID 1 to clean up every browser/IME/X descendant.
