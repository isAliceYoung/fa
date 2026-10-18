#!/usr/bin/env python3
"""Apply only the three build signature fixes; preserve existing implementations."""
import pathlib
import re
import sys


def patch(path):
    original = path.read_text(encoding="utf-8")
    updated = original
    signatures = {
        "checkRemote": "_check?: boolean",
        "applyUpdate": "_options?: { forceBuild?: boolean }",
        "updateDeployConfig": "_config?: { autoUpdate?: boolean; restartOnUpdate?: boolean; intervalMin?: number }",
    }
    for name, arguments in signatures.items():
        declaration = r"(export\s+(?:async\s+)?function\s+" + name + r"\s*)\(\s*\)"
        updated, count = re.subn(declaration, lambda m: m[1] + "(" + arguments + ")", updated)
        if count > 1:
            raise ValueError("Multiple declarations found for " + name)
        if not count:
            existing = re.search(r"export\s+(?:async\s+)?function\s+" + name + r"\s*\((.*?)\)\s*:", updated, re.S)
            expected = arguments.split("?")[0]
            if not existing or expected not in existing[1]:
                raise ValueError("Unrecognized signature for " + name + "; inspect before patching")
    if updated == original:
        print("deploy.ts: build signatures already patched")
        return
    backup = path.with_name(path.name + ".webdesktop-original")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    print("deploy.ts: applied three build signature fixes")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 fix-deploy.py /path/to/webdesktop/src/lib/deploy.ts")
    try:
        patch(pathlib.Path(sys.argv[1]))
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
