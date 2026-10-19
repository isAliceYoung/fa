"""Test opt-in IPv4-only setup using fixture files, never host /proc or /etc."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_FOR_TESTS") or shutil.which("bash")
CONTENT = (
    "# WebDesktop: IPv4-only host networking\n"
    "net.ipv6.conf.all.disable_ipv6 = 1\n"
    "net.ipv6.conf.default.disable_ipv6 = 1\n"
    "net.ipv6.conf.lo.disable_ipv6 = 1\n"
)


@unittest.skipUnless(BASH, "Bash is required")
class IPv4OnlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wd-ipv4-only-")
        self.addCleanup(self.temporary.cleanup)
        self.work = pathlib.Path(self.temporary.name)
        # Coreutils install needs native POSIX paths under Git Bash on Windows.
        self.shell_root = subprocess.check_output(
            [BASH, "--noprofile", "--norc", "-c", 'cd -- "$1"; pwd -P',
             "wd-test", self.work.as_posix()], text=True).strip()
        self.config_dir = self.work / "sysctl.d"
        self.config = self.config_dir / "90-webdesktop-ipv4-only.conf"
        self.controls = self.work / "ipv6/conf"
        for scope in ("all", "default", "lo"):
            path = self.controls / scope / "disable_ipv6"
            path.parent.mkdir(parents=True)
            path.write_text("0\n", encoding="utf-8")
        self.common = self.work / "common.sh"
        source = (ROOT / "webdesktop-deploy/scripts/common.sh").read_text(encoding="utf-8")
        source = source.replace("/etc/sysctl.d", self.shell_path(self.config_dir))
        source = source.replace("/proc/sys/net/ipv6/conf", self.shell_path(self.controls))
        source = source.replace("/etc/webdesktop-deploy.conf", self.shell_path(self.work / "saved.conf"))
        self.common.write_bytes(source.encode("utf-8"))

    def shell_path(self, path):
        return self.shell_root + "/" + path.relative_to(self.work).as_posix()

    def run_shell(self, body, flag="1"):
        env = dict(os.environ, TEST_COMMON=self.shell_path(self.common), TEST_CONFIG=self.shell_path(self.config))
        env.pop("WD_IPV4_ONLY", None)
        if flag is not None:
            env["WD_IPV4_ONLY"] = flag
        return subprocess.run([BASH, "--noprofile", "--norc", "-c",
                               'export PATH="/usr/bin:/bin:$PATH"; source "$TEST_COMMON"; ' + body],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def assert_controls(self, value):
        for scope in ("all", "default", "lo"):
            self.assertEqual((self.controls / scope / "disable_ipv6").read_text(), value + "\n")

    def test_default_does_not_change_network_or_create_config(self):
        result = self.run_shell("apply_ipv4_only", flag=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config.exists())
        self.assert_controls("0")

    def test_explicit_flag_persists_and_applies_all_controls_without_sysctl(self):
        result = self.run_shell('sysctl(){ echo forbidden-sysctl; return 99; }; apply_ipv4_only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_text(), CONTENT)
        self.assertNotIn("forbidden-sysctl", result.stdout)
        self.assert_controls("1")
        if os.name != "nt":
            self.assertEqual(self.config.stat().st_mode & 0o777, 0o644)

    def test_repeat_accepts_identical_manual_config_without_replacing_it(self):
        self.config_dir.mkdir()
        self.config.write_bytes(CONTENT.encode("utf-8"))
        before = self.config.stat()
        for attempt in range(2):
            result = self.run_shell("apply_ipv4_only")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.config.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(self.config.stat().st_ino, before.st_ino)
        self.assert_controls("1")

    def test_different_config_stops_before_changing_controls(self):
        self.config_dir.mkdir()
        custom = CONTENT + "# Keep custom administrator settings\n"
        self.config.write_text(custom, encoding="utf-8")
        result = self.run_shell("apply_ipv4_only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different contents", result.stderr)
        self.assertEqual(self.config.read_text(), custom)
        self.assert_controls("0")

    def test_symlink_is_rejected_without_changing_target(self):
        self.config_dir.mkdir()
        target = self.work / "admin.conf"
        target.write_bytes(CONTENT.encode("utf-8"))
        result = self.run_shell('ln -s ../admin.conf "$TEST_CONFIG"; [[ -L "$TEST_CONFIG" ]] || exit 77; apply_ipv4_only')
        if result.returncode == 77:
            self.skipTest("Host does not support shell symlinks")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing symlink", result.stderr)
        self.assertEqual(target.read_text(), CONTENT)
        self.assert_controls("0")

    def test_kernel_without_ipv6_is_skipped(self):
        (self.controls / "all/disable_ipv6").unlink()
        result = self.run_shell("apply_ipv4_only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kernel controls unavailable", result.stdout)
        self.assertFalse(self.config.exists())

    def test_missing_control_causes_failure_instead_of_reporting_success(self):
        (self.controls / "default/disable_ipv6").unlink()
        result = self.run_shell("apply_ipv4_only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("control is not writable", result.stderr)
        self.assertNotIn("configuration installed", result.stdout)

    def test_persistence_failure_cleans_temporary_config(self):
        result = self.run_shell('mv(){ return 1; }; apply_ipv4_only')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot persist", result.stderr)
        self.assertFalse(self.config.exists())
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assert_controls("0")

    def test_configure_validates_and_defaults_ipv4_flag(self):
        fixture = ('WD_USER=fixtureuser; '
                   'getent(){ echo "fixtureuser:x:1000:1000::/home/fixtureuser:/bin/bash"; }; '
                   'id(){ case "$1" in -u) echo 1000;; -gn) echo fixtureuser;; *) echo fixtureuser;; esac; }; '
                   'configure; echo "flag=$WD_IPV4_ONLY"')
        for supplied, expected in ((None, "0"), ("0", "0"), ("1", "1"), ("2", None), ("yes", None)):
            with self.subTest(flag=supplied):
                result = self.run_shell(fixture, flag=supplied)
                if expected is None:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("WD_IPV4_ONLY must be 0 or 1", result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("flag=" + expected, result.stdout)

    def run_installer_prefix(self, arguments):
        scripts = self.work / "scripts"
        scripts.mkdir()
        (self.work / "lock").mkdir()
        events = self.work / "events"
        events.touch()
        # Use the real option parser and startup order through preflight. Substitute
        # only environment operations and stop before component installation.
        stub = '''set -Eeuo pipefail
require_root(){ :; }
require_debian(){ :; }
require_complete_build(){ :; }
flock(){ :; }
install(){ mkdir -p -- "${!#}"; }
configure(){ export WD_IPV4_ONLY="${WD_IPV4_ONLY:-0}" WD_DEPLOY_MODE="${WD_DEPLOY_MODE:-auto}"; }
die(){ echo "$*" >&2; exit 1; }
apply_ipv4_only(){ echo "ipv4:$WD_IPV4_ONLY" >> "$TEST_EVENTS"; }
save_installation(){ echo save >> "$TEST_EVENTS"; }
preflight_resources(){ echo preflight >> "$TEST_EVENTS"; }
'''
        (scripts / "common.sh").write_bytes(stub.encode("utf-8"))
        installer = self.work / "install.sh"
        source = (ROOT / "webdesktop-deploy/install.sh").read_text(encoding="utf-8")
        source = source.split("# Copy helpers", 1)[0]
        source = source.replace("/run/lock", self.shell_path(self.work / "lock"))
        source = source.replace("/var/log/webdesktop-install", self.shell_path(self.work / "log"))
        installer.write_bytes(source.encode("utf-8"))
        env = dict(os.environ, TEST_EVENTS=self.shell_path(events))
        env.pop("WD_IPV4_ONLY", None)
        env.pop("WD_DEPLOY_MODE", None)
        result = subprocess.run([BASH, "--noprofile", "--norc", "-c",
                                 'export PATH="/usr/bin:/bin:$PATH"; exec bash "$@"',
                                 "wd-test", installer.as_posix()] + arguments,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result, events.read_text().splitlines()

    def test_cli_flag_is_applied_before_save_and_preflight(self):
        result, events = self.run_installer_prefix(["--skip-claude", "--ipv4-only"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events, ["ipv4:1", "save", "preflight"])

    def test_cli_default_keeps_flag_disabled(self):
        result, events = self.run_installer_prefix([])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events, ["ipv4:0", "save", "preflight"])

    def test_service_mode_rejects_flag_before_network_changes(self):
        result, events = self.run_installer_prefix(["--ipv4-only", "--mode", "service"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be combined", result.stdout + result.stderr)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
