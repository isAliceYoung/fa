// Real TLS-terminating proxy. Trusted origin is configuration, never forwarded headers.
import assert from 'node:assert/strict';
import https from 'node:https';
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import WebSocket from 'ws';
let raw='';for await(const b of process.stdin)raw+=b;const c=JSON.parse(raw);
const server=https.createServer({key:fs.readFileSync(c.key),cert:fs.readFileSync(c.cert)},(q,r)=>{
 const up=http.request({hostname:'127.0.0.1',port:3000,path:q.url,method:q.method,headers:q.headers},response=>{r.writeHead(response.statusCode,response.headers);response.pipe(r);});
 up.on('error',()=>r.destroy());q.pipe(up);
});
const peers=new Set();
server.on('upgrade',(req,socket,head)=>{
 const up=net.connect(3000,'127.0.0.1');peers.add(socket);peers.add(up);
 up.once('connect',()=>{
  up.write(`GET ${req.url} HTTP/1.1\r\n`+req.rawHeaders.reduce((s,x,i)=>s+(i%2?x+'\r\n':x+': '),'')+'\r\n');if(head.length)up.write(head);socket.pipe(up).pipe(socket);
 });
 socket.on('error',()=>up.destroy());up.on('error',()=>socket.destroy());socket.on('close',()=>{up.destroy();peers.delete(socket);});up.on('close',()=>{socket.destroy();peers.delete(up);});
});
await new Promise(r=>server.listen(3443,'127.0.0.1',r));
const lookup=(_host,options,done)=>options.all?done(null,[{address:'127.0.0.1',family:4}]):done(null,'127.0.0.1',4);
async function status(path,headers){return new Promise((resolve,reject)=>{
 const ws=new WebSocket('wss://proxy.example:3443'+path+'?access='+encodeURIComponent(c.session),{ca:fs.readFileSync(c.cert),lookup,headers,handshakeTimeout:5000});ws.on('error',()=>{});
 ws.once('unexpected-response',(_q,r)=>{r.resume();resolve(r.statusCode);});ws.once('open',()=>{ws.terminate();reject(new Error('Unexpected upgrade'));});const t=setTimeout(()=>{ws.terminate();reject(new Error('Proxy timeout'));},6000);t.unref();
});}
try{
 for(const path of ['/api/term/ws','/api/gui/ws','/api/browser/ws']){
  const good={Cookie:c.cookie,Origin:'https://proxy.example:3443'};
  assert.equal(await status(path,good),503);
  assert.equal(await status(path,{Origin:good.Origin}),401);
  assert.equal(await status(path,{Cookie:c.cookie}),403);
  assert.equal(await status(path,{...good,Origin:'https://evil.example'}),403);
  assert.equal(await status(path,{...good,Host:'127.0.0.1:3000','X-Forwarded-Host':'proxy.example:3443'}),403);
 }
 const request=(tls,path,body,headers={})=>new Promise((resolve,reject)=>{
  const data=JSON.stringify(body),q=(tls?https:http).request({hostname:tls?'proxy.example':'127.0.0.1',port:tls?3443:3000,path,method:'POST',...(tls?{ca:fs.readFileSync(c.cert),lookup}:{}),headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(data),...headers}},r=>{let text='';r.on('data',b=>text+=b);r.on('end',()=>resolve({status:r.statusCode,headers:r.headers,body:JSON.parse(text)}));});q.on('error',reject);q.end(data);
 });
 const local=await request(false,'/api/auth/login',{password:c.password},{'X-Forwarded-Proto':'https','X-Webdesktop-Https':'1','Forwarded':'proto=https'});
 assert.equal(local.status,200);assert(!local.headers['set-cookie'][0].includes('; Secure'));
 const login=await request(true,'/api/auth/login',{password:c.password},{'X-Forwarded-Proto':'http','X-Webdesktop-Https':'0','Forwarded':'proto=http'});
 assert.equal(login.status,200);const cookie=login.headers['set-cookie'][0];assert(cookie.includes('; Secure'));assert(cookie.includes('; HttpOnly'));assert(cookie.includes('; SameSite=Lax'));
 assert.equal(await status('/api/term/ws',{Origin:'https://proxy.example:3443',Cookie:c.cookie}),401);
 const logout=await request(true,'/api/auth/logout',{}, {Cookie:cookie.split(';')[0],'X-Desktop-Session':login.body.sessionId});
 assert.equal(logout.status,200);assert(logout.headers['set-cookie'][0].includes('Max-Age=0'));assert(logout.headers['set-cookie'][0].includes('; Secure'));
 // Keep the copied cookie: logout must revoke server-side permission too.
 const stale=new WebSocket('wss://proxy.example:3443/api/term/ws?access='+encodeURIComponent(login.body.sessionId),{ca:fs.readFileSync(c.cert),lookup,headers:{Cookie:cookie.split(';')[0],Origin:'https://proxy.example:3443'}});
 stale.on('error',()=>{});assert.equal(await new Promise(r=>stale.once('unexpected-response',(_q,res)=>{res.resume();r(res.statusCode);})),401);
 console.log('Real TLS proxy: preserved Host accepted; forged/missing source and rewritten Host rejected');
}finally{for(const p of peers)p.destroy();await new Promise(r=>server.close(r));}
