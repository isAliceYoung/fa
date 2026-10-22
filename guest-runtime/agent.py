#!/usr/bin/env python3
"""Minimal guest IPC service: no auth, DB, application imports, shell or subprocess."""
import argparse
import ctypes
import errno
import re
import time
import traceback
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
from secure_fs import Denied, MAX_WRITE, DownloadIncomplete
from file_ops import Files, Conflict, MAX_CHUNK

SOCKET = "/run/webdesktop-guest/guest.sock"
MAX_BODY = MAX_WRITE + 65536


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # No paths, request headers or secrets in logs.

    def json_response(self, status, value):
        self.started = True
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

    def binary_chunks(self):
        deadline = time.monotonic() + 20
        total = 0
        encoding = self.headers.get("Transfer-Encoding")
        if encoding and (encoding.lower() != "chunked" or self.headers.get("Content-Length")):
            raise ValueError("Invalid framing")
        remaining = int(self.headers.get("Content-Length", "0")) if not encoding else None
        if remaining is not None and not 0 <= remaining <= MAX_CHUNK:
            raise OverflowError()
        while remaining is None or remaining:
            if time.monotonic() > deadline:
                raise TimeoutError("Upload deadline")
            if encoding:
                line = self.rfile.readline(128)
                if not re.fullmatch(b"[0-9a-fA-F]{1,8}\r\n", line):
                    raise ValueError("Invalid chunk framing")
                size = int(line.strip(), 16)
                if size == 0:
                    if self.rfile.read(2) != b"\r\n": raise ValueError("Trailers forbidden")
                    return
            else:
                size = remaining
            if total + size > MAX_CHUNK:
                raise OverflowError()
            left = size
            while left:
                if time.monotonic() > deadline: raise TimeoutError("Upload deadline")
                chunk = self.rfile.read(min(65536, left))
                if not chunk: raise ValueError("Incomplete upload")
                left -= len(chunk); total += len(chunk)
                yield chunk
            if encoding:
                if self.rfile.read(2) != b"\r\n": raise ValueError("Invalid chunk terminator")
            else:
                remaining -= size

    def send_file(self, descriptor, info, name, zipped=False):
        with os.fdopen(descriptor, "rb") as stream:
            self.started = True
            self.send_response(200)
            self.send_header("Content-Type", "application/zip" if zipped else (mimetypes.guess_type(name)[0] or "application/octet-stream"))
            self.send_header("Content-Length", str(info.st_size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name, safe=""))
            self.send_header("Connection", "close")
            self.end_headers()
            remaining = info.st_size
            deadline = time.monotonic() + 30
            while remaining:
                if time.monotonic() > deadline: raise TimeoutError()
                chunk = stream.read(min(65536, remaining))
                if not chunk: raise ConnectionResetError("File changed")
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def dispatch(self):
        self.close_connection = True
        self.started = False
        try:
            url = urlsplit(self.path)
            query = parse_qs(url.query, keep_blank_values=True)
            allowed_query = {"dir", "name", "id", "offset", "done", "fingerprint", "size"} if url.path == "/v1/upload" else {"path", "hidden"}
            if url.scheme or url.netloc or set(query) - allowed_query or any(len(v) != 1 for v in query.values()):
                raise ValueError("Unexpected query")
            if any(key.lower() not in {"host", "connection", "content-type", "content-length", "transfer-encoding"} for key in self.headers) or any(len(self.headers.get_all(k, [])) > 1 for k in ("Content-Length", "Transfer-Encoding")):
                raise ValueError("Unexpected IPC headers")
            raw = query.get("path", ["~"])[0]
            fs = self.server.workspace
            if url.path == "/v1/upload" and self.command in ("POST", "GET", "DELETE"):
                b = {k: v[0] for k, v in query.items()}
                if self.command == "POST" and self.headers.get("Content-Type") != "application/octet-stream": raise ValueError("Binary content required")
                return self.json_response(200, fs.upload(b, self.binary_chunks() if self.command == "POST" else None, self.command == "DELETE"))
            if self.command == "GET" and url.path == "/v1/info":
                home = fs.home
                return self.json_response(200, {"user":"wdguest", "uid":fs.uid,"home":home,"desktop":home+"/Desktop","documents":home+"/Documents","downloads":home+"/Downloads","pictures":home+"/Pictures","trash":home+"/"+".local/share/Trash/files","host":socket.gethostname(),"os":"Debian 12","protocol":2})
            if self.command == "GET" and url.path == "/v1/list":
                return self.json_response(200, fs.list(raw, query.get("hidden", ["1"])[0] != "0"))
            if self.command == "GET" and url.path == "/v1/read-text":
                return self.json_response(200, fs.read_text(raw))
            if self.command == "GET" and url.path in ("/v1/read", "/v1/export"):
                if url.path == "/v1/read":
                    descriptor, info = fs.file(raw)
                    return self.send_file(descriptor, info, Path(raw).name)
                return self.send_file(*fs.export(raw))
            operations = {
                ("POST","/v1/create"): {"dir","name","kind","content"},
                ("PUT","/v1/write"): {"path","content"},
                ("POST","/v1/action"): {"path","name","moveTo","restore","copyPaths","destDir","cut","pasteFromVnc"},
                ("POST","/v1/remove"): {"path","permanent","emptyTrash"},
                ("POST","/v1/mkdir"): {"dir","name"},
            }
            allowed = operations.get((self.command,url.path))
            if allowed is None: return self.json_response(404, {"error":"Unknown guest operation"})
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type") != "application/json": raise ValueError("Fixed JSON required")
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= MAX_BODY: raise OverflowError()
            data = self.rfile.read(length)
            if len(data) != length: raise ValueError("Incomplete body")
            body = json.loads(data)
            if not isinstance(body,dict) or set(body) - allowed: raise ValueError("Unexpected body fields")
            if url.path == "/v1/create": value = fs.create(body.get("dir","~"),body.get("name"),body.get("kind"),body.get("content",""))
            elif url.path == "/v1/write": value = fs.write(body.get("path"),body.get("content"))
            elif url.path == "/v1/action": value = fs.action(body)
            elif url.path == "/v1/remove": value = fs.remove(body)
            else:
                parent = fs.relative(body.get("dir","~")); name = fs.relative(body.get("name"))
                r = name if parent == "." else parent+"/"+name
                with fs.guard(): fs.ensure_directory(r)
                value = {"path":fs.home+"/"+r}
            return self.json_response(409 if value.get("partial") else 200, value)
        except DownloadIncomplete: self.json_response(409,{"error":"Browser download is incomplete; check Chromium downloads (Ctrl+J)","code":"DOWNLOAD_INCOMPLETE"})
        except (Denied, PermissionError): self.json_response(403,{"error":"Guest permission denied"})
        except FileNotFoundError: self.json_response(404,{"error":"File not found"})
        except (Conflict,FileExistsError): self.json_response(409,{"error":"File conflict; source retained or consult operation status"})
        except OverflowError: self.json_response(413,{"error":"Guest size/time/entry limit exceeded"})
        except (ValueError,TypeError,IsADirectoryError,NotADirectoryError,KeyError): self.json_response(400,{"error":"Invalid guest operation"})
        except OSError as error:
            if self.started: self.close_connection=True; return
            print('Guest filesystem error errno='+str(error.errno),file=sys.stderr)
            status = 507 if error.errno in (errno.ENOSPC,errno.EDQUOT) else 504 if isinstance(error,TimeoutError) else 503
            self.json_response(status,{"error":"Guest storage full" if status == 507 else "Guest operation unavailable"})

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch


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
        frames=[Path(f.filename).name+':'+str(f.lineno)+':'+f.name for f in traceback.extract_tb(sys.exc_info()[2])]
        print("Guest request failed: " + sys.exc_info()[0].__name__+' '+','.join(frames), file=sys.stderr)

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
    if os.getresuid() != (guest.pw_uid,) * 3 or os.getresgid() != (guest.pw_gid,) * 3:
        raise RuntimeError("Real, effective and saved identities must all be guest")
    if ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("NoNewPrivileges is required")
    status = Path("/proc/self/status").read_text()
    if any(int(line.split()[1], 16) for line in status.splitlines() if line.startswith(("CapEff:", "CapPrm:", "CapAmb:"))):
        raise RuntimeError("Guest cannot hold capabilities")
    # Agent does not trust caller environment, even if started outside systemd.
    os.environ.clear()
    os.environ.update({"HOME": guest.pw_dir, "USER": "wdguest", "LOGNAME": "wdguest", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    os.umask(0o007)  # Data is shared only with wdterm's fixed wdguest group.
    workspace = Files(guest.pw_dir, guest.pw_uid)
    for name in ("Desktop", "Documents", "Downloads", "Pictures", ".local/share/Trash/files", ".local/share/Trash/info"):
        workspace.ensure_directory(name)
    inherited = socket.socket(fileno=args.listen_fd)
    if inherited.family != socket.AF_UNIX or inherited.getsockname() != SOCKET or not inherited.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
        raise RuntimeError("Protected inherited Unix listener required")
    inherited.set_inheritable(False)
    Server(inherited, workspace, service.pw_uid).serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()
