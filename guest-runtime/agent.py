#!/usr/bin/env python3
"""Minimal guest IPC service: no auth, DB, application imports, shell or subprocess."""
import argparse
import ctypes
import http.server
import json
import mimetypes
import os
from pathlib import Path
import pwd
import socket
import socketserver
import struct
import sys
import threading
from urllib.parse import parse_qs, quote, urlsplit

# -I excludes the script directory; only the root-owned minimal runtime is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secure_fs import Denied, Workspace, MAX_WRITE

SOCKET = "/run/webdesktop-guest/guest.sock"
MAX_BODY = MAX_WRITE + 65536


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # No paths, request headers or secrets in logs.

    def json_response(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(data) > 2 * 1024 * 1024:
            status, data = 413, b'{"error":"Response too large"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def dispatch(self):
        self.close_connection = True
        try:
            url = urlsplit(self.path)
            query = parse_qs(url.query, keep_blank_values=True)
            if url.scheme or url.netloc or set(query) - {"path", "hidden"} or any(key.lower() not in {"host", "connection", "content-type", "content-length"} for key in self.headers):
                raise ValueError("Unexpected IPC fields")
            raw = query.get("path", ["~"])[0]
            fs = self.server.workspace
            if self.command == "GET" and url.path == "/v1/info":
                home = fs.home
                return self.json_response(200, {"user": "wdguest", "uid": fs.uid, "home": home, "desktop": home + "/Desktop", "documents": home + "/Documents", "downloads": home + "/Downloads", "pictures": home + "/Pictures", "trash": home + "/.local/share/Trash/files", "host": socket.gethostname(), "os": "Debian 12", "protocol": 1})
            if self.command == "GET" and url.path == "/v1/list":
                return self.json_response(200, fs.list(raw, query.get("hidden", ["1"])[0] != "0"))
            if self.command == "GET" and url.path == "/v1/read-text":
                return self.json_response(200, fs.read_text(raw))
            if self.command == "GET" and url.path == "/v1/read":
                descriptor, info = fs.file(raw)
                with os.fdopen(descriptor, "rb") as stream:
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(raw)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(info.st_size))
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(Path(raw).name, safe=""))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    remaining = info.st_size
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            if (self.command, url.path) not in (("POST", "/v1/create"), ("PUT", "/v1/write")):
                return self.json_response(404, {"error": "Unknown guest operation"})
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type") != "application/json":
                return self.json_response(400, {"error": "Fixed-size JSON protocol required"})
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > MAX_BODY:
                return self.json_response(413, {"error": "Request too large"})
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete body")
            body = json.loads(data)
            if not isinstance(body, dict):
                raise ValueError("Object required")
            allowed = {"dir", "name", "kind", "content"} if self.command == "POST" else {"path", "content"}
            if set(body) - allowed:
                raise ValueError("Unexpected IPC fields")
            if self.command == "POST":
                value = fs.create(body.get("dir", "~"), body.get("name"), body.get("kind"), body.get("content", ""))
            else:
                value = fs.write(body.get("path"), body.get("content"))
            return self.json_response(200, value)
        except (Denied, PermissionError):
            self.json_response(403, {"error": "Guest permission denied"})
        except FileNotFoundError:
            self.json_response(404, {"error": "File not found"})
        except FileExistsError:
            self.json_response(409, {"error": "File exists"})
        except OverflowError:
            self.json_response(413, {"error": "Guest operation exceeds size limit"})
        except (ValueError, TypeError, IsADirectoryError, NotADirectoryError):
            self.json_response(400, {"error": "Invalid guest operation"})
        except OSError:
            self.json_response(503, {"error": "Guest filesystem unavailable"})

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True
    block_on_close = False

    def __init__(self, inherited, workspace, service_uid):
        super().__init__(SOCKET, Handler, bind_and_activate=False)
        self.socket.close()
        self.socket = inherited
        self.server_name, self.server_port = "guest-ipc", 0
        self.workspace, self.service_uid = workspace, service_uid
        self.slots = threading.BoundedSemaphore(4)

    def get_request(self):
        while True:
            connection, address = self.socket.accept()
            _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid == self.service_uid:
                connection.settimeout(10)
                return connection, address
            connection.close()

    def handle_error(self, request, address):
        pass  # No traceback containing request data; client receives connection failure.

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.sendall(b"HTTP/1.1 503 Busy\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-fd", type=int, default=3)
    args = parser.parse_args()
    guest, service = pwd.getpwnam("wdguest"), pwd.getpwnam("wdsvc")
    if os.getuid() != guest.pw_uid or not guest.pw_uid or not service.pw_uid or guest.pw_uid == service.pw_uid or guest.pw_gid == service.pw_gid:
        raise RuntimeError("Distinct unprivileged guest/service identities required")
    if os.getgid() != guest.pw_gid or set(os.getgroups()) - {guest.pw_gid}:
        raise RuntimeError("Guest must have only its private group")
    if ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("NoNewPrivileges is required")
    status = Path("/proc/self/status").read_text()
    if any(int(line.split()[1], 16) for line in status.splitlines() if line.startswith(("CapEff:", "CapPrm:", "CapAmb:"))):
        raise RuntimeError("Guest cannot hold capabilities")
    # Agent does not trust caller environment, even if started outside systemd.
    os.environ.clear()
    os.environ.update({"HOME": guest.pw_dir, "USER": "wdguest", "LOGNAME": "wdguest", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    os.umask(0o077)
    for name in ("Desktop", "Documents", "Downloads", "Pictures", ".local/share/Trash/files", ".local/share/Trash/info"):
        Path(guest.pw_dir, name).mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace = Workspace(guest.pw_dir, guest.pw_uid)
    inherited = socket.socket(fileno=args.listen_fd)
    if inherited.family != socket.AF_UNIX or inherited.getsockname() != SOCKET or not inherited.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
        raise RuntimeError("Protected inherited Unix listener required")
    inherited.set_inheritable(False)
    Server(inherited, workspace, service.pw_uid).serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()
