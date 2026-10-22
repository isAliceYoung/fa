import assert from 'node:assert/strict';
import {keyFrames} from './rfb_key_packets.mjs';
let passed=0;const test=fn=>{fn();passed++;};
test(()=>{const text="' > /home/wdguest/Documents/ime-xim.txt",f=keyFrames(text);assert(text.length*2>64);assert.equal(f.length,1);assert.equal(f[0].length,text.length*16);});
test(()=>{const f=keyFrames('a'.repeat(1025));assert.equal(f.length,5);assert(f.every(b=>b.length<=4096));});
test(()=>{const raw=Buffer.concat(keyFrames("nihao1' > /tmp/x"));for(let p=0;p<raw.length;p+=16){assert.equal(raw[p],4);assert.equal(raw[p+1],1);assert.equal(raw[p+8],4);assert.equal(raw[p+9],0);assert.equal(raw.readUInt32BE(p+4),raw.readUInt32BE(p+12));}});
test(()=>{const raw=Buffer.concat(keyFrames('中🧪'));assert.equal(raw.readUInt32BE(4),0x01004e2d);assert.equal(raw.readUInt32BE(20),0x0101f9ea);});
test(()=>assert.deepEqual(keyFrames(''),[]));
console.log(JSON.stringify({passed,failed:0,skipped:0,not_executed:0}));
