// Event-model tests, not a native input method or browser acceptance test.
import assert from 'node:assert/strict';
import {attachTerminalIme} from '../src/components/desktop/terminal-ime.mjs';
import {terminalInputFrames} from '../src/components/desktop/terminal-input.mjs';
import fs from 'node:fs';
class Target{
 constructor(){this.listeners=[];}
 addEventListener(type,fn,capture){this.listeners.push({type,fn,capture});}
 removeEventListener(type,fn){this.listeners=this.listeners.filter(l=>l.type!==type||l.fn!==fn);}
 emit(type,e){for(const l of [...this.listeners])if(l.type===type)l.fn(e);}
}
function fixture(mode=true){
 const area=new Target(),element=new Target(),data=[],callbacks=new Set();area.ownerDocument={activeElement:area};
 const term={textarea:area,element,options:{screenReaderMode:mode,disableStdin:false},onData(fn){callbacks.add(fn);return{dispose:()=>callbacks.delete(fn)};},input(s){for(const fn of callbacks)fn(s);data.push(s);}};
 const ime=attachTerminalIme(term);ime.setEnabled(true);
 const event=(type,extra={})=>{const e={target:area,isTrusted:true,isComposing:false,inputType:'insertText',data:'你好',...extra};element.emit(type,e);area.emit(type,e);};
 const pair=(s='你好',extra={})=>{event('beforeinput',{data:s,...extra});event('input',{data:s,...extra});};
 return{area,element,term,ime,data,event,pair};
}
const tick=()=>new Promise(r=>setTimeout(r,8));let passed=0,failed=0;
async function test(name,fn){try{await fn();passed++;console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+' '+e.name+': '+e.message);}}
await test('replay the actual user-provided Sogou failure metadata',async()=>{const trace=JSON.parse(fs.readFileSync(new URL('./fixtures/terminal-ime-native-trace.json',import.meta.url),'utf8'));assert.equal(trace.baseline.a.helloOnData,0);assert.equal(trace.baseline.b.helloOnData,1);const f=fixture();for(const e of trace.baseline.a.criticalEvents){assert(e.fixedHello&&!e.keyDownSeen&&!e.keyPressHandled&&!e.helperComposing);f.event(e.type,{...e,data:'你好'});}await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('observed Sogou final input without any composition events submits exactly once',async()=>{const f=fixture();f.pair();await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('screen reader false remains owned by xterm',async()=>{const f=fixture(false);f.pair();f.term.input('你好');await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('normal compositionend remains owned by xterm',async()=>{const f=fixture();f.event('compositionstart');f.pair('你好',{isComposing:true});f.event('compositionend');f.term.input('你好');await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('late compositionend cancels the fallback before the native finalizer',async()=>{const f=fixture();f.pair();f.event('compositionend');f.term.input('你好');await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('preedit and unclosed composition are never guessed as final text',async()=>{const f=fixture();f.event('compositionstart');f.pair('nihao',{inputType:'insertCompositionText',isComposing:true});f.pair();await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('composition cancellation does not emit a candidate',async()=>{const f=fixture();f.event('compositionstart');f.event('compositionend',{data:''});await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('duplicate input without a new edit does not double submit',async()=>{const f=fixture();f.pair();f.event('input');await tick();f.event('input');await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('two legitimate identical confirmed edits are both preserved even before a timer tick',async()=>{const f=fixture();f.pair();f.pair();await tick();assert.deepEqual(f.data,['你好','你好']);f.ime.dispose();});
await test('mismatched beforeinput and input fail closed',async()=>{const f=fixture();f.event('beforeinput');f.event('input',{data:'世界'});await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('an input event without matching beforeinput cannot add fallback input',async()=>{const f=fixture();f.event('input');await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('beforeinput alone is not a committed edit',async()=>{const f=fixture();f.event('beforeinput');f.event('keydown');await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('untrusted JavaScript-dispatched events do not use the fallback',async()=>{const f=fixture();f.pair('你好',{isTrusted:false});await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('held key or keypress defers to normal xterm handling',async()=>{const f=fixture();f.event('keydown');f.pair();f.event('keyup');f.event('keypress');f.pair();f.event('keyup');await tick();assert.deepEqual(f.data,[]);f.pair();await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('any xterm output for this edit prevents an extra fallback',async()=>{const f=fixture();f.event('beforeinput');f.term.input('你好');f.event('input');await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('disabled stdin does not acquire or submit an edit',async()=>{const f=fixture();f.term.options.disableStdin=true;f.pair();await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('disconnect then resume does not replay an old pending edit',async()=>{const f=fixture();f.pair();f.ime.setEnabled(false);f.pair();f.ime.setEnabled(true);f.event('input');await tick();assert.deepEqual(f.data,[]);f.pair();await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
await test('blur cancels beforeinput as well as already scheduled fallback',async()=>{for(const committed of [false,true]){const f=fixture();f.event('beforeinput');if(committed)f.event('input');f.event('blur');f.area.ownerDocument.activeElement=null;await tick();assert.deepEqual(f.data,[]);f.ime.dispose();}});
await test('destruction is idempotent, removes listeners and discards scheduled work',async()=>{const f=fixture();f.pair();f.ime.dispose();f.ime.dispose();await tick();assert.deepEqual(f.data,[]);assert.equal(f.area.listeners.length+f.element.listeners.length,0);});
await test('new preedit cancels an ambiguous pending fallback',async()=>{const f=fixture();f.pair();f.event('compositionstart');await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('emoji and astral CJK stay intact',async()=>{const f=fixture();f.pair('𠮷😀');await tick();assert.deepEqual(f.data,['𠮷😀']);f.ime.dispose();});
await test('long CJK uses the original bounded frame encoder without splitting surrogate pairs',async()=>{const f=fixture(),s='你'.repeat(4095)+'😀'+'好'.repeat(2000);f.pair(s);await tick();const frames=[...terminalInputFrames(f.data.join(''))];assert.equal(frames.length,2);assert(frames.every(x=>x.d.length<=4096));assert.equal(frames.map(x=>x.d).join(''),s);assert(!/[\uD800-\uDBFF]$/.test(frames[0].d));f.ime.dispose();});
await test('oversized fallback edit is not retained or sent',async()=>{const f=fixture();f.pair('你'.repeat(16385));await tick();assert.deepEqual(f.data,[]);f.ime.dispose();});
await test('ordinary English keys, controls and paste remain owned by xterm',async()=>{const f=fixture();f.event('keydown');f.pair('a');f.term.input('a');f.event('keyup');for(const s of ['你好\r','你好\x03'])f.pair(s);f.pair('你好',{inputType:'insertFromPaste'});await tick();assert.deepEqual(f.data,['a']);f.term.input('\x03\x04\x1a你好');assert.deepEqual(f.data,['a','\x03\x04\x1a你好']);f.ime.dispose();});
await test('Sogou Enter raw pinyin commit is text, without invented Chinese or shell CR',async()=>{const f=fixture();f.pair('nihao');await tick();assert.deepEqual(f.data,['nihao']);f.ime.dispose();});
for(const [name,key]of [['Enter','\r'],['Ctrl+C','\x03'],['Ctrl+D','\x04'],['Ctrl+Z','\x1a']])await test('confirmed text is flushed before '+name,async()=>{const f=fixture();f.pair();f.event('keydown');f.term.input(key);await tick();assert.deepEqual(f.data,['你好',key]);f.ime.dispose();});
await test('events from a different element do not change this terminal state',async()=>{const f=fixture();f.event('keydown',{target:{}});f.pair();await tick();assert.deepEqual(f.data,['你好']);f.ime.dispose();});
console.log(JSON.stringify({level:'event-model',passed,failed,skipped:0,not_executed:0}));process.exitCode=failed?1:0;
