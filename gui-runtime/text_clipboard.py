"""User-initiated, text-only X11 CLIPBOARD bridge for the wdgui worker.

Only two fixed operations exist: read the CLIPBOARD as UTF8_STRING, or own the
CLIPBOARD with one bounded UTF8_STRING. The gateway (wdsvc) never touches X11;
this module runs as wdgui inside the GUI unit. Program, arguments, display,
selection and target are fixed here; nothing comes from the web client except
the bounded text itself, which is only ever written to xclip's stdin.
"""
import os
import subprocess
import time

MAX_BYTES = 16384
XCLIP = '/usr/bin/xclip'
TIMEOUT = 2.0
# A clipboard that offers files, URIs or images is not plain text, even when a
# text fallback target is also present: never export paths or file lists.
REJECTED_TARGETS = {'text/uri-list', 'x-special/gnome-copied-files', 'x-special/mate-copied-files',
                    'x-special/nautilus-clipboard', 'application/x-kde-cutselection',
                    'application/x-qt-image', 'text/x-moz-url', 'application/x-moz-file'}
ALLOWED_CONTROLS = {0x09, 0x0a, 0x0d}


def text_problem(text):
    """Return an error code for a decoded text, or None when it is acceptable."""
    if not text:
        return 'EMPTY'
    for ch in text:
        o = ord(ch)
        if (o < 0x20 and o not in ALLOWED_CONTROLS) or 0x7f <= o <= 0x9f:
            return 'CONTROL_CHARACTERS'
        if 0xd800 <= o <= 0xdfff:
            return 'INVALID_UTF8'
    return None


def decode(data):
    """Validate raw UTF-8 bytes from X11 or the IPC. Returns (code, text)."""
    if len(data) > MAX_BYTES:
        return 'TOO_LARGE', None
    try:
        text = data.decode('utf-8', 'strict')
    except UnicodeDecodeError:
        return 'INVALID_UTF8', None
    problem = text_problem(text)
    return (problem, None) if problem else (None, text)


def target_problem(targets):
    """Plain text requires UTF8_STRING and no file, URI or image target."""
    names = set(targets)
    if not names:
        return 'EMPTY'
    if names & REJECTED_TARGETS or any(t.startswith('image/') for t in names):
        return 'NON_TEXT'
    if 'UTF8_STRING' not in names:
        return 'NON_TEXT'
    return None


def parse_request(message):
    """Strict IPC request shape. Raises ValueError on any other field or type."""
    if not isinstance(message, dict) or message.get('t') != 'clip':
        raise ValueError('Clipboard message')
    op, n = message.get('op'), message.get('n')
    if type(n) is not int or not 0 < n < 2**31:
        raise ValueError('Clipboard sequence')
    if op == 'read' and set(message) == {'t', 'op', 'n'}:
        return op, n, None
    if op == 'write' and set(message) == {'t', 'op', 'n', 'text'} and isinstance(message['text'], str):
        data = message['text'].encode('utf-8', 'surrogatepass')
        if len(data) > MAX_BYTES:
            raise ValueError('Clipboard text bound')
        return op, n, data
    raise ValueError('Clipboard operation')


def _own(text, env):
    """Own CLIPBOARD with UTF8_STRING. xclip reads stdin, forks one background
    owner (stdout/stderr /dev/null, so no pipe outlives it) and the parent
    exits; that owner stays in this GUI unit's cgroup until it loses the
    selection or PID 1 reaps the unit."""
    proc = subprocess.Popen([XCLIP, '-selection', 'clipboard', '-in', '-target', 'UTF8_STRING'], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        proc.stdin.write(text)
        proc.stdin.close()
        return None if proc.wait(timeout=TIMEOUT) == 0 else 'UNAVAILABLE'
    except subprocess.TimeoutExpired:
        return 'TIMEOUT'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _read(target, env, limit):
    """Fixed xclip -out for one target with bounded output and time; no shell."""
    proc = subprocess.Popen([XCLIP, '-selection', 'clipboard', '-out', '-target', target], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + TIMEOUT
    out = bytearray()
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        while True:
            try:
                chunk = proc.stdout.read(65536)
            except BlockingIOError:
                chunk = None
            if chunk:
                out.extend(chunk)
                if len(out) > limit:
                    return 'TOO_LARGE', None
            elif chunk == b'':
                break
            if time.monotonic() > deadline:
                return 'TIMEOUT', None
            time.sleep(.01)
        try:
            code = proc.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return 'TIMEOUT', None
        return (None, bytes(out)) if code == 0 else ('UNAVAILABLE', None)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def handle(op, data, env):
    """Run one request; returns a response dict without echoing written text."""
    if op == 'write':
        problem, text = decode(data)
        if problem:
            return {'ok': False, 'code': problem}
        # xclip forks one owner process inside this GUI unit; it exits when
        # another client takes the selection and PID 1 reaps it with the unit.
        code = _own(text.encode('utf-8'), env)
        return {'ok': code is None, 'code': code or 'OK', 'bytes': len(data)}
    code, raw = _read('TARGETS', env, 4096)
    if code:
        return {'ok': False, 'code': 'EMPTY' if code == 'UNAVAILABLE' else code}
    problem = target_problem([line.strip() for line in raw.decode('utf-8', 'replace').splitlines() if line.strip()])
    if problem:
        return {'ok': False, 'code': problem}
    code, raw = _read('UTF8_STRING', env, MAX_BYTES)
    if code:
        return {'ok': False, 'code': code}
    problem, text = decode(raw)
    if problem:
        return {'ok': False, 'code': problem}
    return {'ok': True, 'code': 'OK', 'text': text, 'bytes': len(raw)}
