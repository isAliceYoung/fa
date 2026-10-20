// Disposable root-controller helpers, never distributed to visitor runtime.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import WebSocket from '/opt/webdesktop/node_modules/ws/wrapper.mjs';
export const OUT='/root/wd-systemd-evidence',unit='webdesktop-gui@1.service';
export const cmd=(bin,args=[],timeout=20000)=>execFileSync(bin,args,{encoding:'utf8',timeout,stdio:['ignore','pipe','pipe']}).trim();
export const ctl=(...args)=>cmd('systemctl',args),value=(u,p)=>ctl('show',u,'-p',p,'--value'),delay=ms=>new Promise(r=>setTimeout(r,ms));
export async function until(fn,ms=10000){const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await delay(100);}throw new Error('Desktop bounded assertion timeout');}
const config=Object.fromEntries(fs.readFileSync('/opt/webdesktop/.env','utf8').split('\n').filter(x=>x.includes('=')).map(x=>{const i=x.indexOf('=');return[x.slice(0,i),x.slice(i+1)];}));
export async function login(){const start=performance.now();const r=await fetch('http://127.0.0.1:3000/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:config.DESKTOP_PASSWORD}),signal:AbortSignal.timeout(10000)});assert.equal(r.status,200);return{cookie:r.headers.getSetCookie()[0].split(';')[0],access:(await r.json()).sessionId,login_ms:performance.now()-start};}
async function openDisplay(a,app='desktop'){
 const ws=new WebSocket('ws://127.0.0.1:3000/api/gui/ws?sid='+randomBytes(12).toString('hex')+'&app='+app+'&access='+a.access+'&w=1024&h=640',{headers:{Cookie:a.cookie,Origin:'http://127.0.0.1:3000'},handshakeTimeout:20000});
 let pending=Buffer.alloc(0);const c={ws,closed:false};ws.on('error',()=>{});ws.on('message',b=>{pending=Buffer.concat([pending,b]);if(pending.length>8*1024*1024)ws.terminate();});ws.on('close',()=>c.closed=true);
 await new Promise((r,j)=>{ws.once('open',r);ws.once('error',j);});c.send=b=>ws.send(b,{binary:true});
 c.read=async n=>{await until(()=>pending.length>=n||c.closed);assert(pending.length>=n);const b=pending.subarray(0,n);pending=pending.subarray(n);return b;};
 await c.read(12);c.send(Buffer.from('RFB 003.008\n'));await c.read((await c.read(1))[0]);c.send(Buffer.from([1]));assert.equal((await c.read(4)).readUInt32BE(),0);c.send(Buffer.from([1]));const init=await c.read(24);await c.read(init.readUInt32BE(20));
 c.key=(key,down)=>{const b=Buffer.alloc(8);b[0]=4;b[1]=down?1:0;b.writeUInt32BE(key,4);c.send(b);};
 c.press=async keys=>{for(const k of keys)c.key(k,true);for(const k of [...keys].reverse())c.key(k,false);await delay(150);};
 c.type=async text=>{for(const ch of text){const k=ch.codePointAt(0);c.key(k,true);c.key(k,false);}await delay(250);};return c;
}
export async function display(a,app='desktop'){const end=Date.now()+14000;while(true){try{return await openDisplay(a,app);}catch(e){if(!String(e.message).includes('503')||Date.now()>end)throw e;await delay(350);}}}
export async function stopDisplay(c){c?.ws.resume();c?.ws.close();await until(()=>value(unit,'MainPID')==='0',12000);await until(()=>value('webdesktop-gui-lease@1.service','MainPID')==='0',8000);await delay(250);}
export function guiExec(args){return cmd('nsenter',['--target',value(unit,'MainPID'),'--mount','--net','--','setpriv','--reuid',cmd('id',['-u','wdgui']),'--regid',cmd('id',['-g','wdguest']),'--groups',cmd('id',['-g','wdgui']),'--no-new-privs','env','-i','PATH=/usr/bin:/bin','HOME=/home/wdguest','LANG=C.UTF-8','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/wd-gui/bus',...args]);}
export function snapshot(){const result={at:new Date().toISOString(),meminfo:fs.readFileSync('/proc/meminfo','utf8'),swap:fs.readFileSync('/proc/swaps','utf8'),disk:cmd('df',['-PB1','/','/home/wdguest']),cgroups:{}};
 for(const u of ['wdlab-next.service','postgresql@15-main.service','webdesktop-guest.service','webdesktop-pty.slice','webdesktop-gui.slice','webdesktop-gui@1.service','webdesktop-browser@1.service','webdesktop-gui-lease@1.service']){
  const group=value(u,'ControlGroup');if(!group)continue;const fields={limits:ctl('show',u,'-p','MemoryMax','-p','MemoryHigh','-p','MemorySwapMax','-p','TasksMax','-p','CPUQuotaPerSecUSec','-p','LimitNOFILE')};for(const f of ['memory.current','memory.peak','memory.swap.current','memory.events','memory.pressure','pids.current','pids.peak','cpu.stat','cgroup.procs']){const p='/sys/fs/cgroup'+group+'/'+f;if(fs.existsSync(p))fields[f]=fs.readFileSync(p,'utf8').trim();}result.cgroups[u]=fields;
 }return result;
}
