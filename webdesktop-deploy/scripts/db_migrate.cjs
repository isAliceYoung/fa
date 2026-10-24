#!/usr/bin/env node
'use strict';
// Phase 4B-2: versioned PostgreSQL migrations, strict legacy baseline and
// verified backups. Root maintenance tool; no HTTP entry, no arbitrary SQL.
//
//   node db_migrate.cjs status|init|baseline|migrate|backup <absolute app dir>
//
// Migrations come ONLY from <app>/drizzle/migrations (part of the 4B-1 signed,
// per-file verified release). Every SQL file is pinned by SHA256 in the
// manifest; every version pins the exact resulting schema description.
// Passwords, connection strings and row contents are never printed.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync, spawn } = require('node:child_process');
const { createRequire } = require('node:module');
const { parseEnv } = require('node:util');

const RECORD = 'webdesktop_schema_migrations';
const LOCK = [0x57444231, 2]; // 'WDB1', schema migrations
const ID = /^\d{4}_[a-z0-9_]{1,48}$/;
const HEX = /^[0-9a-f]{64}$/;
const BACKUP_DIR = '/var/lib/webdesktop-db/backups';
const PG_DATA = '/var/lib/postgresql';
const RESERVE = 512 * 1024 * 1024;
const KINDS = new Set(['initial', 'expand']);

class Refusal extends Error {
  constructor(code, message, detail) { super(message); this.code = code; this.detail = detail; }
}
const refuse = (code, message, detail) => { throw new Refusal(code, message, detail); };
const require_ = (ok, code, message) => { if (!ok) refuse(code, message); };
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');

function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
}

/* ------------------------------------------------------------ SQL policy */
// Split on ';' outside quotes, dropping -- and /* */ comments.
function statements(sql) {
  const out = []; let cur = ''; let i = 0;
  while (i < sql.length) {
    const c = sql[i], n = sql[i + 1];
    if (c === '-' && n === '-') { while (i < sql.length && sql[i] !== '\n') i++; cur += ' '; continue; }
    if (c === '/' && n === '*') { const e = sql.indexOf('*/', i + 2); require_(e >= 0, 'INVALID_SQL', 'Unterminated comment'); i = e + 2; cur += ' '; continue; }
    if (c === "'" || c === '"') {
      let j = i + 1;
      for (;;) { require_(j < sql.length, 'INVALID_SQL', 'Unterminated quote'); if (sql[j] === c) { if (sql[j + 1] === c) { j += 2; continue; } break; } j++; }
      cur += sql.slice(i, j + 1); i = j + 1; continue;
    }
    if (c === '$') refuse('UNSUPPORTED_STATEMENT', 'Dollar-quoted bodies (functions/DO blocks) are not allowed');
    if (c === ';') { if (cur.trim()) out.push(cur.trim()); cur = ''; i++; continue; }
    cur += c; i++;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

// Reviewed allowlist: only transactional, additive (expand) DDL. Anything
// else - DROP, RENAME, ALTER ... TYPE, data rewrites, CONCURRENTLY, transaction
// control, functions, grants - is refused before the database is touched.
const NAME = '"[a-z_][a-z0-9_]*"';
const ALLOWED = [
  ['create-table', new RegExp('^CREATE TABLE ' + NAME + ' \\(', 'i')],
  ['add-column', new RegExp('^ALTER TABLE ' + NAME + ' ADD COLUMN ' + NAME + ' ', 'i')],
  ['add-constraint', new RegExp('^ALTER TABLE ' + NAME + ' ADD CONSTRAINT ' + NAME + ' (FOREIGN KEY|UNIQUE|CHECK) ?\\(', 'i')],
  ['create-index', new RegExp('^CREATE (UNIQUE )?INDEX ' + NAME + ' ON ' + NAME + ' ', 'i')],
];
const FORBIDDEN = /\b(DROP|TRUNCATE|RENAME|DELETE\s+FROM|UPDATE\s+"|INSERT|COPY|GRANT|REVOKE|OWNER|VACUUM|CLUSTER|REINDEX|LOCK|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|TRANSACTION|SET\s+(ROLE|SESSION)|RESET|SECURITY|EXECUTE|CONCURRENTLY|TYPE\s+\w)\b/i;

function checkSql(sql, kind) {
  require_(KINDS.has(kind), 'INVALID_MANIFEST', 'Unknown migration kind');
  const list = statements(sql);
  require_(list.length > 0 && list.length <= 200, 'INVALID_SQL', 'Migration has no or too many statements');
  return list.map(stmt => {
    const flat = stmt.replace(/\s+/g, ' ');
    if (/\bCONCURRENTLY\b/i.test(flat)) refuse('NOT_TRANSACTIONAL', 'Non-transactional statements (CONCURRENTLY) are not supported');
    // Referential actions are part of a foreign-key definition, not data changes.
    const scan = flat.replace(/'(?:[^']|'')*'/g, "''").replace(/\bON (DELETE|UPDATE) (CASCADE|NO ACTION|RESTRICT|SET NULL|SET DEFAULT)\b/gi, '');
    const match = ALLOWED.find(([, re]) => re.test(flat));
    if (!match || FORBIDDEN.test(scan)) refuse('UNSUPPORTED_STATEMENT', 'Statement is not an allowed transactional expand operation: ' + flat.slice(0, 60));
    if (match[0] === 'add-column' && /\bNOT NULL\b/i.test(scan) && !/\bDEFAULT\b/i.test(scan)) refuse('UNSUPPORTED_STATEMENT', 'A new NOT NULL column needs a DEFAULT (expand-only)');
    if (kind === 'expand' && match[0] === 'create-table' && /\bREFERENCES\b/i.test(scan) && /\bCASCADE\b/i.test(flat)) refuse('UNSUPPORTED_STATEMENT', 'Cascading references are not allowed in automatic expand migrations');
    return match[0];
  });
}

/* ------------------------------------------------------------ manifest */
function protectedTree(dir) {
  // Running as root: the migration set must be part of a root-owned,
  // non-writable release tree (4B-1/4A-R1 rules); never a user-supplied path.
  if (!process.getuid || process.getuid() !== 0) return;
  for (let p = dir; ; p = path.dirname(p)) {
    const s = fs.lstatSync(p);
    require_(!s.isSymbolicLink() && s.uid === 0 && !(s.mode & 0o022), 'UNTRUSTED_MIGRATIONS', 'Migration directory is not a protected release tree');
    if (p === path.dirname(p)) break;
  }
}

function loadManifest(dir) {
  const raw = fs.readFileSync(path.join(dir, 'manifest.json'));
  let m; try { m = JSON.parse(raw); } catch { refuse('INVALID_MANIFEST', 'Migration manifest is not valid JSON'); }
  require_(m && m.schema === 1 && m.format === 'webdesktop-db-migrations' && Array.isArray(m.migrations) && m.migrations.length >= 1, 'INVALID_MANIFEST', 'Unsupported migration manifest');
  require_(Object.keys(m).sort().join(',') === 'format,migrations,schema', 'INVALID_MANIFEST', 'Unknown manifest fields');
  const seen = new Set(); let previous = null;
  const migrations = m.migrations.map((x, index) => {
    require_(x && Object.keys(x).sort().join(',') === 'fingerprint,id,kind,maintenance,previous,sha256,transactional', 'INVALID_MANIFEST', 'Unknown migration fields');
    require_(ID.test(x.id) && !seen.has(x.id), 'INVALID_MANIFEST', 'Invalid or duplicate migration id');
    require_(x.previous === previous, 'INVALID_MANIFEST', 'Migration previous version does not match the chain: ' + x.id);
    require_(index > 0 || x.kind === 'initial', 'INVALID_MANIFEST', 'The first migration must be the initial schema');
    require_(index === 0 || x.kind === 'expand', 'INVALID_MANIFEST', 'Only expand migrations may follow the initial schema: ' + x.id);
    require_(x.transactional === true, 'NOT_TRANSACTIONAL', 'Only transactional migrations are supported: ' + x.id);
    require_(x.maintenance === false, 'INVALID_MANIFEST', 'Migrations requiring a maintenance window are not supported automatically: ' + x.id);
    require_(HEX.test(x.sha256) && HEX.test(x.fingerprint), 'INVALID_MANIFEST', 'Invalid digest');
    const sql = fs.readFileSync(path.join(dir, x.id + '.sql'));
    require_(sha256(sql) === x.sha256, 'DIGEST_MISMATCH', 'Migration SQL digest differs from the manifest: ' + x.id);
    const schemaFile = fs.readFileSync(path.join(dir, x.id + '.schema.json'));
    require_(sha256(schemaFile) === x.fingerprint, 'DIGEST_MISMATCH', 'Schema description digest differs from the manifest: ' + x.id);
    const schema = JSON.parse(schemaFile);
    require_(canonical(schema) === schemaFile.toString('utf8'), 'INVALID_MANIFEST', 'Schema description is not canonical: ' + x.id);
    const ops = checkSql(sql.toString('utf8'), x.kind);
    seen.add(x.id); previous = x.id;
    return { ...x, sql: sql.toString('utf8'), schema, ops };
  });
  return { dir, migrations, latest: migrations[migrations.length - 1] };
}

/* ------------------------------------------------------------ catalog description */
async function describe(client, { only } = {}) {
  const q = async (text) => (await client.query(text)).rows;
  await client.query("SET search_path TO public");
  // Exclude exactly the reviewed record table; same-prefix objects are never hidden.
  const keep = name => only ? name === only : name !== RECORD;
  const me = (await q('SELECT current_user AS u'))[0].u;
  const tables = {}, sequences = {}, other = { relations: [], triggers: [], rules: [], policies: [], functions: [], types: [], extensions: [], schemas: [] };
  for (const r of await q(`SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner) AS owner, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1`)) {
    if (r.relkind === 'r' || r.relkind === 'p') { if (keep(r.relname)) tables[r.relname] = { columns: {}, constraints: {}, indexes: {}, rls: r.relrowsecurity, owned: r.owner === me, kind: r.relkind }; }
    else if (r.relkind === 'S' || r.relkind === 'i' || r.relkind === 'I') continue;
    else other.relations.push(r.relname + ':' + r.relkind);
  }
  for (const r of await q(`SELECT c.relname, a.attname, format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull, pg_get_expr(d.adbin,d.adrelid) AS dflt, a.attidentity::text AS ident, a.attgenerated::text AS gen,
      CASE WHEN a.attcollation<>0 AND a.attcollation<>t.typcollation THEN co.collname END AS coll
    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_type t ON t.oid=a.atttypid
    LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum LEFT JOIN pg_collation co ON co.oid=a.attcollation
    WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped`)) {
    if (tables[r.relname]) tables[r.relname].columns[r.attname] = { type: r.type, not_null: r.attnotnull, default: r.dflt, identity: r.ident, generated: r.gen, collation: r.coll };
  }
  for (const r of await q(`SELECT c.relname, con.conname, con.contype::text AS contype, pg_get_constraintdef(con.oid) AS def FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'`)) {
    if (tables[r.relname]) tables[r.relname].constraints[r.conname] = { type: r.contype, def: r.def };
  }
  for (const r of await q(`SELECT t.relname AS tbl, i.relname, pg_get_indexdef(i.oid) AS def FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public'`)) {
    if (tables[r.tbl]) tables[r.tbl].indexes[r.relname] = r.def;
  }
  for (const r of await q(`SELECT c.relname, format_type(s.seqtypid,NULL) AS type, s.seqstart::text AS start, s.seqincrement::text AS inc, s.seqmin::text AS min, s.seqmax::text AS max, s.seqcache::text AS cache, s.seqcycle,
      (SELECT t.relname||'.'||a.attname FROM pg_depend d JOIN pg_class t ON t.oid=d.refobjid JOIN pg_attribute a ON a.attrelid=d.refobjid AND a.attnum=d.refobjsubid WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype IN ('a','i') LIMIT 1) AS owned_by,
      pg_get_userbyid(c.relowner) AS owner
    FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'`)) {
    // Ownership, never a name prefix, decides whether a sequence belongs to the record table.
    if (only ? Boolean(r.owned_by?.startsWith(only + '.')) : true) sequences[r.relname] = { type: r.type, start: r.start, increment: r.inc, min: r.min, max: r.max, cache: r.cache, cycle: r.seqcycle, owned_by: r.owned_by, owned: r.owner === me };
  }
  if (!only) {
    other.triggers = (await q(`SELECT c.relname||'.'||t.tgname AS n FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace ns ON ns.oid=c.relnamespace WHERE ns.nspname='public' AND NOT t.tgisinternal`)).map(r => r.n);
    other.rules = (await q(`SELECT tablename||'.'||rulename AS n FROM pg_rules WHERE schemaname='public'`)).map(r => r.n);
    other.policies = (await q(`SELECT tablename||'.'||policyname AS n FROM pg_policies WHERE schemaname='public'`)).map(r => r.n);
    other.functions = (await q(`SELECT p.proname||'('||pg_get_function_identity_arguments(p.oid)||')' AS n FROM pg_proc p JOIN pg_namespace ns ON ns.oid=p.pronamespace WHERE ns.nspname='public'`)).map(r => r.n);
    other.types = (await q(`SELECT t.typname||':'||t.typtype::text AS n FROM pg_type t JOIN pg_namespace ns ON ns.oid=t.typnamespace WHERE ns.nspname='public' AND (t.typtype IN ('e','d','r','m') OR (t.typtype='c' AND (SELECT relkind FROM pg_class WHERE oid=t.typrelid)='c'))`)).map(r => r.n);
    other.extensions = (await q(`SELECT extname AS n FROM pg_extension WHERE extname<>'plpgsql'`)).map(r => r.n);
    other.schemas = (await q(`SELECT nspname AS n FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg\\_%'`)).map(r => r.n);
    for (const k of Object.keys(other)) other[k].sort();
  }
  return only ? { tables, sequences } : { tables, sequences, other };
}

// Exact definition of the record table itself (validated, never adopted).
const RECORD_DDL = `CREATE TABLE "${RECORD}" ("seq" integer PRIMARY KEY NOT NULL, "id" text NOT NULL, "sha256" text NOT NULL, "previous" text, "fingerprint" text NOT NULL, "method" text NOT NULL, "applied_at" timestamp with time zone DEFAULT now() NOT NULL, CONSTRAINT "${RECORD}_id_unique" UNIQUE("id"))`;
const RECORD_SCHEMA = { sequences: {}, tables: { [RECORD]: {
  columns: {
    applied_at: { collation: null, default: 'now()', generated: '', identity: '', not_null: true, type: 'timestamp with time zone' },
    fingerprint: { collation: null, default: null, generated: '', identity: '', not_null: true, type: 'text' },
    id: { collation: null, default: null, generated: '', identity: '', not_null: true, type: 'text' },
    method: { collation: null, default: null, generated: '', identity: '', not_null: true, type: 'text' },
    previous: { collation: null, default: null, generated: '', identity: '', not_null: false, type: 'text' },
    seq: { collation: null, default: null, generated: '', identity: '', not_null: true, type: 'integer' },
    sha256: { collation: null, default: null, generated: '', identity: '', not_null: true, type: 'text' },
  },
  constraints: { [RECORD + '_id_unique']: { def: 'UNIQUE (id)', type: 'u' }, [RECORD + '_pkey']: { def: 'PRIMARY KEY (seq)', type: 'p' } },
  indexes: { [RECORD + '_id_unique']: `CREATE UNIQUE INDEX ${RECORD}_id_unique ON public.${RECORD} USING btree (id)`, [RECORD + '_pkey']: `CREATE UNIQUE INDEX ${RECORD}_pkey ON public.${RECORD} USING btree (seq)` },
  kind: 'r', owned: true, rls: false,
} } };

function diff(expected, actual, prefix = '') {
  // Object names only - never row data.
  const out = [];
  const keys = new Set([...Object.keys(expected || {}), ...Object.keys(actual || {})]);
  for (const k of [...keys].sort()) {
    const e = expected?.[k], a = actual?.[k], p = prefix ? prefix + '.' + k : k;
    if (e === undefined) out.push('unexpected ' + p);
    else if (a === undefined) out.push('missing ' + p);
    else if (e && typeof e === 'object' && !Array.isArray(e) && a && typeof a === 'object' && !Array.isArray(a)) out.push(...diff(e, a, p));
    else if (canonical(e) !== canonical(a)) out.push('different ' + p);
  }
  return out;
}

/* ------------------------------------------------------------ classification */
async function records(client) {
  const exists = (await client.query(`SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=$1`, [RECORD])).rowCount > 0;
  if (!exists) return null;
  const shape = await describe(client, { only: RECORD });
  const problems = diff(RECORD_SCHEMA, shape);
  if (problems.length) refuse('RECORD_CORRUPT', 'Migration record table has an unexpected structure', problems);
  return (await client.query(`SELECT seq, id, sha256, previous, fingerprint, method FROM "${RECORD}" ORDER BY seq`)).rows;
}

async function classify(client, manifest) {
  const actual = await describe(client);
  const fingerprint = sha256(canonical(actual));
  const rows = await records(client);
  const base = { fingerprint, latest: manifest.latest.id };
  const expectedNames = Object.keys(manifest.migrations[0].schema.tables);
  if (rows === null) {
    const names = Object.keys(actual.tables);
    const otherCount = Object.values(actual.other).reduce((n, list) => n + list.length, 0);
    if (!names.length && !Object.keys(actual.sequences).length && !otherCount) return { ...base, state: 'empty', current: null };
    if (fingerprint === manifest.migrations[0].fingerprint) return { ...base, state: 'legacy-exact', current: null };
    const unknown = names.filter(n => !expectedNames.includes(n));
    const missing = expectedNames.filter(n => !names.includes(n));
    const problems = diff(manifest.migrations[0].schema, actual);
    if (unknown.length || otherCount) return { ...base, state: 'unknown', current: null, problems };
    if (missing.length) return { ...base, state: 'partial', current: null, problems };
    return { ...base, state: 'mismatch', current: null, problems };
  }
  // Recorded: the whole history must be an exact prefix of this release's chain.
  if (!rows.length) return { ...base, state: 'record-corrupt', current: null, problems: ['empty migration record'] };
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i], m = manifest.migrations[i];
    if (!m || r.seq !== i + 1 || r.id !== m.id || r.sha256 !== m.sha256 || r.previous !== m.previous || r.fingerprint !== m.fingerprint || !['applied', 'baseline'].includes(r.method) || (r.method === 'baseline' && i !== 0)) {
      return { ...base, state: m ? 'record-corrupt' : 'unknown-version', current: r.id, problems: ['record ' + (i + 1) + ' does not match this release'] };
    }
  }
  const current = manifest.migrations[rows.length - 1];
  if (current.fingerprint !== fingerprint) return { ...base, state: 'drift', current: current.id, problems: diff(current.schema, actual) };
  return { ...base, state: 'recorded', current: current.id, pending: manifest.migrations.slice(rows.length).map(m => m.id) };
}

function plan(c, manifest) {
  const pending = c.pending || [];
  return {
    state: c.state, current: c.current, target: manifest.latest.id, pending,
    compatible: c.state === 'recorded' || c.state === 'legacy-exact' || c.state === 'empty',
    migration_allowed: c.state === 'recorded' && pending.length > 0,
    baseline_allowed: c.state === 'legacy-exact', init_allowed: c.state === 'empty',
    maintenance_window: false, problems: c.problems || [],
    note: 'Application rollback never rolls back the database; expand migrations are forward-only and the previous program must stay compatible.',
  };
}

/* ------------------------------------------------------------ execution */
async function locked(client, fn) {
  const got = (await client.query('SELECT pg_try_advisory_lock($1,$2) AS ok', LOCK)).rows[0].ok;
  if (!got) refuse('BUSY', 'Another migration is running');
  try { return await fn(); }
  finally { await client.query('SELECT pg_advisory_unlock($1,$2)', LOCK).catch(() => {}); }
}

async function applyOne(client, manifest, m, method) {
  await client.query('BEGIN');
  try {
    await client.query("SET LOCAL search_path TO public");
    await client.query("SET LOCAL lock_timeout = '10s'");
    await client.query("SET LOCAL statement_timeout = '300s'");
    if (m.kind === 'initial' || method === 'baseline') {
      const exists = (await client.query(`SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=$1`, [RECORD])).rowCount > 0;
      require_(!exists, 'RECORD_CORRUPT', 'Record table already exists');
      await client.query(RECORD_DDL);
    }
    await client.query(`LOCK TABLE "${RECORD}" IN ACCESS EXCLUSIVE MODE`);
    const last = (await client.query(`SELECT id FROM "${RECORD}" ORDER BY seq DESC LIMIT 1`)).rows[0]?.id ?? null;
    require_(last === m.previous, 'CONCURRENT_CHANGE', 'Recorded version changed before applying ' + m.id);
    if (method === 'applied') await client.query(m.sql);
    const seq = manifest.migrations.indexOf(m) + 1;
    await client.query(`INSERT INTO "${RECORD}" (seq, id, sha256, previous, fingerprint, method) VALUES ($1,$2,$3,$4,$5,$6)`, [seq, m.id, m.sha256, m.previous, m.fingerprint, method]);
    const after = await describe(client);
    const problems = diff(m.schema, after);
    if (sha256(canonical(after)) !== m.fingerprint) refuse('SCHEMA_MISMATCH', 'Resulting schema differs from the reviewed description of ' + m.id, problems);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  }
}

async function run(mode, client, manifest) {
  if (mode === 'status') return plan(await classify(client, manifest), manifest);
  return locked(client, async () => {
    const c = await classify(client, manifest);
    if (mode === 'init') {
      require_(c.state === 'empty', 'NOT_EMPTY', 'init requires a verified empty database; state: ' + c.state);
      for (const m of manifest.migrations) await applyOne(client, manifest, m, 'applied');
    } else if (mode === 'baseline') {
      require_(c.state === 'legacy-exact', 'BASELINE_REFUSED', 'Baseline requires an exact legacy schema without records; state: ' + c.state);
      await applyOne(client, manifest, manifest.migrations[0], 'baseline');
    } else if (mode === 'migrate') {
      require_(c.state === 'recorded', 'MIGRATION_REFUSED', 'Migration requires a recorded, verified schema; state: ' + c.state);
      for (const id of c.pending) await applyOne(client, manifest, manifest.migrations.find(m => m.id === id), 'applied');
    } else refuse('USAGE', 'Unknown mode');
    return plan(await classify(client, manifest), manifest);
  });
}

/* ------------------------------------------------------------ backups (root) */
function freeBytes(dir) { const s = fs.statfsSync(dir); return Number(s.bavail) * Number(s.bsize); }

// Bounded row digests. PostgreSQL hashes each row (ROW(columns)::text, fixed
// session formats) and only 32-byte hashes stream back through a cursor in
// primary-key order: no table-wide aggregate, no sort spill, no user text in
// Node memory beyond one batch of digests.
const DIGEST_SESSION = ["SET LOCAL TimeZone TO 'UTC'", "SET LOCAL DateStyle TO 'ISO, YMD'", "SET LOCAL IntervalStyle TO 'postgres'",
  'SET LOCAL extra_float_digits TO 3', "SET LOCAL bytea_output TO 'hex'", 'SET LOCAL search_path TO public'];
function primaryKey(def, table) {
  const pk = Object.values(def.constraints).find(c => c.type === 'p');
  const m = pk && /^PRIMARY KEY \(([a-z0-9_, ]+)\)$/.exec(pk.def);
  require_(m, 'NO_PRIMARY_KEY', 'Row digests need a primary key: ' + table);
  return m[1].split(',').map(c => '"' + c.trim() + '"').join(',');
}
async function digestsInTransaction(client, schema) {
  for (const stmt of DIGEST_SESSION) await client.query(stmt);
  const out = {};
  for (const table of Object.keys(schema.tables).sort()) {
    const def = schema.tables[table];
    const cols = Object.keys(def.columns).sort().map(c => '"' + c + '"').join(',');
    await client.query(`DECLARE wd_digest NO SCROLL CURSOR FOR SELECT sha256(convert_to(ROW(${cols})::text, 'UTF8')) AS h FROM "${table}" ORDER BY ${primaryKey(def, table)}`);
    const h = crypto.createHash('sha256'); let n = 0;
    for (;;) {
      const rows = (await client.query('FETCH 2000 FROM wd_digest')).rows;
      if (!rows.length) break;
      for (const r of rows) { h.update(r.h); n++; }
    }
    await client.query('CLOSE wd_digest');
    out[table] = n + ':' + h.digest('hex');
  }
  return out;
}
async function digests(client, schema) {
  await client.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
  try { const d = await digestsInTransaction(client, schema); await client.query('COMMIT'); return d; }
  catch (e) { await client.query('ROLLBACK').catch(() => {}); throw e; }
}

// Bounded, identity-checked SHA256 of an already open descriptor (positional
// reads leave the offset at 0 so the same descriptor can feed pg_restore).
function fileIdentity(fd, file) {
  const s = fs.fstatSync(fd), l = fs.lstatSync(file);
  require_(s.isFile() && !l.isSymbolicLink() && s.dev === l.dev && s.ino === l.ino && s.uid === 0 && (s.mode & 0o777) === 0o600 && s.nlink === 1, 'BACKUP_IDENTITY', 'Backup file is not the expected root 0600 single-link file');
  return { dev: s.dev, ino: s.ino, size: s.size, mtimeMs: s.mtimeMs };
}
function streamSha256(fd) {
  const h = crypto.createHash('sha256'), buf = Buffer.allocUnsafe(1 << 20); let pos = 0;
  for (;;) { const n = fs.readSync(fd, buf, 0, buf.length, pos); if (!n) break; h.update(buf.subarray(0, n)); pos += n; }
  return { sha256: h.digest('hex'), bytes: pos };
}

module.exports = { RECORD, RECORD_DDL, Refusal, canonical, statements, checkSql, loadManifest, describe, classify, plan, run, digests, digestsInTransaction, streamSha256, fileIdentity, diff, sha256, freeBytes };

/* ------------------------------------------------------------ CLI */
function appConfig(appDir) {
  require_(path.isAbsolute(appDir) && /^\/opt\/webdesktop\/(app|releases\/[0-9a-f]{40}\/app)$/.test(appDir), 'USAGE', 'App directory must be /opt/webdesktop/app or a prepared release app');
  const cfg = parseEnv(fs.readFileSync(path.join(appDir === '/opt/webdesktop/app' ? appDir : '/opt/webdesktop/app', '.env'), 'utf8'));
  require_(cfg.DATABASE_URL, 'USAGE', 'DATABASE_URL missing');
  return cfg;
}

function postgres(args, options = {}) {
  return spawnSync('runuser', ['-u', 'postgres', '--', ...args], { encoding: 'utf8', timeout: 600000, ...options });
}

function newClient(client, database) {
  const { Client } = require(clientModule);
  const p = client.connectionParameters;
  return new Client({ user: p.user, password: p.password, host: p.host, port: p.port, database: database || p.database, connectionTimeoutMillis: 10000, application_name: 'webdesktop-db-backup' });
}

// Consistent backup: pg_dump and the verification digests share ONE exported
// REPEATABLE READ snapshot, so concurrent writes can never make the backup and
// its expected contents describe different database states.
async function backup(appDir, client, manifest) {
  require_(process.getuid() === 0, 'ROOT_REQUIRED', 'Backups require root');
  fs.mkdirSync(BACKUP_DIR, { recursive: true, mode: 0o700 });
  for (const p of ['/var/lib/webdesktop-db', BACKUP_DIR]) { const s = fs.lstatSync(p); require_(!s.isSymbolicLink() && s.uid === 0 && (s.mode & 0o777) === 0o700, 'UNSAFE_BACKUP_DIR', 'Backup directory must be root 0700'); }
  require_(!fs.readdirSync(BACKUP_DIR).some(n => n.endsWith('.partial')), 'STALE', 'An interrupted backup is retained for review');
  const snap = newClient(client);
  await snap.connect();
  let name, partial, expected, schema;
  try {
    await snap.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
    const snapshot = (await snap.query('SELECT pg_export_snapshot() AS s')).rows[0].s;
    require_(/^[0-9A-F-]+$/.test(snapshot), 'BACKUP_FAILED', 'Unexpected snapshot identifier');
    const c = await classify(snap, manifest);
    require_(['recorded', 'legacy-exact'].includes(c.state), 'BACKUP_REFUSED', 'Backup requires a known schema; state: ' + c.state);
    const db = (await snap.query('SELECT current_database() AS d, pg_database_size(current_database())::text AS s')).rows[0];
    const need = Number(db.s) + RESERVE;
    require_(freeBytes(BACKUP_DIR) >= need, 'DISK_SPACE', 'Backup needs ' + need + ' bytes free (database size + reserve); nothing written');
    schema = (manifest.migrations.find(m => m.id === c.current) || manifest.migrations[0]).schema;
    expected = { schema: 1, version: c.current || 'legacy', state: c.state, fingerprint: c.fingerprint, digests: await digestsInTransaction(snap, schema), database_bytes: Number(db.s), at: new Date().toISOString() };
    name = expected.at.replace(/[-:]/g, '').replace(/\..*/, 'Z') + '-' + expected.version + '.dump';
    partial = path.join(BACKUP_DIR, name + '.partial');
    const fd = fs.openSync(partial, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    try {
      const code = await new Promise((resolve, reject) => {
        const child = spawn('runuser', ['-u', 'postgres', '--', 'pg_dump', '-Fc', '--no-password', '--snapshot=' + snapshot, '-d', db.d], { stdio: ['ignore', fd, 'ignore'] });
        child.on('error', reject); child.on('exit', resolve);
      });
      require_(code === 0, 'BACKUP_FAILED', 'pg_dump failed; the partial file is retained for review');
      fs.fsyncSync(fd);
    } finally { fs.closeSync(fd); }
    await snap.query('COMMIT');
  } finally { await snap.end().catch(() => {}); }
  const final = path.join(BACKUP_DIR, name);
  fs.renameSync(partial, final);
  const dirFd = fs.openSync(BACKUP_DIR, 'r'); try { fs.fsyncSync(dirFd); } finally { fs.closeSync(dirFd); }
  const verified = await restoreCheck(final, client, { ...expected, digestSchema: schema });
  // Expectations only (hashes and counts, never row contents), root 0600.
  const sidecar = { ...expected, file: path.basename(final), sha256: verified.sha256, bytes: verified.bytes };
  const sfd = fs.openSync(final + '.json', fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try { fs.writeSync(sfd, JSON.stringify(sidecar, null, 2)); fs.fsyncSync(sfd); } finally { fs.closeSync(sfd); }
  return { file: final, ...verified, version: expected.version, consistent_snapshot: true };
}

// A backup counts only after a real restore into an independent database
// reproduces the snapshot's schema fingerprint and every row digest. The dump
// is hashed and restored through ONE root-opened descriptor (identity checked
// before and after), so a replaced or modified file cannot slip through.
async function restoreCheck(file, client, expected) {
  require_(path.dirname(file) === BACKUP_DIR && /^[0-9TZ]+-[a-z0-9_]+\.dump$/.test(path.basename(file)), 'USAGE', 'Only backups in the protected backup directory can be verified');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const before = fileIdentity(fd, file);
    const { sha256: digest, bytes } = streamSha256(fd);
    require_(bytes === before.size, 'BACKUP_IDENTITY', 'Backup changed while hashing');
    if (expected.sha256) require_(digest === expected.sha256, 'BACKUP_IDENTITY', 'Backup bytes differ from its record');
    const owner = (await client.query('SELECT current_user AS u')).rows[0].u;
    const temp = 'wd_restore_check_' + crypto.randomBytes(6).toString('hex');
    require_(postgres(['createdb', '-O', owner, temp]).status === 0, 'RESTORE_FAILED', 'Could not create the independent restore database');
    try {
      const restored = postgres(['pg_restore', '--exit-on-error', '--no-password', '-d', temp], { stdio: [fd, 'ignore', 'ignore'] });
      require_(restored.status === 0, 'RESTORE_FAILED', 'pg_restore failed into the independent database');
      const after = fileIdentity(fd, file);
      require_(after.ino === before.ino && after.size === before.size && after.mtimeMs === before.mtimeMs, 'BACKUP_IDENTITY', 'Backup changed during restore verification');
      const other = newClient(client, temp);
      await other.connect();
      try {
        require_(sha256(canonical(await describe(other))) === expected.fingerprint, 'RESTORE_MISMATCH', 'Restored schema differs from the backed-up snapshot');
        const actual = await digests(other, expected.digestSchema);
        require_(canonical(actual) === canonical(expected.digests), 'RESTORE_MISMATCH', 'Restored rows differ from the backed-up snapshot');
        return { restore_verified: true, sha256: digest, bytes, tables: Object.keys(actual).length, fingerprint: expected.fingerprint };
      } finally { await other.end().catch(() => {}); }
    } finally { postgres(['dropdb', '--if-exists', temp]); }
  } finally { fs.closeSync(fd); }
}

// Re-verify an existing backup against its own snapshot record (not against the
// live database, which may have moved on).
async function verifyBackup(file, client, manifest) {
  const sfd = fs.openSync(file + '.json', fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  let record;
  try { fileIdentity(sfd, file + '.json'); record = JSON.parse(fs.readFileSync(sfd, 'utf8')); } finally { fs.closeSync(sfd); }
  require_(record && record.schema === 1 && HEX.test(record.sha256 || '') && HEX.test(record.fingerprint || '') && record.file === path.basename(file), 'BACKUP_IDENTITY', 'Backup record is missing or invalid');
  const m = manifest.migrations.find(x => x.id === record.version) || manifest.migrations[0];
  return restoreCheck(file, client, { ...record, digestSchema: m.schema });
}

let clientModule = 'pg';

async function main() {
  const [mode, appDir, extra] = process.argv.slice(2);
  const cfg = appConfig(appDir || '');
  protectedTree(path.join(appDir, 'drizzle/migrations'));
  const manifest = loadManifest(path.join(appDir, 'drizzle/migrations'));
  const appRequire = createRequire(path.join(appDir, 'package.json'));
  clientModule = appRequire.resolve('pg');
  const { Client } = appRequire('pg');
  const client = new Client({ connectionString: cfg.DATABASE_URL, connectionTimeoutMillis: 10000, application_name: 'webdesktop-db-migrate' });
  await client.connect();
  try {
    let result;
    if (mode === 'backup') result = await backup(appDir, client, manifest);
    else if (mode === 'verify-backup') result = await verifyBackup(extra || '', client, manifest);
    else {
      if (mode !== 'status') require_(process.getuid() === 0, 'ROOT_REQUIRED', 'Schema changes require the root maintenance entry');
      if (mode === 'migrate' || mode === 'init' || mode === 'baseline') require_(freeBytes(PG_DATA) >= RESERVE, 'DISK_SPACE', 'At least 512 MiB free on the PostgreSQL volume required');
      result = await run(mode, client, manifest);
    }
    console.log(JSON.stringify({ mode, ok: true, ...result }, null, 2));
  } finally { await client.end().catch(() => {}); }
}

if (require.main === module) {
  main().catch(error => {
    // Fixed codes and object names only; never SQL text, rows or credentials.
    const known = error instanceof Refusal;
    console.log(JSON.stringify({ ok: false, code: known ? error.code : (typeof error.code === 'string' && /^[0-9A-Z]{5}$/.test(error.code) ? 'SQL_' + error.code : 'FAILED'), message: known ? error.message : 'Database operation failed; transaction rolled back', problems: known ? (error.detail || []).slice(0, 40) : [] }, null, 2));
    process.exitCode = 1;
  });
}
