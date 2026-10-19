"""Run PostgreSQL initialization recovery with fake packages/services and real config edits."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_FOR_TESTS") or shutil.which("bash")
DEFAULT_CONFIG = "#listen_addresses = 'localhost' # default binding\nshared_buffers = 128MB\n"
IPV4_CONFIG = "listen_addresses = '127.0.0.1'\nshared_buffers = 128MB\n"
CUSTOM_CONFIG = "listen_addresses = '*'\nshared_buffers = 256MB\n"

STUBS = r'''
source "$TEST_COMMON"
require_root(){ :; }
require_debian(){ :; }
configure(){ :; }
install(){ mkdir -p -- "${!#}"; }
sleep(){ :; }
apt_install(){
  echo apt-install >> "$TEST_EVENTS"
  if [[ ! -e "$TEST_CONF" ]]; then
    [[ -f "$TEST_PENDING" ]] || { echo missing-marker-before-apt >> "$TEST_EVENTS"; return 98; }
    mkdir -p -- "$(dirname -- "$TEST_CONF")"
    printf '%s' "$TEST_CREATED_CONFIG" > "$TEST_CONF"
  fi
  return "$TEST_APT_RC"
}
systemctl(){ echo "systemctl $*" >> "$TEST_EVENTS"; return "$TEST_SERVICE_RC"; }
pg_isready(){
  echo "pg_isready $*" >> "$TEST_EVENTS"
  [[ "$*" == '-h 127.0.0.1 -p 5432' && -f "$TEST_READY" ]]
}
ip(){ [[ "$TEST_LOOPBACK" == 1 ]] && echo 'inet 127.0.0.1/8 scope host lo'; }
python3(){
  if command -v cygpath >/dev/null; then "$TEST_PYTHON" - "$(cygpath -w "$2")"
  else "$TEST_PYTHON" "$@"; fi
}
pg_ctlcluster(){
  echo "pg_ctlcluster $*" >> "$TEST_EVENTS"
  case "$3" in
    status) [[ -f "$TEST_RUNNING" ]] ;;
    start|restart)
      [[ "$TEST_ACTION_RC" == 0 ]] || return "$TEST_ACTION_RC"
      grep -q "listen_addresses.*127.0.0.1" "$TEST_CONF" || return 99
      touch "$TEST_RUNNING"
      if [[ "$TEST_READY_AFTER_ACTION" == 1 ]]; then touch "$TEST_READY"; fi
      ;;
    *) return 99 ;;
  esac
}
pg_lsclusters(){ echo '15 main 5432 down postgres /fixture/data /fixture/log'; }
ss(){ :; }
journalctl(){ :; }
'''


@unittest.skipUnless(BASH, "Bash is required")
class PostgreSQLRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wd-postgres-recovery-")
        self.addCleanup(self.temporary.cleanup)
        self.work = pathlib.Path(self.temporary.name)
        self.shell_root = subprocess.check_output(
            [BASH, "--noprofile", "--norc", "-c", 'cd -- "$1"; pwd -P',
             "wd-test", self.work.as_posix()], text=True).strip()
        self.conf = self.work / "postgresql/postgresql.conf"
        self.pending = self.work / "state/postgresql-15-main-initializing"
        self.events = self.work / "events"
        self.events.touch()
        self.ready = self.work / "ready"
        self.running = self.work / "running"
        source = (ROOT / "webdesktop-deploy/scripts/install-postgres.sh").read_text(encoding="utf-8")
        source = source.replace("/etc/postgresql/15/main/postgresql.conf", self.shell_path(self.conf))
        source = source.replace("/var/lib/webdesktop-deploy", self.shell_path(self.pending.parent))
        self.script = self.work / "install-postgres.sh"
        self.script.write_bytes(source.encode("utf-8"))
        (self.work / "common.sh").write_bytes(STUBS.encode("utf-8"))

    def shell_path(self, path):
        return self.shell_root + "/" + path.relative_to(self.work).as_posix()

    def write_cluster(self, content=DEFAULT_CONFIG, pending=False):
        self.conf.parent.mkdir(exist_ok=True)
        self.conf.write_bytes(content.encode("utf-8"))
        if pending:
            self.pending.parent.mkdir(exist_ok=True)
            self.pending.touch()

    def run_installer(self, service_rc=0, action_rc=0, apt_rc=0, ready_after=True,
                      created_config=DEFAULT_CONFIG, loopback=True):
        self.events.write_text("", encoding="utf-8")
        env = dict(os.environ, TEST_COMMON=(ROOT / "webdesktop-deploy/scripts/common.sh").as_posix(),
                   TEST_CONF=self.shell_path(self.conf), TEST_PENDING=self.shell_path(self.pending),
                   TEST_EVENTS=self.shell_path(self.events), TEST_READY=self.shell_path(self.ready),
                   TEST_RUNNING=self.shell_path(self.running), TEST_SERVICE_RC=str(service_rc),
                   TEST_ACTION_RC=str(action_rc), TEST_APT_RC=str(apt_rc),
                   TEST_READY_AFTER_ACTION=str(int(ready_after)), TEST_CREATED_CONFIG=created_config,
                   TEST_PYTHON=pathlib.Path(sys.executable).as_posix(), TEST_LOOPBACK=str(int(loopback)))
        result = subprocess.run([BASH, "--noprofile", "--norc", "-c",
                                 'export PATH="/usr/bin:/bin:$PATH"; exec bash "$1"',
                                 "wd-test", self.script.as_posix()], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        trace = self.events.read_text()
        self.assertNotIn("missing-marker-before-apt", trace)
        self.assertNotIn("pg_isready -h /var/run/postgresql", trace)
        self.assertNotIn("systemctl is-active", trace)
        self.assertEqual(list(self.conf.parent.glob("*.webdesktop.*")), [], "Backup cleanup failed")
        return result.returncode, result.stdout + result.stderr, trace

    def test_initial_service_failure_and_down_cluster_are_repaired_with_start(self):
        status, output, trace = self.run_installer(service_rc=1)
        self.assertEqual(status, 0, output)
        self.assertIn("pg_ctlcluster 15 main start", trace)
        self.assertNotIn("pg_ctlcluster 15 main restart", trace)
        self.assertEqual(self.conf.read_text(), IPV4_CONFIG)
        self.assertFalse(self.pending.exists())

    def test_failed_first_run_keeps_marker_and_next_run_recovers_existing_config(self):
        status, output, trace = self.run_installer(service_rc=1, action_rc=42)
        self.assertNotEqual(status, 0, output)
        self.assertTrue(self.pending.is_file())
        self.assertEqual(self.conf.read_text(), DEFAULT_CONFIG)
        self.assertIn("pg_ctlcluster 15 main start", trace)
        status, output, trace = self.run_installer()
        self.assertEqual(status, 0, output)
        self.assertIn("pg_ctlcluster 15 main start", trace)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.conf.read_text(), IPV4_CONFIG)

    def test_existing_custom_cluster_without_marker_is_not_modified(self):
        self.write_cluster(CUSTOM_CONFIG)
        status, output, trace = self.run_installer()
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.conf.read_text(), CUSTOM_CONFIG)
        self.assertFalse(self.pending.exists())
        self.assertNotIn("pg_ctlcluster", trace)

    def test_existing_default_cluster_without_marker_is_not_claimed(self):
        self.write_cluster()
        status, output, trace = self.run_installer()
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.conf.read_text(), DEFAULT_CONFIG)
        self.assertFalse(self.pending.exists())
        self.assertNotIn("pg_ctlcluster", trace)

    def test_initializing_custom_config_is_refused_without_restart(self):
        self.write_cluster(CUSTOM_CONFIG, pending=True)
        self.running.touch()
        status, output, trace = self.run_installer()
        self.assertNotEqual(status, 0, output)
        self.assertIn("listen_addresses is custom", output)
        self.assertEqual(self.conf.read_text(), CUSTOM_CONFIG)
        self.assertTrue(self.pending.exists())
        self.assertNotIn("pg_ctlcluster", trace)

    def test_running_initializing_cluster_uses_restart(self):
        self.write_cluster(pending=True)
        self.running.touch()
        status, output, trace = self.run_installer()
        self.assertEqual(status, 0, output)
        self.assertIn("pg_ctlcluster 15 main restart", trace)
        self.assertNotIn("pg_ctlcluster 15 main start\n", trace)
        self.assertFalse(self.pending.exists())

    def test_resume_after_configuration_write_does_not_rewrite_target(self):
        self.write_cluster(IPV4_CONFIG, pending=True)
        before = self.conf.stat().st_mtime_ns
        status, output, trace = self.run_installer()
        self.assertEqual(status, 0, output)
        self.assertEqual(self.conf.stat().st_mtime_ns, before)
        self.assertEqual(self.conf.read_text(), IPV4_CONFIG)
        self.assertFalse(self.pending.exists())

    def test_already_healthy_cluster_is_preserved_and_marker_removed(self):
        self.write_cluster(CUSTOM_CONFIG, pending=True)
        self.ready.touch()
        status, output, trace = self.run_installer(service_rc=1)
        self.assertEqual(status, 0, output)
        self.assertEqual(self.conf.read_text(), CUSTOM_CONFIG)
        self.assertFalse(self.pending.exists())
        self.assertNotIn("pg_ctlcluster", trace)

    def test_package_failure_keeps_initialization_marker(self):
        status, output, trace = self.run_installer(apt_rc=100)
        self.assertEqual(status, 100, output)
        self.assertTrue(self.pending.exists())
        self.assertNotIn("systemctl", trace)

    def test_successful_start_without_ipv4_readiness_rolls_back_and_keeps_marker(self):
        status, output, trace = self.run_installer(ready_after=False)
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.conf.read_text(), DEFAULT_CONFIG)
        self.assertTrue(self.pending.exists())
        self.assertIn("restoring original configuration", output)

    def test_missing_loopback_does_not_edit_or_start_cluster(self):
        status, output, trace = self.run_installer(loopback=False)
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.conf.read_text(), DEFAULT_CONFIG)
        self.assertTrue(self.pending.exists())
        self.assertNotIn("pg_ctlcluster", trace)

    def test_directory_at_marker_path_is_rejected_before_package_install(self):
        self.pending.mkdir(parents=True)
        status, output, trace = self.run_installer()
        self.assertNotEqual(status, 0, output)
        self.assertIn("Invalid PostgreSQL initialization marker", output)
        self.assertNotIn("apt-install", trace)


if __name__ == "__main__":
    unittest.main()
