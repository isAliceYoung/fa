// Fixed-text only, disposable VM. Never a user keystroke/command recorder.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {OUT,cmd,ctl,value,delay,until,login,display,stopDisplay,guiExec,unit,snapshot} from './desktop_lab.mjs';
export async function ximCase({checked=false,label='xim',connection=null,burst=false}={}){
 const active=connection??await display(await login());
 const base='/home/wdguest/.gui/xim-diagnostic-'+label;
 const target='/home/wdguest/Documents/ime-'+label+'.txt';
 const fixture='wdlab-ime-'+label;
 const evidence={label,checked,burst,stages:[],fixed_text_only:true,target};
 const started=Math.floor(Date.now()/1000);
 const type=burst?active.typeBurst:active.type;
 const env=['HOME=/home/wdguest','USER=wdgui','PATH=/usr/bin:/bin','LANG=C.UTF-8','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/wd-gui/bus','XDG_RUNTIME_DIR=/tmp/wd-gui','GTK_IM_MODULE=fcitx','QT_IM_MODULE=fcitx','XMODIFIERS=@im=fcitx','NO_AT_BRIDGE=1'];
 const props=['User=wdgui','Group=wdguest','SupplementaryGroups=wdgui','Slice=webdesktop-gui.slice','NoNewPrivileges=yes','CapabilityBoundingSet=','ProtectSystem=strict','ProtectHome=tmpfs','BindPaths=/home/wdguest /run/webdesktop-display/1/ipc:/tmp/wd-gui /run/webdesktop-display/1/x11:/tmp/.X11-unix','TemporaryFileSystem=/tmp:rw,nosuid,nodev,noexec,size=16M /run:ro,size=1M','PrivateNetwork=yes','RestrictAddressFamilies=AF_UNIX','PrivateDevices=yes','ProtectControlGroups=yes','InaccessiblePaths=/root /opt/webdesktop /var/lib/webdesktop /home/wdguest/.wd-state','MemoryMax=96M','MemorySwapMax=32M','TasksMax=32','KillMode=control-group','RuntimeMaxSec=60','TimeoutStopSec=1s','LimitCORE=0','BindsTo='+unit];
 const transcript=()=>{try{return fs.readFileSync(base+'.log','utf8').slice(-12000);}catch{return '';}};
 let wid;
 function stage(name){
  const focus=guiExec(['xdotool','getwindowfocus']);
  evidence.stages.push({name,focus,xterm:wid,focused:focus===wid,ime:guiExec(['fcitx5-remote']),transcript:transcript()});
  guiExec(['scrot',base+'-'+name+'.png']);fs.copyFileSync(base+'-'+name+'.png',OUT+'/'+label+'-'+name+'.png');
 }
 try{
  cmd('systemd-run',['--quiet','--unit='+fixture,...props.map(p=>'--property='+p),'/usr/bin/env','-i',...env,'/usr/bin/xterm','-title','WD IME XIM','-u8','-xrm','XTerm*openIm:true','-xrm','XTerm*inputMethod:fcitx','-e','/usr/bin/script','-q','-f','-c','/bin/bash --noprofile --norc',base+'.log']);
  await until(()=>{try{wid=guiExec(['xdotool','search','--onlyvisible','--name','WD IME XIM']).split('\n')[0];return !!wid;}catch{return false;}});
  guiExec(['xdotool','windowactivate','--sync',wid]);await delay(600);guiExec(['fcitx5-remote','-c']);
  await type("printf '%s' '");await active.press([0xffe3,0x20]);await type('nihao');await delay(600);stage('candidates');
  await type('1');stage('digit-1');
  if(checked){await until(()=>transcript().includes('你好'));assert.equal(guiExec(['xdotool','getwindowfocus']),wid);}
  await active.press([0xffe3,0x20]);stage('ime-closed');
  if(checked){await until(()=>guiExec(['fcitx5-remote'])==='1');assert.equal(guiExec(['xdotool','getwindowfocus']),wid);}
  await type("' > "+target);stage('command-tail');await active.press([0xff0d]);stage('enter');
  await until(()=>fs.existsSync(target));assert.equal(fs.readFileSync(target,'utf8'),'你好');evidence.passed=true;
 }catch(e){evidence.passed=false;evidence.error=String(e.stack);try{stage('failure');}catch{}}
 finally{
  evidence.transcript=transcript();evidence.unit=ctl('show',fixture,'-p','ActiveState','-p','Result','-p','ExecMainStatus');evidence.resources=snapshot();
  evidence.server_queue_budget_rejected=cmd('journalctl',['--no-pager','-o','cat','--since=@'+started,'-u','wdlab-next.service']).includes('GUI input rejected: GUI queue budget');
  fs.writeFileSync(OUT+'/'+label+'.json',JSON.stringify(evidence,null,2));ctl('stop',fixture);
  if(!connection)await stopDisplay(active);
 }
 return evidence;
}
if(process.argv.includes('--standalone')){
 const reproduction=await ximCase({burst:true,label:'xim-burst-reproduction'});
 const results=[];for(const checked of [false,true])results.push(await ximCase({checked,label:checked?'xim-checked':'xim-batched'}));
 const report={passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,skipped:0,not_executed:0,burst_reproduction:{result:reproduction.passed?'not-reproduced':'failed-as-recorded',queue_budget_rejected:reproduction.server_queue_budget_rejected,detail:reproduction.error},results:results.map(({label,passed})=>({label,passed}))};
 fs.writeFileSync(OUT+'/xim-isolated.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 // Diagnosis always collects both controlled variants; the original failure
 // remains in JSON rather than preventing the sequence interaction experiment.
}
