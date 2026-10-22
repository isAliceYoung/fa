// Host Chrome -> authenticated noVNC -> actual sandboxed guest Chromium -> HTTPS.
// This first-stage probe records baseline behavior; it is not a pass assertion
// for the unfinished download feature, and never writes a positive target file.
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
let input='';for await(const p of process.stdin)input+=p;const c=JSON.parse(input);
const observer=code=>execFileSync('ssh',[...c.ssh,"sudo python3 -I -c '"+code.replaceAll("'","'\\''")+"'"],{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}).trim();
const probe=topic=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 /root/staging-ui-probe.py '+topic],{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}));
const browser=await chromium.launch({executablePath:c.chrome,headless:true,chromiumSandbox:true});
const report={kind:'baseline-real-Chromium-download-probe',write_target_by_controller:false};
const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
const until=async fn=>{const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(150);}throw Error('Bounded probe timeout');};
try{
 await page.goto(c.url,{waitUntil:'domcontentloaded'});await page.getByPlaceholder('访问密码').fill(c.password);await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await page.getByText('终端',{exact:true}).first().waitFor();
 await page.getByText('浏览器',{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor();
 await until(async()=>Number(await page.getByTestId('basic-gui').getAttribute('data-first-frame-ms'))>0&&probe('state').windows.some(w=>w.app==='chromium'&&w.native_new_tab));
 async function navigate(url){await page.getByTestId('basic-gui').locator('canvas').click({position:{x:300,y:250}});await page.keyboard.press('Control+l');await page.waitForTimeout(250);await page.keyboard.type(url,{delay:15});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await until(()=>probe('address').sha256===createHash('sha256').update(url).digest('hex'));await page.keyboard.press('Enter');}
 await navigate('https://203.0.113.2/');
 // Probe in the real private display namespace, without changing its network.
 const visible=which=>"import subprocess;pid=subprocess.check_output(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value'],text=True).strip();r=subprocess.run(['nsenter','-t',pid,'-m','--','runuser','-u','wdgui','--','env','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','xdotool','search','--onlyvisible','--name','D1 HTTPS Fixture Ready "+which+"'],capture_output=True,text=True);print(bool(r.returncode==0))";
 await until(()=>observer(visible('/baseline'))==='True');await page.screenshot({path:c.out+'/download-probe-https.png'});
 await page.keyboard.press('Enter');
 const inspect="import json,os,pathlib,hashlib;root=pathlib.Path('/home/wdguest');rows=[]\nfor p in [root/'Downloads/d1-probe.txt',root/'d1-probe.txt']:\n if p.is_file():\n  s=p.stat();rows.append(dict(path=str(p),uid=s.st_uid,gid=s.st_gid,mode=oct(s.st_mode&4095),size=s.st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))\nprint(json.dumps(rows))";
 await until(()=>JSON.parse(observer(inspect)).length>0);report.files=JSON.parse(observer(inspect));
 report.permissions=JSON.parse(observer("import json,pwd,os,pathlib;names=['wdsvc','wdguest','wdterm','wdgui'];r={u:dict(uid=pwd.getpwnam(u).pw_uid,gid=pwd.getpwnam(u).pw_gid,groups=os.getgrouplist(u,pwd.getpwnam(u).pw_gid)) for u in names};s=os.stat('/home/wdguest/Downloads');r['Downloads']=dict(uid=s.st_uid,gid=s.st_gid,mode=oct(s.st_mode&4095));print(json.dumps(r))"));
 report.api=await page.evaluate(async()=>{const out={};for(const p of ['/api/sys/fs?path=~/Downloads','/api/sys/fs?read=1&path=~/Downloads/d1-probe.txt','/api/sys/download?path=~/Downloads/d1-probe.txt']){const r=await fetch(p);const b=await r.arrayBuffer();out[p]={status:r.status,bytes:b.byteLength};}return out;});
 report.expected={size:Buffer.byteLength('WebDesktop Chromium download probe\n'.repeat(8192)),sha256:createHash('sha256').update('WebDesktop Chromium download probe\n'.repeat(8192)).digest('hex')};
 await page.screenshot({path:c.out+'/download-probe-completed.png'});
 await navigate('https://203.0.113.2/slow-page');await until(()=>observer(visible('/slow'))==='True');await page.keyboard.press('Enter');
 const partial="import pathlib,json;p=list(pathlib.Path('/home/wdguest/Downloads').glob('*.crdownload'));print(json.dumps([dict(name=x.name,size=x.stat().st_size) for x in p]))";
 await until(()=>JSON.parse(observer(partial)).some(x=>x.size>0));report.partial=JSON.parse(observer(partial));
 report.partialApi=await page.evaluate(async name=>{const r=await fetch('/api/sys/raw?path='+encodeURIComponent('~/Downloads/'+name));const b=await r.arrayBuffer();return{status:r.status,bytes:b.byteLength};},report.partial[0].name);
 // Ordinary data-domain fault injection, never a controller-generated download.
 const replacement="import subprocess,json\ncode=\"import os;os.mkdir('/home/wdguest/Documents/d1-escape');os.rename('/home/wdguest/Downloads','/home/wdguest/Downloads-d1-kept');os.symlink('/home/wdguest/Documents/d1-escape','/home/wdguest/Downloads')\"\nr=subprocess.run(['runuser','-u','wdguest','--','python3','-I','-c',code],capture_output=True,text=True);print(json.dumps(dict(replaced=r.returncode==0,exit=r.returncode)))";
 report.directoryReplacement=JSON.parse(observer(replacement));
 try{
  if(report.directoryReplacement.replaced){
   await navigate('https://203.0.113.2/escape-page');await until(()=>observer(visible('/escape'))==='True');await page.keyboard.press('Enter');
   // Diagnosis must distinguish refusal, a pinned old directory and escape;
   // do not assume that a replaceable pathname proves Chromium follows it.
   const evidence="import os,json,sqlite3\nfiles=[]\nfor base,dirs,names in os.walk('/home/wdguest'):\n dirs[:]=[d for d in dirs if d not in ('.gui','.wd-state','.pki')]\n for n in names:\n  if 'd1-escape-probe' in n:files.append(os.path.join(base,n))\ndb=sqlite3.connect('file:/home/wdguest/.gui/chromium/Default/History?mode=ro&immutable=1',uri=True);rows=list(db.execute(\"SELECT target_path,current_path,state,interrupt_reason,received_bytes,total_bytes FROM downloads ORDER BY id DESC LIMIT 4\"));db.close();print(json.dumps(dict(files=files,history=rows)))";
   const prompt="import subprocess,json;pid=subprocess.check_output(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value'],text=True).strip();r=subprocess.run(['nsenter','-t',pid,'-m','--','runuser','-u','wdgui','--','env','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','xdotool','search','--onlyvisible','--name','^Save File$'],capture_output=True,text=True);print(json.dumps(dict(save_dialog=r.returncode==0)))";
   await until(()=>JSON.parse(observer(prompt)).save_dialog||JSON.parse(observer(evidence)).history.some(r=>r[0].includes('d1-escape-probe')&&r[2]!==0));
   report.directoryReplacement.prompt=JSON.parse(observer(prompt));
   report.directoryReplacement.native=JSON.parse(observer(evidence));
   report.directoryReplacement.browserEscaped=report.directoryReplacement.native.files.some(p=>p.startsWith('/home/wdguest/Documents/d1-escape/'));
   report.directoryReplacement.agentStatus=await page.evaluate(async()=>(await fetch('/api/sys/fs?path=~/Downloads')).status);
   if(report.directoryReplacement.prompt.save_dialog){await page.screenshot({path:c.out+'/download-probe-replaced-directory-prompt.png'});await page.keyboard.press('Escape');}
  }
 }finally{if(report.directoryReplacement.replaced)observer("import subprocess;subprocess.run(['runuser','-u','wdguest','--','python3','-I','-c',\"import os;assert os.path.islink('/home/wdguest/Downloads');os.unlink('/home/wdguest/Downloads');os.rename('/home/wdguest/Downloads-d1-kept','/home/wdguest/Downloads')\"],check=True)");}
}catch(e){report.error=String(e.stack).split(c.password).join('[REDACTED]');await page.screenshot({path:c.out+'/download-probe-failure.png'}).catch(()=>{});throw e;}
finally{fs.writeFileSync(c.out+'/download-probe.json',JSON.stringify(report,null,2));await browser.close();}
console.log('Baseline download probe recorded (not full D1 acceptance)');
