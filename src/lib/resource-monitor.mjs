// Aggregate-only Linux telemetry. No process list, user-selected path or command.
import fs from 'node:fs/promises';
import { constants } from 'node:fs';

export const THRESHOLDS=Object.freeze({sampleMs:5000,enterMs:10000,recoverMs:30000,cooldownMs:120000});
const M=1024*1024, ROOT='/sys/fs/cgroup/webdesktop.slice';
const number=value=>Number.isFinite(Number(value))?Number(value):null;
export function fields(text){return Object.fromEntries(text.trim().split('\n').filter(Boolean).map(line=>{const [k,v]=line.split(/\s+/);return [k.replace(':',''),number(v)];}));}
export function psi(text){return Object.fromEntries(text.trim().split('\n').filter(Boolean).map(line=>{const [kind,...parts]=line.split(/\s+/);return [kind,Object.fromEntries(parts.map(part=>{const [k,v]=part.split('=');return [k,number(v)];}))];}));}
const delta=(next,old)=>Object.fromEntries(Object.entries(next).map(([k,v])=>[k,old&&Number.isFinite(old[k])?Math.max(0,v-old[k]):0]));
const cap=x=>Math.max(0,Math.min(100,x));
export class PressureState {
  constructor(){this.level='normal';this.pending=null;this.since=0;this.lastNotice=-Infinity;this.oomUntil=0;}
  observe(s,now){
    if(!s||!s.available){this.pending=null;this.since=now;return {level:'unknown',message:'暂时无法获取服务器资源状态',reasons:['telemetry_unavailable'],notify:false};}
    const warnings=[],critical=[];
    const avail=s.memory.available/Math.max(1,s.memory.effectiveLimit), full=s.memory.psi.full?.avg10??0, some=s.memory.psi.some?.avg10??0;
    if(s.memory.eventsDelta.oom_kill>0||s.memory.eventsDelta.oom>0){this.oomUntil=now+30000;critical.push('oom');}
    if(now<this.oomUntil)critical.push('recent_oom');
    if(avail<.04||full>=8)critical.push('memory_stall');
    else if((avail<.1&&some>=1)||some>=5||full>=2||(s.memory.executionRatio>.9&&s.memory.eventsDelta.max>0))warnings.push('memory_reclaim');
    if(((s.cpu.psi.some?.avg10??0)>=40&&s.cpu.usagePercent>=85)||s.cpu.throttledPercent>=20)warnings.push('cpu_wait');
    if(s.swap.total>0&&s.swap.used/s.swap.total>.6&&(s.swap.growth>8*M||some>=5))warnings.push('swap_pressure');
    for(const [name,d] of Object.entries(s.disk)){
      if(d.available< (name==='workspace'?32*M:256*M)||d.freeInodes<64)critical.push(name+'_full');
      else if(d.available<Math.max(name==='workspace'?64*M:1024*M,d.total*.1)||d.freeInodes<1024)warnings.push(name+'_space');
    }
    const desired=critical.length?'critical':warnings.length?'warning':'normal';
    if(this.pending!==desired){this.pending=desired;this.since=now;}
    const rank={normal:0,warning:1,critical:2};
    if(desired!==this.level&&(now-this.since>=(rank[desired]<rank[this.level]?THRESHOLDS.recoverMs:THRESHOLDS.enterMs)||critical.includes('oom')))this.level=desired;
    const notify=this.level!=='normal'&&desired===this.level&&now-this.lastNotice>=THRESHOLDS.cooldownMs;
    if(notify)this.lastNotice=now;
    return {level:this.level,reasons:[...new Set([...critical,...warnings])].slice(0,8),notify,
      message:this.level==='critical'?'服务器资源压力严重，请先保存工作并关闭暂时不用的应用；新的图形启动可能需要等待。':this.level==='warning'?'当前服务器资源紧张，应用可能响应较慢。建议关闭暂时不用的标签页或应用。':'服务器资源状态正常'};
  }
}

async function read(path,maximum=16384){
  const handle=await fs.open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
  try{const buffer=Buffer.alloc(maximum+1);const {bytesRead}=await handle.read(buffer,0,buffer.length,0);if(bytesRead>maximum)throw new Error('Metric bound');return buffer.subarray(0,bytesRead).toString('utf8');}
  finally{await handle.close();}
}
async function loadPolicy(){
  const path='/etc/webdesktop-resources/policy.json',info=await fs.lstat(path);
  if(info.uid!==0||!info.isFile()||info.nlink!==1||info.mode&0o022)throw new Error('Unsafe resource policy');
  const p=JSON.parse(await read(path,32768));
  if(p.policy!=='adaptive-v1'||p.schema!==1||!Number.isFinite(p.effective?.memory_bytes)||!Number.isFinite(p.effective?.cpu_count))throw new Error('Unknown resource policy');
  return p;
}
async function group(path){
  const names=['memory.current','memory.max','memory.high','memory.swap.current','memory.swap.max','memory.events','memory.events.local','cpu.stat','pids.current'];
  const values=await Promise.all(names.map(name=>read(path+'/'+name)));
  return Object.fromEntries(names.map((name,i)=>[name,name.includes('events')||name==='cpu.stat'?fields(values[i]):values[i].trim()==='max'?null:number(values[i].trim())]));
}
async function disk(path){const d=await fs.statfs(path);return {total:d.blocks*d.bsize,available:d.bavail*d.bsize,freeInodes:d.ffree,totalInodes:d.files};}
const key=Symbol.for('webdesktop.aggregate.resources.v1');
const shared=globalThis[key]??={previous:null,cached:null,pending:null,lastAt:-Infinity,pressure:new PressureState(),timer:null};
async function sample(){
  const previous=shared.previous;
  const p=await loadPolicy();
  const [memText,memoryPsi,cpuPsi,parent,execution,rootDisk,workspaceDisk]=await Promise.all([
    read('/proc/meminfo'),read('/proc/pressure/memory'),read('/proc/pressure/cpu'),group(ROOT),group(ROOT+'/webdesktop-exec.slice'),disk('/'),disk('/home/wdguest')]);
  const now=Date.now(),mem=fields(memText),elapsed=previous?Math.max(.1,(now-previous.at)/1000):null;
  const localDelta=delta(parent['memory.events.local'],previous?.parent['memory.events.local']);
  const treeDelta=delta(parent['memory.events'],previous?.parent['memory.events']);
  const execDelta=delta(execution['memory.events.local'],previous?.execution['memory.events.local']);
  const cpuDelta=delta(parent['cpu.stat'],previous?.parent['cpu.stat']);
  const execCpuDelta=delta(execution['cpu.stat'],previous?.execution['cpu.stat']);
  const s={schema:1,available:true,at:now,sampleIntervalMs:THRESHOLDS.sampleMs,
    memory:{total:mem.MemTotal*1024,available:Math.max(0,Math.min(mem.MemAvailable*1024,p.effective.memory_bytes-parent['memory.current'])),
      hostAvailable:mem.MemAvailable*1024,effectiveLimit:p.effective.memory_bytes,current:parent['memory.current'],max:parent['memory.max'],high:parent['memory.high'],
      executionCurrent:execution['memory.current'],executionMax:execution['memory.max'],executionRatio:execution['memory.current']/execution['memory.max'],
      eventsDelta:treeDelta,eventsLocalDelta:localDelta,executionLocalDelta:execDelta,psi:psi(memoryPsi)},
    cpu:{effectiveCores:p.effective.cpu_count,usagePercent:elapsed?cap(cpuDelta.usage_usec/(elapsed*1e6*p.effective.cpu_count)*100):0,
      throttledPercent:elapsed?cap(Math.max(cpuDelta.throttled_usec,execCpuDelta.throttled_usec)/(elapsed*1e6*p.effective.cpu_count)*100):0,
      parentThrottledPeriodsDelta:cpuDelta.nr_throttled??0,executionThrottledPeriodsDelta:execCpuDelta.nr_throttled??0,psi:psi(cpuPsi)},
    swap:{total:p.effective.swap_bytes,used:Math.max(0,(mem.SwapTotal-mem.SwapFree)*1024),workloadCurrent:parent['memory.swap.current'],workloadLimit:parent['memory.swap.max'],
      growth:previous?Math.max(0,parent['memory.swap.current']-previous.parent['memory.swap.current']):0},
    disk:{system:rootDisk,workspace:workspaceDisk},tasks:{workload:parent['pids.current'],execution:execution['pids.current']}};
  if(!Number.isFinite(s.memory.available)||!Number.isFinite(s.cpu.effectiveCores)||
    ![s.memory.max,s.memory.executionMax].every(x=>typeof x==='number'&&x>0)||
    ![s.memory.psi.some?.avg10,s.memory.psi.full?.avg10,s.cpu.psi.some?.avg10].every(x=>typeof x==='number'&&Number.isFinite(x)))throw new Error('Required Linux metrics unavailable');
  shared.previous={at:now,parent,execution};s.status=shared.pressure.observe(s,now);return s;
}
export async function resourceSnapshot(){
  const now=Date.now();
  if(shared.cached&&now-shared.lastAt<THRESHOLDS.sampleMs)return shared.cached;
  if(shared.pending)return shared.pending;
  shared.pending=(async()=>{try{shared.cached=await sample();}catch(error){if(shared.cached?.available!==false)console.warn('Aggregate resource telemetry unavailable:',error.code||error.name);shared.cached={schema:1,available:false,at:Date.now(),sampleIntervalMs:THRESHOLDS.sampleMs,status:shared.pressure.observe(null,Date.now())};}
    shared.lastAt=Date.now();return shared.cached;})().finally(()=>{shared.pending=null;});
  return shared.pending;
}
export async function graphicalAdmission(){const s=await resourceSnapshot();return !s.available?'unknown':s.status.level==='critical'?'busy':'ready';}
export function startResourceSampling(){
  if(!shared.timer){void resourceSnapshot();shared.timer=setInterval(()=>void resourceSnapshot(),THRESHOLDS.sampleMs);shared.timer.unref();}
  return ()=>{clearInterval(shared.timer);shared.timer=null;};
}
