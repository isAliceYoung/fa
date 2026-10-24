// Phase 4B-2 deterministic tests for db_migrate.cjs.
// Part 1 (always): SQL allowlist, manifest integrity and canonical schema files.
// Part 2 (WD_TEST_PG_ADMIN_URL set, real PostgreSQL 15): fresh init, strict
// legacy baseline, V1->V2 expand, repeat, failure rollback, concurrency,
// drift/corrupt/unknown refusal. Throwaway databases only; never a real VPS.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const M = require(path.join(ROOT, 'webdesktop-deploy/scripts/db_migrate.cjs'));
const PROD = path.join(ROOT, 'drizzle/migrations');
const V2 = path.join(ROOT, 'tests/fixtures/db-migrations-v2');
const FAIL = path.join(ROOT, 'tests/fixtures/db-migrations-fail');
const report = { passed: 0, failed: 0, skipped: 0, not_executed: 0, tests: [] };
const ADMIN = process.env.WD_TEST_PG_ADMIN_URL;

async function test(name, fn, needsDb = false) {
  if (needsDb && !ADMIN) { report.not_executed++; report.tests.push({ name, result: 'not_executed', detail: 'WD_TEST_PG_ADMIN_URL not set' }); return; }
  try { await fn(); report.passed++; report.tests.push({ name, result: 'passed' }); console.log('PASS', name); }
  catch (e) { report.failed++; report.tests.push({ name, result: 'failed', detail: String(e.message) + (e.detail ? ' :: ' + JSON.stringify(e.detail).slice(0, 1500) : '') }); console.log('FAIL', name, e.message, e.detail ? JSON.stringify(e.detail).slice(0, 1500) : ''); }
}
const refused = (fn, code) => { try { fn(); } catch (e) { assert.equal(e.code, code, e.message); return; } assert.fail('expected refusal ' + code); };
async function refusedAsync(fn, code) { try { await fn(); } catch (e) { assert.equal(e.code, code, e.message); return e; } assert.fail('expected refusal ' + code); }

function tempCopy(src, change) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wd-mig-'));
  for (const f of fs.readdirSync(src)) fs.copyFileSync(path.join(src, f), path.join(dir, f));
  if (change) change(dir);
  return dir;
}
const rewriteManifest = (dir, fn) => { const p = path.join(dir, 'manifest.json'); const m = JSON.parse(fs.readFileSync(p)); fn(m); fs.writeFileSync(p, JSON.stringify(m, null, 2)); };

/* ---------------------------------------------------------------- part 1 */
await test('product manifest: pinned SQL, canonical reviewed schema, initial-only chain', () => {
  const m = M.loadManifest(PROD);
  assert.deepEqual(m.migrations.map(x => x.id), ['0001_initial']);
  assert.deepEqual(Object.keys(m.latest.schema.tables).sort(), ['admin_users', 'auth_config', 'desktop_settings', 'desktop_workspace', 'fs_nodes']);
  assert.equal(M.sha256(fs.readFileSync(path.join(PROD, '0001_initial.sql'))), m.latest.sha256);
  // Test-only fixtures never enter the product migration set.
  assert.ok(!fs.readdirSync(PROD).some(f => f.includes('test')));
});

await test('schema files match src/db/schema.ts column by column', () => {
  const schemaTs = fs.readFileSync(path.join(ROOT, 'src/db/schema.ts'), 'utf8');
  const tables = M.loadManifest(PROD).latest.schema.tables;
  for (const [, table, body] of schemaTs.matchAll(/pgTable\("([a-z_]+)", \{([\s\S]*?)\n\}\);/g)) {
    const columns = [...body.matchAll(/^\s+\w+: \w+\("([a-z_]+)"/gm)].map(x => x[1]).sort();
    assert.deepEqual(Object.keys(tables[table].columns).sort(), columns, table);
  }
  assert.equal(Object.keys(tables).length, [...schemaTs.matchAll(/pgTable\(/g)].length);
});

await test('allowlist accepts only transactional expand DDL', () => {
  for (const ok of ['CREATE TABLE "a" ("id" integer PRIMARY KEY NOT NULL);', 'ALTER TABLE "a" ADD COLUMN "b" text;', 'ALTER TABLE "a" ADD COLUMN "c" integer DEFAULT 0 NOT NULL;',
    'CREATE INDEX "a_b_idx" ON "a" USING btree ("b");', 'ALTER TABLE "a" ADD CONSTRAINT "a_b_u" UNIQUE("b");'])
    M.checkSql(ok, 'expand');
  for (const [bad, code] of [['DROP TABLE "a";', 'UNSUPPORTED_STATEMENT'], ['ALTER TABLE "a" DROP COLUMN "b";', 'UNSUPPORTED_STATEMENT'],
    ['CREATE INDEX CONCURRENTLY "x" ON "a" ("b");', 'NOT_TRANSACTIONAL'], ['ALTER TABLE "a" ALTER COLUMN "b" TYPE integer;', 'UNSUPPORTED_STATEMENT'],
    ['TRUNCATE "a";', 'UNSUPPORTED_STATEMENT'], ['BEGIN; CREATE TABLE "a" ("id" integer);', 'UNSUPPORTED_STATEMENT'],
    ['UPDATE "a" SET "b" = 1;', 'UNSUPPORTED_STATEMENT'], ['DELETE FROM "a";', 'UNSUPPORTED_STATEMENT'], ['INSERT INTO "a" VALUES (1);', 'UNSUPPORTED_STATEMENT'],
    ['DO $$ BEGIN END $$;', 'UNSUPPORTED_STATEMENT'], ['ALTER TABLE "a" ADD COLUMN "b" text NOT NULL;', 'UNSUPPORTED_STATEMENT'],
    ['ALTER TABLE "a" RENAME TO "b";', 'UNSUPPORTED_STATEMENT'], ['GRANT ALL ON "a" TO public;', 'UNSUPPORTED_STATEMENT'],
    ['CREATE FUNCTION f() RETURNS int AS \'select 1\' LANGUAGE sql;', 'UNSUPPORTED_STATEMENT'], ['VACUUM;', 'UNSUPPORTED_STATEMENT'],
    ['CREATE TABLE "a" ("id" integer); DROP TABLE "b"', 'UNSUPPORTED_STATEMENT'], ["CREATE TABLE \"a\" (\"id\" text DEFAULT 'x'; DROP TABLE b", 'UNSUPPORTED_STATEMENT'], ["CREATE TABLE \"a\" (\"id\" text DEFAULT 'x); DROP TABLE b", 'INVALID_SQL'], ['', 'INVALID_SQL']])
    refused(() => M.checkSql(bad, 'expand'), code);
  // Referential action words inside a foreign key are not data changes.
  M.checkSql('ALTER TABLE "a" ADD CONSTRAINT "f" FOREIGN KEY ("p") REFERENCES "public"."a"("id") ON DELETE cascade ON UPDATE no action;', 'initial');
  refused(() => M.checkSql('CREATE TABLE "a" ("id" integer);', 'destructive'), 'INVALID_MANIFEST');
});

await test('manifest tampering is refused before any database access', () => {
  const cases = [
    ['sql digest', d => fs.appendFileSync(path.join(d, '0002_test_probe.sql'), '\n'), 'DIGEST_MISMATCH'],
    ['schema digest', d => fs.writeFileSync(path.join(d, '0002_test_probe.schema.json'), '{}'), 'DIGEST_MISMATCH'],
    ['wrong previous', d => rewriteManifest(d, m => { m.migrations[1].previous = null; }), 'INVALID_MANIFEST'],
    ['duplicate id', d => rewriteManifest(d, m => { m.migrations[1].id = '0001_initial'; }), 'INVALID_MANIFEST'],
    ['bad id', d => rewriteManifest(d, m => { m.migrations[1].id = '../x'; }), 'INVALID_MANIFEST'],
    ['non transactional', d => rewriteManifest(d, m => { m.migrations[1].transactional = false; }), 'NOT_TRANSACTIONAL'],
    ['maintenance', d => rewriteManifest(d, m => { m.migrations[1].maintenance = true; }), 'INVALID_MANIFEST'],
    ['second initial', d => rewriteManifest(d, m => { m.migrations[1].kind = 'initial'; }), 'INVALID_MANIFEST'],
    ['unknown field', d => rewriteManifest(d, m => { m.migrations[1].sql_path = '/tmp/x.sql'; }), 'INVALID_MANIFEST'],
    ['top-level field', d => rewriteManifest(d, m => { m.extra = 1; }), 'INVALID_MANIFEST'],
    ['forbidden sql with matching digest', d => {
      const sql = 'DROP TABLE "fs_nodes";\n'; fs.writeFileSync(path.join(d, '0002_test_probe.sql'), sql);
      rewriteManifest(d, m => { m.migrations[1].sha256 = M.sha256(sql); });
    }, 'UNSUPPORTED_STATEMENT'],
  ];
  for (const [name, change, code] of cases) refused(() => M.loadManifest(tempCopy(V2, change)), code);
});

/* ---------------------------------------------------------------- part 2 */
let pg; try { pg = require(path.join(ROOT, 'node_modules/pg')); } catch { pg = null; }
const adminUrl = ADMIN ? new URL(ADMIN) : null;
let admin;
async function freshDb(tag) {
  const name = 'wd_t_' + tag + '_' + Math.random().toString(16).slice(2, 8);
  await admin.query('CREATE DATABASE "' + name + '"');
  const url = new URL(ADMIN); url.pathname = '/' + name;
  const client = new pg.Client({ connectionString: url.toString() }); await client.connect();
  return { name, client, url: url.toString() };
}
const manifest = dir => M.loadManifest(dir);
const count = async (c, table) => Number((await c.query(`SELECT count(*) FROM "${table}"`)).rows[0].count);
async function seed(c) {
  // Real login/business records: admin hash, AUTH_KEY-like secret, session version, files, settings, workspace.
  await c.query(`INSERT INTO admin_users (username, password_hash, must_change) VALUES ('admin', 's1$00ab$' || repeat('cd', 64), false)`);
  await c.query(`INSERT INTO auth_config (key, value) VALUES ('secret', repeat('ab', 32)), ('desk_hash', 's1$11$' || repeat('ef', 64)), ('desk_ver', '7')`);
  await c.query(`INSERT INTO fs_nodes (parent_id, name, kind, content) VALUES (NULL, 'Desktop', 'folder', '')`);
  await c.query(`INSERT INTO fs_nodes (parent_id, name, kind, content) VALUES (1, '中文 note.txt', 'file', 'hello 4B-2')`);
  await c.query(`INSERT INTO desktop_settings (key, value) VALUES ('wallpaper', 'aurora'), ('clock24', '1')`);
  await c.query(`INSERT INTO desktop_workspace (id, snapshot, version) VALUES (1, '{"windows":[{"id":"w1"}]}', 3)`);
}

if (ADMIN && pg) { admin = new pg.Client({ connectionString: ADMIN }); await admin.connect(); }

await test('fresh database: init applies 0001 exactly as reviewed; second init refused', async () => {
  const { client } = await freshDb('fresh');
  const m = manifest(PROD);
  assert.equal((await M.run('status', client, m)).state, 'empty');
  const r = await M.run('init', client, m);
  assert.equal(r.state, 'recorded'); assert.equal(r.current, '0001_initial'); assert.deepEqual(r.pending, []);
  const c = await M.classify(client, m);
  assert.equal(c.fingerprint, m.latest.fingerprint);
  await seed(client);
  await refusedAsync(() => M.run('init', client, m), 'NOT_EMPTY');
  assert.equal(await count(client, 'admin_users'), 1);
  await client.end();
}, true);

await test('legacy database created by drizzle-kit push --force is baselined without changing data', async () => {
  const { client, url } = await freshDb('legacy');
  execFileSync(path.join(ROOT, 'node_modules/.bin/drizzle-kit'), ['push', '--force'], { cwd: ROOT, env: { ...process.env, DATABASE_URL: url }, stdio: 'ignore', timeout: 120000 });
  await seed(client);
  const m = manifest(PROD);
  const before = await M.digests(client, m.latest.schema);
  assert.equal((await M.run('status', client, m)).state, 'legacy-exact');
  const r = await M.run('baseline', client, m);
  assert.equal(r.state, 'recorded'); assert.equal(r.current, '0001_initial');
  assert.deepEqual(await M.digests(client, m.latest.schema), before);
  assert.equal((await client.query(`SELECT method FROM ${M.RECORD}`)).rows[0].method, 'baseline');
  await refusedAsync(() => M.run('baseline', client, m), 'BASELINE_REFUSED');
  await client.end();
}, true);

await test('legacy variants are refused with name-only diagnostics and untouched data', async () => {
  const m = manifest(PROD);
  // update.sh-style DDL: same columns, PostgreSQL default constraint names.
  const variant = `CREATE TABLE fs_nodes (id serial PRIMARY KEY, parent_id integer REFERENCES fs_nodes(id) ON DELETE CASCADE, name text NOT NULL, kind text NOT NULL, content text NOT NULL DEFAULT '', trashed_from integer, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE desktop_settings (key text PRIMARY KEY, value text NOT NULL); CREATE TABLE auth_config (key text PRIMARY KEY, value text NOT NULL);
    CREATE TABLE admin_users (id serial PRIMARY KEY, username text NOT NULL UNIQUE, password_hash text NOT NULL, must_change boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE desktop_workspace (id integer PRIMARY KEY, snapshot jsonb NOT NULL, version integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now());`;
  const cases = [
    ['mismatch', variant, 'mismatch'],
    ['partial', `CREATE TABLE auth_config (key text PRIMARY KEY, value text NOT NULL); CREATE TABLE admin_users (id serial PRIMARY KEY, username text NOT NULL, password_hash text NOT NULL, must_change boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(), constraint admin_users_username_unique unique (username));`, 'partial'],
    ['unknown', m.latest.sql + ';CREATE TABLE "extra_app" ("id" integer);', 'unknown'],
    ['type changed', m.latest.sql.replace('"version" integer DEFAULT 1 NOT NULL', '"version" bigint DEFAULT 1 NOT NULL'), 'mismatch'],
    ['missing index-backed constraint', m.latest.sql.replace(',\n\tCONSTRAINT "admin_users_username_unique" UNIQUE("username")', ''), 'mismatch'],
    ['nullable drift', m.latest.sql.replace('"name" text NOT NULL', '"name" text'), 'mismatch'],
  ];
  for (const [tag, sql, state] of cases) {
    const { client } = await freshDb(tag.replace(/\W/g, ''));
    await client.query(sql.replaceAll('--> statement-breakpoint', ''));
    await client.query(`INSERT INTO auth_config (key, value) VALUES ('secret', 'keep-me')`);
    const s = await M.run('status', client, m);
    assert.equal(s.state, state, tag); assert.ok(s.problems.length > 0, tag); assert.equal(s.baseline_allowed, false);
    assert.ok(s.problems.every(p => /^(missing|unexpected|different) [a-z0-9_.]+$/.test(p)), JSON.stringify(s.problems));
    await refusedAsync(() => M.run('baseline', client, m), 'BASELINE_REFUSED');
    await refusedAsync(() => M.run('migrate', client, m), 'MIGRATION_REFUSED');
    assert.equal((await client.query(`SELECT value FROM auth_config WHERE key='secret'`)).rows[0].value, 'keep-me');
    assert.equal((await client.query(`SELECT 1 FROM pg_class WHERE relname=$1`, [M.RECORD])).rowCount, 0);
    await client.end();
  }
}, true);

await test('V1 -> V2 expand: data kept, reviewed schema, V1-style reads/writes still work, repeat is a no-op', async () => {
  const { client } = await freshDb('v2');
  await M.run('init', client, manifest(PROD)); await seed(client);
  const v1schema = manifest(PROD).latest.schema;
  const before = await M.digests(client, v1schema);
  const m2 = manifest(V2);
  const p = await M.run('status', client, m2);
  assert.deepEqual([p.state, p.current, p.pending, p.migration_allowed], ['recorded', '0001_initial', ['0002_test_probe'], true]);
  const r = await M.run('migrate', client, m2);
  assert.deepEqual([r.state, r.current, r.pending], ['recorded', '0002_test_probe', []]);
  assert.deepEqual(await M.digests(client, v1schema), before);
  // V1 program queries (column lists from schema.ts only).
  await client.query(`INSERT INTO desktop_settings (key, value) VALUES ('accent', 'blue') ON CONFLICT (key) DO UPDATE SET value = excluded.value`);
  await client.query(`INSERT INTO fs_nodes (parent_id, name, kind, content) VALUES (1, 'v1 after v2', 'file', 'ok')`);
  await client.query(`UPDATE desktop_workspace SET snapshot = '{"windows":[]}', version = version + 1 WHERE id = 1`);
  assert.equal((await client.query(`SELECT key, value FROM desktop_settings WHERE key='accent'`)).rows[0].value, 'blue');
  const records = await count(client, M.RECORD);
  const again = await M.run('migrate', client, m2);
  assert.equal(again.current, '0002_test_probe'); assert.equal(await count(client, M.RECORD), records);
  // The V1 release sees a newer, unknown version and refuses to act on it.
  assert.equal((await M.run('status', client, manifest(PROD))).state, 'unknown-version');
  await client.end();
}, true);

await test('failing migration rolls back completely; version does not advance', async () => {
  const { client } = await freshDb('fail');
  await M.run('init', client, manifest(PROD)); await seed(client);
  await M.run('migrate', client, manifest(V2));
  const before = await M.digests(client, manifest(V2).latest.schema);
  const desc = M.sha256(M.canonical(await M.describe(client)));
  const e = await refusedAsync(() => M.run('migrate', client, manifest(FAIL)), '42P01');
  void e;
  const after = await M.classify(client, manifest(V2));
  assert.deepEqual([after.state, after.current], ['recorded', '0002_test_probe']);
  assert.equal(M.sha256(M.canonical(await M.describe(client))), desc);
  assert.equal((await client.query(`SELECT 1 FROM information_schema.columns WHERE table_name='desktop_settings' AND column_name='probe_fail'`)).rowCount, 0);
  assert.deepEqual(await M.digests(client, manifest(V2).latest.schema), before);
  assert.equal(await count(client, M.RECORD), 2);
  await client.end();
}, true);

await test('concurrent migrations: one runs, the other is BUSY; one record row', async () => {
  const { client, url } = await freshDb('conc');
  await M.run('init', client, manifest(PROD));
  // Hold a lock the expand step needs, so migration A is provably in flight.
  const blocker = new pg.Client({ connectionString: url }); await blocker.connect();
  await blocker.query('BEGIN'); await blocker.query('LOCK TABLE desktop_settings IN ACCESS EXCLUSIVE MODE');
  const a = new pg.Client({ connectionString: url }); await a.connect();
  const b = new pg.Client({ connectionString: url }); await b.connect();
  const running = M.run('migrate', a, manifest(V2));
  for (let i = 0; i < 100; i++) {
    const held = (await client.query(`SELECT count(*)::int AS n FROM pg_locks WHERE locktype='advisory' AND granted`)).rows[0].n;
    if (held) break; await new Promise(r => setTimeout(r, 50));
  }
  await refusedAsync(() => M.run('migrate', b, manifest(V2)), 'BUSY');
  await blocker.query('COMMIT'); await blocker.end();
  const r = await running;
  assert.equal(r.current, '0002_test_probe');
  assert.deepEqual((await client.query(`SELECT id FROM ${M.RECORD} ORDER BY seq`)).rows.map(x => x.id), ['0001_initial', '0002_test_probe']);
  await a.end(); await b.end(); await client.end();
}, true);

await test('drift, corrupted records and unknown versions are refused, never repaired', async () => {
  const m = manifest(PROD);
  const drift = await freshDb('drift');
  await M.run('init', drift.client, m);
  await drift.client.query('ALTER TABLE fs_nodes DROP CONSTRAINT fs_nodes_parent_id_fs_nodes_id_fk');
  const d = await M.run('status', drift.client, m);
  assert.equal(d.state, 'drift'); assert.ok(d.problems.includes('missing tables.fs_nodes.constraints.fs_nodes_parent_id_fs_nodes_id_fk'));
  await refusedAsync(() => M.run('migrate', drift.client, manifest(V2)), 'MIGRATION_REFUSED');
  await drift.client.end();
  for (const [tag, sql, state] of [['sha', `UPDATE ${M.RECORD} SET sha256 = repeat('0', 64)`, 'record-corrupt'], ['method', `UPDATE ${M.RECORD} SET method = 'forced'`, 'record-corrupt'],
    ['future', `INSERT INTO ${M.RECORD} (seq, id, sha256, previous, fingerprint, method) VALUES (2, '0009_future', repeat('1', 64), '0001_initial', repeat('2', 64), 'applied')`, 'unknown-version'],
    ['empty', `DELETE FROM ${M.RECORD}`, 'record-corrupt']]) {
    const x = await freshDb('rec' + tag);
    await M.run('init', x.client, m); await x.client.query(sql);
    assert.equal((await M.run('status', x.client, m)).state, state, tag);
    await refusedAsync(() => M.run('migrate', x.client, m), 'MIGRATION_REFUSED');
    await x.client.end();
  }
  const shape = await freshDb('recshape');
  await M.run('init', shape.client, m); await shape.client.query(`ALTER TABLE ${M.RECORD} ADD COLUMN forged text`);
  await refusedAsync(() => M.run('status', shape.client, m), 'RECORD_CORRUPT');
  await shape.client.end();
}, true);

if (admin) {
  for (const r of (await admin.query(`SELECT datname FROM pg_database WHERE datname LIKE 'wd_t_%'`)).rows) await admin.query('DROP DATABASE "' + r.datname + '" WITH (FORCE)');
  await admin.end();
}
if (process.env.WD_TEST_REPORT) fs.writeFileSync(process.env.WD_TEST_REPORT, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ passed: report.passed, failed: report.failed, skipped: report.skipped, not_executed: report.not_executed }));
if (report.failed) process.exitCode = 1;
