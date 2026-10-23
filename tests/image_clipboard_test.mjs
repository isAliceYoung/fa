// Deterministic gateway-side rules of the explicit PNG image clipboard.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createHash } from 'node:crypto';
import { crc32, deflateSync } from 'node:zlib';
import { IMAGE_LIMITS, ImageAssembly, ImageRate, imageStatus, parseImageQuery, pngInfo, sendImageChunks, validImageReply } from '../src/lib/image-clipboard.mjs';

let passed = 0, failed = 0;
async function test(name, fn) { try { await fn(); passed++; console.log('PASS', name); } catch (e) { failed++; console.log('FAIL', name, e.message); } }
const chunk = (type, body) => { const t = Buffer.from(type, 'latin1'), len = Buffer.alloc(4), crc = Buffer.alloc(4); len.writeUInt32BE(body.length); crc.writeUInt32BE(crc32(Buffer.concat([t, body])) >>> 0); return Buffer.concat([len, t, body, crc]); };
function png({ width = 4, height = 3, color = 6, depth = 8, extra = [], raw, palette } = {}) {
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[color];
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = depth; ihdr[9] = color;
  raw ??= Buffer.alloc(height * (1 + Math.ceil(width * channels * depth / 8)));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr),
    ...(color === 3 ? [chunk('PLTE', palette ?? Buffer.from([0, 0, 0, 255, 255, 255]))] : []), ...extra,
    chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
const sha = b => createHash('sha256').update(b).digest('hex');

await test('valid PNG kinds and dimensions', () => {
  for (const o of [{}, { color: 2 }, { color: 0, depth: 1 }, { color: 3, depth: 4 }, { color: 6, depth: 16 }, { width: 4096, height: 1, color: 0, depth: 1 }, { extra: [chunk('tEXt', Buffer.from('a\0b'))] }])
    assert.deepEqual(pngInfo(png(o)), { width: o.width ?? 4, height: o.height ?? 3, bytes: png(o).length }, JSON.stringify(o));
});
await test('size and pixel limits', () => {
  assert.ok(IMAGE_LIMITS.maxBytes < 1024 * 1024, 'stay below the measured xclip INCR threshold');
  assert.equal(pngInfo(png({ width: 4097, height: 1, color: 0, depth: 1 })).error, 'TOO_LARGE');
  assert.equal(pngInfo(png({ width: 4096, height: 2048, color: 0, depth: 1 })).error, 'TOO_LARGE');
  assert.equal(pngInfo(Buffer.alloc(IMAGE_LIMITS.maxBytes + 1)).error, 'TOO_LARGE');
});
await test('corrupt, truncated, mismatched and animated PNG rejected', () => {
  const good = png();
  assert.equal(pngInfo(good.subarray(0, good.length - 1)).error, 'INVALID_IMAGE');
  assert.equal(pngInfo(Buffer.concat([good, Buffer.from('x')])).error, 'INVALID_IMAGE');
  const badCrc = Buffer.from(good); badCrc[30] ^= 1;
  assert.equal(pngInfo(badCrc).error, 'INVALID_IMAGE');
  assert.equal(pngInfo(Buffer.concat([Buffer.from('GIF89a'), good.subarray(6)])).error, 'INVALID_IMAGE');
  assert.equal(pngInfo(png({ color: 2, depth: 3 })).error, 'INVALID_IMAGE');
  assert.equal(pngInfo(png({ color: 3, palette: Buffer.alloc(0) })).error, 'INVALID_IMAGE');
  assert.equal(pngInfo(png({ extra: [chunk('ABCD', Buffer.alloc(0))] })).error, 'INVALID_IMAGE');
  assert.equal(pngInfo(png({ extra: [chunk('acTL', Buffer.alloc(8))] })).error, 'UNSUPPORTED_FORMAT');
  assert.equal(pngInfo('not a buffer').error, 'INVALID_IMAGE');
});
await test('query is exactly sid + op', () => {
  const sid = 'a'.repeat(24);
  assert.deepEqual(parseImageQuery(`/api/gui/image-clipboard?sid=${sid}&op=read`), { sid, op: 'read' });
  assert.deepEqual(parseImageQuery(`/api/gui/image-clipboard?op=write&sid=${sid}`), { sid, op: 'write' });
  for (const q of [`?sid=${sid}`, `?sid=${sid}&op=exec`, `?sid=xyz&op=read`, `?sid=${sid}&op=read&path=/etc/passwd`, `?sid=${sid}&sid=${sid}&op=read`, `?sid=${sid}&op=read&display=:0`])
    assert.equal(parseImageQuery('/api/gui/image-clipboard' + q), null, q);
  assert.equal(parseImageQuery(`/api/gui/image-clipboard/x?sid=${sid}&op=read`), null);
});
await test('worker reply validation', () => {
  assert.deepEqual(validImageReply({ t: 'img', n: 3, ok: true, code: 'OK', size: 10, sha256: 'a'.repeat(64) }, 3, 'read'), { ok: true, size: 10, sha256: 'a'.repeat(64) });
  assert.deepEqual(validImageReply({ t: 'img', n: 3, ok: true, code: 'OK' }, 3, 'write'), { ok: true, code: 'OK' });
  assert.deepEqual(validImageReply({ t: 'img', n: 3, ok: false, code: 'NON_IMAGE' }, 3, 'read'), { ok: false, code: 'NON_IMAGE' });
  for (const [m, op] of [[{ t: 'img', n: 4, ok: true, code: 'OK' }, 'write'], [{ t: 'img', n: 3, ok: true, code: 'OK', size: IMAGE_LIMITS.maxBytes + 1, sha256: 'a'.repeat(64) }, 'read'],
    [{ t: 'img', n: 3, ok: true, code: 'OK', size: 10, sha256: 'a'.repeat(64), path: '/' }, 'read'], [{ t: 'img', n: 3, ok: false, code: 'SHELL' }, 'read'],
    [{ t: 'img', n: 3, ok: false, code: 'OK' }, 'write'], [{ t: 'img', n: 3, ok: true, code: 'OK', size: 10, sha256: 'a'.repeat(64) }, 'write']])
    assert.equal(validImageReply(m, 3, op), null, JSON.stringify(m));
});
await test('assembly: order, bound, digest and PNG check', () => {
  const data = png({ width: 200, height: 200, raw: Buffer.from(Array.from({ length: 200 * (1 + 800) }, (_, i) => i % 251 % 5 === 0 ? 0 : i % 251)) });
  const parts = []; for (let o = 0; o < data.length; o += IMAGE_LIMITS.chunk) parts.push(data.subarray(o, o + IMAGE_LIMITS.chunk).toString('base64'));
  const a = new ImageAssembly(5, data.length, sha(data));
  assert.equal(a.add({ t: 'imgc', n: 5, i: 1, d: parts[1] ?? parts[0] }), false);
  parts.forEach((d, i) => assert.equal(a.add({ t: 'imgc', n: 5, i, d }), true));
  assert.equal(a.complete, true);
  const r = a.result(); assert.equal(r.code, 'OK'); assert.ok(r.data.equals(data));
  const wrong = new ImageAssembly(6, data.length, '0'.repeat(64)); parts.forEach((d, i) => wrong.add({ t: 'imgc', n: 6, i, d }));
  assert.equal(wrong.result().code, 'INVALID_IMAGE');
  const over = new ImageAssembly(7, 3, sha(Buffer.from('abc')));
  assert.equal(over.add({ t: 'imgc', n: 7, i: 0, d: Buffer.from('abcd').toString('base64') }), false);
  assert.equal(over.add({ t: 'imgc', n: 7, i: 0, d: 'ab==', x: 1 }), false);
});
await test('chunk sender keeps IPC buffer under the input budget and stops on abort', async () => {
  class FakeSocket extends EventEmitter {
    constructor() { super(); this.writableLength = 0; this.destroyed = false; this.lines = []; this.maxBuffered = 0; }
    write(line) {
      assert.equal(this.writableLength, 0, 'never writes before the previous chunk drained');
      this.lines.push(line); this.writableLength += Buffer.byteLength(line); this.maxBuffered = Math.max(this.maxBuffered, this.writableLength);
      setTimeout(() => { this.writableLength = 0; this.emit('drain'); }, 1);
      return false;
    }
  }
  const data = Buffer.alloc(IMAGE_LIMITS.maxBytes, 7), socket = new FakeSocket();
  assert.equal(await sendImageChunks(socket, 9, data), true);
  assert.equal(socket.lines.length, Math.ceil(data.length / IMAGE_LIMITS.chunk));
  // Worst case: one image line plus the largest 16 KiB input frame (~22 KiB) stays below the 64 KiB fail-closed input budget.
  assert.ok(socket.maxBuffered + 22000 < 65536, String(socket.maxBuffered));
  const joined = Buffer.concat(socket.lines.map(l => { const m = JSON.parse(l); assert.equal(m.n, 9); return Buffer.from(m.d, 'base64'); }));
  assert.ok(joined.equals(data));
  const s2 = new FakeSocket(); let n = 0;
  assert.equal(await sendImageChunks(s2, 10, data, { aborted: () => ++n > 3 }), false);
  assert.ok(s2.lines.length <= 3);
});
await test('status mapping and per-session rate', () => {
  assert.equal(imageStatus('OK'), 200); assert.equal(imageStatus('TOO_LARGE'), 413); assert.equal(imageStatus('NON_IMAGE'), 415);
  assert.equal(imageStatus('UNSUPPORTED_FORMAT'), 415); assert.equal(imageStatus('INVALID_IMAGE'), 400); assert.equal(imageStatus('???'), 503);
  let now = 0; const rate = new ImageRate(() => now);
  for (let i = 0; i < IMAGE_LIMITS.perWindow; i++) assert.equal(rate.take(), true);
  assert.equal(rate.take(), false); now += IMAGE_LIMITS.windowMs + 1; assert.equal(rate.take(), true);
});
console.log(JSON.stringify({ passed, failed, skipped: 0, not_executed: 0 }));
if (failed) process.exitCode = 1;
