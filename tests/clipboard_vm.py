"""Root-only C1 observer and fault fixture for a disposable VM. Never packaged.

Outputs only booleans, lengths, hashes and identities; clipboard text itself is
never printed, logged or written to evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
import zlib

if os.getuid() != 0 or socket.gethostname() != 'wd-lab' or not Path('/var/tmp/wd-lab-ready').is_file():
    raise SystemExit('Disposable VM only')
gui, guest = pwd.getpwnam('wdgui'), pwd.getpwnam('wdguest')


def run(args, timeout=10, **kw):
    return subprocess.run(args, capture_output=True, timeout=timeout, **kw)


def prefix():
    pid = run(['systemctl', 'show', 'webdesktop-gui@1.service', '-p', 'MainPID', '--value'], text=True).stdout.strip()
    if pid in ('', '0'):
        raise RuntimeError('No active private display')
    return ['nsenter', '-t', pid, '-m', '-n', '--', 'setpriv', '--reuid', str(gui.pw_uid), '--regid', str(guest.pw_gid),
            '--groups', str(gui.pw_gid), '--no-new-privs', 'env', '-i', 'PATH=/usr/bin:/bin', 'DISPLAY=:100', 'XAUTHORITY=/tmp/wd-gui/Xauthority']


def _chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)


def _png(width, height, extra=b'', raw=None, ihdr_size=None):
    w, h = ihdr_size or (width, height)
    raw = raw if raw is not None else bytes(height * (1 + width * 4))
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + extra
            + _chunk(b'IDAT', zlib.compress(raw)) + _chunk(b'IEND', b''))


def fixture(kind):
    """Fixed, random, non-production fixture bytes and their X11 target."""
    token = os.urandom(6).hex()
    if kind == 'png-oversize':  # structurally valid, but over the 4 MiB byte limit
        return 'image/png', _png(2, 2, extra=_chunk(b'zPad', os.urandom(4 * 1024 * 1024)))
    if kind == 'png-huge-dims':  # tiny file declaring 5000x10 pixels
        return 'image/png', _png(5000, 10, raw=bytes(10 * (1 + 5000 * 4)))
    if kind == 'png-corrupt':
        data = bytearray(_png(4, 4)); data[20] ^= 0xff
        return 'image/png', bytes(data)
    if kind == 'apng':
        return 'image/png', _png(4, 4, extra=_chunk(b'acTL', struct.pack('>II', 1, 0)))
    if kind == 'jpeg':
        return 'image/jpeg', b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + token.encode() + b'\xff\xd9'
    return {
        'uri-list': ('text/uri-list', ('file:///home/wdguest/Documents/c1-%s.txt\n' % token).encode()),
        'gnome-files': ('x-special/gnome-copied-files', ('copy\nfile:///home/wdguest/Documents/c1-%s.txt' % token).encode()),
        'png': ('image/png', b'\x89PNG\r\n\x1a\n' + os.urandom(64)),
        'html-only': ('text/html', ('<b>C1 %s</b>' % token).encode()),
        'invalid-utf8': ('UTF8_STRING', b'C1 \xff\xfe ' + token.encode()),
        'control': ('UTF8_STRING', ('C1 \x1b[31m %s' % token).encode()),
        'oversize': ('UTF8_STRING', ('汉' * 5461).encode() + b'ab'),
    }[kind]


op = sys.argv[1]
request = json.loads(sys.stdin.read() or '{}') if op in ('title-match', 'set', 'journal-count', 'xclip-bench') else {}
if op == 'title-match':
    # Expected substring arrives on stdin; only a boolean leaves.
    needle, kind = request['needle'], request['class']
    if kind not in ('chromium', '[Pp]cmanfm') or not isinstance(needle, str) or len(needle) > 512:
        raise ValueError('Fixed window class and bounded needle')
    p = prefix()
    found = run(p + ['xdotool', 'search', '--onlyvisible', '--class', kind], text=True).stdout.split()[:8]
    titles = [run(p + ['xdotool', 'getwindowname', w], text=True).stdout.strip() for w in found]
    result = {'match': any(needle in t for t in titles), 'windows': len(titles)}
elif op == 'remote-hash':
    out = run(prefix() + ['xclip', '-selection', 'clipboard', '-out', '-target', 'UTF8_STRING'])
    result = {'present': out.returncode == 0, 'bytes': len(out.stdout), 'sha256': hashlib.sha256(out.stdout).hexdigest() if out.returncode == 0 else None}
elif op == 'set':
    target, data = fixture(request['kind'])
    proc = subprocess.Popen(prefix() + ['xclip', '-selection', 'clipboard', '-in', '-target', target],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.stdin.write(data)
    proc.stdin.close()
    result = {'set': proc.wait(timeout=10) == 0, 'target': target, 'bytes': len(data)}
elif op == 'image-hash':
    out = run(prefix() + ['xclip', '-selection', 'clipboard', '-out', '-target', 'image/png'], timeout=90)
    result = {'present': out.returncode == 0 and out.stdout[:8] == b'\x89PNG\r\n\x1a\n', 'bytes': len(out.stdout),
              'sha256': hashlib.sha256(out.stdout).hexdigest() if out.returncode == 0 else None}
elif op == 'xclip-bench':
    # Test-only measurement: how long the private X server takes to move a
    # selection of N bytes between two wdgui xclip clients (no content output).
    import time
    result = {'runs': []}
    for size in request['sizes'][:6]:
        if type(size) is not int or not 0 < size <= 8 * 1024 * 1024:
            raise ValueError('Bounded benchmark size')
        data = os.urandom(size)
        proc = subprocess.Popen(prefix() + ['xclip', '-selection', 'clipboard', '-in', '-target', 'image/x-c2-bench'],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.stdin.write(data); proc.stdin.close(); proc.wait(timeout=30)
        started = time.monotonic()
        try:
            out = run(prefix() + ['xclip', '-selection', 'clipboard', '-out', '-target', 'image/x-c2-bench'], timeout=120)
            ok = out.returncode == 0 and hashlib.sha256(out.stdout).digest() == hashlib.sha256(data).digest()
        except subprocess.TimeoutExpired:
            ok = False
        result['runs'].append({'bytes': size, 'ms': round((time.monotonic() - started) * 1000), 'ok': ok})
elif op == 'gui-resources':
    # Numbers only: memory/tasks of the GUI and browser units and open FDs of
    # the GUI unit's processes (worker, xclip owners included).
    result = {}
    for unit in ('webdesktop-gui@1.service', 'webdesktop-browser@1.service'):
        props = dict(line.split('=', 1) for line in run(['systemctl', 'show', unit, '-p', 'MemoryCurrent,MemoryPeak,TasksCurrent,ActiveState,ControlGroup'], text=True).stdout.splitlines() if '=' in line)
        fds = 0
        group = props.get('ControlGroup', '')
        if group:
            try:
                for pid in Path('/sys/fs/cgroup' + group + '/cgroup.procs').read_text().split():
                    try:
                        fds += len(os.listdir('/proc/%s/fd' % pid))
                    except OSError:
                        pass
            except OSError:
                pass
        num = lambda k: int(props[k]) if props.get(k, '').isdigit() else None
        result[unit] = {'state': props.get('ActiveState'), 'memory': num('MemoryCurrent'), 'peak': num('MemoryPeak'), 'tasks': num('TasksCurrent'), 'fds': fds}
elif op == 'xvnc-flags':
    flags = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            argv = Path('/proc/%s/cmdline' % pid).read_bytes().split(b'\0')
        except OSError:
            continue
        if argv and argv[0].endswith(b'Xtigervnc'):
            flags.append({'accept_cut_text_off': b'-AcceptCutText=0' in argv, 'send_cut_text_off': b'-SendCutText=0' in argv,
                          'tcp_off': b'-nolisten' in argv and b'tcp' in argv})
    result = {'servers': flags}
elif op == 'xclip-procs':
    procs = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            argv = Path('/proc/%s/cmdline' % pid).read_bytes().split(b'\0')
            if not argv or not argv[0].endswith(b'xclip'):
                continue
            status = Path('/proc/%s/status' % pid).read_text()
            cgroup = Path('/proc/%s/cgroup' % pid).read_text().strip()
        except OSError:
            continue
        uid = int(next(l.split()[1] for l in status.splitlines() if l.startswith('Uid:')))
        procs.append({'user': pwd.getpwuid(uid).pw_name, 'unit': cgroup.rsplit('/', 1)[-1]})
    result = {'processes': procs}
elif op == 'x11-denied':
    # Real UIDs, no mount-namespace help: only wdgui may reach the display.
    code = ("import socket,json\nr={}\ns=socket.socket(socket.AF_UNIX)\n"
            "try:s.connect('/run/webdesktop-display/1/x11/X100');r['x11']=False\nexcept PermissionError:r['x11']=True\nexcept FileNotFoundError:r['x11']='missing'\n"
            "try:open('/run/webdesktop-display/1/ipc/Xauthority','rb');r['xauth']=False\nexcept PermissionError:r['xauth']=True\nexcept FileNotFoundError:r['xauth']='missing'\n"
            "print(json.dumps(r))")
    result = {}
    for user in ('wdsvc', 'wdguest', 'wdterm'):
        out = run(['runuser', '-u', user, '--', 'python3', '-I', '-c', code], text=True)
        result[user] = json.loads(out.stdout) if out.returncode == 0 else {'error': out.returncode}
elif op == 'protected':
    paths = ['/opt/webdesktop/app/.env', '/opt/webdesktop/source/package.json', '/opt/webdesktop/source/.git/HEAD']
    result = {'hashes': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths if os.path.isfile(p)},
              'executed': [p for p in ('/home/wdguest/Documents/C1-EXECUTED', '/opt/webdesktop/app/C1-EXECUTED', '/tmp/C1-EXECUTED') if os.path.lexists(p)]}
elif op == 'journal-count':
    # Needle arrives on stdin so this command line never contains it.
    needle, since = request['needle'], request['since']
    if not isinstance(needle, str) or not 8 <= len(needle) <= 64 or type(since) is not int:
        raise ValueError('Bounded needle and numeric since')
    out = subprocess.run(['journalctl', '--no-pager', '-o', 'cat', '--since', '@%d' % since], capture_output=True, text=True, timeout=60).stdout
    result = {'count': out.count(needle)}
else:
    raise SystemExit('Unknown observer action')
print(json.dumps(result))
