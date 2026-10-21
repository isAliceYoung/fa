// Disposable Debian VM root acceptance only. Not shipped in a visitor package.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
const require=createRequire('/opt/webdesktop/app/package.json'),WebSocket=require('ws');
const cfg=Object.fromEntries(fs.readFileSync('/opt/webdesktop/app/.env','utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return[l.slice(0,i),l.slice(i+1)];}));
const legacy=process.argv.includes('--legacy'), OUT='/root/staging-results/resource-runtime.json', clients=new Set();
const report={schema:1,legacy,passed:0,failed:0,skipped:0,not_executed:[],tests:[],startups:[],samples:[],latencies:[]};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const command=(bin,args=[])=>execFileSync(bin,args,{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}).trim();
const value=(u,p)=>command('systemctl',['show',u,'-p',p,'--value']);
const counts=path=>Object.fromEntries(fs.readFileSync(path,'utf8').trim().split('\n').map(l=>{const [k,v]=l.split(' ');return[k,Number(v)];}));
const rootGroup='/sys/fs/cgroup/webdesktop.slice';
let auth,headers;
function scrub(error){let s=String(error.stack||error);for(const v of [cfg.DATABASE_URL,cfg.DESKTOP_PASSWORD,cfg.ADMIN_PASSWORD,cfg.AUTH_KEY,auth?.cookie,auth?.access,new URL(cfg.DATABASE_URL).password])if(v)s=s.split(v).join('[REDACTED]');return s.slice(0,1800);}
async function test(name,fn){try{await fn();report.passed++;report.tests.push({name,result:'passed'});}catch(e){report.failed++;report.tests.push({name,result:'failed',detail:scrub(e)});}}
async function until(fn,ms=12000){const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await pause(150);}throw new Error('Bounded acceptance timeout');}
async function api(path,init={}){const r=await fetch('http://127.0.0.1:3000'+path,{headers,...init,signal:AbortSignal.timeout(4000)});const b=await r.text();return{status:r.status,data:b?JSON.parse(b):null,bytes:Buffer.byteLength(b)};}
async function snapshot(){if(legacy)return{at:Date.now(),meminfo:fs.readFileSync('/proc/meminfo','utf8'),cpuPsi:fs.readFileSync('/proc/pressure/cpu','utf8'),memoryPsi:fs.readFileSync('/proc/pressure/memory','utf8'),events:counts(rootGroup+'/memory.events')};const r=await api('/api/resources');assert.equal(r.status,200);assert(r.data.available);report.samples.push(r.data);if(report.samples.length>200)report.samples.shift();return r.data;}
async function openSocket(path,params='',retry=true){
 const end=Date.now()+12000;
 while(true){
  const sid=randomBytes(12).toString('hex');
  const ws=new WebSocket('ws://127.0.0.1:3000'+path+'?sid='+sid+'&access='+auth.access+params,{headers:{Cookie:auth.cookie,Origin:'http://127.0.0.1:3000'},handshakeTimeout:12000});
  ws.on('error',()=>{});ws.pending=Buffer.alloc(0);ws.output='';
  ws.on('message',(d,b)=>{if(path==='/api/gui/ws'){ws.pending=Buffer.concat([ws.pending,d]);if(ws.pending.length>2*1024*1024)ws.terminate();}else if(b)ws.output=(ws.output+d.toString()).slice(-65536);});
  try{await new Promise((r,j)=>{ws.once('open',r);ws.once('error',j);});clients.add(ws);ws.sid=sid;return ws;}
  catch(e){ws.terminate();const clean=path==='/api/gui/ws'&&['webdesktop-gui@1.service','webdesktop-gui-lease@1.service','webdesktop-browser@1.service'].every(u=>value(u,'MainPID')==='0'&&value(u,'ActiveState')!=='failed');
   if(!retry||Date.now()>end||e.message!=='Unexpected server response: 503'||!clean)throw e;await pause(150);
  }
 }
}
async function terminal(){const ws=await openSocket('/api/term/ws');await ping(ws);return ws;}
async function ping(ws,extra=''){
 assert.equal(ws.readyState,1);const nonce=randomBytes(8).toString('hex'),start=performance.now();
 ws.send(JSON.stringify({t:'i',d:extra+"printf 'RES_%s\\n' '"+nonce+"'\r"}));
 await until(()=>ws.output.includes('RES_'+nonce),4000);return performance.now()-start;
}
async function closeTerminal(ws){if(ws.readyState===1)ws.send(JSON.stringify({t:'kill'}));ws.close();clients.delete(ws);await until(()=>['webdesktop-pty@1.service','webdesktop-pty@2.service'].every(u=>value(u,'MainPID')==='0'));}
async function closeGui(ws){ws.close();clients.delete(ws);await until(()=>['webdesktop-gui@1.service','webdesktop-gui-lease@1.service','webdesktop-browser@1.service'].every(u=>value(u,'MainPID')==='0'));}
async function read(ws,n){await until(()=>ws.pending.length>=n);const b=ws.pending.subarray(0,n);ws.pending=ws.pending.subarray(n);return b;}
async function rfb(ws){
 assert.equal((await read(ws,12)).toString(),'RFB 003.008\n');ws.send(Buffer.from('RFB 003.008\n'));
 await read(ws,(await read(ws,1))[0]);ws.send(Buffer.from([1]));assert.equal((await read(ws,4)).readUInt32BE(),0);ws.send(Buffer.from([1]));
 const init=await read(ws,24);await read(ws,init.readUInt32BE(20));
 const enc=Buffer.alloc(8);enc[0]=2;enc.writeUInt16BE(1,2);enc.writeInt32BE(7,4);ws.send(enc);
 const request=Buffer.alloc(10);request[0]=3;request.writeUInt16BE(64,6);request.writeUInt16BE(64,8);ws.send(request);
 const update=await read(ws,4);assert.equal(update[0],0);assert(update.readUInt16BE(2)>0);await read(ws,12);
 ws.pending=Buffer.alloc(0);return{width:init.readUInt16BE(0),height:init.readUInt16BE(2)};
}
async function checkResponsive(ws,label){
 const start=performance.now(),name='resource-'+randomBytes(8).toString('hex')+'.txt',path='~/'+name;
 const [list,authResult,tty]=await Promise.all([api('/api/sys/fs?path=~'),api('/api/auth/session'),ping(ws)]);
 assert.equal(list.status,200);assert.equal(authResult.status,200);
 for(const [method,body]of [['POST',{dir:'~',name,kind:'file',content:label}],['PUT',{path,content:label+'-saved'}]]){
  const r=await api('/api/sys/fs',{method,body:JSON.stringify(body)});assert.equal(r.status,200);
 }
 assert.equal((await api('/api/sys/fs?read=1&path='+encodeURIComponent(path))).data.content,label+'-saved');
 assert.equal((await api('/api/sys/fs?path='+encodeURIComponent(path),{method:'DELETE'})).status,200);
 report.latencies.push({phase:label,elapsed_ms:performance.now()-start,terminal_ms:tty});
}
async function benchmark(ws,label){
 let running=true,error=null;const start=performance.now(),before=await snapshot();let gui;
 const parallel=(async()=>{try{while(running){await checkResponsive(ws,label);await pause(400);}}catch(e){error=e;}})();
 try{
  gui=await openSocket('/api/gui/ws','&app=chromium&w=1024&h=640');
  const upgradeMs=performance.now()-start, geometry=await rfb(gui);
  const record={label,upgrade_ms:upgradeMs,rfb_first_payload_ms:performance.now()-start,geometry,before,after:await snapshot()};
  if(!legacy){const p=await api('/api/gui/status?sid='+gui.sid);assert.equal(p.status,200);record.stages=p.data;}
  report.startups.push(record);assert(record.rfb_first_payload_ms<12000);
 }finally{running=false;await parallel;if(gui)await closeGui(gui);}
 if(error)throw error;
}
const protectedUnits=['webdesktop-staging.service','postgresql@15-main.service','webdesktop-guest.service'];
function protectedEvents(){return Object.fromEntries(protectedUnits.map(u=>{const g=value(u,'ControlGroup');assert(g);return[u,counts('/sys/fs/cgroup'+g+'/memory.events.local')];}));}
let beforeProtected;
try{
 await test('real login and resource endpoint requires Cookie plus current page session',async()=>{
  assert.equal((await fetch('http://127.0.0.1:3000/api/resources')).status,legacy?404:401);
  const r=await fetch('http://127.0.0.1:3000/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:cfg.DESKTOP_PASSWORD})});assert.equal(r.status,200);
  auth={cookie:r.headers.getSetCookie()[0].split(';')[0],access:(await r.json()).sessionId};headers={Cookie:auth.cookie,'X-Desktop-Session':auth.access,'Content-Type':'application/json'};
  if(!legacy){
   for(const h of [{Cookie:auth.cookie},{...headers,'X-Desktop-Session':'wrong'}])assert.equal((await fetch('http://127.0.0.1:3000/api/resources',{headers:h})).status,401);
   const sample=await api('/api/resources');assert.equal(sample.status,200);assert(sample.data.available);assert(sample.bytes<16384);
   assert.equal((await api('/api/resources?path=/etc/shadow')).status,400);
   const text=JSON.stringify(sample.data);for(const v of [cfg.AUTH_KEY,cfg.DATABASE_URL,cfg.DESKTOP_PASSWORD])assert(!text.includes(v));assert(!/cmdline|environ|selfPid|processes|\/opt\/webdesktop/.test(text));
  }
  beforeProtected=protectedEvents();
 });
 let shell;
 await test('cold visible Chromium while actual PTY and file CRUD remain responsive',async()=>{shell=await terminal();await benchmark(shell,'cold');});
 await test('warm Chromium repeat startup with concurrent authenticated operations',async()=>{await benchmark(shell,'warm');});
 if(shell)await closeTerminal(shell);
 if(legacy)report.not_executed.push('adaptive pressure API, disk admission, controlled memory/CPU/task stress: absent from baseline');
 else{
  await test('copy cut paste rename and trash keep working with VNC clipboard disabled',async()=>{
   assert.equal((await api('/api/gui/clipboard',{method:'POST',body:'{"paths":["~/Documents"]}'})).status,503);
   const name='resource-copy-'+randomBytes(8).toString('hex')+'.txt',path='~/Documents/'+name;
   assert.equal((await api('/api/sys/fs',{method:'POST',body:JSON.stringify({dir:'~/Documents',name,kind:'file',content:'copy-proof'})})).status,200);
   for(const cut of [false,true]){
    const r=await api('/api/sys/fs',{method:'PATCH',body:JSON.stringify({destDir:cut?'~/Desktop':'~/Downloads',copyPaths:[path],cut})});assert.equal(r.status,200);assert.equal(r.data.count,1);
    const target=r.data.paths[0],renamed=await api('/api/sys/fs',{method:'PATCH',body:JSON.stringify({path:target,name:'renamed-'+name})});assert.equal(renamed.status,200);
    assert.equal((await api('/api/sys/fs?read=1&path='+encodeURIComponent(renamed.data.path))).data.content,'copy-proof');
    assert.equal((await api('/api/sys/fs?path='+encodeURIComponent(renamed.data.path),{method:'DELETE'})).status,200);
   }
  });
  await test('real bounded-volume pressure warns, rejects new GUI, preserves shell and recovers',async()=>{
   const ws=await terminal(),file='/home/wdguest/resource-disk-'+randomBytes(8).toString('hex');
   try{
    const d=await snapshot(),size=Math.floor((d.disk.workspace.available-16*1024**2)/1048576);assert(size>32);
    await ping(ws,'fallocate -l '+size+'M '+file+'; ');
    await until(async()=>{const s=await snapshot();return s.status.level==='critical'&&s.status.reasons.includes('workspace_full');},25000);
    await checkResponsive(ws,'disk-pressure');
    let rejected=false;try{const g=await openSocket('/api/gui/ws','&app=chromium&w=800&h=600',false);await closeGui(g);}catch(e){rejected=e.message==='Unexpected server response: 503';}assert(rejected);
   }finally{await ping(ws,'rm -- '+file+'; ');await closeTerminal(ws);}
   await until(async()=>{const s=await snapshot();return s.status.level==='normal';},45000);
  });
  await test('CPU contention alerts without blocking authentication or file/terminal input',async()=>{
   const ws=await terminal(),cores=JSON.parse(fs.readFileSync('/etc/webdesktop-resources/policy.json')).effective.cpu_count;
   try{
    await ping(ws,'for n in $(seq 1 '+Math.min(16,Math.max(4,Math.ceil(cores*4)))+'); do (while :; do :; done) & done; ');
    const end=Date.now()+22000;let seen=false;
    while(Date.now()<end){await checkResponsive(ws,'cpu-pressure');const s=await snapshot();seen ||= s.status.reasons.includes('cpu_wait');await pause(700);}
    assert(seen,'CPU PSI/throttling must be observable under actual quota contention');
   }finally{await closeTerminal(ws);}
  });
  await test('fork exhaustion is confined to wdterm cgroup and complete process tree is reclaimed',async()=>{
   const ws=await terminal();
   try{
    ws.send(JSON.stringify({t:'i',d:"python3 -c 'import os,time; exec(\"while True:\\n try:\\n  p=os.fork()\\n  if p==0: time.sleep(60); os._exit(0)\\n except OSError: print(\\\"FORK_\\\"+\\\"BOUND\\\",flush=True); time.sleep(20); break\")'\r"}));
    await until(()=>ws.output.includes('FORK_BOUND'),10000);
    for(const p of ['/api/auth/session','/api/sys/fs?path=~/Documents'])assert.equal((await api(p)).status,200);
    const group=value('webdesktop-pty@1.service','ControlGroup'),limit=value('webdesktop-pty@1.service','TasksMax');
    report.fork={current:Number(fs.readFileSync('/sys/fs/cgroup'+group+'/pids.current','utf8')),limit:Number(limit)};
    assert(report.fork.current<=report.fork.limit);
   }finally{await closeTerminal(ws);}
  });
  await test('actual memory exhaustion kills only the bounded visitor unit; control-plane OOM stays zero',async()=>{
   const before=counts(rootGroup+'/memory.events'),ws=await terminal();
   ws.send(JSON.stringify({t:'i',d:"python3 -c 'a=[];exec(\"while True: a.append(bytearray(4194304))\")'\r"}));
   try{
    await until(async()=>{for(const path of ['/api/auth/session','/api/sys/fs?path=~/Documents'])assert.equal((await api(path)).status,200);return counts(rootGroup+'/memory.events').oom_kill>before.oom_kill;},30000);
    report.memoryFailure={before,after:counts(rootGroup+'/memory.events')};
   }finally{await closeTerminal(ws);}
   const after=protectedEvents();for(const unit of protectedUnits)assert.equal(after[unit].oom_kill,beforeProtected[unit].oom_kill);
   await until(async()=>{const s=await snapshot();return s.status.level==='normal';},80000);
  });
 }
 await test('executed work has bounded latency, no privileged OOM and leaves no untrusted processes',async()=>{
  assert(report.latencies.length>=2);
  const lat=report.latencies.map(x=>x.elapsed_ms).sort((a,b)=>a-b),p95=lat[Math.floor((lat.length-1)*.95)];
  report.response={count:lat.length,p95_ms:p95,max_ms:lat.at(-1)};
  assert(p95<2500&&lat.at(-1)<5000,'Control path latency exceeded usable budget');
  if(!legacy){const after=protectedEvents();for(const unit of protectedUnits)assert.equal(after[unit].oom_kill,beforeProtected[unit].oom_kill);}
  for(const user of ['wdgui','wdterm'])await until(()=>{try{return !command('pgrep',['-u',user]);}catch(e){return e.status===1;}});
 });
}catch(e){report.failed++;report.tests.push({name:'acceptance fixture',result:'failed',detail:scrub(e)});}
finally{
 for(const ws of clients){if(ws.readyState===1&&ws.output!==undefined)try{ws.send(JSON.stringify({t:'kill'}));}catch{}ws.terminate();}
 fs.writeFileSync(OUT,JSON.stringify(report,null,2));
}
console.log(JSON.stringify({passed:report.passed,failed:report.failed,skipped:report.skipped,not_executed:report.not_executed.length}));
process.exitCode=report.failed?1:0;
