import fs from 'node:fs/promises';
import os from 'node:os';
import net from 'node:net';
import { execFile } from 'node:child_process';
import { WebSocketServer, WebSocket } from 'ws';
import { checkTerminalAuth } from './pty-gateway.mjs';

const PACKAGE='/usr/local/lib/webdesktop-gui', HIGH=1024*1024, LOW=256*1024;
const packet=value=>Buffer.from(JSON.stringify(value)+'\n');
export async function guiGateway(authSocket) {
  const ready=await fs.lstat(PACKAGE+'/ready').catch(e=>{if(e.code!=='ENOENT')throw e;return null;});
  if(!ready)return null;
  const identity=os.userInfo();
  if(identity.username!=='wdsvc'||!identity.uid)throw new Error('Service identity required');
  for(const file of ['', '/worker.py','/guard.py','/openbox.xml','/dbus.conf','/ready']){
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
  let current=null;const starts=[];
  const stopUnit=()=>new Promise(resolve=>execFile('/usr/bin/systemctl',['--no-ask-password','stop','webdesktop-gui@1.service'],
    {env:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8'},timeout:5000,maxBuffer:4096},e=>resolve(!e)));
  const guardGone=async()=>{
    for(let i=0;i<60;i++){
      const gone=await new Promise(resolve=>execFile('/usr/bin/systemctl',['show','webdesktop-gui-lease@1.service','-p','MainPID','--value'],{env:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8'},timeout:1000,maxBuffer:128},(e,out)=>resolve(!e&&out.trim()==='0')));
      if(gone)return true;await new Promise(r=>setTimeout(r,100));
    }return false;
  };
  function stop(s){
    if(s.stopping)return;s.stopping=true;clearInterval(s.timer);clearInterval(s.flow);clearTimeout(s.startTimer);
    if(s.client?.readyState===WebSocket.OPEN){s.client.resume();s.client.close(1013);const t=setTimeout(()=>s.client.terminate(),1000);t.unref();}
    s.ipc?.destroy();s.guard?.end();
    s.browser?.destroy();s.abortCleanup?.();
    s.cleanup=(async()=>{await s.preparedDone;s.ipc?.destroy();s.browser?.destroy();s.guard?.end();const stopped=await stopUnit();const closed=await guardGone();if(stopped&&closed&&current===s)current=null;})();
  }
  async function verify(s){
    if(s.stopping||s.blocked)return false;
    const status=await checkTerminalAuth(authSocket,s.cookie,s.access);
    if(s.stopping||s.blocked)return false;
    if(status===200){s.guard.write('renew\n');return true;}
    s.blocked=true;
    if(status===401)stop(s);
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
    async prepare(url,cookie,{signal}={}){
      if(signal?.aborted)throw new Error('GUI request cancelled');
      if([...url.searchParams.keys()].some(k=>!['sid','access','app','w','h'].includes(k)||url.searchParams.getAll(k).length!==1))throw new Error('Fixed GUI parameters required');
      const sid=url.searchParams.get('sid'),app=url.searchParams.get('app')||'pcmanfm';
      const w=Number(url.searchParams.get('w')||1024),h=Number(url.searchParams.get('h')||640);
      if(!/^[a-f0-9]{24,64}$/.test(sid||'')||!['desktop','pcmanfm',...(browserEnabled?['chromium']:[])].includes(app)||!Number.isInteger(w)||!Number.isInteger(h)||w<640||w>1280||h<480||h>800)throw new Error('GUI budget');
      while(starts.length&&starts[0]<=Date.now()-10000)starts.shift();
      if(current||starts.length>=4)throw new Error('GUI capacity');
      const s={cookie,access:url.searchParams.get('access')||'',rate:0,epoch:Date.now(),initial:[],initialBytes:0};current=s;starts.push(Date.now());
      s.preparedDone=new Promise(resolve=>{s.preparedResolve=resolve;});
      const aborted=()=>stop(s);signal?.addEventListener('abort',aborted,{once:true});s.abortCleanup=()=>signal?.removeEventListener('abort',aborted);
      try{
        s.guard=await connect('lease');s.guardClosed=new Promise(r=>s.guard.once('close',()=>r(true)));
        if(s.stopping){s.guard.destroy();throw new Error('GUI cancelled');}
        s.guard.on('error',()=>stop(s));s.guard.on('close',()=>stop(s));
        await new Promise((resolve,reject)=>{
          let data='';const timer=setTimeout(()=>reject(new Error('GUI lease timeout')),3000);
          s.guard.once('close',()=>{clearTimeout(timer);reject(new Error('GUI guard closed'));});
          s.guard.on('data',chunk=>{data+=chunk;if(data==='READY\n'){clearTimeout(timer);resolve();}else if(data.length>128){clearTimeout(timer);reject(new Error('GUI guard protocol'));}});
        });
        s.ipc=await connect('session');s.ipc.on('error',()=>stop(s));s.ipc.on('close',()=>stop(s));
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
                if(!initialized){if(m.t!=='ready')throw new Error('GUI init');initialized=true;clearTimeout(timer);resolve();continue;}
                if(m.t!=='d'||typeof m.d!=='string'||!/^[A-Za-z0-9+/]*={0,2}$/.test(m.d))throw new Error('GUI protocol');
                const bytes=Buffer.from(m.d,'base64');if(!bytes.length||bytes.length>16384||bytes.toString('base64')!==m.d)throw new Error('GUI frame');
                s.rate+=bytes.length;
                if(s.client?.readyState===WebSocket.OPEN)s.client.send(bytes,{binary:true});
                else {s.initialBytes+=bytes.length;if(s.initialBytes>65536)throw new Error('GUI initial budget');s.initial.push(bytes);}
                if(s.rate>=4*1024*1024||s.client?.bufferedAmount>HIGH)s.ipc.pause();
              }
            }catch{clearTimeout(timer);reject(new Error('GUI protocol failed'));stop(s);}
          });
        });
        if(app==='chromium'){
          s.browser=await connect('session',true);s.browser.on('error',()=>stop(s));s.browser.on('close',()=>stop(s));
          if(s.stopping){s.browser.destroy();throw new Error('GUI cancelled');}
          await new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>reject(new Error('Browser startup timeout')),5000);let data='';
            s.browser.once('connect',()=>s.browser.write(packet({t:'open'})));
            s.browser.once('close',()=>{clearTimeout(timer);reject(new Error('Browser stopped'));});
            s.browser.on('data',chunk=>{data+=chunk;if(data==='{\"t\":\"ready\"}\n'){clearTimeout(timer);resolve();}else if(data.length>128){clearTimeout(timer);reject(new Error('Browser protocol'));}});
          });
        }
        if(s.stopping)throw new Error('GUI stopped');
        let checking=false;s.lastPong=Date.now();
        s.timer=setInterval(async()=>{if(checking)return;checking=true;try{await verify(s);}finally{checking=false;}if(s.client?.readyState===WebSocket.OPEN){if(Date.now()-s.lastPong>20000)s.client.terminate();else s.client.ping();}},5000);
        s.flow=setInterval(()=>{if(Date.now()-s.epoch>=1000){s.epoch=Date.now();s.rate=0;}if(s.rate<4*1024*1024&&s.client?.readyState===WebSocket.OPEN&&s.client.bufferedAmount<LOW)s.ipc.resume();},50);
        s.startTimer=setTimeout(()=>stop(s),5000);return s;
      }catch(e){stop(s);throw e;}finally{s.preparedResolve();}
    },
    upgrade(req,socket,head,s){
      wss.handleUpgrade(req,socket,head,ws=>{
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
