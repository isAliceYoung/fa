// Actual authenticated desktop -> noVNC -> visible sandboxed Chromium -> TLS.
// Root observes metadata/history only; it never writes a positive download.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
import {matchHistory,rejectedUI} from './download_observation.mjs';
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
// R2 diagnostic subset: precondition download plus exactly one named case in a
// fresh VM. Ordinary regression runs (no c.only) keep the complete plan.
const only=c.only?(Array.isArray(c.only)?c.only:[c.only]):null;
if(only){for(const name of only)assert(planned.includes(name),'Unknown isolated case');planned.splice(0,planned.length,...planned.filter((n,i)=>i===0||only.includes(n)));}
const report={kind:'visible-Chromium-to-Agent-to-local-device',passed:0,failed:0,skipped:0,not_executed:planned.length,tests:[],resources:[],native:[],timeline:[],watches:[],controller_writes_positive_download:false,...(only?{isolated_cases:only}:{})};
const trace=(event,extra={})=>report.timeline.push({t:Date.now(),event,...extra});
// Read-only pixel evidence from the host noVNC canvas (no VM input). Region is
// the Chromium content area below its toolbar, left of the profile bubble.
async function paint(){try{return await canvas().evaluate(e=>{const x=e.getContext('2d');if(!x)return null;const W=e.width,H=e.height,y0=Math.round(H*.17),y1=Math.round(H*.98),x1=Math.round(W*.70);const d=x.getImageData(0,y0,x1,y1-y0).data;let n=0,t=0;for(let i=0;i<d.length;i+=4*7){t++;if(Math.min(d[i],d[i+1],d[i+2])<235)n++;}return {ratio:Math.round(n/t*1e4)/1e4,w:W,h:H};});}catch{return null;}}
let diagSeq=0;const diag=since=>{try{const r=observer('diag',{since},30000);report.diagnostics??=[];report.diagnostics.push({seq:++diagSeq,t:Date.now(),...r});return diagSeq;}catch(e){trace('diag-error',{detail:String(e).slice(0,200)});return null;}};
async function watchAfterFailure(name){
 // Diagnostic-only, bounded observation of whether the native page ever paints.
 const started=Date.now(),samples=[];let painted=null;
 diag(started/1000-90);
 for(let i=0;Date.now()-started<40000;i++){const v=await paint();samples.push({ms:Date.now()-started,ratio:v?.ratio??null});if(painted===null&&v?.ratio>=.05)painted=Date.now()-started;
  if(i%5===0)await page.screenshot({path:c.out+'/d1-watch-'+report.failed+'-'+String(Math.round((Date.now()-started)/1000)).padStart(2,'0')+'s.png'}).catch(()=>{});
  await page.waitForTimeout(2000);}
 let resource=null;try{const r=await api('/api/resources');resource={status:r.status,level:JSON.parse(r.text).status?.level,reasons:JSON.parse(r.text).status?.reasons};}catch{}
 // Layer evidence for a still-white page: private X11 framebuffer (vs noVNC),
 // DOM presence (one-time selection marker), then user-like remedies.
 const layers={host_ratio:(await paint())?.ratio??null};
 try{layers.x11=observer('xwd');}catch(e){layers.x11={error:String(e).slice(0,160)};}
 try{const marker='D1-selection-'+randomBytes(12).toString('hex');observer('download-ui',{name:'Download history',names:[],marker});
  await canvas().click({position:{x:300,y:400}});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');await page.waitForTimeout(1500);
  const ui=observer('download-ui',{name:'Download history',names:[],marker_check:marker});layers.dom={copied:!ui.selection_marker,excerpt:ui.fixture_excerpt.slice(0,120)};}catch(e){layers.dom={error:String(e).slice(0,160)};}
 const after=async ms=>{const e=Date.now()+ms;while(Date.now()<e){const v=await paint();if(v?.ratio>=.05)return true;await page.waitForTimeout(200);}return false;};
 if(painted===null){
  layers.remedies=[];await page.mouse.move(420,520,{steps:8});layers.remedies.push({step:'mouse-move',painted:await after(3000)});
  if(!layers.remedies.at(-1).painted){await page.keyboard.press('Control+Tab');await page.waitForTimeout(600);await page.keyboard.press('Control+Shift+Tab');layers.remedies.push({step:'tab-switch',painted:await after(3000)});}
  if(!layers.remedies.at(-1).painted){await page.keyboard.press('F5');layers.remedies.push({step:'reload',painted:await after(6000)});}
  try{layers.x11_after=observer('xwd');}catch(e){layers.x11_after={error:String(e).slice(0,160)};}
  await page.screenshot({path:c.out+'/d1-watch-'+report.failed+'-remedied.png'}).catch(()=>{});
 }
 report.watches.push({after:name,failure:report.failed,painted_after_ms:painted,samples,resource,layers,diag:diag(started/1000-5)});
}
const observer=(action,body,timeout=12000)=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 -I /root/download-bridge-vm.py '+action],{encoding:'utf8',input:body?JSON.stringify(body):undefined,timeout,stdio:['pipe','pipe','pipe']}));
const probe=topic=>JSON.parse(execFileSync('ssh',[...c.ssh,'sudo python3 /root/staging-ui-probe.py '+topic],{encoding:'utf8',timeout:12000,stdio:['ignore','pipe','pipe']}));
const hash=b=>createHash('sha256').update(b).digest('hex');
const gui=()=>page.getByTestId('basic-gui'),canvas=()=>gui().locator('canvas');
let sid='',access='';page.on('websocket',s=>{const u=new URL(s.url());if(u.pathname==='/api/gui/ws'){sid=u.searchParams.get('sid');access=u.searchParams.get('access');}});
async function until(fn,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await page.waitForTimeout(200);}throw Error('Bounded observable state timeout');}
async function test(name,fn){if(only&&!planned.includes(name))return;assert(planned.includes(name));report.not_executed--;trace('test-start',{name});if(c.diagnose)diag();try{await fn();report.passed++;report.tests.push({name,result:'passed'});trace('test-passed',{name});}catch(e){report.failed++;trace('test-failed',{name,detail:String(e.message).slice(0,200)});if(c.diagnose)await watchAfterFailure(name);report.tests.push({name,result:'failed',detail:String(e.stack).split(c.password).join('[REDACTED]').slice(0,1800)});try{report.failureObservation={state:observer('state'),title:observer('title')};}catch{}await page.screenshot({path:c.out+(c.after_reboot?'/d1-reboot-failure-':'/d1-failure-')+report.failed+'.png'}).catch(()=>{});throw e;}}
async function independent(name,fn){
 if(only&&!planned.includes(name))return;
 try{await test(name,fn);}catch{
  trace('recovery-start',{after:name});
  // The failing assertion remains failed. Recover only a known GUI slot and
  // verify the canonical file before another stateful browser case may run.
  try{await page.keyboard.press('Escape');await close({allowFailed:true});await verify(c.names.normal,'normal');await resourcesNormal();await open();}
  catch(error){report.recovery_failure={after:name,detail:String(error.stack).split(c.password).join('[REDACTED]').slice(0,1800)};throw error;}
 }
}
// The gateway intentionally refuses a new GUI while the aggregate level is not
// normal (e.g. 30 s hysteresis after a workspace fault). Wait, bounded, for the
// real API to report normal; never bypass or retry the refused start blindly.
async function resourcesNormal(limit=120000){const start=Date.now();let level=null;
 await until(async()=>{try{const r=await api('/api/resources');level=r.status===200?JSON.parse(r.text).status?.level:'http-'+r.status;}catch{level='unavailable';}return level==='normal';},limit)
  .catch(e=>{trace('resources-not-normal',{level});throw e;});
 (report.resource_waits??=[]).push({ms:Date.now()-start});}
async function login(p=page){await p.getByPlaceholder('访问密码').fill(c.password);await p.getByRole('button',{name:'解锁并接管桌面',exact:true}).click();await p.getByText('终端',{exact:true}).first().waitFor();}
async function api(url,method='GET',body){return page.evaluate(async({url,method,body})=>{const r=await fetch(url,{method,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const bytes=await r.arrayBuffer();return {status:r.status,text:new TextDecoder().decode(bytes),bytes:Array.from(new Uint8Array(bytes)),disposition:r.headers.get('Content-Disposition')};},{url,method,body});}
const path=name=>'~/Downloads/'+name;
const get=name=>api('/api/sys/download?path='+encodeURIComponent(path(name)));
const patch=b=>api('/api/sys/fs','PATCH',b);
async function verify(name,key){const s=observer('hash',{name}),ids=observer('state').accounts;assert.equal(s.size,c.expected[key].size);assert.equal(s.sha256,c.expected[key].sha256);assert.equal(s.mode,'0o660');assert.equal(s.uid,ids.wdgui.uid);assert.equal(s.gid,ids.wdguest.gid);const r=await get(name);assert.equal(r.status,200,r.text.slice(0,100));assert.equal(hash(Buffer.from(r.bytes)),s.sha256);return s;}
async function open(){trace('open-request');await page.getByText('浏览器',{exact:true}).first().dblclick();try{await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({timeout:45000});}catch(e){const g=gui();trace('open-timeout',{status:await g.getAttribute('data-status').catch(()=>null),text:(await g.textContent().catch(()=>'')).slice(0,160)});throw e;}trace('open-ready');await until(()=>probe('state').windows.some(w=>w.app==='chromium'&&w.native_new_tab),45000);}
async function close({allowFailed=false}={}){if(await gui().count()){await gui().locator('xpath=../..').getByRole('button',{name:'关闭',exact:true}).click();}let state;await until(()=>{state=observer('execution-idle');return state.idle;});if(!allowFailed)for(const unit of Object.values(state.units))assert.equal(unit.ActiveState,'inactive');return state;}
async function navigate(url){
 // On 1 vCPU the omnibox can take focus after typing began and drop leading
 // characters (R2: "://203.0.113.2/..."). Re-enter the whole address, at most
 // three bounded times; Enter is only pressed after the exact URL is copied.
 for(let attempt=1;;attempt++){
  // Focus via the omnibox itself, never page content: on 1 vCPU a late click
  // on a Downloads card focused its link button after typing and took focus
  // back from the omnibox, so Enter copied a link instead (R2-C evidence).
  await canvas().click({position:{x:450,y:63}});await page.keyboard.press('Control+l');await page.waitForTimeout(250);await page.keyboard.press('Control+a');
  await page.keyboard.type(url,{delay:8});await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');
  try{await until(()=>probe('address').sha256===hash(url),10000);break;}
  catch(error){trace('address-reentry',{attempt});if(attempt>=3)throw error;await page.keyboard.press('Escape');}
 }
 await page.keyboard.press('Enter');}
let visit=0;
const tasks=new Map();
async function nativeDownloadUI(key){
 // Escape closes a stray overlay on the CURRENT page only. R2 proved that the
 // former Escape right after the Downloads title appeared stops the native
 // WebUI load on 1 vCPU (title set, module scripts aborted): an empty DOM and
 // a permanently white tab that only a reload repaints. Never send it there.
 await page.keyboard.press('Escape');
 trace('ctrl-j',{key,paint:await paint()});
 await page.keyboard.press('Control+j');await until(()=>observer('title').some(t=>/[Dd]ownload|下载/.test(t)));trace('downloads-title',{key,paint:await paint()});
 // Each card has its own Shadow DOM selection. Select the latest native card
 // explicitly. The exact fixture name must match; unrelated/multiple cards
 // remain rejected.
 await page.keyboard.press('Control+Home');
 const geometry=await canvas().evaluate(e=>({w:e.width,h:e.height,css:e.getBoundingClientRect().width}));assert(geometry.w>=800);
 const request={name:c.names[key],names:Object.values(c.names)},marker='D1-selection-'+randomBytes(12).toString('hex');
 let ui;await until(async()=>{
  observer('download-ui',{...request,marker});trace('selection-attempt',{key,paint:await paint()});
  // Anchor selection in the name text, not blank card padding: replacing an
  // in-progress card with Canceled can remove the previous text selection.
  // These paths observe failed/in-progress/canceled fixtures, never open a
  // completed executable download.
  await canvas().click({position:{x:(geometry.w/2-180)*geometry.css/geometry.w,y:252*geometry.css/geometry.w},clickCount:3});
  await page.keyboard.press('Control+a');await page.keyboard.press('Control+c');
  // A selection not yet replaced (page still loading) is retried within the
  // outer bound instead of aborting it; the marker check itself is unchanged.
  try{await until(()=>{ui=observer('download-ui',{...request,marker_check:marker});return !ui.selection_marker;},2000);}catch{return false;}
  report.last_native_ui={key,task:tasks.get(key),geometry,ui};return ui.name_visible&&ui.other_names_visible.length===0;
 },20000).catch(async error=>{trace('native-ui-unobserved',{key,paint:await paint()});throw new Error('Native Downloads card for '+key+' not observed: '+error.message);});
 await page.screenshot({path:c.out+'/d1-native-'+key+'.png'});return ui;
}
async function download(key,{slow=false,allowInterrupted=false}={}){trace('download-start',{key});
 const initial=observer('state'),names=new Set(initial.files.map(x=>x.name)),current=++visit,title='D1 Ready '+key+' '+current;
 const token=randomBytes(16).toString('hex'),task={key,token,url:'https://203.0.113.2/file/'+key+'?task='+token,beforeIds:initial.history.map(x=>x.id)};tasks.set(key,task);
 await navigate('https://203.0.113.2/page/'+key+'?visit='+current+'&task='+token);trace('navigated',{key,paint:await paint()});await until(()=>observer('title').some(t=>t===title||t.startsWith(title+' - ')));trace('page-ready',{key});await page.keyboard.press('Enter');let row;
 let file,completed=false;
 try{await until(()=>{
  const s=observer('state');row=matchHistory(s.history,task,['traversal','absolute','separator','nul','repeat'].includes(key)?undefined:c.names[key]);
  const fresh=s.files.filter(x=>!names.has(x.name)&&x.regular);
  if(slow){file=fresh.find(x=>x.name.endsWith('.crdownload')&&x.size>0);return Boolean(file);}
  file=fresh.find(x=>!x.name.endsWith('.crdownload')&&!x.name.startsWith('.org.chromium.')&&x.size===c.expected[key].size);
  if(file){const bytes=observer('hash',{name:file.name});completed=bytes.sha256===c.expected[key].sha256;if(completed)return true;}
  return allowInterrupted&&row&&row.state===3;
 },30000);}
 catch(error){
  if(!allowInterrupted)throw error;
  const ui=await nativeDownloadUI(key);report.native.push({case:key,task,kind:'native-ui-observation',ui});
  const expected=rejectedUI(key,ui)||(['script','desktop'].includes(key)&&ui.name_visible&&!ui.other_names_visible.length&&ui.security_review);
  if(!expected)throw error;
  // A visible, named rejection is distinct from a complete file or a SQLite
  // state. Never invent a History row merely because a wait expired.
  return{kind:'native-ui-rejection',case:key,task,ui,history:null,history_status:'no-trustworthy-committed-record'};
 }
 let result;
 if(slow)result={kind:'native-file-in-progress',current_path:'/home/wdguest/Downloads/'+file.name,task,history:row??null};
 else if(completed)result={kind:'native-complete-file',complete:true,target_path:'/home/wdguest/Downloads/'+file.name,task,history:matchHistory(observer('state').history,task,file.name)};
 else{result={...row,task,kind:'trusted-task-history'};if(row.target_path)assert(row.target_path.startsWith('/home/wdguest/Downloads/'));}
 if('history'in result)result.history_status=result.history?'trusted-task-record':'no-trustworthy-committed-record';
 report.native.push({case:key,...result});report.resources.push({case:key,...observer('resources')});return result;
}
async function unpublished(key){
 const state=observer('state'),responses={};
 assert(!state.files.some(f=>f.name===c.names[key]),'Failed download has a final pathname');
 const list=await api('/api/sys/fs?path=~/Downloads');assert.equal(list.status,200);
 assert(!JSON.parse(list.text).entries.some(f=>f.name===c.names[key]));
 for(const route of ['raw','download']){const r=await api('/api/sys/'+route+'?path='+encodeURIComponent(path(c.names[key])));responses[route]=r.status;assert.equal(r.status,404);}
 report.native.push({case:key,kind:'not-published',task:tasks.get(key),responses});
}
function origin(key,outcome){const task=tasks.get(key);assert(task);const evidence=observer('origin-evidence',{task:task.token});report.native.push({case:key,kind:'origin',...evidence});assert(evidence.records.some(x=>x.event==='request'&&x.case===key&&x.chromium));assert(evidence.records.some(x=>x.event==='transfer'&&x.case===key&&x.outcome===outcome&&(outcome!=='peer-closed'||x.bytes<8*1024**2)));return evidence;}
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
  await test('native bookmark retained with download data',async()=>{await page.keyboard.press('Control+d');await page.keyboard.press('Enter');await until(()=>observer('profile').bookmark,12000);report.profile=observer('profile');
   // First ordinary close with no preceding crash: the private display must
   // outlive Chromium so it exits cleanly ("Restore pages" otherwise). After
   // the deliberate SIGKILL case Chromium keeps Crashed until the restore
   // prompt is acknowledged, so that later value is recorded, not asserted.
   await close();const exit=observer('diag').profile.exit_type;report.first_close_exit_type=exit;assert(['Normal','SessionEnded'].includes(exit),'Chromium did not exit cleanly on GUI close: '+exit);});
  if(!only)await close();
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
  if(!only)await open();
  for(const key of ['chinese','spaces','unicode','repeat','long','special'])await test('native filename '+key,async()=>{let r=await download(key);const name=r.target_path.split('/').at(-1);await verify(name,key);if(key==='repeat'){const second=await download(key);assert.notEqual(second.target_path,r.target_path);await verify(second.target_path.split('/').at(-1),key);}const b=await get(name);assert.match(b.disposition,/filename\*=UTF-8/i);});
  for(const key of ['traversal','absolute','separator','nul'])await test('native unsafe filename contained '+key,async()=>{const r=await download(key,{allowInterrupted:true});assert(!r.target_path.slice('/home/wdguest/Downloads/'.length).includes('/'));if(r.complete)await verify(r.target_path.split('/').at(-1),key);else assert.equal(r.state,3);});
  if(!only||planned.includes('symlink collision protects root secret'))try{await test('symlink collision protects root secret',async()=>{observer('links');try{const r=await download('symlink',{allowInterrupted:true});assert.equal((await get(c.names.symlink)).status,403);if(r.complete){assert.notEqual(r.target_path,'/home/wdguest/Downloads/'+c.names.symlink);await verify(r.target_path.split('/').at(-1),'symlink');}else assert(r.state===3||r.kind==='native-ui-rejection');assert(observer('secret-check').preserved);}finally{observer('unlink-test-link');}});}catch{await page.keyboard.press('Escape');}
  if(!only||planned.includes('native script and desktop downloads never execute'))try{await test('native script and desktop downloads never execute',async()=>{const before=observer('execution-check');assert(before.not_executed&&before.guest_sentinel_writable);for(const k of ['script','desktop']){const r=await download(k,{allowInterrupted:true});if(r.complete)await verify(r.target_path.split('/').at(-1),k);else assert(r.state===3||r.kind==='native-ui-rejection');}assert(observer('state').files.filter(x=>[c.names.script,c.names.desktop].includes(x.name)).every(x=>x.mode==='0o660'));report.execution_check=observer('execution-check');assert(report.execution_check.not_executed&&report.execution_check.guest_sentinel_writable);});}catch{await page.keyboard.press('Escape');}
  await test('in-progress Chromium bytes are hidden and refused',async()=>{const r=await download('slow',{slow:true});report.partial=await partial(r);await page.screenshot({path:c.out+'/d1-partial.png'});});
  await test('live PTY and file API respond during native download',async()=>{
   const started=Date.now();await page.getByTitle('终端',{exact:true}).last().click();await page.locator('.xterm-helper-textarea').first().focus();await page.keyboard.type("printf 'D1_%s\\n' PTY_ALIVE");await page.keyboard.press('Enter');await until(()=>page.locator('.xterm-accessibility-tree').first().textContent().then(t=>t.includes('D1_PTY_ALIVE')),10000);
   report.pty_response_ms=Date.now()-started;await page.getByTestId('system-terminal').locator('xpath=../..').getByRole('button',{name:'最小化',exact:true}).click();
   const at=Date.now();assert.equal((await api('/api/sys/fs?path=~/Downloads')).status,200);report.file_response_ms=Date.now()-at;assert.equal((await api('/api/resources')).status,200);
  });
  await independent('network interruption never publishes completed bytes',async()=>{try{const r=await download('reset',{allowInterrupted:true});if(r.kind!=='native-ui-rejection'){assert.equal(r.state,3);assert.notEqual(r.interrupt_reason,0);}}finally{try{origin('reset','intentional-network-cut');}finally{await unpublished('reset');}}});
  // Native Downloads WebUI, never SIGKILL or an injected network error.
  // Require named Canceled UI, actual early peer closure, no final file and
  // state=2 if a trusted History row has actually committed.
  await independent('native user cancellation',async()=>{
   const started=await download('cancel',{slow:true});await nativeDownloadUI('cancel');
   // Latest unfiltered card's native action menu, through the actual VNC
   // mouse. Require Canceled UI + same-task early peer closure.
   const geometry=await canvas().evaluate(e=>({w:e.width,h:e.height,css:e.getBoundingClientRect().width}));assert(geometry.w>=800);
   // On 1 vCPU the native action menu can open after keys were sent; a lost
   // ArrowDown then selects its default first item (R2: "Paused"). Wait,
   // bounded, until the menu has actually been drawn below the button.
   const menuArea=()=>canvas().evaluate((e,g)=>{const x=e.getContext('2d'),x0=Math.round(g.w/2+120),y0=262,d=x.getImageData(x0,y0,Math.round(g.w/2+330)-x0,180).data;let n=0;for(let i=0;i<d.length;i+=4*5)if(Math.min(d[i],d[i+1],d[i+2])<200)n++;return n;},geometry);
   const closedMenu=await menuArea();
   await canvas().click({position:{x:(geometry.w/2+312)*geometry.css/geometry.w,y:252*geometry.css/geometry.w}});
   await until(async()=>(await menuArea())>closedMenu+40,5000);await page.waitForTimeout(300);
   await page.screenshot({path:c.out+'/d1-native-cancel-menu.png'});
   await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
   try{
    const ui=await nativeDownloadUI('cancel');report.native.push({case:'cancel',kind:'native-cancel',ui});assert(ui.canceled&&!ui.failed);
    const row=matchHistory(observer('state').history,started.task,c.names.cancel);if(row)assert.equal(row.state,2);
    report.cancel_history={status:row?'trusted':'no-trustworthy-committed-record',row};
   }finally{try{origin('cancel','peer-closed');}finally{await unpublished('cancel');}}
   await verify(c.names.normal,'normal');
  });
  await independent('browser crash preserves completed data and recovers',async()=>{const r=await download('crash',{slow:true});await partial(r);observer('crash');report.crash_cleanup=await close({allowFailed:true});assert.equal(report.crash_cleanup.units['webdesktop-browser@1.service'].Result,'signal');for(const name of ['webdesktop-gui@1.service','webdesktop-gui-lease@1.service'])assert.equal(report.crash_cleanup.units[name].ActiveState,'inactive');await unpublished('crash');await verify(c.names.normal,'normal');await open();await download('recover');await verify(c.names.recover,'recover');});
  await independent('GUI close during download and reopen',async()=>{const r=await download('close',{slow:true});await partial(r);await close();
   // A normal close keeps the private display alive until Chromium exits on
   // its own; otherwise it aborts and every next start offers "Restore pages".
   report.close_exit_type=observer('diag').profile.exit_type;await unpublished('close');await verify(c.names.normal,'normal');await open();await download('normal');await verify(c.names.normal,'normal');});
  for(const kind of ['space','inodes'])await independent('actual '+(kind==='space'?'volume space':'inode')+' exhaustion and download recovery',async()=>{let fault;try{fault=observer(kind,undefined,70000);report[kind]=fault;assert(fault.enospc);try{const r=await download(kind,{allowInterrupted:true});if(r.kind!=='native-ui-rejection'){assert.equal(r.state,3);assert.notEqual(r.interrupt_reason,0);}}finally{await unpublished(kind);}}finally{report[kind+'_clear']=observer('clear-fault',undefined,70000);}await verify(c.names.normal,'normal');await download('recover');});
  await independent('directory replacement cannot redirect Agent into source',async()=>{observer('directory-source');try{assert([400,403].includes((await api('/api/sys/fs?path=~/Downloads')).status));assert.equal((await get('package.json')).status,403);}finally{observer('restore-directory');}await verify(c.names.normal,'normal');});
  await independent('hardlink cannot export browser bytes through a second name',async()=>{observer('hardlink');try{assert.equal((await get('d1-hardlink.txt')).status,403);assert.equal((await get(c.names.normal)).status,403);}finally{observer('unlink-hardlink');}await verify(c.names.normal,'normal');});
  await independent('real UID DAC and download mount boundaries',async()=>{report.boundaries=observer('boundaries');for(const v of Object.values(report.boundaries))assert(Array.isArray(v)?v.every(Boolean):v===true,JSON.stringify(report.boundaries));const mount=observer('state').mounts;assert(mount.some(s=>s.includes('nosuid')&&s.includes('nodev')&&s.includes('noexec')));report.directoryReplacement=observer('replacement');});
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
