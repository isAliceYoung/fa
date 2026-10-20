#!/usr/bin/env python3
"""Trusted wdsvc lease guard. Never executes a shell or handles credentials.

Only stopping the root-defined basic GUI unit is authorized. Socket activation
starts them; no transient-unit, StartUnit, property or arbitrary sudo grant.
"""
import fcntl
import os
from pathlib import Path
import pwd
import selectors
import stat
import socket
import struct
import subprocess
import sys
import time

def main():
    service = pwd.getpwnam('wdsvc')
    if os.getresuid() != (service.pw_uid,) * 3 or not service.pw_uid or len(sys.argv) != 2 or sys.argv[1] != '1':
        raise RuntimeError('Fixed unprivileged service guard required')
    unit = 'webdesktop-gui@' + sys.argv[1] + '.service'
    directory = Path(service.pw_dir) / '.webdesktop-gui'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != service.pw_uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('Unsafe lease directory')
    lock = os.open(str(directory / ('slot-' + sys.argv[1])), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != service.pw_uid or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('Unsafe lease lock')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.environ.clear()
    environment = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    def stop():
        subprocess.run(['/usr/bin/systemctl','--no-ask-password','stop',unit],env=environment,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=5)
    stop()  # No previous incarnation may survive slot reuse.
    listener=socket.socket(fileno=3)
    listener.set_inheritable(False)
    if listener.family!=socket.AF_UNIX or listener.getsockname()!='/run/webdesktop-gui/lease-'+sys.argv[1]+'.sock':
        raise RuntimeError('Protected lease socket required')
    listener.settimeout(5)
    with listener:connection=listener.accept()[0]
    _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if uid!=service.pw_uid:raise RuntimeError('Service peer required')
    connection.setblocking(False)
    deadline = time.monotonic() + 10
    buffer = bytearray()
    connection.sendall(b'READY\n')
    try:
        with selectors.DefaultSelector() as poll:
            poll.register(connection,selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not poll.select(max(0,deadline-time.monotonic())): break
                data=connection.recv(128)
                if not data: break
                buffer.extend(data)
                if len(buffer)>128: break
                while b'\n' in buffer:
                    line,_,remaining=buffer.partition(b'\n');buffer=bytearray(remaining)
                    if line != b'renew': return
                    deadline=time.monotonic()+10
    finally:
        stop()
        connection.close()
        os.close(lock)

if __name__ == '__main__':
    main()
