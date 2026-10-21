"""Real production Next/PostgreSQL status admission tests; disposable Debian only."""
import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import time
import unittest
import urllib.parse
import uuid
from linux_guest_layer import GuestLayerTests as Fixture

PATHS = ('/api/resources', '/api/gui/status?sid='+'a'*24)


class StatusRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixture.setUpClass()
        cls.dbname = urllib.parse.unquote(urllib.parse.urlparse(Fixture.cfg['DATABASE_URL']).path.lstrip('/'))

    @classmethod
    def tearDownClass(cls):
        Fixture.tearDownClass()

    def setUp(self):
        # Deliberate window separation, not an application readiness sleep.
        time.sleep(1.05)
        self.login()

    def login(self):
        code, body, headers = Fixture.web('/api/auth/login', 'POST', {'password': Fixture.cfg['DESKTOP_PASSWORD']}, auth=False)
        self.assertEqual(code, 200)
        Fixture.cookie = headers.get('Set-Cookie').split(';', 1)[0]
        Fixture.session = json.loads(body)['sessionId']
        return Fixture.cookie, Fixture.session

    def request(self, path, pair=None, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', 3000, timeout=5)
        headers = {'Connection': 'close'}
        if pair:
            if pair[0] is not None: headers['Cookie'] = pair[0]
            if pair[1] is not None: headers['X-Desktop-Session'] = pair[1]
        headers.update(extra or {})
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            body = response.read()
            self.assertLess(len(body), 16384)
            for value in (Fixture.cookie, Fixture.session, Fixture.cfg['AUTH_KEY']):
                self.assertFalse(value.encode() in body, 'Protected credential unexpectedly disclosed')
            return response.status, body
        finally:
            connection.close()

    def valid(self, path):
        status, body = self.request(path, (Fixture.cookie, Fixture.session))
        # Container has no systemd adaptive telemetry; its genuine unavailable
        # snapshot is 503. The separate staging VM requires actual 200 data.
        self.assertEqual(status, 200 if 'gui/status' in path else 503)
        if 'gui/status' in path: self.assertEqual(json.loads(body)['state'], 'unavailable')
        else:self.assertFalse(json.loads(body)['available'])

    @staticmethod
    def forged():
        sid = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip('=')
        body = base64.urlsafe_b64encode(json.dumps({'k':'d','sid':sid,'exp':int(time.time()*1000)+3600000,'v':'1'}).encode()).decode().rstrip('=')
        return 'wd_session='+body+'.'+'A'*43, sid

    def test_01_cookie_page_and_current_session_requirements(self):
        good = (Fixture.cookie, Fixture.session)
        for path in PATHS:
            self.valid(path)
            for pair in (None, (good[0], None), (None, good[1]), ('wd_session=bad', good[1]), (good[0], self.forged()[1])):
                self.assertEqual(self.request(path, pair)[0], 401)

    def test_02_original_quota_theft_reproduction_no_cookie_and_wrong_page_flood(self):
        for path in PATHS:
            for i in range(36):
                pair = None if i%2 else (Fixture.cookie, self.forged()[1])
                self.assertEqual(self.request(path, pair)[0], 401)
            # Baseline rejected this exact next valid request with 429.
            self.valid(path)

    def test_03_concurrent_random_forgery_flood_has_separate_bounded_admission(self):
        statuses=[]
        def flood():
            deadline=time.monotonic()+1.3
            while time.monotonic()<deadline:
                statuses.append(self.request(PATHS[len(statuses)%2], self.forged())[0])
                time.sleep(.01)
        with ThreadPoolExecutor(max_workers=3) as threads:
            attacks=[threads.submit(flood) for _ in range(2)]
            for _ in range(4):
                for path in PATHS:self.valid(path)
                time.sleep(.3)
            for task in attacks:task.result()
        self.assertIn(429,statuses)
        self.assertTrue(all(status in (401,429) for status in statuses))

    def test_04_forwarded_headers_cannot_create_capacity_or_private_auth_access(self):
        statuses=[]
        for i in range(24):
            headers={'X-Forwarded-For':str(i),'CF-Connecting-IP':str(i),'X-Real-IP':str(i),'X-WebDesktop-Client-IP':str(i)}
            statuses.append(self.request(PATHS[i%2], self.forged(), headers)[0])
        self.assertIn(429,statuses)
        pair=(Fixture.cookie,Fixture.session)
        self.assertEqual(self.request('/api/guest/status-auth',pair,{'X-WebDesktop-Status-Auth':'gui'})[0],401)
        for path in PATHS:self.valid(path)

    def test_05_new_login_revokes_warm_old_hint_without_spending_new_user_quota(self):
        old=(Fixture.cookie,Fixture.session)
        self.valid(PATHS[1]);self.login()
        for _ in range(12):
            for path in PATHS:self.assertIn(self.request(path,old)[0],(401,429))
        for path in PATHS:self.valid(path)

    def psql(self, query):
        return subprocess.check_output(['runuser','-u','postgres','--','psql','-XqAt','-d',self.dbname,'-v','ON_ERROR_STOP=1','-c',query],text=True,timeout=5).strip()

    def test_06_actual_blocked_database_and_cancelled_clients_cannot_build_unbounded_auth_queue(self):
        old=(Fixture.cookie,Fixture.session);self.login();current=(Fixture.cookie,Fixture.session)
        lock=subprocess.Popen(['runuser','-u','postgres','--','psql','-XqAt','-d',self.dbname,'-v','ON_ERROR_STOP=1'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        peers=[]
        try:
            lock.stdin.write("BEGIN; LOCK TABLE auth_config IN ACCESS EXCLUSIVE MODE; SELECT 'LOCK_READY';\n");lock.stdin.flush()
            self.assertTrue(select.select([lock.stdout],[],[],5)[0]);self.assertEqual(lock.stdout.readline().strip(),'LOCK_READY')
            for pair,path in [(current,PATHS[1])]*2+[(old,PATHS[0])]*2:
                peer=socket.create_connection(('127.0.0.1',3000),timeout=3);peers.append(peer)
                peer.sendall(('GET '+path+' HTTP/1.1\r\nHost: 127.0.0.1:3000\r\nCookie: '+pair[0]+'\r\nX-Desktop-Session: '+pair[1]+'\r\nConnection: close\r\n\r\n').encode())
            query="select count(*) from pg_stat_activity where datname=current_database() and wait_event_type='Lock' and query ilike '%auth_config%'"
            deadline=time.monotonic()+4
            while int(self.psql(query))<4 and time.monotonic()<deadline:time.sleep(.05)
            self.assertEqual(int(self.psql(query)),4)
            for peer in peers:peer.close()
            # Cross the old 1.5s HTTP timeout while actual SQL stays blocked.
            deadline=time.monotonic()+2.4
            while time.monotonic()<deadline:
                peer=socket.create_connection(('127.0.0.1',3000),timeout=3)
                peer.sendall(('GET '+PATHS[1]+' HTTP/1.1\r\nHost: 127.0.0.1:3000\r\nCookie: '+current[0]+'\r\nX-Desktop-Session: '+current[1]+'\r\n\r\n').encode());peer.close()
                self.assertLessEqual(int(self.psql(query)),4)
                time.sleep(.04)
        finally:
            for peer in peers:peer.close()
            if lock.poll() is None:
                lock.stdin.write('ROLLBACK;\n\\q\n');lock.stdin.flush();lock.communicate(timeout=5)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            if self.request(PATHS[1],current)[0]==200:break
            time.sleep(.15)
        else:self.fail('Real SQL and cancelled-client authentication slots did not recover')

    def test_07_real_database_outage_is_503_and_successfully_recovers(self):
        try:
            subprocess.run(['pg_ctlcluster','15','main','stop'],check=True,capture_output=True,timeout=15)
            for path in PATHS:self.assertEqual(self.request(path,(Fixture.cookie,Fixture.session))[0],503)
        finally:
            subprocess.run(['pg_ctlcluster','15','main','start'],check=True,capture_output=True,timeout=20)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.request(PATHS[1],(Fixture.cookie,Fixture.session))[0]==200:break
            time.sleep(.15)
        else:self.fail('Status authentication did not recover after real DB restart')

    def test_08_restart_restores_authenticated_priority_via_normal_page_bootstrap_during_flood(self):
        Fixture.next.kill();Fixture.next.wait(timeout=15)
        root=Path(__file__).resolve().parents[1]
        node=subprocess.check_output(['which','node'],text=True).strip()
        Fixture.next=subprocess.Popen([node,str(root/'tests/ws_runtime_entry.mjs')],cwd=root,
            user=Fixture.service.pw_uid,group=Fixture.service.pw_gid,extra_groups=[],
            env={'PATH':str(Path(node).parent)+':/usr/bin:/bin','HOME':Fixture.service.pw_dir,'NODE_ENV':'production','NEXT_TELEMETRY_DISABLED':'1'},stdout=Fixture.next_log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            try:
                if Fixture.web('/api/health',auth=False)[0]==200:break
            except OSError:pass
            time.sleep(.2)
        else:self.fail('Disposable Next restart failed')
        def flood():
            end=time.monotonic()+1.2
            while time.monotonic()<end:
                self.assertIn(self.request(PATHS[1],self.forged())[0],(401,429))
                time.sleep(.01)
        with ThreadPoolExecutor(max_workers=2) as threads:
            attack=threads.submit(flood)
            # Existing UI already does this BEFORE restoring the desktop.
            self.assertEqual(Fixture.web('/api/auth/session')[0],200)
            for path in PATHS:self.valid(path)
            attack.result()


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(StatusRuntimeTests)
    planned=suite.countTestCases()
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary=dict(total=planned,executed=result.testsRun,passed=max(0,result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)),failed=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),not_executed=max(0,planned-result.testsRun))
    Path('/tmp/webdesktop-status-auth.json').write_text(json.dumps(summary))
    print(json.dumps(summary));raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
