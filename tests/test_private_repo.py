"""No network or production credentials: fake Git/SSH plus temporary test keys."""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("private_repo", ROOT / "webdesktop-deploy/scripts/private_repo.py")
private = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(private)
HEAD = "a" * 40
OLD = "b" * 40
TEST_PUBLIC = "ssh-ed25519 AAAATESTONLYNOTAPRODUCTIONKEY fixture"


class FakeTools:
    def __init__(self):
        self.calls = []
        self.environments = []
        self.error = ""
        self.remote_head = HEAD
        self.clone_head = HEAD
        self.checked_out = HEAD
        self.dirty = False
        self.fail_clone = False
        self.hook = None

    def __call__(self, argv, **kwargs):
        self.calls.append(list(map(str, argv)))
        self.environments.append(kwargs["env"])
        if argv[0].endswith("ssh-keygen"):
            if "-y" in argv:
                return subprocess.CompletedProcess(argv, 0, TEST_PUBLIC, "")
            key = Path(argv[argv.index("-f") + 1])
            key.write_text("TEST_PRIVATE_KEY_NEVER_PRINT_THIS", encoding="utf-8")
            key.with_suffix(".pub").write_text(TEST_PUBLIC + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        args = list(map(str, argv[1:]))
        while args and args[0] == "-c":
            args = args[2:]
        directory = None
        if args[0] == "-C":
            directory, args = Path(args[1]), args[2:]
        command = args[0]
        out = ""
        if command == "check-ref-format":
            if ".." in args[-1] or args[-1].endswith("/"):
                return subprocess.CompletedProcess(argv, 128, "", "invalid branch")
        elif command == "ls-remote":
            if self.error:
                return subprocess.CompletedProcess(argv, 128, "", self.error)
            out = self.remote_head + "\t" + args[-1]
        elif command == "clone":
            stage = Path(args[-1])
            (stage / ".git").mkdir()
            (stage / "README.md").write_text("test checkout", encoding="utf-8")
            if self.hook:
                self.hook()
            if self.fail_clone:
                return subprocess.CompletedProcess(argv, 128, "", "SECRET_MUST_NOT_LEAK")
        elif command == "rev-parse":
            out = self.clone_head if args[-1].startswith("refs/") else self.checked_out
        elif command == "checkout":
            self.checked_out = args[-1]
        elif command == "remote":
            out = "git@github.com:isAliceYoung/fa.git"
        elif command == "status":
            out = " M README.md" if self.dirty else ""
        elif command not in ("cat-file", "merge-base"):
            raise AssertionError("Unexpected tool operation: " + repr(argv))
        return subprocess.CompletedProcess(argv, 0, out, "")


class PortableFixture(private.PrivateSource):
    """Only ownership/locks are simulated on Windows; production has no bypass flag."""
    def directory(self, path, create=False, private=True):
        path = Path(path)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise FileNotFoundError("Missing fixture directory")

    def check_node(self, path, directory=False, private=False):
        if not Path(path).exists():
            raise FileNotFoundError(str(path))

    @contextlib.contextmanager
    def locked(self, create=False):
        self.directory(self.state, create=create)
        yield


class PrivateSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wd-private-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = FakeTools()
        self.source = PortableFixture(self.root / "identity", self.root / "protected/source",
                                      "https://github.com/isAliceYoung/fa.git", runner=self.tools)

    def prepared(self):
        self.source.prepare()
        self.tools.calls.clear()
        self.tools.environments.clear()

    def cloned(self):
        self.prepared()
        result = self.source.clone()
        self.tools.calls.clear()
        self.tools.environments.clear()
        return result

    def assert_failure(self, code, function):
        with self.assertRaises(private.Failure) as error:
            function()
        self.assertEqual(error.exception.code, code)

    def test_prepare_creates_separate_key_and_pinned_configuration(self):
        self.assertEqual(self.source.prepare(), TEST_PUBLIC)
        self.assertEqual((self.source.state / "known_hosts").read_text(), private.HOST_KEY)
        self.assertIn("StrictHostKeyChecking yes", self.source.ssh_config())
        self.assertIn("IdentityAgent none", self.source.ssh_config())
        self.assertFalse(self.source.source.exists())
        self.assertFalse(any("ls-remote" in call for call in self.tools.calls))

    def test_repeated_prepare_preserves_private_key_bytes(self):
        self.prepared()
        before = self.source.key.read_bytes()
        self.source.prepare()
        self.assertEqual(self.source.key.read_bytes(), before)
        self.assertFalse(any("-t" in call for call in self.tools.calls))

    def test_missing_public_key_is_derived_without_rotating_private_key(self):
        self.prepared()
        before = self.source.key.read_bytes()
        self.source.key.with_suffix(".pub").unlink()
        self.source.prepare()
        self.assertEqual(before, self.source.key.read_bytes())
        self.assertFalse(any("-t" in call for call in self.tools.calls))

    def test_public_key_without_private_key_is_never_replaced(self):
        self.prepared()
        self.source.key.unlink()
        self.assert_failure("KEY_PAIR", self.source.prepare)
        self.assertEqual(self.source.key.with_suffix(".pub").read_text().strip(), TEST_PUBLIC)

    def test_mismatched_public_key_is_rejected(self):
        self.prepared()
        self.source.key.with_suffix(".pub").write_text("ssh-ed25519 DIFFERENT")
        self.assert_failure("KEY_PAIR", self.source.prepare)

    def test_host_pin_change_is_rejected_without_overwrite(self):
        self.prepared()
        hosts = self.source.state / "known_hosts"
        hosts.write_text("UNTRUSTED HOST KEY\n")
        self.assert_failure("CONFIG_CHANGED", self.source.prepare)
        self.assertEqual(hosts.read_text(), "UNTRUSTED HOST KEY\n")

    def test_ssh_config_change_is_rejected(self):
        self.prepared()
        (self.source.state / "ssh_config").write_text("Host *\n StrictHostKeyChecking no\n")
        self.assert_failure("CONFIG_CHANGED", self.source.verify_local)

    def test_same_key_directory_cannot_be_reused_for_another_repo(self):
        self.prepared()
        self.source.repo = "git@github.com:another/repository.git"
        self.assert_failure("CONFIG_CHANGED", self.source.prepare)

    def test_compiled_host_pin_is_verified_independently(self):
        with mock.patch.object(private, "HOST_FINGERPRINT", "SHA256:bad"):
            self.assert_failure("HOST_PIN", self.source.prepare)

    def test_unauthorized_repository_stops_before_any_clone(self):
        self.prepared()
        self.tools.error = "Permission denied (publickey). SECRET_MUST_NOT_LEAK"
        self.assert_failure("AUTHORIZATION", self.source.clone)
        self.assertFalse(self.source.source.exists())
        self.assertFalse(any("clone" in call for call in self.tools.calls))

    def test_network_failure_preserves_key(self):
        self.prepared()
        before = self.source.key.read_bytes()
        self.tools.error = "ssh: Could not resolve hostname github.com"
        self.assert_failure("NETWORK", self.source.clone)
        self.assertEqual(before, self.source.key.read_bytes())

    def test_host_handshake_failure_is_distinct_from_authorization(self):
        self.prepared()
        self.tools.error = "Host key verification failed."
        self.assert_failure("HOST_KEY", self.source.authorized)

    def test_raw_tool_stderr_never_enters_error_message(self):
        self.prepared()
        self.tools.error = "SECRET_MUST_NOT_LEAK"
        with self.assertRaises(private.Failure) as error:
            self.source.authorized()
        self.assertNotIn("SECRET_MUST_NOT_LEAK", str(error.exception))

    def test_clean_environment_does_not_inherit_credentials_or_agent(self):
        with mock.patch.dict(os.environ, {"SSH_AUTH_SOCK": "/test-agent", "GIT_CONFIG_COUNT": "1", "AUTH_KEY": "secret", "DATABASE_URL": "secret", "GIT_SSH_COMMAND": "malicious"}):
            self.prepared()
            self.source.authorized()
        environment = self.tools.environments[-1]
        for name in ("SSH_AUTH_SOCK", "GIT_CONFIG_COUNT", "AUTH_KEY", "DATABASE_URL", "SUDO_USER"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["GIT_ALLOW_PROTOCOL"], "ssh")
        self.assertTrue(environment["GIT_SSH_COMMAND"].startswith("/usr/bin/ssh -F "))
        self.assertNotIn("malicious", environment["GIT_SSH_COMMAND"])

    def test_root_git_always_disables_hooks_fsmonitor_and_credentials(self):
        self.prepared()
        self.source.clone()
        for call in self.tools.calls:
            if call[0].endswith("git"):
                self.assertIn("core.hooksPath=/dev/null", call)
                self.assertIn("core.fsmonitor=false", call)
                self.assertIn("credential.helper=", call)

    def test_successful_clone_records_exact_commit_and_has_no_install_commands(self):
        self.prepared()
        result = self.source.clone()
        self.assertEqual(result, {"commit": HEAD, "existing": False, "dirty": False})
        record = json.loads((self.source.source / ".git/webdesktop-source.json").read_text())
        self.assertEqual(record["commit"], HEAD)
        self.assertTrue((self.source.source / "README.md").is_file())
        commands = " ".join(" ".join(call) for call in self.tools.calls)
        for forbidden in ("npm", "apt-get", "systemctl", "runuser", "sudo", "chown"):
            self.assertNotIn(forbidden, commands)

    def test_requested_commit_is_checked_and_pinned(self):
        self.prepared()
        self.source.commit = OLD
        self.assertEqual(self.source.clone()["commit"], OLD)
        self.assertTrue(any("merge-base" in call and OLD in call for call in self.tools.calls))

    def test_existing_checkout_is_reused_offline_without_fetch(self):
        self.cloned()
        self.tools.error = "Repository not found"
        self.assertTrue(self.source.clone()["existing"])
        for operation in ("clone", "fetch", "checkout", "reset", "pull", "ls-remote"):
            self.assertFalse(any(operation in call for call in self.tools.calls))

    def test_existing_user_edits_are_preserved_and_reported(self):
        self.cloned()
        path = self.source.source / "README.md"
        path.write_text("KEEP USER EDITS")
        self.tools.dirty = True
        self.assertTrue(self.source.clone()["dirty"])
        self.assertEqual(path.read_text(), "KEEP USER EDITS")

    def test_existing_source_is_not_adopted_without_manifest(self):
        self.cloned()
        (self.source.source / ".git/webdesktop-source.json").unlink()
        self.assert_failure("EXISTING_SOURCE", self.source.clone)
        self.assertTrue((self.source.source / "README.md").is_file())

    def test_existing_source_branch_mismatch_is_not_changed(self):
        self.cloned()
        self.source.branch = "other"
        self.assert_failure("EXISTING_SOURCE", self.source.clone)
        self.assertFalse(any("checkout" in call for call in self.tools.calls))

    def test_existing_source_commit_mismatch_is_not_changed(self):
        self.cloned()
        self.source.commit = OLD
        self.assert_failure("EXISTING_SOURCE", self.source.clone)

    def test_changed_remote_branch_does_not_publish_source(self):
        self.prepared()
        self.tools.clone_head = OLD
        self.assert_failure("REMOTE_CHANGED", self.source.clone)
        self.assertFalse(self.source.source.exists())
        self.assertEqual(len(list(self.source.source.parent.glob(".source-*"))), 1)

    def test_failed_clone_is_retained_and_retry_can_succeed(self):
        self.prepared()
        self.tools.fail_clone = True
        self.assert_failure("COMMAND", self.source.clone)
        failed = list(self.source.source.parent.glob(".source-*"))
        self.assertEqual(len(failed), 1)
        self.tools.fail_clone = False
        self.assert_failure("STALE_STAGE", self.source.clone)
        reviewed = failed[0].with_name("reviewed-retained-source")
        failed[0].rename(reviewed)  # Administrator review is simulated; no data deleted.
        self.assertEqual(self.source.clone()["commit"], HEAD)
        self.assertTrue(reviewed.exists())

    def test_low_disk_does_not_create_staging_or_contact_remote(self):
        self.prepared()
        with mock.patch.object(private.shutil, "disk_usage", return_value=SimpleNamespace(free=1)):
            self.assert_failure("DISK_SPACE", self.source.clone)
        self.assertFalse(list(self.source.source.parent.glob(".source-*")))
        self.assertFalse(any("ls-remote" in call for call in self.tools.calls))

    def test_interrupted_key_stage_blocks_unlimited_new_generations(self):
        self.source.state.mkdir(parents=True)
        entry = self.source.state / ".key-unknown"
        entry.mkdir()
        self.assert_failure("STALE_KEY_STAGE", self.source.prepare)
        self.assertTrue(entry.exists())
        self.assertFalse(self.source.key.exists())

    def test_unknown_staging_entry_is_preserved_and_blocks_another_clone(self):
        self.prepared()
        self.source.source.parent.mkdir(parents=True)
        entry = self.source.source.parent / ".source-unknown"
        entry.write_text("KEEP UNKNOWN DATA")
        self.assert_failure("STALE_STAGE", self.source.clone)
        self.assertEqual(entry.read_text(), "KEEP UNKNOWN DATA")

    def test_destination_appearing_mid_clone_is_not_replaced(self):
        self.prepared()
        def create_destination():
            self.source.source.mkdir()
            (self.source.source / "KEEP").write_text("existing user data")
        self.tools.hook = create_destination
        with self.assertRaises(OSError):
            self.source.clone()
        self.assertEqual((self.source.source / "KEEP").read_text(), "existing user data")

    def test_timeout_returns_only_sanitized_error(self):
        self.prepared()
        self.source.runner = mock.Mock(side_effect=subprocess.TimeoutExpired(["secret"], 1, stderr="SECRET"))
        self.assert_failure("TIMEOUT", self.source.authorized)

    def test_missing_tools_return_actionable_error(self):
        self.prepared()
        self.source.runner = mock.Mock(side_effect=FileNotFoundError("SECRET"))
        self.assert_failure("TOOL", self.source.authorized)


class ValidationTests(unittest.TestCase):
    def options(self, **changes):
        values = dict(repo="https://github.com/isAliceYoung/fa.git", state_dir="/root/.ssh/webdesktop-fa",
                      source_dir="/opt/webdesktop/source", branch="feature/private-deploy-isolation", commit="")
        values.update(changes)
        return argparse.Namespace(**values)

    def test_accepts_https_scp_and_ssh_url_without_credentials(self):
        for value in ("https://github.com/isAliceYoung/fa.git", "git@github.com:isAliceYoung/fa.git", "ssh://git@github.com/isAliceYoung/fa.git"):
            self.assertEqual(private.repository(value), "git@github.com:isAliceYoung/fa.git")

    def test_rejects_credentials_query_other_hosts_and_shell_options(self):
        for value in ("https://token@github.com/isAliceYoung/fa", "https://github.com.evil/owner/repo", "https://github.com/owner/repo?token=SECRET", "git@github.com:owner/repo;touch /tmp/pwn", "-u evil", "ssh://root@github.com/owner/repo"):
            with self.subTest(value=value), self.assertRaises(private.Failure):
                private.repository(value)

    def test_rejects_unsafe_paths_and_guest_home(self):
        for value in ("/home/wdguest/source", "/opt/webdesktop/../source", "/opt/webdesktop/./source", "/tmp/source", "/opt/webdesktop/source name"):
            with self.subTest(value=value), self.assertRaises(private.Failure):
                private.validate_options(self.options(source_dir=value))

    def test_rejects_credentials_outside_root_protected_locations(self):
        for value in ("/home/wdsvc/.ssh", "/tmp/keys", "/root", "/root/.ssh", "/root/.ssh/../keys"):
            with self.subTest(value=value), self.assertRaises(private.Failure):
                private.validate_options(self.options(state_dir=value))

    def test_rejects_invalid_commit_and_option_branch(self):
        for changes in ({"commit": "HEAD"}, {"commit": "a" * 39}, {"branch": "--upload-pack=evil"}, {"branch": "main;evil"}):
            with self.subTest(changes=changes), self.assertRaises(private.Failure):
                private.validate_options(self.options(**changes))

    def test_normalizes_commit_and_repository(self):
        options = self.options(commit="A" * 40)
        private.validate_options(options)
        self.assertEqual(options.commit, HEAD)
        self.assertTrue(options.repo.startswith("git@github.com:"))

    def test_unsafe_metadata_and_hard_links_are_rejected(self):
        source = private.PrivateSource("/root/test", "/opt/webdesktop/source", "https://github.com/isAliceYoung/fa")
        for mode, uid, links in ((stat.S_IFLNK | 0o600, 0, 1), (stat.S_IFREG | 0o600, 1000, 1),
                                 (stat.S_IFREG | 0o644, 0, 1), (stat.S_IFREG | 0o600, 0, 2),
                                 (stat.S_IFDIR | 0o700, 0, 1)):
            path = mock.Mock()
            path.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links)
            with self.subTest(mode=mode, uid=uid, links=links), self.assertRaises(private.Failure):
                source.check_node(path, private=True)

    def test_root_private_regular_file_is_accepted(self):
        source = private.PrivateSource("/root/test", "/opt/webdesktop/source", "https://github.com/isAliceYoung/fa")
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)
        source.check_node(path, private=True)

    def test_nonroot_cli_is_rejected(self):
        with mock.patch.object(private.os, "geteuid", return_value=1000, create=True), self.assertRaises(private.Failure):
            private.require_platform()


class RealTemporaryKeyTests(unittest.TestCase):
    def test_actual_ed25519_key_generation_and_reuse_only_in_temporary_directory(self):
        git = shutil.which("git")
        keygen = shutil.which("ssh-keygen")
        if not keygen and git and os.name == "nt":
            keygen = str(Path(git).resolve().parent.parent / "usr/bin/ssh-keygen.exe")
        self.assertTrue(git and keygen and Path(keygen).is_file(), "Install Git/OpenSSH test tools; this test must not silently skip.")
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            if argv[0].endswith("git"):
                self.assertIn("check-ref-format", argv)
                argv = [git] + argv[1:]
            else:
                self.assertTrue(argv[0].endswith("ssh-keygen"))
                argv = [keygen] + argv[1:]
            # Native Windows executables need Windows OS variables; production
            # environment construction remains tested separately above.
            kwargs["env"] = dict(os.environ, **kwargs["env"])
            return subprocess.run(argv, **kwargs)
        with tempfile.TemporaryDirectory(prefix="wd-test-key-only-") as work:
            source = PortableFixture(Path(work) / "identity", Path(work) / "source", "https://github.com/isAliceYoung/fa", runner=run)
            public = source.prepare()
            before = source.key.read_bytes()
            self.assertTrue(public.startswith("ssh-ed25519 "))
            self.assertIn(b"BEGIN OPENSSH PRIVATE KEY", before)
            self.assertEqual(public, source.prepare())
            self.assertEqual(before, source.key.read_bytes())
        self.assertEqual(sum("-t" in call for call in commands), 1)


class RealLocalGitTests(unittest.TestCase):
    """Real Git/keygen/checkout, with only the remote transport replaced by file."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wd-local-git-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed = self.root / "upstream"
        self.git = shutil.which("git")
        self.keygen = shutil.which("ssh-keygen")
        if not self.keygen and self.git and os.name == "nt":
            self.keygen = str(Path(self.git).resolve().parent.parent / "usr/bin/ssh-keygen.exe")
        self.assertTrue(self.git and self.keygen and Path(self.keygen).is_file(), "Git/OpenSSH tools required.")
        self.git_raw("init", "--initial-branch=main", self.seed)
        (self.seed / "README.md").write_text("first test revision\n")
        self.git_raw("-C", self.seed, "add", ".")
        self.git_raw("-C", self.seed, "commit", "-m", "fixture first")
        self.first = self.git_raw("-C", self.seed, "rev-parse", "HEAD").strip()
        (self.seed / "README.md").write_text("second test revision\n")
        self.git_raw("-C", self.seed, "commit", "-am", "fixture second")
        self.head = self.git_raw("-C", self.seed, "rev-parse", "HEAD").strip()
        self.calls = []
        self.source = PortableFixture(self.root / "identity", self.root / "protected/source", "https://github.com/isAliceYoung/fa", runner=self.local_transport)
        self.source.prepare()

    def git_raw(self, *args):
        environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        result = subprocess.run([self.git, "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test Fixture", "-c", "user.email=fixture@example.invalid", *map(str, args)],
                                env=environment, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return result.stdout

    def local_transport(self, argv, **kwargs):
        self.calls.append(list(argv))
        kwargs["env"] = dict(os.environ, **kwargs["env"])
        if argv[0].endswith("ssh-keygen"):
            return subprocess.run([self.keygen] + argv[1:], **kwargs)
        mapped = [self.seed.as_uri() if value == self.source.repo else str(value) for value in argv[1:]]
        mapped = ["protocol.file.allow=always" if value == "protocol.file.allow=never" else value for value in mapped]
        kwargs["env"]["GIT_ALLOW_PROTOCOL"] = "file"
        result = subprocess.run([self.git] + mapped, **kwargs)
        if "clone" in argv and result.returncode == 0:
            self.git_raw("-C", argv[-1], "remote", "set-url", "origin", self.source.repo)
        return result

    def test_real_git_clones_and_reuses_exact_upstream_commit_without_fetch(self):
        self.assertEqual(self.source.clone()["commit"], self.head)
        path = self.source.source / "README.md"
        path.write_text("KEEP REAL LOCAL EDIT\n")
        self.calls.clear()
        self.assertTrue(self.source.clone()["dirty"])
        self.assertEqual(path.read_text(), "KEEP REAL LOCAL EDIT\n")
        self.assertFalse(any("ls-remote" in call or "fetch" in call or "checkout" in call for call in self.calls))

    def test_real_git_checks_out_requested_earlier_branch_commit(self):
        self.source.commit = self.first
        self.assertEqual(self.source.clone()["commit"], self.first)
        self.assertEqual((self.source.source / "README.md").read_text(), "first test revision\n")

    def test_real_git_missing_branch_is_reported_without_creating_source(self):
        self.source.branch = "nonexistent-fixture-branch"
        with self.assertRaises(private.Failure) as error:
            self.source.clone()
        self.assertEqual(error.exception.code, "BRANCH")
        self.assertFalse(self.source.source.exists())


if __name__ == "__main__":
    unittest.main()
