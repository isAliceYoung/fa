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
import re
import sys
import time
from urllib.parse import quote, urlsplit, parse_qs

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
    if key=='script':return b'#!/bin/sh\ntouch /home/wdguest/Documents/D1-EXECUTED\ntouch /opt/webdesktop/app/D1-EXECUTED\n'
    if key=='desktop':return b'[Desktop Entry]\nType=Application\nName=D1 never run\nExec=/bin/sh -c "touch /home/wdguest/Documents/D1-EXECUTED; touch /opt/webdesktop/app/D1-EXECUTED"\n'
    return ('D1 '+key+' 下载内容\n').encode()*4096
def expected(key):
    data=content(key);return dict(size=len(data),sha256=hashlib.sha256(data).hexdigest())

CLIP_COMMON="""<script>
async function digest(v){const h=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v)));return Array.from(h.slice(0,8),x=>x.toString(16).padStart(2,'0')).join('')}
const t=document.querySelector('textarea');document.addEventListener('click',()=>{t.focus();t.select()});
</script>"""
CLIP_TARGET='''<html><head><meta charset="utf-8"><title>C1 Ready</title></head><body><textarea style="width:90%;height:60%" autofocus></textarea>'''+CLIP_COMMON+'''<script>
t.addEventListener('input',async()=>{const v=t.value;document.title='C1 '+Array.from(v).length+' '+await digest(v)});t.focus();
</script></body></html>'''
CLIP_SOURCE='''<html><head><meta charset="utf-8"><title>C1 Loading</title></head><body><textarea style="width:90%;height:60%" readonly></textarea>'''+CLIP_COMMON+'''<script>
(async()=>{const b=location.hash.slice(1).replace(/-/g,'+').replace(/_/g,'/');const v=new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(b),c=>c.charCodeAt(0)));
t.value=v;t.focus();t.select();document.title='C1 Source '+Array.from(v).length+' '+await digest(v)})();
</script></body></html>'''

# C2 TEST pages only. Images never reach this server: the fixture is drawn in
# the page with exact pixel values (alpha 0/128/255, channels 0/255) so local
# and remote Chromium decode identical pixels. The E2E test extracts this exact
# source between the markers below and runs it in the local browser.
# C2-FIXTURE-BEGIN
C2_FIXTURE='''async function c2Fixture(kind){const [w,h]=kind==='alpha'?[96,64]:kind==='wide'?[320,40]:[24,16];
const d=new ImageData(w,h);for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=(y*w+x)*4,a=[0,128,255][(x+2*y+(kind==='wide'?1:0))%3];if(!a)continue;
d.data[i]=(x*7+y)%2?255:0;d.data[i+1]=(x+y*5)%3?255:0;d.data[i+2]=(x*3+y)%2?0:255;d.data[i+3]=a;}
const c=new OffscreenCanvas(w,h);c.getContext('2d').putImageData(d,0,0);return c.convertToBlob({type:'image/png'});}
async function c2Pixels(blob){const b=await createImageBitmap(blob,{premultiplyAlpha:'none',colorSpaceConversion:'none'});
const c=new OffscreenCanvas(b.width,b.height),g=c.getContext('2d',{willReadFrequently:true});g.drawImage(b,0,0);
const px=g.getImageData(0,0,b.width,b.height).data;const h=new Uint8Array(await crypto.subtle.digest('SHA-256',px));
return b.width+'x'+b.height+' '+Array.from(h.slice(0,8),x=>x.toString(16).padStart(2,'0')).join('')}'''
# C2-FIXTURE-END
IMG_TARGET='''<html><head><meta charset="utf-8"><title>C2 Ready</title></head><body style="font:24px sans-serif">
<div contenteditable="true" style="width:90%;height:70%;border:2px dashed #888">在此粘贴图片（C2 图片剪贴板测试）</div><script>'''+C2_FIXTURE+'''
const box=document.querySelector('div');document.addEventListener('click',()=>box.focus());box.focus();
document.addEventListener('paste',async e=>{e.preventDefault();const f=[...e.clipboardData.items].find(i=>i.type==='image/png')?.getAsFile();
document.title=f?'C2 '+await c2Pixels(f):'C2 NoImage'});
</script></body></html>'''
IMG_SOURCE='''<html><head><meta charset="utf-8"><title>C2 Source Loading</title></head><body style="margin:0">
<button style="width:100%;height:90vh;font:28px sans-serif">点击复制测试图片 <img style="width:192px;image-rendering:pixelated"></button><script>'''+C2_FIXTURE+'''
(async()=>{const kind=new URLSearchParams(location.search).get('kind');const blob=await c2Fixture(kind);const px=await c2Pixels(blob);
document.querySelector('img').src=URL.createObjectURL(blob);document.title='C2 Source '+px;
document.querySelector('button').addEventListener('click',()=>navigator.clipboard.write([new ClipboardItem({'image/png':blob})])
.then(()=>{document.title='C2 Copied '+px}).catch(e=>{document.title='C2 CopyFailed '+e.name}))})();
</script></body></html>'''

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):
        url=urlsplit(self.path);route,_,key=url.path.rpartition('/')
        if url.path in ('/img','/img-src'):
            q=parse_qs(url.query)
            if not re.fullmatch('[0-9a-f]{32}',q.get('task',[''])[0]) or (url.path=='/img-src' and q.get('kind',[''])[0] not in ('alpha','wide','small')):self.send_error(400);return
            data=(IMG_SOURCE if url.path=='/img-src' else IMG_TARGET).encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        if url.path in ('/clip','/clip-src'):
            # C1 TEST pages only. Text never reaches this server: /clip-src
            # reads it from the URL fragment; titles carry only length+hash.
            if not re.fullmatch('[0-9a-f]{32}',parse_qs(url.query).get('task',[''])[0]):self.send_error(400);return
            body=CLIP_SOURCE if url.path=='/clip-src' else CLIP_TARGET
            data=body.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        visit=parse_qs(url.query).get('visit',['0'])[0]
        task=parse_qs(url.query).get('task',[''])[0]
        if not re.fullmatch('[0-9a-f]{32}',task):self.send_error(400);return
        if not visit.isdigit() or len(visit)>4:self.send_error(400);return
        if key not in NAMES or route not in ('/page','/file'):
            self.send_error(404);return
        if route=='/page':
            data=('<html><head><meta charset="utf-8"><title>D1 Loading</title></head><body><h1>D1 controlled HTTPS '+key+'</h1><a href="/file/'+key+'?task='+task+'" style="font-size:32px">Download</a><script>requestAnimationFrame(()=>{document.querySelector("a").focus();document.title="D1 Ready '+key+' '+visit+'"})</script></body></html>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        data=content(key);total=8*1024**2 if key in SLOW or key=='reset' else len(data)
        offset=0
        if self.headers.get('Range'):
            # Fixed, bounded fixture resumption. Reset always fails, including
            # retries, rather than accidentally changing into a valid download.
            match=re.fullmatch(r'bytes=(\d+)-',self.headers['Range'])
            if not match or int(match[1])>=total:self.send_error(416);return
            offset=int(match[1])
        self.send_response(206 if offset else 200)
        self.send_header('Content-Type','application/octet-stream')
        self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(NAMES[key],safe=''))
        self.send_header('Content-Length',str(total-offset));self.send_header('Accept-Ranges','bytes')
        if offset:self.send_header('Content-Range','bytes '+str(offset)+'-'+str(total-1)+'/'+str(total))
        self.end_headers();sent=0;outcome='sent'
        print(json.dumps(dict(event='request',case=key,task=task,chromium='Chrome/' in self.headers.get('User-Agent',''),range_start=offset)),flush=True)
        try:
            for pos in range(offset,total,65536):
                n=min(65536,total-pos);block=(b'D'*n) if key in SLOW or key=='reset' else data[pos:pos+n]
                self.wfile.write(block);self.wfile.flush();sent+=len(block)
                if key=='reset':
                    outcome='intentional-network-cut';self.close_connection=True;self.connection.shutdown(socket.SHUT_RDWR);break
                if key in SLOW:time.sleep(.2)
        except (OSError,ssl.SSLError):outcome='peer-closed'
        print(json.dumps(dict(event='transfer',case=key,task=task,bytes=sent,outcome=outcome)),flush=True)

def serve():
    if os.getuid()!=0 or socket.gethostname()!='wd-lab' or sys.argv[1:]!=['--disposable-vm'] or not Path('/var/tmp/wd-lab-ready').is_file():raise SystemExit('Disposable VM only')
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain('/root/d1-origin/cert.pem','/root/d1-origin/key.pem')
    server=http.server.ThreadingHTTPServer(('203.0.113.2',443),Handler);server.socket=tls.wrap_socket(server.socket,server_side=True)
    nobody=pwd.getpwnam('nobody');os.setgroups([]);os.setresgid(nobody.pw_gid,nobody.pw_gid,nobody.pw_gid);os.setresuid(nobody.pw_uid,nobody.pw_uid,nobody.pw_uid);os.chdir('/')
    server.serve_forever()

if __name__=='__main__':serve()
