#!/usr/bin/env python3
"""Untrusted PTY data plane. No login, database, service control or secrets.

Bash can attack this same-UID process. Authentication, leases and cgroup stop
therefore belong to the different-UID backend guard, never to this worker.
"""
import base64
import fcntl
import json
import os
import pty
import pwd
import selectors
import socket
import struct
import sys
import termios
import time

HOME = '/home/wdguest'
MAX_FRAME = 65536

def dimensions(message):
    cols, rows = message.get('c'), message.get('r')
    if type(cols) is not int or type(rows) is not int or not 10 <= cols <= 500 or not 2 <= rows <= 200:
        raise ValueError('Invalid terminal size')
    return cols, rows

def packet(value):
    return json.dumps(value, separators=(',', ':')).encode() + b'\n'

def serve(connection):
    connection.settimeout(5)
    initial = bytearray()
    while b'\n' not in initial:
        data = connection.recv(4096)
        if not data or len(initial) + len(data) > MAX_FRAME:
            raise ValueError('Invalid initial frame')
        initial.extend(data)
    line, extra = bytes(initial).split(b'\n', 1)
    message = json.loads(line)
    if not isinstance(message, dict) or set(message) != {'t', 'c', 'r'} or message['t'] != 'open' or extra:
        raise ValueError('Fixed open frame required')
    cols, rows = dimensions(message)
    child, master = pty.fork()
    if child == 0:
        os.closerange(3, 128)
        os.chdir(HOME)
        environment = {'HOME': HOME, 'USER': 'wdterm', 'LOGNAME': 'wdterm',
            'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TERM': 'xterm-256color', 'SHELL': '/bin/bash', 'PS1': '\\u@webdesktop:\\w\\$ '}
        os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-i'], environment)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    connection.sendall(packet({'t': 'ready'}))
    connection.setblocking(False)
    os.set_blocking(master, False)
    pending, incoming, input_bytes = bytearray(), bytearray(), bytearray()
    exited = None
    exit_deadline = None
    try:
        with selectors.DefaultSelector() as poll:
            poll.register(connection, selectors.EVENT_READ)
            poll.register(master, selectors.EVENT_READ)
            while True:
                if exited is None:
                    waited, status = os.waitpid(child, os.WNOHANG)
                    if waited:
                        exited = os.waitstatus_to_exitcode(status)
                        pending.extend(packet({'t': 'exit', 'code': exited}))
                        exit_deadline = time.monotonic() + 1
                if exited is not None and (not pending or time.monotonic() >= exit_deadline):
                    return
                poll.modify(connection, selectors.EVENT_READ | (selectors.EVENT_WRITE if pending else 0))
                events = (selectors.EVENT_READ if len(pending) < 256 * 1024 and exited is None else 0) | (selectors.EVENT_WRITE if input_bytes else 0)
                try:
                    if events: poll.modify(master, events)
                    else: poll.unregister(master)
                except KeyError:
                    if events: poll.register(master, events)
                for key, mask in poll.select(.05):
                    if key.fileobj is connection:
                        if mask & selectors.EVENT_WRITE:
                            try:
                                sent = connection.send(pending)
                                del pending[:sent]
                            except BlockingIOError: pass
                        if mask & selectors.EVENT_READ:
                            data = connection.recv(16384)
                            if not data: return
                            incoming.extend(data)
                            if len(incoming) > MAX_FRAME: raise ValueError('Input frame budget')
                            while b'\n' in incoming:
                                line, _, remainder = incoming.partition(b'\n')
                                incoming = bytearray(remainder)
                                message = json.loads(line)
                                if not isinstance(message, dict): raise ValueError('Object required')
                                if message.get('t') == 'i' and set(message) == {'t', 'd'} and isinstance(message['d'], str):
                                    data = message['d'].encode('utf-8')
                                    if len(data) > 16384 or len(input_bytes) + len(data) > MAX_FRAME: raise ValueError('Input budget')
                                    input_bytes.extend(data)
                                elif message.get('t') == 'r' and set(message) == {'t', 'c', 'r'}:
                                    cols, rows = dimensions(message)
                                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
                                elif message == {'t': 'kill'}: return
                                else: raise ValueError('Unknown terminal operation')
                    else:
                        if mask & selectors.EVENT_WRITE:
                            try:
                                sent = os.write(master, input_bytes)
                                del input_bytes[:sent]
                            except BlockingIOError: pass
                        if mask & selectors.EVENT_READ:
                            try: data = os.read(master, 16384)
                            except BlockingIOError: continue
                            except OSError: data = b''
                            if data: pending.extend(packet({'t': 'o', 'd': base64.b64encode(data).decode('ascii')}))
    finally:
        os.close(master)
        # No PID/group kills: exiting the main worker makes systemd reap every
        # descendant in the unit cgroup, including setsid and double-fork daemons.

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('1', '2'): raise RuntimeError('Fixed slot required')
    terminal, data, service = map(pwd.getpwnam, ('wdterm', 'wdguest', 'wdsvc'))
    if os.getresuid() != (terminal.pw_uid,) * 3 or os.getresgid() != (data.pw_gid,) * 3 or not terminal.pw_uid:
        raise RuntimeError('Restricted terminal identity required')
    if set(os.getgroups()) - {data.pw_gid, terminal.pw_gid}: raise RuntimeError('Unexpected groups')
    status = open('/proc/self/status').read()
    if 'NoNewPrivs:\t1' not in status or any(int(line.split()[1],16) for line in status.splitlines() if line.startswith(('CapEff:','CapPrm:','CapAmb:','CapBnd:'))):
        raise RuntimeError('No privileges permitted')
    os.environ.clear()
    os.umask(0o007)
    listener = socket.socket(fileno=3)
    listener.set_inheritable(False)
    if listener.family != socket.AF_UNIX or listener.getsockname() != '/run/webdesktop-pty/session-' + sys.argv[1] + '.sock' or not listener.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN):
        raise RuntimeError('Protected inherited listener required')
    listener.settimeout(10)
    with listener:
        with listener.accept()[0] as connection:
            connection.set_inheritable(False)
            _, uid, _ = struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid != service.pw_uid: raise RuntimeError('Service peer required')
            serve(connection)

if __name__ == '__main__':
    main()
