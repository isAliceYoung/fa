"""Real TCP WebSocket handshakes against production-built Next and PostgreSQL."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
import sys
from linux_guest_layer import GuestLayerTests as Fixture, ROOT

PATHS = ('/api/term/ws', '/api/gui/ws', '/api/browser/ws')

class WebSocketRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        Fixture.tearDownClass()

    def handshake(self, path, cookie=None, session=None):
        if session is not None:
            path += '?access=' + session
        request = ('GET ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1:3000\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ' + base64.b64encode(os.urandom(16)).decode() + '\r\n')
        if cookie:
            request += 'Cookie: ' + cookie + '\r\n'
        with socket.create_connection(('127.0.0.1', 3000), timeout=8) as sock:
            sock.sendall((request + '\r\n').encode())
            data = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                self.assertLess(len(data), 16384)
        self.assertNotIn(b'101 Switching', data)
        self.assertNotIn(b'Sec-WebSocket-Accept:', data)
        return int(data.split(b' ', 2)[1])

    def all_paths(self, expected, cookie=None, session=None):
        for path in PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.handshake(path, cookie, session), expected)
        self.assertFalse((Path(Fixture.service.pw_dir) / 'forbidden-internal-upgrade').exists())

    def test_no_authentication(self):
        self.all_paths(401)

    def test_invalid_cookie(self):
        self.all_paths(401, 'wd_session=invalid', Fixture.session)

    def test_wrong_page_session(self):
        self.all_paths(401, Fixture.cookie, 'wrong-page')

    def test_revoked_session(self):
        old_cookie, old_session = Fixture.cookie, Fixture.session
        status, body, headers = Fixture.web('/api/auth/login', 'POST', {'password': Fixture.cfg['DESKTOP_PASSWORD']}, auth=False)
        self.assertEqual(status, 200)
        Fixture.cookie = headers.get('Set-Cookie').split(';', 1)[0]
        Fixture.session = json.loads(body)['sessionId']
        self.all_paths(401, old_cookie, old_session)
        self.all_paths(503, Fixture.cookie, Fixture.session)

    def test_valid_session_still_denied_after_delayed_duplicate_listeners(self):
        time.sleep(1.2)
        for _ in range(3):
            self.all_paths(503, Fixture.cookie, Fixture.session)

    def test_database_unavailable_closes_safely_and_recovers(self):
        subprocess.run(['pg_ctlcluster', '15', 'main', 'stop', '-m', 'fast'], check=True)
        try:
            self.all_paths(503, Fixture.cookie, Fixture.session)
            self.assertIsNone(Fixture.next.poll())
        finally:
            subprocess.run(['pg_ctlcluster', '15', 'main', 'start'], check=True)
        self.all_paths(503, Fixture.cookie, Fixture.session)

    def test_restart_preserves_denial(self):
        Fixture.next.terminate()
        Fixture.next.wait(timeout=15)
        node = subprocess.check_output(['which', 'node'], text=True).strip()
        Fixture.next = subprocess.Popen([node, str(ROOT / 'tests/ws_runtime_entry.mjs')], cwd=ROOT,
            user=Fixture.service.pw_uid, group=Fixture.service.pw_gid, extra_groups=[],
            env={'PATH': str(Path(node).parent) + ':/usr/bin:/bin', 'HOME': Fixture.service.pw_dir, 'NODE_ENV': 'production', 'BIND_HOST': '127.0.0.1', 'NEXT_TELEMETRY_DISABLED': '1'},
            stdout=Fixture.next_log, stderr=subprocess.STDOUT)
        for _ in range(40):
            try:
                if Fixture.web('/api/health', auth=False)[0] == 200:
                    break
            except OSError:
                time.sleep(.25)
        self.all_paths(503, Fixture.cookie, Fixture.session)
        self.all_paths(401)

if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WebSocketRuntimeTests))
    summary = dict(total=result.testsRun, passed=max(0, result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)), failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped))
    Path('/tmp/webdesktop-ws-runtime.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
