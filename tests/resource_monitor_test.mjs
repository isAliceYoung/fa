import assert from 'node:assert/strict';
import { PressureState, fields, psi } from '../src/lib/resource-monitor.mjs';
import { boundedDesktopSize } from '../public/novnc/core/util/desktop-size.mjs';
const M=1024*1024;
function normal(){return {available:true,memory:{available:400*M,effectiveLimit:1024*M,executionRatio:.4,eventsDelta:{oom:0,oom_kill:0,max:0,high:0},psi:{some:{avg10:0},full:{avg10:0}}},cpu:{usagePercent:20,throttledPercent:0,psi:{some:{avg10:0}}},swap:{total:1024*M,used:0,growth:0},disk:{system:{available:4*1024*M,total:10*1024*M,freeInodes:100000},workspace:{available:400*M,total:512*M,freeInodes:16000}}};}
let count=0;function test(fn){fn();count++;}
test(()=>{assert.equal(fields('MemAvailable: 500 kB\nMemFree: 2 kB').MemAvailable,500);assert.equal(psi('some avg10=61.19 avg60=5 total=100').some.avg10,61.19);});
test(()=>{const p=new PressureState();assert.equal(p.observe(normal(),0).level,'normal');assert.equal(p.observe(null,1).level,'unknown');});
test(()=>{const p=new PressureState(),s=normal();s.memory.psi.some.avg10=20;assert.equal(p.observe(s,0).level,'normal');assert.equal(p.observe(s,9999).level,'normal');assert.equal(p.observe(s,10000).level,'warning');assert.equal(p.observe(s,10001).notify,false);assert.equal(p.observe(s,130001).notify,true);});
test(()=>{const p=new PressureState(),s=normal();s.memory.eventsDelta.high=900000;assert.equal(p.observe(s,0).level,'normal');assert.equal(p.observe(s,20000).level,'normal');});
test(()=>{const p=new PressureState(),s=normal();s.memory.psi.full.avg10=10;p.observe(s,0);assert.equal(p.observe(s,10000).level,'critical');p.observe(normal(),10001);assert.equal(p.observe(normal(),39999).level,'critical');assert.equal(p.observe(normal(),40001).level,'normal');});
test(()=>{const p=new PressureState(),s=normal();s.memory.eventsDelta.oom_kill=1;assert.equal(p.observe(s,0).level,'critical');s.memory.eventsDelta.oom_kill=0;assert.equal(p.observe(s,5000).level,'critical');});
test(()=>{const p=new PressureState(),s=normal();s.cpu.usagePercent=98;s.cpu.psi.some.avg10=62;p.observe(s,0);assert.equal(p.observe(s,10000).level,'warning');});
test(()=>{const p=new PressureState(),s=normal();s.swap.used=900*M;p.observe(s,0);assert.equal(p.observe(s,20000).level,'normal');s.swap.growth=16*M;p.observe(s,20001);assert.equal(p.observe(s,30001).level,'warning');});
test(()=>{const p=new PressureState(),s=normal();s.disk.workspace.freeInodes=10;p.observe(s,0);assert.equal(p.observe(s,10000).level,'critical');});
test(()=>{const p=new PressureState(),s=normal();s.memory.psi.some.avg10=20;p.observe(s,0);p.observe(null,20000);assert.equal(p.observe(s,30000).level,'normal');assert.equal(p.observe(s,40000).level,'warning');});
test(()=>{const p=new PressureState(),s=normal();s.derivativesReady=false;assert.equal(p.observe(s,0).level,'unknown');s.derivativesReady=true;assert.equal(p.observe(s,5000).level,'normal');});
test(()=>{const p=new PressureState(),s=normal();s.io={psi:{some:{avg10:20},full:{avg10:0}}};p.observe(s,0);const result=p.observe(s,10000);assert.equal(result.level,'warning');assert(result.reasons.includes('io_wait'));});
test(()=>{assert.deepEqual(boundedDesktopSize(1920,1080),{width:1600,height:900});assert.deepEqual(boundedDesktopSize(800,1200),{width:666,height:1000});assert.deepEqual(boundedDesktopSize(320,200),{width:640,height:480});assert.throws(()=>boundedDesktopSize(NaN,1000));});
console.log(JSON.stringify({passed:count,failed:0,skipped:0,not_executed:0}));
