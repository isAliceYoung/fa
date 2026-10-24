// Phase 4B-2 database acceptance ONLY in the controller's disposable Debian VM.
// Root, real PostgreSQL 15, the real installed backend (V1 program).
//   --installed : isolated install made by db_migrate init (scenarios A, C-G, backup/restore)
//   --legacy    : staging-baseline install made by the OLD installer (push --force) (scenario B)
// Test-only migrations come from /root/db-test (copied by the driver), never a release.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { parseEnv } from 'node:util';

const argv = process.argv.slice(2);
if (!argv.includes('--disposable-vm') || process.getuid() !== 0 || os.hostname() !== 'wd-lab' || !fs.existsSync('/var/tmp/wd-lab-ready')) throw new Error('Explicit disposable VM required');
const LEGACY = argv.includes('--legacy');
const APP = '/opt/webdesktop/app';
const TEST = '/root/db-test';
const STATE = LEGACY ? '/etc/webdesktop-staging' : '/etc/webdesktop-isolated';
const BACKEND = LEGACY ? 'webdesktop-staging.service' : 'webdesktop-isolated.service';
const require = createRequire(path.join(APP, 'package.json'));
const pg = require('pg');
const M = createRequire(import.meta.url)(LEGACY ? path.join(TEST, 'db_migrate.cjs') : '/opt/webdesktop/source/webdesktop-deploy/scripts/db_migrate.cjs');
const CLI = '/opt/webdesktop/source/webdesktop-deploy/scripts/db_migrate.cjs';
const cfg = parseEnv(fs.readFileSync(path.join(APP, '.env'), 'utf8'));
const login = JSON.parse(fs.readFileSync(path.join(STATE, 'login.json'), 'utf8'));
const OUT = '/root/staging-results/' + (LEGACY ? 'db-baseline-legacy.json' : 'db-migrate-vm.json');
const report = { schema: 1, passed: 0, failed: 0, skipped: 0, not_executed: 0, tests: [], resources: {} };
const secrets = [cfg.DATABASE_URL, cfg.ADMIN_PASSWORD, cfg.DESKTOP_PASSWORD, cfg.AUTH_KEY, new URL(cfg.DATABASE_URL).password].filter(Boolean);
const redact = text => secrets.reduce((t, s) => t.split(s).join('[REDACTED]'), String(text));
const save = () => fs.writeFileSync(OUT, JSON.stringify(report, null, 2));

function check(ok, message) { if (!ok) throw new Error(message); }
async function test(name, fn) {
  try { const evidence = await fn(); report.passed++; report.tests.push({ name, result: 'passed', evidence: evidence ?? null }); }
  catch (e) { report.failed++; report.tests.push({ name, result: 'failed', detail: redact((e.code ? e.code + ': ' : '') + e.message + (e.detail ? ' ' + JSON.stringify(e.detail) : '')).slice(0, 1500) }); }
  finally { save(); }
}
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const psql = (sql, db = 'postgres') => execFileSync('runuser', ['-u', 'postgres', '--', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-d', db, '-c', sql], { encoding: 'utf8' }).trim();
const urlFor = db => { const u = new URL(cfg.DATABASE_URL); u.pathname = '/' + db; return u.toString(); };
const dbName = new URL(cfg.DATABASE_URL).pathname.slice(1);
async function connect(db = dbName) { const c = new pg.Client({ connectionString: urlFor(db) }); await c.connect(); return c; }
function cli(mode, extra = []) {
  const r = spawnSync(path.join(APP, 'node'), [CLI, mode, APP, ...extra], { encoding: 'utf8', timeout: 900000 });
  let json = null; try { json = JSON.parse(r.stdout); } catch { /* */ }
  return { status: r.status, json, stderr: redact(r.stderr).slice(-400) };
}
async function measured(name, args) {
  const before = fs.statfsSync('/var/lib'); const t0 = Date.now();
  const child = spawn(args[0], args.slice(1), { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = ''; child.stdout.on('data', d => { out += d; });
  let peak = 0, fds = 0, minAvail = Infinity;
  const timer = setInterval(() => {
    try {
      const pids = [child.pid, ...fs.readFileSync(`/proc/${child.pid}/task/${child.pid}/children`, 'utf8').split(/\s+/).filter(Boolean).map(Number)];
      let rss = 0; for (const p of pids) { const m = /VmRSS:\s+(\d+)/.exec(fs.readFileSync(`/proc/${p}/status`, 'utf8')); if (m) rss += Number(m[1]) * 1024; }
      peak = Math.max(peak, rss); fds = Math.max(fds, fs.readdirSync(`/proc/${child.pid}/fd`).length);
    } catch { /* exited */ }
    minAvail = Math.min(minAvail, Number(/MemAvailable:\s+(\d+)/.exec(fs.readFileSync('/proc/meminfo', 'utf8'))[1]) * 1024);
  }, 100);
  const code = await new Promise(r => child.on('exit', r)); clearInterval(timer);
  const after = fs.statfsSync('/var/lib');
  report.resources[name] = { wall_s: (Date.now() - t0) / 1000, peak_rss_mib: Math.round(peak / 2 ** 20), peak_fds: fds, min_mem_available_mib: Math.round(minAvail / 2 ** 20),
    disk_used_mib: Math.round(((before.bavail - after.bavail) * after.bsize) / 2 ** 20), cpus: os.cpus().length, mem_total_mib: Math.round(os.totalmem() / 2 ** 20) };
  let json = null; try { json = JSON.parse(out); } catch { /* */ }
  return { code, json };
}
const oomKills = () => Number(/oom_kill (\d+)/.exec(fs.readFileSync('/sys/fs/cgroup/webdesktop.slice/memory.events', 'utf8'))[1]);

// HTTP against the real running backend (the V1 program).
const BASE = 'http://127.0.0.1:3000';
async function desktop() {
  const r = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: cfg.DESKTOP_PASSWORD }) });
  check(r.status === 200, 'desktop login ' + r.status);
  const cookie = r.headers.getSetCookie()[0].split(';')[0];
  return { Cookie: cookie, 'X-Desktop-Session': (await r.json()).sessionId, 'Content-Type': 'application/json' };
}
async function coreReadWrite(tag) {
  const h = await desktop();
  const fsr = await fetch(BASE + '/api/fs', { headers: h }); check(fsr.status === 200, 'GET /api/fs ' + fsr.status);
  const { nodes } = await fsr.json(); const folder = nodes.find(n => n.kind === 'folder'); check(folder, 'seed folder');
  const created = await fetch(BASE + '/api/fs', { method: 'POST', headers: h, body: JSON.stringify({ parentId: folder.id, name: '4b2-' + tag + '.txt', kind: 'file', content: 'db ' + tag }) });
  check(created.status === 200, 'POST /api/fs ' + created.status);
  const set = await fetch(BASE + '/api/settings', { method: 'PUT', headers: h, body: JSON.stringify({ key: 'wallpaper', value: '4b2-' + tag }) }); check(set.status === 200, 'PUT /api/settings ' + set.status);
  const ws = await (await fetch(BASE + '/api/workspace', { headers: h })).json();
  const put = await fetch(BASE + '/api/workspace', { method: 'PUT', headers: h, body: JSON.stringify({ snapshot: ws.snapshot, version: ws.version }) });
  check(put.status === 200 || put.status === 409, 'PUT /api/workspace ' + put.status);
  const admin = await fetch(BASE + '/api/ops/' + login.ADMIN_PATH + '/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: login.ADMIN_USER, password: login.ADMIN_PASSWORD }) });
  check(admin.status === 200, 'admin login ' + admin.status);
  await fetch(BASE + '/api/auth/logout', { method: 'POST', headers: h });
  return { fs: 200, settings: 200, workspace: put.status, admin: 200 };
}
async function authValues(c) {
  const rows = (await c.query(`SELECT key, value FROM auth_config WHERE key IN ('secret','desk_hash') ORDER BY key`)).rows;
  const admin = (await c.query('SELECT username, password_hash FROM admin_users ORDER BY id')).rows;
  return sha(JSON.stringify({ rows, admin }));
}
const manifestOf = dir => M.loadManifest(dir);
const PROD = LEGACY ? path.join(TEST, 'migrations') : path.join(APP, 'drizzle/migrations');
const prod = manifestOf(PROD);

try {
  if (LEGACY) {
    // Scenario B: the database really made by the old installer (drizzle-kit push --force).
    await test('B: old-installer database is recognized as the exact legacy schema', async () => {
      const c = await connect(); try { const s = await M.run('status', c, prod); check(s.state === 'legacy-exact', 'state ' + s.state + ' ' + JSON.stringify(s.problems)); return { state: s.state }; } finally { await c.end(); }
    });
    await test('B: baseline records 0001 without changing any business data; login keeps working', async () => {
      const c = await connect();
      try {
        const before = await M.digests(c, prod.latest.schema), auth = await authValues(c);
        const r = await M.run('baseline', c, prod);
        check(r.state === 'recorded' && r.current === '0001_initial', 'after ' + r.state);
        check(JSON.stringify(await M.digests(c, prod.latest.schema)) === JSON.stringify(before), 'business rows changed');
        check(await authValues(c) === auth, 'credentials changed');
        check((await c.query(`SELECT method FROM ${M.RECORD}`)).rows[0].method === 'baseline', 'method');
        const h = await desktop(); check((await fetch(BASE + '/api/fs', { headers: h })).status === 200, 'old program reads after baseline');
        return { tables: Object.keys(before).length, rows_unchanged: true, credentials_unchanged: true };
      } finally { await c.end(); }
    });
    await test('B: a second baseline and an init are refused', async () => {
      const c = await connect();
      try {
        for (const mode of ['baseline', 'init']) { let code = null; try { await M.run(mode, c, prod); } catch (e) { code = e.code; } check(['BASELINE_REFUSED', 'NOT_EMPTY'].includes(code), mode + ' ' + code); }
        return { refused: ['baseline', 'init'] };
      } finally { await c.end(); }
    });
  } else {
    const live = await connect();
    let backupFile = null, D0 = null, A0 = null;
    await test('A0: a foreign table (phase4a_fixture, created by the earlier 4A test) is drift; migrate and backup refused', async () => {
      const s = cli('status');
      check(s.json?.state === 'drift' && JSON.stringify(s.json.problems) === '["unexpected tables.phase4a_fixture"]', JSON.stringify(s.json));
      const m = cli('migrate'), b = cli('backup');
      check(m.status === 1 && m.json?.code === 'MIGRATION_REFUSED' && b.status === 1 && b.json?.code === 'BACKUP_REFUSED', JSON.stringify([m.json, b.json]));
      // Test-fixture cleanup only: phase4a_modes.py created this table; the product never drops tables.
      psql('DROP TABLE phase4a_fixture', dbName);
      return { detected: s.json.problems, migrate: m.json.code, backup: b.json.code };
    });
    await test('A: fresh install recorded 0001 through versioned migrations (no schema push)', async () => {
      const s = cli('status'); check(s.status === 0 && s.json.state === 'recorded' && s.json.current === '0001_initial', JSON.stringify(s.json));
      const rows = (await live.query(`SELECT id, method FROM ${M.RECORD} ORDER BY seq`)).rows;
      check(rows.length === 1 && rows[0].method === 'applied', 'record');
      check(!fs.readFileSync('/etc/webdesktop-isolated/install.log', 'utf8').includes('drizzle-kit'), 'installer used drizzle-kit');
      return { state: s.json.state, record: rows };
    });
    await test('A: real backend logins and core reads/writes on the fresh schema', async () => coreReadWrite('fresh'));
    D0 = await M.digests(live, prod.latest.schema); A0 = await authValues(live);
    await test('backup: pg_dump custom format, root 0600 in a root 0700 directory, verified by real restore', async () => {
      const kills = oomKills();
      const r = await measured('backup', [path.join(APP, 'node'), CLI, 'backup', APP]);
      check(r.code === 0 && r.json?.restore_verified === true, 'backup ' + JSON.stringify(r.json));
      backupFile = r.json.file;
      const st = fs.statSync(backupFile), dir = fs.statSync(path.dirname(backupFile));
      check(st.uid === 0 && (st.mode & 0o777) === 0o600 && dir.uid === 0 && (dir.mode & 0o777) === 0o700, 'permissions');
      check(psql(`SELECT count(*) FROM pg_database WHERE datname LIKE 'wd_restore_check_%'`) === '0', 'temporary restore database left behind');
      check(oomKills() === kills, 'OOM during backup');
      return { bytes: st.size, sha256: r.json.sha256, tables: r.json.tables, resources: report.resources.backup };
    });
    await test('backup: corrupted file fails restore verification; live data untouched', async () => {
      const bad = path.join(path.dirname(backupFile), '20991231T000000Z-corrupt.dump');
      const data = fs.readFileSync(backupFile); fs.writeFileSync(bad, data.subarray(0, Math.floor(data.length / 2)), { mode: 0o600 });
      try {
        const r = cli('verify-backup', [bad]); check(r.status === 1 && ['RESTORE_FAILED', 'RESTORE_MISMATCH'].includes(r.json?.code), JSON.stringify(r.json));
        check(psql(`SELECT count(*) FROM pg_database WHERE datname LIKE 'wd_restore_check_%'`) === '0', 'restore database left behind');
        check(JSON.stringify(await M.digests(live, prod.latest.schema)) === JSON.stringify(D0), 'live data changed');
        return { code: r.json.code };
      } finally { fs.unlinkSync(bad); }
    });
    await test('backup: insufficient disk space and an interrupted (.partial) backup are refused before writing', async () => {
      const dir = path.dirname(backupFile), before = fs.readdirSync(dir).sort();
      const size = Number(psql(`SELECT pg_database_size('${dbName}')`));
      const s = fs.statfsSync(dir), free = s.bavail * s.bsize, need = size + 512 * 2 ** 20;
      const filler = '/var/lib/wd-4b2-filler';
      execFileSync('fallocate', ['-l', String(Math.max(0, free - need + 64 * 2 ** 20)), filler]);
      let space;
      try { space = cli('backup'); } finally { fs.unlinkSync(filler); }
      check(space.status === 1 && space.json?.code === 'DISK_SPACE', 'space ' + JSON.stringify(space.json));
      check(JSON.stringify(fs.readdirSync(dir).sort()) === JSON.stringify(before), 'file written despite DISK_SPACE');
      const partial = path.join(dir, '20991231T000000Z-interrupted.dump.partial'); fs.writeFileSync(partial, 'PGDMP', { mode: 0o600 });
      let stale; try { stale = cli('backup'); } finally { fs.unlinkSync(partial); }
      check(stale.status === 1 && stale.json?.code === 'STALE', 'stale ' + JSON.stringify(stale.json));
      return { disk_space: space.json.code, interrupted: stale.json.code };
    });
    const v2 = manifestOf(path.join(TEST, 'db-migrations-v2'));
    await test('C: V1 -> V2 expand on the live database; V1 rows and credentials unchanged', async () => {
      const kills = oomKills(); const t0 = Date.now();
      const r = await M.run('migrate', live, v2);
      report.resources.migrate_v2 = { wall_s: (Date.now() - t0) / 1000 };
      check(r.state === 'recorded' && r.current === '0002_test_probe', JSON.stringify(r));
      check(JSON.stringify(await M.digests(live, prod.latest.schema)) === JSON.stringify(D0), 'V1 data changed');
      check(await authValues(live) === A0, 'credentials changed');
      check(oomKills() === kills, 'OOM');
      return { current: r.current, records: (await live.query(`SELECT id, method FROM ${M.RECORD} ORDER BY seq`)).rows };
    });
    await test('C: the running V1 program keeps full core reads/writes on the V2 schema', async () => coreReadWrite('v2'));
    await test('C: the V1 release recognizes an unknown newer version and refuses to migrate it', async () => {
      const s = cli('status'); check(s.json?.state === 'unknown-version', JSON.stringify(s.json));
      const m = cli('migrate'); check(m.status === 1 && m.json?.code === 'MIGRATION_REFUSED', JSON.stringify(m.json));
      return { state: s.json.state };
    });
    await test('D: repeating the V2 migration changes nothing', async () => {
      const before = M.sha256(M.canonical(await M.describe(live))), n = (await live.query(`SELECT count(*)::int AS n FROM ${M.RECORD}`)).rows[0].n;
      const r = await M.run('migrate', live, v2);
      check(r.current === '0002_test_probe' && M.sha256(M.canonical(await M.describe(live))) === before && (await live.query(`SELECT count(*)::int AS n FROM ${M.RECORD}`)).rows[0].n === n, 'changed');
      return { records: n };
    });
    await test('E: injected SQL failure rolls back completely; version stays; backend keeps working', async () => {
      const before = M.sha256(M.canonical(await M.describe(live)));
      let code = null; try { await M.run('migrate', live, manifestOf(path.join(TEST, 'db-migrations-fail'))); } catch (e) { code = e.code; }
      check(code === '42P01', 'expected a real SQL error, got ' + code);
      const c = await M.classify(live, v2);
      check(c.state === 'recorded' && c.current === '0002_test_probe' && M.sha256(M.canonical(await M.describe(live))) === before, 'not rolled back');
      check((await live.query(`SELECT 1 FROM information_schema.columns WHERE table_name='desktop_settings' AND column_name='probe_fail'`)).rowCount === 0, 'partial column');
      const h = await desktop(); check((await fetch(BASE + '/api/fs', { headers: h })).status === 200, 'backend after failure');
      return { sql_error: code, current: c.current };
    });
    // Scratch databases restored from the verified V1 backup.
    // Root-only backups are passed to pg_restore as an open descriptor (as db_migrate does).
    const restoreInto = name => { const fd = fs.openSync(backupFile, 'r'); try { execFileSync('runuser', ['-u', 'postgres', '--', 'pg_restore', '--exit-on-error', '-d', name], { stdio: [fd, 'ignore', 'pipe'] }); } finally { fs.closeSync(fd); } };
    const scratch = name => { psql(`CREATE DATABASE "${name}" OWNER "${new URL(cfg.DATABASE_URL).username}"`); restoreInto(name); return name; };
    await test('F: two concurrent migrations - one runs, the other is BUSY; one record each', async () => {
      const name = scratch('wd_4b2_conc');
      const [a, b, blocker, view] = [await connect(name), await connect(name), await connect(name), await connect(name)];
      try {
        await blocker.query('BEGIN'); await blocker.query('LOCK TABLE desktop_settings IN ACCESS EXCLUSIVE MODE');
        const running = M.run('migrate', a, v2);
        for (let i = 0; i < 100 && !(await view.query(`SELECT count(*)::int AS n FROM pg_locks WHERE locktype='advisory' AND granted`)).rows[0].n; i++) await new Promise(r => setTimeout(r, 50));
        let code = null; try { await M.run('migrate', b, v2); } catch (e) { code = e.code; }
        await blocker.query('COMMIT');
        const r = await running;
        const ids = (await view.query(`SELECT id FROM ${M.RECORD} ORDER BY seq`)).rows.map(x => x.id);
        check(code === 'BUSY' && r.current === '0002_test_probe' && JSON.stringify(ids) === '["0001_initial","0002_test_probe"]', code + ' ' + ids);
        return { second: code, records: ids };
      } finally { for (const c of [a, b, blocker, view]) await c.end().catch(() => {}); psql(`DROP DATABASE "${name}" WITH (FORCE)`); }
    });
    await test('G: unknown structure, drift and tampered records are refused, never repaired', async () => {
      const out = {};
      for (const [tag, sql, want] of [['extra-table', 'CREATE TABLE intruder (id integer)', 'drift'], ['drift', 'ALTER TABLE fs_nodes DROP CONSTRAINT fs_nodes_parent_id_fs_nodes_id_fk', 'drift'],
        ['digest', `UPDATE ${M.RECORD} SET sha256 = repeat('0', 64)`, 'record-corrupt']]) {
        const name = scratch('wd_4b2_' + tag.replace('-', '_')); const c = await connect(name);
        try {
          await c.query(sql);
          const s = await M.run('status', c, prod); out[tag] = s.state;
          // On a recorded database any unknown object is drift from the recorded version.
          check(s.state === want, tag + ' ' + s.state);
          let code = null; try { await M.run('migrate', c, v2); } catch (e) { code = e.code; }
          check(code === 'MIGRATION_REFUSED', tag + ' migrate ' + code);
        } finally { await c.end(); psql(`DROP DATABASE "${name}" WITH (FORCE)`); }
      }
      return out;
    });
    await live.end();
    await test('rollback: the database is rolled back only by restoring the pre-migration backup', async () => {
      // Program rollback alone would leave V2 in place; a real DB rollback is a restore.
      execFileSync('systemctl', ['stop', BACKEND]);
      psql(`ALTER DATABASE "${dbName}" RENAME TO "${dbName}_v2_saved"`);
      psql(`CREATE DATABASE "${dbName}" OWNER "${new URL(cfg.DATABASE_URL).username}"`);
      restoreInto(dbName);
      execFileSync('bash', ['/opt/webdesktop/source/webdesktop-deploy/install-isolated.sh', '--mode', 'service', '--commit', JSON.parse(fs.readFileSync('/etc/webdesktop-isolated/state.json', 'utf8')).commit], { stdio: 'ignore', timeout: 180000 });
      const c = await connect();
      try {
        const s = cli('status'); check(s.json?.state === 'recorded' && s.json.current === '0001_initial', JSON.stringify(s.json));
        check(JSON.stringify(await M.digests(c, prod.latest.schema)) === JSON.stringify(D0), 'restored rows differ from the backup snapshot');
        check(await authValues(c) === A0, 'credentials differ after restore');
      } finally { await c.end(); }
      await coreReadWrite('restored');
      psql(`DROP DATABASE "${dbName}_v2_saved"`);
      return { restored_to: '0001_initial', rows_equal_backup: true, credentials_equal: true };
    });
  }
} finally { save(); }
console.log(JSON.stringify({ passed: report.passed, failed: report.failed, skipped: report.skipped, not_executed: report.not_executed }));
if (report.failed) process.exitCode = 1;
