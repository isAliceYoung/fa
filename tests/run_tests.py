#!/usr/bin/env python3
"""Discover a real Bash on Windows and report skips separately from passes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


def find_bash():
    choices = [os.environ.get("BASH_FOR_TESTS"), shutil.which("bash")]
    git = shutil.which("git")
    if os.name == "nt" and git:
        root = Path(git).resolve().parent.parent
        choices.extend([str(root / "bin/bash.exe"), str(root / "usr/bin/bash.exe")])
    for candidate in choices:
        if not candidate:
            continue
        try:
            result = subprocess.run([candidate, "--noprofile", "--norc", "-c", 'test -n "$BASH_VERSION"'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if result.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError("A real Bash is required. Install Git for Windows or use Linux; no tests were run.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="Write machine-readable result summary")
    parser.add_argument("--allow-skips", action="store_true", help="Report skips but do not fail on them")
    args = parser.parse_args()
    os.environ["BASH_FOR_TESTS"] = find_bash()
    if os.name == "nt":
        # Git Bash's system-file links need no Windows developer mode/admin rights.
        # These exercise shell symlink rejection; not Linux DAC permission tests.
        settings = [v for v in os.environ.get("MSYS", "").split() if not v.startswith("winsymlinks")]
        os.environ["MSYS"] = " ".join(settings + ["winsymlinks:sys"])
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    print("Bash: " + os.environ["BASH_FOR_TESTS"], flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py"))
    summary = {
        "total": result.testsRun, "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped) - len(result.expectedFailures) - len(result.unexpectedSuccesses),
        "failed": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "not_executed": ["Debian 12 full installation/systemd/GUI acceptance", "Production VPS tests (not authorized)"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() and (args.allow_skips or not result.skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
