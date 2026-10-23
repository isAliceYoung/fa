// D1-R2 TEST-only probe: repeatedly open a fresh native chrome://downloads tab
// in the visible restricted Chromium and record whether its content is ever
// presented. No fault injection, no CDP, no runtime/profile modification.
// Phase "quiet" makes no VM observer call while a tab opens; phase "polling"
// repeats the original harness pattern (SSH title observer every 200 ms).
import fs from 'node:fs';
import {randomBytes} from 'node:crypto';
import {execFileSync,execFile} from 'node:child_process';
import {chromium} from 'playwright-core';
let input='';for await(const p of process.stdin)input+=p;const c=JSON.parse(input);
const browser=await chromium.launch({executablePath:c.chrome,headless:true,chromiumSandbox:true});
const page=await (await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'})).newPage();page.setDefaultTimeout(20000);
const observer=(action,body,timeout=20000)=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 -I /root/download-bridge-vm.py '+action],{encoding:'utf8',input:body?JSON.stringify(body):undefined,timeout,stdio:['pipe','pipe','pipe']}));
const probe=topic=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 /root/staging-ui-probe.py '+topic],{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}));
const canvas=()=>page.getByTestId('basic-gui').locator('canvas');
// Calibrated on retained evidence: truly white tabs measure exactly 0, an
// empty-but-rendered Downloads page about 0.03, a partial first frame 0.013.
const PAINTED=.005;
const report={kind:'d1-r2-native-downloads-paint-probe',config:c.config,iterations:[],summary:{}};
async function paint(){try{return await canvas().evaluate(e=>{const x=e.getContext('2d');const W=e.width,H=e.height,y0=Math.round(H*.17),y1=Math.round(H*.98),x1=Math.round(W*.70);const d=x.getImageData(0,y0,x1,y1-y0).data;let n=0,t=0;for(let i=0;i<d.length;i+=4*7){t++;if(Math.min(d[i],d[i+1],d[i+2])<235)n++;}return Math.round(n/t*1e4)/1e4;});}catch{return null;}}
async function waitPaint(ms){const s=Date.now();while(Date.now()-s<ms){const v=await paint();if(v!==null&&v>=PAINTED)return Date.now()-s;await page.waitForTimeout(100);}return null;}
async function until(fn,timeout=45000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(250);}throw Error('probe setup timeout');}
// Background SSH title polling, identical in cost to the original observer loop.
let polling=false,polls=0;
function pollLoop(){if(!polling)return;execFile('ssh',[...c.ssh,'sudo python3 -I /root/download-bridge-vm.py title'],{timeout:12000},()=>{polls++;setTimeout(pollLoop,200);});}
async function domSelectable(){
 // Same one-time private-display selection marker as the harness: does the
 // white page contain any DOM text that Ctrl+A/Ctrl+C can copy?
 const marker='D1-selection-'+randomBytes(12).toString('hex');
 observer('download-ui',{name:'Download history',names:[],marker});
 await canvas().click({position:{x:300,y:400}});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await page.waitForTimeout(1500);
 const ui=observer('download-ui',{name:'Download history',names:[],marker_check:marker});
 return {copied:!ui.selection_marker,excerpt:ui.fixture_excerpt.slice(0,80)};
}
try{
 await page.goto(c.url,{waitUntil:'domcontentloaded'});
 await page.getByPlaceholder('访问密码').fill(c.password);await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await page.getByText('终端',{exact:true}).first().waitFor();
 await page.getByText('浏览器',{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:45000});
 await until(()=>probe('state').windows.some(w=>w.app==='chromium'&&w.native_new_tab));
 await page.waitForTimeout(3000);
 // Chromium reuses a New Tab page for Ctrl+J. Leave a non-NTP tab first so
 // every iteration opens (and Ctrl+W closes) its own fresh downloads tab.
 // One genuine native download first, so the page has a card as in D1.
 const token=randomBytes(16).toString('hex');
 await canvas().click({position:{x:540,y:24}});await page.keyboard.press('Control+l');await page.keyboard.type('https://203.0.113.2/page/normal?visit=1&task='+token,{delay:8});await page.keyboard.press('Enter');
 await until(()=>observer('title').some(t=>t.startsWith('D1 Ready normal 1')));await page.keyboard.press('Enter');
 await until(()=>observer('state').files.some(f=>f.name==='d1-normal.txt'));report.precondition_download=true;await page.waitForTimeout(1500);
 const phases=[['quiet',12],['polling',12],['quiet',6]];
 for(const [phase,count] of phases){
  polling=phase==='polling';if(polling)pollLoop();
  for(let i=0;i<count;i++){
   const it={phase,index:report.iterations.length,t:Date.now(),polls_before:polls};
   await canvas().click({position:{x:540,y:24}});  // focus the Chromium window strip, not page content
   await page.keyboard.press('Control+j');
   it.painted_ms=await waitPaint(8000);
   if(it.painted_ms===null){
    polling=false;  // stop background load while collecting the white-state evidence
    it.white=true;it.host_ratio=await paint();
    try{it.x11=observer('xwd');}catch(e){it.x11={error:String(e).slice(0,160)};}
    try{it.dom=await domSelectable();}catch(e){it.dom={error:String(e).slice(0,160)};}
    await page.screenshot({path:c.out+'/r2-paint-white-'+it.index+'.png'});
    it.remedies=[];
    await canvas().hover({position:{x:400,y:380}});await page.mouse.move(420,520,{steps:8});
    it.remedies.push({step:'mouse-move',painted_ms:await waitPaint(3000)});
    if(it.remedies.at(-1).painted_ms===null){await page.keyboard.press('Control+Tab');await page.waitForTimeout(600);await page.keyboard.press('Control+Shift+Tab');it.remedies.push({step:'tab-switch',painted_ms:await waitPaint(3000)});}
    if(it.remedies.at(-1).painted_ms===null){await page.keyboard.press('F5');it.remedies.push({step:'reload',painted_ms:await waitPaint(6000)});}
    try{it.x11_after=observer('xwd');}catch(e){it.x11_after={error:String(e).slice(0,160)};}
    it.diag_units=observer('diag').units;
    polling=phase==='polling';if(polling)pollLoop();
   }
   await page.keyboard.press('Control+w');await page.waitForTimeout(1200);
   report.iterations.push(it);
  }
  polling=false;await page.waitForTimeout(1500);
 }
}catch(e){report.error=String(e.stack).split(c.password).join('[REDACTED]').slice(0,1500);}
finally{
 polling=false;
 for(const phase of ['quiet','polling']){const xs=report.iterations.filter(x=>x.phase===phase);report.summary[phase]={opened:xs.length,white:xs.filter(x=>x.white).length,painted_ms:xs.filter(x=>!x.white).map(x=>x.painted_ms)};}
 fs.writeFileSync(c.out+'/r2-paint-probe.json',JSON.stringify(report,null,2));await browser.close();
}
console.log(JSON.stringify(report.summary));
