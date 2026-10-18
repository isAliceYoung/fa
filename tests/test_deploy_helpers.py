import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_FOR_TESTS") or shutil.which("bash")
NODE = shutil.which("node")
spec = importlib.util.spec_from_file_location("desktop_fix", ROOT / "webdesktop-deploy/patches/fix-desktop.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopEntryTests(unittest.TestCase):
    def test_absolute_launcher_keeps_arguments_and_field_codes(self):
        value = '"/opt/Claude Desktop/claude-desktop" --flag "%U"'
        self.assertEqual(desktop.rewrite_exec(value), ('/usr/local/bin/claude-desktop --flag "%U"', True))

    def test_env_prefix_is_preserved(self):
        value = 'env EXAMPLE="two words" /usr/bin/claude-desktop %u'
        self.assertEqual(desktop.rewrite_exec(value), ('env EXAMPLE="two words" /usr/local/bin/claude-desktop %u', True))

    def test_unrelated_app_is_not_overridden(self):
        self.assertIsNone(desktop.rewrite_desktop('[Desktop Entry]\nExec=calculator\nDBusActivatable=true\n'))

    def test_dbus_and_action_entries_route_through_wrapper(self):
        result = desktop.rewrite_desktop('[Desktop Entry]\nExec=/usr/bin/claude-desktop %U\nDBusActivatable=true\n[Desktop Action New]\nExec=claude-desktop --new-window\n')
        self.assertIn('DBusActivatable=false', result)
        self.assertEqual(result.count('Exec=/usr/local/bin/claude-desktop'), 2)


@unittest.skipUnless(NODE, "Node.js is required")
class DatabaseDiagnosticTests(unittest.TestCase):
    def exercise(self, code):
        with tempfile.TemporaryDirectory(prefix="wd-db-errors-") as temporary:
            app = pathlib.Path(temporary)
            (app / "node_modules/pg").mkdir(parents=True)
            (app / "package.json").write_text('{}', encoding="utf-8")
            secret = 'PRIVATE_PASSWORD_MUST_NOT_APPEAR'
            (app / ".env").write_text(
                'DATABASE_URL=postgresql://user:' + secret + '@127.0.0.1:5432/database\n'
                'ADMIN_USER=admin\nADMIN_PASSWORD=seed\nDESKTOP_PASSWORD=desktop\nADMIN_PATH=ops-test\nAUTH_KEY=key\n', encoding="utf-8")
            (app / "node_modules/pg/index.js").write_text(
                'exports.Client=class { async connect(){ const e=new Error("PRIVATE_PASSWORD_MUST_NOT_APPEAR SELECT secret FROM table");'
                'e.code=process.env.CASE_CODE; throw e; } async end(){} };', encoding="utf-8")
            result = subprocess.run([NODE, str(ROOT / "webdesktop-deploy/scripts/bootstrap.cjs"), "check-db", str(app)],
                                    env=dict(os.environ, CASE_CODE=code), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(secret, output)
            self.assertNotIn('postgresql://', output)
            self.assertNotIn('SELECT secret', output)
            return output

    def test_authentication_failure_is_classified_without_secrets(self):
        self.assertIn('authentication failed', self.exercise('28P01'))

    def test_missing_database_is_classified_without_secrets(self):
        self.assertIn('database does not exist', self.exercise('3D000'))

    def test_stopped_database_is_classified_without_secrets(self):
        self.assertIn('connection refused', self.exercise('ECONNREFUSED'))

    def test_untrusted_error_code_is_not_printed(self):
        output = self.exercise('PRIVATE_PASSWORD_MUST_NOT_APPEAR\n')
        self.assertIn('[UNKNOWN]', output)


@unittest.skipUnless(BASH, "Bash is required")
class ResourcePreflightTests(unittest.TestCase):
    def run_shell(self, body):
        source = (ROOT / "webdesktop-deploy/scripts/common.sh").as_posix()
        env = dict(os.environ, TEST_COMMON=source)
        return subprocess.run([BASH, "--noprofile", "--norc", "-c",
                               'export PATH="/usr/bin:/bin:$PATH"; source "$TEST_COMMON"; ' + body],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_disk_warning_is_independent_of_disabled_swap(self):
        result = self.run_shell('WD_HOME=/; WD_SWAP_GB=0; df(){ printf "Filesystem MiB Used Avail Use Mounted\\n/dev/test 10000 9000 1000 90 /\\n"; }; check_local_port(){ :; }; preflight_resources')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Less than 8 GiB', result.stdout)

    def test_sufficient_disk_does_not_warn(self):
        result = self.run_shell('WD_HOME=/; df(){ printf "Filesystem MiB Used Avail Use Mounted\\n/dev/test 50000 1000 49000 2 /\\n"; }; check_local_port(){ :; }; preflight_resources')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('WARNING', result.stdout)

    def test_foreign_listener_is_rejected_without_killing(self):
        result = self.run_shell('ss(){ echo "LISTEN 0 100 127.0.0.1:3000 0.0.0.0:* users:node"; }; systemctl(){ return 1; }; check_local_port')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no WebDesktop service', result.stderr)

    def test_free_port_is_accepted(self):
        result = self.run_shell('ss(){ :; }; check_local_port')
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(BASH, "Bash is required")
class ModuleReportingTests(unittest.TestCase):
    def run_modules(self, fail=False):
        with tempfile.TemporaryDirectory(prefix="wd-module-errors-") as temporary:
            work = pathlib.Path(temporary)
            scripts = work / "scripts"
            scripts.mkdir()
            shutil.copyfile(ROOT / "webdesktop-deploy/scripts/run-modules.sh", scripts / "run-modules.sh")
            (scripts / "common.sh").write_bytes(b'set -Eeuo pipefail\nDEPLOY_ROOT="$TEST_TOOL_ROOT"\nrequire_root(){ :; }\nrequire_debian(){ :; }\nconfigure(){ :; }\nlog(){ echo "$*"; }\ndie(){ echo "$*"; exit 1; }\nrecord_stage(){ echo "stage $*"; }\n')
            modules = ('install-system', 'install-nodejs', 'install-postgres', 'install-gui', 'install-claude', 'install-fcitx5', 'install-webdesktop')
            for name in modules:
                status = 37 if fail and name == 'install-claude' else 0
                (scripts / (name + '.sh')).write_bytes(('echo executed:' + name + '\nexit ' + str(status) + '\n').encode())
            result = subprocess.run([BASH, '--noprofile', '--norc', '-c',
                                     'export PATH="/usr/bin:/bin:$PATH"; exec bash "$1"', 'wd-test', (scripts / 'run-modules.sh').as_posix()],
                                    env=dict(os.environ, TEST_TOOL_ROOT=work.as_posix(), LOG_FILE='/tmp/example-install.log'),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return result.returncode, result.stdout + result.stderr

    def test_failing_module_and_exit_code_are_reported(self):
        status, output = self.run_modules(fail=True)
        self.assertEqual(status, 37, output)
        self.assertIn('FAILED: install-claude', output)
        self.assertIn('Exit code: 37', output)
        self.assertIn('/tmp/example-install.log', output)
        self.assertNotIn('executed:install-fcitx5', output)

    def test_success_reaches_last_module(self):
        status, output = self.run_modules()
        self.assertEqual(status, 0, output)
        self.assertIn('SUCCESS: install-webdesktop', output)


@unittest.skipUnless(BASH, "Bash is required")
class FcitxWrapperTests(unittest.TestCase):
    def test_generated_wrapper_reports_start_failure_and_continues(self):
        installer = (ROOT / 'webdesktop-deploy/scripts/install-fcitx5.sh').read_text(encoding='utf-8')
        match = re.search(r'  cat > "\$target" <<EOF\n(.*?)\nEOF', installer, re.S)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory(prefix='wd-fcitx-wrapper-') as temporary:
            work = pathlib.Path(temporary)
            target = work / 'chromium-wrapper.sh'
            render = 'export PATH="/usr/bin:/bin:$PATH"\ntarget="$1"\napp=chromium\ncat > "$target" <<EOF\n' + match[1] + '\nEOF\n'
            result = subprocess.run([BASH, '--noprofile', '--norc', '-c', render, 'wd-test', target.as_posix()],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            syntax = subprocess.run([BASH, '-n', target.as_posix()], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            content = target.read_text(encoding='utf-8')
            self.assertIn('exec /usr/bin/chromium "$@"', content)
            # Replace only the final external application launch in this temporary fixture.
            target.write_bytes(content.replace('exec /usr/bin/chromium "$@"', 'echo application-started').encode('utf-8'))
            exercise = ('export PATH="/usr/bin:/bin:$PATH"; '
                        'export XDG_STATE_HOME="$(mktemp -d /tmp/wd-wrapper-state.XXXXXXXX)"; '
                        '[[ "$XDG_STATE_HOME" == /tmp/wd-wrapper-state.* ]] || exit 1; '
                        "trap 'rm -rf -- \"$XDG_STATE_HOME\"' EXIT; "
                        'fcitx5-remote(){ echo 0; }; fcitx5(){ echo simulated-fcitx-error; return 1; }; '
                        'source "$1"; for file in "$XDG_STATE_HOME"/webdesktop/*.log; do cat "$file"; done')
            result = subprocess.run([BASH, '--noprofile', '--norc', '-c', exercise, 'wd-test', target.as_posix()],
                                    env=dict(os.environ, DISPLAY=':99'),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Fcitx5 failed to start', result.stderr)
            self.assertIn('application-started', result.stdout)
            self.assertIn('simulated-fcitx-error', result.stdout)


if __name__ == '__main__':
    unittest.main()
