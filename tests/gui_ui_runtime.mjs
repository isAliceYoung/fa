import assert from 'node:assert/strict';
import fs from 'node:fs';
import {chromium} from 'playwright-core';
let input='';for await(const chunk of process.stdin)input+=chunk;const config=JSON.parse(input);
const browser=await chromium.launch({executablePath:config.chrome,headless:true,chromiumSandbox:true});
const report={passed:0,failed:0,skipped:0,not_executed:4,tests:[]};let page;const frames=[];
async function check(name,body){report.not_executed--;try{await body();report.passed++;report.tests.push({name,result:'passed'});}catch(e){report.failed++;report.tests.push({name,result:'failed',error:String(e.stack).slice(0,2500)});throw e;}}
const enc=x=>Buffer.from(x).toString('base64');
async function terminal(){await page.getByText('终端',{exact:true}).first().dblclick();await page.locator('[data-testid="system-terminal"][data-status="open"]').waitFor({timeout:15000});await page.locator('.xterm-helper-textarea').first().focus();}
async function osc(command){await page.keyboard.type("printf '\\033]777;gui;"+enc(command)+";"+enc('~')+"\\007'");await page.keyboard.press('Enter');await page.waitForTimeout(400);}
try{
 const context=await browser.newContext({viewport:{width:1280,height:900},locale:'zh-CN'});page=await context.newPage();
 page.on('websocket',socket=>{if(!socket.url().includes('/api/gui/ws'))return;frames.push({event:'connect'});socket.on('framesent',f=>{frames.push({event:'input',bytes:Buffer.isBuffer(f.payload)?f.payload.toString('hex'):f.payload});if(frames.length>1000)frames.shift();});});
 await page.goto(config.url,{waitUntil:'domcontentloaded'});await page.getByPlaceholder('访问密码').fill(config.password);await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();
 await check('OSC arbitrary commands rejected, fixed requests require deliberate review and cancellation does not execute',async()=>{
  await terminal();await osc('pcmanfm; touch /home/wdguest/osc-forbidden');assert.equal(await page.locator('[data-testid="terminal-action-request"]').count(),0);assert.equal(frames.length,0);
  await osc('pcmanfm');await page.getByTestId('terminal-action-request').waitFor();await page.keyboard.press('Enter');await page.waitForTimeout(300);assert.equal(frames.length,0);assert.equal(await page.getByText('审阅终端请求',{exact:true}).count(),0);
  await page.getByRole('button',{name:'审阅请求',exact:true}).click();await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal(frames.length,0);
  await page.getByRole('button',{name:'关闭',exact:true}).first().click();
 });
 await check('real noVNC canvas, PCManFM mouse keyboard and shared-folder creation',async()=>{
  await page.getByText('PCManFM',{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:20000});
  const canvas=page.locator('[data-testid="basic-gui"] canvas');await canvas.waitFor();await page.waitForTimeout(1000);await canvas.click({position:{x:250,y:180}});
  await page.keyboard.press('Control+l');await page.keyboard.type('/home/wdguest/Documents');await page.keyboard.press('Enter');await page.waitForTimeout(400);
  await page.keyboard.press('Control+Shift+n');await page.waitForTimeout(300);const folder='gui-ui-'+Date.now();await page.keyboard.type(folder);await page.keyboard.press('Enter');
  await page.waitForFunction(async name=>JSON.stringify(await(await fetch('/api/sys/fs?path=~/Documents')).json()).includes(name),folder,{timeout:10000});
  const colors=await canvas.evaluate(c=>{const ctx=c.getContext('2d'),d=ctx.getImageData(0,0,c.width,c.height).data,colors=new Set();for(let i=0;i<d.length;i+=128)colors.add(`${d[i]},${d[i+1]},${d[i+2]}`);return colors.size;});assert(colors>20);
  assert(frames.some(f=>f.event==='input'&&f.bytes.startsWith('05')));
  // TigerVNC/noVNC may negotiate QEMU ExtendedKeyEvent (ff00) instead of
  // legacy KeyEvent (04). Folder creation above proves actual GTK delivery.
  assert(frames.some(f=>f.event==='input'&&(f.bytes.startsWith('04')||f.bytes.startsWith('ff00'))));
  await page.screenshot({path:config.out+'/pcmanfm-ui.png'});await page.getByRole('button',{name:'关闭',exact:true}).first().click();await page.waitForTimeout(600);
 });
 await check('approved fixed OSC request opens only the restricted PCManFM session',async()=>{
  await terminal();await osc('pcmanfm');await page.getByTestId('terminal-action-request').waitFor();const before=frames.filter(f=>f.event==='connect').length;
  await page.getByRole('button',{name:'审阅请求',exact:true}).click();assert.equal(frames.filter(f=>f.event==='connect').length,before);
  await page.getByRole('button',{name:'确认打开',exact:true}).click();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:20000});assert.equal(frames.filter(f=>f.event==='connect').length,before+1);
  await page.screenshot({path:config.out+'/osc-confirmed-gui.png'});
 });
 await check('another device login revokes the live GUI and terminal UI',async()=>{
  const other=await browser.newContext();const newcomer=await other.newPage();await newcomer.goto(config.url);await newcomer.getByPlaceholder('访问密码').fill(config.password);await newcomer.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();
  await page.getByPlaceholder('访问密码').waitFor({timeout:15000});assert.equal(await page.getByTestId('basic-gui').count(),0);await other.close();
 });
}finally{
 if(report.failed&&page&&!page.isClosed()){await page.screenshot({path:config.out+'/gui-ui-failure.png'}).catch(()=>{});fs.writeFileSync(config.out+'/gui-ui-dom.txt',await page.locator('body').innerText());}
 fs.writeFileSync(config.out+'/gui-ui.json',JSON.stringify({...report,total:report.passed+report.failed},null,2));fs.writeFileSync(config.out+'/gui-input-frames.json',JSON.stringify(frames,null,2));await browser.close();
}
console.log(JSON.stringify(report));
