#!/usr/bin/env python3
"""Validate an INLINE tail block with real Debian sshd; never touch running SSH."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SshParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wd-sshd-parser-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "host-test-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True)
        self.include = ROOT / "webdesktop-deploy/templates/sshd-wdtunnel.conf.example"
        self.config = self.root / "sshd_config"
        self.early = self.root / "early-global.conf"
        self.early.write_text("LogLevel VERBOSE\n")
        Path("/run/sshd").mkdir(exist_ok=True)

    def command(self, *args):
        return subprocess.run(["/usr/sbin/sshd", "-f", str(self.config), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_inline_tail_preserves_global_include_options_and_admin_match(self):
        self.config.write_text("HostKey {}\nInclude {}\nUsePAM yes\nSubsystem sftp internal-sftp\nMaxSessions 10\nMatch User admin\n    PermitTTY yes\n".format(self.key, self.early) + self.include.read_text())
        checked = self.command("-t")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        guest = self.command("-T", "-C", "user=wdtunnel,host=test,addr=127.0.0.1")
        admin = self.command("-T", "-C", "user=admin,host=test,addr=127.0.0.1")
        self.assertEqual(guest.returncode, 0, guest.stderr)
        self.assertIn("maxsessions 0", guest.stdout)
        self.assertIn("permitopen 127.0.0.1:3000", guest.stdout)
        self.assertIn("maxsessions 10", admin.stdout)
        self.assertIn("permittty yes", admin.stdout)

    def test_early_include_with_match_all_does_not_restore_global_context(self):
        broken = self.root / "bad-early.conf"
        broken.write_text(self.include.read_text() + "\nMatch all\n")
        self.config.write_text("HostKey {}\nInclude {}\nUsePAM yes\nSubsystem sftp internal-sftp\n".format(self.key, broken))
        self.assertNotEqual(self.command("-t").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
