"""Test default IPv4-only setup using fixture files, never host /proc or /etc."""
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
CONFIGURE_FIXTURE = (
    'WD_USER=fixtureuser; '
    'getent(){ echo "fixtureuser:x:1000:1000::/home/fixtureuser:/bin/bash"; }; '
    'id(){ case "$1" in -u) echo 1000;; -gn) echo fixtureuser;; *) echo fixtureuser;; esac; }; '
    'configure'
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

    def test_configured_default_persists_ipv4_only_and_applies_controls(self):
        result = self.run_shell(CONFIGURE_FIXTURE + "; apply_ipv4_only", flag=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_text(), CONTENT)
        self.assert_controls("1")

    def test_explicit_allow_ipv6_does_not_change_network_or_create_config(self):
        result = self.run_shell(CONFIGURE_FIXTURE + "; apply_ipv4_only", flag="0")
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
        for scope in ("default", "lo"):
            with self.subTest(scope=scope):
                missing = self.controls / scope / "disable_ipv6"
                missing.unlink()
                result = self.run_shell("apply_ipv4_only")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("control is unavailable or not writable", result.stderr)
                self.assertNotIn("configuration installed", result.stdout)
                self.assertFalse(self.config.exists(), "Persistence changed before all controls were checked")
                missing.write_text("0\n", encoding="utf-8")
                self.assert_controls("0")

    def test_persistence_failure_cleans_temporary_config(self):
        result = self.run_shell('mv(){ return 1; }; apply_ipv4_only')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot persist", result.stderr)
        self.assertFalse(self.config.exists())
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assert_controls("0")

    def test_configure_validates_and_defaults_ipv4_flag(self):
        fixture = CONFIGURE_FIXTURE + '; echo "flag=$WD_IPV4_ONLY"'
        for supplied, expected in ((None, "1"), ("0", "0"), ("1", "1"), ("2", None), ("yes", None)):
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
        scripts.mkdir(exist_ok=True)
        (self.work / "lock").mkdir(exist_ok=True)
        events = self.work / "events"
        events.write_text("", encoding="utf-8")
        # Use the real option parser and startup order through preflight. Substitute
        # only environment operations and stop before component installation.
        stub = '''set -Eeuo pipefail
require_root(){ :; }
require_debian(){ :; }
require_complete_build(){ :; }
flock(){ :; }
install(){ mkdir -p -- "${!#}"; }
configure(){ export WD_IPV4_ONLY="${WD_IPV4_ONLY:-1}" WD_DEPLOY_MODE="${WD_DEPLOY_MODE:-auto}"; }
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

    def test_cli_default_enables_ipv4_only(self):
        result, events = self.run_installer_prefix([])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events, ["ipv4:1", "save", "preflight"])

    def test_cli_allow_ipv6_skips_changes(self):
        result, events = self.run_installer_prefix(["--allow-ipv6"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(events, ["ipv4:0", "save", "preflight"])

    def test_service_mode_never_calls_network_helper(self):
        for extra in ([], ["--ipv4-only"], ["--allow-ipv6"]):
            with self.subTest(extra=extra):
                result, events = self.run_installer_prefix(["--mode", "service"] + extra)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(events, ["save", "preflight"])

    def test_allow_ipv6_does_not_undo_previous_persistent_configuration(self):
        result = self.run_shell("apply_ipv4_only")
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.config.stat().st_mtime_ns
        result = self.run_shell(CONFIGURE_FIXTURE + "; apply_ipv4_only", flag="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_text(), CONTENT)
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.assert_controls("1")

    def run_network_status(self, addresses="", routes="", failure="", gated=False):
        source = (ROOT / "webdesktop-deploy/check.sh").read_text(encoding="utf-8")
        function = "ipv4_only_status() {" + source.split("ipv4_only_status() {", 1)[1].split("unit_present()", 1)[0]
        function = function.replace("/proc/sys/net/ipv6/conf", self.shell_path(self.controls))
        commands = self.work / "ip-calls"
        commands.write_text("", encoding="utf-8")
        stub = r'''
ip(){
  echo "$*" >> "$TEST_IP_CALLS"
  if [[ "$*" == '-6 -o addr show' ]]; then
    [[ "$TEST_IP_FAILURE" != addresses ]] || return 1
    printf '%s' "$TEST_IPV6_ADDRESSES"
  elif [[ "$*" == '-6 route show' ]]; then
    [[ "$TEST_IP_FAILURE" != routes ]] || return 1
    printf '%s' "$TEST_IPV6_ROUTES"
  else return 99; fi
}
'''
        body = function + stub
        if gated:
            marker = "if [[ -f /etc/sysctl.d/90-webdesktop-ipv4-only.conf ]]; then"
            gate = marker + source.split(marker, 1)[1].split("\nfi", 1)[0] + "\nfi\n"
            gate = gate.replace("/etc/sysctl.d", self.shell_path(self.config_dir))
            body += '\ncheck(){ echo "check:$1"; shift; "$@"; };\n' + gate
        else:
            body += "\nipv4_only_status"
        env = dict(os.environ, TEST_IPV6_ADDRESSES=addresses, TEST_IPV6_ROUTES=routes,
                   TEST_IP_FAILURE=failure, TEST_IP_CALLS=self.shell_path(commands))
        result = subprocess.run([BASH, "--noprofile", "--norc", "-c", body],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result, commands.read_text()

    def disable_fixture_controls(self):
        for scope in ("all", "default", "lo"):
            (self.controls / scope / "disable_ipv6").write_text("1\n", encoding="utf-8")

    def test_network_status_requires_disabled_controls(self):
        self.disable_fixture_controls()
        for scope in ("all", "default", "lo"):
            with self.subTest(scope=scope):
                path = self.controls / scope / "disable_ipv6"
                path.write_text("0\n", encoding="utf-8")
                result, calls = self.run_network_status()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, "")
                path.write_text("1\n", encoding="utf-8")

    def test_network_status_requires_no_addresses_and_no_routes(self):
        self.disable_fixture_controls()
        for addresses, routes, expected in (("", "", 0), ("2: eth0 inet6 ::1/128", "", 1),
                                             ("", "default via fe80::1 dev eth0", 1)):
            with self.subTest(addresses=addresses, routes=routes):
                result, calls = self.run_network_status(addresses=addresses, routes=routes)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn("-6 -o addr show", calls)
                self.assertIn("-6 route show", calls)
                self.assert_controls("1")

    def test_network_status_handles_failed_ip_queries(self):
        self.disable_fixture_controls()
        for failure in ("addresses", "routes"):
            result, calls = self.run_network_status(failure=failure)
            self.assertNotEqual(result.returncode, 0)

    def test_network_status_passes_without_ipv6_kernel_support(self):
        (self.controls / "all/disable_ipv6").unlink()
        result, calls = self.run_network_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")

    def test_check_sh_only_checks_ipv4_only_when_config_is_present(self):
        result, calls = self.run_network_status(gated=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("check:", result.stdout)
        self.assertEqual(calls, "")
        self.config_dir.mkdir()
        self.config.write_text(CONTENT, encoding="utf-8")
        result, calls = self.run_network_status(gated=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("check:IPv4-only networking", result.stdout)


if __name__ == "__main__":
    unittest.main()
