"""Fixed unprivileged probe, not a desktop launch API. No backend dependencies."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

assert sys.argv[1:] in (['chromium'],['claude']) and os.getuid()!=0
app=sys.argv[1];os.environ.clear();os.umask(0o007)
run=Path('/tmp/wdgui-probe');run.mkdir(mode=0o700)
profile=Path('/home/wdguest/.gui')/('probe-'+app);profile.mkdir(mode=0o700,exist_ok=True)
env={'HOME':'/home/wdguest','USER':'wdgui','LOGNAME':'wdgui','PATH':'/usr/bin:/bin','LANG':'C.UTF-8','NO_AT_BRIDGE':'1',
 'DISPLAY':':101','XAUTHORITY':str(run/'Xauthority'),'XDG_RUNTIME_DIR':str(run),
 'XDG_CONFIG_HOME':str(profile/'config'),'XDG_CACHE_HOME':str(profile/'cache'),'XDG_DATA_HOME':str(profile/'data'),
 'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(run/'bus')}
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
fcitx=subprocess.Popen(['/usr/bin/fcitx5','-D','--replace'],env=env,stdin=subprocess.DEVNULL)
end=time.monotonic()+5
while True:
 owner=subprocess.run(['/usr/bin/dbus-send','--session','--print-reply','--dest=org.freedesktop.DBus','/','org.freedesktop.DBus.NameHasOwner','string:org.fcitx.Fcitx5'],env=env,capture_output=True,text=True,timeout=3)
 if owner.returncode==0 and 'boolean true' in owner.stdout or time.monotonic()>end:break
 time.sleep(.1)
assert fcitx.poll() is None and owner.returncode==0 and 'boolean true' in owner.stdout
# Query only after name ownership; an early remote call can D-Bus-activate a
# competing daemon. State 0 is legitimate before a focused input context.
remote=subprocess.run(['/usr/bin/fcitx5-remote'],env=env,capture_output=True,text=True,timeout=3)
print('FCITX_PRIVATE_STATUS '+str(remote.returncode)+' '+remote.stdout.strip(),flush=True)
assert remote.returncode==0 and remote.stdout.strip() in ('0','1','2')
if app=='chromium':
 args=['/usr/bin/chromium','--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(profile/'browser'),'--remote-debugging-port=9222','chrome://sandbox']
else:
 executable=Path('/usr/local/lib/webdesktop-gui-probe/claude-path').read_text().strip()
 args=[executable,'--disable-gpu','--user-data-dir='+str(profile/'browser'),'--remote-debugging-port=9222']
child=subprocess.Popen(args,env=env,stdin=subprocess.DEVNULL)
print('BROWSER_PROBE_STARTED '+app,flush=True)
end=time.monotonic()+25
while time.monotonic()<end and child.poll() is None:time.sleep(.1)
print('BROWSER_PROBE_RESULT '+app+' '+str(child.poll()),flush=True)
# Unit exit causes PID 1 to clean up every browser/IME/X descendant.
