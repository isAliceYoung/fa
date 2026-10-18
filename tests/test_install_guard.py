"""Exercise the actual installer with temporary files and mocked system commands.

No systemd, package installation, database operation or real service is used.
"""
import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_FOR_TESTS") or shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required")
class InstallGuardTests(unittest.TestCase):
    def exercise(self, mode="auto", active=True, build=True, complete=True, wrong_service=False):
        with tempfile.TemporaryDirectory(prefix="wd-installer-") as temporary:
            work = pathlib.Path(temporary)
            scripts = work / "scripts"
            app = work / "application"
            scripts.mkdir()
            for folder in (".git", "scripts", "public/novnc/core", "node_modules", ".next"):
                (app / folder).mkdir(parents=True, exist_ok=True)
            for name, content in {
                "package.json": "{}\n",
                "scripts/start.sh": "#!/bin/bash\n",
                "public/novnc/core/rfb.js": "// fixture\n",
                ".env": "KEEP_THIS=unchanged\n",
            }.items():
                (app / name).write_text(content, encoding="utf-8")
            if build:
                (app / ".next/BUILD_ID").write_text("preserve-this-build", encoding="utf-8")
            if not complete:
                (app / ".env").unlink()
            installer = scripts / "install-webdesktop.sh"
            shutil.copyfile(ROOT / "webdesktop-deploy/scripts/install-webdesktop.sh", installer)
            common = '''set -Eeuo pipefail
require_root() { :; }
require_debian() { :; }
configure() {
  export WD_APP_DIR="$TEST_APP_DIR"
  export WD_DEPLOY_MODE="$TEST_MODE"
  export WD_BRANCH=main
  export WD_REPO_URL=https://github.com/isAliceYoung/fa.git
}
log() { printf '%s\\n' "$*"; }
die() { printf 'ERROR: %s\\n' "$*" >&2; exit 1; }
as_user() { "$@"; }
git() {
  if [[ "$1" == check-ref-format ]]; then return 0; fi
  if [[ "$1" == -C && "$3" == remote ]]; then printf '%s\\n' "$WD_REPO_URL"; return 0; fi
  echo "unexpected git command" >> "$TEST_EVENTS"; return 98
}
systemctl() {
  printf 'systemctl %s\\n' "$*" >> "$TEST_EVENTS"
  case "$1" in
    is-active) [[ "$TEST_ACTIVE" == 1 ]] ;;
    show) if [[ "$TEST_WRONG_SERVICE" == 1 ]]; then echo /some/other/application; else echo "$WD_APP_DIR"; fi ;;
    *) return 99 ;;
  esac
}
node() { echo unexpected-node >> "$TEST_EVENTS"; return 99; }
python3() { echo unexpected-python >> "$TEST_EVENTS"; return 99; }
npm() { echo unexpected-npm >> "$TEST_EVENTS"; return 99; }
'''
            (scripts / "common.sh").write_bytes(common.encode("utf-8"))
            events = work / "events.log"
            events.write_text("", encoding="utf-8")
            before = {
                file.relative_to(app).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                for file in app.rglob("*") if file.is_file()
            }
            env = dict(os.environ, TEST_APP_DIR=app.as_posix(), TEST_MODE=mode,
                       TEST_ACTIVE="1" if active else "0", TEST_EVENTS=events.as_posix(),
                       TEST_WRONG_SERVICE="1" if wrong_service else "0")
            result = subprocess.run([BASH, "--noprofile", "--norc", "-c",
                                     'export PATH="/usr/bin:/bin:$PATH"; exec bash "$1"',
                                     "wd-test", installer.as_posix()], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            after = {
                file.relative_to(app).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                for file in app.rglob("*") if file.is_file()
            }
            self.assertEqual(before, after, "Existing application files changed")
            trace = events.read_text(encoding="utf-8")
            for forbidden in ("systemctl stop", "systemctl restart", "unexpected-node", "unexpected-python", "unexpected-npm"):
                self.assertNotIn(forbidden, trace)
            return result.returncode, result.stdout + result.stderr

    def test_repeated_install_keeps_running_build(self):
        status, output = self.exercise()
        self.assertEqual(status, 0, output)
        self.assertIn("Existing application preserved", output)

    def test_repair_keeps_inactive_build_stopped(self):
        status, output = self.exercise(mode="repair", active=False)
        self.assertEqual(status, 0, output)

    def test_first_install_mode_rejects_existing_build(self):
        status, output = self.exercise(mode="install")
        self.assertNotEqual(status, 0, output)
        self.assertIn("first installation only", output)

    def test_unknown_service_is_not_touched(self):
        status, output = self.exercise(wrong_service=True)
        self.assertNotEqual(status, 0, output)
        self.assertIn("another installation", output)

    def test_live_service_without_build_is_not_touched(self):
        status, output = self.exercise(build=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("refusing to modify", output)

    def test_incomplete_existing_build_fails_without_modification(self):
        status, output = self.exercise(complete=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("incomplete", output)

    def test_fresh_install_requires_committed_lockfile(self):
        status, output = self.exercise(active=False, build=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("package-lock.json is required", output)

    def test_repair_mode_rejects_uninstalled_application(self):
        status, output = self.exercise(mode="repair", active=False, build=False)
        self.assertNotEqual(status, 0, output)
        self.assertIn("requires an existing application build", output)


if __name__ == "__main__":
    unittest.main()
