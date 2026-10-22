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
const browser=await chromium.launch({executablePath:c.chrome,headless:true,chromiumSandbox:true});
const report={kind:'baseline-real-Chromium-download-probe',write_target_by_controller:false};
const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
const until=async fn=>{const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(150);}throw Error('Bounded probe timeout');};
try{
 await page.goto(c.url,{waitUntil:'domcontentloaded'});await page.getByPlaceholder('访问密码').fill(c.password);await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await page.getByText('终端',{exact:true}).first().waitFor();
 await page.getByText('浏览器',{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor();
 await page.getByTestId('basic-gui').locator('canvas').click();await page.keyboard.press('Control+l');await page.keyboard.type('https://203.0.113.2/',{delay:15});await page.keyboard.press('Enter');
 // Probe in the real private display namespace, without changing its network.
 const visible="import subprocess;pid=subprocess.check_output(['systemctl','show','webdesktop-gui@1.service','-p','MainPID','--value'],text=True).strip();r=subprocess.run(['nsenter','-t',pid,'-m','--','runuser','-u','wdgui','--','env','DISPLAY=:100','XAUTHORITY=/tmp/wd-gui/Xauthority','xdotool','search','--onlyvisible','--name','D1 HTTPS Fixture Ready'],capture_output=True,text=True);print(bool(r.returncode==0))";
 await until(()=>observer(visible)==='True');await page.screenshot({path:c.out+'/download-probe-https.png'});
 await page.keyboard.press('Enter');
 const inspect="import json,os,pathlib,hashlib;root=pathlib.Path('/home/wdguest');rows=[]\nfor p in [root/'Downloads/d1-probe.txt',root/'d1-probe.txt']:\n if p.is_file():\n  s=p.stat();rows.append(dict(path=str(p),uid=s.st_uid,gid=s.st_gid,mode=oct(s.st_mode&4095),size=s.st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))\nprint(json.dumps(rows))";
 await until(()=>JSON.parse(observer(inspect)).length>0);report.files=JSON.parse(observer(inspect));
 report.permissions=JSON.parse(observer("import json,pwd,os,pathlib;names=['wdsvc','wdguest','wdterm','wdgui'];r={u:dict(uid=pwd.getpwnam(u).pw_uid,gid=pwd.getpwnam(u).pw_gid,groups=os.getgrouplist(u,pwd.getpwnam(u).pw_gid)) for u in names};s=os.stat('/home/wdguest/Downloads');r['Downloads']=dict(uid=s.st_uid,gid=s.st_gid,mode=oct(s.st_mode&4095));print(json.dumps(r))"));
 report.api=await page.evaluate(async()=>{const out={};for(const p of ['/api/sys/fs?path=~/Downloads','/api/sys/fs?read=1&path=~/Downloads/d1-probe.txt','/api/sys/download?path=~/Downloads/d1-probe.txt']){const r=await fetch(p);const b=await r.arrayBuffer();out[p]={status:r.status,bytes:b.byteLength};}return out;});
 report.expected={size:Buffer.byteLength('WebDesktop Chromium download probe\n'.repeat(8192)),sha256:createHash('sha256').update('WebDesktop Chromium download probe\n'.repeat(8192)).digest('hex')};
 await page.screenshot({path:c.out+'/download-probe-completed.png'});
}catch(e){report.error=String(e.stack).split(c.password).join('[REDACTED]');await page.screenshot({path:c.out+'/download-probe-failure.png'}).catch(()=>{});throw e;}
finally{fs.writeFileSync(c.out+'/download-probe.json',JSON.stringify(report,null,2));await browser.close();}
console.log('Baseline download probe recorded (not full D1 acceptance)');
