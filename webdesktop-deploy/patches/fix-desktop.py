#!/usr/bin/env python3
"""Create user-level launcher overrides without editing package-owned files."""
import os
import pathlib
import re
import shlex
import sys

MARKER = "# webdesktop-fcitx-desktop-override"
COMMAND = re.compile(
    r"^(?P<prefix>(?:(?:/usr/bin/)?env(?:\s+[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|[^\s]+))*\s+)?)"
    r"(?P<command>\"[^\"]+\"|'[^']+'|[^\s]+)(?P<rest>.*)$"
)


def rewrite_exec(value):
    match = COMMAND.match(value)
    if not match:
        return value, False
    try:
        command = shlex.split(match["command"])[0]
    except (ValueError, IndexError):
        return value, False
    name = pathlib.PurePosixPath(command).name
    if name not in ("claude-desktop", "chromium"):
        return value, False
    return match["prefix"] + "/usr/local/bin/" + name + match["rest"], True


def rewrite_desktop(text):
    lines = []
    matched = False
    in_desktop = False
    for line in text.splitlines():
        if line.startswith("["):
            in_desktop = line == "[Desktop Entry]"
        if line.startswith(("Exec=", "TryExec=")):
            key, value = line.split("=", 1)
            rewritten, found = rewrite_exec(value)
            matched = matched or found
            line = key + "=" + rewritten
        if in_desktop and line == "DBusActivatable=true":
            line = "DBusActivatable=false"
        lines.append(line)
    if not matched:
        return None
    return MARKER + "\n" + "\n".join(lines) + "\n"


def install(home):
    destination = pathlib.Path(home) / ".local/share/applications"
    destination.mkdir(parents=True, exist_ok=True)
    found = 0
    for directory in ("/usr/share/applications", "/usr/local/share/applications"):
        for source in sorted(pathlib.Path(directory).glob("*.desktop")):
            if source.stat().st_size > 100000:
                continue
            result = rewrite_desktop(source.read_text(encoding="utf-8"))
            if result is None:
                continue
            target = destination / source.name
            if target.exists() and MARKER not in target.read_text(encoding="utf-8"):
                print("WARNING: Keeping custom user launcher; verify its input method wrapper: " + target.name)
                continue
            temporary = target.with_name(target.name + ".webdesktop-tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(result)
            temporary.chmod(0o644)
            os.replace(str(temporary), str(target))
            found += 1
            print("Installed Fcitx user launcher override: " + target.name)
    if not found:
        print("WARNING: No supported package launcher was overridden; verify Claude's actual desktop entry on the VPS.")


def audit(home):
    selected = {}
    for directory in (pathlib.Path("/usr/share/applications"), pathlib.Path("/usr/local/share/applications"),
                      pathlib.Path(home) / ".local/share/applications"):
        for source in directory.glob("*.desktop"):
            selected[source.name] = source
    found = 0
    failed = 0
    for name, source in sorted(selected.items()):
        in_entry = False
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.startswith("["):
                in_entry = line == "[Desktop Entry]"
            if not in_entry or not line.startswith("Exec="):
                continue
            value = line.split("=", 1)[1]
            rewritten, matched = rewrite_exec(value)
            if matched:
                found += 1
                if rewritten != value:
                    failed += 1
                    print("FAIL: launcher may bypass the Fcitx wrapper: " + name)
                else:
                    print("OK: launcher uses the Fcitx wrapper: " + name)
    if not found:
        print("FAIL: no supported application launch entry found; inspect the actual package desktop entry.")
    return 0 if found and not failed else 1


if __name__ == "__main__":
    checking = len(sys.argv) == 3 and sys.argv[1] == "--check"
    home = sys.argv[-1]
    if (len(sys.argv) != 2 and not checking) or not pathlib.Path(home).is_absolute():
        sys.exit("Usage: python3 fix-desktop.py [--check] /absolute/user/home")
    if checking:
        sys.exit(audit(home))
    install(home)
