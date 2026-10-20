// Root feasibility controller. No browser/Claude route is enabled by this test.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {execFileSync,spawn} from 'node:child_process';
const OUT='/root/wd-systemd-evidence';
const cmd=(bin,args=[])=>execFileSync(bin,args,{encoding:'utf8',timeout:20000,stdio:['ignore','pipe','pipe']}).trim();
const ctl=(...args)=>cmd('systemctl',args),delay=ms=>new Promise(r=>setTimeout(r,ms));
const report={passed:0,failed:0,skipped:0,not_executed:0,checks:[],applications:{}};
const check=(name,fn)=>{try{fn();report.passed++;report.checks.push({name,result:'passed'});}catch(e){report.failed++;report.checks.push({name,result:'failed',error:String(e.message)});}};
ctl('stop','webdesktop-gui@1.service','webdesktop-pty@1.service','webdesktop-pty@2.service');
const guiUID=Number(cmd('id',['-u','wdgui'])),dataGID=cmd('id',['-g','wdguest']);
const netcode="import socket,urllib.request,json\nr={}\nfor host in ['127.0.0.1','10.231.0.1','10.0.2.15','10.0.2.2','169.254.169.254']:\n for port in [22,3000,5432]:\n  try:s=socket.create_connection((host,port),timeout=.4);s.close();r[host+':'+str(port)]='CONNECTED'\n  except OSError:r[host+':'+str(port)]='denied'\ntry:r['https']=urllib.request.urlopen('https://example.com',timeout=15).status\nexcept Exception as e:r['https']=str(e)\nprint(json.dumps(r))";
const networking=JSON.parse(cmd('ip',['netns','exec','wdgui-probe','setpriv','--reuid',String(guiUID),'--regid',dataGID,'--clear-groups','--no-new-privs','--inh-caps=-all','--ambient-caps=-all','--bounding-set=-all','python3','-I','-c',netcode]));
report.network=networking;
check('actual guest network namespace denies host PostgreSQL Next SSH and link-local',()=>{for(const [key,v] of Object.entries(networking))if(key!=='https')assert.equal(v,'denied',key);});
check('same constrained guest namespace can reach external HTTPS',()=>assert.equal(networking.https,200));
function size(){return Number(cmd('du',['-sb','/home/wdguest/.gui']).split(/\s/)[0]);}
for(const app of ['chromium','claude']){
 const unit='wdlab-'+app+'-probe.service',log=OUT+'/probe-'+app+'.log';const result={profile_before_bytes:size(),processes:[],minimum_available_kib:Infinity};report.applications[app]=result;
 const properties=['User=wdgui','Group=wdguest','WorkingDirectory=/home/wdguest','NoNewPrivileges=yes','CapabilityBoundingSet=',
 'ProtectHome=tmpfs','BindPaths=/home/wdguest','ProtectSystem=strict','InaccessiblePaths=/opt/webdesktop /etc/webdesktop /var/lib/webdesktop /root /home/wdguest/.wd-state',
 'TemporaryFileSystem=/tmp:rw,nosuid,nodev,noexec,size=32M,mode=1777 /var/tmp:rw,nosuid,nodev,noexec,size=4M,mode=1777 /run:ro,nosuid,nodev,noexec,size=1M,mode=0755',
 'BindReadOnlyPaths=/usr/local/lib/webdesktop-gui-probe/resolv.conf:/etc/resolv.conf',
 'NetworkNamespacePath=/run/netns/wdgui-probe','RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK','RestrictNamespaces=user pid net mnt',
 'PrivateDevices=yes','PrivateIPC=yes','ProtectControlGroups=yes','ProtectKernelTunables=yes','ProtectKernelModules=yes','RestrictRealtime=yes','LockPersonality=yes','SystemCallArchitectures=native','ProtectProc=invisible','MemoryMax=320M','MemorySwapMax=64M','TasksMax=128','CPUQuota=70%',
 'LimitNOFILE=512','LimitCORE=0','RuntimeMaxSec=45','KillMode=control-group','TimeoutStopSec=1500ms','OOMPolicy=kill','StandardOutput=append:'+log,'StandardError=append:'+log];
 cmd('systemd-run',['--unit='+unit,'--quiet',...properties.flatMap(p=>['--property='+p]),'/usr/bin/python3','-I','/usr/local/lib/webdesktop-gui-probe/probe.py',app]);
 const seen=new Map();let cdp=null;const deadline=Date.now()+32000;
 while(Date.now()<deadline){
  result.minimum_available_kib=Math.min(result.minimum_available_kib,Number(/^MemAvailable:\s+(\d+)/m.exec(fs.readFileSync('/proc/meminfo','utf8'))[1]));
  const group=ctl('show',unit,'-p','ControlGroup','--value');
  if(group&&fs.existsSync('/sys/fs/cgroup'+group+'/cgroup.procs')){
   for(const pid of fs.readFileSync('/sys/fs/cgroup'+group+'/cgroup.procs','utf8').trim().split('\n')){
    if(!pid)continue;
    try{
     const args=fs.readFileSync('/proc/'+pid+'/cmdline','utf8').replace(/\0/g,' '),status=fs.readFileSync('/proc/'+pid+'/status','utf8');
     if(!args.includes('--type=renderer')&&!args.includes('--user-data-dir='))continue;
     seen.set(pid,{pid,args,status:status.split('\n').filter(x=>/^(Uid|Gid|NSpid|NoNewPrivs|Seccomp|Seccomp_filters|CapEff|CapPrm|CapAmb|CapBnd|VmRSS):/.test(x)).join('\n'),user_ns:fs.readlinkSync('/proc/'+pid+'/ns/user'),pid_ns:fs.readlinkSync('/proc/'+pid+'/ns/pid'),net_ns:fs.readlinkSync('/proc/'+pid+'/ns/net')});
    }catch{}
   }
   result.cgroup={};for(const f of ['memory.peak','memory.events','pids.current','pids.peak','cpu.stat']){const p='/sys/fs/cgroup'+group+'/'+f;if(fs.existsSync(p))result.cgroup[f]=fs.readFileSync(p,'utf8').trim();}
  }
  if(!cdp&&fs.existsSync(log)&&fs.readFileSync(log,'utf8').includes('BROWSER_PROBE_STARTED')){
   cdp=spawn('ip',['netns','exec','wdgui-probe','/usr/local/bin/node','/root/wd-lab/tests/cdp_gui_probe.mjs',app],{stdio:'inherit'});
  }
  if(['inactive','failed'].includes(ctl('show',unit,'-p','ActiveState','--value')))break;
  await delay(100);
 }
 if(cdp){await Promise.race([new Promise(r=>{if(cdp.exitCode!==null)r();else cdp.once('exit',r);}),delay(5000)]);if(cdp.exitCode===null)cdp.kill();}
 result.processes=[...seen.values()];result.unit=ctl('show',unit,'-p','Result','-p','ExecMainStatus','-p','MemoryMax','-p','TasksMax','-p','NetworkNamespacePath');ctl('stop',unit);result.profile_after_bytes=size();result.profile_growth_bytes=result.profile_after_bytes-result.profile_before_bytes;
 const renderers=result.processes.filter(p=>p.args.includes('--type=renderer'));
 const parent=result.processes.find(p=>!p.args.includes('--type=')&&p.args.includes('--user-data-dir='));
 const safe=p=>!p.args.includes('--no-sandbox')&&!p.args.includes('--disable-seccomp-filter-sandbox')&&/NoNewPrivs:\s+1/.test(p.status)&&/Seccomp:\s+2/.test(p.status)&&/CapEff:\s+0{16}/.test(p.status)&&Number(/Uid:\s+(\d+)/.exec(p.status)?.[1])===guiUID;
 const filters=p=>Number(/Seccomp_filters:\s+(\d+)/.exec(p.status)?.[1]||0);
 result.native_sandbox_verified=!!parent&&renderers.length>0&&renderers.every(p=>safe(p)&&p.user_ns!==parent.user_ns&&p.pid_ns!==parent.pid_ns&&filters(p)>filters(parent));
 result.fcitx_private_started=fs.existsSync(log)&&/FCITX_PRIVATE_STATUS 0 [12]/.test(fs.readFileSync(log,'utf8'));
 result.feasibility=result.native_sandbox_verified?'native-sandbox-verified':'blocked-or-unverified';
 check(app+' never runs as root or disables renderer sandbox',()=>{for(const p of renderers)assert(safe(p));});
 check(app+' private Fcitx D-Bus starts without host session access',()=>assert(result.fcitx_private_started));
 // Application availability is reported separately; a refusal is not evidence
 // that its native sandbox worked, and never triggers --no-sandbox fallback.
 result.native_sandbox_test={passed:result.native_sandbox_verified?1:0,failed:result.native_sandbox_verified?0:1,skipped:0,not_executed:0};
 check(app+' native renderer namespace and additional seccomp filters verified',()=>assert(result.native_sandbox_verified));
}
report.profile_total_bytes=size();report.disk=cmd('df',['-PB1','/','/home/wdguest']);report.swap=fs.readFileSync('/proc/swaps','utf8');report.kernel_oom=cmd('dmesg').split('\n').filter(x=>/oom-kill:|out of memory/i.test(x));
check('no global OOM and original 512 MiB guest filesystem unchanged',()=>{assert(!report.kernel_oom.some(x=>x.includes('CONSTRAINT_NONE')));const stat=fs.statfsSync('/home/wdguest');assert(stat.blocks*stat.bsize<=512*1024*1024);});
fs.writeFileSync(OUT+'/gui-feasibility.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));process.exit(report.failed?1:0);
