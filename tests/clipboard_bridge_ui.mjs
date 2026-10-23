// C1 acceptance: explicit text-only clipboard between the real local browser
// and the restricted native GUI (host Chrome -> noVNC -> wdgui X11).
// Clipboard text is random, non-production and never written to evidence:
// the report keeps only lengths, hashes, codes and HTTP statuses.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright-core';
let input = ''; for await (const p of process.stdin) input += p; const c = JSON.parse(input);
const browser = await chromium.launch({ executablePath: c.chrome, headless: true, chromiumSandbox: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' });
await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: c.url });
const page = await context.newPage(); page.setDefaultTimeout(20000);
const planned = c.after_reboot ? ['after actual reboot: PCManFM text both directions', 'after actual reboot: legacy interfaces stay closed'] : [
  'web to native Chromium text (zh-Hans/zh-Hant/mixed/emoji/multiline/quotes)', 'web to native Chromium 16 KiB UTF-8 boundary', 'native Chromium to web text',
  'native PCManFM location bar both directions', 'non-text remote clipboard formats rejected', 'PCManFM copied files are not exported',
  'local oversize, control and empty text rejected without truncation', 'API authentication, session binding and request shape',
  'concurrent and rapid requests are bounded', 'text is never executed, read as a path, logged or stored', 'bridge runs only as wdgui; other UIDs have no X11 access',
  'legacy clipboard, drag and launch interfaces stay closed', 'page refresh invalidates the bridge session',
  'GUI close, Chromium crash and backend restart invalidate the bridge', 'login revocation and new device login revoke the bridge'];
const report = { kind: 'c1-text-clipboard', passed: 0, failed: 0, skipped: 0, not_executed: planned.length, tests: [], evidence: [] };
const token = randomBytes(6).toString('hex'), started = Date.now();
const ssh = (cmd, body, timeout = 20000) => JSON.parse(execFileSync('ssh', [...c.ssh, cmd], { encoding: 'utf8', input: body ? JSON.stringify(body) : undefined, timeout, stdio: ['pipe', 'pipe', 'pipe'] }));
const observer = (action, body, timeout) => ssh('sudo python3 -I /root/download-bridge-vm.py ' + action, body, timeout);
const cv = (action, body) => ssh('sudo python3 -I /root/clipboard-vm.py ' + action, body ?? {});
const probe = topic => ssh('sudo python3 /root/staging-ui-probe.py ' + topic);
const sha = s => createHash('sha256').update(s).digest('hex');
const meta = s => ({ chars: Array.from(s).length, bytes: Buffer.byteLength(s), sha256: sha(s) });
const title = s => 'C1 ' + Array.from(s).length + ' ' + sha(s).slice(0, 16);
const b64url = s => Buffer.from(s).toString('base64url');
const gui = () => page.getByTestId('basic-gui'), canvas = () => gui().locator('canvas');
let sid = '', access = '';
page.on('websocket', s => { const u = new URL(s.url()); if (u.pathname === '/api/gui/ws') { sid = u.searchParams.get('sid'); access = u.searchParams.get('access'); } });
async function until(fn, timeout = 30000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await fn()) return; await page.waitForTimeout(250); } throw Error('Bounded observable state timeout'); }
async function test(name, fn) {
  assert(planned.includes(name)); report.not_executed--;
  try { await fn(); report.passed++; report.tests.push({ name, result: 'passed' }); }
  catch (e) {
    report.failed++; report.tests.push({ name, result: 'failed', detail: String(e.stack).split(c.password).join('[REDACTED]').split(token).join('[TOKEN]').slice(0, 1500) });
    await page.screenshot({ path: c.out + '/c1-failure-' + report.failed + '.png' }).catch(() => {});
    await page.goto(c.url, { waitUntil: 'domcontentloaded' }).catch(() => {}); if (await page.getByPlaceholder('访问密码').count()) await login().catch(() => {});
  }
}
async function login(p = page) { await p.getByPlaceholder('访问密码').fill(c.password); await p.getByRole('button', { name: '解锁并接管桌面', exact: true }).click(); await p.getByText('终端', { exact: true }).first().waitFor(); }
async function api(url, method = 'GET', body, headers = {}) { return page.evaluate(async ({ url, method, body, headers }) => { const r = await fetch(url, { method, headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers }, ...(body !== undefined ? { body: typeof body === 'string' ? body : JSON.stringify(body) } : {}) }); const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch {} return { status: r.status, code: j?.code ?? null, text: typeof j?.text === 'string' ? j.text : null }; }, { url, method, body, headers }); }
const clipApi = (body, headers) => api('/api/gui/text-clipboard', 'POST', body, headers);
async function idle() { await until(() => observer('execution-idle').idle, 30000); }
async function close() { if (await gui().count()) await gui().locator('xpath=../..').getByRole('button', { name: '关闭', exact: true }).click(); await idle(); }
async function open(app) {
  await page.getByText(app === 'browser' ? '浏览器' : 'PCManFM', { exact: true }).first().dblclick();
  await page.locator('[data-testid="basic-gui"][data-status="open"]').waitFor({ timeout: 60000 });
  await until(() => probe('state').windows.some(w => app === 'browser' ? w.app === 'chromium' : w.app === '[Pp]cmanfm'), 45000);
}
async function navigate(url) {
  for (let attempt = 1; ; attempt++) {
    await canvas().click({ position: { x: 450, y: 63 } }); await page.keyboard.press('Control+l'); await page.waitForTimeout(250); await page.keyboard.press('Control+a');
    await page.keyboard.type(url, { delay: 8 }); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+c');
    try { await until(() => probe('address').sha256 === sha(url), 10000); break; } catch (e) { if (attempt >= 3) throw e; await page.keyboard.press('Escape'); }
  }
  await page.keyboard.press('Enter');
}
const titled = (needle, cls = 'chromium', timeout = 20000) => until(() => cv('title-match', { needle, class: cls }).match, timeout);
async function button(id) {
  if (await page.getByTestId('gui-clip-close').count()) await page.getByTestId('gui-clip-close').click();
  await page.getByTestId(id).click();
  await until(async () => (await page.getByTestId('gui-clip-notice').count()) > 0 && !(await page.getByTestId(id).isDisabled()), 20000);
  return page.getByTestId('gui-clip-notice').getAttribute('data-code');
}
const setLocal = t => page.evaluate(t => navigator.clipboard.writeText(t), t);
const getLocal = () => page.evaluate(() => navigator.clipboard.readText());
async function webToChromium(text) {
  await setLocal(text); assert.equal(await button('gui-clip-paste'), 'OK');
  await canvas().click({ position: { x: 300, y: 300 } }); await page.keyboard.press('Control+v');
  await titled(title(text)); report.evidence.push({ direction: 'web->chromium', ...meta(text) });
}
const sample = {
  hans: 'C1 简体中文剪贴板测试 ' + token, hant: 'C1 繁體中文剪貼簿測試 ' + token, mixed: 'C1 中English混排 123，。！？；：“”（）【】 ' + token,
  emoji: 'C1 😀👍🏽🇨🇳 𠮷 ¼→✓ ' + token, multi: 'C1 第一行\n\t第二行 tab\n  third line  ' + token,
  quotes: 'C1 "\'\\<a href="x">&amp;</a> $(touch /home/wdguest/Documents/C1-EXECUTED) `touch /tmp/C1-EXECUTED` file:///opt/webdesktop/app/.env ' + token,
};
let task = 0; const page_ = (p, text) => 'https://203.0.113.2/' + p + '?task=' + randomBytes(16).toString('hex') + '&n=' + (++task) + (text !== undefined ? '#' + b64url(text) : '');
try {
  await page.goto(c.url, { waitUntil: 'domcontentloaded' }); await login();
  const baseline = cv('protected');
  if (c.after_reboot) {
    await test(planned[0], async () => {
      const name = 'C1-重启后-' + token; const made = await api('/api/sys/fs', 'POST', { dir: '~', name, kind: 'folder' }); assert.equal(made.status, 200);
      await open('pcmanfm'); const path = '/home/wdguest/' + name;
      await setLocal(path); assert.equal(await button('gui-clip-paste'), 'OK');
      await canvas().click({ position: { x: 500, y: 350 } }); await page.keyboard.press('Control+l'); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+v'); await page.keyboard.press('Enter');
      await titled(name, '[Pp]cmanfm');
      await page.keyboard.press('Control+l'); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+c'); await setLocal('C1-sentinel-' + token);
      assert.equal(await button('gui-clip-copy'), 'OK'); assert.equal(await getLocal(), path); report.evidence.push({ direction: 'both-after-reboot', ...meta(path) });
      await close();
    });
    await test(planned[1], async () => {
      for (const [u, m, b] of [['/api/gui/clipboard', 'GET'], ['/api/gui/clipboard', 'POST', {}], ['/api/gui/' + 'a'.repeat(24) + '/clipboard', 'POST', {}], ['/api/gui/' + 'a'.repeat(24) + '/drop', 'POST', {}]]) {
        const r = await api(u, m, b); assert.equal(r.status, 503, u); assert.equal(r.code, 'GUEST_FEATURE_PENDING', u);
      }
    });
  } else {
    await open('browser'); await navigate(page_('clip')); await titled('C1 Ready');
    await test(planned[0], async () => { for (const k of ['hans', 'hant', 'mixed', 'emoji', 'multi', 'quotes']) await webToChromium(sample[k]); });
    await test(planned[1], async () => { const exact = '汉'.repeat(5461) + 'a'; assert.equal(Buffer.byteLength(exact), 16384); await webToChromium(exact); });
    await test(planned[2], async () => {
      for (const text of [sample.hans + ' 😀', sample.multi, sample.hant + ' ' + sample.mixed]) {
        await navigate(page_('clip-src', text)); await titled('C1 Source ' + Array.from(text).length + ' ' + sha(text).slice(0, 16));
        await canvas().click({ position: { x: 300, y: 300 } }); await page.keyboard.press('Control+c'); await setLocal('C1-sentinel-' + token);
        await until(() => { const h = cv('remote-hash'); return h.present && h.sha256 === sha(text); }, 10000);
        assert.equal(await button('gui-clip-copy'), 'OK'); assert.equal(await getLocal(), text); report.evidence.push({ direction: 'chromium->web', ...meta(text) });
      }
    });
    await close();
    const folder = 'C1-目录-' + token + '-繁體'; let folderPath = '';
    await test(planned[3], async () => {
      const made = await api('/api/sys/fs', 'POST', { dir: '~', name: folder, kind: 'folder' }); assert.equal(made.status, 200);
      assert.equal((await api('/api/sys/fs', 'POST', { dir: '~/' + folder, name: 'c1-file-' + token + '.txt', kind: 'file', content: 'C1 file ' + token })).status, 200);
      await open('pcmanfm'); folderPath = '/home/wdguest/' + folder;
      await setLocal(folderPath); assert.equal(await button('gui-clip-paste'), 'OK');
      await canvas().click({ position: { x: 500, y: 350 } }); await page.keyboard.press('Control+l'); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+v'); await page.keyboard.press('Enter');
      await titled(folder, '[Pp]cmanfm');
      await page.keyboard.press('Control+l'); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+c'); await setLocal('C1-sentinel-' + token);
      assert.equal(await button('gui-clip-copy'), 'OK'); assert.equal(await getLocal(), folderPath); report.evidence.push({ direction: 'pcmanfm-both', ...meta(folderPath) });
    });
    await test(planned[4], async () => {
      for (const [kind, code] of [['uri-list', 'NON_TEXT'], ['gnome-files', 'NON_TEXT'], ['png', 'NON_TEXT'], ['html-only', 'NON_TEXT'], ['invalid-utf8', 'INVALID_UTF8'], ['control', 'CONTROL_CHARACTERS'], ['oversize', 'TOO_LARGE']]) {
        assert(cv('set', { kind }).set, kind); const sentinel = 'C1-sentinel-' + kind + '-' + token; await setLocal(sentinel);
        assert.equal(await button('gui-clip-copy'), code, kind); assert.equal(await getLocal(), sentinel, kind + ' must not touch local clipboard');
        report.evidence.push({ remote: kind, code });
      }
    });
    await test(planned[5], async () => {
      await page.keyboard.press('Escape'); await canvas().click({ position: { x: 600, y: 420 } }); await page.keyboard.press('Control+a'); await page.keyboard.press('Control+c');
      await page.waitForTimeout(800); const sentinel = 'C1-sentinel-files-' + token; await setLocal(sentinel);
      assert.equal(await button('gui-clip-copy'), 'NON_TEXT'); assert.equal(await getLocal(), sentinel);
    });
    await test(planned[6], async () => {
      assert.equal((await clipApi({ sid, op: 'write', text: 'C1 baseline ' + token })).status, 200);
      const before = cv('remote-hash'); assert(before.present);
      for (const [text, code] of [['汉'.repeat(5461) + 'ab', 'TOO_LARGE'], ['C1 a\u001bb ' + token, 'CONTROL_CHARACTERS'], ['', 'EMPTY']]) {
        await setLocal(text); assert.equal(await button('gui-clip-paste'), code); assert.deepEqual(cv('remote-hash'), before, code + ' must not change remote clipboard');
      }
      assert.equal((await clipApi({ sid, op: 'write', text: '汉'.repeat(5461) + 'ab' })).status, 413);
      assert.equal((await clipApi({ sid, op: 'write', text: 'a\u0000b' })).status, 400);
      assert.equal((await clipApi({ sid, op: 'write', text: '\ud800' })).status, 400);
      assert.deepEqual(cv('remote-hash'), before);
    });
    await test(planned[7], async () => {
      assert.equal((await clipApi({ sid: randomBytes(12).toString('hex'), op: 'read' })).status, 409);
      for (const body of [{ sid, op: 'read', path: '/etc/passwd' }, { sid, op: 'exec', text: 'id' }, { sid, op: 'read', display: ':0' }, { sid, op: 'write', text: 'x', mime: 'image/png' }])
        assert.equal((await clipApi(body)).status, 400, JSON.stringify(Object.keys(body)));
      assert.equal((await api('/api/gui/text-clipboard', 'GET')).status, 405);
      assert.equal((await api('/api/gui/text-clipboard', 'POST', 'x'.repeat(70000), { 'Content-Type': 'application/json' })).status, 413);
      assert.equal((await api('/api/gui/text-clipboard', 'POST', JSON.stringify({ sid, op: 'read' }), { 'Content-Type': 'text/plain' })).status, 415);
      const raw = await context.request.post(c.url + '/api/gui/text-clipboard', { headers: { 'Content-Type': 'application/json', 'X-Desktop-Session': access }, data: Buffer.concat([Buffer.from('{"sid":"' + sid + '","op":"write","text":"'), Buffer.from([0xff, 0xfe]), Buffer.from('"}')]) });
      assert.equal(raw.status(), 400);
      const anon = await browser.newContext(); assert.equal((await anon.request.post(c.url + '/api/gui/text-clipboard', { data: { sid, op: 'read' } })).status(), 401); await anon.close();
      assert.equal((await context.request.post(c.url + '/api/gui/text-clipboard', { headers: { 'X-Desktop-Session': 'bad-page-session' }, data: { sid, op: 'read' } })).status(), 401);
      assert.equal((await context.request.post(c.url + '/api/gui/text-clipboard', { headers: { 'X-Desktop-Session': access, Origin: 'https://evil.example' }, data: { sid, op: 'read' } })).status(), 403);
      assert.equal((await context.request.post(c.url + '/api/gui/text-clipboard', { headers: { 'X-Desktop-Session': access }, data: { sid, op: 'read' } })).status() < 500, true);
    });
    await test(planned[8], async () => {
      const statuses = (await Promise.all([1, 2, 3].map(() => clipApi({ sid, op: 'read' })))).map(r => r.status);
      assert(statuses.includes(409), 'one request at a time: ' + statuses); report.evidence.push({ concurrent: statuses });
      await page.waitForTimeout(10500);
      const burst = []; for (let i = 0; i < 12; i++) burst.push((await clipApi({ sid, op: 'write', text: 'C1 burst ' + i + ' ' + token })).status);
      assert(burst.includes(429), 'rate window: ' + burst); report.evidence.push({ burst });
      await page.waitForTimeout(10500); assert.equal((await clipApi({ sid, op: 'write', text: 'C1 after window ' + token })).status, 200);
    });
    await test(planned[9], async () => {
      for (const text of ['/opt/webdesktop/app/.env', '/opt/webdesktop/source/.git/config', 'file:///root/.ssh/id_ed25519', '$(touch /home/wdguest/Documents/C1-EXECUTED);`touch /tmp/C1-EXECUTED` ' + token]) {
        assert.equal((await clipApi({ sid, op: 'write', text })).status, 200); const r = await clipApi({ sid, op: 'read' });
        assert.equal(r.status, 200); assert.equal(r.text, text, 'returned the string itself, never file content');
      }
      const now = cv('protected'); assert.deepEqual(now.hashes, baseline.hashes); assert.deepEqual(now.executed, []);
      const stored = await page.evaluate(t => JSON.stringify({ ...localStorage, ...sessionStorage }).includes(t), token); assert.equal(stored, false);
      // The token goes in on stdin: sudo logs argv, which would match itself.
      assert.equal(cv('journal-count', { needle: token, since: Math.floor(started / 1000) }).count, 0);
    });
    await test(planned[10], async () => {
      assert.equal((await clipApi({ sid, op: 'write', text: 'C1 owner ' + token })).status, 200);
      const procs = cv('xclip-procs').processes; assert(procs.length >= 1); for (const p of procs) { assert.equal(p.user, 'wdgui'); assert.equal(p.unit, 'webdesktop-gui@1.service'); }
      const denied = cv('x11-denied'); for (const u of ['wdsvc', 'wdguest', 'wdterm']) assert.deepEqual(denied[u], { x11: true, xauth: true }, u);
      const flags = cv('xvnc-flags').servers; assert(flags.length === 1 && flags.every(f => f.accept_cut_text_off && f.send_cut_text_off && f.tcp_off));
      report.evidence.push({ owners: procs.length, x11_denied: denied, xvnc: flags });
    });
    await test(planned[11], async () => {
      for (const [u, m, b] of [['/api/gui/clipboard', 'GET'], ['/api/gui/clipboard', 'POST', {}], ['/api/gui/' + sid + '/clipboard', 'POST', {}], ['/api/gui/' + sid + '/drop', 'POST', {}],
        ['/api/gui', 'POST', {}], ['/api/gui/apps', 'GET'], ['/api/gui/shortcut', 'POST', {}], ['/api/sys/fs', 'PATCH', { pasteFromVnc: true }]]) {
        const r = await page.evaluate(async ({ u, m, b }) => { const x = await fetch(u, { method: m, headers: b ? { 'Content-Type': 'application/json' } : {}, body: b ? JSON.stringify(b) : undefined }); return { s: x.status, j: await x.json().catch(() => null) }; }, { u, m, b });
        assert.equal(r.s, 503, u); assert.equal(r.j?.code, 'GUEST_FEATURE_PENDING', u);
      }
    });
    await test(planned[12], async () => {
      const old = sid; assert.equal((await clipApi({ sid: old, op: 'write', text: 'C1 before refresh ' + token })).status, 200);
      await page.reload(); await page.getByText('终端', { exact: true }).first().waitFor({ timeout: 30000 });
      assert.equal((await clipApi({ sid: old, op: 'read' })).status, 409, 'old bridge session invalid after refresh');
      // Same-tab refresh restores the window and opens a NEW GUI session.
      await until(async () => sid !== old && (await gui().count()) > 0 && (await gui().getAttribute('data-status')) === 'open', 60000).catch(() => {});
      if (sid === old || !(await gui().count()) || (await gui().getAttribute('data-status')) !== 'open') { if (await gui().count()) await close(); await open('pcmanfm'); }
      assert.notEqual(sid, old); const fresh = await clipApi({ sid, op: 'read' }); report.evidence.push({ after_refresh_read: fresh.status });
      assert(fresh.status === 404 || fresh.status === 200);  // new X server: old clipboard not carried over (404 expected)
    });
    await test(planned[13], async () => {
      const s1 = sid; await close(); assert.equal((await clipApi({ sid: s1, op: 'read' })).status, 409); assert.deepEqual(cv('xclip-procs').processes, []);
      await open('browser'); const s2 = sid; assert.equal((await clipApi({ sid: s2, op: 'write', text: 'C1 crash ' + token })).status, 200);
      observer('crash'); await until(() => observer('execution-idle').idle, 30000);
      assert.equal((await clipApi({ sid: s2, op: 'read' })).status, 409); assert.deepEqual(cv('xclip-procs').processes, []);
      if (await gui().count()) await gui().locator('xpath=../..').getByRole('button', { name: '关闭', exact: true }).click();
      await open('pcmanfm'); const s3 = sid; observer('restart-backend ' + c.backend);
      await until(async () => { try { return (await clipApi({ sid: s3, op: 'read' })).status === 409; } catch { return false; } }, 60000);
      await page.goto(c.url, { waitUntil: 'domcontentloaded' }); if (await page.getByPlaceholder('访问密码').count()) await login();
      await idle().catch(() => {}); if (await gui().count()) await close();
      await open('pcmanfm'); assert.equal((await clipApi({ sid, op: 'write', text: 'C1 after backend ' + token })).status, 200);
      assert.equal((await clipApi({ sid, op: 'read' })).text, 'C1 after backend ' + token);
    });
    await test(planned[14], async () => {
      const old = sid, other = await browser.newContext(), p = await other.newPage(); await p.goto(c.url); await login(p);
      assert.equal((await clipApi({ sid: old, op: 'read' })).status, 401, 'revoked page cannot read');
      assert.equal((await clipApi({ sid: old, op: 'write', text: 'C1 revoked ' + token })).status, 401, 'revoked page cannot write');
      await until(() => observer('execution-idle').idle, 30000).catch(() => {});
      await p.close(); await other.close();
    });
  }
} catch (e) { if (!report.failed) { report.failed++; report.tests.push({ name: 'C1 harness/setup', result: 'failed', detail: String(e.stack).split(c.password).join('[REDACTED]').split(token).join('[TOKEN]').slice(0, 1500) }); } }
finally {
  await page.goto(c.url, { waitUntil: 'domcontentloaded' }).catch(() => {}); if (await page.getByPlaceholder('访问密码').count()) await login().catch(() => {});
  await close().catch(() => {});
  try { report.final_xclip = cv('xclip-procs').processes.length; } catch {}
  fs.writeFileSync(c.out + (c.after_reboot ? '/clipboard-reboot.json' : '/clipboard-bridge.json'), JSON.stringify(report, null, 2)); await browser.close();
}
console.log(JSON.stringify({ kind: report.kind, passed: report.passed, failed: report.failed, skipped: report.skipped, not_executed: report.not_executed }));
if (report.failed) process.exitCode = 1;
