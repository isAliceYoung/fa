// Deterministic gateway-side rules of the explicit text-only clipboard.
import assert from 'node:assert/strict';
import { CLIPBOARD_LIMITS, ClipboardRate, clipboardStatus, parseClipboardBody, textProblem, validReply } from '../src/lib/text-clipboard.mjs';

let passed = 0, failed = 0;
function test(name, fn) { try { fn(); passed++; console.log('PASS', name); } catch (e) { failed++; console.log('FAIL', name, e.message); } }
const sid = 'a'.repeat(24);

test('plain unicode text is accepted unchanged', () => {
  for (const t of ['Hello 123', '简体中文', '繁體中文', '中English，。！', 'a\tb\nc\r\nd', '😀👍🏽🇨🇳 𠮷', '"\'\\<tag> $(id)', '/opt/webdesktop/app/.env'])
    assert.deepEqual(parseClipboardBody(JSON.stringify({ sid, op: 'write', text: t })), { sid, op: 'write', text: t });
});
test('exact 16 KiB boundary and oversize rejection without truncation', () => {
  const exact = '汉'.repeat(5461) + 'a';
  assert.equal(Buffer.byteLength(exact), CLIPBOARD_LIMITS.maxBytes);
  assert.equal(textProblem(exact), null);
  assert.equal(textProblem(exact + 'a'), 'TOO_LARGE');
  assert.equal(parseClipboardBody(JSON.stringify({ sid, op: 'write', text: exact + 'a' })).error, 'TOO_LARGE');
});
test('empty, lone surrogate and control characters rejected', () => {
  assert.equal(textProblem(''), 'EMPTY');
  assert.equal(textProblem('\ud800x'), 'INVALID_UTF8');
  for (const t of ['a\u0000', 'a\u001b[31m', '\u0007', '\u007f', 'a\u0085']) assert.equal(textProblem(t), 'CONTROL_CHARACTERS');
});
test('only exact read/write shapes; no path, command, display or MIME fields', () => {
  assert.deepEqual(parseClipboardBody(JSON.stringify({ sid, op: 'read' })), { sid, op: 'read' });
  for (const body of [{ sid, op: 'read', path: '/etc/passwd' }, { sid, op: 'read', text: 'x' }, { sid, op: 'exec', text: 'id' },
    { sid, op: 'write' }, { sid, op: 'write', text: 5 }, { sid, op: 'write', text: 'x', mime: 'image/png' }, { sid: 'xyz', op: 'read' },
    { sid, op: 'read', display: ':0' }, [sid], null])
    assert.equal(parseClipboardBody(JSON.stringify(body)).error, 'INVALID_REQUEST', JSON.stringify(body));
  assert.equal(parseClipboardBody('{"sid":').error, 'INVALID_REQUEST');
});
test('worker reply validation: text only on successful read', () => {
  assert.deepEqual(validReply({ t: 'clip', n: 3, ok: true, code: 'OK', text: '你好', bytes: 6 }, 3, 'read'), { ok: true, code: 'OK', text: '你好' });
  assert.deepEqual(validReply({ t: 'clip', n: 3, ok: true, code: 'OK', bytes: 6 }, 3, 'write'), { ok: true, code: 'OK' });
  assert.deepEqual(validReply({ t: 'clip', n: 3, ok: false, code: 'NON_TEXT' }, 3, 'read'), { ok: false, code: 'NON_TEXT' });
  for (const [m, n, op] of [[{ t: 'clip', n: 4, ok: true, code: 'OK', text: 'x' }, 3, 'read'], [{ t: 'clip', n: 3, ok: true, code: 'OK', text: 'x' }, 3, 'write'],
    [{ t: 'clip', n: 3, ok: true, code: 'OK', text: 'a\u001b' }, 3, 'read'], [{ t: 'clip', n: 3, ok: true, code: 'OK', text: 'x', path: '/' }, 3, 'read'],
    [{ t: 'clip', n: 3, ok: false, code: 'SHELL' }, 3, 'read'], [{ t: 'd', n: 3, ok: true, code: 'OK' }, 3, 'write']])
    assert.equal(validReply(m, n, op), null, JSON.stringify(m));
});
test('status mapping is explicit', () => {
  assert.equal(clipboardStatus('OK'), 200); assert.equal(clipboardStatus('TOO_LARGE'), 413); assert.equal(clipboardStatus('NON_TEXT'), 415);
  assert.equal(clipboardStatus('GUI_INACTIVE'), 409); assert.equal(clipboardStatus('RATE_LIMITED'), 429); assert.equal(clipboardStatus('???'), 503);
});
test('per-session rate window', () => {
  let now = 0; const rate = new ClipboardRate(() => now);
  for (let i = 0; i < CLIPBOARD_LIMITS.perWindow; i++) assert.equal(rate.take(), true);
  assert.equal(rate.take(), false);
  now += CLIPBOARD_LIMITS.windowMs + 1; assert.equal(rate.take(), true);
});
console.log(JSON.stringify({ passed, failed, skipped: 0, not_executed: 0 }));
if (failed) process.exitCode = 1;
