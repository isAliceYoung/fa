import importlib.util
from pathlib import Path
import struct
import unittest

spec=importlib.util.spec_from_file_location('rfb_input',Path(__file__).resolve().parents[1]/'gui-runtime/rfb_input.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
HELLO=b'RFB 003.008\n\x01\x01'
def resize(w,h,screens=1):
    return struct.pack('>BBHHBBIHHHHI',251,0,w,h,screens,0,0,0,0,w,h,0)


class RfbBoundaryTests(unittest.TestCase):
    def ready(self):
        p=r.ClientInput();self.assertEqual(p.feed(HELLO),HELLO);return p

    def test_fragmented_handshake_and_key_events(self):
        p=r.ClientInput();data=HELLO+bytes([4,1,0,0,0,0,0,65])+bytes([255,0,0,1,0,0,0,65,0,0,0,30])
        self.assertEqual(b''.join(p.feed(bytes([b])) for b in data),data)

    def test_fragmented_resize(self):
        p=self.ready();data=resize(1400,900)
        self.assertEqual(b''.join(p.feed(bytes([b]),now=1) for b in data),data)

    def test_size_bound_with_valid_frame(self):
        for w,h in ((0,800),(1601,1000),(640,1001),(639,480)):
            with self.assertRaises(ValueError):self.ready().feed(resize(w,h))

    def test_multiple_screens_rejected(self):
        with self.assertRaises(ValueError):self.ready().feed(resize(1000,700,2))

    def test_screen_dimensions_cannot_disagree(self):
        b=bytearray(resize(1000,700));b[16:18]=struct.pack('>H',1500)
        with self.assertRaises(ValueError):self.ready().feed(b)

    def test_resize_rate_and_recovery(self):
        p=self.ready();p.feed(resize(1000,700),now=1)
        with self.assertRaises(ValueError):p.feed(resize(1100,700),now=1.1)
        p=self.ready();p.feed(resize(1000,700),now=1);self.assertEqual(p.feed(resize(1100,700),now=1.4),resize(1100,700))

    def test_clipboard_data_never_reaches_xvnc(self):
        p=self.ready();self.assertEqual(p.feed(struct.pack('>BBBBI',6,0,0,0,4)+b'test'),b'')
        with self.assertRaises(ValueError):p.feed(struct.pack('>BBBBI',6,0,0,0,900000))

    def test_encoding_and_pixel_format_limits(self):
        with self.assertRaises(ValueError):self.ready().feed(struct.pack('>BBH',2,0,257))
        with self.assertRaises(ValueError):self.ready().feed(bytes([0,0,0,0,64,64])+bytes(14))

    def test_unknown_message_and_unbounded_input(self):
        with self.assertRaises(ValueError):self.ready().feed(b'\x7f')
        with self.assertRaises(ValueError):self.ready().feed(bytes(65537))


if __name__=='__main__':unittest.main()
