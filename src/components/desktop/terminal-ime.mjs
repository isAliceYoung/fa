// xterm 6.0.0's screen-reader input guard drops the composition-free Windows
// IME commit observed in our Sogou trace. Keep accessibility and normal xterm
// composition handling; only fill an otherwise silent, confirmed DOM edit.
export function attachTerminalIme(term) {
  const area=term.textarea, element=term.element;
  if(!area||!element)throw new Error('Open the terminal before attaching IME support');
  let enabled=false, disposed=false, down=false, pressed=false, composing=false, settling=false;
  let pending=null, commitTimer, settleTimer;
  const listeners=[];
  const listen=(target,type,fn,capture=false)=>{target.addEventListener(type,fn,capture);listeners.push(()=>target.removeEventListener(type,fn,capture));};
  const cancel=()=>{clearTimeout(commitTimer);commitTimer=undefined;pending=null;};
  const ready=()=>enabled&&!disposed&&!term.options.disableStdin&&term.options.screenReaderMode===true&&area.ownerDocument.activeElement===area;
  const confirmed=e=>e.isTrusted&&e.inputType==='insertText'&&e.isComposing===false&&typeof e.data==='string'&&e.data.length>0&&e.data.length<=16384&&!/[\x00-\x1f\x7f]/.test(e.data);
  const flush=()=>{
    const p=pending;cancel();
    if(p?.confirmed&&!p.emitted&&ready()&&!down&&!pressed&&!composing&&!settling)term.input(p.data,true);
  };
  // Observation only: do not replace, cancel or suppress xterm's own onData.
  const dataSubscription=term.onData(()=>{if(pending)pending.emitted=true;});
  listen(area,'beforeinput',e=>{
    flush();
    if(ready()&&confirmed(e)&&!down&&!pressed&&!composing&&!settling)
      pending={data:e.data,confirmed:false,emitted:false};
  });
  // Bubble after xterm's input capture handler, giving it first ownership.
  listen(area,'input',e=>{
    const p=pending;
    if(!p||p.confirmed)return;
    if(!ready()||!confirmed(e)||p.data!==e.data||down||pressed||composing||settling){cancel();return;}
    p.confirmed=true;
    // One cancellable task, no retries. A late compositionend or xterm data
    // in this event chain owns the commit and cancels/satisfies this fallback.
    commitTimer=setTimeout(flush,0);
  });
  listen(area,'compositionstart',()=>{cancel();composing=true;settling=false;clearTimeout(settleTimer);});
  listen(area,'compositionend',()=>{
    cancel();composing=false;settling=true;clearTimeout(settleTimer);
    // Registered after xterm's compositionend handler: its finalizer runs first.
    settleTimer=setTimeout(()=>{settling=false;settleTimer=undefined;},0);
  });
  // Local ancestor capture flushes a committed edit BEFORE xterm sends Enter
  // or a control character. Never listen on document/window or inspect keys.
  listen(element,'keydown',e=>{if(e.target!==area)return;flush();down=true;},true);
  listen(element,'keypress',e=>{if(e.target!==area)return;flush();pressed=true;},true);
  listen(element,'keyup',e=>{if(e.target!==area)return;flush();down=false;pressed=false;},true);
  const reset=()=>{cancel();clearTimeout(settleTimer);settleTimer=undefined;down=pressed=composing=settling=false;};
  listen(area,'blur',reset);
  return{
    setEnabled(value){reset();enabled=!!value&&!disposed;},
    dispose(){if(disposed)return;enabled=false;disposed=true;reset();dataSubscription.dispose();for(const remove of listeners)remove();}
  };
}
