"""Disposable VM HTTPS origin; never installed with the application."""
import hashlib
import http.server
import json
import os
from pathlib import Path
import pwd
import socket
import ssl
import sys

if os.getuid()!=0 or socket.gethostname()!='wd-lab' or sys.argv[1:]!=['--disposable-vm'] or not Path('/var/tmp/wd-lab-ready').is_file():
    raise SystemExit('Explicit disposable VM required')
DATA=(b'WebDesktop Chromium download probe\n'*8192)
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/':
            data=b'<html><head><title>D1 HTTPS Fixture</title></head><body><h1>D1 HTTPS Fixture</h1><a id="download" href="/baseline" style="font-size:32px">Download fixed baseline</a><script>requestAnimationFrame(()=>{document.querySelector("a").focus();document.title="D1 HTTPS Fixture Ready"})</script></body></html>'
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        elif self.path=='/baseline':
            self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="d1-probe.txt"');self.send_header('Content-Length',str(len(DATA)));self.end_headers();self.wfile.write(DATA)
            print(json.dumps(dict(event='served',bytes=len(DATA),sha256=hashlib.sha256(DATA).hexdigest(),chromium='Chrome/' in self.headers.get('User-Agent',''))),flush=True)
        else:self.send_error(404)

tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain('/root/d1-origin/cert.pem','/root/d1-origin/key.pem')
server=http.server.ThreadingHTTPServer(('203.0.113.2',443),Handler)
server.socket=tls.wrap_socket(server.socket,server_side=True)
nobody=pwd.getpwnam('nobody');os.setgroups([]);os.setresgid(nobody.pw_gid,nobody.pw_gid,nobody.pw_gid);os.setresuid(nobody.pw_uid,nobody.pw_uid,nobody.pw_uid)
os.chdir('/');server.serve_forever()
