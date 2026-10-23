// Cross-language protocol check without X11 or a VM: the gateway's chunk sender,
// reply validation and assembly talk over a pipe to the real wdgui Bridge
// (xclip replaced by an in-memory clipboard). Verifies field names, ordering,
// backpressure, BUSY and cancel semantics end to end.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { crc32, deflateSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { IMAGE_LIMITS, ImageAssembly, sendImageChunks, validImageReply } from '../src/lib/image-clipboard.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const harness = `
import json, runpy, sys, select, os
m = runpy.run_path(${JSON.stringify(root + 'gui-runtime/image_clipboard.py')})
store = {'data': None}
def fake_write(data, env): store['data'] = bytes(data); return {'ok': True, 'code': 'OK'}
def fake_read(env):
    if store['data'] is None: return {'ok': False, 'code': 'EMPTY'}, None
    code, _ = m['png_info'](store['data'])
    return ({'ok': False, 'code': code}, None) if code else ({'ok': True, 'code': 'OK'}, store['data'])
m['write'].__globals__['write'] = fake_write
m['read'].__globals__['read'] = fake_read
bridge = m['Bridge']({})
out = sys.stdout.buffer; buf = b''
while True:
    for reply in bridge.poll(lambda: True): out.write(json.dumps(reply, separators=(',', ':')).encode() + b'\\n')
    out.flush()
    if not select.select([0], [], [], .02)[0]: continue
    chunk = os.read(0, 65536)
    if not chunk: break
    buf += chunk
    if len(buf) > 65536 and b'\\n' not in buf: raise SystemExit('IPC line budget')
    while b'\\n' in buf:
        line, buf = buf.split(b'\\n', 1)
        msg = json.loads(line)
        if msg.get('t') == 'd': continue  # simulated user input interleaved with image chunks
        for reply in bridge.handle(msg): out.write(json.dumps(reply, separators=(',', ':')).encode() + b'\\n')
`;
let passed = 0, failed = 0;
async function test(name, fn) { try { await fn(); passed++; console.log('PASS', name); } catch (e) { failed++; console.log('FAIL', name, e.stack); } }
const chunk = (type, body) => { const t = Buffer.from(type, 'latin1'), len = Buffer.alloc(4), crc = Buffer.alloc(4); len.writeUInt32BE(body.length); crc.writeUInt32BE(crc32(Buffer.concat([t, body])) >>> 0); return Buffer.concat([len, t, body, crc]); };
function png(width, height) {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 6;
  // Incompressible pixels (filter byte 0 per row) so sizes are realistic.
  const raw = randomBytes(height * (1 + width * 4)); for (let r = 0; r < height; r++) raw[r * (1 + width * 4)] = 0;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw, { level: 1 })), chunk('IEND', Buffer.alloc(0))]);
}
const sha = b => createHash('sha256').update(b).digest('hex');

const child = spawn('python3', ['-I', '-c', harness], { stdio: ['pipe', 'pipe', 'inherit'] });
const socket = child.stdin;
let incoming = '', waiters = [];
child.stdout.on('data', d => { incoming += d; let i; while ((i = incoming.indexOf('\n')) >= 0) { const line = incoming.slice(0, i); incoming = incoming.slice(i + 1); assert.ok(line.length < 65536, 'worker line within gateway budget'); const m = JSON.parse(line); const w = waiters.shift(); if (w) w(m); else queue.push(m); } });
const queue = [];
const next = () => queue.length ? Promise.resolve(queue.shift()) : new Promise(r => waiters.push(r));
let n = 0;

async function write(data) {
  const id = ++n;
  socket.write(JSON.stringify({ t: 'img', op: 'write', n: id, size: data.length, sha256: sha(data) }) + '\n');
  const sending = sendImageChunks(socket, id, data);
  const input = setInterval(() => socket.write(JSON.stringify({ t: 'd', d: 'AAAA' }) + '\n'), 2);
  const reply = validImageReply(await next(), id, 'write');
  clearInterval(input); await sending;
  return reply;
}
async function read() {
  const id = ++n;
  socket.write(JSON.stringify({ t: 'img', op: 'read', n: id }) + '\n');
  const head = validImageReply(await next(), id, 'read');
  assert.ok(head, 'valid header');
  if (!head.ok || !head.size) return { code: head.code };
  const a = new ImageAssembly(id, head.size, head.sha256);
  while (!a.complete) assert.equal(a.add(await next()), true, 'sequential valid chunk');
  return a.result();
}

await test('empty clipboard read', async () => assert.equal((await read()).code, 'EMPTY'));
await test('small transparent PNG round trip', async () => {
  const data = png(3, 2);
  assert.deepEqual(await write(data), { ok: true, code: 'OK' });
  const r = await read(); assert.equal(r.code, 'OK'); assert.ok(r.data.equals(data));
});
await test('near-limit PNG round trip with interleaved input', async () => {
  const data = png(1000, 1000);
  assert.ok(data.length > 3.5 * 1024 * 1024 && data.length <= IMAGE_LIMITS.maxBytes, String(data.length));
  assert.deepEqual(await write(data), { ok: true, code: 'OK' });
  const r = await read(); assert.equal(r.code, 'OK'); assert.ok(r.data.equals(data));
});
await test('digest mismatch never reaches the clipboard', async () => {
  const before = (await read()).data, bad = png(5, 5), id = ++n;
  socket.write(JSON.stringify({ t: 'img', op: 'write', n: id, size: bad.length, sha256: '0'.repeat(64) }) + '\n');
  await sendImageChunks(socket, id, bad);
  assert.deepEqual(validImageReply(await next(), id, 'write'), { ok: false, code: 'INVALID_IMAGE' });
  assert.ok((await read()).data.equals(before));
});
await test('cancel mid-upload frees the slot; late chunks are ignored', async () => {
  const data = png(400, 400), id = ++n;
  socket.write(JSON.stringify({ t: 'img', op: 'write', n: id, size: data.length, sha256: sha(data) }) + '\n');
  let sent = 0;
  await sendImageChunks(socket, id, data, { aborted: () => ++sent > 5 });
  socket.write(JSON.stringify({ t: 'img', op: 'cancel', n: id }) + '\n');
  socket.write(JSON.stringify({ t: 'imgc', n: id, i: 5, d: data.subarray(5 * IMAGE_LIMITS.chunk, 6 * IMAGE_LIMITS.chunk).toString('base64') }) + '\n');
  const r = await read(); assert.equal(r.code, 'OK'); assert.notEqual(r.data.length, data.length);
});
child.stdin.end();
await new Promise(r => child.on('close', r));
console.log(JSON.stringify({ passed, failed, skipped: 0, not_executed: 0 }));
if (failed) process.exitCode = 1;
