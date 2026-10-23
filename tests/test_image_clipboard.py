"""Deterministic rules of the wdgui PNG image clipboard (no X11 required)."""
import base64
import hashlib
import importlib.util
import re
import struct
import unittest
import zlib
from pathlib import Path

spec = importlib.util.spec_from_file_location('image_clipboard', Path(__file__).resolve().parents[1] / 'gui-runtime/image_clipboard.py')
img = importlib.util.module_from_spec(spec)
spec.loader.exec_module(img)


def chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)


def png(width=4, height=3, color=6, depth=8, interlace=0, raw=None, extra=(), level=6, palette=None):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    if raw is None:
        raw = img._raw_size(width, height, channels * depth, interlace)
        raw = bytes(raw)  # all-zero filter bytes and pixels: a valid image
    parts = [img.PNG_SIGNATURE, chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace))]
    if color == 3:
        parts.append(chunk(b'PLTE', palette if palette is not None else b'\x00\x00\x00\xff\xff\xff'))
    parts.extend(extra)
    parts += [chunk(b'IDAT', zlib.compress(raw, level)), chunk(b'IEND', b'')]
    return b''.join(parts)


class PngValidation(unittest.TestCase):
    def test_valid_kinds(self):
        for kwargs in ({}, {'color': 2}, {'color': 0, 'depth': 1}, {'color': 0, 'depth': 16}, {'color': 4},
                       {'color': 3, 'depth': 4}, {'color': 6, 'depth': 16}, {'interlace': 1, 'width': 13, 'height': 9},
                       {'extra': [chunk(b'tEXt', b'Comment\x00C2')]}):
            code, info = img.png_info(png(**kwargs))
            self.assertIsNone(code, kwargs)
            self.assertEqual((info['width'], info['height']), (kwargs.get('width', 4), kwargs.get('height', 3)))

    def test_transparency_survives_unchanged(self):
        # RGBA with alpha 0/128/255: the bridge never re-encodes, bytes are forwarded as-is.
        rows = b''.join(b'\x00' + bytes([255, 0, 0, 0, 0, 255, 0, 128, 0, 0, 255, 255]) for _ in range(2))
        data = png(width=3, height=2, raw=rows)
        self.assertIsNone(img.png_info(data)[0])
        upload = img.Upload(7, len(data), hashlib.sha256(data).hexdigest())
        for i, offset in enumerate(range(0, len(data), img.CHUNK)):
            upload.add(i, data[offset:offset + img.CHUNK])
        self.assertEqual(bytes(upload.data), data)

    def test_dimension_and_size_limits(self):
        self.assertIsNone(img.png_info(png(width=4096, height=1, color=0, depth=1))[0])
        self.assertIsNone(img.png_info(png(width=3840, height=2160, color=0, depth=1, level=9))[0])
        self.assertEqual(img.png_info(png(width=4097, height=1, color=0, depth=1))[0], 'TOO_LARGE')
        self.assertEqual(img.png_info(png(width=4096, height=2048, color=0, depth=1, level=9))[0], 'TOO_LARGE')  # > 3840x2160 pixels
        self.assertEqual(img.png_info(png(width=0, height=1))[0], 'TOO_LARGE')
        self.assertEqual(img.png_info(b'\x00' * (img.MAX_BYTES + 1))[0], 'TOO_LARGE')

    def test_corrupt_truncated_and_mismatched(self):
        good = png()
        self.assertEqual(img.png_info(good[:-1])[0], 'INVALID_IMAGE')               # truncated IEND
        self.assertEqual(img.png_info(good + b'x')[0], 'INVALID_IMAGE')             # trailing bytes
        self.assertEqual(img.png_info(b'GIF89a' + good[6:])[0], 'INVALID_IMAGE')    # format mismatch
        bad_crc = bytearray(good); bad_crc[30] ^= 1
        self.assertEqual(img.png_info(bytes(bad_crc))[0], 'INVALID_IMAGE')
        self.assertEqual(img.png_info(png(raw=b'\x00' * 10))[0], 'INVALID_IMAGE')  # decodes to the wrong size
        self.assertEqual(img.png_info(png(depth=3, color=2))[0], 'INVALID_IMAGE')
        self.assertEqual(img.png_info(png(color=3, palette=b''))[0], 'INVALID_IMAGE')
        self.assertEqual(img.png_info(png(extra=[chunk(b'ABCD', b'')]))[0], 'INVALID_IMAGE')  # unknown critical
        broken = good.replace(zlib.compress(bytes(img._raw_size(4, 3, 32, 0)), 6), b'\x78\x9c\xff\xff')
        self.assertEqual(img.png_info(broken)[0], 'INVALID_IMAGE')
        self.assertEqual(img.png_info(png(extra=[chunk(b'acTL', b'\x00' * 8)]))[0], 'UNSUPPORTED_FORMAT')

    def test_decompression_bomb_is_bounded(self):
        # A small stream that would inflate far beyond the declared image size.
        bomb = png(width=4, height=3, raw=b'\x00' * (50 * 1024 * 1024), level=9)
        self.assertLess(len(bomb), 100000)
        self.assertEqual(img.png_info(bomb)[0], 'INVALID_IMAGE')

    def test_targets(self):
        self.assertIsNone(img.target_problem(['TARGETS', 'image/png', 'text/html']))
        self.assertEqual(img.target_problem(['UTF8_STRING', 'TEXT']), 'NON_IMAGE')
        self.assertEqual(img.target_problem(['image/jpeg']), 'UNSUPPORTED_FORMAT')
        for targets in (['image/png', 'text/uri-list'], ['x-special/gnome-copied-files', 'image/png'], ['application/x-kde-cutselection', 'image/png']):
            self.assertEqual(img.target_problem(targets), 'UNSUPPORTED_FORMAT', targets)
        self.assertEqual(img.target_problem([]), 'EMPTY')


class Protocol(unittest.TestCase):
    def test_exact_messages(self):
        digest = 'a' * 64
        self.assertEqual(img.parse_request({'t': 'img', 'op': 'read', 'n': 1}), ('read', 1, None))
        self.assertEqual(img.parse_request({'t': 'img', 'op': 'cancel', 'n': 2}), ('cancel', 2, None))
        self.assertEqual(img.parse_request({'t': 'img', 'op': 'write', 'n': 3, 'size': 10, 'sha256': digest}), ('write', 3, (10, digest)))
        self.assertEqual(img.parse_request({'t': 'imgc', 'n': 3, 'i': 0, 'd': base64.b64encode(b'abc').decode()}), ('chunk', 3, (0, b'abc')))
        for bad in ({'t': 'img', 'op': 'read', 'n': 1, 'path': '/etc/passwd'}, {'t': 'img', 'op': 'exec', 'n': 1},
                    {'t': 'img', 'op': 'write', 'n': 1, 'size': img.MAX_BYTES + 1, 'sha256': digest},
                    {'t': 'img', 'op': 'write', 'n': 1, 'size': 10, 'sha256': 'XYZ'},
                    {'t': 'img', 'op': 'write', 'n': 1, 'size': 10, 'sha256': digest, 'mime': 'image/jpeg'},
                    {'t': 'img', 'op': 'read', 'n': True}, {'t': 'img', 'op': 'read', 'n': 0},
                    {'t': 'imgc', 'n': 1, 'i': 0, 'd': '!!!'}, {'t': 'imgc', 'n': 1, 'i': -1, 'd': ''},
                    {'t': 'imgc', 'n': 1, 'i': 0, 'd': 'A' * 30000}, {'t': 'imgc', 'n': 1, 'i': 0, 'd': '', 'display': ':0'}):
            with self.assertRaises((ValueError, base64.binascii.Error), msg=repr(bad)[:80]):
                img.parse_request(bad)

    def test_upload_order_bound_digest_and_idle(self):
        data = png(width=64, height=64)
        clock = [0.0]
        upload = img.Upload(5, len(data), hashlib.sha256(data).hexdigest(), now=lambda: clock[0])
        with self.assertRaises(ValueError):
            upload.add(1, data[:10])  # out of order
        with self.assertRaises(ValueError):
            upload.add(0, b'')
        with self.assertRaises(ValueError):
            upload.add(0, b'x' * (img.CHUNK + 1))
        self.assertTrue(upload.add(0, data))
        self.assertIsNone(upload.verified())
        wrong = img.Upload(6, len(data), '0' * 64)
        wrong.add(0, data)
        self.assertEqual(wrong.verified(), 'INVALID_IMAGE')
        over = img.Upload(7, 4, '0' * 64)
        with self.assertRaises(ValueError):
            over.add(0, b'12345')
        idle = img.Upload(8, 10, '0' * 64, now=lambda: clock[0])
        clock[0] += img.UPLOAD_IDLE + 1
        self.assertTrue(idle.expired())

    def test_download_chunks_reassemble(self):
        data = bytes(range(256)) * 300
        download = img.Download(9, data)
        header = download.header()
        self.assertEqual((header['size'], header['sha256']), (len(data), hashlib.sha256(data).hexdigest()))
        parts, last = [], -1
        while (m := download.next_chunk()) is not None:
            self.assertEqual(m['i'], last + 1); last = m['i']
            self.assertLessEqual(len(base64.b64decode(m['d'])), img.CHUNK)
            self.assertLess(len(repr(m)), 65536)  # fits the IPC line budget
            parts.append(base64.b64decode(m['d']))
        self.assertEqual(b''.join(parts), data)

    def test_fixed_program_no_file_or_shell(self):
        source = (Path(__file__).resolve().parents[1] / 'gui-runtime/image_clipboard.py').read_text()
        self.assertEqual(img.XCLIP, '/usr/bin/xclip')
        self.assertNotIn('shell=True', source)
        self.assertNotIn("'primary'", source)
        for forbidden in (r'os\.system', r'(?<![\w.])eval\(', r'(?<![\w.])exec\(', r'(?<![\w.])open\(', 'DISPLAY', 'tempfile', 'convert'):
            self.assertIsNone(re.search(forbidden, source), forbidden)



class SyncThread:
    def __init__(self, target): self.target, self.alive = target, False
    def start(self): self.target()
    def is_alive(self): return self.alive


class BridgeStateMachine(unittest.TestCase):
    def setUp(self):
        self.saved = img.read, img.write
        self.writes = []
        img.write = lambda data, env: (self.writes.append(bytes(data)), {'ok': True, 'code': 'OK'})[1]
        self.data = png(width=40, height=30)

    def tearDown(self):
        img.read, img.write = self.saved

    def upload(self, bridge, n, data, digest=None):
        replies = bridge.handle({'t': 'img', 'op': 'write', 'n': n, 'size': len(data), 'sha256': digest or hashlib.sha256(data).hexdigest()})
        for i, offset in enumerate(range(0, len(data), img.CHUNK)):
            replies += bridge.handle({'t': 'imgc', 'n': n, 'i': i, 'd': base64.b64encode(data[offset:offset + img.CHUNK]).decode()})
        return replies + bridge.poll()

    def test_write_success_and_digest_failure(self):
        bridge = img.Bridge({}, start=SyncThread)
        self.assertEqual(self.upload(bridge, 1, self.data), [{'t': 'img', 'n': 1, 'ok': True, 'code': 'OK'}])
        self.assertEqual(self.writes, [self.data])
        self.assertEqual(self.upload(bridge, 2, self.data, '0' * 64), [{'t': 'img', 'n': 2, 'ok': False, 'code': 'INVALID_IMAGE'}])
        self.assertEqual(len(self.writes), 1)  # never reaches X11
        self.assertFalse(bridge.busy())

    def test_read_streams_header_then_bounded_chunks(self):
        big = png(width=400, height=300, raw=bytes(range(256)) * 1876 + bytes(300 * 1601 - 256 * 1876), level=0)
        img.read = lambda env: ({'ok': True, 'code': 'OK'}, big)
        bridge = img.Bridge({}, start=SyncThread)
        self.assertEqual(bridge.handle({'t': 'img', 'op': 'read', 'n': 3}), [])
        room = [3]
        first = bridge.poll(lambda: room.__setitem__(0, room[0] - 1) or room[0] >= 0)
        self.assertEqual(first[0]['size'], len(big))
        self.assertEqual(len(first), 1 + 3)  # header + only as many chunks as there was room for
        rest = bridge.poll()
        parts = [base64.b64decode(m['d']) for m in first[1:] + rest]
        self.assertEqual(b''.join(parts), big)
        self.assertFalse(bridge.busy())

    def test_read_failure_codes_pass_through(self):
        img.read = lambda env: ({'ok': False, 'code': 'NON_IMAGE'}, None)
        bridge = img.Bridge({}, start=SyncThread)
        bridge.handle({'t': 'img', 'op': 'read', 'n': 4})
        self.assertEqual(bridge.poll(), [{'t': 'img', 'n': 4, 'ok': False, 'code': 'NON_IMAGE'}])

    def test_job_exception_reports_unavailable_not_crash(self):
        def boom(env): raise OSError('xclip missing')
        img.read = boom
        bridge = img.Bridge({}, start=SyncThread)
        bridge.handle({'t': 'img', 'op': 'read', 'n': 5})
        self.assertEqual(bridge.poll(), [{'t': 'img', 'n': 5, 'ok': False, 'code': 'UNAVAILABLE'}])

    def test_busy_and_late_chunks_after_busy_or_cancel(self):
        bridge = img.Bridge({}, start=SyncThread, text_busy=lambda: True)
        self.assertEqual(bridge.handle({'t': 'img', 'op': 'write', 'n': 6, 'size': 10, 'sha256': 'a' * 64}), [{'t': 'img', 'n': 6, 'ok': False, 'code': 'BUSY'}])
        self.assertEqual(bridge.handle({'t': 'imgc', 'n': 6, 'i': 0, 'd': 'AAAA'}), [])  # dropped, not a protocol error
        bridge = img.Bridge({}, start=SyncThread)
        bridge.handle({'t': 'img', 'op': 'write', 'n': 7, 'size': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()})
        self.assertTrue(bridge.busy())
        bridge.handle({'t': 'img', 'op': 'cancel', 'n': 7})
        self.assertFalse(bridge.busy())
        self.assertEqual(bridge.handle({'t': 'imgc', 'n': 7, 'i': 0, 'd': 'AAAA'}), [])
        with self.assertRaises(ValueError):
            bridge.handle({'t': 'imgc', 'n': 99, 'i': 0, 'd': 'AAAA'})  # never announced: protocol violation
        self.assertEqual(self.writes, [])

    def test_cancelled_read_result_is_dropped(self):
        class Deferred(SyncThread):
            def start(self): self.alive = True
        threads = []
        img.read = lambda env: ({'ok': True, 'code': 'OK'}, self.data)
        bridge = img.Bridge({}, start=lambda target: threads.append(Deferred(target)) or threads[-1])
        bridge.handle({'t': 'img', 'op': 'read', 'n': 8})
        bridge.handle({'t': 'img', 'op': 'cancel', 'n': 8})
        threads[0].target(); threads[0].alive = False  # the job finishes after the cancel
        self.assertEqual(bridge.poll(), [])
        self.assertFalse(bridge.busy())

    def test_idle_upload_times_out(self):
        clock = [0.0]
        saved = img.Upload
        img.Upload = lambda n, size, digest: saved(n, size, digest, now=lambda: clock[0])
        try:
            bridge = img.Bridge({}, start=SyncThread)
            bridge.handle({'t': 'img', 'op': 'write', 'n': 9, 'size': 100, 'sha256': 'a' * 64})
            clock[0] += img.UPLOAD_IDLE + 1
            self.assertEqual(bridge.poll(), [{'t': 'img', 'n': 9, 'ok': False, 'code': 'TIMEOUT'}])
            self.assertFalse(bridge.busy())
        finally:
            img.Upload = saved


if __name__ == '__main__':
    unittest.main()
