// C2 acceptance: explicit PNG image clipboard between the real local browser and
// the restricted native GUI (host Chrome -> noVNC -> wdgui X11 -> Chromium).
// Images are generated test fixtures; evidence keeps only sizes, hashes, codes
// and pixel digests. Three facts are recorded separately for every direction:
// remote copy (X11 holds image/png), bridge (bytes identical across the API),
// application (a real Chromium paste decodes identical pixels).
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import http from 'node:http';
import { crc32, deflateSync } from 'node:zlib';
import { chromium } from 'playwright-core';
let input = ''; for await (const p of process.stdin) input += p; const c = JSON.parse(input);
const origin = fs.readFileSync(new URL('./download_bridge_origin.py', import.meta.url), 'utf8');
const FIXTURE = origin.split("C2_FIXTURE='''")[1].split("'''")[0];
const browser = await chromium.launch({ executablePath: c.chrome, headless: true, chromiumSandbox: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' });
await context.addInitScript({ content: FIXTURE + ';globalThis.c2Fixture=c2Fixture;globalThis.c2Pixels=c2Pixels;' });
await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: c.url });
const page = await context.newPage(); page.setDefaultTimeout(20000);
const planned = c.after_reboot ? ['after actual reboot: PNG web to remote Chromium and back'] : [
  'web to remote Chromium PNG with alpha (remote copy, bridge, application)', 'remote Chromium PNG copy to web (remote copy, bridge, application)',
  'exact 1000 KiB and 3840x2160 accepted byte-identical; one byte, side or pixel over rejected',
  'unsupported remote clipboard rejected (text, JPEG, APNG, files, corrupt, oversize, huge dimensions)',
  'local invalid images rejected without touching the remote clipboard', 'browser clipboard permission denied uses explicit paste area and preview',
  'API authentication, session binding and request shape', 'one clipboard operation at a time; image rate limit',
  'image bytes never logged, stored or executed; bridge only as wdgui', 'text and image operations alternate correctly',
  'GUI close and reopen invalidate the image bridge; no bridge processes remain', 'login revocation revokes the image bridge',
  'resource use bounded and released'];
const report = { kind: 'c2-image-clipboard', passed: 0, failed: 0, skipped: 0, not_executed: planned.length, tests: [], evidence: [] };
const token = randomBytes(6).toString('hex'), started = Date.now();
const ssh = (cmd, body, timeout = 30000) => JSON.parse(execFileSync('ssh', [...c.ssh, cmd], { encoding: 'utf8', input: body ? JSON.stringify(body) : undefined, timeout, stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 1 << 20 }));
const observer = (action, body, timeout) => ssh('sudo python3 -I /root/download-bridge-vm.py ' + action, body, timeout);
const cv = (action, body, timeout = 30000) => ssh('sudo python3 -I /root/clipboard-vm.py ' + action, body ?? {}, timeout);
const probe = topic => ssh('sudo python3 /root/staging-ui-probe.py ' + topic);
const sha = b => createHash('sha256').update(b).digest('hex');
const gui = () => page.getByTestId('basic-gui'), canvas = () => gui().locator('canvas');
let sid = '', access = '';
page.on('websocket', s => { const u = new URL(s.url()); if (u.pathname === '/api/gui/ws') { sid = u.searchParams.get('sid'); access = u.searchParams.get('access'); } });
async function until(fn, timeout = 30000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await fn()) return; await page.waitForTimeout(250); } throw Error('Bounded observable state timeout'); }
async function test(name, fn) {
  assert(planned.includes(name)); report.not_executed--;
  try { await fn(); report.passed++; report.tests.push({ name, result: 'passed' }); }
  catch (e) {
    report.failed++; report.tests.push({ name, result: 'failed', detail: String(e.stack).split(c.password).join('[REDACTED]').split(token).join('[TOKEN]').slice(0, 1500) });
    await page.screenshot({ path: c.out + '/c2-failure-' + (c.after_reboot ? 'reboot-' : '') + report.failed + '.png' }).catch(() => {});
    await page.keyboard.press('Escape').catch(() => {});
    if (await page.getByPlaceholder('访问密码').count()) await login().catch(() => {});
  }
}
async function login(p = page) { await p.getByPlaceholder('访问密码').fill(c.password); await p.getByRole('button', { name: '解锁并接管桌面', exact: true }).click(); await p.getByText('终端', { exact: true }).first().waitFor(); }
async function idle() { await until(() => observer('execution-idle').idle, 30000); }
async function close() { if (await gui().count()) await gui().locator('xpath=../..').getByRole('button', { name: '关闭', exact: true }).click(); await idle(); }
async function open(app) {
  await page.getByText(app === 'browser' ? '浏览器' : 'PCManFM', { exact: true }).first().dblclick();
  for (let attempt = 0; ; attempt++) {  // existing guard: at most 4 GUI starts per 10 s
    await until(async () => (await gui().count()) > 0 && ['open', 'closed'].includes(await gui().getAttribute('data-status')), 60000);
    if ((await gui().getAttribute('data-status')) === 'open') break;
    if (attempt >= 2) throw Error('GUI did not open');
    await page.waitForTimeout(10500);
    if ((await gui().getAttribute('data-status')) === 'closed') await gui().getByRole('button', { name: '重新打开', exact: true }).click();
  }
  await until(() => probe('state').windows.some(w => app === 'browser' ? w.app === 'chromium' : w.app === '[Pp]cmanfm'), 45000);
}
async function ensure(app, prepare) {
  const ready = async () => (await gui().count()) > 0 && (await gui().getAttribute('data-status')) === 'open' &&
    probe('state').windows.some(w => app === 'browser' ? w.app === 'chromium' : w.app === '[Pp]cmanfm');
  if (await ready()) return;
  if (await gui().count()) await close();
  await open(app); if (prepare) await prepare();
}
const titled = (needle, timeout = 20000) => until(() => cv('title-match', { needle, class: 'chromium' }).match, timeout);
async function navigate(url, ready) {
  for (let attempt = 1; ; attempt++) {
    for (let a = 1; ; a++) {
      await canvas().click({ position: { x: 450, y: 63 } }); await page.keyboard.press('Control+l'); await page.waitForTimeout(250); await page.keyboard.press('Control+a');
      await page.keyboard.type(url, { delay: 8 }); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+c');
      try { await until(() => probe('address').sha256 === sha(url), 10000); break; } catch (e) { if (a >= 3) throw e; await page.keyboard.press('Escape'); }
    }
    await page.keyboard.press('Enter');
    try { await titled(ready, 20000); return; } catch (e) { if (attempt >= 3) throw e; await page.keyboard.press('Escape'); }
  }
}
const task = () => randomBytes(16).toString('hex');
const imgTarget = () => navigate('https://203.0.113.2/img?task=' + task(), 'C2 Ready');
// Local browser helpers (fixture code injected by addInitScript; no eval in the app page).
const localPixels = kind => page.evaluate(async k => c2Pixels(await c2Fixture(k)), kind);
const setLocalImage = kind => page.evaluate(async k => { const b = await c2Fixture(k); await navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]); return c2Pixels(b); }, kind);
const setLocalText = text => page.evaluate(t => navigator.clipboard.writeText(t), text);
const readLocalImage = () => page.evaluate(async () => { const items = await navigator.clipboard.read(); const item = items.find(i => i.types.includes('image/png')); return item ? c2Pixels(await item.getType('image/png')) : 'none'; });
async function imageApi(op, bytes, { contentType = 'image/png', query, headers = {} } = {}) {
  return page.evaluate(async ({ op, b64, contentType, query, headers, sid }) => {
    const body = b64 === null ? undefined : Uint8Array.from(atob(b64), x => x.charCodeAt(0));
    const r = await fetch('/api/gui/image-clipboard' + (query ?? ('?sid=' + sid + '&op=' + op)), { method: 'POST', headers: { ...(body ? { 'Content-Type': contentType } : {}), ...headers }, body });
    const data = new Uint8Array(await r.arrayBuffer()), ct = r.headers.get('content-type');
    let code = null; if (ct?.startsWith('application/json')) code = JSON.parse(new TextDecoder().decode(data)).code;
    const hex = ct === 'image/png' ? Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), x => x.toString(16).padStart(2, '0')).join('') : null;
    return { status: r.status, code, ct, sha256: hex, bytes: data.length };
  }, { op, b64: bytes ? Buffer.from(bytes).toString('base64') : null, contentType, query, headers, sid });
}
// Same authenticated session as the page, sent from the host through the tunnel:
// headers plus the first half of the body; the rest is sent by finish().
async function heldUpload(bytes) {
  const host = new URL(c.url).hostname, cookie = (await context.cookies()).filter(k => k.domain.replace(/^\./, '') === host).map(k => k.name + '=' + k.value).join('; ');
  const req = http.request(c.url + '/api/gui/image-clipboard?sid=' + sid + '&op=write', { method: 'POST', agent: false,
    headers: { Cookie: cookie, 'X-Desktop-Session': access, 'Content-Type': 'image/png', 'Content-Length': bytes.length } });
  const response = new Promise((resolve, reject) => { req.on('error', reject); req.on('response', r => { let b = ''; r.on('data', d => b += d); r.on('end', () => resolve({ status: r.statusCode, body: b })); }); });
  req.write(bytes.subarray(0, bytes.length >> 1)); await new Promise(r => setTimeout(r, 1500));
  return { finish: async () => { req.end(bytes.subarray(bytes.length >> 1)); return response; } };
}
// Respect the designed 6-per-60 s image window in unrelated cases.
async function image(op, bytes, opts) { let r = await imageApi(op, bytes, opts); for (let i = 0; i < 2 && r.status === 429; i++) { await page.waitForTimeout(61000); r = await imageApi(op, bytes, opts); } return r; }
async function uiButton(id) {
  for (let attempt = 0; ; attempt++) {
    if (await page.getByTestId('gui-img-close').count()) await page.getByTestId('gui-img-close').click();
    if (await page.getByTestId('gui-clip-close').count()) await page.getByTestId('gui-clip-close').click();
    const before = await page.getByTestId('gui-clip-notice').count() ? await page.getByTestId('gui-clip-notice').textContent() : null;
    await page.getByTestId(id).click();
    await until(async () => before === null || !(await page.getByTestId('gui-clip-notice').count()) || (await page.getByTestId('gui-clip-notice').textContent()) !== before || await page.getByTestId(id).isDisabled(), 3000).catch(() => {});
    await until(async () => (await page.getByTestId('gui-clip-notice').count()) > 0 && !(await page.getByTestId(id).isDisabled()), 60000);
    const code = await page.getByTestId('gui-clip-notice').getAttribute('data-code');
    if (code !== 'RATE_LIMITED' || attempt >= 2) return code;
    await page.waitForTimeout(61000);
  }
}
const chunk = (type, body) => { const t = Buffer.from(type, 'latin1'), len = Buffer.alloc(4), crc = Buffer.alloc(4); len.writeUInt32BE(body.length); crc.writeUInt32BE(crc32(Buffer.concat([t, body])) >>> 0); return Buffer.concat([len, t, body, crc]); };
function png(width, height, { pad = 0, extra = [] } = {}) {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 6;
  const idat = chunk('IDAT', deflateSync(Buffer.alloc(height * (1 + width * 4)), { level: 9 }));
  const head = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), ...extra]);
  const tail = Buffer.concat([idat, chunk('IEND', Buffer.alloc(0))]);
  const padding = pad ? [chunk('zPad', randomBytes(pad - head.length - tail.length - 12))] : [];
  return Buffer.concat([head, ...padding, tail]);
}
const remote = () => cv('image-hash', {}, 120000);

try {
  await page.goto(c.url, { waitUntil: 'domcontentloaded' }); await login();
  const baseline = cv('protected');
  if (c.after_reboot) {
    await test(planned[0], async () => {
      // The D1 test origin is a transient service and does not survive the
      // reboot, so this proves the bridge round trip through X11 only.
      await ensure('browser');
      const px = await setLocalImage('alpha'); assert.equal(await uiButton('gui-img-paste'), 'OK');
      const held = remote(); assert.ok(held.present, 'remote X11 holds image/png');
      await setLocalImage('small'); assert.equal(await uiButton('gui-img-copy'), 'OK');
      assert.equal(await readLocalImage(), px, 'web -> X11 -> web keeps identical pixels');
      const back = await image('read'); assert.equal(back.sha256, held.sha256);
      report.evidence.push({ after_reboot: true, pixels: px, bytes: held.bytes });
      await close();
    });
  } else {
    await test(planned[0], async () => {
      await ensure('browser'); await imgTarget();
      for (const kind of ['alpha', 'wide']) {
        const px = await setLocalImage(kind);
        assert.equal(await uiButton('gui-img-paste'), 'OK');
        const held = remote(); assert.ok(held.present, 'remote copy: X11 CLIPBOARD holds image/png');
        await canvas().click({ position: { x: 500, y: 350 } }); await page.keyboard.press('Control+v');
        await titled('C2 ' + px);  // application: native Chromium decoded identical pixels
        report.evidence.push({ direction: 'web->remote', kind, pixels: px, remote_bytes: held.bytes, remote_sha256: held.sha256 });
      }
    });
    await test(planned[1], async () => {
      await ensure('browser');
      for (const kind of ['alpha', 'small']) {
        const expected = await localPixels(kind);
        await navigate('https://203.0.113.2/img-src?kind=' + kind + '&task=' + task(), 'C2 Source ' + expected);
        await canvas().click({ position: { x: 600, y: 400 } }); await titled('C2 Copied ' + expected);
        const held = remote(); assert.ok(held.present, 'remote copy: Chromium put image/png on X11');
        await setLocalImage(kind === 'alpha' ? 'wide' : 'alpha');  // sentinel
        assert.equal(await uiButton('gui-img-copy'), 'OK');
        assert.equal(await readLocalImage(), expected, 'application: local clipboard decodes identical pixels');
        const bridged = await image('read'); assert.equal(bridged.status, 200);
        assert.equal(bridged.sha256, held.sha256, 'bridge: bytes identical to X11');
        report.evidence.push({ direction: 'remote->web', kind, pixels: expected, bytes: held.bytes, sha256: held.sha256 });
      }
    });
    await test(planned[2], async () => {
      await ensure('browser');
      // Measured, not assumed: how long the private X server needs to move N bytes between two xclip clients.
      const bench = cv('xclip-bench', { sizes: [65536, 524288, 1024000, 1048576] }, 300000);
      report.evidence.push({ xclip_bench: bench.runs });
      const exact = png(2, 2, { pad: 1000 * 1024 }); assert.equal(exact.length, 1000 * 1024);
      const w = await image('write', exact); assert.equal(w.status, 200, JSON.stringify(w));
      assert.equal(remote().sha256, sha(exact), 'bridge: exact 1000 KiB byte-identical in X11');
      const r = await image('read'); assert.equal(r.status, 200); assert.equal(r.sha256, sha(exact)); assert.equal(r.bytes, exact.length);
      const uhd = png(3840, 2160); assert.equal((await image('write', uhd)).status, 200); assert.equal(remote().sha256, sha(uhd));
      const before = remote();
      assert.equal((await image('write', Buffer.concat([exact, Buffer.alloc(1)]))).status, 413);
      assert.equal((await image('write', png(4097, 1))).status, 413);
      assert.equal((await image('write', png(4096, 2048))).status, 413);
      assert.deepEqual(remote(), before, 'rejected uploads never reach X11');
      report.evidence.push({ boundary: { exact_bytes: exact.length, uhd_bytes: uhd.length } });
    });
    await test(planned[3], async () => {
      await ensure('browser');
      const text = await page.evaluate(async ({ sid }) => (await fetch('/api/gui/text-clipboard', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sid, op: 'write', text: 'C2 only text' }) })).status, { sid });
      assert.equal(text, 200);
      assert.deepEqual([(await image('read')).code], ['NON_IMAGE']);
      for (const [kind, code, status] of [['jpeg', 'UNSUPPORTED_FORMAT', 415], ['apng', 'UNSUPPORTED_FORMAT', 415], ['gnome-files', 'UNSUPPORTED_FORMAT', 415],
        ['uri-list', 'UNSUPPORTED_FORMAT', 415], ['png-corrupt', 'INVALID_IMAGE', 400], ['png-oversize', 'TOO_LARGE', 413], ['png-huge-dims', 'TOO_LARGE', 413]]) {
        assert(cv('set', { kind }).set, kind);
        const r = await image('read'); assert.deepEqual([r.status, r.code], [status, code], kind);
        report.evidence.push({ remote: kind, code });
      }
      // The same through the UI: the local clipboard is never touched.
      assert(cv('set', { kind: 'jpeg' }).set);
      const sentinel = await setLocalImage('small');
      assert.equal(await uiButton('gui-img-copy'), 'UNSUPPORTED_FORMAT');
      assert.equal(await readLocalImage(), sentinel);
    });
    await test(planned[4], async () => {
      await ensure('browser');
      const good = png(8, 8); assert.equal((await image('write', good)).status, 200);
      const before = remote(); assert.equal(before.sha256, sha(good));
      const crc = Buffer.from(good); crc[20] ^= 0xff;
      const cases = [[crc, 'image/png', 400], [good.subarray(0, good.length - 3), 'image/png', 400], [Buffer.concat([Buffer.from('GIF89a'), good.subarray(6)]), 'image/png', 400],
        [png(4, 4, { extra: [chunk('acTL', Buffer.alloc(8))] }), 'image/png', 415], [good, 'image/jpeg', 415], [good, 'text/plain', 415]];
      for (const [bytes, type, status] of cases) assert.equal((await image('write', bytes, { contentType: type })).status, status, type + ' ' + bytes.length);
      assert.deepEqual(remote(), before);
      await setLocalText('C2 only text ' + token);
      assert.equal(await uiButton('gui-img-paste'), 'NON_IMAGE');
      assert.deepEqual(remote(), before);
    });
    await test(planned[5], async () => {
      await ensure('browser'); await imgTarget();
      await page.waitForTimeout(61000);  // earlier cases used this session's image window
      await context.clearPermissions();
      try {
        assert.equal(await uiButton('gui-img-paste'), 'LOCAL_DENIED');
        await page.getByTestId('gui-img-dropzone').waitFor();
        const px = await page.evaluate(async () => {
          const blob = await c2Fixture('wide'), dt = new DataTransfer(); dt.items.add(new File([blob], 'x.png', { type: 'image/png' }));
          document.querySelector('[data-testid="gui-img-dropzone"]').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
          return c2Pixels(blob);
        });
        await until(async () => (await page.getByTestId('gui-clip-notice').getAttribute('data-code')) === 'OK', 30000);
        await canvas().click({ position: { x: 500, y: 350 } }); await page.keyboard.press('Control+v'); await titled('C2 ' + px);
      } finally { await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: c.url }); }
      // Local write refused (simulated): the remote image is offered as a preview only.
      await page.evaluate(() => { window.__c2write = navigator.clipboard.write; navigator.clipboard.write = () => Promise.reject(new DOMException('denied', 'NotAllowedError')); });
      try {
        assert.equal(await uiButton('gui-img-copy'), 'LOCAL_DENIED');
        assert.match(await page.getByTestId('gui-img-preview').getAttribute('src'), /^blob:/);
        await page.getByTestId('gui-img-close').click();
        assert.equal(await page.getByTestId('gui-img-preview').count(), 0);
      } finally { await page.evaluate(() => { navigator.clipboard.write = window.__c2write; }); }
    });
    await test(planned[6], async () => {
      await ensure('browser');
      const small = png(4, 4);
      assert.equal((await imageApi('read', null, { query: '?sid=' + randomBytes(12).toString('hex') + '&op=read' })).status, 409);
      for (const q of ['?sid=' + sid, '?sid=' + sid + '&op=exec', '?sid=' + sid + '&op=read&path=/etc/passwd', '?sid=' + sid + '&op=read&display=:0', '?sid=xyz&op=read'])
        assert.equal((await imageApi('read', null, { query: q })).status, 400, q);
      assert.equal((await imageApi('read', small)).status, 400, 'read with a body');
      assert.equal((await page.evaluate(async () => (await fetch('/api/gui/image-clipboard?sid=a&op=read')).status)), 405);
      const anon = await browser.newContext();
      assert.equal((await anon.request.post(c.url + '/api/gui/image-clipboard?sid=' + sid + '&op=read')).status(), 401); await anon.close();
      assert.equal((await context.request.post(c.url + '/api/gui/image-clipboard?sid=' + sid + '&op=read', { headers: { 'X-Desktop-Session': 'bad-page-session' } })).status(), 401);
      assert.equal((await context.request.post(c.url + '/api/gui/image-clipboard?sid=' + sid + '&op=write', { headers: { 'X-Desktop-Session': access, Origin: 'https://evil.example', 'Content-Type': 'image/png' }, data: small })).status(), 403);
      assert.equal((await context.request.post(c.url + '/api/gui/image-clipboard?sid=' + sid + '&op=write', { headers: { 'X-Desktop-Session': access, 'Content-Type': 'image/png' }, data: small })).status(), 200);
    });
    await test(planned[7], async () => {
      await ensure('browser');
      await page.waitForTimeout(61000);  // a fresh image window
      const big = png(2, 2, { pad: 1000 * 1024 - 1000 });
      // Server-wide: a second image upload while one is in flight is refused.
      // A 1000 KiB transfer now completes in milliseconds, so the first upload is
      // held in flight deterministically: headers and half the body, then wait.
      const held = await heldUpload(big);
      const second = await imageApi('write', big);
      const first = await held.finish();
      report.evidence.push({ parallel_images: [first.status, second.status], second_code: second.code });
      assert.deepEqual([first.status, second.status], [200, 429]); assert.equal(second.code, 'RATE_LIMITED');
      // Per session: while an image is being transferred, text gets BUSY (checked before the text rate
      // limit, so polling every 10 ms only adds 429s; the 61 s wait below resets both windows).
      const seen = await page.evaluate(async ({ sid, b64 }) => {
        const body = Uint8Array.from(atob(b64), x => x.charCodeAt(0)); let done = false; const statuses = [];
        const upload = fetch('/api/gui/image-clipboard?sid=' + sid + '&op=write', { method: 'POST', headers: { 'Content-Type': 'image/png' }, body }).then(r => { done = true; return r.status; });
        while (!done && statuses.length < 1000) { statuses.push((await fetch('/api/gui/text-clipboard', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sid, op: 'read' }) })).status); await new Promise(r => setTimeout(r, 10)); }
        return { image: await upload, text: statuses };
      }, { sid, b64: big.toString('base64') });
      report.evidence.push({ busy_probe: { image: seen.image, text_409: seen.text.filter(x => x === 409).length, text_total: seen.text.length } });
      assert.equal(seen.image, 200); assert.ok(seen.text.includes(409), 'one clipboard operation per session at a time');
      await page.waitForTimeout(61000);
      const burst = []; for (let i = 0; i < 7; i++) burst.push((await imageApi('write', png(3, 3))).status);
      report.evidence.push({ image_burst: burst });
      assert.deepEqual(burst.slice(0, 6), [200, 200, 200, 200, 200, 200]); assert.equal(burst[6], 429);
      await page.waitForTimeout(61000); assert.equal((await imageApi('write', png(3, 3))).status, 200);
    });
    await test(planned[8], async () => {
      await ensure('browser');
      const now = cv('protected'); assert.deepEqual(now.hashes, baseline.hashes); assert.deepEqual(now.executed, []);
      const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })); assert.ok(!stored.includes('iVBOR'));
      assert.equal(cv('journal-count', { needle: 'iVBORw0KGgo', since: Math.floor(started / 1000) }).count, 0, 'no PNG data in the journal');
      const procs = cv('xclip-procs').processes; assert.ok(procs.length <= 1);
      for (const p of procs) { assert.equal(p.user, 'wdgui'); assert.equal(p.unit, 'webdesktop-gui@1.service'); }
      const denied = cv('x11-denied'); for (const u of ['wdsvc', 'wdguest', 'wdterm']) assert.deepEqual(denied[u], { x11: true, xauth: true }, u);
      const flags = cv('xvnc-flags').servers; assert(flags.length === 1 && flags.every(f => f.accept_cut_text_off && f.send_cut_text_off && f.tcp_off));
    });
    await test(planned[9], async () => {
      await ensure('browser');
      const text = (op, t) => page.evaluate(async ({ sid, op, t }) => { const r = await fetch('/api/gui/text-clipboard', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(op === 'write' ? { sid, op, text: t } : { sid, op }) }); return { status: r.status, ...(await r.json()) }; }, { sid, op, t });
      const a = png(5, 5), b = png(6, 6);
      assert.equal((await text('write', 'C2 中文 ' + token)).status, 200);
      assert.equal((await image('write', a)).status, 200);
      assert.equal((await text('read')).code, 'NON_TEXT');
      assert.equal((await text('write', 'C2 第二段 ' + token)).status, 200);
      assert.equal((await image('read')).code, 'NON_IMAGE');
      assert.equal((await image('write', b)).status, 200);
      assert.equal((await image('read')).sha256, sha(b));
      assert.equal((await text('write', 'C2 最后 ' + token)).status, 200);
      assert.equal((await text('read')).text, 'C2 最后 ' + token);
    });
    await test(planned[10], async () => {
      await ensure('browser');
      assert.equal((await image('write', png(4, 4))).status, 200);
      const old = sid; await close();
      assert.equal((await imageApi('read', null, { query: '?sid=' + old + '&op=read' })).status, 409);
      assert.deepEqual(cv('xclip-procs').processes, [], 'no bridge process survives the GUI');
      await open('browser'); assert.notEqual(sid, old);
      assert.equal((await image('read')).code, 'EMPTY', 'a new X server starts with an empty clipboard');
      const fresh = png(7, 7); assert.equal((await image('write', fresh)).status, 200); assert.equal((await image('read')).sha256, sha(fresh));
    });
    await test(planned[11], async () => {
      await ensure('browser');
      const old = sid, other = await browser.newContext(), p = await other.newPage(); await p.goto(c.url); await login(p);
      assert.equal((await imageApi('read', null, { query: '?sid=' + old + '&op=read' })).status, 401);
      assert.equal((await imageApi('write', png(4, 4), { query: '?sid=' + old + '&op=write' })).status, 401);
      await idle().catch(() => {}); await p.close(); await other.close();
      await page.goto(c.url, { waitUntil: 'domcontentloaded' }); if (await page.getByPlaceholder('访问密码').count()) await login();
    });
    await test(planned[12], async () => {
      await ensure('browser');
      const before = cv('gui-resources');
      for (let i = 0; i < 3; i++) { const bytes = png(1200, 800 + i); assert.equal((await image('write', bytes)).status, 200); assert.equal((await image('read')).sha256, sha(bytes)); }
      const after = cv('gui-resources');
      report.evidence.push({ resources: { before, after } });
      const g0 = before['webdesktop-gui@1.service'], g1 = after['webdesktop-gui@1.service'];
      assert.ok(g1.tasks <= g0.tasks + 3, 'tasks bounded: ' + g0.tasks + '->' + g1.tasks);
      assert.ok(g1.fds <= g0.fds + 12, 'fds bounded: ' + g0.fds + '->' + g1.fds);
      assert.ok(cv('xclip-procs').processes.length <= 1);
      await close();
      assert.deepEqual(cv('xclip-procs').processes, []);
    });
  }
} catch (e) { if (!report.failed) { report.failed++; report.tests.push({ name: 'C2 harness/setup', result: 'failed', detail: String(e.stack).split(c.password).join('[REDACTED]').split(token).join('[TOKEN]').slice(0, 1500) }); } }
finally {
  await page.goto(c.url, { waitUntil: 'domcontentloaded' }).catch(() => {}); if (await page.getByPlaceholder('访问密码').count()) await login().catch(() => {});
  await close().catch(() => {});
  try { report.final_xclip = cv('xclip-procs').processes.length; } catch {}
  fs.writeFileSync(c.out + (c.after_reboot ? '/image-clipboard-reboot.json' : '/image-clipboard.json'), JSON.stringify(report, null, 2)); await browser.close();
}
console.log(JSON.stringify({ kind: report.kind, passed: report.passed, failed: report.failed, skipped: report.skipped, not_executed: report.not_executed }));
if (report.failed) process.exitCode = 1;
