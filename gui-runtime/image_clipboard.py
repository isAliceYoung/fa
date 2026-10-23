"""User-initiated, PNG-only X11 CLIPBOARD image bridge for the wdgui worker.

Two fixed operations: own the CLIPBOARD with one bounded image/png, or read
the CLIPBOARD's image/png. The gateway (wdsvc) never touches X11; this module
runs as wdgui inside the GUI unit. Program, arguments, display, selection and
target are fixed here. Only bounded PNG bytes come from the web client, and
they are only ever written to xclip's stdin, never to a file.

Images cross the existing session IPC in small base64 chunks so a transfer can
never fill the IPC write buffer that keyboard/mouse input also needs.
"""
import base64
import binascii
import hashlib
import os
import struct
import subprocess
import time
import zlib

MAX_BYTES = 4 * 1024 * 1024
MAX_SIDE = 4096
MAX_PIXELS = 3840 * 2160
MAX_CHUNKS = 10000
CHUNK = 16384
XCLIP = '/usr/bin/xclip'
TIMEOUT = 15.0  # per xclip call; large selections move in INCR chunks
UPLOAD_IDLE = 20.0
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
TARGET = 'image/png'
# File lists and URIs are never exported as images (no file-read path).
FILE_TARGETS = {'text/uri-list', 'x-special/gnome-copied-files', 'x-special/mate-copied-files',
                'x-special/nautilus-clipboard', 'application/x-kde-cutselection',
                'text/x-moz-url', 'application/x-moz-file'}
_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_ANIMATION = {b'acTL', b'fcTL', b'fdAT'}
_CRITICAL = {b'IHDR', b'PLTE', b'IDAT', b'IEND'}


def _raw_size(width, height, bits, interlace):
    row = lambda w: (w * bits + 7) // 8
    if not interlace:
        return height * (1 + row(width))
    total = 0
    for x0, y0, dx, dy in _ADAM7:
        pw = (width - x0 + dx - 1) // dx if width > x0 else 0
        ph = (height - y0 + dy - 1) // dy if height > y0 else 0
        if pw and ph:
            total += ph * (1 + row(pw))
    return total


def png_info(data, inflate=True):
    """Validate a complete static PNG. Returns (code, info).

    Structure, every chunk CRC, sizes and (with inflate) that the zlib stream
    decodes to exactly the expected number of bytes, streamed without keeping
    the decoded image. Codes: TOO_LARGE, INVALID_IMAGE, UNSUPPORTED_FORMAT."""
    if not isinstance(data, (bytes, bytearray)):
        return 'INVALID_IMAGE', None
    if len(data) > MAX_BYTES:
        return 'TOO_LARGE', None
    if len(data) < 8 + 25 + 12 + 12 or data[:8] != PNG_SIGNATURE:
        return 'INVALID_IMAGE', None
    pos, chunks, info, idat, seen_idat, after_idat, palette = 8, 0, None, [], False, False, False
    while True:
        if pos + 12 > len(data):
            return 'INVALID_IMAGE', None
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        if length > 0x7fffffff or pos + 12 + length > len(data) or not kind.isalpha():
            return 'INVALID_IMAGE', None
        body = data[pos + 8:pos + 8 + length]
        crc, = struct.unpack('>I', data[pos + 8 + length:pos + 12 + length])
        if binascii.crc32(kind + body) & 0xffffffff != crc:
            return 'INVALID_IMAGE', None
        chunks += 1
        if chunks > MAX_CHUNKS:
            return 'INVALID_IMAGE', None
        if chunks == 1:
            if kind != b'IHDR' or length != 13:
                return 'INVALID_IMAGE', None
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE) or width * height > MAX_PIXELS:
                return 'TOO_LARGE', None
            if color not in _DEPTHS or depth not in _DEPTHS[color] or compression or filtering or interlace not in (0, 1):
                return 'INVALID_IMAGE', None
            info = {'width': width, 'height': height, 'depth': depth, 'color': color, 'interlace': interlace}
        elif kind == b'IHDR':
            return 'INVALID_IMAGE', None
        elif kind in _ANIMATION:
            return 'UNSUPPORTED_FORMAT', None
        elif kind == b'PLTE':
            if seen_idat or palette or not length or length % 3 or length > 768:
                return 'INVALID_IMAGE', None
            palette = True
        elif kind == b'IDAT':
            if after_idat:
                return 'INVALID_IMAGE', None
            seen_idat = True
            idat.append(body)
        elif kind == b'IEND':
            if length or not seen_idat or pos + 12 != len(data):
                return 'INVALID_IMAGE', None
            break
        elif kind[0] < 0x61 and kind not in _CRITICAL:  # unknown critical chunk
            return 'INVALID_IMAGE', None
        if seen_idat and kind != b'IDAT':
            after_idat = True
        pos += 12 + length
    if info['color'] == 3 and not palette:
        return 'INVALID_IMAGE', None
    expected = _raw_size(info['width'], info['height'], _CHANNELS[info['color']] * info['depth'], info['interlace'])
    if inflate:
        decoder, produced = zlib.decompressobj(), 0
        try:
            for part in idat:
                buffer = part
                while buffer:
                    out = decoder.decompress(buffer, 262144)
                    produced += len(out)
                    if produced > expected:
                        return 'INVALID_IMAGE', None
                    buffer = decoder.unconsumed_tail
            out = decoder.flush()
            produced += len(out)
        except zlib.error:
            return 'INVALID_IMAGE', None
        if produced != expected or not decoder.eof or decoder.unused_data:
            return 'INVALID_IMAGE', None
    info['bytes'] = len(data)
    return None, info


def target_problem(targets):
    """An image read requires image/png and no file/URI target."""
    names = set(targets)
    if not names:
        return 'EMPTY'
    if names & FILE_TARGETS:
        return 'UNSUPPORTED_FORMAT'
    if TARGET in names:
        return None
    if any(name.startswith('image/') for name in names):
        return 'UNSUPPORTED_FORMAT'
    return 'NON_IMAGE'


def parse_request(message):
    """Strict gateway->worker image messages. Raises ValueError otherwise.

    {t:'img',op:'write',n,size,sha256}  {t:'img',op:'read',n}
    {t:'img',op:'cancel',n}             {t:'imgc',n,i,d}"""
    if not isinstance(message, dict) or message.get('t') not in ('img', 'imgc'):
        raise ValueError('Image message')
    n = message.get('n')
    if type(n) is not int or not 0 < n < 2**31:
        raise ValueError('Image sequence')
    if message['t'] == 'imgc':
        i, d = message.get('i'), message.get('d')
        if set(message) != {'t', 'n', 'i', 'd'} or type(i) is not int or not 0 <= i < MAX_BYTES // CHUNK + 1 or not isinstance(d, str):
            raise ValueError('Image chunk')
        if len(d) > (CHUNK + 2) // 3 * 4:
            raise ValueError('Image chunk bound')
        return 'chunk', n, (i, base64.b64decode(d, validate=True))
    op = message.get('op')
    if op in ('read', 'cancel') and set(message) == {'t', 'op', 'n'}:
        return op, n, None
    if op == 'write' and set(message) == {'t', 'op', 'n', 'size', 'sha256'}:
        size, digest = message['size'], message['sha256']
        if type(size) is not int or not 0 < size <= MAX_BYTES or not isinstance(digest, str) or len(digest) != 64 \
                or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Image header')
        return op, n, (size, digest)
    raise ValueError('Image operation')


class Upload:
    """Receives one announced image in strictly sequential bounded chunks."""

    def __init__(self, n, size, digest, now=time.monotonic):
        self.n, self.size, self.digest, self.now = n, size, digest, now
        self.data, self.next, self.touched = bytearray(), 0, now()

    def add(self, i, chunk):
        """Returns True when complete. Raises ValueError on protocol misuse."""
        if i != self.next or not chunk or len(chunk) > CHUNK or len(self.data) + len(chunk) > self.size:
            raise ValueError('Image chunk order/bound')
        self.data.extend(chunk)
        self.next += 1
        self.touched = self.now()
        return len(self.data) == self.size

    def expired(self):
        return self.now() - self.touched > UPLOAD_IDLE

    def verified(self):
        """Code for a complete upload: digest first, then full PNG check."""
        if hashlib.sha256(self.data).hexdigest() != self.digest:
            return 'INVALID_IMAGE'
        code, _ = png_info(bytes(self.data))
        return code


class Download:
    """Streams one validated image as header + sequential chunks on demand."""

    def __init__(self, n, data):
        self.n, self.data, self.offset, self.index = n, data, 0, 0

    def header(self):
        return {'t': 'img', 'n': self.n, 'ok': True, 'code': 'OK', 'size': len(self.data),
                'sha256': hashlib.sha256(self.data).hexdigest()}

    def next_chunk(self):
        if self.offset >= len(self.data):
            return None
        part = self.data[self.offset:self.offset + CHUNK]
        message = {'t': 'imgc', 'n': self.n, 'i': self.index, 'd': base64.b64encode(part).decode()}
        self.offset += len(part)
        self.index += 1
        return message


def _own(data, env):
    """Own CLIPBOARD as image/png. xclip reads stdin, forks one background owner
    (stdout/stderr /dev/null) and exits; the owner stays in this GUI unit's
    cgroup until another client takes the selection or the unit stops."""
    proc = subprocess.Popen([XCLIP, '-selection', 'clipboard', '-in', '-target', TARGET], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        proc.stdin.write(data)
        proc.stdin.close()
        return None if proc.wait(timeout=TIMEOUT) == 0 else 'UNAVAILABLE'
    except (subprocess.TimeoutExpired, BrokenPipeError):
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
                chunk = proc.stdout.read(262144)
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
            if chunk is None:
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


def write(data, env):
    """Own the CLIPBOARD with an already verified PNG. Returns a result dict."""
    code = _own(bytes(data), env)
    return {'ok': code is None, 'code': code or 'OK'}


def read(env):
    """Read the CLIPBOARD's PNG. Returns (result dict, bytes or None)."""
    code, raw = _read('TARGETS', env, 4096)
    if code:
        return {'ok': False, 'code': 'EMPTY' if code == 'UNAVAILABLE' else code}, None
    problem = target_problem([line.strip() for line in raw.decode('utf-8', 'replace').splitlines() if line.strip()])
    if problem:
        return {'ok': False, 'code': problem}, None
    code, raw = _read(TARGET, env, MAX_BYTES)
    if code:
        return {'ok': False, 'code': code}, None
    problem, _ = png_info(raw)
    if problem:
        return {'ok': False, 'code': problem}, None
    return {'ok': True, 'code': 'OK'}, raw


class Bridge:
    """Worker-side image state machine, kept out of the RFB relay loop.

    handle(message) consumes one gateway message and returns reply dicts;
    poll(room) returns finished results, idle timeouts and, while room() is
    true, the next download chunks. Jobs (validation + xclip) run in a thread
    created by start(); a failing job reports UNAVAILABLE, never an exception."""

    def __init__(self, env, start=None, text_busy=lambda: False):
        import threading
        self.env, self.text_busy = env, text_busy
        self.start = start or (lambda target: threading.Thread(target=target, daemon=True))
        self.upload = self.download = self.thread = self.result = None
        self.cancelled = 0

    def busy(self):
        return bool(self.thread or self.upload or self.download)

    def _job(self, n, op, upload):
        try:
            if op == 'read':
                result, raw = read(self.env)
                self.result = (n, dict(t='img', n=n, **result), raw)
            else:
                code = upload.verified()
                self.result = (n, dict(t='img', n=n, ok=False, code=code) if code else dict(t='img', n=n, **write(upload.data, self.env)), None)
        except Exception:
            self.result = (n, dict(t='img', n=n, ok=False, code='UNAVAILABLE'), None)

    def _run(self, n, op, upload=None):
        self.thread = self.start(lambda: self._job(n, op, upload))
        self.thread.start()

    def handle(self, message):
        op, n, data = parse_request(message)
        if op == 'cancel':
            self.cancelled = n
            if self.upload and self.upload.n == n:
                self.upload = None
            if self.download and self.download.n == n:
                self.download = None
            return []
        if op == 'chunk':
            if not self.upload or self.upload.n != n:
                if n == self.cancelled:
                    return []  # in flight before the cancel/BUSY
                raise ValueError('Image chunk without upload')
            if self.upload.add(*data):
                upload, self.upload = self.upload, None
                self._run(n, 'write', upload)
            return []
        if self.busy() or self.text_busy():
            if op == 'write':
                self.cancelled = n  # its chunks may already be in flight
            return [{'t': 'img', 'n': n, 'ok': False, 'code': 'BUSY'}]
        if op == 'write':
            self.upload = Upload(n, *data)
        else:
            self._run(n, 'read')
        return []

    def poll(self, room=lambda: True):
        out = []
        if self.thread and not self.thread.is_alive():
            self.thread = None
            (n, result, raw), self.result = self.result, None
            if n != self.cancelled:
                if raw is not None:
                    self.download = Download(n, raw)
                    out.append(self.download.header())
                else:
                    out.append(result)
        if self.upload and self.upload.expired():
            out.append({'t': 'img', 'n': self.upload.n, 'ok': False, 'code': 'TIMEOUT'})
            self.upload = None
        while self.download and room():
            part = self.download.next_chunk()
            if part is None:
                self.download = None
            else:
                out.append(part)
        return out
