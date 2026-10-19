#!/usr/bin/env python3
"""Actual root/non-root permission + real Git tests in a disposable Debian CI container.

No production users/keys/network: a private temporary fixture below /root, local
file transport substituted ONLY in the test adapter, and the existing nobody UID.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("private_repo", ROOT / "webdesktop-deploy/scripts/private_repo.py")
private = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(private)
REPOSITORY = "git@github.com:isAliceYoung/fa.git"


class LinuxPrivateSourceTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0, "Run only in the disposable Debian CI container as root.")
        self.temporary = tempfile.TemporaryDirectory(prefix="wd-phase2-test-only-", dir="/root")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.seed = self.root / "local-upstream"
        self.raw_git("init", "--initial-branch=main", self.seed)
        (self.seed / "README.md").write_text("TEST SOURCE FIRST REVISION\n")
        self.raw_git("-C", self.seed, "add", ".")
        self.raw_git("-C", self.seed, "commit", "-m", "test fixture first")
        self.first = self.raw_git("-C", self.seed, "rev-parse", "HEAD").strip()
        (self.seed / "README.md").write_text("TEST SOURCE SECOND REVISION\n")
        self.raw_git("-C", self.seed, "commit", "-am", "test fixture second")
        self.head = self.raw_git("-C", self.seed, "rev-parse", "HEAD").strip()
        self.calls = []
        self.source = private.PrivateSource(self.root / "identity", self.root / "protected/source", REPOSITORY, runner=self.local_transport)
        self.source.prepare()

    def raw_git(self, *args):
        result = subprocess.run(["/usr/bin/git", "-c", "user.name=Test Fixture", "-c", "user.email=fixture@example.invalid", *map(str, args)],
                                encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return result.stdout

    def local_transport(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] != "/usr/bin/git":
            return subprocess.run(argv, **kwargs)
        mapped = [self.seed.as_uri() if value == REPOSITORY else value for value in argv]
        mapped = ["protocol.file.allow=always" if value == "protocol.file.allow=never" else value for value in mapped]
        kwargs["env"] = dict(kwargs["env"], GIT_ALLOW_PROTOCOL="file")
        result = subprocess.run(mapped, **kwargs)
        if "clone" in argv and result.returncode == 0:
            self.raw_git("-C", argv[-1], "remote", "set-url", "origin", REPOSITORY)
        return result

    def denied_read(self, path):
        result = subprocess.run(["/usr/sbin/runuser", "-u", "nobody", "--", "/bin/cat", str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_root_source_and_private_key_are_actually_unreadable_to_another_uid(self):
        result = self.source.clone()
        self.assertEqual(result["commit"], self.head)
        self.assertEqual((self.source.source.stat().st_mode & 0o777), 0o700)
        self.assertEqual((self.source.key.stat().st_mode & 0o777), 0o600)
        self.denied_read(self.source.key)
        self.denied_read(self.source.source / "README.md")
        self.denied_read(self.source.source / ".git/config")
        link = self.root / "credential-link"
        link.symlink_to(self.source.key)
        self.denied_read(link)

    def test_real_git_pins_earlier_branch_commit_and_reuses_offline_without_changes(self):
        self.source.commit = self.first
        self.assertEqual(self.source.clone()["commit"], self.first)
        path = self.source.source / "README.md"
        path.write_text("KEEP LOCAL TEST EDIT\n")
        self.calls.clear()
        result = self.source.clone()
        self.assertTrue(result["dirty"])
        self.assertEqual(path.read_text(), "KEEP LOCAL TEST EDIT\n")
        for operation in ("ls-remote", "clone", "fetch", "checkout", "reset", "pull"):
            self.assertFalse(any(operation in call for call in self.calls))

    def test_real_git_missing_branch_does_not_publish_checkout(self):
        self.source.branch = "missing-test-branch"
        with self.assertRaises(private.Failure) as error:
            self.source.clone()
        self.assertEqual(error.exception.code, "BRANCH")
        self.assertFalse(self.source.source.exists())

    def test_symlink_and_hardlink_identity_attacks_are_rejected(self):
        known = self.source.state / "known_hosts"
        backup = self.source.state / "original-known-hosts"
        known.rename(backup)
        known.symlink_to(backup)
        with self.assertRaises(private.Failure):
            self.source.verify_local()
        known.unlink()
        backup.rename(known)
        os.link(self.source.key, self.source.state / "second-key-link")
        with self.assertRaises(private.Failure):
            self.source.verify_local()

    def test_insecure_existing_state_is_rejected_without_chmod(self):
        os.chmod(self.source.state, 0o755)
        with self.assertRaises(private.Failure):
            self.source.prepare()
        self.assertEqual(self.source.state.stat().st_mode & 0o777, 0o755)

    def test_second_bootstrap_cannot_enter_locked_state(self):
        with self.source.locked():
            with self.assertRaises(private.Failure) as error:
                with self.source.locked():
                    self.fail("Concurrent lock was acquired")
            self.assertEqual(error.exception.code, "LOCKED")

    def test_publish_never_replaces_an_existing_empty_directory(self):
        self.source.directory(self.source.source.parent, create=True)
        self.source.source.mkdir()
        stage = self.source.source.parent / "test-stage"
        stage.mkdir()
        (stage / "KEEP").write_text("test fixture")
        with self.assertRaises(OSError):
            self.source.publish(stage)
        self.assertFalse((self.source.source / "KEEP").exists())
        self.assertTrue((stage / "KEEP").exists())

    def test_retained_source_blocks_repeated_new_stages(self):
        self.source.directory(self.source.source.parent, create=True)
        stage = self.source.source.parent / ".source-retained"
        stage.mkdir()
        (stage / "KEEP").write_text("retained unknown data")
        self.calls.clear()
        for _ in range(3):
            with self.assertRaises(private.Failure) as error:
                self.source.clone()
            self.assertEqual(error.exception.code, "STALE_STAGE")
        self.assertEqual(len(list(self.source.source.parent.glob(".source-*"))), 1)
        self.assertEqual((stage / "KEEP").read_text(), "retained unknown data")
        self.assertFalse(any("ls-remote" in call for call in self.calls))

    def test_disk_watchdog_reaps_actual_process_group(self):
        created = []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            created.append(process)
            return process
        with mock.patch.object(private.subprocess, "Popen", side_effect=popen), mock.patch.object(self.source, "stage_budget", return_value="DISK_SPACE"):
            with self.assertRaises(private.Failure):
                self.source.monitored_clone(["/usr/bin/python3", "-c", "import time;time.sleep(60)"], timeout=5,
                                            env=self.source.environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", check=False)
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())


if __name__ == "__main__":
    if sys.platform != "linux" or os.geteuid() != 0:
        sys.exit("NOT EXECUTED: requires a disposable root Linux container; no tests were run.")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LinuxPrivateSourceTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("Linux source permissions: total={}, passed={}, failures={}, errors={}, skipped={}".format(
        result.testsRun, result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        len(result.failures), len(result.errors), len(result.skipped)))
    Path("/tmp/webdesktop-source-permissions.json").write_text(json.dumps({"total": result.testsRun, "passed": max(0, result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)), "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}, indent=2) + "\n")
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
