import fs from 'node:fs/promises';
import os from 'node:os';
import net from 'node:net';
import { execFile } from 'node:child_process';
import { WebSocketServer, WebSocket } from 'ws';
import { checkTerminalAuth } from './pty-gateway.mjs';
import { graphicalAdmission } from './resource-monitor.mjs';
import { CLIPBOARD_LIMITS, ClipboardRate, validReply } from './text-clipboard.mjs';
import { IMAGE_LIMITS, ImageAssembly, ImageRate, sendImageChunks, sha256Hex, validImageReply } from './image-clipboard.mjs';

const PACKAGE='/usr/local/lib/webdesktop-gui', HIGH=1024*1024, LOW=256*1024;
const packet=value=>Buffer.from(JSON.stringify(value)+'\n');
export async function guiGateway(authSocket) {
  const ready=await fs.lstat(PACKAGE+'/ready').catch(e=>{if(e.code!=='ENOENT')throw e;return null;});
  if(!ready)return null;
  const identity=os.userInfo();
  if(identity.username!=='wdsvc'||!identity.uid)throw new Error('Service identity required');
  for(const file of ['', '/worker.py','/guard.py','/rfb_input.py','/text_clipboard.py','/image_clipboard.py','/openbox.xml','/dbus.conf','/ready']){
    const info=await fs.lstat(PACKAGE+file);
    if(info.uid!==0||info.isSymbolicLink()||(file?!info.isFile()||info.nlink!==1||(info.mode&0o777)!==0o644:!info.isDirectory()||(info.mode&0o777)!==0o755))throw new Error('Unsafe GUI package');
  }
  if(await fs.readFile(PACKAGE+'/ready','utf8')!=='gui-basic-v1\n')throw new Error('Unknown GUI protocol');
  const browserPackage='/usr/local/lib/webdesktop-browser';let browserEnabled=false;
  if(await fs.lstat(browserPackage+'/ready').catch(e=>{if(e.code!=='ENOENT')throw e;return null;})){
    for(const file of ['', '/worker.py','/network.sh','/network.nft','/resolv.conf','/ready']){
      const st=await fs.lstat(browserPackage+file),mode=file==='/network.sh'?0o755:file?0o644:0o755;
      if(st.uid!==0||st.isSymbolicLink()||(st.mode&0o777)!==mode||(file?(!st.isFile()||st.nlink!==1):!st.isDirectory()))throw new Error('Unsafe browser package');
    }
    if(await fs.readFile(browserPackage+'/ready','utf8')!=='chromium-visible-v1\n')throw new Error('Browser protocol');browserEnabled=true;
  }
  const wss=new WebSocketServer({noServer:true,maxPayload:65536,perMessageDeflate:false});
  let current=null,last=null;const starts=[];
  const stages=new Set(['request','authenticated','resource_check','lease_ready','gui_socket','gui_worker','x11_ready','dbus_started','ime_ready','openbox_started','gui_ready','browser_socket','browser_worker','browser_spawned','browser_visible','browser_ready','websocket_ready']);
  function mark(s,name,workerMs){
    if(!stages.has(name))throw new Error('Unknown GUI stage');
    if(Object.hasOwn(s.timings,name))throw new Error('Duplicate GUI stage');
    s.stage=name;s.timings[name]=Date.now()-s.started;
    if(Number.isFinite(workerMs))s.workerTimings[name]=workerMs;
    console.info(JSON.stringify({event:'gui_start_stage',stage:name,elapsed_ms:s.timings[name],...(Number.isFinite(workerMs)?{worker_ms:workerMs}:{})}));
  }
  function view(s){return {state:s.stopping?'closed':s.client?'open':'starting',stage:s.stage,elapsedMs:Date.now()-s.started,timings:s.timings,workerTimings:s.workerTimings,...(s.stopReason?{reason:s.stopReason}:{})};}
  const stopUnit=()=>new Promise(resolve=>execFile('/usr/bin/systemctl',['--no-ask-password','stop','webdesktop-gui@1.service'],
    {env:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8'},timeout:5000,maxBuffer:4096},e=>resolve(!e)));
  const unitGone=async(unit,tries)=>{
    for(let i=0;i<tries;i++){
      const gone=await new Promise(resolve=>execFile('/usr/bin/systemctl',['show',unit,'-p','MainPID','--value'],{env:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8'},timeout:1000,maxBuffer:128},(e,out)=>resolve(!e&&out.trim()==='0')));
      if(gone)return true;await new Promise(r=>setTimeout(r,100));
    }return false;
  };
  const guardGone=()=>unitGone('webdesktop-gui-lease@1.service',60);
  function stop(s,reason='session_end'){
    if(s.stopping)return;s.stopping=true;s.stopReason=reason;clearInterval(s.timer);clearInterval(s.flow);clearTimeout(s.startTimer);
    console.info(JSON.stringify({event:'gui_stop',reason,stage:s.stage,elapsed_ms:Date.now()-s.started}));
    if(last?.sid!==s.sid||last.value.state!=='waiting_resources')last={sid:s.sid,cookie:s.cookie,access:s.access,until:Date.now()+30000,value:view(s)};
    if(s.client?.readyState===WebSocket.OPEN){s.client.resume();s.client.close(1013);const t=setTimeout(()=>s.client.terminate(),1000);t.unref();}
    // A pending text/image clipboard request never outlives its GUI session.
    if(s.clip?.resolve){clearTimeout(s.clip.timer);s.clip.resolve({code:'GUI_INACTIVE'});}
    // Input is closed above. Visible Chromium still needs its private X11 and
    // D-Bus while it shuts itself down; destroying the display or ending the
    // lease first makes it abort, and every next start offers "Restore pages".
    // Order the teardown only: wait (bounded, read-only) for the browser unit,
    // then release the display and lease exactly as before.
    s.browser?.destroy();s.abortCleanup?.();
    if(!s.browser){s.ipc?.destroy();s.guard?.end();}else s.ipc?.pause();
    s.cleanup=(async()=>{await s.preparedDone;if(s.browser){s.browser.destroy();await unitGone('webdesktop-browser@1.service',40);}s.ipc?.destroy();s.guard?.end();const stopped=await stopUnit();const closed=await guardGone();if(stopped&&closed&&current===s)current=null;})();
  }
  async function verify(s){
    if(s.stopping||s.blocked)return false;
    const status=await checkTerminalAuth(authSocket,s.cookie,s.access);
    if(s.stopping||s.blocked)return false;
    if(status===200){s.guard.write('renew\n');return true;}
    s.blocked=true;
    if(status===401)stop(s,'authentication_revoked');
    else if(s.client?.readyState===WebSocket.OPEN){s.client.resume();s.client.close(1013);}
    return false;
  }
  async function connect(name,browser=false){
    const directory=browser?'/run/webdesktop-browser':'/run/webdesktop-gui',path=directory+'/'+name+'-1.sock';
    const [parent,info]=await Promise.all([fs.lstat(directory),fs.lstat(path)]);
    if(!parent.isDirectory()||parent.uid!==0||parent.gid!==identity.gid||(parent.mode&0o777)!==0o750||!info.isSocket()||info.uid!==0||info.gid!==identity.gid||(info.mode&0o777)!==0o660)throw new Error('Unsafe GUI IPC');
    return net.createConnection(path);
  }
  return {
    cancel:stop,
    // Explicit text clipboard for the CURRENT session only. Returns
    // {code[, text]}; text is never logged.
    async clipboard(sid,cookie,access,op,text){
      const s=current,started=Date.now();
      if(!s||s.sid!==sid||s.cookie!==cookie||s.access!==access||s.stopping||s.blocked||s.client?.readyState!==WebSocket.OPEN)return {code:'GUI_INACTIVE'};
      if(s.clip)return {code:'BUSY'};
      if(!s.clipRate.take())return {code:'RATE_LIMITED'};
      const pending={n:++s.clipSeq,op};s.clip=pending;let result={code:'UNAVAILABLE'};
      try{
        if(!(await verify(s))||s.stopping||current!==s)return result={code:'GUI_INACTIVE'};
        result=await new Promise(resolve=>{
          pending.resolve=resolve;
          pending.timer=setTimeout(()=>{s.clipLate=pending.n;resolve({code:'TIMEOUT'});},CLIPBOARD_LIMITS.replyMs);
          s.ipc.write(packet(op==='write'?{t:'clip',op,n:pending.n,text}:{t:'clip',op,n:pending.n}));
        });
        return result;
      }finally{
        clearTimeout(pending.timer);if(s.clip===pending)s.clip=null;
        console.info(JSON.stringify({event:'gui_clipboard',op,code:result.code,bytes:Buffer.byteLength(op==='write'?text:(result.text??''),'utf8'),elapsed_ms:Date.now()-started}));
      }
    },
    // Explicit PNG image clipboard for the CURRENT session only. Shares the
    // single clipboard slot with text. Returns {code[, data, info]}; image
    // bytes are never logged or stored.
    async image(sid,cookie,access,op,data){
      const s=current,started=Date.now();
      if(!s||s.sid!==sid||s.cookie!==cookie||s.access!==access||s.stopping||s.blocked||s.client?.readyState!==WebSocket.OPEN)return {code:'GUI_INACTIVE'};
      if(s.clip)return {code:'BUSY'};
      if(!s.imageRate.take())return {code:'RATE_LIMITED'};
      const pending={n:++s.clipSeq,op,kind:'image',done:false};s.clip=pending;let result={code:'UNAVAILABLE'};
      const finish=value=>{if(pending.done)return;pending.done=true;clearTimeout(pending.timer);pending.resolve(value);};
      try{
        if(!(await verify(s))||s.stopping||current!==s)return result={code:'GUI_INACTIVE'};
        result=await new Promise(resolve=>{
          pending.resolve=resolve;
          pending.finish=finish;
          // Bounded overall time; a late worker reply/chunk for this n is dropped and the worker is told to stop.
          pending.timer=setTimeout(()=>{finish({code:'TIMEOUT'});},IMAGE_LIMITS.replyMs);
          if(op==='write'){
            s.ipc.write(packet({t:'img',op:'write',n:pending.n,size:data.length,sha256:sha256Hex(data)}));
            sendImageChunks(s.ipc,pending.n,data,{aborted:()=>pending.done||s.stopping||current!==s}).then(sent=>{if(!sent)finish({code:pending.done?'TIMEOUT':'GUI_INACTIVE'});},()=>finish({code:'UNAVAILABLE'}));
          }else s.ipc.write(packet({t:'img',op:'read',n:pending.n}));
        });
        return result;
      }finally{
        pending.done=true;clearTimeout(pending.timer);pending.assembly=null;
        if(s.clip===pending){
          s.clip=null;
          if(result.code!=='OK'&&!s.stopping&&current===s&&!s.ipc.destroyed){s.imageDrop=pending.n;s.ipc.write(packet({t:'img',op:'cancel',n:pending.n}));}
        }
        console.info(JSON.stringify({event:'gui_image_clipboard',op,code:result.code,bytes:op==='write'?data.length:(result.data?.length??0),elapsed_ms:Date.now()-started}));
      }
    },
    progress(sid,cookie,access){
      if(last?.sid===sid&&last.cookie===cookie&&last.access===access&&last.until>Date.now()&&last.value.state==='waiting_resources')return last.value;
      if(current?.sid===sid&&current.cookie===cookie&&current.access===access)return {...view(current),...(current.stopping?{state:'stopping'}:{})};
      if(last?.sid===sid&&last.cookie===cookie&&last.access===access&&last.until>Date.now())return last.value;
      return {state:'pending',stage:'request',timings:{},workerTimings:{}};
    },
    async prepare(url,cookie,{signal,requestStarted,authenticatedAt}={}){
      if(signal?.aborted)throw new Error('GUI request cancelled');
      if([...url.searchParams.keys()].some(k=>!['sid','access','app','w','h'].includes(k)||url.searchParams.getAll(k).length!==1))throw new Error('Fixed GUI parameters required');
      const sid=url.searchParams.get('sid'),app=url.searchParams.get('app')||'pcmanfm';
      const w=Number(url.searchParams.get('w')||1024),h=Number(url.searchParams.get('h')||640);
      if(!/^[a-f0-9]{24,64}$/.test(sid||'')||!['desktop','pcmanfm',...(browserEnabled?['chromium']:[])].includes(app)||!Number.isInteger(w)||!Number.isInteger(h)||w<640||w>1600||h<480||h>1000)throw new Error('GUI budget');
      while(starts.length&&starts[0]<=Date.now()-10000)starts.shift();
      if(current||starts.length>=4)throw new Error('GUI capacity');
      const s={sid,cookie,access:url.searchParams.get('access')||'',clip:null,clipSeq:0,clipLate:0,clipRate:new ClipboardRate(),imageRate:new ImageRate(),imageDrop:0,started:requestStarted||Date.now(),timings:{request:0,authenticated:Math.max(0,(authenticatedAt||Date.now())-(requestStarted||Date.now()))},workerTimings:{},stage:'authenticated',rate:0,epoch:Date.now(),initial:[],initialBytes:0};current=s;starts.push(Date.now());
      s.preparedDone=new Promise(resolve=>{s.preparedResolve=resolve;});
      const aborted=()=>stop(s,'upgrade_aborted');signal?.addEventListener('abort',aborted,{once:true});s.abortCleanup=()=>signal?.removeEventListener('abort',aborted);
      try{
        const policy=await fs.lstat('/etc/webdesktop-resources/policy.json').catch(e=>{if(e.code!=='ENOENT')throw e;return null;});
        if(policy){
          const admission=await graphicalAdmission();
          if(admission!=='ready'){
            last={sid,cookie,access:s.access,until:Date.now()+30000,value:{state:'waiting_resources',reason:admission==='unknown'?'telemetry_unavailable':'resource_pressure',stage:'resource_check',timings:s.timings,workerTimings:{}}};
            throw new Error('GUI waiting for resources');
          }
        }
        mark(s,'resource_check');
        s.guard=await connect('lease');s.guardClosed=new Promise(r=>s.guard.once('close',()=>r(true)));
        if(s.stopping){s.guard.destroy();throw new Error('GUI cancelled');}
        s.guard.on('error',()=>stop(s,'lease_error'));s.guard.on('close',()=>stop(s,'lease_closed'));
        await new Promise((resolve,reject)=>{
          let data='';const timer=setTimeout(()=>reject(new Error('GUI lease timeout')),3000);
          s.guard.once('close',()=>{clearTimeout(timer);reject(new Error('GUI guard closed'));});
          s.guard.on('data',chunk=>{data+=chunk;if(data==='READY\n'){clearTimeout(timer);resolve();}else if(data.length>128){clearTimeout(timer);reject(new Error('GUI guard protocol'));}});
        });
        mark(s,'lease_ready');
        let checking=false;s.lastPong=Date.now();
        s.timer=setInterval(async()=>{if(checking)return;checking=true;try{await verify(s);}finally{checking=false;}if(s.client?.readyState===WebSocket.OPEN){if(Date.now()-s.lastPong>20000)s.client.terminate();else s.client.ping();}},5000);
        s.ipc=await connect('session');s.ipc.on('error',()=>stop(s,'display_error'));s.ipc.on('close',()=>stop(s,'display_closed'));
        mark(s,'gui_socket');
        if(s.stopping){s.ipc.destroy();throw new Error('GUI cancelled');}
        await new Promise((resolve,reject)=>{
          let incoming=Buffer.alloc(0),initialized=false;const timer=setTimeout(()=>reject(new Error('GUI startup timeout')),10000);
          s.ipc.once('connect',()=>s.ipc.write(packet({t:'open',app:app==='chromium'?'desktop':app,w,h})));
          s.ipc.once('error',()=>{clearTimeout(timer);reject(new Error('GUI unavailable'));});
          s.ipc.once('close',()=>{clearTimeout(timer);reject(new Error('GUI closed during startup'));});
          s.ipc.on('data',chunk=>{
            try{
              incoming=Buffer.concat([incoming,chunk]);if(incoming.length>131072)throw new Error('GUI IPC budget');let end;
              while((end=incoming.indexOf(10))!==-1){
                if(end>65536)throw new Error('GUI frame budget');const m=JSON.parse(incoming.subarray(0,end));incoming=incoming.subarray(end+1);
                if(!initialized){if(m.t==='stage'){if(!Number.isInteger(m.ms)||m.ms<0||m.ms>10000)throw new Error('GUI stage budget');mark(s,m.stage,m.ms);continue;}if(m.t!=='ready')throw new Error('GUI init');initialized=true;mark(s,'gui_ready');clearTimeout(timer);resolve();continue;}
                if(m.t==='img'||m.t==='imgc'){
                  // Image replies/chunks: only for the one outstanding image
                  // request; anything for a cancelled/timed-out n is dropped.
                  const c=s.clip;
                  if(!(c&&c.kind==='image'&&m.n===c.n)){if(s.imageDrop&&m.n===s.imageDrop)continue;throw new Error('GUI image protocol');}
                  if(c.done)continue;
                  if(m.t==='img'){
                    if(c.assembly)throw new Error('GUI image protocol');
                    const reply=validImageReply(m,c.n,c.op);
                    if(!reply)throw new Error('GUI image protocol');
                    if(reply.ok&&c.op==='read')c.assembly=new ImageAssembly(c.n,reply.size,reply.sha256);
                    else c.finish({code:reply.code});
                  }else{
                    if(!c.assembly||!c.assembly.add(m))throw new Error('GUI image protocol');
                    if(c.assembly.complete)c.finish(c.assembly.result());
                  }
                  continue;
                }
                if(m.t==='clip'){
                  // Only the one outstanding request (or the single reply that
                  // arrived after its own timeout) may answer; anything else is
                  // a protocol violation that closes the session.
                  if(s.clipLate&&m.n===s.clipLate&&!(s.clip&&m.n===s.clip.n)){s.clipLate=0;continue;}
                  const reply=s.clip&&validReply(m,s.clip.n,s.clip.op);
                  if(!reply)throw new Error('GUI clipboard protocol');
                  clearTimeout(s.clip.timer);s.clip.resolve(reply);continue;
                }
                if(m.t!=='d'||typeof m.d!=='string'||!/^[A-Za-z0-9+/]*={0,2}$/.test(m.d))throw new Error('GUI protocol');
                const bytes=Buffer.from(m.d,'base64');if(!bytes.length||bytes.length>16384||bytes.toString('base64')!==m.d)throw new Error('GUI frame');
                s.rate+=bytes.length;
                if(s.client?.readyState===WebSocket.OPEN)s.client.send(bytes,{binary:true});
                else {s.initialBytes+=bytes.length;if(s.initialBytes>65536)throw new Error('GUI initial budget');s.initial.push(bytes);}
                if(s.rate>=4*1024*1024||s.client?.bufferedAmount>HIGH)s.ipc.pause();
              }
            }catch{clearTimeout(timer);reject(new Error('GUI protocol failed'));stop(s,'display_protocol');}
          });
        });
        if(app==='chromium'){
          s.browser=await connect('session',true);s.browser.on('error',()=>stop(s,'browser_error'));s.browser.on('close',()=>stop(s,'browser_closed'));
          mark(s,'browser_socket');
          if(s.stopping){s.browser.destroy();throw new Error('GUI cancelled');}
          await new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>reject(new Error('Browser startup timeout')),5000);let data='';
            s.browser.once('connect',()=>s.browser.write(packet({t:'open'})));
            s.browser.once('close',()=>{clearTimeout(timer);reject(new Error('Browser stopped'));});
            s.browser.on('data',chunk=>{try{data+=chunk;if(data.length>2048)throw new Error('Browser frame bound');let end;while((end=data.indexOf('\n'))!==-1){const m=JSON.parse(data.slice(0,end));data=data.slice(end+1);if(m.t==='stage'&&Number.isInteger(m.ms)&&m.ms>=0&&m.ms<=5000)mark(s,m.stage,m.ms);else if(m.t==='ready'){clearTimeout(timer);mark(s,'browser_ready');resolve();}else throw new Error('Browser protocol');}}catch{clearTimeout(timer);reject(new Error('Browser protocol'));}});
          });
        }
        if(s.stopping)throw new Error('GUI stopped');
        s.flow=setInterval(()=>{if(Date.now()-s.epoch>=1000){s.epoch=Date.now();s.rate=0;}if(s.rate<4*1024*1024&&s.client?.readyState===WebSocket.OPEN&&s.client.bufferedAmount<LOW)s.ipc.resume();},50);
        s.startTimer=setTimeout(()=>stop(s),5000);return s;
      }catch(e){console.info(JSON.stringify({event:'gui_start_failure',stage:s.stage,reason:s.stopReason||'initialization_failed',elapsed_ms:Date.now()-s.started}));stop(s,'initialization_failed');throw e;}finally{s.preparedResolve();}
    },
    upgrade(req,socket,head,s){
      wss.handleUpgrade(req,socket,head,ws=>{
        mark(s,'websocket_ready');
        s.abortCleanup?.();
        clearTimeout(s.startTimer);s.client=ws;for(const bytes of s.initial)ws.send(bytes,{binary:true});s.initial=[];s.initialBytes=0;
        ws.on('pong',()=>{s.lastPong=Date.now();});
        const queue=[];let queued=0,busy=false;
        const drain=async()=>{if(busy)return;busy=true;ws.pause();try{if(!(await verify(s)))return;while(queue.length){const b=queue.shift();queued-=b.length;if(s.ipc.writableLength+b.length>65536)throw new Error('GUI backpressure');s.ipc.write(b);}}catch{console.warn('GUI input forwarding failed closed');stop(s);}finally{busy=false;if(!s.stopping&&!s.blocked)ws.resume();}};
        ws.on('message',(raw,binary)=>{
          if(s.stopping||s.blocked)return;
          try{if(!binary||raw.length>16384)throw new Error('GUI input budget');const b=packet({t:'d',d:raw.toString('base64')});if(queue.length>=64||queued+b.length>65536)throw new Error('GUI queue budget');queue.push(b);queued+=b.length;void drain();}catch(e){console.warn('GUI input rejected:',e instanceof Error?e.message:'invalid frame');stop(s);}
        });
        ws.on('close',()=>stop(s));ws.on('error',()=>stop(s));s.ipc.resume();
      });
    },
    async close(){if(current){stop(current);await current.cleanup;}for(const ws of wss.clients)ws.terminate();wss.close();},
  };
}
