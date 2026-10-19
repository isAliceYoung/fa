"""Actual new terminal UID versus the real file agent and Next.js."""
import errno
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import unittest
from linux_guest_layer import GuestLayerTests as F

class TerminalBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        F.setUpClass()
        cls.terminal = pwd.getpwnam('wdterm')

    @classmethod
    def tearDownClass(cls):
        F.tearDownClass()

    def run_terminal(self, code, *args):
        return subprocess.run(['/usr/bin/python3', '-I', '-c', code, *map(str, args)],
            user=self.terminal.pw_uid, group=F.guest.pw_gid, extra_groups=[],
            capture_output=True, text=True, timeout=8)

    def test_distinct_uid_cannot_signal_or_inspect_agent_or_backend(self):
        self.assertNotEqual(self.terminal.pw_uid,F.guest.pw_uid)
        for target in (F.agent.pid,F.next.pid):
            code='''import os,sys,signal,ctypes,json
pid=int(sys.argv[1]);r={}
for sig in (0,signal.SIGSTOP,signal.SIGTERM):
 try:os.kill(pid,sig);raise AssertionError('Signal allowed')
 except PermissionError:r[str(sig)]='EPERM'
for suffix in ('environ','mem','fd'):
 try:
  if suffix=='fd':os.listdir('/proc/%s/fd'%pid)
  else:open('/proc/%s/%s'%(pid,suffix),'rb').read(1)
  raise AssertionError('Process internals readable')
 except PermissionError:r[suffix]='EACCES'
libc=ctypes.CDLL(None,use_errno=True)
assert libc.ptrace(16,pid,None,None)==-1 and ctypes.get_errno()==1
print(json.dumps(r))
'''
            r=self.run_terminal(code,target)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIsNone(F.agent.poll());self.assertIsNone(F.next.poll())

    def test_transaction_mount_cannot_be_read_written_renamed_or_replaced(self):
        probe=F.home/'.wd-state/boundary-probe'
        probe.write_text('private');os.chown(probe,F.guest.pw_uid,F.guest.pw_gid);probe.chmod(0o600)
        try:
            r=self.run_terminal('''import os,sys,errno,pathlib,json
p=pathlib.Path(sys.argv[1]);r={}
for op in ('read','write','rename','replace'):
 try:
  if op=='read':p.read_bytes()
  elif op=='write':p.write_text('tampered')
  elif op=='rename':p.parent.rename(p.parent.with_name('.wd-stolen'))
  else:p.parent.rmdir()
  raise AssertionError('Private state operation allowed')
 except OSError as e:
  assert e.errno in (errno.EACCES,errno.EPERM,errno.EBUSY)
  r[op]=e.errno
print(json.dumps(r))
''',probe)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(probe.read_text(),'private')
            self.assertEqual(F.ipc('/v1/info')[0],200)
        finally:probe.unlink(missing_ok=True)

    def test_only_guest_data_group_is_shared_and_real_files_interoperate(self):
        self.assertNotEqual(F.guest.pw_gid,F.service.pw_gid)
        r=self.run_terminal("import os,sys;os.umask(7);p=sys.argv[1];open(p,'w').write('shell file');print(os.getuid(),os.getgid(),os.getgroups())",F.home/'Documents/terminal-owned.txt')
        self.assertEqual(r.returncode,0,r.stderr)
        status,body,_=F.web('/api/sys/fs?read=1&path=~/Documents/terminal-owned.txt')
        self.assertEqual(status,200,body[:200]);self.assertEqual(json.loads(body)['content'],'shell file')
        self.assertEqual(F.web('/api/sys/fs','PUT',{'path':'~/Documents/terminal-owned.txt','content':'agent update'})[0],200)
        r=self.run_terminal("import pathlib,sys;p=pathlib.Path(sys.argv[1]);assert p.read_text()=='agent update';p.write_text('shell update')",F.home/'Documents/terminal-owned.txt')
        self.assertEqual(r.returncode,0,r.stderr)

    def test_terminal_cannot_connect_file_ipc_or_internal_next_socket(self):
        state=json.loads((Path(F.service.pw_dir)/'gateway-runtime.json').read_text())
        for path in ('/run/webdesktop-guest/guest.sock',state['internal']):
            r=self.run_terminal("import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1])",path)
            self.assertNotEqual(r.returncode,0);self.assertIn('PermissionError',r.stderr)

if __name__=='__main__':
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TerminalBoundaryTests))
    report=dict(total=r.testsRun,passed=r.testsRun-len(r.failures)-len(r.errors)-len(r.skipped),failed=len(r.failures),errors=len(r.errors),skipped=len(r.skipped))
    Path('/tmp/webdesktop-terminal-boundary.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report));sys.exit(0 if r.wasSuccessful() and not r.skipped else 1)
