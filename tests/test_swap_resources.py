"""Exercise the real Swap installer with fake hardware and isolated file paths.

No APT, host Swap, fstab or large allocations are touched by these tests.
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

HARDWARE = r'''
awk() {
  if [[ "${!#}" == /proc/meminfo ]]; then echo "$((TEST_RAM_MIB * 1024))"
  else command awk "$@"; fi
}
df() {
  local available="$TEST_ROOT_FREE"
  [[ "${!#}" == / ]] || available="$TEST_APP_FREE"
  printf 'Filesystem MiB Used Avail Use Mounted\n/dev/test 40000 1000 %s 3 /\n' "$available"
}
swapon() {
  [[ "${TEST_SWAPON_FAIL:-0}" == 0 ]] || return 1
  case "$1" in
    --show=NAME) command awk '{print $1}' "$TEST_AREAS" ;;
    --show=SIZE) command awk '{print $2}' "$TEST_AREAS" ;;
    --show) cat "$TEST_AREAS" ;;
    *)
      [[ "$1" == "$TEST_SWAPFILE" ]] || return 97
      echo activate >> "$TEST_EVENTS"
      printf '%s %s\n' "$TEST_SWAPFILE" "$(($(cat "$TEST_NOMINAL") - 4096))" >> "$TEST_AREAS"
      ;;
  esac
}
'''

INSTALL_STUBS = HARDWARE + r'''
require_root() { :; }
require_debian() { :; }
configure() {
  WD_HOME="$TEST_HOME"; WD_APP_DIR="$TEST_HOME/app"; WD_SWAP_GB="$TEST_MODE"
}
apt-get() { :; }
apt_install() { :; }
preflight_resources() { :; }
nproc() { echo 1; }
getconf() { echo 4096; }
stat() {
  if [[ "${!#}" == / || "$TEST_SEPARATE" == 0 ]]; then echo 1; else echo 2; fi
}
findmnt() { echo ext4; }
dd() {
  local value count=0
  for value in "$@"; do [[ "$value" != count=* ]] || count="${value#count=}"; done
  echo "allocate:$count" >> "$TEST_EVENTS"
  echo "$((count * 1048576))" > "$TEST_NOMINAL"
}
mkswap() { echo format >> "$TEST_EVENTS"; }
swapoff() { echo FORBIDDEN-swapoff >> "$TEST_EVENTS"; return 98; }
'''


@unittest.skipUnless(BASH, "Bash is required")
class SwapInstallerTests(unittest.TestCase):
    def exercise(self, ram=960, disk=20480, existing_mib=0, app_disk=None,
                 separate=False, repeats=1, mode="auto", inactive=False, areas=1):
        with tempfile.TemporaryDirectory(prefix="wd-swap-resources-") as temporary:
            work = pathlib.Path(temporary)
            home = work / "home"
            (home / "app").mkdir(parents=True)
            swapfile = work / "swapfile"
            fstab = work / "fstab"
            fstab.write_text("# isolated fixture\n", encoding="utf-8")
            active = work / "areas"
            records = []
            if existing_mib:
                # Each active area loses a header page; production code must account for all of them.
                swapfile.write_bytes(b"preserve-existing-swap-data")
                for index in range(areas):
                    name = swapfile if index == 0 else work / ("other-swap-" + str(index))
                    records.append("{} {}\n".format(name.as_posix(), existing_mib * 1048576 // areas - 4096))
            if inactive:
                swapfile.write_bytes(b"preserve-inactive-swap-data")
            active.write_text("".join(records), encoding="utf-8")
            original_hash = hashlib.sha256(swapfile.read_bytes()).hexdigest() if swapfile.exists() else None
            events = work / "events"
            events.touch()
            script = work / "install-system.sh"
            source = (ROOT / "webdesktop-deploy/scripts/install-system.sh").read_text(encoding="utf-8")
            # Redirect only the host filesystem sinks; keep the real algorithm and control flow.
            source = source.replace("/swapfile", swapfile.as_posix()).replace("/etc/fstab", fstab.as_posix())
            script.write_bytes(source.encode("utf-8"))
            production = (ROOT / "webdesktop-deploy/scripts/common.sh").as_posix()
            (work / "common.sh").write_bytes(('source "$TEST_COMMON"\n' + INSTALL_STUBS).encode("utf-8"))
            env = dict(os.environ, TEST_COMMON=production, TEST_RAM_MIB=str(ram),
                       TEST_ROOT_FREE=str(disk), TEST_APP_FREE=str(disk if app_disk is None else app_disk),
                       TEST_HOME=home.as_posix(), TEST_MODE=mode, TEST_SEPARATE=str(int(separate)),
                       TEST_AREAS=active.as_posix(), TEST_EVENTS=events.as_posix(),
                       TEST_SWAPFILE=swapfile.as_posix(), TEST_NOMINAL=(work / "nominal").as_posix())
            outputs = []
            previous_events = ""
            for run in range(repeats):
                result = subprocess.run([BASH, "--noprofile", "--norc", "-c",
                                         'export PATH="/usr/bin:/bin:$PATH"; exec bash "$1"',
                                         "wd-test", script.as_posix()], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                outputs.append(result.stdout + result.stderr)
                trace = events.read_text(encoding="utf-8")
                self.assertNotIn("FORBIDDEN", trace)
                if original_hash:
                    self.assertEqual(hashlib.sha256(swapfile.read_bytes()).hexdigest(), original_hash)
                    self.assertNotIn("format", trace)
                    self.assertNotIn("allocate:", trace)
                if run:
                    self.assertEqual(previous_events, trace, "Repeat run changed active Swap")
                previous_events = trace
            nominal_mib = sum((int(line.split()[1]) + 4096) // 1048576
                              for line in active.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(fstab.read_text(encoding="utf-8").count(" none swap sw 0 0"), 1)
            return result.returncode, "\n".join(outputs), trace, nominal_mib

    def test_low_ram_with_7_6_gib_disk_selects_one_gib_and_rerun_preserves_it(self):
        status, output, trace, capacity = self.exercise(disk=7782, repeats=2)
        self.assertEqual(status, 0, output)
        self.assertIn("Auto Swap target: 1 GiB", output)
        self.assertEqual(trace.count("allocate:1024"), 1)
        self.assertEqual(capacity, 1024)

    def test_low_ram_with_20_gib_disk_selects_four_gib(self):
        status, output, trace, capacity = self.exercise(repeats=2)
        self.assertEqual(status, 0, output)
        self.assertIn("Auto Swap target: 4 GiB", output)
        self.assertEqual(trace.count("format"), 1)
        self.assertEqual(capacity, 4096)

    def test_existing_one_gib_on_small_disk_is_preserved(self):
        status, output, trace, capacity = self.exercise(disk=3482, existing_mib=1024, repeats=2)
        self.assertEqual(status, 0, output)
        self.assertEqual(capacity, 1024)

    def test_existing_four_gib_on_small_disk_is_preserved(self):
        status, output, trace, capacity = self.exercise(disk=3482, existing_mib=4096, repeats=2)
        self.assertEqual(status, 0, output)
        self.assertEqual(capacity, 4096)

    def test_zero_target_with_satisfied_budget_still_warns_low_memory(self):
        status, output, trace, capacity = self.exercise(disk=6500)
        self.assertEqual(status, 0, output)
        self.assertIn("Auto Swap target: 0 GiB", output)
        self.assertNotIn("Cannot preserve", output)
        self.assertIn("disk budget prevents new Swap", output)
        self.assertIn("OOM", output)
        self.assertEqual(capacity, 0)
        self.assertEqual(trace, "")

    def test_eight_gib_ram_does_not_allocate_swap(self):
        status, output, trace, capacity = self.exercise(ram=8192)
        self.assertEqual(status, 0, output)
        self.assertIn("Auto Swap target: 0 GiB", output)
        self.assertNotIn("disk budget prevents new Swap", output)
        self.assertEqual(trace, "")

    def test_medium_ram_uses_two_gib_as_maximum(self):
        status, output, trace, capacity = self.exercise(ram=4096)
        self.assertEqual(status, 0, output)
        self.assertIn("Auto Swap target: 2 GiB", output)
        self.assertEqual(capacity, 2048)

    def test_exact_budget_boundary_allows_one_gib(self):
        status, output, trace, capacity = self.exercise(disk=7168)
        self.assertEqual(status, 0, output)
        self.assertEqual(capacity, 1024)

    def test_separate_small_application_partition_blocks_allocation(self):
        status, output, trace, capacity = self.exercise(app_disk=3000, separate=True)
        self.assertEqual(status, 0, output)
        self.assertIn("Cannot preserve", output)
        self.assertIn("disk budget prevents new Swap", output)
        self.assertEqual(trace, "")

    def test_separate_app_partition_does_not_pay_for_root_swap(self):
        status, output, trace, capacity = self.exercise(app_disk=6144, separate=True)
        self.assertEqual(status, 0, output)
        self.assertEqual(capacity, 4096)

    def test_existing_smaller_swapfile_is_not_expanded_automatically(self):
        status, output, trace, capacity = self.exercise(existing_mib=1024, repeats=2)
        self.assertEqual(status, 0, output)
        self.assertIn("Preserving existing Swap without resizing", output)
        self.assertEqual(capacity, 1024)

    def test_explicit_target_cannot_overwrite_existing_swapfile(self):
        status, output, trace, capacity = self.exercise(existing_mib=1024, mode="4")
        self.assertNotEqual(status, 0, output)
        self.assertIn("Inspect it manually", output)
        self.assertEqual(capacity, 1024)

    def test_inactive_existing_swapfile_is_preserved(self):
        status, output, trace, capacity = self.exercise(inactive=True)
        self.assertEqual(status, 0, output)
        self.assertEqual(capacity, 0)
        self.assertEqual(trace, "")

    def test_multiple_swap_headers_do_not_trigger_reformat(self):
        status, output, trace, capacity = self.exercise(existing_mib=4096, areas=2, repeats=2)
        self.assertEqual(status, 0, output)
        self.assertIn("Swap target met", output)
        self.assertEqual(capacity, 4096)


@unittest.skipUnless(BASH, "Bash is required")
class ResourceStatusTests(unittest.TestCase):
    def run_status(self, function, root_disk=20480, app_disk=20480, ram=960,
                   swap=False, swapon_fail=False):
        with tempfile.TemporaryDirectory(prefix="wd-resource-status-") as temporary:
            work = pathlib.Path(temporary)
            active = work / "areas"
            active.write_text("/dev/test 1073737728\n" if swap else "", encoding="utf-8")
            body = ('export PATH="/usr/bin:/bin:$PATH"; source "$TEST_COMMON"; ' + HARDWARE +
                    '\nWD_HOME="$TEST_HOME"; WD_APP_DIR="$TEST_HOME/not-created/nested"; ' + function)
            result = subprocess.run([BASH, "--noprofile", "--norc", "-c", body],
                                    env=dict(os.environ, TEST_COMMON=(ROOT / "webdesktop-deploy/scripts/common.sh").as_posix(),
                                             TEST_ROOT_FREE=str(root_disk), TEST_APP_FREE=str(app_disk),
                                             TEST_RAM_MIB=str(ram), TEST_AREAS=active.as_posix(),
                                             TEST_HOME=work.as_posix(), TEST_SWAPON_FAIL=str(int(swapon_fail))),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return result.returncode, result.stdout + result.stderr

    def test_zero_free_disk_is_failure(self):
        status, output = self.run_status("disk_capacity_status", root_disk=0)
        self.assertEqual(status, 1, output)
        self.assertIn("No free disk space", output)

    def test_separate_full_application_disk_is_failure(self):
        status, output = self.run_status("disk_capacity_status", app_disk=0)
        self.assertEqual(status, 1, output)

    def test_low_free_disk_is_warning(self):
        status, output = self.run_status("disk_capacity_status", app_disk=1024)
        self.assertEqual(status, 2, output)
        self.assertIn("Less than 2 GiB", output)

    def test_invalid_disk_reading_is_failure(self):
        status, output = self.run_status("disk_capacity_status", root_disk="unknown")
        self.assertEqual(status, 1, output)
        self.assertIn("Cannot detect free disk space", output)

    def test_full_root_is_not_downgraded_to_warning_by_low_app_disk(self):
        status, output = self.run_status("disk_capacity_status", root_disk=0, app_disk=1024)
        self.assertEqual(status, 1, output)

    def test_two_gib_free_disk_is_ok(self):
        status, output = self.run_status("disk_capacity_status", root_disk=2048, app_disk=2048)
        self.assertEqual(status, 0, output)

    def test_missing_swap_on_low_ram_is_warning(self):
        status, output = self.run_status("swap_capacity_status")
        self.assertEqual(status, 2, output)
        self.assertIn("No active Swap", output)

    def test_missing_swap_on_large_ram_is_optional(self):
        status, output = self.run_status("swap_capacity_status", ram=8192)
        self.assertEqual(status, 0, output)
        self.assertIn("optional", output)

    def test_active_swap_is_ok(self):
        status, output = self.run_status("swap_capacity_status", swap=True)
        self.assertEqual(status, 0, output)
        self.assertIn("Active Swap areas: 1", output)

    def test_failed_swap_query_is_failure(self):
        status, output = self.run_status("swap_capacity_status", swapon_fail=True)
        self.assertEqual(status, 1, output)

    def test_missing_app_path_uses_existing_parent(self):
        status, output = self.run_status('[[ "$(application_disk_path)" == "$WD_HOME" ]]')
        self.assertEqual(status, 0, output)

    def test_check_sh_reports_real_capacity_status_and_counts_only_failures(self):
        source = (ROOT / "webdesktop-deploy/check.sh").read_text(encoding="utf-8")
        wrapper = "resource_check() {" + source.split("resource_check() {", 1)[1].split("has_bin()", 1)[0]
        for code, label, failures in ((0, "OK", 0), (2, "WARN", 0), (1, "FAIL", 1)):
            with self.subTest(code=code):
                status, output = self.run_status(
                    wrapper + '\nfailures=0; probe(){ return ' + str(code) +
                    '; }; resource_check capacity probe; echo "failures=$failures"')
                self.assertEqual(status, 0, output)
                self.assertIn(label + " capacity" if label == "WARN" or label == "FAIL" else "OK   capacity", output)
                self.assertIn("failures=" + str(failures), output)


if __name__ == "__main__":
    unittest.main()
