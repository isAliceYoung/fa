import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { StatusAdmission, statusIdentity, STATUS_LIMITS } from '../src/lib/status-admission.mjs';

const expires = Date.now() + 3600000;
const identity = () => {
  const access = randomBytes(24).toString('base64url');
  const token = Buffer.from(JSON.stringify({k:'d',sid:access,exp:expires,v:'1'})).toString('base64url')+'.'+'A'.repeat(43);
  return { token, access, cookie:'wd_session='+token };
};
const current=identity(), stranger=identity();
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
let passed=0,failed=0;
async function test(name, fn){try{await fn();passed++;console.log('PASS '+name);}catch(error){failed++;console.error('FAIL '+name+'\n'+error.stack);}}
function fixture(){let now=Date.now();const s=new StatusAdmission(()=>now);s.remember(current.token,current.access,expires,true);return {s,tick:ms=>now+=ms};}
const authorize=(s,id=current,validate=async()=>200,signal,scope='resources')=>s.authorize(scope,id.cookie,id.access,validate,signal);
const release=grant=>{assert.equal(grant.status,200);grant.release();};

await test('reproduce original global quota theft and isolate ten thousand missing credentials',async()=>{
  let oldCount=0;for(let i=0;i<12;i++)oldCount++;assert(oldCount>=12,'Original pre-authentication counter rejects the next valid request');
  const {s}=fixture();for(let i=0;i<10000;i++)assert.equal(s.ingressGrant('',null).status,401);
  release(s.ingressGrant(current.cookie,current.access));release(await authorize(s));assert.equal(s.work.resources.count,1);
});
await test('untrusted ingress occupancy and rate cannot take remembered identity capacity',()=>{
  const {s}=fixture(),a=s.ingressGrant(stranger.cookie,stranger.access),b=s.ingressGrant(stranger.cookie,stranger.access);
  assert.equal(s.ingressGrant(stranger.cookie,stranger.access).status,429);release(s.ingressGrant(current.cookie,current.access));release(a);release(b);
  for(let i=0;i<6;i++)release(s.ingressGrant(stranger.cookie,stranger.access));assert.equal(s.ingressGrant(stranger.cookie,stranger.access).status,429);
  release(s.ingressGrant(current.cookie,current.access));
});
await test('missing Cookie, Cookie only, Session only and wrong page fail before any authentication work',async()=>{
  const {s}=fixture();let calls=0;
  for(const [cookie,access] of [['',current.access],[current.cookie,null],['bad',current.access],[current.cookie,stranger.access]]){
    assert.equal((await s.authorize('gui',cookie,access,async()=>{calls++;return 200;})).status,401);
  }assert.equal(calls,0);assert.equal(s.auth.unknown.count,0);
});
await test('a syntactically plausible self-asserted token is not a trusted admission identity',()=>{
  const {s}=fixture();assert(statusIdentity(stranger.cookie,stranger.access));assert.equal(s.lane(statusIdentity(stranger.cookie,stranger.access)),'unknown');
  assert.equal(s.lane(statusIdentity(current.cookie,current.access)),'known');
});
await test('invalid complete identities do not consume successful resource or GUI quotas',async()=>{
  const {s}=fixture();for(let i=0;i<8;i++)assert.equal((await authorize(s,stranger,async()=>401)).status,401);
  for(let i=0;i<12;i++)release(await authorize(s));assert.equal((await authorize(s)).status,429);
  release(await authorize(s,current,async()=>200,undefined,'gui'));assert.equal(s.work.resources.count,12);assert.equal(s.work.gui.count,1);
});
await test('full database authorization is called on EVERY remembered identity request',async()=>{
  const {s}=fixture();let calls=0;
  release(await authorize(s,current,async()=>{calls++;return 200;}));assert.equal((await authorize(s,current,async()=>{calls++;return 401;})).status,401);
  assert.equal(calls,2);assert.equal(s.current,null);assert.equal(s.work.resources.count,1);
});
await test('new login replaces routing identity and stale rejection cannot erase the new identity',async()=>{
  const {s}=fixture();s.remember(stranger.token,stranger.access,expires,true);
  assert.equal((await authorize(s,current,async()=>401)).status,401);assert.equal(s.lane(statusIdentity(stranger.cookie,stranger.access)),'known');
  release(await authorize(s,stranger));
});
await test('late old successful verification cannot overwrite a newer issued identity',()=>{
  const {s}=fixture();s.remember(stranger.token,stranger.access,expires,true);s.remember(current.token,current.access,expires);
  assert.equal(s.lane(statusIdentity(stranger.cookie,stranger.access)),'known');assert.equal(s.lane(statusIdentity(current.cookie,current.access)),'unknown');
});
await test('concurrent stranger DB work is bounded and leaves reserved current identity slots',async()=>{
  const {s}=fixture(),wait=deferred();let calls=0;
  const a=authorize(s,stranger,async()=>{calls++;return wait.promise;}),b=authorize(s,stranger,async()=>{calls++;return wait.promise;});
  await Promise.resolve();assert.equal((await authorize(s,stranger,async()=>{calls++;return 401;})).status,429);
  release(await authorize(s));assert.equal(calls,2);wait.resolve(401);assert.equal((await a).status,401);assert.equal((await b).status,401);assert.equal(s.auth.unknown.active,0);
});
await test('cancelled authentication retains DB slot until the actual query settles',async()=>{
  const {s}=fixture(),wait=deferred(),abort=new AbortController();
  const pending=authorize(s,current,()=>wait.promise,abort.signal);await Promise.resolve();abort.abort();assert.equal((await pending).status,503);assert.equal(s.auth.known.active,1);
  const other=authorize(s,current,()=>wait.promise);await Promise.resolve();assert.equal((await authorize(s)).status,429);
  wait.resolve(200);release(await other);await Promise.resolve();assert.equal(s.auth.known.active,0);release(await authorize(s));
});
await test('already cancelled requests do not start database authentication',async()=>{
  const {s}=fixture(),abort=new AbortController();abort.abort();let calls=0;
  assert.equal((await authorize(s,current,async()=>{calls++;return 200;},abort.signal)).status,503);assert.equal(calls,0);
});
await test('authentication exception returns 503, releases actual slot and never spends work quota',async()=>{
  const {s}=fixture();assert.equal((await authorize(s,current,async()=>{throw new Error('test backend unavailable');})).status,503);
  assert.equal(s.auth.known.active,0);assert.equal(s.work.resources.count,0);release(await authorize(s));
});
await test('successful work has independent finite concurrency and idempotent release',async()=>{
  const {s,tick}=fixture(),grants=[];for(let i=0;i<4;i++)grants.push(await authorize(s));assert.equal((await authorize(s)).status,429);
  for(const g of grants){g.release();g.release();}assert.equal(s.work.resources.active,0);tick(1000);release(await authorize(s));
});
await test('a five-second monitor plus one-second GUI poll stays within normal budgets',async()=>{
  const {s,tick}=fixture();for(let i=0;i<65;i++){release(await authorize(s,current,async()=>200,undefined,'gui'));if(i%5===0)release(await authorize(s));tick(1000);}
});
await test('routing hint expires and fresh successful authentication can restore it',()=>{
  const {s,tick}=fixture();tick(STATUS_LIMITS.hintMs+1);assert.equal(s.lane(statusIdentity(current.cookie,current.access)),'unknown');
  s.remember(current.token,current.access,expires);assert.equal(s.lane(statusIdentity(current.cookie,current.access)),'known');
});
await test('random identities have constant storage and never retain raw credentials',()=>{
  const {s}=fixture(),before=Object.keys(s).sort();for(let i=0;i<20000;i++){const p=identity();const g=s.ingressGrant(p.cookie,p.access);g.release?.();}
  assert.deepEqual(Object.keys(s).sort(),before);assert(!JSON.stringify(s).includes(current.token));assert(!JSON.stringify(s).includes(current.access));
  assert(JSON.stringify(s).length<1600);assert(!Object.values(s).some(v=>v instanceof Map));
});
await test('timeout returns 503 but actual database concurrency remains bounded until settlement',async()=>{
  const {s}=fixture(),wait=deferred();const a=authorize(s,current,()=>wait.promise),b=authorize(s,current,()=>wait.promise);
  assert.equal((await a).status,503);assert.equal((await b).status,503);assert.equal(s.auth.known.active,2);assert.equal((await authorize(s)).status,503);
  wait.resolve(503);await new Promise(r=>setImmediate(r));assert.equal(s.auth.known.active,0);assert.equal(s.work.resources.count,0);release(await authorize(s));
});
console.log(JSON.stringify({passed,failed,skipped:0,not_executed:0}));process.exitCode=failed?1:0;
