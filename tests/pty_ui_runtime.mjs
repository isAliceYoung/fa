import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright-core';

let input='';for await(const chunk of process.stdin)input+=chunk;
const config=JSON.parse(input);
const browser=await chromium.launch({executablePath:config.chrome,headless:true,chromiumSandbox:true});
const report={passed:0,failed:0,skipped:0,not_executed:3,tests:[]};
let page;
const frames=[];
async function check(name,body){report.not_executed--;try{await body();report.passed++;report.tests.push({name,result:'passed'});}catch(e){report.failed++;report.tests.push({name,result:'failed',error:String(e.stack).slice(0,2000)});throw e;}}
try{
  const context=await browser.newContext({viewport:{width:1280,height:900},locale:'zh-CN'});
  await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:config.url});
  page=await context.newPage();
  page.on('websocket',socket=>{
    if(!socket.url().includes('/api/term/ws'))return;
    for(const direction of ['framesent','framereceived'])socket.on(direction,frame=>{
      frames.push({direction,data:Buffer.isBuffer(frame.payload)?frame.payload.toString('utf8'):frame.payload});
      if(frames.length>500)frames.shift();
    });
  });
  await page.goto(config.url,{waitUntil:'networkidle'});
  await check('real desktop login, xterm, ANSI color, UTF-8, keyboard input and resize',async()=>{
    await page.getByPlaceholder('访问密码').fill(config.password);
    await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();
    await page.getByText('终端',{exact:true}).first().dblclick();
    await page.locator('[data-testid="system-terminal"][data-status="open"]').waitFor({timeout:15000});
    await page.locator('.xterm-helper-textarea').first().focus();
    await page.keyboard.type("printf '\\033[31mUI_%s\\033[0m\\n' TERMINAL_OK; printf 'UTF8_%s\\n' ");
    // Chromium keyboard.type does not synthesize IME composition for CJK.
    // Exercise the real clipboard shortcut instead of dispatching mock events.
    await page.evaluate(()=>navigator.clipboard.writeText('你好'));
    await page.keyboard.press('Control+Shift+V');
    const end=Date.now()+3000;
    while(!frames.some(frame=>frame.direction==='framesent'&&frame.data.includes('你好'))&&Date.now()<end)await new Promise(resolve=>setTimeout(resolve,20));
    assert(frames.some(frame=>frame.direction==='framesent'&&frame.data.includes('你好')));
    await page.keyboard.type("; printf 'BASH_PID_%s\\n' $$");
    await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('.xterm-accessibility-tree')?.textContent?.includes('UI_TERMINAL_OK'));
    await page.waitForFunction(()=>[...document.querySelectorAll('.xterm-rows > div')].some(row=>row.textContent?.trim()==='UTF8_你好'));
    const pasted=frames.filter(frame=>frame.direction==='framesent').map(frame=>JSON.parse(frame.data).d||'').join('');
    assert.equal(pasted.split('你好').length-1,1,'Clipboard shortcut must paste exactly once');
    await page.waitForFunction(()=>[...document.querySelectorAll('.xterm-rows span')].some(el=>el.textContent?.includes('UI_TERMINAL_OK')&&getComputedStyle(el).color==='rgb(248, 113, 113)'));
    await page.keyboard.type('sleep 30');await page.keyboard.press('Enter');await page.waitForTimeout(250);
    await page.keyboard.press('Control+C');
    await page.keyboard.type("printf 'UI_INTERRUPT_%s\\n' ok");await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('.xterm-accessibility-tree')?.textContent?.includes('UI_INTERRUPT_ok'));
    await page.screenshot({path:config.out+'/terminal-ui.png'});
    const sizes=()=>frames.filter(frame=>frame.direction==='framesent').map(frame=>JSON.parse(frame.data)).filter(m=>m.t==='r');
    const before=sizes().at(-1),beforeCount=sizes().length;
    await page.setViewportSize({width:1000,height:760});
    await page.getByRole('button',{name:'最大化',exact:true}).first().click();
    const until=Date.now()+3000;while(!(sizes().length>beforeCount&&(sizes().at(-1).c!==before.c||sizes().at(-1).r!==before.r))&&Date.now()<until)await new Promise(resolve=>setTimeout(resolve,20));
    const size=sizes().at(-1);assert(size.c!==before.c||size.r!==before.r);
    await page.locator('.xterm-helper-textarea').first().focus();await page.keyboard.type("printf 'UI_SIZE_%s\\n' \"$(stty size)\"");await page.keyboard.press('Enter');
    await page.waitForFunction(expected=>document.querySelector('.xterm-accessibility-tree')?.textContent?.includes(expected),`UI_SIZE_${size.r} ${size.c}`);
  });
  const text=await page.locator('.xterm-accessibility-tree').first().textContent();
  const pid=/BASH_PID_(\d+)/.exec(text)?.[1];assert(pid);
  await check('same-tab full reload resumes original Bash and bounded history',async()=>{
    await page.waitForTimeout(500);
    await page.reload({waitUntil:'domcontentloaded'});
    await page.locator('[data-testid="system-terminal"][data-status="open"]').waitFor({timeout:15000});
    await page.waitForFunction(()=>document.querySelector('.xterm-accessibility-tree')?.textContent?.includes('UI_TERMINAL_OK'));
    await page.locator('.xterm-helper-textarea').first().focus();await page.keyboard.type("printf 'REFRESH_PID_%s\\n' $$");await page.keyboard.press('Enter');
    await page.waitForFunction(expected=>document.querySelector('.xterm-accessibility-tree')?.textContent?.includes(expected),'REFRESH_PID_'+pid);
    await page.screenshot({path:config.out+'/terminal-reload.png'});
  });
  await check('new tab cannot reuse Cookie alone and new device revokes old terminal UI',async()=>{
    const other=await context.newPage();await other.goto(config.url);
    await other.getByPlaceholder('访问密码').waitFor();assert.equal(await other.locator('[data-testid="system-terminal"]').count(),0);
    const device=await browser.newContext();const newcomer=await device.newPage();await newcomer.goto(config.url);
    await newcomer.getByPlaceholder('访问密码').fill(config.password);
    await newcomer.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();
    await page.getByPlaceholder('访问密码').waitFor({timeout:15000});
    await other.close();await device.close();
  });
}finally{
  if(report.failed&&page&&!page.isClosed()){
    await page.screenshot({path:config.out+'/terminal-ui-failure.png'}).catch(()=>{});
    fs.writeFileSync(config.out+'/terminal-ui-dom.txt',await page.locator('body').innerText().catch(()=>''));
    fs.writeFileSync(config.out+'/terminal-ui-frames.json',JSON.stringify(frames,null,2));
  }
  fs.writeFileSync(config.out+'/terminal-ui.json',JSON.stringify({...report,total:report.passed+report.failed},null,2));
  await browser.close();
}
console.log(JSON.stringify(report));
