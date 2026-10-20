#!/usr/bin/env python3
"""3A actual Debian users + inherited Unix socket + real authenticated Next/PG.

Run only in the disposable Debian CI job after setup/build. No mock auth/UIDs,
no production secrets, and no production or existing-install migration.
"""
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import resource
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOCKET = Path("/run/webdesktop-guest/guest.sock")
CLIENT = '''import socket,http.client,json,sys,base64
class IPCConnection(http.client.HTTPConnection):
 def putrequest(self,method,url,**kwargs):
  kwargs["skip_accept_encoding"]=True
  return super().putrequest(method,url,**kwargs)
request=json.loads(sys.stdin.read())
c=IPCConnection("localhost",timeout=12)
c.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
c.sock.connect("/run/webdesktop-guest/guest.sock")
c.request(request["method"],request["path"],body=request.get("body"),headers=request.get("headers",{}))
r=c.getresponse()
print(json.dumps({"status":r.status,"body":base64.b64encode(r.read()).decode()}))
'''


class GuestLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeError("Disposable root Debian 12 environment required; not a mock test")
        cls.service, cls.guest = pwd.getpwnam("wdsvc"), pwd.getpwnam("wdguest")
        cls.home = Path(cls.guest.pw_dir)
        cls.temp = tempfile.TemporaryDirectory(prefix="wd-3a-test-only-", dir="/root")
        cls.root = Path(cls.temp.name)
        cls.root_file = cls.root / "deploy_key.test"
        cls.root_file.write_text("TEST_ROOT_SECRET_NEVER_DISCLOSED")
        cls.root_file.chmod(0o600)
        cls.service_file = Path(cls.service.pw_dir) / "postgres-credentials.test"
        cls.service_file.write_text("TEST_SERVICE_SECRET_NEVER_DISCLOSED")
        os.chown(cls.service_file, cls.service.pw_uid, cls.service.pw_gid)
        cls.service_file.chmod(0o600)
        cls.agent_log = open(cls.root / "agent.log", "wb")
        cls.agent = None
        cls.listener = None
        cls.start_agent()
        cls.next_log = open(cls.root / "next.log", "wb")
        node = subprocess.check_output(["which", "node"], text=True).strip()
        cls.next = subprocess.Popen([node, str(ROOT / "tests/ws_runtime_entry.mjs")],
                                    cwd=ROOT, user=cls.service.pw_uid, group=cls.service.pw_gid, extra_groups=[],
                                    env={"PATH": str(Path(node).parent) + ":/usr/bin:/bin", "HOME": cls.service.pw_dir, "NODE_ENV": "production", "NEXT_TELEMETRY_DISABLED": "1", "WD_PUBLIC_ORIGINS":"http://127.0.0.1:3000,https://desktop.example"},
                                    stdout=cls.next_log, stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:3000/api/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.HTTPError):
                time.sleep(1)
        else:
            raise RuntimeError("Real Next.js/database readiness failed; inspect protected CI logs")
        cls.cfg = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if "=" in line)
        status, body, headers = cls.web("/api/auth/login", "POST", {"password": cls.cfg["DESKTOP_PASSWORD"]}, auth=False)
        if status != 200:
            raise RuntimeError("Real database desktop login failed")
        cls.cookie = headers.get("Set-Cookie").split(";", 1)[0]
        cls.session = json.loads(body)["sessionId"]

    @classmethod
    def start_agent(cls):
        if SOCKET.exists():
            raise RuntimeError("Existing IPC socket is not a disposable fixture; refusing replacement")
        cls.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cls.listener.bind(str(SOCKET))
        os.chown(SOCKET, 0, cls.service.pw_gid)
        SOCKET.chmod(0o660)
        cls.listener.listen(16)
        fd = cls.listener.fileno()
        # Disposable root instrumentation mirrors systemd's norbind HOME view;
        # it drops UID/GID before exec, and the agent validates all saved IDs.
        launcher='mount --bind /home/wdguest /home/wdguest; exec setpriv --reuid=wdguest --regid=wdguest --clear-groups /usr/bin/python3 -I -B /usr/local/lib/webdesktop-guest/agent.py --listen-fd "$1"'
        cls.agent = subprocess.Popen(['unshare','--mount','--propagation','private','/bin/sh','-ec',launcher,'lab-agent',str(fd)],
                                     pass_fds=(fd,), cwd=cls.home,
                                     env={"HOME": str(cls.home), "USER": "wdguest", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, stdout=cls.agent_log, stderr=subprocess.STDOUT)
        last_status = None
        for _ in range(40):
            if cls.agent.poll() is not None:
                raise RuntimeError("Actual guest process failed startup: " + Path(cls.agent_log.name).read_text()[-3000:])
            try:
                last_status = cls.ipc("/v1/info")[0]
                if last_status == 200:
                    return
            except (subprocess.SubprocessError, ValueError):
                pass
            time.sleep(0.1)
        raise RuntimeError("Guest service readiness failed; IPC status={}".format(last_status))

    @classmethod
    def stop_agent(cls):
        if cls.agent and cls.agent.poll() is None:
            cls.agent.terminate()
            cls.agent.wait(timeout=10)
        if cls.listener:
            cls.listener.close()
        SOCKET.unlink(missing_ok=True)  # Only this test's root-created socket.

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "next", None):
            cls.next.terminate()
            cls.next.wait(timeout=15)
        cls.stop_agent()
        cls.agent_log.close()
        cls.next_log.close()
        cls.service_file.unlink(missing_ok=True)
        cls.temp.cleanup()

    @classmethod
    def ipc(cls, path, method="GET", body=None, headers=None, uid=None):
        payload = json.dumps(body) if body is not None else None
        request = {"path": path, "method": method, "body": payload, "headers": headers or ({"Content-Type": "application/json"} if payload else {})}
        user = cls.service if uid is None else uid
        result = subprocess.run(["/usr/bin/python3", "-I", "-c", CLIENT], input=json.dumps(request), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                                user=user.pw_uid, group=user.pw_gid, extra_groups=[], env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, "IPC test client")
        value = json.loads(result.stdout)
        return value["status"], base64.b64decode(value["body"])

    @classmethod
    def web(cls, path, method="GET", body=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers.update({"Cookie": cls.cookie, "X-Desktop-Session": cls.session})
        request = urllib.request.Request("http://127.0.0.1:3000" + path, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_distinct_actual_uids_and_groups(self):
        self.assertNotEqual(self.service.pw_uid, self.guest.pw_uid)
        self.assertNotEqual(self.service.pw_gid, self.guest.pw_gid)
        self.assertNotEqual(self.guest.pw_uid, 0)
        for account in (self.service, self.guest):
            groups = subprocess.check_output(["id", "-G", account.pw_name], text=True).split()
            self.assertEqual(groups, [str(account.pw_gid)])
        status = Path("/proc/{}/status".format(self.next.pid)).read_text()
        self.assertIn("Uid:\t{}\t".format(self.service.pw_uid), status)

    def test_actual_agent_uid_and_environment_have_no_service_credentials(self):
        status = Path("/proc/{}/status".format(self.agent.pid)).read_text()
        self.assertIn("Uid:\t{}\t".format(self.guest.pw_uid), status)
        self.assertIn("NoNewPrivs:\t1", status)
        self.assertIn("CapEff:\t0000000000000000", status)
        environment = subprocess.check_output(["/usr/bin/python3", "-I", "-c", "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())", "/proc/{}/environ".format(self.agent.pid)],
                                              user=self.guest.pw_uid, group=self.guest.pw_gid, extra_groups=[])
        for secret in (b"DATABASE_URL=", b"AUTH_KEY=", b"ADMIN_PASSWORD=", b"DESKTOP_PASSWORD=", b"SSH_AUTH_SOCK=", b"Cookie"):
            self.assertNotIn(secret, environment)

    def test_kernel_denies_guest_read_and_write_of_root_and_service_and_real_source(self):
        for path in (self.root_file, self.service_file, ROOT / "src/lib/auth.ts", ROOT / ".env", ROOT / ".git/HEAD"):
            before = path.read_bytes()
            for operation in ("read", "write", "copy"):
                code = {"read": "from pathlib import Path; p=Path(__import__('sys').argv[1]); p.read_bytes()",
                        "write": "from pathlib import Path; p=Path(__import__('sys').argv[1]); p.write_bytes(b'overwrite')",
                        "copy": "import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])"}[operation]
                result = subprocess.run(["/usr/bin/python3", "-I", "-c", code, str(path), str(self.home / ("denied-copy-" + uuid.uuid4().hex))], user=self.guest.pw_uid, group=self.guest.pw_gid,
                                        extra_groups=[], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"PermissionError", result.stderr)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(before).digest())

    def test_authenticated_file_create_write_list_and_read(self):
        name = "file-" + uuid.uuid4().hex + ".txt"
        status, data, _ = self.web("/api/sys/fs", "POST", {"dir": "~/Documents", "name": name, "kind": "file", "content": "hello guest"})
        self.assertEqual(status, 200, data[:300] + Path(self.agent_log.name).read_bytes()[-2000:])
        path = json.loads(data)["path"]
        self.assertEqual(self.web("/api/sys/fs", "PUT", {"path": path, "content": "updated guest"})[0], 200)
        status, data, _ = self.web("/api/sys/fs?read=1&path=" + urllib.parse.quote(path))
        self.assertEqual(status, 200, data[:300] + Path(self.agent_log.name).read_bytes()[-2000:])
        self.assertEqual(json.loads(data)["content"], "updated guest")
        self.assertEqual(Path(path).stat().st_uid, self.guest.pw_uid)
        self.assertEqual(self.web("/api/sys/fs?path=~/Documents")[0], 200)

    def test_absolute_paths_and_traversal_are_rejected(self):
        for raw in (str(self.root_file), str(self.service_file), str(ROOT / ".env"), "../root", "~/Documents/../../root", "/etc/postgresql/15/main/pg_hba.conf"):
            for endpoint in ("/api/sys/fs?read=1&path=", "/api/sys/raw?path="):
                status, data, _ = self.web(endpoint + urllib.parse.quote(raw, safe=""))
                self.assertEqual(status, 403)
                self.assertNotIn(b"TEST_ROOT_SECRET", data)

    def test_symlinks_and_hardlinks_cannot_export_protected_data(self):
        link = self.home / ("link-" + uuid.uuid4().hex)
        link.symlink_to(self.service_file)
        hard = self.home / ("hard-" + uuid.uuid4().hex)
        with self.assertRaises(OSError) as error:
            os.link(self.service_file, hard)
        self.assertEqual(error.exception.errno,18) # Separate bounded filesystem.
        protected=self.home/('protected-root-'+uuid.uuid4().hex)
        protected.mkdir(mode=0o700)
        secret=protected/'secret.test'
        secret.write_text('TEST_SAME_FILESYSTEM_ROOT_SECRET')
        secret.chmod(0o600)
        os.link(secret,hard)
        for path in (link, hard):
            self.assertEqual(self.web("/api/sys/raw?path=" + urllib.parse.quote(str(path)))[0], 403)
            result = subprocess.run(["/bin/cat", str(path)], user=self.guest.pw_uid, group=self.guest.pw_gid, extra_groups=[], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_hardlink_to_guest_file_is_also_rejected(self):
        path = self.home / ("single-" + uuid.uuid4().hex)
        path.write_text("guest data")
        os.chown(path, self.guest.pw_uid, self.guest.pw_gid)
        other = self.home / ("double-" + uuid.uuid4().hex)
        os.link(path, other)
        self.assertEqual(self.web("/api/sys/raw?path=" + urllib.parse.quote(str(other)))[0], 403)

    def test_unauthenticated_and_cookie_only_requests_do_not_execute(self):
        for path in ("/api/sys/fs?path=~", "/api/sys/raw?path=~/Documents", "/api/sys/info?lite=1"):
            self.assertEqual(self.web(path, auth=False)[0], 401)
        request = urllib.request.Request("http://127.0.0.1:3000/api/sys/fs?path=~", headers={"Cookie": self.cookie})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 401)

    def test_guest_cannot_connect_to_service_socket(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.ipc("/v1/info", uid=self.guest)

    def test_ipc_rejects_cookie_headers_and_unknown_operations(self):
        self.assertEqual(self.ipc("/v1/info", headers={"Cookie": "TEST_COOKIE"})[0], 400)
        self.assertEqual(self.ipc("/v1/exec")[0], 404)
        self.assertEqual(self.ipc("/v1/info?access=TEST_SESSION")[0], 400)

    def test_streamed_file_keeps_contents_and_does_not_accept_guest_cookies(self):
        path = self.home / ("stream-" + uuid.uuid4().hex + ".bin")
        data = os.urandom(1024 * 1024)
        path.write_bytes(data)
        os.chown(path, self.guest.pw_uid, self.guest.pw_gid)
        status, actual, headers = self.web("/api/sys/raw?path=" + urllib.parse.quote(str(path)))
        self.assertEqual(status, 200)
        self.assertEqual(actual, data)
        self.assertIsNone(headers.get("Set-Cookie"))
        self.assertEqual(headers.get("Content-Security-Policy"), "sandbox")

    def test_database_cloud_and_admin_auth_still_work(self):
        self.assertEqual(self.web("/api/health", auth=False)[0], 200)
        self.assertEqual(self.web("/api/fs")[0], 200)
        path = "/api/ops/" + self.cfg["ADMIN_PATH"] + "/state"
        self.assertEqual(self.web(path)[0], 401)  # Desktop auth grants no admin role.
        status, _, _ = self.web("/api/ops/" + self.cfg["ADMIN_PATH"] + "/login", "POST", {"username": self.cfg["ADMIN_USER"], "password": self.cfg["ADMIN_PASSWORD"]}, auth=False)
        self.assertEqual(status, 200)

    def test_unmigrated_execution_routes_fail_closed(self):
        for path, method, body in (("/api/gui", "POST", {"exec": "id"}), ("/api/browser", "POST", {}), ("/api/sys/kill", "POST", {"pid": self.next.pid, "signal": "SIGKILL"})):
            status, data, _ = self.web(path, method, body)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(data)["code"], "GUEST_FEATURE_PENDING")
        self.assertIsNone(self.next.poll())

    def test_guest_cannot_signal_backend(self):
        result = subprocess.run(["/bin/kill", "-STOP", str(self.next.pid)], user=self.guest.pw_uid, group=self.guest.pw_gid, extra_groups=[], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.next.poll())

    def test_service_offline_fails_without_high_privilege_fallback(self):
        self.stop_agent()
        try:
            status, data, _ = self.web("/api/sys/raw?path=" + urllib.parse.quote(str(self.service_file)))
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(data)["code"], "GUEST_UNAVAILABLE")
            self.assertNotIn(b"TEST_SERVICE_SECRET", data)
        finally:
            self.start_agent()

    def test_resource_snapshot(self):
        def rss(pid):
            text = Path("/proc/{}/status".format(pid)).read_text()
            return next(int(line.split()[1]) for line in text.splitlines() if line.startswith("VmRSS:"))
        self.__class__.resources = {"measurement": "CI idle RSS KiB", "guest": rss(self.agent.pid), "next": rss(self.next.pid), "guest_runtime_bytes": sum(path.stat().st_size for path in Path('/usr/local/lib/webdesktop-guest').glob('*.py'))}
        print(json.dumps(self.resources), flush=True)
        self.assertLess(rss(self.agent.pid), 96 * 1024)

    def test_timeout_is_explicit_and_does_not_fallback(self):
        os.kill(self.agent.pid, signal.SIGSTOP)
        try:
            status, data, _ = self.web("/api/sys/info?lite=1")
            self.assertEqual(status, 504)
            self.assertEqual(json.loads(data)["code"], "GUEST_UNAVAILABLE")
        finally:
            os.kill(self.agent.pid, signal.SIGCONT)

    def test_midstream_guest_exit_aborts_instead_of_reopening_locally(self):
        path = self.home / ("interrupted-" + uuid.uuid4().hex + ".bin")
        with path.open("wb") as stream:
            for _ in range(32):
                stream.write(os.urandom(1024 * 1024))
        os.chown(path, self.guest.pw_uid, self.guest.pw_gid)
        connection = http.client.HTTPConnection("127.0.0.1", 3000, timeout=15)
        connection.request("GET", "/api/sys/raw?path=" + urllib.parse.quote(str(path)), headers={"Cookie": self.cookie, "X-Desktop-Session": self.session})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read(1024)
        self.stop_agent()
        try:
            with self.assertRaises((http.client.IncompleteRead, ConnectionError)):
                response.read()
        finally:
            connection.close()
            self.start_agent()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GuestLayerTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {"total": result.testsRun, "passed": max(0, result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)), "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped), "resources": getattr(GuestLayerTests, "resources", None)}
    Path("/tmp/webdesktop-3a-results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
