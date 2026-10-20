// Real TLS-terminating proxy. Trusted origin is configuration, never forwarded headers.
import assert from 'node:assert/strict';
import https from 'node:https';
import net from 'node:net';
import fs from 'node:fs';
import WebSocket from 'ws';
let raw='';for await(const b of process.stdin)raw+=b;const c=JSON.parse(raw);
const server=https.createServer({key:fs.readFileSync(c.key),cert:fs.readFileSync(c.cert)},(_q,r)=>r.end());
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
 console.log('Real TLS proxy: preserved Host accepted; forged/missing source and rewritten Host rejected');
}finally{for(const p of peers)p.destroy();await new Promise(r=>server.close(r));}
