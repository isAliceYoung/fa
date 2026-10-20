// Real host-browser -> temporary HTTPS proxy -> SSH forward -> public gateway
// -> noVNC -> visible guest Chromium. No CDP is enabled in the guest browser.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import https from 'node:https';
import http from 'node:http';
import net from 'node:net';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
let raw='';for await(const b of process.stdin)raw+=b;const c=JSON.parse(raw);
const key=c.out+'/tls.key',cert=c.out+'/tls.crt';execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1','-keyout',key,'-out',cert],{stdio:'ignore'});
const proxy=https.createServer({key:fs.readFileSync(key),cert:fs.readFileSync(cert)},(q,r)=>{const up=http.request({hostname:'127.0.0.1',port:18890,path:q.url,method:q.method,headers:{...q.headers,'x-forwarded-proto':'http','x-webdesktop-https':'0'}},s=>{r.writeHead(s.statusCode,s.headers);s.pipe(r);});up.on('error',()=>r.destroy());q.pipe(up);});
const peers=new Set();proxy.on('connection',s=>{peers.add(s);s.on('error',()=>{});s.on('close',()=>peers.delete(s));});
proxy.on('upgrade',(q,s,head)=>{const up=net.connect(18890,'127.0.0.1');peers.add(up);up.on('error',()=>s.destroy());s.on('error',()=>up.destroy());s.on('close',()=>up.destroy());up.on('close',()=>{peers.delete(up);s.destroy();});up.once('connect',()=>{up.write('GET '+q.url+' HTTP/1.1\r\n'+q.rawHeaders.reduce((x,v,i)=>x+(i%2?v+'\r\n':v+': '),'')+'\r\n');if(head.length)up.write(head);s.pipe(up).pipe(s);});});
await new Promise(r=>proxy.listen(18443,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:c.chrome,headless:true,chromiumSandbox:true});
const report={passed:0,failed:0,skipped:0,not_executed:5,checks:[]};let page,io;
function capture(topic){return JSON.parse(execFileSync('ssh',[...c.ssh,'sudo /usr/bin/python3 /root/wd-lab/tests/desktop_capture.py '+topic],{encoding:'utf8',timeout:20000,stdio:['ignore','pipe','pipe']}));}
async function check(name,fn){report.not_executed--;try{await fn();report.passed++;report.checks.push({name,result:'passed'});}catch(e){report.failed++;report.checks.push({name,result:'failed',error:String(e.stack).slice(0,2500)});throw e;}}
async function open(){await page.getByText('浏览器',{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:30000});await page.waitForTimeout(1800);await page.locator('[data-testid="basic-gui"] canvas').click({position:{x:300,y:200}});}
async function navigate(url){await page.keyboard.press('Control+l');await page.keyboard.type(url);await page.keyboard.press('Enter');}
async function close(){await page.getByRole('button',{name:'关闭',exact:true}).first().click();await page.waitForTimeout(2500);}
try{
 // Self-signed fixture only. Production template uses a normal trusted cert.
 const context=await browser.newContext({viewport:{width:1280,height:900},locale:'zh-CN',ignoreHTTPSErrors:true});page=await context.newPage();await page.goto('https://127.0.0.1:18443');await page.getByPlaceholder('访问密码').fill(c.password);await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();
 await check('HTTPS password login Secure HttpOnly cookie and live visible Chromium over WSS',async()=>{
  assert((await context.cookies()).some(x=>x.name==='wd_session'&&x.secure&&x.httpOnly&&x.sameSite==='Lax'));
  await page.getByText('终端',{exact:true}).first().dblclick();await page.locator('[data-testid="system-terminal"][data-status="open"]').waitFor({timeout:15000});await page.locator('.xterm-helper-textarea').first().focus();await page.keyboard.type('python3 -c "import time; x=bytearray(24*1024*1024); print(\'GUI_CONCURRENT\'); time.sleep(180)"');await page.keyboard.press('Enter');
  report.before=capture('before');await open();await navigate('https://example.com');await page.waitForTimeout(4000);
  // Copy stays entirely inside the guest X11 selection; no browser/host
  // clipboard bridge or VNC CutText is enabled.
  await page.locator('[data-testid="basic-gui"] canvas').click({position:{x:350,y:330}});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await page.waitForTimeout(300);
  const proof=capture('external');assert.match(proof.clipboard,/domain|域名|example/i);report.external=proof;
  await page.screenshot({path:c.out+'/chromium-https-visible.png'});
 });
 await check('real pinyin candidates digit choice English switch and Chinese browser text input',async()=>{
  await navigate('data:text/html,'+encodeURIComponent('<html><body style="font-size:32px"><h1>Visitor IME</h1><input autofocus style="font-size:36px;width:90%"></body></html>'));await page.waitForTimeout(1200);
  await page.keyboard.press('Control+Space');await page.keyboard.type('nihao');await page.waitForTimeout(700);await page.screenshot({path:c.out+'/chromium-pinyin-candidates.png'});await page.keyboard.type('1');await page.keyboard.press('Control+Space');await page.keyboard.type('hello');await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await page.waitForTimeout(300);
  const proof=capture('browser-ime');assert.equal(proof.clipboard,'你好hello');report.browser_ime=proof;await page.screenshot({path:c.out+'/chromium-chinese-input.png'});
 });
 await check('profile persistence close/reopen and concurrent authenticated file operations',async()=>{
  await navigate('https://example.com');await page.waitForTimeout(2000);await page.keyboard.press('Control+d');await page.waitForTimeout(400);await page.keyboard.press('Enter');await page.waitForTimeout(500);assert(capture('profile').bookmark_exists);
  const lat=[];for(let i=0;i<15;i++){const start=Date.now();const status=await page.evaluate(async()=>{const r=await fetch('/api/sys/fs?path=~/Documents');return r.status;});assert.equal(status,200);lat.push(Date.now()-start);await page.waitForTimeout(1000);}report.file_latency_ms=lat;
  await close();const closed=capture('closed');assert.equal(closed.gui_pids,'');report.closed=closed;await open();assert(capture('reopened').bookmark_exists);await close();await open();await page.screenshot({path:c.out+'/chromium-reopened.png'});
 });
 await check('page refresh replaces old GUI safely and preserves profile after authenticated reopen',async()=>{
  const old=capture('reopened').gui_pids.split('\n').filter(Boolean);await page.reload();await page.waitForTimeout(2500);
  if(await page.getByPlaceholder('访问密码').count()){await page.getByPlaceholder('访问密码').fill(c.password);await page.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();}
  if(await page.getByTestId('basic-gui').count()){
   if(await page.getByRole('button',{name:'重新打开',exact:true}).count())await page.getByRole('button',{name:'重新打开',exact:true}).click();
   await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:30000});
  }else await open();const fresh=capture('reopened');assert(fresh.bookmark_exists);assert(!fresh.gui_pids.split('\n').some(pid=>old.includes(pid)));
 });
 await check('another HTTPS device login revokes live graphical and PTY processes',async()=>{
  const other=await browser.newContext({ignoreHTTPSErrors:true});const newcomer=await other.newPage();await newcomer.goto('https://127.0.0.1:18443');await newcomer.getByPlaceholder('访问密码').fill(c.password);await newcomer.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await page.getByPlaceholder('访问密码').waitFor({timeout:15000});await page.waitForTimeout(1800);assert.equal(capture('revoked').gui_pids,'');await other.close();
 });
}finally{
 if(report.failed&&page&&!page.isClosed())await page.screenshot({path:c.out+'/desktop-ui-failure.png'}).catch(()=>{});
 fs.writeFileSync(c.out+'/desktop-ui.json',JSON.stringify(report,null,2));await browser.close();for(const s of peers)s.destroy();await new Promise(r=>proxy.close(r));
 // The test TLS private key is never an artifact.
 fs.unlinkSync(key);
}
console.log(JSON.stringify({passed:report.passed,failed:report.failed,skipped:0,not_executed:report.not_executed}));
