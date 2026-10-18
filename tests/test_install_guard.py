"""Run the real installation/recovery scripts with isolated fake system services."""
import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_FOR_TESTS") or shutil.which("bash")

STUBS = r'''
source "$TEST_PRODUCTION_COMMON"
require_root() { :; }
require_debian() { :; }
configure() {
  export WD_APP_DIR="$TEST_APP_DIR" WD_USER=appuser WD_DEPLOY_MODE="$TEST_MODE"
  export WD_BRANCH=main WD_SOURCE_COMMIT="${TEST_SOURCE_COMMIT:-}"
  export WD_REPO_URL=https://github.com/isAliceYoung/fa.git
  export DEPLOY_ROOT="$TEST_TOOL_ROOT"
}
as_user() { "$@"; }
record_stage() { echo "stage $*" >> "$TEST_EVENTS"; }
install_service_unit() { echo publish-unit >> "$TEST_EVENTS"; touch "$TEST_UNIT"; }
git() {
  if [[ "$1" == check-ref-format ]]; then return 0; fi
  if [[ "$1" == -C && "$3" == remote ]]; then echo "$WD_REPO_URL"; return 0; fi
  if [[ "$1" == -C && "$3" == rev-parse ]]; then echo 1111111111111111111111111111111111111111; return 0; fi
  echo "unexpected git $*" >> "$TEST_EVENTS"; return 98
}
systemctl() {
  echo "systemctl $*" >> "$TEST_EVENTS"
  case "$1" in
    cat) [[ -f "$TEST_UNIT" ]] ;;
    is-active) [[ -f "$TEST_ACTIVE_FILE" ]] ;;
    show)
      case "$4" in
        WorkingDirectory)
          if [[ "$TEST_STALE_UNIT" == 1 && ! -f "$TEST_LOADED_FILE" ]]; then echo ''
          elif [[ "$TEST_WRONG_SERVICE" == 1 ]]; then echo /some/other/application
          else echo "$WD_APP_DIR"; fi ;;
        User) if [[ "$TEST_WRONG_USER" == 1 ]]; then echo otheruser; else echo "$WD_USER"; fi ;;
        *) echo 'ActiveState=test' ;;
      esac ;;
    start) [[ "$TEST_START_OK" == 1 ]] || return 42; touch "$TEST_ACTIVE_FILE" ;;
    daemon-reload) touch "$TEST_LOADED_FILE" ;;
    enable|reset-failed) return 0 ;;
    *) return 99 ;;
  esac
}
node() {
  echo "node $2" >> "$TEST_EVENTS"
  case "$2" in
    check-env|check-db) return 0 ;;
    schema-state) echo ready ;;
    auth-state) [[ "$TEST_AUTH_OK" == 1 ]] ;;
    *) echo unexpected-node-write >> "$TEST_EVENTS"; return 99 ;;
  esac
}
curl() { [[ "$TEST_HEALTH_OK" == 1 ]]; }
sleep() { :; }
python3() { echo unexpected-python >> "$TEST_EVENTS"; return 99; }
npm() { echo unexpected-npm >> "$TEST_EVENTS"; return 99; }
'''


@unittest.skipUnless(BASH, "Bash is required")
class InstallGuardTests(unittest.TestCase):
    def exercise(self, mode="auto", active=True, build=True, complete=True,
                 service=True, wrong_service=False, wrong_user=False,
                 health=True, auth=True, start_ok=True, source_commit="", stale_unit=False):
        with tempfile.TemporaryDirectory(prefix="wd-recovery-") as temporary:
            work = pathlib.Path(temporary)
            scripts = work / "scripts"
            app = work / "application"
            scripts.mkdir()
            for folder in (".git", "scripts", "public/novnc/core", "node_modules", ".next"):
                (app / folder).mkdir(parents=True, exist_ok=True)
            for name, content in {"package.json": "{}\n", "scripts/start.sh": "#!/bin/bash\n",
                                  "public/novnc/core/rfb.js": "// fixture\n", ".env": "KEEP=unchanged\n"}.items():
                (app / name).write_text(content, encoding="utf-8")
            if build:
                (app / ".next/BUILD_ID").write_text("preserve", encoding="utf-8")
            if not complete:
                (app / ".env").unlink()
            for name in ("install-webdesktop.sh", "deploy-service.sh"):
                shutil.copyfile(ROOT / "webdesktop-deploy/scripts" / name, scripts / name)
            (scripts / "common.sh").write_bytes(STUBS.encode("utf-8"))
            events = work / "events.log"
            events.touch()
            unit = work / "unit"
            active_file = work / "active"
            if service:
                unit.touch()
            if active:
                active_file.touch()
            before = {p.relative_to(app).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in app.rglob("*") if p.is_file()}
            env = dict(os.environ, TEST_APP_DIR=app.as_posix(), TEST_MODE=mode,
                       TEST_TOOL_ROOT=work.as_posix(), TEST_EVENTS=events.as_posix(),
                       TEST_UNIT=unit.as_posix(), TEST_ACTIVE_FILE=active_file.as_posix(),
                       TEST_PRODUCTION_COMMON=(ROOT / "webdesktop-deploy/scripts/common.sh").as_posix(),
                       TEST_WRONG_SERVICE=str(int(wrong_service)), TEST_WRONG_USER=str(int(wrong_user)),
                       TEST_HEALTH_OK=str(int(health)), TEST_AUTH_OK=str(int(auth)),
                       TEST_START_OK=str(int(start_ok)), TEST_SOURCE_COMMIT=source_commit)
            env['TEST_STALE_UNIT'] = str(int(stale_unit))
            env['TEST_LOADED_FILE'] = (work / 'manager-loaded').as_posix()
            result = subprocess.run([BASH, "--noprofile", "--norc", "-c",
                                     'export PATH="/usr/bin:/bin:$PATH"; exec bash "$1"',
                                     "wd-test", (scripts / "install-webdesktop.sh").as_posix()],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            after = {p.relative_to(app).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in app.rglob("*") if p.is_file()}
            self.assertEqual(before, after, "Existing application files changed")
            trace = events.read_text()
            for forbidden in ("systemctl stop", "systemctl restart", "unexpected-node-write", "unexpected-python", "unexpected-npm", "unexpected git"):
                self.assertNotIn(forbidden, trace)
            return result.returncode, result.stdout + result.stderr, trace

    def test_running_healthy_service_is_preserved(self):
        status, output, trace = self.exercise()
        self.assertEqual(status, 0, output)
        self.assertNotIn("systemctl start", trace)
        self.assertNotIn("publish-unit", trace)

    def test_interruption_after_build_completes_missing_service(self):
        status, output, trace = self.exercise(active=False, service=False)
        self.assertEqual(status, 0, output)
        self.assertIn("publish-unit", trace)
        self.assertIn("systemctl start", trace)
        self.assertIn("service-configured", trace)

    def test_inactive_matching_service_recovers_without_rebuild(self):
        status, output, trace = self.exercise(mode="repair", active=False)
        self.assertEqual(status, 0, output)
        self.assertIn("systemctl start", trace)
        self.assertNotIn("publish-unit", trace)

    def test_install_mode_can_resume_a_completed_build(self):
        status, output, trace = self.exercise(mode="install", active=False, service=False)
        self.assertEqual(status, 0, output)
        self.assertIn("publish-unit", trace)

    def test_interruption_before_daemon_reload_is_recovered(self):
        status, output, trace = self.exercise(active=False, stale_unit=True)
        self.assertEqual(status, 0, output)
        self.assertIn("systemctl daemon-reload", trace)
        self.assertIn("systemctl start", trace)

    def test_inactive_wrong_service_directory_is_rejected(self):
        status, output, trace = self.exercise(active=False, wrong_service=True)
        self.assertNotEqual(status, 0, output)
        self.assertIn("another application directory", output)
        self.assertNotIn("systemctl start", trace)

    def test_wrong_service_user_is_rejected(self):
        status, output, trace = self.exercise(wrong_user=True)
        self.assertNotEqual(status, 0, output)
        self.assertIn("another Linux user", output)

    def test_active_unhealthy_service_does_not_report_success(self):
        status, output, trace = self.exercise(health=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("readiness", output)
        self.assertNotIn("systemctl start", trace)

    def test_missing_login_initialization_does_not_report_success(self):
        status, output, trace = self.exercise(auth=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("readiness", output)

    def test_recovery_start_failure_reports_diagnostics(self):
        status, output, trace = self.exercise(active=False, start_ok=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("service start failed", output)
        self.assertIn("journalctl", output)

    def test_active_service_without_persistent_unit_is_rejected(self):
        status, output, trace = self.exercise(service=False)
        self.assertNotEqual(status, 0, output)
        self.assertNotIn("publish-unit", trace)

    def test_live_service_without_build_is_preserved(self):
        status, output, trace = self.exercise(build=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("refusing to modify", output)

    def test_incomplete_build_fails_without_service_mutation(self):
        status, output, trace = self.exercise(complete=False)
        self.assertNotEqual(status, 0, output)
        self.assertNotIn("systemctl start", trace)

    def test_fresh_install_requires_lockfile(self):
        status, output, trace = self.exercise(active=False, build=False, service=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("package-lock.json is required", output)

    def test_repair_without_build_is_rejected(self):
        status, output, trace = self.exercise(mode="repair", active=False, build=False, service=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("requires an existing application build", output)

    def test_pinned_source_mismatch_never_checks_out_existing_tree(self):
        status, output, trace = self.exercise(source_commit="2" * 40)
        self.assertNotEqual(status, 0, output)
        self.assertIn("does not match --commit", output)


if __name__ == "__main__":
    unittest.main()
