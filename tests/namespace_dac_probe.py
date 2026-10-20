"""Adversarial user-namespace root, on the actual HOST mount namespace.

Root lab controller drops to wdgui before unshare. This package contains no
secrets and is not an authenticated execution API. Reads must fail via DAC,
without ProtectHome/InaccessiblePaths hiding the protected files.
"""
import errno
import json
import os
from pathlib import Path
import socket
import sys

uid, service, mount = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
assert os.getuid() == 0 and service > 1
assert os.readlink('/proc/self/ns/mnt') == mount
mapping = Path('/proc/self/uid_map').read_text().split()
assert list(map(int, mapping)) == [0, uid, 1]
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert int(next(l.split()[1] for l in status.splitlines() if l.startswith('CapEff:')), 16) > 0
report = {'uid_map': mapping, 'same_host_mount_namespace': True, 'protected_files': [], 'denied_sockets': []}
for path in ['/opt/webdesktop/server.mjs', '/opt/webdesktop/.env', '/opt/webdesktop/.git/HEAD', '/opt/webdesktop/private-source.test', '/root/deploy-key.test']:
    for flags in [os.O_RDONLY, os.O_WRONLY]:
        try:
            fd = os.open(path, flags)
        except OSError as error:
            assert error.errno in [errno.EACCES, errno.EPERM], (path, error)
        else:
            os.close(fd)
            raise AssertionError('Namespace capability bypassed host DAC')
    report['protected_files'].append(path)
for path in ['/run/webdesktop-guest/guest.sock', '/run/webdesktop-gui/session-1.sock', '/run/webdesktop-browser/session-1.sock']:
    with socket.socket(socket.AF_UNIX) as connection:
        try:
            connection.connect(path)
        except OSError as error:
            assert error.errno in [errno.EACCES, errno.EPERM], (path, error)
        else:
            raise AssertionError('Namespace root reached service IPC')
    report['denied_sockets'].append(path)
try:
    os.kill(service, 0)
except PermissionError:
    report['service_signal_denied'] = True
else:
    raise AssertionError('Namespace root may signal service UID')
print(json.dumps(report))
