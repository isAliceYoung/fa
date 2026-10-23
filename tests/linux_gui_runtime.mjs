// Disposable root controller; public WS -> Next auth -> wdgui -> actual Xvnc.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import WebSocket from '/opt/webdesktop/node_modules/ws/wrapper.mjs';
const phase=process.argv[2]||'prepare',OUT='/root/wd-systemd-evidence',unit='webdesktop-gui@1.service';
const config=Object.fromEntries(fs.readFileSync('/opt/webdesktop/.env','utf8').split('\n').filter(x=>x.includes('=')).map(x=>{const i=x.indexOf('=');return[x.slice(0,i),x.slice(i+1)];}));
const cmd=(bin,args=[])=>execFileSync(bin,args,{encoding:'utf8',timeout:15000,stdio:['ignore','pipe','pipe']}).trim();
const ctl=(...args)=>cmd('/usr/bin/systemctl',args),value=(u,p)=>ctl('show',u,'-p',p,'--value');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,ms=7000){const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await delay(100);}throw new Error('Bounded GUI assertion timed out');}
const report={phase,passed:0,failed:0,skipped:0,not_executed:0,tests:[],resources:{}};const clients=new Set();
let minimumAvailable=Infinity;
const peaks={tasks:0,processes:0,memory_bytes:0,cpu_usec:0,cpu_percent_of_one_vcpu:0};let lastCpu=0,lastCpuAt=0;
const cpuStart=fs.readFileSync('/proc/stat','utf8').split('\n')[0];
const sample=setInterval(()=>{
 minimumAvailable=Math.min(minimumAvailable,Number(/^MemAvailable:\s+(\d+)/m.exec(fs.readFileSync('/proc/meminfo','utf8'))[1]));
 const base='/sys/fs/cgroup/webdesktop.slice/webdesktop-gui.slice';
 try{
  peaks.tasks=Math.max(peaks.tasks,Number(fs.readFileSync(base+'/pids.current','utf8')));
  const procs=base+'/'+unit+'/cgroup.procs';
  if(fs.existsSync(procs))peaks.processes=Math.max(peaks.processes,fs.readFileSync(procs,'utf8').trim().split('\n').filter(Boolean).length);
  peaks.memory_bytes=Math.max(peaks.memory_bytes,Number(fs.readFileSync(base+'/memory.current','utf8')));
  const use=Number(/^usage_usec (\d+)/m.exec(fs.readFileSync(base+'/cpu.stat','utf8'))[1]),now=performance.now();
  if(lastCpuAt&&use>=lastCpu)peaks.cpu_percent_of_one_vcpu=Math.max(peaks.cpu_percent_of_one_vcpu,100*(use-lastCpu)/((now-lastCpuAt)*1000));
  peaks.cpu_usec+=use>=lastCpu?use-lastCpu:use;lastCpu=use;lastCpuAt=now;
 }catch{lastCpu=0;lastCpuAt=0;}
},100);
async function login(){const started=performance.now();const r=await fetch('http://127.0.0.1:3000/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:config.DESKTOP_PASSWORD}),signal:AbortSignal.timeout(5000)});assert.equal(r.status,200);return{cookie:r.headers.getSetCookie()[0].split(';')[0],access:(await r.json()).sessionId,login_ms:performance.now()-started};}
async function stopped(){await until(()=>value(unit,'MainPID')==='0'&&['inactive','failed'].includes(value(unit,'ActiveState')),7000);}
async function terminal(a){
 const end=Date.now()+12000;
 while(true){
  const c={terminal:true,ws:new WebSocket('ws://127.0.0.1:3000/api/term/ws?sid='+randomBytes(12).toString('hex')+'&access='+a.access,{headers:{Cookie:a.cookie,Origin:'http://127.0.0.1:3000'},handshakeTimeout:8000})};
  c.ws.on('error',()=>{});
  try{await new Promise((r,j)=>{c.ws.once('open',r);c.ws.once('error',j);});clients.add(c);c.ws.on('close',()=>clients.delete(c));return c;}
  catch(e){c.ws.terminate();if(!e.message.includes('503')||Date.now()>end)throw e;await delay(250);}
 }
}
async function test(name,body){try{await body();report.passed++;report.tests.push({name,result:'passed'});console.log('PASS '+name);}catch(e){report.failed++;report.tests.push({name,result:'failed',error:String(e.stack).slice(0,3000)});console.error('FAIL '+name+' '+e.message);}finally{
 const hadTerminal=[...clients].some(c=>c.terminal);
 for(const c of [...clients]){if(c.terminal&&c.ws.readyState===WebSocket.OPEN)c.ws.send(JSON.stringify({t:'kill'}));c.ws.close();}
 await delay(200);for(const c of clients)c.ws.terminate();await stopped().catch(()=>{});
 if(hadTerminal)await until(()=>[1,2].every(i=>value('webdesktop-pty@'+i+'.service','ActiveState')!=='active'),7000).catch(()=>{});
}}
function ws(auth,app='pcmanfm',overrides={}){return new WebSocket('ws://127.0.0.1:3000/api/gui/ws?sid='+randomBytes(12).toString('hex')+'&access='+encodeURIComponent(auth?.access||'')+'&app='+app+'&w=800&h=600', {headers:{Origin:'http://127.0.0.1:3000',...(auth?{Cookie:auth.cookie}:{}),...overrides},handshakeTimeout:12000});}
async function denied(auth,code,app='pcmanfm',headers={}){const c=ws(auth,app,headers);c.on('error',()=>{});const result=await new Promise((r,j)=>{c.once('unexpected-response',(_q,res)=>{res.resume();r(res.statusCode);});c.once('open',()=>{c.terminate();j(new Error('Unexpected upgrade'));});setTimeout(()=>{c.terminate();j(new Error('Handshake timeout'));},13000).unref();});assert.equal(result,code);}
async function display(auth,app='pcmanfm'){
 const end=Date.now()+15000;
 while(true){
  const c={ws:ws(auth,app),pending:Buffer.alloc(0),received:0,closed:false};
  c.ws.on('error',()=>{});c.ws.on('message',(b,binary)=>{assert(binary);c.received+=b.length;c.pending=Buffer.concat([c.pending,b]);if(c.pending.length>6*1024*1024)c.ws.terminate();});c.ws.on('close',()=>{c.closed=true;clients.delete(c);});
  try{await new Promise((r,j)=>{c.ws.once('open',r);c.ws.once('error',j);});clients.add(c);c.send=b=>c.ws.send(b,{binary:true});
   c.read=async n=>{await until(()=>c.pending.length>=n||c.closed,10000);assert(c.pending.length>=n,'RFB closed early');const b=c.pending.subarray(0,n);c.pending=c.pending.subarray(n);return b;};
   assert.match((await c.read(12)).toString(),/^RFB 003/);c.send(Buffer.from('RFB 003.008\n'));const n=(await c.read(1))[0],types=await c.read(n);assert(types.includes(1));c.send(Buffer.from([1]));assert.equal((await c.read(4)).readUInt32BE(),0);c.send(Buffer.from([0]));const init=await c.read(24);c.w=init.readUInt16BE();c.h=init.readUInt16BE(2);await c.read(init.readUInt32BE(20));
   c.key=(key,down)=>{const b=Buffer.alloc(8);b[0]=4;b[1]=down?1:0;b.writeUInt32BE(key,4);c.send(b);};
   c.press=async keys=>{for(const k of keys)c.key(k,true);for(const k of [...keys].reverse())c.key(k,false);await delay(150);};
   c.type=async text=>{for(const ch of text){const k=ch.codePointAt(0);c.key(k,true);c.key(k,false);}await delay(200);};return c;
  }catch(e){c.ws.terminate();if(!String(e.message).includes('503')||Date.now()>end)throw e;await delay(300);}
 }
}
function inside(code,args=[]){const pid=value(unit,'MainPID');return cmd('/usr/bin/nsenter',['--target',pid,'--mount','--net','--','/usr/bin/setpriv','--reuid',cmd('id',['-u','wdgui']),'--regid',cmd('id',['-g','wdguest']),'--groups',cmd('id',['-g','wdgui']),'/usr/bin/python3','-I','-c',code,...args]);}
function snapshot(){
 const result={};for(const u of ['wdlab-next.service','postgresql@15-main.service','webdesktop-guest.service','webdesktop-pty.slice','webdesktop-gui.slice','webdesktop-gui-lease@1.service']){
  const group=value(u,'ControlGroup');if(!group)continue;const item={};for(const f of ['memory.current','memory.peak','memory.swap.current','memory.events','memory.pressure','pids.current','pids.peak','cpu.stat']){const p='/sys/fs/cgroup'+group+'/'+f;if(fs.existsSync(p))item[f]=fs.readFileSync(p,'utf8').trim();}result[u]=item;
 }return{cgroups:result,meminfo:fs.readFileSync('/proc/meminfo','utf8'),disk:cmd('df',['-PB1','/','/home/wdguest']),profile_bytes:Number(cmd('du',['-sb','/home/wdguest/.gui']).split(/\s/)[0])};
}
async function measuredSnapshot(){
 const before=snapshot(),start=performance.now();await delay(1500);const after=snapshot();
 after.cpu_observation_ms=performance.now()-start;after.cpu_percent_of_one_vcpu={};
 for(const [name,fields] of Object.entries(after.cgroups)){
  const use=s=>Number(/^usage_usec (\d+)/m.exec(s||'')?.[1]||0);
  if(before.cgroups[name])after.cpu_percent_of_one_vcpu[name]=100*(use(fields['cpu.stat'])-use(before.cgroups[name]['cpu.stat']))/(after.cpu_observation_ms*1000);
 }
 return after;
}

await test('different GUI UID, private groups, root-owned runtime and socket permissions',async()=>{
 const ids=['wdsvc','wdguest','wdterm','wdgui'].map(u=>cmd('id',['-u',u]));assert.equal(new Set(ids).size,4);
 for(const u of ['wdsvc','wdgui'])assert.equal(cmd('id',['-G',u]),cmd('id',['-g',u]));
 for(const p of ['/run/webdesktop-gui/session-1.sock','/run/webdesktop-gui/lease-1.sock']){const st=fs.statSync(p);assert.equal(st.uid,0);assert.equal(st.gid,Number(cmd('id',['-g','wdsvc'])));assert.equal(st.mode&511,0o660);}
 assert.deepEqual(new Set(fs.readdirSync('/usr/local/lib/webdesktop-gui')),new Set(['worker.py','guard.py','rfb_input.py','text_clipboard.py','openbox.xml','dbus.conf','ready']));
 for(const p of fs.readdirSync('/usr/local/lib/webdesktop-gui')){const st=fs.statSync('/usr/local/lib/webdesktop-gui/'+p);assert.equal(st.uid,0);assert.equal(st.mode&511,0o644);}
 for(const account of ['wdguest','wdterm','wdgui','nobody']){
  const uid=cmd('id',['-u',account]),gid=cmd('id',['-g',['wdterm','wdgui'].includes(account)?'wdguest':account]);
  for(const path of ['/run/webdesktop-gui/session-1.sock','/run/webdesktop-gui/lease-1.sock']){
   try{cmd('setpriv',['--reuid',uid,'--regid',gid,'--clear-groups','python3','-I','-c',"import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1])",path]);assert.fail('Unprivileged GUI IPC connection accepted');}
   catch(e){assert(String(e.stderr).includes('PermissionError'));}
  }
 }
 for(const args of [['start',unit],['stop','ssh.service'],['set-property',unit,'MemoryMax=1G']])assert.throws(()=>cmd('runuser',['-u','wdsvc','--','systemctl','--no-ask-password',...args]));
 report.resources.identities=Object.fromEntries(['wdsvc','wdguest','wdterm','wdgui'].map(u=>[u,cmd('id',[u])]));
});
await test('real Next GUI handshake rejects untrusted sources, cookies, pages and arbitrary apps',async()=>{
 const a=await login();await denied(null,401);await denied({...a,cookie:'wd_session=wrong'},401);await denied({...a,access:'wrong'},401);
 await denied(a,403,'pcmanfm',{Origin:'https://evil.example',Host:'evil.example'});await denied(a,403,'pcmanfm',{Origin:'http://127.0.0.1:3000',Host:'evil.example'});
 await denied(a,503,'bash');await denied(a,503,'chromium');await login();await denied(a,401);
});
await test('actual Xvnc Openbox private Xauthority and D-Bus; no network or host display',async()=>{
 const a=await login();await terminal(a);report.resources.baseline=await measuredSnapshot();report.resources.baseline.login_ms=a.login_ms;
 const c=await display(a,'desktop');assert.equal(c.w,800);assert.equal(c.h,600);
 const pid=value(unit,'MainPID'),status=fs.readFileSync('/proc/'+pid+'/status','utf8');assert(status.includes('Uid:\t'+cmd('id',['-u','wdgui'])+'\t'));assert(status.includes('Gid:\t'+cmd('id',['-g','wdguest'])+'\t'));assert(status.includes('NoNewPrivs:\t1'));
 for(const key of ['CapEff','CapPrm','CapAmb','CapBnd'])assert(status.includes(key+':\t0000000000000000'));
 const facts=inside("import os,socket,stat,subprocess,json\np='/tmp/wd-gui';assert stat.S_IMODE(os.stat(p+'/Xauthority').st_mode)==0o600\nassert os.path.exists('/tmp/.X11-unix/X100') and not os.path.exists('/tmp/.X11-unix/X0')\nassert os.path.exists(p+'/bus')\nfor port in [22,3000,5432]:\n try:s=socket.create_connection(('127.0.0.1',port),timeout=1);raise AssertionError('Host port allowed')\n except OSError:pass\nenv={'DISPLAY':':100','XAUTHORITY':p+'/Xauthority','DBUS_SESSION_BUS_ADDRESS':'unix:path='+p+'/bus'}\nr=subprocess.run(['/usr/bin/dbus-send','--session','--print-reply','--dest=org.freedesktop.DBus','/','org.freedesktop.DBus.ListNames'],env=env,capture_output=True,text=True,check=True,timeout=3)\nassert 'org.freedesktop.systemd1' not in r.stdout\nprint(r.stdout)");
 const listeners=cmd('ss',['-ltnp']);assert(!listeners.split('\n').some(x=>/Xtigervnc|openbox|pcmanfm/.test(x)));
 fs.writeFileSync(OUT+'/gui-'+phase+'-identity.txt',status+'\n'+ctl('show',unit,'-p','ControlGroup','-p','CPUQuotaPerSecUSec','-p','MemoryMax','-p','TasksMax','-p','NoNewPrivileges')+'\n'+facts+'\n'+listeners);
 report.resources.desktop=await measuredSnapshot();
 report.resources.desktop.login_ms=(await login()).login_ms;
});
await test('kernel DAC blocks real source, git, env and keys without namespace hiding',async()=>{
 const uid=cmd('id',['-u','wdgui']),gid=cmd('id',['-g','wdguest']);
 for(const p of ['/opt/webdesktop/server.mjs','/opt/webdesktop/.env','/opt/webdesktop/.git/HEAD','/opt/webdesktop/private-source.test','/root/deploy-key.test']){
  const original=fs.readFileSync(p);
  for(const op of ['read','write','copy']){
   const script="import pathlib,sys,shutil;p=pathlib.Path(sys.argv[1]);op=sys.argv[2]\nif op=='read':p.read_bytes()\nelif op=='write':p.write_bytes(b'forbidden')\nelse:shutil.copyfile(p,'/home/wdguest/forbidden-gui-copy')";
   assert.throws(()=>cmd('setpriv',['--reuid',uid,'--regid',gid,'--clear-groups','python3','-I','-c',script,p,op]));assert.deepEqual(fs.readFileSync(p),original);
  }
 }
 assert(!fs.existsSync('/home/wdguest/forbidden-gui-copy'));
});
await test('GUI cannot signal ptrace read privileged proc or connect service IPC or edit transaction state',async()=>{
 const a=await login();await display(a);await terminal(a);
 const targets=['wdlab-next.service','webdesktop-guest.service','webdesktop-gui-lease@1.service',...['webdesktop-pty-lease@1.service','webdesktop-pty-lease@2.service'].filter(u=>Number(value(u,'MainPID'))>0)];
 const pids=targets.map(u=>value(u,'MainPID'));
 inside("import os,sys,ctypes,socket,pathlib\nfor pid in map(int,sys.argv[1:]):\n for sig in [0,19,15]:\n  try:os.kill(pid,sig);raise AssertionError('signal allowed')\n  except PermissionError:pass\n for name in ['environ','fd']:\n  try:\n   p=pathlib.Path('/proc/%s/%s'%(pid,name));list(p.iterdir()) if name=='fd' else p.read_bytes();raise AssertionError('proc allowed')\n  except (PermissionError,FileNotFoundError):pass\n libc=ctypes.CDLL(None,use_errno=True);assert libc.ptrace(16,pid,None,None)==-1\nfor path in ['/run/webdesktop-guest/guest.sock','/run/webdesktop-pty/lease-1.sock','/var/lib/webdesktop/.webdesktop-http/next.sock','/run/dbus/system_bus_socket']:\n try:s=socket.socket(socket.AF_UNIX);s.connect(path);raise AssertionError('IPC allowed')\n except (PermissionError,FileNotFoundError):pass\nfor path in ['/home/wdguest/.wd-state','/home/wdguest/.wd-state/agent.lock']:\n try:pathlib.Path(path).read_bytes();raise AssertionError('state allowed')\n except (PermissionError,IsADirectoryError):pass\nprint('KERNEL_GUI_BOUNDARIES_OK')",pids);
 const uid=cmd('id',['-u','wdgui']),gid=cmd('id',['-g','wdguest']);
 const state="import pathlib,errno\np=pathlib.Path('/home/wdguest/.wd-state')\nfor op in ['write','rename','replace']:\n try:\n  if op=='write':(p/'gui-forbidden').write_text('bad')\n  elif op=='rename':p.rename(p.with_name('.gui-stolen-state'))\n  else:p.rmdir()\n  raise AssertionError('Private state changed')\n except OSError as e:assert e.errno in (errno.EACCES,errno.EPERM,errno.EBUSY)";
 cmd('setpriv',['--reuid',uid,'--regid',gid,'--clear-groups','python3','-I','-c',state]);
 for(const target of ['wdlab-next.service','webdesktop-guest.service','webdesktop-pty-lease@1.service',unit])assert.throws(()=>cmd('runuser',['-u','wdgui','--','systemctl','--no-ask-password','stop',target]));
});
await test('PCManFM uses real RFB keyboard and mouse; shared files remain guest data',async()=>{
 const a=await login();await terminal(a);const c=await display(a);await delay(800);
 const pointer=Buffer.from([5,0,0,120,0,100]);c.send(pointer);
 await c.press([0xffe3,0x6c]);await c.type('/home/wdguest/Documents');await c.press([0xff0d]);await delay(400);
 await c.press([0xffe3,0xffe1,0x4e]);await delay(300);const folder='gui-'+phase+'-'+randomBytes(4).toString('hex');await c.type(folder);await c.press([0xff0d]);
 await until(()=>fs.existsSync('/home/wdguest/Documents/'+folder));assert.equal(fs.statSync('/home/wdguest/Documents/'+folder).uid,Number(cmd('id',['-u','wdgui'])));
 inside("import os,pathlib;os.umask(7);pathlib.Path('/home/wdguest/Documents/gui-shared.txt').write_text('GUI shared data')");
 const r=await fetch('http://127.0.0.1:3000/api/sys/fs?read=1&path=~/Documents/gui-shared.txt',{headers:{Cookie:a.cookie,'X-Desktop-Session':a.access}});assert.equal(r.status,200);assert.equal((await r.json()).content,'GUI shared data');
 const updated=await fetch('http://127.0.0.1:3000/api/sys/fs',{method:'PUT',headers:{Cookie:a.cookie,'X-Desktop-Session':a.access,'Content-Type':'application/json'},body:JSON.stringify({path:'~/Documents/gui-shared.txt',content:'agent update'})});assert.equal(updated.status,200);
 inside("import pathlib;p=pathlib.Path('/home/wdguest/Documents/gui-shared.txt');assert p.read_text()=='agent update';p.write_text('GUI modification')");
 report.resources.pcmanfm=await measuredSnapshot();
 report.resources.pcmanfm.login_ms=(await login()).login_ms;
});
await test('new desktop login revokes all GUI descendants',async()=>{
 const identity=pid=>{try{const stat=fs.readFileSync('/proc/'+pid+'/stat','utf8');return stat.slice(stat.lastIndexOf(')')+2).split(/\s+/)[19];}catch(e){if(e.code==='ENOENT')return null;throw e;}};
 const a=await login(),c=await display(a);const group=value(unit,'ControlGroup');const pids=fs.readFileSync('/sys/fs/cgroup'+group+'/cgroup.procs','utf8').trim().split('\n').filter(Boolean);
 const original=Object.fromEntries(pids.map(pid=>[pid,identity(pid)]).filter(([,start])=>start!==null));assert(Object.keys(original).length>0);
 const start=Date.now(),deadline=start+14000;await login();await until(()=>c.closed,Math.max(1,deadline-Date.now()));await stopped();
 await until(()=>Object.entries(original).every(([pid,born])=>identity(pid)!==born),Math.max(1,deadline-Date.now()));
 report.resources.revocation={original_process_start_times:original,elapsed_ms:Date.now()-start,remaining_original_processes:0};
});
await test('database failure stops GUI input and expires independent lease',async()=>{
 const a=await login(),c=await display(a);cmd('pg_ctlcluster',['15','main','stop','-m','fast']);
 try{c.send(Buffer.from([5,0,0,30,0,30]));await until(()=>c.closed,7000);await until(()=>value(unit,'ActiveState')!=='active',13000);}
 finally{cmd('pg_ctlcluster',['15','main','start']);}
});
await test('PID 1 reaps GUI when backend and worker stop and trusted guard crashes',async()=>{
 const a=await login();await display(a);const backend=value('wdlab-next.service','MainPID'),worker=value(unit,'MainPID'),guard=value('webdesktop-gui-lease@1.service','MainPID');
 const identity=pid=>{try{return fs.readFileSync('/proc/'+pid+'/stat','utf8').split(')').slice(1).join(')').trim().split(/\s+/)[19];}catch(e){if(e.code==='ENOENT')return null;throw e;}};
 const group=value(unit,'ControlGroup'),original=Object.fromEntries(fs.readFileSync('/sys/fs/cgroup'+group+'/cgroup.procs','utf8').trim().split('\n').filter(Boolean).map(pid=>[pid,identity(pid)]));
 assert(Object.keys(original).length>0);const began=Date.now();
 process.kill(Number(backend),'SIGSTOP');process.kill(Number(worker),'SIGSTOP');
 try{process.kill(Number(guard),'SIGKILL');await until(()=>value(unit,'MainPID')==='0'&&['inactive','failed'].includes(value(unit,'ActiveState'))&&Object.entries(original).every(([pid,born])=>identity(pid)!==born),7000);report.resources.guard_crash_cleanup={elapsed_ms:Date.now()-began,remaining_original_processes:0,original_process_start_times:original};}
 finally{process.kill(Number(backend),'SIGCONT');}
});
await test('GUI crash recovery and bounded stream pressure preserve backend health',async()=>{
 const a=await login();await terminal(a);const c=await display(a);const old=value(unit,'MainPID');process.kill(Number(old),'SIGKILL');await until(()=>c.closed);await stopped();
 const next=await display(a);assert.notEqual(value(unit,'MainPID'),old);
 const enc=Buffer.from([2,0,0,1,0,0,0,0]);next.send(enc);
 const request=Buffer.alloc(10);request[0]=3;request.writeUInt16BE(next.w,6);request.writeUInt16BE(next.h,8);next.send(request);
 await until(()=>next.received>128*1024);next.ws.pause();for(let i=0;i<4;i++)next.send(request);await delay(2000);
 const latencies=[];for(let i=0;i<10;i++){const start=performance.now();assert.equal((await fetch('http://127.0.0.1:3000/api/health',{signal:AbortSignal.timeout(4000)})).status,200);latencies.push(performance.now()-start);}
 report.resources.health_ms=latencies;report.resources.pressure=await measuredSnapshot();next.ws.resume();const fresh=await login();report.resources.login_ms=fresh.login_ms;await stopped();report.resources.after_close=await measuredSnapshot();
 try{const leftovers=cmd('ps',['-u','wdgui','-o','pid=,args=']);assert.equal(leftovers,'');}catch(error){assert.equal(error.status,1);}
 for(const name of ['wdlab-next.service','webdesktop-guest.service'])assert(!/oom [1-9]/.test(report.resources.pressure.cgroups[name]['memory.events']));
});
clearInterval(sample);report.total=report.passed+report.failed;report.resources.minimum_available_kib=minimumAvailable;report.resources.sampled_100ms_gui_peaks=peaks;report.resources.cpu_stat_start=cpuStart;report.resources.cpu_stat_end=fs.readFileSync('/proc/stat','utf8').split('\n')[0];report.resources.kernel_oom=cmd('dmesg').split('\n').filter(x=>/oom-kill:|out of memory/i.test(x));
assert(!report.resources.kernel_oom.some(x=>x.includes('CONSTRAINT_NONE')));
fs.writeFileSync(OUT+'/gui-'+phase+'.json',JSON.stringify(report,null,2));console.log(JSON.stringify({phase,total:report.total,passed:report.passed,failed:report.failed,skipped:0,not_executed:0}));process.exit(report.failed?1:0);
