// Actual authenticated desktop -> noVNC -> visible sandboxed Chromium -> TLS.
// Root observes metadata/history only; it never writes a positive download.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
let input='';for await(const p of process.stdin)input+=p;const c=JSON.parse(input);
const browser=await chromium.launch({executablePath:c.chrome,headless:true,chromiumSandbox:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',acceptDownloads:true});
const page=await context.newPage();page.setDefaultTimeout(20000);
const planned=c.after_reboot?['actual VM reboot retains native Chromium bytes','profile and download permissions survive reboot']:[
 'visible Chromium HTTPS native download and Agent byte identity','native bookmark retained with download data','file manager preview rename and local device download','Agent copy move trash restore and ZIP preserve Chromium bytes',
 ...['chinese','spaces','unicode','repeat','long','special'].map(k=>'native filename '+k),
 ...['traversal','absolute','separator','nul'].map(k=>'native unsafe filename contained '+k),
 'symlink collision protects root secret','native script and desktop downloads never execute',
 'in-progress Chromium bytes are hidden and refused','live PTY and file API respond during native download','network interruption never publishes completed bytes',
 'native user cancellation','browser crash preserves completed data and recovers','GUI close during download and reopen',
 'actual volume space exhaustion and download recovery','actual inode exhaustion and download recovery',
 'directory replacement cannot redirect Agent into source','hardlink cannot export browser bytes through a second name',
 'real UID DAC and download mount boundaries','API double authentication and current session revocation',
 'refresh Agent backend restart and profile persistence'];
const report={kind:'visible-Chromium-to-Agent-to-local-device',passed:0,failed:0,skipped:0,not_executed:planned.length,tests:[],resources:[],native:[],controller_writes_positive_download:false};
const observer=(action,body,timeout=12000)=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 -I /root/download-bridge-vm.py '+action],{encoding:'utf8',input:body?JSON.stringify(body):undefined,timeout,stdio:['pipe','pipe','pipe']}));
const probe=topic=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 /root/staging-ui-probe.py '+topic],{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}));
const hash=b=>createHash('sha256').update(b).digest('hex');
const gui=()=>page.getByTestId('basic-gui'),canvas=()=>gui().locator('canvas');
let sid='',access='';page.on('websocket',s=>{const u=new URL(s.url());if(u.pathname==='/api/gui/ws'){sid=u.searchParams.get('sid');access=u.searchParams.get('access');}});
async function until(fn,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(200);}throw Error('Bounded observable state timeout');}
async function test(name,fn){assert(planned.includes(name));report.not_executed--;try{await fn();report.passed++;report.tests.push({name,result:'passed'});}catch(e){report.failed++;report.tests.push({name,result:'failed',detail:String(e.stack).split(c.password).join('[REDACTED]').slice(0,1800)});try{report.failureObservation={state:observer('state'),title:observer('title')};}catch{}await page.screenshot({path:c.out+(c.after_reboot?'/d1-reboot-failure-':'/d1-failure-')+report.failed+'.png'}).catch(()=>{});throw e;}}
async function login(p=page){await p.getByPlaceholder('访问密码').fill(c.password);await p.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await p.getByText('终端',{exact:true}).first().waitFor();}
async function api(url,method='GET',body){return page.evaluate(async({url,method,body})=>{const r=await fetch(url,{method,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const bytes=await r.arrayBuffer();return {status:r.status,text:new TextDecoder().decode(bytes),bytes:Array.from(new Uint8Array(bytes)),disposition:r.headers.get('Content-Disposition')};},{url,method,body});}
const path=name=>'~/Downloads/'+name;
const get=name=>api('/api/sys/download?path='+encodeURIComponent(path(name)));
const patch=b=>api('/api/sys/fs','PATCH',b);
async function verify(name,key){const s=observer('hash',{name}),ids=observer('state').accounts;assert.equal(s.size,c.expected[key].size);assert.equal(s.sha256,c.expected[key].sha256);assert.equal(s.mode,'0o660');assert.equal(s.uid,ids.wdgui.uid);assert.equal(s.gid,ids.wdguest.gid);const r=await get(name);assert.equal(r.status,200,r.text.slice(0,100));assert.equal(hash(Buffer.from(r.bytes)),s.sha256);return s;}
async function open(){await page.getByText('浏览器',{exact:true}).first().dblclick();await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:45000});await until(()=>probe('state').windows.some(w=>w.app==='chromium'&&w.native_new_tab),45000);}
async function close(){if(await gui().count()){await gui().locator('xpath=../..').getByRole('button',{name:'关闭',exact:true}).click();await until(()=>probe('idle').idle);}}
async function navigate(url){await canvas().click({position:{x:300,y:250}});await page.keyboard.press('Control+l');await page.waitForTimeout(250);await page.keyboard.type(url,{delay:8});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await until(()=>probe('address').sha256===hash(url));await page.keyboard.press('Enter');}
let visit=0;
async function nativeDownloadUI(key){
 await page.keyboard.press('Control+j');await until(()=>observer('title').some(t=>/[Dd]ownload|下载/.test(t)));
 await page.keyboard.press('Control+f');await page.keyboard.press('Control+a');await page.keyboard.type(c.names[key],{delay:10});
 let ui;await until(async()=>{await canvas().click({position:{x:50,y:400}});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');ui=observer('download-ui',{name:c.names[key],names:Object.values(c.names)});return ui.name_visible&&ui.other_names_visible.length===0;},10000);
 await page.screenshot({path:c.out+'/d1-native-'+key+'.png'});return ui;
}
async function download(key,{slow=false,allowInterrupted=false}={}){
 const initial=observer('state'),before=new Set(initial.history.map(x=>x.id)),names=new Set(initial.files.map(x=>x.name)),current=++visit,title='D1 Ready '+key+' '+current;
 await navigate('https://203.0.113.2/page/'+key+'?visit='+current);await until(()=>observer('title').some(t=>t===title||t.startsWith(title+' - ')));await page.keyboard.press('Enter');let row;
 let file,completed=false;
 try{await until(()=>{
  const s=observer('state');row=s.history.find(x=>!before.has(x.id));
  const fresh=s.files.filter(x=>!names.has(x.name)&&x.regular);
  if(slow){file=fresh.find(x=>x.name.endsWith('.crdownload')&&x.size>0);return Boolean(file);}
  file=fresh.find(x=>!x.name.endsWith('.crdownload')&&!x.name.startsWith('.org.chromium.')&&x.size===c.expected[key].size);
  if(file){const bytes=observer('hash',{name:file.name});completed=bytes.sha256===c.expected[key].sha256;if(completed)return true;}
  return allowInterrupted&&row&&row.state===3;
 },30000);}
 catch(error){
  if(!allowInterrupted)throw error;
  const ui=await nativeDownloadUI(key);report.native.push({case:key,kind:'native-ui-observation',ui});
  const expected=key==='symlink'?(ui.permission_failure||ui.failed):['script','desktop'].includes(key)?ui.security_review:['space','inodes'].includes(key)?ui.disk_full:key==='reset'?ui.network_failure:false;
  if(!expected)throw error;
  // A visible, named rejection is distinct from a complete file or a SQLite
  // state. Never invent a History row merely because a wait expired.
  return{kind:'native-ui-rejection',case:key,ui};
 }
 let result;
 if(slow)result={kind:'native-file-in-progress',current_path:'/home/wdguest/Downloads/'+file.name,before_ids:[...before],history:row??null};
 else if(completed)result={kind:'native-complete-file',complete:true,target_path:'/home/wdguest/Downloads/'+file.name,history:row??null};
 else{result=row;if(row.target_path)assert(row.target_path.startsWith('/home/wdguest/Downloads/'));}
 report.native.push({case:key,...result});report.resources.push({case:key,...observer('resources')});return result;
}
async function partial(started){const name=started.current_path.split('/').at(-1);let f;await until(()=>{f=observer('state').files.find(x=>x.name===name);return f&&f.size>0;});const first=f.size;await until(()=>observer('state').files.some(x=>x.name===name&&x.ino===f.ino&&x.size>first),8000);const list=JSON.parse((await api('/api/sys/fs?path=~/Downloads')).text);assert(!list.entries.some(x=>x.name===f.name));for(const endpoint of ['raw','download']){const r=await api('/api/sys/'+endpoint+'?path='+encodeURIComponent(path(f.name)));assert.equal(r.status,409);assert.equal(JSON.parse(r.text).code,'DOWNLOAD_INCOMPLETE');}return f;}
try{
 await page.goto(c.url,{waitUntil:'domcontentloaded'});await login();
 if(c.after_reboot){
  await test(planned[0],async()=>{await verify(c.names.normal,'normal');});
  await test(planned[1],async()=>{const s=observer('state');assert.equal(s.directory.mode,'0o2770');assert(s.history.some(x=>x.target_path.endsWith('/'+c.names.normal)&&x.state===1));const earlier=JSON.parse(fs.readFileSync(c.out+'/download-bridge.json','utf8'));assert(earlier.profile?.bookmark);assert.deepEqual(observer('profile'),earlier.profile);});
 }else{
  if(!await page.getByTestId('system-terminal').count())await page.getByTitle('终端',{exact:true}).last().click();
  await until(async()=>await page.getByTestId('system-terminal').getAttribute('data-status')==='open',20000);
  const terminalWindow=page.getByTestId('system-terminal').locator('xpath=../..');
  if(await terminalWindow.isVisible())await terminalWindow.getByRole('button',{name:'最小化',exact:true}).click();
  await open();
  await test(planned[0],async()=>{await download('normal');report.completed=await verify(c.names.normal,'normal');await page.screenshot({path:c.out+'/d1-real-https-completed.png'});});
  await test('native bookmark retained with download data',async()=>{await page.keyboard.press('Control+d');await page.keyboard.press('Enter');await until(()=>observer('profile').bookmark,12000);report.profile=observer('profile');});
  await close();
  await test('file manager preview rename and local device download',async()=>{
   await page.getByText('主目录',{exact:true}).first().dblclick();await page.getByText('下载',{exact:true}).last().click();await page.getByTestId('browser-download-hint').waitFor();
   await page.getByText(c.names.normal,{exact:true}).last().dblclick();await until(async()=>{const a=await page.locator('textarea').evaluateAll(es=>es.map(e=>e.value));return a.some(t=>t.includes('下载内容'));});
   await page.getByRole('button',{name:'关闭',exact:true}).last().click();
   await page.getByText(c.names.normal,{exact:true}).last().click({button:'right'});await page.locator('[data-ctxmenu]').getByRole('button',{name:'重命名',exact:false}).click();
   const dialog=page.locator('form').filter({has:page.getByRole('heading',{name:'重命名',exact:true})});await dialog.getByRole('textbox').fill('d1-renamed.txt');await dialog.getByRole('button',{name:'确定',exact:true}).click();await page.getByText('d1-renamed.txt',{exact:true}).last().waitFor();
   await page.getByText('d1-renamed.txt',{exact:true}).last().click({button:'right'});const event=page.waitForEvent('download');await page.locator('[data-ctxmenu]').getByRole('button',{name:'下载到电脑',exact:false}).click();const local=await event;await local.saveAs(c.out+'/d1-local-download.txt');assert.equal(hash(fs.readFileSync(c.out+'/d1-local-download.txt')),c.expected.normal.sha256);assert.equal(local.suggestedFilename(),'d1-renamed.txt');
   assert.equal((await patch({path:path('d1-renamed.txt'),name:c.names.normal})).status,200);await page.screenshot({path:c.out+'/d1-file-manager.png'});await page.getByRole('button',{name:'关闭',exact:true}).first().click();
  });
  await test('Agent copy move trash restore and ZIP preserve Chromium bytes',async()=>{
   const folder=await api('/api/sys/fs','POST',{dir:'~/Documents',name:'d1-managed',kind:'folder'});assert.equal(folder.status,200,folder.text);const dest=JSON.parse(folder.text).path;
   const copied=await patch({copyPaths:[path(c.names.normal)],destDir:dest});assert.equal(copied.status,200,copied.text);const p=JSON.parse(copied.text).paths[0];
   assert.equal((await patch({path:p,name:'renamed.txt'})).status,200);const moved=await patch({path:dest+'/renamed.txt',moveTo:'~/Pictures'});assert.equal(moved.status,200,moved.text);const m=JSON.parse(moved.text).path;
   const trashed=await api('/api/sys/fs?path='+encodeURIComponent(m),'DELETE');assert.equal(trashed.status,200);assert.equal((await patch({path:JSON.parse(trashed.text).path,restore:true})).status,200);
   const bytes=await api('/api/sys/download?path='+encodeURIComponent(m));assert.equal(hash(Buffer.from(bytes.bytes)),c.expected.normal.sha256);
   const zip=await api('/api/sys/download?path='+encodeURIComponent('~/Pictures'));assert.equal(zip.status,200);assert.match(zip.disposition,/\.zip/);fs.writeFileSync(c.out+'/d1-pictures.zip',Buffer.from(zip.bytes));
  });
  await open();
  for(const key of ['chinese','spaces','unicode','repeat','long','special'])await test('native filename '+key,async()=>{let r=await download(key);const name=r.target_path.split('/').at(-1);await verify(name,key);if(key==='repeat'){const second=await download(key);assert.notEqual(second.target_path,r.target_path);await verify(second.target_path.split('/').at(-1),key);}const b=await get(name);assert.match(b.disposition,/filename\*=UTF-8/i);});
  for(const key of ['traversal','absolute','separator','nul'])await test('native unsafe filename contained '+key,async()=>{const r=await download(key,{allowInterrupted:true});assert(!r.target_path.slice('/home/wdguest/Downloads/'.length).includes('/'));if(r.complete)await verify(r.target_path.split('/').at(-1),key);else assert.equal(r.state,3);});
  try{await test('symlink collision protects root secret',async()=>{observer('links');try{const r=await download('symlink',{allowInterrupted:true});assert.equal((await get(c.names.symlink)).status,403);if(r.complete){assert.notEqual(r.target_path,'/home/wdguest/Downloads/'+c.names.symlink);await verify(r.target_path.split('/').at(-1),'symlink');}else assert(r.state===3||r.kind==='native-ui-rejection');assert(observer('secret-check').preserved);}finally{observer('unlink-test-link');}});}catch{await page.keyboard.press('Escape');}
  try{await test('native script and desktop downloads never execute',async()=>{for(const k of ['script','desktop']){const r=await download(k,{allowInterrupted:true});if(r.complete)await verify(r.target_path.split('/').at(-1),k);else assert(r.state===3||r.kind==='native-ui-rejection');}assert(observer('state').files.filter(x=>[c.names.script,c.names.desktop].includes(x.name)).every(x=>x.mode==='0o660'));});}catch{await page.keyboard.press('Escape');}
  await test('in-progress Chromium bytes are hidden and refused',async()=>{const r=await download('slow',{slow:true});report.partial=await partial(r);await page.screenshot({path:c.out+'/d1-partial.png'});});
  await test('live PTY and file API respond during native download',async()=>{
   const started=Date.now();await page.getByTitle('终端',{exact:true}).last().click();await page.locator('.xterm-helper-textarea').first().focus();await page.keyboard.type("printf 'D1_%s\\n' PTY_ALIVE");await page.keyboard.press('Enter');await until(()=>page.locator('.xterm-accessibility-tree').first().textContent().then(t=>t.includes('D1_PTY_ALIVE')),10000);
   report.pty_response_ms=Date.now()-started;await page.getByTestId('system-terminal').locator('xpath=../..').getByRole('button',{name:'最小化',exact:true}).click();
   const at=Date.now();assert.equal((await api('/api/sys/fs?path=~/Downloads')).status,200);report.file_response_ms=Date.now()-at;assert.equal((await api('/api/resources')).status,200);
  });
  await test('network interruption never publishes completed bytes',async()=>{const r=await download('reset',{allowInterrupted:true});if(r.kind!=='native-ui-rejection'){assert.equal(r.state,3);assert.notEqual(r.interrupt_reason,0);}assert.equal((await get(c.names.reset)).status,404);});
  // Native Downloads WebUI, not SIGKILL or a privileged test control API.
  // Filter to one item, focus its action menu and choose the last visible
  // action (Cancel). The native state=2 oracle is mandatory; a wrong focus or
  // completed transfer is a test failure, never accepted as cancellation.
  try{await test('native user cancellation',async()=>{
   const started=await download('cancel',{slow:true});await page.keyboard.press('Control+j');await until(()=>observer('title').some(t=>/Downloads|下载/.test(t)));
   await page.keyboard.press('Control+f');await page.keyboard.type(c.names.cancel,{delay:20});await page.keyboard.press('Tab');await page.keyboard.press('Tab');await page.keyboard.press('Space');
   await page.screenshot({path:c.out+'/d1-native-cancel-menu.png'});await page.keyboard.press('ArrowUp');await page.keyboard.press('Enter');
   await until(()=>observer('state').history.some(x=>!started.before_ids.includes(x.id)&&x.target_path.endsWith(c.names.cancel)&&x.state===2),10000);
   assert.equal((await get(c.names.cancel)).status,404);await verify(c.names.normal,'normal');
  });}catch{await page.keyboard.press('Escape');}
  await test('browser crash preserves completed data and recovers',async()=>{const r=await download('crash',{slow:true});await partial(r);observer('crash');await close();await verify(c.names.normal,'normal');await open();await download('recover');await verify(c.names.recover,'recover');});
  await test('GUI close during download and reopen',async()=>{const r=await download('close',{slow:true});await partial(r);await close();await verify(c.names.normal,'normal');await open();await download('normal');await verify(c.names.normal,'normal');});
  for(const kind of ['space','inodes'])await test('actual '+(kind==='space'?'volume space':'inode')+' exhaustion and download recovery',async()=>{let fault;try{fault=observer(kind,undefined,70000);report[kind]=fault;assert(fault.enospc);const r=await download(kind,{allowInterrupted:true});if(r.kind!=='native-ui-rejection'){assert.equal(r.state,3);assert.notEqual(r.interrupt_reason,0);}assert.equal((await get(c.names[kind])).status,404);}finally{observer('clear-fault',undefined,70000);}await verify(c.names.normal,'normal');await download('recover');});
  await test('directory replacement cannot redirect Agent into source',async()=>{observer('directory-source');try{assert([400,403].includes((await api('/api/sys/fs?path=~/Downloads')).status));assert.equal((await get('package.json')).status,403);}finally{observer('restore-directory');}await verify(c.names.normal,'normal');});
  await test('hardlink cannot export browser bytes through a second name',async()=>{observer('hardlink');try{assert.equal((await get('d1-hardlink.txt')).status,403);assert.equal((await get(c.names.normal)).status,403);}finally{observer('unlink-hardlink');}await verify(c.names.normal,'normal');});
  await test('real UID DAC and download mount boundaries',async()=>{report.boundaries=observer('boundaries');for(const v of Object.values(report.boundaries))assert(Array.isArray(v)?v.every(Boolean):v===true,JSON.stringify(report.boundaries));const mount=observer('state').mounts;assert(mount.some(s=>s.includes('nosuid')&&s.includes('nodev')&&s.includes('noexec')));report.directoryReplacement=observer('replacement');});
  await test('API double authentication and current session revocation',async()=>{
   const target=c.url+'/api/sys/download?path='+encodeURIComponent(path(c.names.normal));const anon=await browser.newContext();assert.equal((await anon.request.get(target)).status(),401);await anon.close();
   assert.equal((await context.request.get(target)).status(),401);assert.equal((await context.request.get(target,{headers:{'X-Desktop-Session':'bad-page-session'}})).status(),401);
   const oldCookie=await context.cookies(),oldAccess=access,oldSid=sid;const other=await browser.newContext(),p=await other.newPage();await p.goto(c.url);await login(p);const status=await context.request.get(target,{headers:{'X-Desktop-Session':oldAccess}});assert.equal(status.status(),401);report.revoked=true;
   await p.close();await other.close();await page.goto(c.url);await login();await until(()=>probe('idle').idle,30000).catch(async()=>{if(await gui().count())await close();});assert(oldCookie.length>0&&oldSid);
  });
  await test('refresh Agent backend restart and profile persistence',async()=>{await close();await page.reload();await page.getByText('终端',{exact:true}).first().waitFor();await verify(c.names.normal,'normal');observer('restart-agent');await verify(c.names.normal,'normal');observer('restart-backend '+c.backend);await until(async()=>{try{return (await get(c.names.normal)).status===200;}catch{return false;}},45000);await verify(c.names.normal,'normal');const s=observer('state');assert(s.history.some(x=>x.target_path.endsWith('/'+c.names.normal)&&x.state===1));assert.deepEqual(observer('profile'),report.profile);});
 }
}catch(e){if(!report.failed){report.failed++;report.tests.push({name:'D1 harness/setup',result:'failed',detail:String(e.stack).split(c.password).join('[REDACTED]').slice(0,1600)});}}
finally{
 await close().catch(()=>{});
 if(await page.getByTestId('system-terminal').count()){
  const window=page.getByTestId('system-terminal').locator('xpath=../..');
  if(!await window.isVisible())await page.getByTitle('终端',{exact:true}).last().click().catch(()=>{});
  await window.getByRole('button',{name:'关闭',exact:true}).click().catch(()=>{});
 }
 try{await until(()=>observer('terminal-idle').idle);report.terminal_cleanup=observer('terminal-idle');}
 catch{report.failed++;report.tests.push({name:'terminal cleanup before health handoff',result:'failed'});}
 fs.writeFileSync(c.out+(c.after_reboot?'/download-reboot.json':'/download-bridge.json'),JSON.stringify(report,null,2));await browser.close();
}
console.log(JSON.stringify({kind:report.kind,passed:report.passed,failed:report.failed,skipped:report.skipped,not_executed:report.not_executed}));if(report.failed)process.exitCode=1;
