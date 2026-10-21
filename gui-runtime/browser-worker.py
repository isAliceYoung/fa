"""Fixed visible Chromium, no URL/command/environment supplied by the web client."""
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
import time

assert sys.argv[1:]==['1']
gui,data,svc=map(pwd.getpwnam,('wdgui','wdguest','wdsvc'))
assert os.getresuid()==(gui.pw_uid,)*3 and os.getresgid()==(data.pw_gid,)*3
status=Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert not any(int(l.split()[1],16) for l in status.splitlines() if l.startswith(('CapEff:','CapPrm:','CapBnd:','CapAmb:')))
os.environ.clear();os.umask(0o007)
listener=socket.socket(fileno=3)
assert listener.getsockname()=='/run/webdesktop-browser/session-1.sock'
listener.settimeout(8)
with listener:conn=listener.accept()[0]
with conn:
 assert struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]==svc.pw_uid
 conn.settimeout(5);message=bytearray()
 while b'\n' not in message:
  b=conn.recv(256)
  if not b or len(message)+len(b)>256:raise ValueError('Open budget')
  message.extend(b)
 assert json.loads(message)=={'t':'open'}
 started=time.monotonic()
 def stage(name):conn.sendall((json.dumps({'t':'stage','stage':name,'ms':round((time.monotonic()-started)*1000)},separators=(',',':'))+'\n').encode())
 env={'HOME':'/home/wdguest','USER':'wdgui','LOGNAME':'wdgui','PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
      'DISPLAY':':100','XAUTHORITY':'/tmp/wd-gui/Xauthority','DBUS_SESSION_BUS_ADDRESS':'unix:path=/tmp/wd-gui/bus',
      'XDG_RUNTIME_DIR':'/tmp/wd-gui','XDG_CONFIG_HOME':'/home/wdguest/.gui/config','XDG_CACHE_HOME':'/home/wdguest/.gui/cache',
      'XDG_DATA_HOME':'/home/wdguest/.gui/data','GTK_IM_MODULE':'fcitx','QT_IM_MODULE':'fcitx','XMODIFIERS':'@im=fcitx','NO_AT_BRIDGE':'1'}
 stage('browser_worker')
 child=subprocess.Popen(['/usr/bin/chromium','--no-first-run','--no-default-browser-check','--disable-gpu',
   '--disable-background-networking','--disable-component-update','--disk-cache-size=16777216',
   '--user-data-dir=/home/wdguest/.gui/chromium','--start-maximized','chrome://newtab/'],env=env,stdin=subprocess.DEVNULL,close_fds=True)
 stage('browser_spawned')
 # An alive launcher/renderer is not yet a visible application. Do not expose
 # an apparently ready canvas until its real private X11 window is mapped.
 visible=subprocess.Popen(['/usr/bin/xdotool','search','--sync','--onlyvisible','--class','chromium'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 try:
  probe_exit=visible.wait(timeout=4)
  if probe_exit!=0 or child.poll() is not None:
   # Fixed probe only; bounded diagnostics go to the protected unit journal,
   # never to the visitor IPC or to an environment/command dump.
   detail=visible.stderr.read(1024).decode('utf-8','replace') if probe_exit else ''
   print(json.dumps({'event':'browser_window_failure','probe_exit':probe_exit,'browser_exit':child.poll(),'detail':detail}),file=sys.stderr,flush=True)
   raise RuntimeError('Chromium startup failed')
 except subprocess.TimeoutExpired:
  visible.kill();visible.wait();print('{"event":"browser_window_failure","reason":"probe_timeout"}',file=sys.stderr,flush=True);raise TimeoutError('Visible Chromium startup timeout')
 finally:visible.stderr.close()
 stage('browser_visible')
 conn.sendall(b'{"t":"ready"}\n');conn.settimeout(.25)
 while child.poll() is None:
  try:
   if conn.recv(1):raise ValueError('No browser command protocol')
   break
  except socket.timeout:pass
# Main exit/GUI dependency loss causes PID 1 to kill the entire application tree.
