// Actual desktop/noVNC interaction on the disposable staging VM.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
import {boundedDesktopSize} from '../public/novnc/core/util/desktop-size.mjs';
let input='';for await(const part of process.stdin)input+=part;const cfg=JSON.parse(input);
const browser=await chromium.launch({executablePath:cfg.chrome,headless:true,chromiumSandbox:true});
const report={passed:0,failed:0,skipped:0,not_executed:5,tests:[],sizes:[],firstFrames:[],clientErrors:[]};
const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1.25,locale:'zh-CN'}),page=await context.newPage();
page.setDefaultTimeout(10000);
let graphicalSid='';
page.on('websocket',socket=>{const u=new URL(socket.url());if(u.pathname==='/api/gui/ws')graphicalSid=u.searchParams.get('sid');});
const probe=topic=>JSON.parse(execFileSync('ssh',[...cfg.ssh,'sudo python3 /root/staging-ui-probe.py '+topic],{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}));
const gui=()=>page.getByTestId('basic-gui'),frame=()=>gui().locator('canvas');
async function until(fn,timeout=12000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(150);}throw new Error('Bounded real UI timeout');}
async function test(name,fn){report.not_executed--;try{await fn();report.passed++;report.tests.push({name,result:'passed'});}catch(e){report.failed++;report.tests.push({name,result:'failed',detail:String(e.stack).split(cfg.password).join('[REDACTED]').slice(0,1600)});await page.screenshot({path:cfg.out+'/staging-ui-failure-'+report.failed+'.png'}).catch(()=>{});if(await gui().count())await close().catch(()=>{});for(let i=0;i<4&&await page.getByRole('button',{name:'关闭',exact:true}).count();i++)await page.getByRole('button',{name:'关闭',exact:true}).first().click().catch(()=>{});}}
page.on('pageerror',error=>{report.clientErrors.push((error.name+': '+error.message).replace(/[A-Za-z0-9_-]{24,}/g,'[TOKEN]').slice(0,400));if(report.clientErrors.length>20)report.clientErrors.shift();});
async function login(target){await target.getByPlaceholder('访问密码').fill(cfg.password);await target.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await target.getByText('终端',{exact:true}).first().waitFor();}
async function open(name){await page.getByText(name,{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:15000});const ms=Number(await gui().getAttribute('data-first-frame-ms'));assert(ms>0&&ms<15000);report.firstFrames.push({app:name,first_frame_ms:ms});}
async function close(){const sid=graphicalSid;await gui().locator('xpath=../..').getByRole('button',{name:'关闭',exact:true}).click();await until(async()=>await page.evaluate(async sid=>{const r=await fetch('/api/gui/status?sid='+sid);return r.status===200&&['closed','pending'].includes((await r.json()).state);},sid));}
async function navigate(url){await frame().click();await page.keyboard.press('Control+l');await page.waitForTimeout(250);await page.keyboard.type(url,{delay:12});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await until(()=>probe('address').sha256===createHash('sha256').update(url).digest('hex'));await page.keyboard.press('Enter');}
try{
 await page.goto(cfg.url,{waitUntil:'domcontentloaded'});await login(page);
 await test('authenticated aggregate monitor and file copy show success without VNC bridge requests',async()=>{
  await page.getByTestId('resource-status').waitFor();await until(async()=>await page.getByTestId('resource-status').getAttribute('data-level')!=='unknown');
  const name='ui-copy-'+Date.now()+'.txt';let bridges=0;page.on('request',r=>{if(r.url().includes('/api/gui/clipboard'))bridges++;});
  assert.equal(await page.evaluate(async name=>(await fetch('/api/sys/fs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dir:'~',name,kind:'file',content:'UI clipboard proof'})})).status,name),200);
  await page.getByText('主目录',{exact:true}).first().dblclick();await page.getByText(name,{exact:true}).last().click({button:'right'});
  await page.locator('[data-ctxmenu]').getByRole('button',{name:/复制（在文件夹中粘贴）/}).click();await page.getByText(new RegExp('已复制.*'+name)).waitFor();
  assert.equal(bridges,0);assert.equal(await page.getByText('此功能的访客执行层尚未迁移',{exact:false}).count(),0);
  assert.equal(await page.getByText('文件系统 /',{exact:true}).count(),0);assert.equal(await page.getByText('/etc',{exact:true}).count(),0);
  await page.getByRole('button',{name:'关闭',exact:true}).first().click();
 });
 await test('PCManFM native maximization and dynamic framebuffer retain worker PID and pointer coordinates',async()=>{
  await open('PCManFM');await until(()=>probe('state').windows.some(w=>w.app==='[Pp]cmanfm'&&w.maximized));const initial=probe('state');
  await gui().locator('xpath=../..').getByRole('button',{name:'最大化',exact:true}).click();
  for(const [width,height] of [[1920,1080],[1440,1000],[1000,800],[720,720]]){
   await page.setViewportSize({width,height});
   await until(async()=>{const s=probe('state'),canvas=await frame().evaluate(c=>({w:c.width,h:c.height})),box=await gui().locator(':scope > div.relative').boundingBox(),target=boundedDesktopSize(box.width,box.height);return s.pid===initial.pid&&s.framebuffer[0]===canvas.w&&s.framebuffer[1]===canvas.h&&canvas.w===target.width&&canvas.h===target.height&&s.windows.some(w=>w.maximized&&Math.abs(w.width-canvas.w)<=2&&Math.abs(w.height-canvas.h)<=50);});
   const native=probe('state');report.sizes.push({viewport:[width,height],...native});
   assert(native.windows.some(w=>w.maximized&&w.width>=native.framebuffer[0]-.1*native.framebuffer[0]));
   // Neutral file-view space, not the toolbar's history dropdown (which grabs
   // GTK input while open). Verify coordinates before keyboard navigation.
   const canvasBox=await frame().boundingBox(),point={x:canvasBox.width*.75,y:canvasBox.height*.75};await frame().click({position:point});
   const expected={X:Math.floor(point.x*native.framebuffer[0]/canvasBox.width),Y:Math.floor(point.y*native.framebuffer[1]/canvasBox.height)};
   await until(()=>{const p=probe('state').pointer;return Math.abs(p.X-expected.X)<=2&&Math.abs(p.Y-expected.Y)<=2;});
   const documents=width===1920||width===1000;
   await page.keyboard.press('Escape');await page.keyboard.press('Control+l');await page.waitForTimeout(250);await page.keyboard.type('/home/wdguest/'+(documents?'Documents':'Downloads'),{delay:12});await page.keyboard.press('Enter');
   await until(()=>probe('state').windows.some(w=>documents?w.documents_open:w.downloads_open));
  }
  await page.screenshot({path:cfg.out+'/staging-pcmanfm-resize.png'});await close();await page.setViewportSize({width:1440,height:1000});
 });
 await test('Chromium opens native new tab and recommended Google policy is actually loaded',async()=>{
  await open('浏览器');assert(probe('state').windows.some(w=>w.app==='chromium'&&w.native_new_tab));
  await navigate('chrome://policy');
  // The real WebUI can replace its loading DOM. Re-select after observable
  // clipboard results; never mistake the old omnibox selection for page data.
  await until(async()=>{await frame().click({position:{x:40,y:250}});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');return probe('policy').google_policy_visible;});await page.screenshot({path:cfg.out+'/staging-google-policy.png'});
 });
 await test('real resized Chromium pinyin candidates digit selection and English switch remain correct',async()=>{
  if(!await gui().count())await open('浏览器');
  const before=probe('state');await gui().locator('xpath=../..').getByRole('button',{name:'最大化',exact:true}).click();await page.setViewportSize({width:1000,height:800});
  await until(async()=>{const s=probe('state'),box=await gui().locator(':scope > div.relative').boundingBox(),target=boundedDesktopSize(box.width,box.height);return s.pid===before.pid&&s.framebuffer[0]===target.width&&s.framebuffer[1]===target.height;});
  await navigate('data:text/html,'+encodeURIComponent('<html><head><title>Staging IME</title></head><body style="font-size:32px"><h1>Staging IME</h1><input autofocus onfocus="document.title=\'Staging IME Focused\'" oninput="if(this.value===\'你好\')document.title=\'Staging IME Chinese\'" style="position:absolute;left:20px;top:180px;height:60px;font-size:36px;width:90%"></body></html>'));
  await until(()=>probe('state').windows.some(w=>w.ime_fixture));
  // A mapped window/title does not prove document focus or an active IME.
  // Observe real focus via the fixture title and the private, read-only IME
  // state; all activation and text still travel through browser/noVNC keys.
  await until(()=>probe('state').windows.some(w=>w.ime_focused));
  if(probe('ime').state===2){await page.keyboard.press('Control+Space');await until(()=>probe('ime').state===1);}
  await until(()=>probe('ime').state===1);await page.keyboard.press('Control+Space');await until(()=>probe('ime').state===2);
  await page.keyboard.type('nihao',{delay:40});await page.waitForTimeout(700);
  await page.screenshot({path:cfg.out+'/staging-pinyin-candidates.png'});await page.keyboard.type('1');await until(()=>probe('state').windows.some(w=>w.ime_committed));await page.keyboard.press('Control+Space');await until(()=>probe('ime').state===1);await page.keyboard.type('hello',{delay:40});
  await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await until(()=>probe('clipboard').ime_correct);
  await page.screenshot({path:cfg.out+'/staging-chinese-input.png'});await close();await page.setViewportSize({width:1440,height:1000});
 });
 await test('another device invalidates the monitor and live GUI without bypassing lease cleanup',async()=>{
  await open('浏览器');const other=await browser.newContext(),newPage=await other.newPage();await newPage.goto(cfg.url);await login(newPage);
  await page.getByPlaceholder('访问密码').waitFor({timeout:15000});
  // New login legitimately restores its saved browser window. Close that NEW
  // authenticated window deliberately before handing the VM to health checks.
  await newPage.locator('[data-testid="basic-gui"][data-status="open"], [data-testid="basic-gui"][data-status="closed"]').waitFor({timeout:15000});
  await newPage.getByTestId('basic-gui').locator('xpath=../..').getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>probe('idle').idle);report.afterUiCleanup=probe('idle');await other.close();
 });
}catch(e){
 if(report.failed===0){report.failed++;report.tests.push({name:'staging UI fixture',result:'failed',detail:String(e.name)});}
 await page.screenshot({path:cfg.out+'/staging-ui-failure.png'}).catch(()=>{});
}finally{fs.writeFileSync(cfg.out+'/staging-ui.json',JSON.stringify(report,null,2));await browser.close();}
console.log(JSON.stringify({passed:report.passed,failed:report.failed,skipped:0,not_executed:report.not_executed}));
process.exitCode=report.failed?1:0;
