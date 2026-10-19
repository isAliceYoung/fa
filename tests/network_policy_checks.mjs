import assert from 'node:assert/strict';
import net from 'node:net';
import { bindAddress } from '../scripts/network-policy.mjs';

delete process.env.BIND_HOST;
assert.equal(bindAddress(), '127.0.0.1');
assert.equal(bindAddress(''), '127.0.0.1');
assert.equal(bindAddress('::1'), '::1');
for (const invalid of ['localhost', ' 127.0.0.1', '127.0.0.1:3000', '::1%lo', '--help', 'example.org', '127.1']) {
  assert.throws(() => bindAddress(invalid));
}
const warnings = [];
const original = console.error;
console.error = message => warnings.push(message);
try { assert.equal(bindAddress('0.0.0.0'), '0.0.0.0'); assert.equal(bindAddress('::'), '::'); }
finally { console.error = original; }
assert.equal(warnings.length, 2);
const listener = net.createServer(socket => socket.end());
await new Promise(resolve => listener.listen(0, bindAddress(), resolve));
assert.equal(listener.address().address, '127.0.0.1');
assert.equal(listener.address().family, 'IPv4');
await new Promise(resolve => listener.close(resolve));
console.log('Network policy and real default listener passed');
