#!/usr/bin/env python3
"""Untrusted GUI data plane. Fixed Xvnc/Openbox/PCManFM; no credentials."""
import base64
import json
import os
from pathlib import Path
import pwd
import secrets
import selectors
import socket
import struct
import subprocess
import sys
import time

HOME = '/home/wdguest'
RUN = '/tmp/wd-gui'
FRAME = 65536

def packet(value):
    return json.dumps(value, separators=(',', ':')).encode() + b'\n'

def main():
    gui, data, service = map(pwd.getpwnam, ('wdgui', 'wdguest', 'wdsvc'))
    if sys.argv[1:] != ['1'] or os.getresuid() != (gui.pw_uid,)*3 or os.getresgid() != (data.pw_gid,)*3:
        raise RuntimeError('Fixed GUI identity/slot required')
    status = Path('/proc/self/status').read_text()
    if 'NoNewPrivs:\t1' not in status or any(int(line.split()[1],16) for line in status.splitlines() if line.startswith(('CapEff:','CapPrm:','CapAmb:','CapBnd:'))):
        raise RuntimeError('Privileges forbidden')
    if set(os.getgroups()) - {gui.pw_gid,data.pw_gid}: raise RuntimeError('Unexpected groups')
    for family in (socket.AF_INET,socket.AF_INET6):
        try: probe=socket.socket(family)
        except OSError: continue
        probe.close();raise RuntimeError('Basic GUI must not allocate Internet sockets')
    os.environ.clear(); os.umask(0o007)
    listener = socket.socket(fileno=3)
    if listener.family != socket.AF_UNIX or listener.getsockname() != '/run/webdesktop-gui/session-1.sock': raise RuntimeError('Protected GUI socket required')
    listener.settimeout(10)
    with listener: connection = listener.accept()[0]
    with connection:
        _,uid,_ = struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if uid != service.pw_uid: raise RuntimeError('Service peer required')
        connection.settimeout(8)
        initial = bytearray()
        while b'\n' not in initial:
            chunk = connection.recv(4096)
            if not chunk or len(initial)+len(chunk)>4096: raise ValueError('Open budget')
            initial.extend(chunk)
        line,extra = bytes(initial).split(b'\n',1)
        m=json.loads(line)
        if extra or not isinstance(m,dict) or set(m)!={'t','app','w','h'} or m['t']!='open' or m['app'] not in ('desktop','pcmanfm'):
            raise ValueError('Fixed GUI application required')
        w,h=m['w'],m['h']
        if type(w) is not int or type(h) is not int or not 640<=w<=1280 or not 480<=h<=800: raise ValueError('Geometry budget')
        Path(RUN).mkdir(mode=0o700)
        profile=Path(HOME+'/.gui')
        for name in ('config','cache','data'): (profile/name).mkdir(mode=0o700,exist_ok=True)
        env={'HOME':HOME,'USER':'wdgui','LOGNAME':'wdgui','PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
             'DISPLAY':':100','XAUTHORITY':RUN+'/Xauthority','XDG_RUNTIME_DIR':RUN,
             'XDG_CONFIG_HOME':str(profile/'config'),'XDG_CACHE_HOME':str(profile/'cache'),'XDG_DATA_HOME':str(profile/'data'),
             'DBUS_SESSION_BUS_ADDRESS':'unix:path='+RUN+'/bus','NO_AT_BRIDGE':'1'}
        def spawn(args): return subprocess.Popen(args,env=env,cwd=HOME,stdin=subprocess.DEVNULL,close_fds=True)
        subprocess.run(['/usr/bin/xauth','-f',env['XAUTHORITY'],'add',':100','.',secrets.token_hex(16)],env=env,check=True,timeout=5)
        os.chmod(env['XAUTHORITY'],0o600)
        xvnc=spawn(['/usr/bin/Xtigervnc',':100','-geometry',f'{w}x{h}','-depth','24','-rfbport','-1',
            '-rfbunixpath',RUN+'/vnc.sock','-rfbunixmode','0600','-SecurityTypes','None','-localhost',
            '-nolisten','tcp','-auth',env['XAUTHORITY'],'-AcceptCutText=0','-SendCutText=0',
            '-AcceptSetDesktopSize=0','-AllowOverride=','-FrameRate','15','-desktop','WebDesktop guest'])
        end=time.monotonic()+6
        while not Path(RUN+'/vnc.sock').exists() or not Path('/tmp/.X11-unix/X100').exists():
            if xvnc.poll() is not None or time.monotonic()>end: raise RuntimeError('Xvnc startup failed')
            time.sleep(.05)
        bus=spawn(['/usr/bin/dbus-daemon','--session','--nofork','--address='+env['DBUS_SESSION_BUS_ADDRESS']])
        openbox=spawn(['/usr/bin/openbox','--config-file','/usr/local/lib/webdesktop-gui/openbox.xml'])
        children=[xvnc,bus,openbox]
        if m['app']=='pcmanfm': children.append(spawn(['/usr/bin/pcmanfm','--new-win',HOME]))
        remote=socket.socket(socket.AF_UNIX);remote.settimeout(3);remote.connect(RUN+'/vnc.sock')
        connection.sendall(packet({'t':'ready'}))
        connection.setblocking(False);remote.setblocking(False)
        incoming=bytearray();to_vnc=bytearray();outgoing=bytearray()
        with remote,selectors.DefaultSelector() as poll:
            poll.register(connection,selectors.EVENT_READ);poll.register(remote,selectors.EVENT_READ)
            while True:
                if any(child.poll() is not None for child in children): return
                poll.modify(connection,selectors.EVENT_READ|(selectors.EVENT_WRITE if outgoing else 0))
                events=(selectors.EVENT_READ if len(outgoing)<256*1024 else 0)|(selectors.EVENT_WRITE if to_vnc else 0)
                try:
                    if events: poll.modify(remote,events)
                    else: poll.unregister(remote)
                except KeyError:
                    if events: poll.register(remote,events)
                for key,mask in poll.select(.05):
                    if key.fileobj is connection:
                        if mask&selectors.EVENT_WRITE:
                            try: count=connection.send(outgoing);del outgoing[:count]
                            except BlockingIOError: pass
                        if mask&selectors.EVENT_READ:
                            chunk=connection.recv(16384)
                            if not chunk:return
                            incoming.extend(chunk)
                            if len(incoming)>FRAME:raise ValueError('IPC budget')
                            while b'\n' in incoming:
                                line,_,rest=incoming.partition(b'\n');incoming=bytearray(rest);m=json.loads(line)
                                if not isinstance(m,dict) or set(m)!={'t','d'} or m['t']!='d' or not isinstance(m['d'],str):raise ValueError('Fixed binary protocol')
                                chunk=base64.b64decode(m['d'],validate=True)
                                if len(chunk)>16384 or len(to_vnc)+len(chunk)>FRAME:raise ValueError('Input budget')
                                to_vnc.extend(chunk)
                    else:
                        if mask&selectors.EVENT_WRITE:
                            try:count=remote.send(to_vnc);del to_vnc[:count]
                            except BlockingIOError:pass
                        if mask&selectors.EVENT_READ:
                            try:chunk=remote.recv(16384)
                            except BlockingIOError:continue
                            if not chunk:return
                            outgoing.extend(packet({'t':'d','d':base64.b64encode(chunk).decode()}))
    # PID 1, not negative-PID signaling, reaps the unit and all descendants.

if __name__=='__main__':main()
