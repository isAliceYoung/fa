// Stock dependency A/B evidence. Expected losses here reproduce defects; they
// are NOT compatibility passes. No native Windows input method is driven.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright-core';
import {startImeLab} from './terminal_ime_lab.mjs';
const out=process.argv[2]||'.test-results/terminal-ime-baseline.json';
const executablePath=process.env.IME_TEST_CHROME||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':execFileSync('sh',['-c','command -v google-chrome || command -v chromium'],{encoding:'utf8'}).trim());
const lab=await startImeLab(0),browser=await chromium.launch({executablePath,headless:true,chromiumSandbox:true});
const report={schema:1,kind:'stock-xterm-6.0.0-CDP-and-synthetic-events-not-native-Windows-IME',observations:[],errors:0};
try{
 const page=await browser.newPage();await page.goto(lab.origin);
 const cdp=await page.context().newCDPSession(page);
 const cases=[
  ['confirmed-insertText-without-composition',false,async()=>{await cdp.send('Input.insertText',{text:'你好'});},[0,1]],
  ['normal-compositionend',false,async()=>{await cdp.send('Input.imeSetComposition',{text:'nihao',selectionStart:5,selectionEnd:5});await cdp.send('Input.insertText',{text:'你好'});},[1,1]],
  ['whole-textarea-replacement-with-compositionend',true,async id=>{await page.evaluate(id=>{const t=window.imeLab.sessions.get(id).term,ta=t.textarea;ta.value='abc';ta.setSelectionRange(3,3);ta.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));ta.dispatchEvent(new CompositionEvent('compositionupdate',{data:'nihao',bubbles:true}));ta.value='nihao';},id);await page.waitForTimeout(20);await page.evaluate(id=>{const ta=window.imeLab.sessions.get(id).term.textarea;ta.dispatchEvent(new CompositionEvent('compositionend',{data:'你好',bubbles:true}));ta.value='你好';},id);},[0,0]],
  ['confirmed-input-with-active-composition-but-no-compositionend',true,async id=>{await page.evaluate(id=>{const ta=window.imeLab.sessions.get(id).term.textarea;ta.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));ta.dispatchEvent(new CompositionEvent('compositionupdate',{data:'nihao',bubbles:true}));ta.value='nihao';},id);await page.waitForTimeout(20);await page.evaluate(id=>{const ta=window.imeLab.sessions.get(id).term.textarea;ta.dispatchEvent(new InputEvent('beforeinput',{data:'你好',inputType:'insertText',isComposing:false,composed:true,bubbles:true}));ta.value='你好';ta.dispatchEvent(new InputEvent('input',{data:'你好',inputType:'insertText',isComposing:false,composed:true,bubbles:true}));},id);},[0,1]],
  ['confirmed-insertText-while-modifier-keyDownSeen',false,async()=>{await page.keyboard.down('Shift');await cdp.send('Input.insertText',{text:'你好'});await page.keyboard.up('Shift');},[0,0]],
  ['cancel-composition',false,async()=>{await cdp.send('Input.imeSetComposition',{text:'nihao',selectionStart:5,selectionEnd:5});await cdp.send('Input.imeSetComposition',{text:'',selectionStart:0,selectionEnd:0});},[0,0]]
 ];
 for(const [name,synthetic,run,expected]of cases){
  for(const [i,id]of ['a','b'].entries()){
   await page.evaluate(()=>window.imeLab.reset());await page.waitForFunction(()=>[...window.imeLab.sessions.values()].every(s=>s.ws.readyState===1));
   await page.evaluate(id=>window.imeLab.sessions.get(id).term.focus(),id);await run(id);await page.waitForTimeout(60);
   const events=await page.evaluate(id=>window.imeLab.sessions.get(id).events,id),sent=events.filter(e=>e.type==='xterm.onData'&&e.fixedHello).length;
   report.observations.push({name,screenReaderMode:i===0,driver:synthetic?'synthetic-DOM':'Chromium-CDP',helloSubmissions:sent,events});
   assert.equal(sent,expected[i],name+' stock A/B behavior changed');
  }
 }
}catch(error){report.errors++;console.error(error.name+': '+error.message);process.exitCode=1;}
finally{fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,JSON.stringify(report,null,2));await browser.close();await lab.close();}
console.log(JSON.stringify({observations:report.observations.length,errors:report.errors,nativeWindowsIme:'not_executed'}));
