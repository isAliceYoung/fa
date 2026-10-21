import {terminalInputFrames} from '/terminal-input.mjs';
import {attachTerminalIme} from '/terminal-ime.mjs';
const sessions=new Map();
const fixed=location.pathname==='/fixed';
if(fixed)document.getElementById('title').textContent='本机输入法 A/B 诊断 · 6.0.0 + 窄范围提交修复';
const meta=data=>({length:typeof data==='string'?data.length:0,containsChinese:typeof data==='string'&&/\p{Script=Han}/u.test(data),fixedHello:data==='你好'});
function create(id,screenReaderMode){
 const host=document.getElementById(id);host.replaceChildren();
 const term=new window.Terminal({cols:90,rows:13,fontSize:14,screenReaderMode,allowProposedApi:true,theme:{background:'#0b1120'}});term.open(host);
 const ime=fixed?attachTerminalIme(term):null;
 term.attachCustomKeyEventHandler(e=>{
  if(e.type!=='keydown')return true;
  const mod=e.ctrlKey||e.metaKey,key=e.key.toLowerCase();
  if(mod&&key==='c'&&(e.shiftKey||term.hasSelection())){e.preventDefault();const text=term.getSelection();if(text)void navigator.clipboard.writeText(text);term.clearSelection();return false;}
  if(mod&&e.shiftKey&&key==='v'){e.preventDefault();void navigator.clipboard.readText().then(text=>text&&term.paste(text));return false;}
  return true;
 });
 const events=[],ws=new WebSocket(location.origin.replace('http:','ws:')+'/echo');let count=0,chinese=0;
 const snapshot=()=>{const core=term._core,c=core?._compositionHelper,ta=term.textarea;return{selectionStart:ta?.selectionStart,selectionEnd:ta?.selectionEnd,textareaLength:ta?.value.length,keyDownSeen:core?._keyDownSeen,keyPressHandled:core?._keyPressHandled,helperComposing:c?.isComposing,helperSending:c?._isSendingComposition,compositionStart:c?._compositionPosition?.start,compositionEnd:c?._compositionPosition?.end};};
 const record=(type,data={})=>{events.push({sequence:++count,ms:Math.round(performance.now()),type,...data,...snapshot()});if(events.length>256)events.shift();document.getElementById(id+'-summary').textContent=JSON.stringify({ready:ws.readyState===1,onDataChineseCount:chinese,last:events.at(-1)},null,2);};
 for(const type of ['compositionstart','compositionupdate','compositionend','keydown','keyup','beforeinput','input','blur','focus']){
  term.textarea.addEventListener(type,e=>record(type,{inputType:e.inputType,isComposing:e.isComposing,composed:e.composed,isTrusted:e.isTrusted,keyKind:e.keyCode===229?'ime':e.key==='Enter'?'enter':e.key===' '?'space':e.key==='1'?'candidate-1':e.ctrlKey||e.metaKey?'control':e.key?.length===1?'printable':e.key?'other':undefined,...meta(e.data)}),true);
 }
 term.onData(data=>{if(/\p{Script=Han}/u.test(data))chinese++;record('xterm.onData',meta(data));for(const frame of terminalInputFrames(data)){if(ws.readyState===1){record('WebSocket.inputFrame',meta(frame.d));ws.send(JSON.stringify(frame));}}});
 ws.onopen=()=>{ime?.setEnabled(true);record('socket.open');};ws.onmessage=e=>{const m=JSON.parse(e.data);record('WebSocket.echo',meta(m.d));term.write(m.d.replaceAll('\r','\r\n'));};
 const s={term,ime,ws,events,record,screenReaderMode,dispose(){ime?.dispose();ws.close();term.dispose();}};sessions.set(id,s);return s;
}
function reset(){for(const s of sessions.values())s.dispose();sessions.clear();create('a',true);create('b',false);}
const report=()=>({schema:1,version:'6.0.0',fixed,kind:'local-echo-diagnostic-no-shell',browser:navigator.userAgent,sessions:[...sessions].map(([id,s])=>({id,screenReaderMode:s.screenReaderMode,events:s.events}))});
document.getElementById('reset').onclick=reset;
document.getElementById('copy').onclick=async()=>{await navigator.clipboard.writeText(JSON.stringify(report(),null,2));document.getElementById('notice').textContent='已复制脱敏记录';};
document.getElementById('download').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(report(),null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='webdesktop-ime-metadata.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
window.imeLab={sessions,reset,report};reset();
