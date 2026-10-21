// Actual Chromium + stock xterm + public bridge + WebSocket echo integration.
// CDP and deliberate DOM fault injection are NOT native Windows Sogou tests.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
import {startImeLab} from './terminal_ime_lab.mjs';
const out=process.argv[2]||'.test-results/terminal-ime-browser.json';
const executablePath=process.env.IME_TEST_CHROME||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':execFileSync('sh',['-c','command -v google-chrome || command -v chromium'],{encoding:'utf8'}).trim());
const lab=await startImeLab(0),browser=await chromium.launch({executablePath,headless:true,chromiumSandbox:true});
const report={level:'real-Chromium-xterm-WebSocket-CDP-not-native-IME',passed:0,failed:0,skipped:0,not_executed:0,tests:[]};
const cases=[
 ['observed composition-free confirmed Chinese exactly once',async f=>{await f.insert('你好');await f.expect('你好');}],
 ['normal native composition pipeline exactly once',async f=>{await f.cdp.send('Input.imeSetComposition',{text:'nihao',selectionStart:5,selectionEnd:5});await f.insert('你好');await f.expect('你好');}],
 ['rapid identical legitimate commits both survive',async f=>{await f.insert('你好');await f.insert('你好');await f.expect('你好你好');}],
 ['English CJK punctuation and fast switching preserve order',async f=>{await f.page.keyboard.type('abc');await f.insert('你好');await f.page.keyboard.type('XYZ');await f.insert('，。');await f.expect('abc你好XYZ，。');}],
 ['Enter follows confirmed Chinese in order',async f=>{await f.insert('你好');await f.page.keyboard.press('Enter');await f.expect('你好\r');}],
 ['Sogou Enter style raw pinyin commit does not invent a CR',async f=>{await f.insert('nihao');await f.expect('nihao');}],
 ['Ctrl C D Z keep original control bytes',async f=>{for(const key of ['Control+c','Control+d','Control+z'])await f.page.keyboard.press(key);await f.expect('\x03\x04\x1a');}],
 ['real clipboard Ctrl Shift V uses original paste semantics',async f=>{await f.page.evaluate(()=>navigator.clipboard.writeText('你好'));await f.page.keyboard.press('Control+Shift+V');await f.expect('你好');}],
 ['emoji and astral CJK are not split',async f=>{await f.insert('𠮷😀');await f.expect('𠮷😀');}],
 ['long CJK uses existing bounded UTF16 frames',async f=>{const text='你'.repeat(4095)+'😀好';await f.insert(text);await f.expect(text);assert(f.frames.every(s=>s.length<=4096));assert.equal(f.frames.join(''),text);assert.equal(f.frames.length,2);}],
 ['native candidate cancellation emits nothing',async f=>{await f.cdp.send('Input.imeSetComposition',{text:'nihao',selectionStart:5,selectionEnd:5});await f.cdp.send('Input.imeSetComposition',{text:'',selectionStart:0,selectionEnd:0});await f.expect('');}],
 ['untrusted duplicate DOM events cannot use the bridge',async f=>{await f.page.evaluate(id=>{const ta=window.imeLab.sessions.get(id).term.textarea;for(let n=0;n<2;n++)for(const type of ['beforeinput','input'])ta.dispatchEvent(new InputEvent(type,{data:'你好',inputType:'insertText',isComposing:false,composed:true,bubbles:true}));},f.id);await f.expect(f.mode?'':'你好你好');}],
 ['blur before fallback discards pending bridge input',async f=>{await f.inject('blur');await f.insert('你好');await f.expect(f.mode?'':'你好');}],
 ['disconnect and resume do not replay the old bridge edit',async f=>{await f.inject('disconnect');await f.insert('你好');await f.pause();const first=await f.data();assert.equal(first,f.mode?'':'你好');await f.page.evaluate(id=>{const s=window.imeLab.sessions.get(id);s.term.options.disableStdin=false;s.ime.setEnabled(true);s.term.focus();},f.id);await f.expect(first);await f.insert('世界');await f.expect(first+'世界');}],
 ['destroying the component cancels pending bridge work',async f=>{await f.inject('dispose');await f.insert('你好');await f.pause();assert.equal(await f.data(),f.mode?'':'你好');}],
 ['full page refresh never replays a pending edit',async f=>{await f.insert('你好');await f.expect('你好');await f.page.reload();await f.page.waitForFunction(()=>window.imeLab?.sessions.get('a').ws.readyState===1);assert.equal(await f.page.evaluate(()=>window.imeLab.sessions.get('a').events.filter(e=>e.type==='WebSocket.inputFrame').length),0);}],
 ['screen reader tree and live region remain configured',async f=>{const result=await f.page.evaluate(id=>{const t=window.imeLab.sessions.get(id).term;return{tree:!!t.element.querySelector('.xterm-accessibility-tree'),live:!!t.element.querySelector('[aria-live]')};},f.id);assert.equal(result.tree,f.mode);assert.equal(result.live,f.mode);}],
 ['cancel then switch focus does not leak a candidate',async f=>{await f.cdp.send('Input.imeSetComposition',{text:'nihao',selectionStart:5,selectionEnd:5});await f.cdp.send('Input.imeSetComposition',{text:'',selectionStart:0,selectionEnd:0});await f.page.evaluate(id=>window.imeLab.sessions.get(id==='a'?'b':'a').term.focus(),f.id);await f.expect('');}]
];
report.not_executed=cases.length*2;
try{
 for(const mode of [true,false])for(const [name,run]of cases){
  const context=await browser.newContext();await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:lab.origin});
  const page=await context.newPage(),frames=[],id=mode?'a':'b';
  page.on('websocket',ws=>ws.on('framesent',event=>{const m=JSON.parse(String(event.payload));if(m.t==='i')frames.push(m.d);}));
  report.not_executed--;
  try{
   await page.goto(lab.origin+'/fixed');await page.waitForFunction(()=>[...window.imeLab.sessions.values()].every(s=>s.ws.readyState===1));
   await page.evaluate(id=>{const s=window.imeLab.sessions.get(id);window.imeSubject=s;window.testOutput=[];s.term.onData(d=>window.testOutput.push(d));s.term.focus();},id);
   const cdp=await context.newCDPSession(page),f={page,cdp,id,mode,frames,insert:text=>cdp.send('Input.insertText',{text}),pause:()=>page.waitForTimeout(40),data:()=>page.evaluate(()=>window.testOutput.join('')),
    async expect(text){await page.waitForTimeout(45);assert.equal(await this.data(),text);},
    async inject(action){this.action=action;await page.evaluate(action=>{window.imeSubject.term.textarea.addEventListener('input',()=>queueMicrotask(()=>{const s=window.imeSubject;if(action==='blur')s.term.blur();else if(action==='dispose')s.dispose();else if(action==='disconnect'){s.ime.setEnabled(false);s.term.options.disableStdin=true;s.term.blur();}window.imeFaultInjected=action;}),{once:true});},action);}
   };
   await run(f);if(f.action)assert.equal(await page.evaluate(()=>window.imeFaultInjected),f.action);report.passed++;report.tests.push({name,screenReaderMode:mode,result:'passed'});
  }catch(error){report.failed++;report.tests.push({name,screenReaderMode:mode,result:'failed',detail:(error.name+': '+error.message).slice(0,1500)});}
  finally{await context.close();}
 }
}finally{await browser.close();await lab.close();fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,JSON.stringify(report,null,2));}
console.log(JSON.stringify({level:report.level,passed:report.passed,failed:report.failed,skipped:report.skipped,not_executed:report.not_executed}));process.exitCode=report.failed?1:0;
