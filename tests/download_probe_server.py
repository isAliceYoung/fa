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
import time

if os.getuid()!=0 or socket.gethostname()!='wd-lab' or sys.argv[1:]!=['--disposable-vm'] or not Path('/var/tmp/wd-lab-ready').is_file():
    raise SystemExit('Explicit disposable VM required')
DATA=(b'WebDesktop Chromium download probe\n'*8192)
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path in ('/','/slow-page','/escape-page'):
            target={'/':'/baseline','/slow-page':'/slow','/escape-page':'/escape'}[self.path]
            data=('<html><head><title>D1 HTTPS Fixture</title></head><body><h1>D1 HTTPS Fixture</h1><a href="'+target+'" style="font-size:32px">Download fixed test</a><script>requestAnimationFrame(()=>{document.querySelector("a").focus();document.title="D1 HTTPS Fixture Ready '+target+'"})</script></body></html>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        elif self.path in ('/baseline','/slow','/escape'):
            name={'/baseline':'d1-probe.txt','/slow':'d1-in-progress.bin','/escape':'d1-escape-probe.txt'}[self.path]
            data=DATA if self.path!='/slow' else b'P'*8*1024**2
            self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="'+name+'"');self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:
                for offset in range(0,len(data),65536):
                    self.wfile.write(data[offset:offset+65536]);self.wfile.flush()
                    if self.path=='/slow':time.sleep(.2)
                print(json.dumps(dict(event='served',case=self.path,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),chromium='Chrome/' in self.headers.get('User-Agent',''))),flush=True)
            except (BrokenPipeError,ConnectionResetError,ssl.SSLError):print(json.dumps(dict(event='peer-closed',case=self.path)),flush=True)
        else:self.send_error(404)

tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain('/root/d1-origin/cert.pem','/root/d1-origin/key.pem')
server=http.server.ThreadingHTTPServer(('203.0.113.2',443),Handler)
server.socket=tls.wrap_socket(server.socket,server_side=True)
nobody=pwd.getpwnam('nobody');os.setgroups([]);os.setresgid(nobody.pw_gid,nobody.pw_gid,nobody.pw_gid);os.setresuid(nobody.pw_uid,nobody.pw_uid,nobody.pw_uid)
os.chdir('/');server.serve_forever()
