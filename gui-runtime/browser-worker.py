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
 env={'HOME':'/home/wdguest','USER':'wdgui','LOGNAME':'wdgui','PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
      'DISPLAY':':100','XAUTHORITY':'/tmp/wd-gui/Xauthority','DBUS_SESSION_BUS_ADDRESS':'unix:path=/tmp/wd-gui/bus',
      'XDG_RUNTIME_DIR':'/tmp/wd-gui','XDG_CONFIG_HOME':'/home/wdguest/.gui/config','XDG_CACHE_HOME':'/home/wdguest/.gui/cache',
      'XDG_DATA_HOME':'/home/wdguest/.gui/data','GTK_IM_MODULE':'fcitx','QT_IM_MODULE':'fcitx','XMODIFIERS':'@im=fcitx','NO_AT_BRIDGE':'1'}
 child=subprocess.Popen(['/usr/bin/chromium','--no-first-run','--no-default-browser-check','--disable-gpu',
   '--disable-background-networking','--disable-component-update','--disk-cache-size=16777216',
   '--user-data-dir=/home/wdguest/.gui/chromium','--start-maximized','about:blank'],env=env,stdin=subprocess.DEVNULL,close_fds=True)
 # An alive launcher/renderer is not yet a visible application. Do not expose
 # an apparently ready canvas until its real private X11 window is mapped.
 deadline=time.monotonic()+4
 while True:
  if child.poll() is not None:raise RuntimeError('Chromium startup failed')
  visible=subprocess.run(['/usr/bin/xdotool','search','--onlyvisible','--class','chromium'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1)
  if visible.returncode==0:break
  if time.monotonic()>=deadline:raise TimeoutError('Visible Chromium startup timeout')
  time.sleep(.05)
 conn.sendall(b'{"t":"ready"}\n');conn.settimeout(.25)
 while child.poll() is None:
  try:
   if conn.recv(1):raise ValueError('No browser command protocol')
   break
  except socket.timeout:pass
# Main exit/GUI dependency loss causes PID 1 to kill the entire application tree.
