import assert from 'node:assert/strict';
import { boundedJson, guestRequest, MAX_BODY } from '../src/lib/guest-client.mjs';

assert.deepEqual(await boundedJson(new Request('http://localhost/', { method: 'POST', body: '{"name":"hello"}' })), { name: 'hello' });
await assert.rejects(() => boundedJson(new Request('http://localhost/', { method: 'POST', body: '[]' })));
await assert.rejects(() => boundedJson(new Request('http://localhost/', { method: 'POST', body: '{' })));
await assert.rejects(() => boundedJson(new Request('http://localhost/', { method: 'POST', body: 'x'.repeat(MAX_BODY + 1) })), RangeError);
let cancelled = false;
await assert.rejects(() => boundedJson(new Request('http://localhost/', { method: 'POST', body: new ReadableStream({ cancel() { cancelled = true; } }), duplex: 'half' }), 10), { name: 'TimeoutError' });
assert.equal(cancelled, true);
const unknown = await guestRequest('/v1/exec');
assert.equal(unknown.status, 503);
assert.equal((await unknown.json()).code, 'GUEST_UNAVAILABLE');
console.log('Node IPC contract checks passed (real UID/socket tests run separately on Linux).');
