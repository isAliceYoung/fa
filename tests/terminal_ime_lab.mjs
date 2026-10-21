// Local diagnosis only: fixed static assets and an echo socket, never a shell.
import http from 'node:http';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {WebSocketServer} from 'ws';
const root=new URL('../',import.meta.url);
export async function startImeLab(port=18891){
 const dependency=JSON.parse(await fs.readFile(new URL('node_modules/@xterm/xterm/package.json',root),'utf8'));
 if(dependency.version!=='6.0.0')throw new Error('Review the IME compatibility bridge before changing xterm version');
 const assets=new Map([
  ['/',['tests/fixtures/terminal-ime-lab.html','text/html; charset=utf-8']],
  ['/lab.mjs',['tests/fixtures/terminal-ime-lab.mjs','text/javascript; charset=utf-8']],
  ['/xterm.js',['node_modules/@xterm/xterm/lib/xterm.js','text/javascript; charset=utf-8']],
  ['/xterm.css',['node_modules/@xterm/xterm/css/xterm.css','text/css']],
  ['/terminal-input.mjs',['src/components/desktop/terminal-input.mjs','text/javascript; charset=utf-8']],
  ['/terminal-ime.mjs',['src/components/desktop/terminal-ime.mjs','text/javascript; charset=utf-8']]
 ]);
 let origin;
 const server=http.createServer(async(req,res)=>{
  if(req.headers.host!==new URL(origin).host||!['GET','HEAD'].includes(req.method)){res.writeHead(403);res.end();return;}
  const asset=assets.get(req.url==='/fixed'?'/':req.url);
  if(!asset){res.writeHead(404);res.end();return;}
  try{const data=await fs.readFile(new URL(asset[0],root));res.writeHead(200,{'Content-Type':asset[1],'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'",'X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:data);}
  catch{res.writeHead(503);res.end();}
 });
 const wss=new WebSocketServer({noServer:true,maxPayload:32768,perMessageDeflate:false});
 server.on('upgrade',(req,socket,head)=>{
  if(req.url!=='/echo'||req.headers.host!==new URL(origin).host||req.headers.origin!==origin||wss.clients.size>=8){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;}
  wss.handleUpgrade(req,socket,head,ws=>wss.emit('connection',ws,req));
 });
 wss.on('connection',ws=>{ws.on('error',()=>{});ws.on('message',(data,binary)=>{
  try{const m=JSON.parse(data.toString());if(binary||m.t!=='i'||typeof m.d!=='string'||m.d.length>4096||ws.bufferedAmount>65536)throw new Error();ws.send(JSON.stringify(m));}
  catch{ws.close(1008);}
 });});
 await new Promise(resolve=>server.listen(port,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
 return{origin,close:async()=>{for(const ws of wss.clients)ws.terminate();wss.close();await new Promise(r=>server.close(r));}};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 const port=Number(process.argv[2]||18891);if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid local lab port');
 const lab=await startImeLab(port);console.log('Local xterm 6.0.0 A/B diagnosis (echo only): '+lab.origin);
 for(const sig of ['SIGINT','SIGTERM'])process.on(sig,()=>{void lab.close().then(()=>process.exit());});
}
