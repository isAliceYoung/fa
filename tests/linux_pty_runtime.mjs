// Root is only the disposable VM test controller. Every command below crosses
// real public WebSocket -> authenticated Next -> wdterm -> systemd cgroup.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import WebSocket from '/opt/webdesktop/node_modules/ws/wrapper.mjs';

const phase = process.argv[2] || 'prepare';
const OUT = '/root/wd-systemd-evidence';
const HISTORY_BYTES=256*1024;
const pythonInput = script => 'python3 -c "import base64;exec(base64.b64decode(\'' + Buffer.from(script.replace(/\\n/g,'\n')).toString('base64') + '\'))"\n';
const config = Object.fromEntries(fs.readFileSync('/opt/webdesktop/.env','utf8').split('\n').filter(line=>line.includes('=')).map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)];}));
const command = (bin,args=[]) => execFileSync(bin,args,{encoding:'utf8',timeout:15000,stdio:['ignore','pipe','pipe']}).trim();
const ctl = (...args) => command('/usr/bin/systemctl',args);
const value = (unit,property) => ctl('show',unit,'-p',property,'--value');
const delay = ms => new Promise(resolve=>setTimeout(resolve,ms));
const eventually = async (predicate, ms=6000) => { const end=Date.now()+ms; while(Date.now()<end){if(await predicate())return;await delay(100);}throw new Error('Bounded assertion timed out');};
const report={phase,passed:0,failed:0,skipped:0,not_executed:0,tests:[],resources:{}};
const clients = new Set();
let minimumAvailable=Infinity,minimumSwap=Infinity;
const sampleTimer=setInterval(()=>{
  const info=fs.readFileSync('/proc/meminfo','utf8');
  minimumAvailable=Math.min(minimumAvailable,Number(/^MemAvailable:\s+(\d+)/m.exec(info)[1]));
  minimumSwap=Math.min(minimumSwap,Number(/^SwapFree:\s+(\d+)/m.exec(info)[1]));
},100);
async function login(){
  const r=await fetch('http://127.0.0.1:3000/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:config.DESKTOP_PASSWORD}),signal:AbortSignal.timeout(5000)});
  assert.equal(r.status,200);return {cookie:r.headers.getSetCookie()[0].split(';')[0],access:(await r.json()).sessionId};
}
async function status(auth,extra='',path='/api/term/ws'){
  return new Promise((resolve,reject)=>{
    const ws=new WebSocket('ws://127.0.0.1:3000'+path+'?sid='+randomBytes(12).toString('hex')+'&access='+encodeURIComponent(auth?.access||'')+extra,{headers:auth?{Cookie:auth.cookie}:{},handshakeTimeout:5000});
    ws.on('error',()=>{});ws.once('unexpected-response',(_req,r)=>{r.resume();ws.terminate();resolve(r.statusCode);});
    ws.once('open',()=>{ws.close();resolve(101);});setTimeout(()=>{ws.terminate();reject(new Error('Handshake timeout'));},6000).unref();
  });
}
async function terminal(auth,sid=randomBytes(12).toString('hex')){
  const deadline=Date.now()+12000;
  while(true){try{return await terminalOnce(auth,sid);}catch(error){if(!/Unexpected server response: 503/.test(error.message)||Date.now()>=deadline)throw error;await delay(250);}}
}
async function terminalOnce(auth,sid){
  const ws=new WebSocket(`ws://127.0.0.1:3000/api/term/ws?sid=${sid}&cols=80&rows=24&access=${encodeURIComponent(auth.access)}`,{headers:{Cookie:auth.cookie},handshakeTimeout:8000});
  const decoder=new TextDecoder();
  const client={ws,sid,log:'',bytes:0,controls:[],closed:false,send:m=>ws.send(JSON.stringify(m))};
  clients.add(client);
  ws.on('message',(data,binary)=>{if(binary){client.bytes+=data.length;client.log=(client.log+decoder.decode(data,{stream:true})).slice(-512*1024);}else client.controls.push(JSON.parse(data.toString()));});
  ws.on('close',()=>{client.closed=true;clients.delete(client);});ws.on('error',()=>{});
  await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject);});
  client.input=d=>client.send({t:'i',d});
  client.wait=async(text,ms=6000)=>eventually(()=>typeof text==='string'?client.log.includes(text):text.test(client.log),ms);
  await client.wait('webdesktop');
  return client;
}
const units=()=>[1,2].map(slot=>'webdesktop-pty@'+slot+'.service');
const active=unit=>value(unit,'ActiveState')==='active';
async function allStopped(){await eventually(()=>units().every(unit=>!active(unit)),7000);await delay(200);}
async function test(name,body){
  try {await body();report.passed++;report.tests.push({name,result:'passed'});console.log('PASS '+name);}
  catch(error){report.failed++;report.tests.push({name,result:'failed',error:String(error.stack).slice(0,2500)});console.error('FAIL '+name+' '+error.message);}
  finally {for(const client of clients){try{client.send({t:'kill'});client.ws.close();}catch{}}await allStopped().catch(()=>{});}
}

await test('real UID, socket activation, native PTY, ANSI, UTF-8, input, Ctrl+C, resize',async()=>{
  const auth=await login(),c=await terminal(auth);
  c.input("printf '__uid:%s__\\n' \"$(id -u)\"; printf '\\033[31m你好PTY\\033[0m\\n'\n");
  await c.wait('__uid:'+command('id',['-u','wdterm'])+'__');await c.wait('\u001b[31m你好PTY\u001b[0m');
  c.send({t:'r',c:111,r:37});await delay(150);c.input('stty size\n');await c.wait(/37 111/);
  c.input('sleep 30\n');await delay(250);c.input('\u0003');await delay(250);c.input("printf 'INTERRUPTED_%s\\n' ok\n");await c.wait('INTERRUPTED_ok');
  const unit=units().find(active),pid=value(unit,'MainPID');
  const proc=fs.readFileSync('/proc/'+pid+'/status','utf8');
  assert(proc.includes('Uid:\t'+command('id',['-u','wdterm'])+'\t'));assert(proc.includes('NoNewPrivs:\t1'));
  for(const key of ['CapEff','CapPrm','CapAmb','CapBnd'])assert(proc.includes(key+':\t0000000000000000'));
  const guardUnit=unit.replace('pty@','pty-lease@'),guardPid=value(guardUnit,'MainPID');
  const guardStatus=fs.readFileSync('/proc/'+guardPid+'/status','utf8');
  assert(guardStatus.includes('Uid:\t'+command('id',['-u','wdsvc'])+'\t'));assert(guardStatus.includes('NoNewPrivs:\t1'));
  for(const key of ['CapEff','CapPrm','CapAmb','CapBnd'])assert(guardStatus.includes(key+':\t0000000000000000'));
  assert.notEqual(value(guardUnit,'ControlGroup'),value(unit,'ControlGroup'));
  assert.notEqual(value(guardUnit,'ControlGroup'),value('wdlab-next.service','ControlGroup'));
  try{
    command('/usr/sbin/runuser',['-u','wdterm','--','python3','-I','-c',"import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/webdesktop-pty/lease-"+unit.match(/@(\d)/)[1]+".sock')"]);
    assert.fail('Terminal connected directly to trusted lease guard');
  }catch(error){assert(String(error.stderr).includes('PermissionError'));}
  fs.writeFileSync(OUT+'/pty-'+phase+'-guard.txt',guardStatus+'\n'+ctl('show',guardUnit,'-p','ControlGroup','-p','MemoryMax','-p','TasksMax','-p','CPUQuotaPerSecUSec'));
  fs.writeFileSync(OUT+'/pty-'+phase+'-identity.txt',proc+'\n'+ctl('show',unit,'-p','ControlGroup','-p','MemoryMax','-p','TasksMax','-p','LimitNOFILE','-p','CPUQuotaPerSecUSec','-p','ProtectControlGroups','-p','PrivateNetwork'));
});
await test('all public handshake auth failures and still-disabled GUI/browser',async()=>{
  const auth=await login();assert.equal(await status(null),401);
  assert.equal(await status({...auth,cookie:'wd_session=invalid'}),401);
  assert.equal(await status({...auth,access:'wrong-page'}),401);
  const fresh=await login();assert.equal(await status(auth),401);
  for(const path of ['/api/gui/ws','/api/browser/ws'])assert.equal(await status(fresh,'',path),503);
  for(const extra of ['&cmd=id','&cwd=/root','&uid=0','&env=SECRET'])assert.equal(await status(fresh,extra),503);
  assert(units().every(unit=>!active(unit)));
});
await test('new login revokes live input and entire execution cgroup',async()=>{
  const auth=await login(),c=await terminal(auth),marker='/home/wdguest/revoked-'+randomBytes(8).toString('hex');
  await login();c.input('touch '+marker+'\n');await eventually(()=>c.closed);await allStopped();assert(!fs.existsSync(marker));
});
await test('short disconnect reconnects same Bash and bounded output replay',async()=>{
  const auth=await login(),c=await terminal(auth);c.input('export WD_RECONNECT_PROBE=kept; printf "REPLAY_%s\\n" kept\n');await c.wait('REPLAY_kept');
  c.ws.close();await eventually(()=>c.closed);const resumed=await terminal({...auth,cookie:auth.cookie+'; unrelated=does-not-change-owner'},c.sid);await resumed.wait('REPLAY_kept');
  resumed.input('printf "VALUE_%s\\n" "$WD_RECONNECT_PROBE"\n');await resumed.wait('VALUE_kept');
  resumed.input("python3 -c \"print('x'*400000);print('HISTORY_'+'DONE')\"\n");await resumed.wait('HISTORY_DONE');resumed.ws.close();await eventually(()=>resumed.closed);
  const replay=await terminal(auth,c.sid);await delay(200);assert(replay.bytes<=HISTORY_BYTES+65536);assert(replay.controls.some(m=>m.t==='gap'));
});
await test('wdterm cannot access source, secrets, agent state, IPC, proc or internal network',async()=>{
  const auth=await login(),c=await terminal(auth);
  const agent=value('webdesktop-guest.service','MainPID'),backend=value('wdlab-next.service','MainPID');
  const script=`import os,socket,json\npaths=['/opt/webdesktop/.env','/opt/webdesktop/server.mjs','/root/deploy-key.test','/home/wdguest/.wd-state/agent.lock','/proc/${agent}/environ','/proc/${backend}/environ']\nfor p in paths:\n try:open(p,'rb').read(1);raise AssertionError('read allowed')\n except (PermissionError,FileNotFoundError):pass\nfor p in ['/run/webdesktop-guest/guest.sock','/var/lib/webdesktop/.webdesktop-http/next.sock']:\n try:s=socket.socket(socket.AF_UNIX);s.connect(p);raise AssertionError('IPC allowed')\n except (PermissionError,FileNotFoundError):pass\nfor pid in [1,${agent},${backend}]:\n try:os.kill(pid,0);raise AssertionError('signal allowed')\n except PermissionError:pass\ntry:socket.socket(socket.AF_INET);raise AssertionError('TCP allowed')\nexcept OSError:pass\nprint('KERNEL_BOUNDARIES_CONFIRMED')`;
  c.input(pythonInput(script));await c.wait('KERNEL_BOUNDARIES_CONFIRMED');
  c.input('env\n');await delay(200);for(const secret of [config.AUTH_KEY,config.DATABASE_URL,config.DESKTOP_PASSWORD])assert(!c.log.includes(secret));
  c.input("printf 'echo INTERPRETED_%s\\n' OK > /home/wdguest/pty-script.sh; chmod +x /home/wdguest/pty-script.sh; /home/wdguest/pty-script.sh; bash /home/wdguest/pty-script.sh\n");
  await c.wait('Permission denied');await c.wait('INTERPRETED_OK');
});
await test('setsid, background daemon, stopped worker and PID reuse cannot escape unit cleanup',async()=>{
  const outsider=spawn('/bin/sleep',['60']);
  try {
    const auth=await login(),c=await terminal(auth);
    c.input("setsid /bin/sh -c 'sleep 120' </dev/null >/dev/null 2>&1 & sleep 120 & printf 'TREE_%s\\n' ready\n");await c.wait('TREE_ready');
    const unit=units().find(active),group=value(unit,'ControlGroup');
    const pids=fs.readFileSync('/sys/fs/cgroup'+group+'/cgroup.procs','utf8').trim().split('\n');assert(pids.length>=4);
    const starts=new Map(pids.map(pid=>[pid,fs.readFileSync('/proc/'+pid+'/stat','utf8').split(' ').at(21)]));
    c.input('kill -STOP "$PPID"\n');await delay(200);c.send({t:'kill'});await allStopped();
    assert(!fs.existsSync('/sys/fs/cgroup'+group));
    for(const [pid,start]of starts)if(fs.existsSync('/proc/'+pid+'/stat'))assert.notEqual(fs.readFileSync('/proc/'+pid+'/stat','utf8').split(' ').at(21),start);
    assert.equal(outsider.exitCode,null); // Outside process must survive all cleanup.
  } finally {outsider.kill();}
});
await test('fork and FD exhaustion respect kernel per-session and aggregate budgets',async()=>{
  const auth=await login(),c=await terminal(auth);
  c.input('ulimit -n\n');await c.wait('128');
  c.input(pythonInput("import os,errno\nhandles=[]\ntry:\n for i in range(300):handles.append(os.open('/dev/null',os.O_RDONLY))\nexcept OSError as e:\n assert e.errno==errno.EMFILE;print('FD_LIMIT_CONFIRMED')\nfinally:\n for fd in handles:os.close(fd)"));
  await c.wait('FD_LIMIT_CONFIRMED');
  const script="import subprocess,time,errno\nchildren=[]\ntry:\n for i in range(100):children.append(subprocess.Popen(['/bin/sleep','60']))\nexcept OSError as e:\n assert e.errno==errno.EAGAIN;print('TASK_LIMIT_CONFIRMED',flush=True)\ntime.sleep(60)";
  c.input(pythonInput(script));await c.wait('TASK_LIMIT_CONFIRMED');
  const unit=units().find(active);assert(Number(value(unit,'TasksCurrent'))<=32);assert(Number(value('webdesktop-pty.slice','TasksCurrent'))<=64);
  c.send({t:'kill'});await allStopped();
});
await test('actual kernel PID reuse does not make cgroup cleanup kill an unrelated wdterm process',async()=>{
  const auth=await login(),c=await terminal(auth);
  c.input("/bin/sleep .1 & printf 'OLD_PID_%s\\n' $!\n");await c.wait(/OLD_PID_\d+/);
  const target=Number(/OLD_PID_(\d+)/.exec(c.log)[1]);await delay(300);assert(!fs.existsSync('/proc/'+target));
  const original=fs.readFileSync('/proc/sys/kernel/pid_max','utf8');
  let outsider,start;
  try{
    const maximum=Math.max(4096,Math.ceil((target+512)/1024)*1024);
    fs.writeFileSync('/proc/sys/kernel/pid_max',String(maximum));
    const script=`import os,sys,time,json,signal\ntarget=int(sys.argv[1]);uid=int(sys.argv[2]);gid=int(sys.argv[3]);maximum=int(sys.argv[4])\nfor i in range(maximum*3):\n child=os.fork()\n if child==0:\n  if os.getpid()!=target:os._exit(0)\n  null=os.open('/dev/null',os.O_RDWR)\n  for fd in range(3):os.dup2(null,fd)\n  os.closerange(3,256);os.setgroups([]);os.setgid(gid);os.setuid(uid);time.sleep(60);os._exit(0)\n if child==target:\n  print(json.dumps({'pid':child,'forks':i+1}),flush=True);break\n os.waitpid(child,0)\nelse:raise RuntimeError('Actual PID reuse not achieved')`;
    const result=JSON.parse(execFileSync('/usr/bin/python3',['-I','-c',script.replace(/\\n/g,'\n'),String(target),command('id',['-u','wdterm']),command('id',['-g','wdguest']),String(maximum)],{encoding:'utf8',timeout:45000}));
    outsider=result.pid;assert.equal(outsider,target);
    start=fs.readFileSync('/proc/'+outsider+'/stat','utf8').split(' ').at(21);
    c.send({t:'kill'});await allStopped();
    assert.equal(fs.readFileSync('/proc/'+outsider+'/stat','utf8').split(' ').at(21),start);
    report.resources.pid_reuse={reused_pid:outsider,forks:result.forks,outside_process_survived:true};
  }finally{
    fs.writeFileSync('/proc/sys/kernel/pid_max',original);
    if(outsider&&fs.existsSync('/proc/'+outsider+'/stat')&&fs.readFileSync('/proc/'+outsider+'/stat','utf8').split(' ').at(21)===start)process.kill(outsider,'SIGKILL');
  }
});
await test('memory exhaustion kills only constrained guest cgroup',async()=>{
  const auth=await login(),c=await terminal(auth);c.input("python3 -c 'x=bytearray(200*1024*1024);input()'\n");
  await eventually(()=>c.closed,10000);await allStopped();assert.equal((await fetch('http://127.0.0.1:3000/api/health')).status,200);
});
await test('database failure blocks new input and expires guest execution',async()=>{
  const auth=await login(),c=await terminal(auth),marker='/home/wdguest/db-down-'+randomBytes(8).toString('hex');
  command('/usr/bin/pg_ctlcluster',['15','main','stop','-m','fast']);
  try {c.input('touch '+marker+'\n');await eventually(()=>c.closed);await eventually(()=>units().every(unit=>!active(unit)),13000);assert(!fs.existsSync(marker));assert.equal(await status(auth),503);}
  finally {command('/usr/bin/pg_ctlcluster',['15','main','start']);}
});
await test('independent monotonic lease kills session while whole Node backend is SIGSTOP',async()=>{
  const auth=await login(),c=await terminal(auth),backend=value('wdlab-next.service','MainPID');
  c.input("setsid /bin/sleep 120 >/dev/null 2>&1 & printf 'LEASE_%s\\n' armed\n");await c.wait('LEASE_armed');
  process.kill(Number(backend),'SIGSTOP');
  try {await eventually(()=>units().every(unit=>!active(unit)),15000);}
  finally {process.kill(Number(backend),'SIGCONT');}
  await eventually(()=>c.closed,6000);
});
await test('backend service crash plus stopped worker still reaps all descendants',async()=>{
  const auth=await login(),c=await terminal(auth);
  c.input('kill -STOP "$PPID"\n');await delay(250);
  ctl('kill','--kill-who=main','--signal=SIGKILL','wdlab-next.service');
  try {await allStopped();}
  finally {ctl('restart','wdlab-next.service');await eventually(async()=>{try{return(await fetch('http://127.0.0.1:3000/api/health')).status===200;}catch{return false;}},15000);}
  // Remove both userspace cleanup paths: PID 1's BindsTo dependency must act
  // even while Node and the untrusted worker cannot process socket closure.
  const fresh=await login(),other=await terminal(fresh);
  other.input('kill -STOP "$PPID"\n');await delay(200);
  const worker=units().find(active),guard=worker.replace('pty@','pty-lease@');
  const backend=Number(value('wdlab-next.service','MainPID'));
  process.kill(backend,'SIGSTOP');
  try {ctl('kill','--kill-who=main','--signal=SIGKILL',guard);await allStopped();}
  finally {process.kill(backend,'SIGCONT');}
  await eventually(()=>other.closed);
});
await test('stop-only service policy denies root/transient/property/other service control',async()=>{
  for(const args of [['start','ssh.service'],['stop','ssh.service'],['start','webdesktop-pty@1.service'],['set-property','webdesktop-pty@1.service','MemoryMax=infinity']]){
    assert.throws(()=>command('/usr/sbin/runuser',['-u','wdsvc','--','systemctl','--no-ask-password',...args]));
  }
  assert.throws(()=>command('/usr/sbin/runuser',['-u','wdsvc','--','systemd-run','--no-ask-password','--unit=webdesktop-pty@1.service','--property=User=root','/bin/true']));
  assert.throws(()=>command('/usr/sbin/runuser',['-u','wdterm','--','systemctl','--no-ask-password','stop','webdesktop-pty@1.service']));
  command('/usr/sbin/runuser',['-u','wdsvc','--','systemctl','--no-ask-password','stop','webdesktop-pty@1.service']);
});
await test('two slots, streaming pressure and file IPC coexist without backend OOM',async()=>{
  const auth=await login(),first=await terminal(auth),second=await terminal(auth);
  assert.equal(await status(auth),503);
  const backend=Number(value('wdlab-next.service','MainPID'));
  const before=Number(/^VmRSS:\s+(\d+)/m.exec(fs.readFileSync('/proc/'+backend+'/status','utf8'))[1]);
  first.input("python3 -c 'while True:pass' & yes\n");first.ws.pause();
  second.input("python3 -c 'while True:pass' & yes\n");second.ws.pause();
  const headers={Cookie:auth.cookie,'X-Desktop-Session':auth.access};
  const upload=fetch('http://127.0.0.1:3000/api/sys/upload?dir=~/Documents&name=pressure.bin&id='+randomBytes(12).toString('hex')+'&done=1',{method:'POST',headers,body:new Uint8Array(8*1024*1024),signal:AbortSignal.timeout(10000)});
  const response=await upload;assert.equal(response.status,200);const file=(await response.json()).path;
  const download=await fetch('http://127.0.0.1:3000/api/sys/download?path='+encodeURIComponent(file),{headers,signal:AbortSignal.timeout(10000)});
  assert.equal(download.status,200);const reader=download.body.getReader();await reader.read();await reader.cancel();
  const zip=await fetch('http://127.0.0.1:3000/api/sys/download?path=~/Documents',{headers,signal:AbortSignal.timeout(10000)});
  assert.equal(zip.status,200);assert.equal(zip.headers.get('content-type'),'application/zip');
  const archive=zip.body.getReader();await archive.read();await archive.cancel();
  await delay(3000);assert.equal((await fetch('http://127.0.0.1:3000/api/health')).status,200);
  const after=Number(/^VmRSS:\s+(\d+)/m.exec(fs.readFileSync('/proc/'+backend+'/status','utf8'))[1]);
  assert(after-before<64*1024);report.resources.backend_rss_before_kib=before;report.resources.backend_rss_after_kib=after;
  report.resources.backend=ctl('show','wdlab-next.service','-p','MemoryCurrent','-p','MemoryPeak','-p','MemoryMax');
  report.resources.terminals=ctl('show','webdesktop-pty.slice','-p','MemoryCurrent','-p','MemoryPeak','-p','MemoryMax','-p','TasksCurrent','-p','TasksMax');
  report.resources.cgroups={};
  for(const unit of ['wdlab-next.service','webdesktop-guest.service','webdesktop-pty.slice','webdesktop-pty-lease@1.service','webdesktop-pty-lease@2.service']){
    const group=value(unit,'ControlGroup');const sample={};
    for(const field of ['memory.current','memory.peak','memory.events','pids.current','cpu.stat']){
      const path='/sys/fs/cgroup'+group+'/'+field;
      if(group&&fs.existsSync(path))sample[field]=fs.readFileSync(path,'utf8').trim();
    }
    report.resources.cgroups[unit]=sample;
  }
  assert(Number(/^nr_throttled (\d+)/m.exec(report.resources.cgroups['webdesktop-pty.slice']['cpu.stat'])[1])>0);
  const oom=command('/usr/bin/dmesg',[]).split('\n').filter(line=>/oom-kill:|Out of memory|out of memory/.test(line));
  report.resources.kernel_oom_events=oom;
  assert(!oom.some(line=>/constraint=CONSTRAINT_NONE/.test(line)));
  first.ws.resume();second.ws.resume();
});
report.total=report.passed+report.failed;
report.resources.meminfo=fs.readFileSync('/proc/meminfo','utf8').split('\n').slice(0,5).join('\n');
report.resources.disk=command('/bin/df',['-PB1','/','/home/wdguest']);
clearInterval(sampleTimer);
report.resources.sampled_100ms_minimum_available_kib=minimumAvailable;
report.resources.sampled_100ms_minimum_swap_free_kib=minimumSwap;
fs.writeFileSync(OUT+'/pty-'+phase+'.json',JSON.stringify(report,null,2));
console.log(JSON.stringify({phase,total:report.total,passed:report.passed,failed:report.failed,skipped:0,not_executed:0}));
process.exit(report.failed?1:0);
