"""Deterministic rules of the wdgui text-only clipboard (no X11 required)."""
import importlib.util
import re
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('text_clipboard', Path(__file__).resolve().parents[1] / 'gui-runtime/text_clipboard.py')
clip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clip)


class TextRules(unittest.TestCase):
    def test_plain_unicode_accepted(self):
        for text in ('Hello 123', '简体中文', '繁體中文', '中English，。！', 'a\tb\nc\r\nd', '😀👍🏽🇨🇳 𠮷',
                     '"\'\\<tag> &amp; $(touch /x) `id`', '/opt/webdesktop/app/.env', 'file:///etc/passwd'):
            self.assertEqual(clip.decode(text.encode()), (None, text))

    def test_boundary_and_oversize(self):
        exact = ('汉' * 5461).encode() + b'a'
        self.assertEqual(len(exact), clip.MAX_BYTES)
        self.assertIsNone(clip.decode(exact)[0])
        self.assertEqual(clip.decode(exact + b'a')[0], 'TOO_LARGE')

    def test_invalid_and_control(self):
        self.assertEqual(clip.decode(b'\xff\xfe')[0], 'INVALID_UTF8')
        self.assertEqual(clip.decode('\ud800'.encode('utf-8', 'surrogatepass'))[0], 'INVALID_UTF8')
        for bad in ('a\x00b', 'a\x1bb', 'a\x07', 'a\x7f', 'a\x85b'):
            self.assertEqual(clip.decode(bad.encode())[0], 'CONTROL_CHARACTERS')
        self.assertEqual(clip.decode(b'')[0], 'EMPTY')

    def test_targets(self):
        self.assertIsNone(clip.target_problem(['TARGETS', 'UTF8_STRING', 'text/html', 'STRING']))
        for targets in (['UTF8_STRING', 'text/uri-list'], ['x-special/gnome-copied-files', 'UTF8_STRING'],
                        ['image/png', 'UTF8_STRING'], ['text/html'], ['application/x-kde-cutselection', 'UTF8_STRING']):
            self.assertEqual(clip.target_problem(targets), 'NON_TEXT', targets)
        self.assertEqual(clip.target_problem([]), 'EMPTY')

    def test_protocol_is_exact(self):
        self.assertEqual(clip.parse_request({'t': 'clip', 'op': 'read', 'n': 1}), ('read', 1, None))
        self.assertEqual(clip.parse_request({'t': 'clip', 'op': 'write', 'n': 2, 'text': '你好'}), ('write', 2, '你好'.encode()))
        for bad in ({'t': 'clip', 'op': 'read', 'n': 1, 'path': '/etc/passwd'}, {'t': 'clip', 'op': 'exec', 'n': 1},
                    {'t': 'clip', 'op': 'read', 'n': 0}, {'t': 'clip', 'op': 'read', 'n': True}, {'t': 'clip', 'op': 'write', 'n': 1, 'text': 5},
                    {'t': 'clip', 'op': 'write', 'n': 1}, {'t': 'clip', 'op': 'read', 'n': 2**31}, {'t': 'd', 'op': 'read', 'n': 1},
                    {'t': 'clip', 'op': 'write', 'n': 1, 'text': 'x' * (clip.MAX_BYTES + 1)}, ['clip']):
            with self.assertRaises(ValueError, msg=repr(bad)[:80]):
                clip.parse_request(bad)

    def test_fixed_program_and_selection(self):
        source = (Path(__file__).resolve().parents[1] / 'gui-runtime/text_clipboard.py').read_text()
        self.assertEqual(clip.XCLIP, '/usr/bin/xclip')
        self.assertNotIn('shell=True', source)
        self.assertNotIn("'primary'", source)
        for forbidden in (r'os\.system', r'(?<![\w.])eval\(', r'(?<![\w.])exec\(', r'(?<![\w.])open\(', 'DISPLAY'):
            self.assertIsNone(re.search(forbidden, source), forbidden)


if __name__ == '__main__':
    unittest.main()
