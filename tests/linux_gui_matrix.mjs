// Root-only resource experiments. Resource failures remain failures in JSON;
// a completed experiment is not an application availability acceptance.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import WebSocket from '/opt/webdesktop/node_modules/ws/wrapper.mjs';
import {OUT,cmd,ctl,value,delay,login,snapshot} from './desktop_lab.mjs';
const report={experiments:[],resource_passed:0,resource_failed:0,security_failed:0,skipped:0,not_executed:0,host_before:snapshot()};
ctl('stop','webdesktop-gui@1.service','webdesktop-pty@1.service','webdesktop-pty@2.service');
const auth=await login(),terminal=new WebSocket('ws://127.0.0.1:3000/api/term/ws?sid='+randomBytes(12).toString('hex')+'&access='+auth.access,{headers:{Cookie:auth.cookie,Origin:'http://127.0.0.1:3000'}});
terminal.on('error',()=>{});terminal.on('message',()=>{});await new Promise((r,j)=>{terminal.once('open',r);terminal.once('error',j);});terminal.send(JSON.stringify({t:'i',d:"python3 -c \"import time; x=bytearray(24*1024*1024); print('CONCURRENT_PTY'); time.sleep(900)\"\r"}));
const headers={Cookie:auth.cookie,'X-Desktop-Session':auth.access,'Content-Type':'application/json'};
const fileName='resource-concurrent-'+randomBytes(8).toString('hex')+'.txt';
assert.equal((await fetch('http://127.0.0.1:3000/api/sys/fs',{method:'POST',headers,body:JSON.stringify({dir:'~/Documents',name:fileName,kind:'file',content:'x'.repeat(32768)})})).status,200);
const p95=a=>a.length?[...a].sort((a,b)=>a-b)[Math.min(a.length-1,Math.floor(a.length*.95))]:null;
for(const [app,budget] of [['chromium',320],['chromium',384],['claude',320],['claude',384],['claude',448]]){
 const name='wdlab-matrix-'+app+'-'+budget+'.service',log=OUT+'/matrix-'+app+'-'+budget+'.log',taskLimit=app==='claude'?160:128;const result={app,budget_mib:budget,memory_swap_max_mib:192,tasks_max:taskLimit,host_before:snapshot(),stages:{},samples:[],processes:[],health_ms:[],x11_ms:[],max_swap_bytes:0,max_tasks:0};report.experiments.push(result);
 const props=['User=wdgui','Group=wdguest','Slice=webdesktop.slice','NoNewPrivileges=yes','CapabilityBoundingSet=','ProtectSystem=strict','ProtectHome=tmpfs','BindPaths=/home/wdguest',
  'InaccessiblePaths=/root /opt/webdesktop /var/lib/webdesktop /etc/webdesktop /home/wdguest/.wd-state','TemporaryFileSystem=/tmp:rw,nosuid,nodev,noexec,size=32M,mode=1777 /var/tmp:rw,nosuid,nodev,noexec,size=4M,mode=1777 /run:ro,nosuid,nodev,noexec,size=1M,mode=0755',
  'NetworkNamespacePath=/run/netns/wdgui-probe','BindReadOnlyPaths=/usr/local/lib/webdesktop-gui-probe/resolv.conf:/etc/resolv.conf','RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK','RestrictNamespaces=user pid net mnt',
  'PrivateIPC=yes','PrivateDevices=yes','ProtectControlGroups=yes','ProtectKernelTunables=yes','ProtectKernelModules=yes','ProtectProc=invisible','RestrictRealtime=yes','LockPersonality=yes','SystemCallArchitectures=native',
  'MemoryMax='+budget+'M','MemorySwapMax=192M','TasksMax='+taskLimit,'CPUQuota=70%','LimitNOFILE=512','LimitCORE=0','RuntimeMaxSec=125','TimeoutStopSec=1500ms','KillMode=control-group','OOMPolicy=kill','RemainAfterExit=yes','StandardOutput=append:'+log,'StandardError=append:'+log];
 cmd('systemd-run',['--quiet','--unit='+name,...props.map(p=>'--property='+p),'/usr/bin/python3','-I','/usr/local/lib/webdesktop-gui-probe/matrix.py',app,String(budget),'90']);
 const start=performance.now(),seen=new Map();let cdp,last=0,lastShot=0,loginAttempted=false;const group=value(name,'ControlGroup'),base='/sys/fs/cgroup'+group;
 try{
  while(performance.now()-start<100000){
   const elapsed=performance.now()-start,stage=elapsed<15000?'startup':elapsed<35000?'idle':elapsed<55000?'visible_window':'sustained';
   const sample={elapsed_ms:elapsed,stage,meminfo:fs.readFileSync('/proc/meminfo','utf8'),swap:fs.readFileSync('/proc/swaps','utf8'),cgroup:{}};
   for(const f of ['memory.current','memory.peak','memory.swap.current','memory.events','memory.pressure','cpu.stat','cpu.pressure','pids.current','pids.peak','pids.events'])if(fs.existsSync(base+'/'+f))sample.cgroup[f]=fs.readFileSync(base+'/'+f,'utf8').trim();
   result.last_cgroup=sample.cgroup;result.max_swap_bytes=Math.max(result.max_swap_bytes,Number(sample.cgroup['memory.swap.current']||0));result.max_tasks=Math.max(result.max_tasks,Number(sample.cgroup['pids.current']||0));
   if(elapsed-last>=1000){result.samples.push(sample);last=elapsed;}
   result.stages[stage]=sample;
   if(fs.existsSync(base+'/cgroup.procs'))for(const pid of fs.readFileSync(base+'/cgroup.procs','utf8').trim().split('\n')){if(!pid)continue;try{
    const args=fs.readFileSync('/proc/'+pid+'/cmdline','utf8').replace(/\0/g,' ');if(!args.includes('/chromium')&&!args.includes('/claude-desktop'))continue;
    const proc={pid,args,status:fs.readFileSync('/proc/'+pid+'/status','utf8'),user_ns:fs.readlinkSync('/proc/'+pid+'/ns/user'),uid_map:fs.readFileSync('/proc/'+pid+'/uid_map','utf8'),pid_ns:fs.readlinkSync('/proc/'+pid+'/ns/pid')};
    try{proc.memory=fs.readFileSync('/proc/'+pid+'/smaps_rollup','utf8').split('\n').filter(x=>/^(Rss|Pss|Swap|Private|Shared)/.test(x)).join('\n');}catch{}
    proc.role=args.includes('--type=renderer')?'renderer':args.includes('--type=')||args.includes('crashpad')?'helper':'main';
    seen.set(pid,proc);
   }catch{}}
   if(elapsed-lastShot>20000&&Number(value(name,'MainPID'))>0){
    lastShot=elapsed;const pid=value(name,'MainPID'),env=['PATH=/usr/bin:/bin','HOME=/home/wdguest','DISPLAY=:101','XAUTHORITY=/tmp/wdgui-probe/Xauthority'];
    const run=args=>cmd('nsenter',['--target',pid,'--mount','--net','--','setpriv','--reuid',cmd('id',['-u','wdgui']),'--regid',cmd('id',['-g','wdguest']),'--clear-groups','env','-i',...env,...args],5000);
    try{const x=performance.now();const window=run(['xdotool','getactivewindow']);result.x11_ms.push(performance.now()-x);result.window_title=run(['xdotool','getwindowname',window]);
     // Stock pinned Claude welcome window, 800x600 Xvnc. Normal user action,
     // no vendor patches, debugger, credentials or automated account login.
     if(app==='claude'&&elapsed>35000&&!loginAttempted){run(['xdotool','mousemove','400','532','click','1']);loginAttempted=true;result.login_action='clicked-stock-get-started';}
     run(['scrot','/home/wdguest/.gui/matrix-'+app+'-'+budget+'-'+stage+'.png']);fs.copyFileSync('/home/wdguest/.gui/matrix-'+app+'-'+budget+'-'+stage+'.png',OUT+'/matrix-'+app+'-'+budget+'-'+stage+'.png');}catch(e){result.display_error=String(e.message).slice(0,200);}
   }
   if(elapsed%2000<250){const t=performance.now();try{const r=await fetch('http://127.0.0.1:3000/api/auth/session',{headers,signal:AbortSignal.timeout(3000)});if(r.status!==200)result.backend_status=r.status;result.health_ms.push(performance.now()-t);const file=await fetch('http://127.0.0.1:3000/api/sys/fs?read=1&path=~/Documents/'+fileName,{headers,signal:AbortSignal.timeout(3000)});if(file.status!==200)result.file_status=file.status;await file.arrayBuffer();}catch{result.health_ms.push(3000);}}
   if(app==='chromium'&&!cdp&&fs.existsSync(log)&&fs.readFileSync(log,'utf8').includes('BROWSER_PROBE_STARTED'))cdp=spawn('ip',['netns','exec','wdgui-probe','/usr/local/bin/node','/root/wd-lab/tests/cdp_gui_probe.mjs','chromium'],{stdio:'inherit'});
   if(value(name,'MainPID')==='0'||['inactive','failed'].includes(value(name,'ActiveState')))break;
   await delay(200);
  }
 }finally{
  result.unit=ctl('show',name,'-p','Result','-p','ExecMainStatus','-p','MemoryMax','-p','MemorySwapMax');ctl('stop',name);if(cdp?.exitCode===null)cdp.kill();
  result.after_close=snapshot();result.remaining_cgroup=fs.existsSync(base+'/cgroup.procs')?fs.readFileSync(base+'/cgroup.procs','utf8').trim():'';assert.equal(result.remaining_cgroup,'');await delay(2000);result.after_close_settled=snapshot();
 }
 result.processes=[...seen.values()];const main=result.processes.find(p=>p.role==='main'&&p.args.includes('--user-data-dir=')),renderers=result.processes.filter(p=>p.args.includes('--type=renderer'));
 const safe=p=>Number(/Uid:\s+(\d+)/.exec(p.status)?.[1])===Number(cmd('id',['-u','wdgui']))&&/NoNewPrivs:\s+1/.test(p.status)&&/CapEff:\s+0000000000000000/.test(p.status)&&!p.args.includes('--no-sandbox')&&!p.args.includes('--disable-seccomp-filter-sandbox');
 result.native_verified=!!main&&renderers.length>0&&renderers.every(p=>safe(p)&&p.user_ns!==main.user_ns&&p.pid_ns!==main.pid_ns&&Number(/Seccomp_filters:\s+(\d+)/.exec(p.status)?.[1])>Number(/Seccomp_filters:\s+(\d+)/.exec(main.status)?.[1]));
 const namespaceSetup=p=>{const uid=Number(cmd('id',['-u','wdgui'])),map=p.uid_map.trim().split(/\s+/).map(Number);return p.args.includes('--type=zygote')&&p.user_ns!==main?.user_ns&&p.user_ns!==fs.readlinkSync('/proc/1/ns/user')&&/CapEff:\s+(?:0000000000200000|000001ffffffffff)\s/.test(p.status)&&map.length===3&&[0,uid].includes(map[0])&&map[1]===uid&&map[2]===1&&/NoNewPrivs:\s+1/.test(p.status);};
 if(!result.processes.every(p=>safe(p)||namespaceSetup(p))){report.security_failed++;result.security='failed';}else result.security=renderers.length?'observed-safe':'unverified-no-renderer';
 result.process_memory_by_role={};for(const p of result.processes){const role=result.process_memory_by_role[p.role]??={observed_processes:0,pss_kib:0,swap_kib:0};role.observed_processes++;role.pss_kib+=Number(/^Pss:\s+(\d+)/m.exec(p.memory||'')?.[1]||0);role.swap_kib+=Number(/^Swap:\s+(\d+)/m.exec(p.memory||'')?.[1]||0);}
 // Different process lifetimes are sampled at different instants. These role
 // totals describe the last observation of each PID, not simultaneous RAM.
 if(!renderers.length)report.not_executed++;
 const text=fs.existsSync(log)?fs.readFileSync(log,'utf8'):'';result.survived=text.includes('BROWSER_PROBE_RESULT '+app+' None');result.health_p95_ms=p95(result.health_ms);result.x11_p95_ms=p95(result.x11_ms);
 result.thread_exhaustion=/pthread_create.*Resource temporarily unavailable|fork.*Resource temporarily unavailable/.test(text)||/max [1-9]/.test(result.last_cgroup?.['pids.events']||'');
 result.resource_stable_smoke=result.survived&&result.native_verified&&!result.thread_exhaustion&&!/oom_kill [1-9]/.test(result.last_cgroup?.['memory.events']||'')&&result.health_p95_ms!==null&&result.health_p95_ms<750&&result.x11_p95_ms!==null&&result.x11_p95_ms<500;
 result.status=result.resource_stable_smoke?'passed-smoke-only':'failed-resource-or-interaction';result.production_usable=false;
 if(result.resource_stable_smoke)report.resource_passed++;else report.resource_failed++;
 result.profile_bytes=Number(cmd('du',['-sb','/home/wdguest/.gui/matrix-'+app+'-'+budget]).split(/\s/)[0]);
 console.log(JSON.stringify({app,budget,swap_max_mib:192,status:result.status,native:result.native_verified,unit:result.unit,swap_peak:result.max_swap_bytes}));
 fs.writeFileSync(OUT+'/gui-resource-matrix.json',JSON.stringify(report,null,2));await delay(1500);
}
terminal.send(JSON.stringify({t:'kill'}));terminal.close();report.host_after=snapshot();report.experiment_count=report.experiments.length;
report.kernel_oom=cmd('dmesg').split('\n').filter(line=>/oom-kill:|out of memory/i.test(line));
report.global_oom=report.kernel_oom.some(line=>line.includes('CONSTRAINT_NONE'));if(report.global_oom)report.security_failed++;
fs.writeFileSync(OUT+'/gui-resource-matrix.json',JSON.stringify(report,null,2));console.log(JSON.stringify({experiments:report.experiment_count,resource_passed:report.resource_passed,resource_failed:report.resource_failed,security_failed:report.security_failed,skipped:0,not_executed:report.not_executed}));
// Only unsafe execution or incomplete collection fails this controller. The
// resource experiment failures above remain visible and keep Claude blocked.
process.exit(report.security_failed?1:0);
