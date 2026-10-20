"""Real TCP WebSocket handshakes against production-built Next and PostgreSQL."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import pwd
import signal
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

    def handshake(self, path, cookie=None, session=None, origin="http://127.0.0.1:3000", host="127.0.0.1:3000", extra=""):
        if session is not None:
            path += '?access=' + session
        request = ('GET ' + path + ' HTTP/1.1\r\nHost: ' + host + '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ' + base64.b64encode(os.urandom(16)).decode() + '\r\n')
        if origin is not None:
            request += "Origin: " + origin + "\r\n"
        request += extra
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

    def test_real_tls_reverse_proxy_preserves_host_and_authentication(self):
        cert,key=Fixture.root/'proxy.crt',Fixture.root/'proxy.key'
        subprocess.run(['openssl','req','-x509','-newkey','ed25519','-nodes','-days','1','-subj','/CN=proxy.example','-addext','subjectAltName=DNS:proxy.example','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        result=subprocess.run(['node',str(ROOT/'tests/ws_proxy_runtime.mjs')],input=json.dumps(dict(cookie=Fixture.cookie,session=Fixture.session,cert=str(cert),key=str(key))),text=True,capture_output=True,timeout=30,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_real_host_origin_and_preserved_host_reverse_proxy_policy(self):
        for path in PATHS:
            for origin,host in [(None,'127.0.0.1:3000'),('null','127.0.0.1:3000'),('https://evil.example','evil.example'),('http://127.0.0.1:3001','127.0.0.1:3001'),('http://127.0.0.1:3000','evil.example'),('https://desktop.example','127.0.0.1:3000')]:
                self.assertEqual(self.handshake(path,Fixture.cookie,Fixture.session,origin,host),403)
            self.assertEqual(self.handshake(path,Fixture.cookie,Fixture.session,'https://desktop.example','desktop.example'),503)
            self.assertEqual(self.handshake(path,None,None,'https://desktop.example','desktop.example'),401)
            self.assertEqual(self.handshake(path,Fixture.cookie,'bad-page','https://desktop.example','desktop.example'),401)
            self.assertEqual(self.handshake(path,Fixture.cookie,Fixture.session,'https://evil.example','evil.example','X-Forwarded-Host: desktop.example\r\nX-Forwarded-Proto: https\r\n'),403)
            self.assertEqual(self.handshake(path,Fixture.cookie,Fixture.session,extra='Origin: https://evil.example\r\n'),403)
            self.assertIn(self.handshake(path,Fixture.cookie,Fixture.session,extra='Host: evil.example\r\n'),(400,403))

    def test_default_real_listener_is_ipv4_loopback_and_internal_is_private_unix(self):
        state = json.loads((Path(Fixture.service.pw_dir) / 'gateway-runtime.json').read_text())
        self.assertEqual(state['public']['address'], '127.0.0.1')
        self.assertEqual(state['public']['family'], 'IPv4')
        internal = state['internal']
        self.assertIsInstance(internal, str)
        self.assertEqual(Path(internal).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(internal).parent.stat().st_mode & 0o777, 0o700)
        listeners = subprocess.check_output(['ss', '-ltnp'], text=True)
        own = [line for line in listeners.splitlines() if 'pid={},'.format(Fixture.next.pid) in line]
        self.assertEqual(len(own), 1, own)
        self.assertIn('127.0.0.1:3000', own[0])

    def test_guest_and_nobody_cannot_connect_direct_internal_http_or_upgrade(self):
        state = json.loads((Path(Fixture.service.pw_dir) / 'gateway-runtime.json').read_text())
        for account in (Fixture.guest, pwd.getpwnam('nobody')):
            for upgrade in (False, True):
                code = 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(2);s.connect(sys.argv[1]);s.sendall(b"GET /api/health HTTP/1.1\\r\\nHost: localhost\\r\\n' + ('Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n' if upgrade else '') + '\\r\\n");s.recv(1024)'
                r = subprocess.run(['/usr/bin/python3', '-I', '-c', code, state['internal']],
                    user=account.pw_uid, group=account.pw_gid, extra_groups=[], capture_output=True, timeout=4)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(b'PermissionError', r.stderr)

    def test_same_uid_attack_baseline_requires_distinct_terminal_identity(self):
        # This is an exploit demonstration against the accepted file-only model,
        # not a claim that same-UID interactive Bash is safe.
        probe = Fixture.home / '.wd-state/security-review-probe'
        probe.write_text('private transaction test')
        os.chown(probe, Fixture.guest.pw_uid, Fixture.guest.pw_gid)
        probe.chmod(0o600)
        code = '''import os,signal,sys,json,pathlib,ctypes
pid=int(sys.argv[1]); p=pathlib.Path(sys.argv[2]); result={}
result['private_state_readable']=p.read_text()=='private transaction test'
p.write_text('tampered'); result['private_state_writable']=True
os.kill(pid,signal.SIGSTOP); result['signal_stop_allowed']=True
try:
 result['proc_fd_readable']=bool(list(pathlib.Path('/proc/%s/fd'%pid).iterdir()))
 result['proc_environment_readable']=pathlib.Path('/proc/%s/environ'%pid).read_bytes() is not None
 libc=ctypes.CDLL(None,use_errno=True)
 result['ptrace_attach_result']=libc.ptrace(16,pid,None,None)
 result['ptrace_errno']=ctypes.get_errno()
 if result['ptrace_attach_result']==0:
  os.waitpid(pid,os.WUNTRACED);libc.ptrace(17,pid,None,None)
finally: os.kill(pid,signal.SIGCONT)
print(json.dumps(result))
'''
        try:
            r = subprocess.run(['/usr/bin/python3', '-I', '-c', code, str(Fixture.agent.pid), str(probe)],
                user=Fixture.guest.pw_uid, group=Fixture.guest.pw_gid, extra_groups=[], capture_output=True, text=True, timeout=5)
            self.assertEqual(r.returncode, 0, r.stderr)
            evidence = json.loads(r.stdout)
            self.assertTrue(evidence['private_state_readable'])
            self.assertTrue(evidence['private_state_writable'])
            self.assertTrue(evidence['signal_stop_allowed'])
            Path('/tmp/webdesktop-same-uid-baseline.json').write_text(json.dumps(evidence, indent=2))
            self.assertEqual(Fixture.ipc('/v1/info')[0], 200)
        finally:
            os.kill(Fixture.agent.pid, signal.SIGCONT)
            probe.unlink(missing_ok=True)

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
        Fixture.next.kill()  # Leave the private UDS behind to exercise crash recovery.
        Fixture.next.wait(timeout=15)
        node = subprocess.check_output(['which', 'node'], text=True).strip()
        Fixture.next = subprocess.Popen([node, str(ROOT / 'tests/ws_runtime_entry.mjs')], cwd=ROOT,
            user=Fixture.service.pw_uid, group=Fixture.service.pw_gid, extra_groups=[],
            env={'PATH': str(Path(node).parent) + ':/usr/bin:/bin', 'HOME': Fixture.service.pw_dir, 'NODE_ENV': 'production', 'BIND_HOST': '127.0.0.1', 'NEXT_TELEMETRY_DISABLED': '1', 'WD_PUBLIC_ORIGINS':'http://127.0.0.1:3000,https://desktop.example,https://proxy.example:3443'},
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
