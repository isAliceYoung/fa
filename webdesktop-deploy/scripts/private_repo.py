#!/usr/bin/env python3
"""Root-only private source acquisition. No application installation or migration.

Only the Python standard library is used. Never source application configuration,
inherit an SSH agent, run Git hooks, execute checked-out code, or forward tool stderr.
"""
import argparse
import base64
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time


# Published by GitHub, verified against the official fingerprint on 2026-10-19.
# Rotation requires an independently reviewed update, never automatic acceptance.
HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
HOST_FINGERPRINT = "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
STATE_ROOTS = (Path("/root"), Path("/etc/webdesktop-deploy"))
SOURCE_ROOT = Path("/opt/webdesktop")
MIN_CLONE_FREE = 1024 * 1024 * 1024
CLONE_RESERVE = 512 * 1024 * 1024
MAX_STAGE_BYTES = 512 * 1024 * 1024


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise Failure(code, message)


def repository(value):
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)", value)
    if not match:
        fail("REPOSITORY", "Use a GitHub HTTPS or git@github.com SSH URL without credentials, query or options.")
    owner, name = match.groups()
    name = name[:-4] if name.endswith(".git") else name
    if not name or owner in (".", "..") or name in (".", ".."):
        fail("REPOSITORY", "Invalid repository identity.")
    return "git@github.com:{}/{}.git".format(owner, name)


def location(value):
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or any(p in (".", "..") for p in value.split("/")):
        fail("PATH", "Use a normalized absolute path without spaces, dot components or shell syntax.")
    return Path(value)


def validate_options(args):
    args.repo = repository(args.repo)
    args.state_dir, args.source_dir = location(args.state_dir), location(args.source_dir)
    if not any(root in args.state_dir.parents for root in STATE_ROOTS):
        fail("PATH", "Deploy credentials must be in a dedicated directory below /root or /etc/webdesktop-deploy.")
    if args.state_dir == Path("/root/.ssh"):
        fail("PATH", "Use a dedicated directory, not the administrator's general .ssh directory.")
    if SOURCE_ROOT not in args.source_dir.parents:
        fail("PATH", "Private source must be below /opt/webdesktop; guest home checkouts are forbidden.")
    if args.source_dir == args.state_dir or args.source_dir in args.state_dir.parents or args.state_dir in args.source_dir.parents:
        fail("PATH", "Source and credentials must be separate directories.")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", args.branch):
        fail("BRANCH", "Invalid branch.")
    if args.commit and not re.fullmatch(r"[0-9a-fA-F]{40}", args.commit):
        fail("COMMIT", "Commit must be a full 40-character SHA.")
    args.commit = args.commit.lower()


def require_platform():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        fail("ROOT_REQUIRED", "Run this source bootstrap as root, never as the service or guest user.")
    release = Path("/etc/os-release").read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in release.splitlines() if "=" in line and not line.startswith("#"))
    if values.get("ID", "").strip('"') != "debian" or values.get("VERSION_ID", "").strip('"') != "12":
        fail("PLATFORM", "This bootstrap targets Debian 12.")


class PrivateSource:
    def __init__(self, state, source, repo, branch="main", commit="", runner=None):
        self.state, self.source = Path(state), Path(source)
        self.repo, self.branch, self.commit = repository(repo), branch, commit.lower()
        self.runner = runner or subprocess.run
        self.key = self.state / "deploy_key"
        self.clone_stage = None

    def check_node(self, path, directory=False, private=False):
        info = path.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode):
            fail("OWNERSHIP", "Protected paths must be root-owned and must not be symbolic links.")
        if directory != stat.S_ISDIR(info.st_mode) or (not directory and not stat.S_ISREG(info.st_mode)):
            fail("FILE_TYPE", "Unexpected protected path type.")
        mask = 0o077 if private else 0o022
        if info.st_mode & mask or (not directory and info.st_nlink != 1):
            fail("PERMISSIONS", "Protected paths have unsafe permissions or multiple hard links; no automatic ownership changes made.")

    def directory(self, path, create=False, private=True):
        # Check every ancestor before creating anything. Never follow a guest link.
        for node in reversed((path,) + tuple(path.parents)):
            if node == Path(node.anchor):
                continue
            if not node.exists() and not node.is_symlink():
                if not create:
                    fail("MISSING", "Protected directory is absent; run prepare first.")
                node.mkdir(mode=0o700)
            self.check_node(node, directory=True, private=(node == path and private))

    def write_new(self, path, data):
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    @contextlib.contextmanager
    def locked(self, create=False):
        import fcntl
        self.directory(self.state, create=create)
        lock = self.state / "bootstrap.lock"
        descriptor = os.open(str(lock), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            self.check_node(lock, private=True)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("LOCKED", "Another source bootstrap is running.")
            yield
        finally:
            os.close(descriptor)

    def ssh_config(self):
        return "\n".join([
            "Host github.com", "    HostName github.com", "    User git",
            "    IdentityFile " + self.key.as_posix(), "    IdentitiesOnly yes",
            "    IdentityAgent none", "    BatchMode yes", "    PasswordAuthentication no",
            "    KbdInteractiveAuthentication no", "    StrictHostKeyChecking yes",
            "    HostKeyAlgorithms ssh-ed25519", "    UpdateHostKeys no",
            "    UserKnownHostsFile " + (self.state / "known_hosts").as_posix(),
            "    GlobalKnownHostsFile /dev/null", "    ForwardAgent no",
            "    ProxyCommand none", "    ProxyJump none", "    PermitLocalCommand no",
            "    ConnectTimeout 15", "    ConnectionAttempts 1", "    LogLevel ERROR", "",
        ])

    def environment(self):
        # Build a fresh environment, not a blacklist of inherited secrets.
        return {
            "PATH": "/usr/bin:/bin", "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ALLOW_PROTOCOL": "ssh", "GIT_SSH_VARIANT": "ssh",
            "GIT_SSH_COMMAND": shlex.join(["/usr/bin/ssh", "-F", (self.state / "ssh_config").as_posix()]),
        }

    def run(self, argv, timeout=120):
        try:
            runner = self.monitored_clone if self.clone_stage and ("clone" in argv or "checkout" in argv) and self.runner is subprocess.run else self.runner
            result = runner(argv, env=self.environment(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 encoding="utf-8", timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            fail("TIMEOUT", "Source operation timed out; existing keys and source were preserved.")
        except OSError:
            fail("TOOL", "Required tool unavailable; use bootstrap.sh --install-tools prepare on Debian 12.")
        if result.returncode:
            detail = (result.stderr or "").lower()
            if "ls-remote" in argv and result.returncode == 2:
                fail("BRANCH", "Requested branch was not found; check --branch before retrying. No source was published.")
            if "host key verification failed" in detail or "host identification has changed" in detail:
                fail("HOST_KEY", "GitHub SSH host verification failed. Stop and independently verify official host keys; do not disable checking.")
            if "permission denied (publickey)" in detail or "repository not found" in detail or "could not read from remote repository" in detail:
                fail("AUTHORIZATION", "Repository access denied. Add this VPS public key to the intended repository Deploy Keys, leave Allow write access unchecked, then rerun check. No application installation started.")
            if "could not resolve hostname" in detail or "connection refused" in detail or "network is unreachable" in detail:
                fail("NETWORK", "GitHub SSH is unavailable; check DNS and outbound TCP 22, then retry. No credentials were replaced.")
            fail("COMMAND", "Source operation failed (exit {}). Tool output is hidden to avoid credential leaks.".format(result.returncode))
        return result.stdout.strip()

    def clone_budget(self):
        if any(entry.name.startswith(".source-") for entry in self.source.parent.iterdir()):
            fail("STALE_STAGE", "A retained .source-* entry requires administrator review. No new staging directory was created; no unknown data was deleted.")
        if shutil.disk_usage(self.source.parent).free < MIN_CLONE_FREE:
            fail("DISK_SPACE", "Source acquisition requires at least 1 GiB free, preserving a 512 MiB reserve. No source was changed.")

    def stage_budget(self):
        if shutil.disk_usage(self.source.parent).free < CLONE_RESERVE:
            return "DISK_SPACE"
        size, entries = 0, 0
        for directory, folders, files in os.walk(self.clone_stage, followlinks=False):
            for name in folders + files:
                entries += 1
                size += (Path(directory) / name).lstat().st_size
                if size > MAX_STAGE_BYTES or entries > 100000:
                    return "SOURCE_LIMIT"
        return None

    def monitored_clone(self, argv, timeout, **kwargs):
        """Stop the entire Git/SSH group before it consumes the disk reserve."""
        process = subprocess.Popen(argv, start_new_session=True, **{key: value for key, value in kwargs.items() if key != "check"})
        deadline = time.monotonic() + timeout
        try:
            while True:
                violation = self.stage_budget()
                if violation:
                    fail(violation, "Source acquisition exceeded the disk reserve or 512 MiB source budget; staging retained for review.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()

    def git(self, *args):
        return self.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                         "-c", "credential.helper=", "-c", "protocol.file.allow=never", *map(str, args)], timeout=600)

    def branch_valid(self):
        self.git("check-ref-format", "--branch", self.branch)

    def validate_identity(self):
        self.check_node(self.key, private=True)
        public = self.key.with_suffix(".pub")
        self.check_node(public, private=True)
        derived = self.run(["/usr/bin/ssh-keygen", "-y", "-P", "", "-f", str(self.key)]).split()
        saved = public.read_text(encoding="utf-8").split()
        if len(derived) < 2 or derived[0] != "ssh-ed25519" or saved[:2] != derived[:2]:
            fail("KEY_PAIR", "Deploy key is invalid or its public key does not match; existing files were not overwritten.")
        return " ".join(saved)

    def fixed_file(self, name, expected, create=False):
        path = self.state / name
        if not path.exists() and not path.is_symlink() and create:
            self.write_new(path, expected)
        self.check_node(path, private=True)
        if path.read_text(encoding="utf-8") != expected:
            fail("CONFIG_CHANGED", "Existing {} differs from the trusted bootstrap configuration; preserved for review.".format(name))

    def trusted_config(self, create=False):
        actual = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(HOST_KEY.split()[2])).digest()).decode().rstrip("=")
        if actual != HOST_FINGERPRINT:
            fail("HOST_PIN", "Bundled GitHub host key and independently verified fingerprint disagree.")
        self.fixed_file("known_hosts", HOST_KEY, create)
        self.fixed_file("ssh_config", self.ssh_config(), create)
        self.fixed_file("repository.json", json.dumps({"schema": 1, "repository": self.repo}, sort_keys=True) + "\n", create)

    def prepare(self):
        self.branch_valid()
        with self.locked(create=True):
            # Establish/validate the repository and host identity before generating keys.
            self.trusted_config(create=True)
            public = self.key.with_suffix(".pub")
            if not self.key.exists() and not self.key.is_symlink():
                if public.exists() or public.is_symlink():
                    fail("KEY_PAIR", "A public key exists without its private key; refusing to replace it.")
                stage = Path(tempfile.mkdtemp(prefix=".key-", dir=str(self.state)))
                staged_key = stage / "deploy_key"
                self.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "webdesktop-read-only-deploy", "-f", str(staged_key)])
                # Keep interrupted generation in the protected staging directory.
                os.chmod(staged_key, 0o600)
                os.chmod(staged_key.with_suffix(".pub"), 0o600)
                os.link(staged_key, self.key)
                staged_key.unlink()
                os.link(staged_key.with_suffix(".pub"), public)
                staged_key.with_suffix(".pub").unlink()
                stage.rmdir()
            elif not public.exists() and not public.is_symlink():
                self.check_node(self.key, private=True)
                derived = self.run(["/usr/bin/ssh-keygen", "-y", "-P", "", "-f", str(self.key)])
                self.write_new(public, derived + "\n")
            return self.validate_identity()

    def verify_local(self):
        self.directory(self.state)
        self.trusted_config()
        self.validate_identity()
        self.branch_valid()

    def authorized(self):
        output = self.git("ls-remote", "--exit-code", "--refs", self.repo, "refs/heads/" + self.branch)
        fields = output.split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]) or fields[1] != "refs/heads/" + self.branch:
            fail("BRANCH", "The requested branch is absent or returned an unexpected identity.")
        return fields[0]

    def existing(self):
        self.directory(self.source)
        self.check_node(self.source / ".git", directory=True, private=True)
        manifest = self.source / ".git" / "webdesktop-source.json"
        if not manifest.exists() and not manifest.is_symlink():
            fail("EXISTING_SOURCE", "Existing source lacks a bootstrap manifest; no adoption or migration attempted.")
        self.check_node(manifest, private=True)
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            fail("EXISTING_SOURCE", "Existing source has no valid bootstrap manifest; no adoption or migration attempted.")
        if record.get("schema") != 1 or record.get("repository") != self.repo or record.get("branch") != self.branch:
            fail("EXISTING_SOURCE", "Existing source belongs to another repository/branch; no checkout, fetch or reset performed.")
        if repository(self.git("-C", self.source, "remote", "get-url", "origin")) != self.repo:
            fail("EXISTING_SOURCE", "Existing source origin differs; preserved unchanged.")
        head = self.git("-C", self.source, "rev-parse", "HEAD")
        if head != record.get("commit") or (self.commit and head != self.commit):
            fail("EXISTING_SOURCE", "Existing commit differs; no checkout, fetch or reset performed.")
        dirty = bool(self.git("-C", self.source, "status", "--porcelain", "--untracked-files=normal"))
        return {"commit": head, "existing": True, "dirty": dirty}

    def publish(self, stage):
        # Linux rename() alone can replace an existing empty directory.
        if sys.platform == "linux":
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            if rename(-100, os.fsencode(stage), -100, os.fsencode(self.source), 1) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
        else:
            os.rename(stage, self.source)  # Windows refuses an existing destination.

    def clone(self):
        with self.locked():
            self.verify_local()
            if self.source.exists() or self.source.is_symlink():
                return self.existing()  # Offline reuse; never fetch or overwrite.
            self.directory(self.source.parent, create=True)
            self.clone_budget()
            expected_head = self.authorized()
            stage = Path(tempfile.mkdtemp(prefix=".source-", dir=str(self.source.parent)))
            self.clone_stage = stage
            try:
                self.git("clone", "--no-local", "--no-checkout", "--single-branch", "--branch", self.branch, "--", self.repo, stage)
                if self.stage_budget():
                    fail("SOURCE_LIMIT", "Source budget exceeded; staging retained for review.")
                # No application code, hooks or dependency scripts are executed.
                branch_head = self.git("-C", stage, "rev-parse", "refs/remotes/origin/" + self.branch)
                if branch_head != expected_head:
                    fail("REMOTE_CHANGED", "Remote branch changed during acquisition; retry after reviewing the preserved staging checkout.")
                selected = self.commit or branch_head
                self.git("-C", stage, "cat-file", "-e", selected + "^{commit}")
                self.git("-C", stage, "merge-base", "--is-ancestor", selected, "refs/remotes/origin/" + self.branch)
                self.git("-C", stage, "checkout", "--detach", selected)
                actual = self.git("-C", stage, "rev-parse", "HEAD")
                if actual != selected:
                    fail("COMMIT", "Acquired checkout does not match the requested commit.")
                os.chmod(stage / ".git", 0o700)
                self.write_new(stage / ".git" / "webdesktop-source.json", json.dumps({"schema": 1, "repository": self.repo, "branch": self.branch, "commit": actual}, sort_keys=True) + "\n")
                self.publish(stage)
                return {"commit": actual, "existing": False, "dirty": False}
            except Exception:
                # Never delete a pre-existing checkout, user file or failed clone.
                print("Incomplete source is protected and retained at {}. Review before retrying; it was not published.".format(stage), file=sys.stderr)
                raise
            finally:
                self.clone_stage = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase-two root-only private source bootstrap; no app install or migration.")
    parser.add_argument("action", choices=("prepare", "check", "clone"))
    parser.add_argument("--repo", default="git@github.com:isAliceYoung/fa.git")
    parser.add_argument("--state-dir", default="/root/.ssh/webdesktop-fa")
    parser.add_argument("--source-dir", default="/opt/webdesktop/source")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    validate_options(args)
    require_platform()
    os.umask(0o077)
    source = PrivateSource(args.state_dir, args.source_dir, args.repo, args.branch, args.commit)
    if args.action == "prepare":
        public = source.prepare()
        print("Public deploy key (private key is never printed):\n" + public)
        print("Add this public key at https://github.com/{}/settings/keys".format(args.repo.split(":", 1)[1][:-4]))
        print("Leave Allow write access UNCHECKED. Read-only status must be reviewed in GitHub; read access alone cannot prove it.")
        print("Next: rerun this bootstrap with check, then clone. No application installation has started.")
    elif args.action == "check":
        with source.locked():
            source.verify_local()
            head = source.authorized()
        print("Repository read authorization and pinned GitHub SSH host verified; branch commit: " + head)
    else:
        result = source.clone()
        print("Protected source ready: {} (existing={}, dirty={}). No application code executed.".format(result["commit"], result["existing"], result["dirty"]))
        print("STOP: phase three/four isolation and deployment are not implemented; do not run this checkout as the guest.")


if __name__ == "__main__":
    try:
        main()
    except Failure as error:
        print("ERROR [{}]: {}".format(error.code, error), file=sys.stderr)
        sys.exit(1)
    except (OSError, UnicodeError):
        print("ERROR [FILESYSTEM]: protected operation failed; existing source and credentials were not replaced. Inspect ownership, permissions, space and interrupted staging files.", file=sys.stderr)
        sys.exit(1)
