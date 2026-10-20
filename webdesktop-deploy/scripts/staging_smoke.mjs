// Root-only acceptance controller. Never runs inside an untrusted GUI/PTY worker.
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
const require=createRequire('/opt/webdesktop/app/package.json'),WebSocket=require('ws');
const cfg=Object.fromEntries(fs.readFileSync('/opt/webdesktop/app/.env','utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return[l.slice(0,i),l.slice(i+1)];}));
const report={passed:0,failed:0,tests:[]};
const cmd=(bin,args=[])=>execFileSync(bin,args,{encoding:'utf8',timeout:15000,stdio:['ignore','pipe','pipe']}).trim();
const value=(unit,key)=>cmd('systemctl',['show',unit,'-p',key,'--value']);
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,ms=12000){const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await delay(100);}throw Error('bounded timeout');}
async function test(name,fn){try{await fn();report.passed++;report.tests.push({name,result:'passed'});}catch{report.failed++;report.tests.push({name,result:'failed',detail:'Fixed runtime assertion failed; inspect protected service diagnostics'});}}
let auth,headers;
await test('unauthenticated file request denied and independent random login works',async()=>{
 assert.equal((await fetch('http://127.0.0.1:3000/api/sys/fs')).status,401);
 const r=await fetch('http://127.0.0.1:3000/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:cfg.DESKTOP_PASSWORD}),signal:AbortSignal.timeout(8000)});
 assert.equal(r.status,200);auth={cookie:r.headers.getSetCookie()[0].split(';')[0],access:(await r.json()).sessionId};headers={Cookie:auth.cookie,'X-Desktop-Session':auth.access,'Content-Type':'application/json'};
});
async function open(path,params=''){
 assert(auth);const ws=new WebSocket('ws://127.0.0.1:3000'+path+'?sid='+randomBytes(12).toString('hex')+'&access='+auth.access+params,{headers:{Cookie:auth.cookie,Origin:'http://127.0.0.1:3000'},handshakeTimeout:16000});ws.on('error',()=>{});
 await new Promise((r,j)=>{ws.once('open',r);ws.once('error',j);});return ws;
}
await test('authenticated file create read write and trash through wdguest API',async()=>{
 const name='staging-health-'+randomBytes(12).toString('hex')+'.txt',path='~/'+name;
 for(const [method,body]of [['POST',{dir:'~',name,kind:'file',content:'staging'}],['PUT',{path,content:'staging-updated'}]]){
  const r=await fetch('http://127.0.0.1:3000/api/sys/fs',{method,headers,body:JSON.stringify(body)});assert.equal(r.status,200);await r.arrayBuffer();
 }
 const r=await fetch('http://127.0.0.1:3000/api/sys/raw?path='+encodeURIComponent(path),{headers});assert.equal(await r.text(),'staging-updated');
 const denied=await fetch('http://127.0.0.1:3000/api/sys/raw?path=/opt/webdesktop/app/.env',{headers});assert.notEqual(denied.status,200);await denied.arrayBuffer();
 const removed=await fetch('http://127.0.0.1:3000/api/sys/fs?path='+encodeURIComponent(path),{method:'DELETE',headers});assert.equal(removed.status,200);await removed.arrayBuffer();
});
await test('real authenticated wdterm Bash and cgroup cleanup',async()=>{
 const ws=await open('/api/term/ws');let output='';ws.on('message',(d,b)=>{if(b)output=(output+d.toString()).slice(-16384);});
 try{ws.send(JSON.stringify({t:'i',d:"printf 'STAGING_%s\\n' READY; id -un\r"}));await until(()=>output.includes('STAGING_READY')&&output.includes('wdterm'));assert(value('webdesktop-pty@1.service','User')==='wdterm');assert.equal(Number(/Uid:\s+(\d+)/.exec(fs.readFileSync('/proc/'+value('webdesktop-pty@1.service','MainPID')+'/status','utf8'))[1]),Number(cmd('id',['-u','wdterm'])));}
 finally{if(ws.readyState===1)ws.send(JSON.stringify({t:'kill'}));ws.close();await until(()=>value('webdesktop-pty@1.service','MainPID')==='0');}
});
async function gui(app){
 const ws=await open('/api/gui/ws','&app='+app+'&w=800&h=600');let pending=Buffer.alloc(0);ws.on('message',d=>{pending=Buffer.concat([pending,d]);if(pending.length>2*1024*1024)ws.terminate();});
 const read=async n=>{await until(()=>pending.length>=n);const b=pending.subarray(0,n);pending=pending.subarray(n);return b;};
 try{
  assert.equal((await read(12)).toString(),'RFB 003.008\n');ws.send(Buffer.from('RFB 003.008\n'));await read((await read(1))[0]);ws.send(Buffer.from([1]));assert.equal((await read(4)).readUInt32BE(),0);ws.send(Buffer.from([1]));const init=await read(24);await read(init.readUInt32BE(20));
  const pid=value('webdesktop-gui@1.service','MainPID');assert.notEqual(pid,'0');
  const env=['PATH=/usr/bin:/bin','HOME=/home/wdguest','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/wd-gui/bus'];
  const run=args=>cmd('nsenter',['--target',pid,'--mount','--net','--','setpriv','--reuid',cmd('id',['-u','wdgui']),'--regid',cmd('id',['-g','wdguest']),'--groups',cmd('id',['-g','wdgui']),'--no-new-privs','env','-i',...env,...args]);
  assert(run(['pgrep','-u','wdgui','-x','fcitx5']));
  await until(()=>{try{return !!run(['xdotool','search','--onlyvisible','--class',app==='chromium'?'chromium':'[Pp]cmanfm']);}catch{return false;}});
  if(app==='chromium'){
   const main=value('webdesktop-browser@1.service','MainPID');assert.notEqual(main,'0');
   const group='/sys/fs/cgroup'+value('webdesktop-browser@1.service','ControlGroup');
   await until(()=>fs.readFileSync(group+'/cgroup.procs','utf8').trim().split('\n').some(id=>{try{const args=fs.readFileSync('/proc/'+id+'/cmdline','utf8'),s=fs.readFileSync('/proc/'+id+'/status','utf8');return args.includes('--type=renderer')&&Number(/Uid:\s+(\d+)/.exec(s)?.[1])===Number(cmd('id',['-u','wdgui']))&&/NoNewPrivs:\s+1/.test(s)&&/CapEff:\s+0000000000000000/.test(s)&&Number(/Seccomp_filters:\s+(\d+)/.exec(s)?.[1])>=2&&!args.includes('--no-sandbox')&&fs.readlinkSync('/proc/'+id+'/ns/user')!==fs.readlinkSync('/proc/1/ns/user')&&fs.readlinkSync('/proc/'+id+'/ns/pid')!==fs.readlinkSync('/proc/1/ns/pid');}catch{return false;}}));
   const curl=url=>cmd('ip',['netns','exec','wd-browser','setpriv','--reuid',cmd('id',['-u','wdgui']),'--regid',cmd('id',['-g','wdgui']),'--clear-groups','--no-new-privs','curl','--noproxy','*','-fsS','--max-time','5','--output','/dev/null',url]);
   curl('https://example.com');
   for(const port of [22,3000,5432]){let denied=false;try{curl('http://10.232.0.1:'+port);}catch{denied=true;}assert(denied);}
  }
 }finally{ws.close();await until(()=>value('webdesktop-gui@1.service','MainPID')==='0');await until(()=>value('webdesktop-gui-lease@1.service','MainPID')==='0');await until(()=>value('webdesktop-browser@1.service','MainPID')==='0');}
}
await test('PCManFM actual RFB, private X11 and Fcitx startup',()=>gui('pcmanfm'));
await test('visible Chromium native sandbox, external HTTPS, management denial and cleanup',()=>gui('chromium'));
await test('closed GUI slot safely restarts PCManFM',()=>gui('pcmanfm'));
await test('no untrusted GUI processes remain after close',async()=>{
 assert.equal(value('webdesktop-gui@1.service','MainPID'),'0');
 let r='';try{r=cmd('ps',['-u','wdgui','-o','pid=,comm=']);}catch(e){assert.equal(e.status,1);}assert.equal(r,'');
});
// This local health login intentionally replaces previous authentication, then
// revokes its own permission. It never prints Cookie/session/password values.
await test('health login logout revokes server-side permission',async()=>{
 assert(auth);const r=await fetch('http://127.0.0.1:3000/api/auth/logout',{method:'POST',headers});assert.equal(r.status,200);
 const stale=await fetch('http://127.0.0.1:3000/api/sys/fs',{headers});assert.equal(stale.status,401);
});
console.log(JSON.stringify(report));process.exitCode=report.failed?1:0;
