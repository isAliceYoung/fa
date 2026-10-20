import fs from 'node:fs';
import assert from 'node:assert/strict';
import net from 'node:net';
import {randomBytes} from 'node:crypto';
import {OUT,cmd,ctl,value,delay,until,login,display,stopDisplay,guiExec,snapshot,unit} from './desktop_lab.mjs';
const report={passed:0,failed:0,skipped:0,not_executed:0,checks:[],resources:{}};let active;
async function check(name,fn){try{await fn();report.passed++;report.checks.push({name,result:'passed'});console.log('PASS '+name);}catch(e){report.failed++;report.checks.push({name,result:'failed',error:String(e.stack).slice(0,3000)});console.error('FAIL '+name+' '+e.message);}finally{if(active)await stopDisplay(active).catch(()=>{});active=null;}}
report.resources.before=snapshot();
await check('visible Chromium service with actual native renderer sandbox and no public debugger',async()=>{
 const a=await login();active=await display(a,'chromium');const group=value('webdesktop-browser@1.service','ControlGroup');let processes=[];
 await until(()=>{
  processes=fs.readFileSync('/sys/fs/cgroup'+group+'/cgroup.procs','utf8').trim().split('\n').flatMap(pid=>{try{const args=fs.readFileSync('/proc/'+pid+'/cmdline','utf8').replace(/\0/g,' ');if(!args.includes('/chromium'))return[];return[{pid,args,status:fs.readFileSync('/proc/'+pid+'/status','utf8'),user:fs.readlinkSync('/proc/'+pid+'/ns/user'),uid_map:fs.readFileSync('/proc/'+pid+'/uid_map','utf8'),pidns:fs.readlinkSync('/proc/'+pid+'/ns/pid')}];}catch{return[];}});
  return processes.some(p=>p.args.includes('--type=renderer'));
 },15000);
 const parent=processes.find(p=>!p.args.includes('--type=')),renderers=processes.filter(p=>p.args.includes('--type=renderer'));assert(parent);assert(renderers.length);
 for(const p of processes){
  assert(!p.args.includes('--no-sandbox'));assert(!/remote-debugging|--headless|disable-seccomp/.test(p.args));assert.match(p.status,/NoNewPrivs:\s+1/);const uid=Number(cmd('id',['-u','wdgui']));assert.equal(Number(/Uid:\s+(\d+)/.exec(p.status)[1]),uid);
  // Chromium's short-lived sandbox setup may hold SYS_ADMIN only inside its
  // single-UID user namespace. Never accept that capability in the host user
  // namespace, main browser or renderer. Renderers must have dropped it.
  if(!/CapEff:\s+0000000000000000/.test(p.status)){
   assert(p.args.includes('--type=zygote'));assert.notEqual(p.user,parent.user);assert.notEqual(p.user,fs.readlinkSync('/proc/1/ns/user'));assert.match(p.status,/CapEff:\s+0000000000200000/);
   assert.deepEqual(p.uid_map.trim().split(/\s+/).map(Number),[0,uid,1]);
  }
 }
 assert.match(parent.status,/CapEff:\s+0000000000000000/);
 for(const p of renderers){assert.match(p.status,/CapEff:\s+0000000000000000/);assert.notEqual(p.user,parent.user);assert.notEqual(p.pidns,parent.pidns);assert.match(p.status,/Seccomp:\s+2/);assert(Number(/Seccomp_filters:\s+(\d+)/.exec(p.status)[1])>Number(/Seccomp_filters:\s+(\d+)/.exec(parent.status)[1]));}
 report.native_processes=processes;report.resources.visible_chromium=snapshot();
 const environment=fs.readFileSync('/proc/'+parent.pid+'/environ','utf8');assert(!/AUTH_KEY=|DATABASE_URL=|DESKTOP_PASSWORD=|COOKIE=/.test(environment));
 const top=guiExec(['xdotool','search','--onlyvisible','--class','chromium']);assert(top);assert(!cmd('ss',['-ltnp']).includes('9222'));
});
await check('actual browser egress allows HTTPS and rejects all host management and private sockets',async()=>{
 const a=await login();active=await display(a,'chromium');
 const code="import socket,urllib.request,json\nr={}\nfor host in ['127.0.0.1','10.232.0.1','10.0.2.15','10.0.2.2','169.254.169.254']:\n for port in [22,3000,5432,5900,9222]:\n  try:s=socket.create_connection((host,port),timeout=.3);s.close();r[host+':'+str(port)]='CONNECTED'\n  except OSError:r[host+':'+str(port)]='denied'\nr['https']=urllib.request.urlopen('https://example.com',timeout=15).status\nprint(json.dumps(r))";
 const r=JSON.parse(cmd('ip',['netns','exec','wd-browser','setpriv','--reuid',cmd('id',['-u','wdgui']),'--regid',cmd('id',['-g','wdguest']),'--clear-groups','--no-new-privs','--bounding-set=-all','python3','-I','-c',code]));for(const [k,v]of Object.entries(r))assert.equal(v,k==='https'?200:'denied');report.network=r;
 const direct=fs.statSync('/run/webdesktop-browser/session-1.sock');assert.equal(direct.uid,0);assert.equal(direct.mode&511,0o660);
 for(const account of ['wdgui','wdterm','wdguest','nobody'])assert.throws(()=>cmd('setpriv',['--reuid',cmd('id',['-u',account]),'--regid',cmd('id',['-g',account]),'--clear-groups','python3','-I','-c',"import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/webdesktop-browser/session-1.sock')"]));
});
await check('aborted Upgrade startup releases protected slot and permits safe restart',async()=>{
 const a=await login();for(let i=0;i<2;i++){
  const s=net.connect(3000,'127.0.0.1');await new Promise(r=>s.once('connect',r));s.write('GET /api/gui/ws?app=chromium&sid='+randomBytes(12).toString('hex')+'&access='+a.access+' HTTP/1.1\r\nHost: 127.0.0.1:3000\r\nOrigin: http://127.0.0.1:3000\r\nCookie: '+a.cookie+'\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+randomBytes(16).toString('base64')+'\r\n\r\n');await delay(i?250:5);s.destroy();await delay(300);await until(()=>value(unit,'MainPID')==='0',12000);
 }
 active=await display(a,'chromium');assert(Number(value('webdesktop-browser@1.service','MainPID'))>0);
});
await check('paused display cannot defer authentication revocation or independent cleanup',async()=>{
 const a=await login();active=await display(a,'chromium');active.ws.pause();await login();await until(()=>value(unit,'MainPID')==='0',14000);assert.equal(value('webdesktop-browser@1.service','MainPID'),'0');active.ws.resume();
});
await check('browser worker crash closes display and reaps descendants before fixed-slot restart',async()=>{
 const a=await login();active=await display(a,'chromium');const browser='webdesktop-browser@1.service',group=value(browser,'ControlGroup');
 cmd('kill',['-KILL',value(browser,'MainPID')]);await until(()=>value(unit,'MainPID')==='0',14000);
 assert(!fs.existsSync('/sys/fs/cgroup'+group+'/cgroup.procs')||!fs.readFileSync('/sys/fs/cgroup'+group+'/cgroup.procs','utf8').trim());
 await stopDisplay(active);active=null;active=await display(a,'chromium');assert(Number(value(browser,'MainPID'))>0);
});
await check('PCManFM directory and thumbnail pressure remains bounded with renewable all-day lease',async()=>{
 const a=await login();active=await display(a,'pcmanfm');assert.equal(value(unit,'RuntimeMaxUSec'),'infinity');assert.equal(value('webdesktop-gui-lease@1.service','RuntimeMaxUSec'),'infinity');
 guiExec(['python3','-I','-c',"import pathlib,zlib,struct;p=pathlib.Path('/home/wdguest/Documents/thumbnail-lab');p.mkdir(exist_ok=True);chunk=lambda t,d:struct.pack('!I',len(d))+t+d+struct.pack('!I',zlib.crc32(t+d));png=b'\\x89PNG\\r\\n\\x1a\\n'+chunk(b'IHDR',struct.pack('!2I5B',128,128,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\\0'+b'\\x80\\x20\\xc0'*128)*128))+chunk(b'IEND',b'');[(p/('%03d.png'%i)).write_bytes(png) for i in range(160)]"]);
 await active.press([0xffe3,0x6c]);await active.type('/home/wdguest/Documents/thumbnail-lab');await active.press([0xff0d]);
 const lat=[];for(let i=0;i<60;i++){if(i%6===0)await active.press([0xffc2]);const start=performance.now();assert.equal((await fetch('http://127.0.0.1:3000/api/health',{signal:AbortSignal.timeout(4000)})).status,200);lat.push(performance.now()-start);assert(Number(value(unit,'MainPID'))>0);await delay(5000);}
 report.resources.pcmanfm_300s=snapshot();report.directory_health_ms=lat;
});
for(const kind of ['gtk','qt','xim'])await check('real '+kind+' Chinese pinyin candidates numeric selection and private IME restart',async()=>{
 const a=await login();active=await display(a);const fixture='wdlab-ime-'+kind;
 const env=['HOME=/home/wdguest','USER=wdgui','PATH=/usr/bin:/bin','LANG=C.UTF-8','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/wd-gui/bus','XDG_RUNTIME_DIR=/tmp/wd-gui','GTK_IM_MODULE=fcitx','QT_IM_MODULE=fcitx','XMODIFIERS=@im=fcitx','NO_AT_BRIDGE=1'];
 const props=['User=wdgui','Group=wdguest','SupplementaryGroups=wdgui','Slice=webdesktop-gui.slice','NoNewPrivileges=yes','CapabilityBoundingSet=','ProtectSystem=strict','ProtectHome=tmpfs','BindPaths=/home/wdguest /run/webdesktop-display/1/ipc:/tmp/wd-gui /run/webdesktop-display/1/x11:/tmp/.X11-unix','TemporaryFileSystem=/tmp:rw,nosuid,nodev,noexec,size=16M /run:ro,size=1M','PrivateNetwork=yes','RestrictAddressFamilies=AF_UNIX','PrivateDevices=yes','ProtectControlGroups=yes','InaccessiblePaths=/root /opt/webdesktop /var/lib/webdesktop /home/wdguest/.wd-state','MemoryMax=96M','MemorySwapMax=32M','TasksMax=32','KillMode=control-group','RuntimeMaxSec=60','TimeoutStopSec=1s','LimitCORE=0','BindsTo='+unit];
 const application=kind==='xim'?['/usr/bin/xterm','-title','WD IME XIM','-u8','-xrm','XTerm*openIm:true','-xrm','XTerm*inputMethod:fcitx','-e','/bin/bash','--noprofile','--norc']:['/usr/bin/python3','/usr/local/lib/webdesktop-test-ui/ime.py',kind];
 cmd('systemd-run',['--quiet','--unit='+fixture,...props.map(p=>'--property='+p),'/usr/bin/env','-i',...env,...application]);
 try{
  let wid;await until(()=>{try{wid=guiExec(['xdotool','search','--onlyvisible','--name','WD IME '+(kind==='qt'?'Qt':kind.toUpperCase())]).split('\n')[0];return !!wid;}catch{return false;}},10000);guiExec(['xdotool','windowactivate','--sync',wid]);await delay(600);guiExec(['fcitx5-remote','-c']);
  if(kind==='xim')await active.type("printf '%s' '");
  await active.press([0xffe3,0x20]);await active.type('nihao');await delay(600);guiExec(['scrot','/home/wdguest/.gui/ime-'+kind+'-candidates.png']);fs.copyFileSync('/home/wdguest/.gui/ime-'+kind+'-candidates.png',OUT+'/ime-'+kind+'-candidates.png');
  await active.type('1');await active.press([0xffe3,0x20]);
  if(kind==='xim'){await active.type("' > /home/wdguest/Documents/ime-xim.txt");await active.press([0xff0d]);await until(()=>fs.existsSync('/home/wdguest/Documents/ime-xim.txt'));assert.equal(fs.readFileSync('/home/wdguest/Documents/ime-xim.txt','utf8'),'你好');}
  else{
   await active.type('hello');await until(()=>fs.existsSync('/home/wdguest/.gui/ime-'+kind+'.txt')&&fs.readFileSync('/home/wdguest/.gui/ime-'+kind+'.txt','utf8')==='你好hello');
   if(kind==='gtk'){
    cmd('systemd-run',['--quiet','--unit=wdlab-ime-qt-focus',...props.map(p=>'--property='+p),'/usr/bin/env','-i',...env,'/usr/bin/python3','/usr/local/lib/webdesktop-test-ui/ime.py','qt']);
    try{let qt;await until(()=>{try{qt=guiExec(['xdotool','search','--onlyvisible','--name','WD IME Qt']).split('\n')[0];return !!qt;}catch{return false;}});guiExec(['xdotool','windowactivate','--sync',qt]);await delay(500);guiExec(['fcitx5-remote','-c']);await active.press([0xffe3,0x20]);await active.type('nihao');await active.type('1');await until(()=>fs.readFileSync('/home/wdguest/.gui/ime-qt.txt','utf8')==='你好');guiExec(['xdotool','windowactivate','--sync',wid]);await delay(300);guiExec(['fcitx5-remote','-c']);await active.type('world');await until(()=>fs.readFileSync('/home/wdguest/.gui/ime-gtk.txt','utf8')==='你好helloworld');}
    finally{ctl('stop','wdlab-ime-qt-focus');}
   }
   if(kind==='qt'){
    const before=cmd('pgrep',['-u','wdgui','-x','fcitx5']);cmd('kill',['-TERM',before]);await until(()=>{try{return cmd('pgrep',['-u','wdgui','-x','fcitx5'])!==before;}catch{return false;}});await delay(1000);
    await active.press([0xffe3,0x61]);await active.press([0xff08]);await until(()=>fs.readFileSync('/home/wdguest/.gui/ime-qt.txt','utf8')==='');guiExec(['fcitx5-remote','-c']);await active.press([0xffe3,0x20]);await active.type('nihao');await active.type('1');await until(()=>fs.readFileSync('/home/wdguest/.gui/ime-qt.txt','utf8')==='你好');
   }
  }
  report.resources['ime_'+kind]=snapshot();
 }finally{ctl('stop',fixture);}
});
report.resources.after=snapshot();report.total=report.passed+report.failed;fs.writeFileSync(OUT+'/desktop-runtime.json',JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,failed:report.failed,skipped:0,not_executed:0}));process.exit(report.failed?1:0);
