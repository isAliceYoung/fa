"""Booted systemd only. No inherited-FD fixture or container approximation."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time
import unittest

LOG = Path('/root/wd-systemd-evidence')
LOG.mkdir(exist_ok=True)
def run(*args, check=True, **kwargs):
    kwargs.setdefault('timeout',45)
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kwargs)
    with (LOG/'commands.txt').open('a') as stream:
        stream.write('$ ' + ' '.join(args) + '\n' + result.stdout + '\nexit=' + str(result.returncode) + '\n')
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(args))
    return result

IPC = '''import socket,http.client
c=http.client.HTTPConnection('localhost',timeout=10)
c.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);c.sock.connect('/run/webdesktop-guest/guest.sock')
c.putrequest('GET','/v1/info',skip_accept_encoding=True);c.endheaders()
r=c.getresponse();assert r.status==200;print(r.read().decode())
'''
def invoke():
    return run('runuser','-u','wdsvc','--','python3','-I','-c',IPC)
def pid():
    return int(run('systemctl','show','webdesktop-guest.service','-p','MainPID','--value').stdout.strip())

class SystemdRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if Path('/proc/1/comm').read_text().strip() != 'systemd' or os.geteuid() != 0:
            raise RuntimeError('Booted disposable root Debian VM required')
        cls.guest, cls.svc = pwd.getpwnam('wdguest'), pwd.getpwnam('wdsvc')
        run('systemd-analyze','verify','/etc/systemd/system/webdesktop-guest.service','/etc/systemd/system/webdesktop-guest.socket')
        run('systemd-analyze','security','--no-pager','webdesktop-guest.service')
        run('systemctl','status','--no-pager','webdesktop-guest.socket')

    def test_01_socket_activation_and_actual_permissions(self):
        s = Path('/run/webdesktop-guest/guest.sock').stat()
        self.assertEqual((s.st_uid,s.st_gid,s.st_mode & 0o777), (0,self.svc.pw_gid,0o660))
        self.assertEqual(Path('/run/webdesktop-guest').stat().st_mode & 0o777,0o750)
        invoke()
        self.assertGreater(pid(),0)
        run('systemctl','status','--no-pager','webdesktop-guest.service')

    def test_02_unauthorized_socket_connections(self):
        code = "import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/webdesktop-guest/guest.sock')"
        for user in ('wdguest','nobody'):
            self.assertNotEqual(run('runuser','-u',user,'--','python3','-I','-c',code,check=False).returncode,0)

    def test_03_actual_process_identity_and_limits(self):
        invoke()
        text = Path('/proc/{}/status'.format(pid())).read_text()
        self.assertIn('Uid:\t{0}\t{0}\t{0}\t{0}'.format(self.guest.pw_uid),text)
        self.assertIn('Gid:\t{0}\t{0}\t{0}\t{0}'.format(self.guest.pw_gid),text)
        self.assertIn('NoNewPrivs:\t1',text)
        for name in ('CapEff','CapPrm','CapAmb','CapBnd'):
            self.assertIn(name+':\t0000000000000000',text)
        run('systemctl','show','webdesktop-guest.service','-p','User','-p','Group','-p','NoNewPrivileges','-p','MemoryCurrent','-p','MemoryPeak','-p','MemoryMax','-p','TasksMax')

    def test_04_namespace_mounts_and_home_rw(self):
        invoke()
        # nsenter is a root-only test instrument. Commands drop to the actual
        # guest UID after entering the service mount namespace; never in agent.
        def inside(code):
            return run('nsenter','--target',str(pid()),'--mount','--','setpriv','--reuid',str(self.guest.pw_uid),'--regid',str(self.guest.pw_gid),'--clear-groups','python3','-I','-c',code,check=False)
        self.assertEqual(inside("from pathlib import Path;p=Path('/home/wdguest/systemd-write.test');p.write_text('ok');assert p.read_text()=='ok'").returncode,0)
        for path in ('/opt/webdesktop/private-source.test','/root/deploy-key.test','/var/lib/webdesktop'):
            self.assertNotEqual(inside("from pathlib import Path;Path("+repr(path)+").read_bytes()").returncode,0)
        mountinfo = Path('/proc/{}/mountinfo'.format(pid())).read_text()
        self.assertTrue(any(' /home ' in line and ' - tmpfs ' in line for line in mountinfo.splitlines()))
        self.assertTrue(any(' /usr ' in line and ' ro,' in line for line in mountinfo.splitlines()) or any(' / ' in line and ' ro,' in line for line in mountinfo.splitlines()))
        (LOG/'mountinfo.txt').write_text(mountinfo)

    def test_05_crash_and_termination_restart(self):
        for sig in ('SIGKILL','SIGTERM'):
            invoke(); old=pid()
            run('systemctl','kill','--signal='+sig,'webdesktop-guest.service')
            # A clean TERM exits without Restart=on-failure; a new socket
            # request reactivates it. KILL is automatically restarted.
            time.sleep(3)
            invoke()
            self.assertNotEqual(pid(),old)

    def test_06_start_failure_is_visible_and_recoverable(self):
        run('systemctl','stop','webdesktop-guest.service')
        runtime=Path('/usr/local/lib/webdesktop-guest/agent.py')
        saved=runtime.with_suffix('.test-only-disabled')
        runtime.rename(saved)
        try:
            result=run('systemctl','start','webdesktop-guest.service',check=False)
            time.sleep(1)
            state=run('systemctl','show','webdesktop-guest.service','-p','Result','-p','ExecMainStatus')
            self.assertNotIn('ExecMainStatus=0\n',state.stdout)
        finally:
            run('systemctl','stop','webdesktop-guest.service',check=False)
            saved.rename(runtime)
            run('systemctl','reset-failed','webdesktop-guest.service')
            run('systemctl','restart','webdesktop-guest.socket')
        invoke()

    def test_07_ssh_administrator_configuration(self):
        run('/usr/sbin/sshd','-t')
        run('systemctl','is-active','ssh')
        self.assertNotEqual(self.guest.pw_uid,self.svc.pw_uid)

if __name__ == '__main__':
    stage=sys.argv[1] if len(sys.argv)>1 else 'prepare'
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SystemdRuntimeTests))
    summary=dict(stage=stage,total=result.testsRun,passed=max(0,result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)),failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    (LOG/(stage+'.json')).write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
