"""Guest-only filesystem operations. Linux openat2, no symlink or local fallback."""
import ctypes
import errno
import os
import platform
import stat
from pathlib import Path

MAX_FILE = 100 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
MAX_WRITE = 5 * 1024 * 1024


class Denied(Exception):
    pass


class OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


class Workspace:
    def __init__(self, home, uid):
        if platform.machine() not in ("x86_64", "aarch64"):
            raise RuntimeError("Supported Linux openat2 architecture required")
        self.home, self.uid = str(Path(home)), uid
        self.root = os.open(self.home, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        if os.fstat(self.root).st_uid != uid:
            raise RuntimeError("Workspace must belong to guest")
        probe = self.open(".", os.O_RDONLY | os.O_DIRECTORY)
        os.close(probe)  # ENOSYS/EINVAL abort startup; no weaker fallback.

    def relative(self, raw):
        if not isinstance(raw, str) or len(raw) > 4096 or "\0" in raw or "\\" in raw:
            raise Denied("Invalid path")
        if raw in ("", "~", self.home):
            return "."
        if raw.startswith("~/"):
            raw = raw[2:]
        elif raw.startswith(self.home + "/"):
            raw = raw[len(self.home) + 1:]
        elif raw.startswith("/"):
            raise Denied("Outside guest workspace")
        parts = raw.rstrip("/").split("/")
        if not parts or any(value in ("", ".", "..") for value in parts):
            raise Denied("Path traversal is not allowed")
        return "/".join(parts)

    def open(self, relative, flags, mode=0):
        how = OpenHow(flags | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, mode, 0x02 | 0x04 | 0x08)
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.syscall(ctypes.c_long(437), ctypes.c_int(self.root), ctypes.c_char_p(os.fsencode(relative)), ctypes.byref(how), ctypes.sizeof(how))
        if result < 0:
            error = ctypes.get_errno()
            if error in (errno.ELOOP, errno.EXDEV, errno.EACCES, errno.EPERM):
                raise Denied("Path is not accessible to guest")
            raise OSError(error, os.strerror(error))
        return result

    def regular(self, descriptor):
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_nlink != 1:
            raise Denied("Only single-link guest-owned regular files are allowed")
        if info.st_size > MAX_FILE:
            raise OverflowError("File exceeds 100 MiB")
        return info

    def file(self, raw):
        descriptor = self.open(self.relative(raw), os.O_RDONLY)
        try:
            return descriptor, self.regular(descriptor)
        except Exception:
            os.close(descriptor)
            raise

    def list(self, raw, hidden=True):
        relative = self.relative(raw)
        descriptor = self.open(relative, os.O_RDONLY | os.O_DIRECTORY)
        try:
            names = os.listdir(descriptor)
            if len(names) > 4000:
                raise OverflowError("Directory exceeds 4000 entries")
            rows = []
            absolute = self.home if relative == "." else self.home + "/" + relative
            for name in names:
                if not hidden and name.startswith("."):
                    continue
                try:
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    continue  # Do not resolve links, sockets, devices or FIFOs.
                rows.append({"name": name, "path": absolute + "/" + name, "kind": "folder" if stat.S_ISDIR(info.st_mode) else "file", "link": False,
                             "size": info.st_size, "mtime": info.st_mtime * 1000, "mode": format(info.st_mode & 0o777, "03o"), "hidden": name.startswith(".")})
            rows.sort(key=lambda value: (value["kind"] != "folder", value["name"]))
            return {"path": absolute, "parent": None if relative == "." else str(Path(absolute).parent), "entries": rows}
        finally:
            os.close(descriptor)

    def read_text(self, raw):
        descriptor, info = self.file(raw)
        with os.fdopen(descriptor, "rb") as stream:
            too_large = info.st_size > MAX_TEXT
            data = b"" if too_large else stream.read(MAX_TEXT + 1)
        if len(data) > MAX_TEXT:
            too_large, data = True, b""
        binary = b"\0" in data[:8000]
        return {"path": self.home + "/" + self.relative(raw), "name": Path(raw).name, "size": info.st_size,
                "mtime": info.st_mtime * 1000, "tooLarge": too_large, "binary": binary, "content": "" if binary else data.decode("utf-8", errors="replace")}

    def create(self, raw, name, kind, content=""):
        if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\0")):
            raise Denied("Invalid filename")
        parent = self.relative(raw)
        relative = name if parent == "." else parent + "/" + name
        if kind == "folder":
            parent_fd = self.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        elif kind == "file":
            data = self.data(content)
            descriptor = self.open(relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        else:
            raise ValueError("Invalid file kind")
        return {"path": self.home + "/" + relative}

    def data(self, content):
        if not isinstance(content, str):
            raise ValueError("Text content required")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE:
            raise OverflowError("Write exceeds 5 MiB")
        return data

    def write(self, raw, content):
        data = self.data(content)
        descriptor = self.open(self.relative(raw), os.O_WRONLY)
        try:
            self.regular(descriptor)  # Validate ownership/link count BEFORE truncate.
            os.ftruncate(descriptor, 0)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
        finally:
            os.close(descriptor)
        return {"ok": True}
