"""Controlled TLS bytes only, inside a disposable *external* origin namespace.

No application Cookie, URL fetcher, filesystem serving or command interface.
All positive download targets are written by the real GUI Chromium, never here.
"""
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
from urllib.parse import quote

NAMES={
 'normal':'d1-normal.txt','chinese':'d1-中文 文件.txt','spaces':'d1-file with spaces.txt',
 'unicode':'d1-🧪-𠮷.txt','repeat':'d1-repeat.txt','long':'d1-'+('长'*70)+'.txt',
 'special':"d1-$() '[]; 文件.txt",'traversal':'../../d1-escape.txt',
 'absolute':'/root/d1-absolute.txt','separator':r'd1-\separated.txt','nul':'d1-\x00-name.txt',
 'symlink':'d1-secret-link.txt','script':'d1-never-run.sh','desktop':'d1-never-run.desktop',
 'slow':'d1-slow.bin','cancel':'d1-cancel.bin','reset':'d1-reset.bin',
 'crash':'d1-crash.bin','close':'d1-close.bin','space':'d1-space.bin','inodes':'d1-inodes.bin',
 'recover':'d1-recovered.txt',
}
SLOW={'slow','cancel','crash','close','space','inodes'}
def content(key):
    if key=='script':return b'#!/bin/sh\ntouch /opt/webdesktop/app/D1-EXECUTED\n'
    if key=='desktop':return b'[Desktop Entry]\nType=Application\nName=D1 never run\nExec=touch /opt/webdesktop/app/D1-EXECUTED\n'
    return ('D1 '+key+' 下载内容\n').encode()*4096
def expected(key):
    data=content(key);return dict(size=len(data),sha256=hashlib.sha256(data).hexdigest())

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):
        route,_,key=self.path.rpartition('/')
        if key not in NAMES or route not in ('/page','/file'):
            self.send_error(404);return
        if route=='/page':
            data=('<html><head><meta charset="utf-8"><title>D1 Loading</title></head><body><h1>D1 controlled HTTPS '+key+'</h1><a href="/file/'+key+'" style="font-size:32px">Download</a><script>requestAnimationFrame(()=>{document.querySelector("a").focus();document.title="D1 Ready '+key+'"})</script></body></html>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        data=content(key);total=8*1024**2 if key in SLOW or key=='reset' else len(data)
        offset=0
        if self.headers.get('Range'):
            # Fixed, bounded fixture resumption. Reset always fails, including
            # retries, rather than accidentally changing into a valid download.
            import re
            match=re.fullmatch(r'bytes=(\d+)-',self.headers['Range'])
            if not match or int(match[1])>=total:self.send_error(416);return
            offset=int(match[1])
        self.send_response(206 if offset else 200)
        self.send_header('Content-Type','application/octet-stream')
        self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(NAMES[key],safe=''))
        self.send_header('Content-Length',str(total-offset));self.send_header('Accept-Ranges','bytes')
        if offset:self.send_header('Content-Range','bytes '+str(offset)+'-'+str(total-1)+'/'+str(total))
        self.end_headers();sent=0;outcome='sent'
        print(json.dumps(dict(event='request',case=key,chromium='Chrome/' in self.headers.get('User-Agent',''),range_start=offset)),flush=True)
        try:
            for pos in range(offset,total,65536):
                n=min(65536,total-pos);block=(b'D'*n) if key in SLOW or key=='reset' else data[pos:pos+n]
                self.wfile.write(block);self.wfile.flush();sent+=len(block)
                if key=='reset':
                    outcome='intentional-network-cut';self.close_connection=True;self.connection.shutdown(socket.SHUT_RDWR);break
                if key in SLOW:time.sleep(.2)
        except (OSError,ssl.SSLError):outcome='peer-closed'
        print(json.dumps(dict(event='transfer',case=key,bytes=sent,outcome=outcome)),flush=True)

def serve():
    if os.getuid()!=0 or socket.gethostname()!='wd-lab' or sys.argv[1:]!=['--disposable-vm'] or not Path('/var/tmp/wd-lab-ready').is_file():raise SystemExit('Disposable VM only')
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain('/root/d1-origin/cert.pem','/root/d1-origin/key.pem')
    server=http.server.ThreadingHTTPServer(('203.0.113.2',443),Handler);server.socket=tls.wrap_socket(server.socket,server_side=True)
    nobody=pwd.getpwnam('nobody');os.setgroups([]);os.setresgid(nobody.pw_gid,nobody.pw_gid,nobody.pw_gid);os.setresuid(nobody.pw_uid,nobody.pw_uid,nobody.pw_uid);os.chdir('/')
    server.serve_forever()

if __name__=='__main__':serve()
