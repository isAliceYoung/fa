#!/usr/bin/env node
// .env is parsed as data, never sourced as shell code. Passwords never enter argv.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const { spawnSync } = require('node:child_process');
const { parseEnv } = require('node:util');

function databaseFailure(error) {
  const code = typeof error.code === 'string' && /^[A-Z0-9_]+$/.test(error.code) ? error.code : 'UNKNOWN';
  const category = {
    ECONNREFUSED: 'connection refused; check PostgreSQL service and port',
    ETIMEDOUT: 'connection timed out; check server availability',
    ENOTFOUND: 'database host name could not be resolved',
    '28P01': 'authentication failed; check the protected .env credentials',
    '28000': 'authentication or access policy rejected the connection',
    '3D000': 'database does not exist',
    '42501': 'database role has insufficient privileges',
    '42P01': 'required database table is missing',
  }[code] || 'connection/query failed; inspect PostgreSQL service logs';
  return new Error(`Database failure [${code}]: ${category}. Connection strings, passwords and SQL are hidden.`);
}

async function main() {
  const [mode, appDir] = process.argv.slice(2);
  if (!mode || !appDir || !path.isAbsolute(appDir)) throw new Error('Usage: node bootstrap.cjs MODE /absolute/app/directory');
  const appRequire = createRequire(path.join(appDir, 'package.json'));
  const envPath = path.join(appDir, '.env');
  if (mode === 'create-env') {
    if (fs.existsSync(envPath)) {
      console.log('Keeping existing .env and login seeds.');
    } else {
      const hex = bytes => crypto.randomBytes(bytes).toString('hex');
      const repo = process.env.WD_REPO_URL || 'https://github.com/isAliceYoung/fa.git';
      const branch = process.env.WD_BRANCH || 'main';
      const values = {
        DATABASE_URL: `postgresql://wd_app:${hex(24)}@127.0.0.1:5432/wd_db`,
        ADMIN_USER: 'admin', ADMIN_PASSWORD: hex(24), DESKTOP_PASSWORD: hex(24),
        ADMIN_PATH: `ops-${hex(8)}`, AUTH_KEY: hex(32),
        UPSTREAM_REPOSITORY: repo, UPSTREAM_BRANCH: branch,
        AUTO_UPDATE: '0', UPDATE_RESTART: '0', UPDATE_INTERVAL_MIN: '10',
        BIND_HOST: '127.0.0.1', PORT: '3000',
      };
      fs.writeFileSync(envPath, Object.entries(values).map(([k, v]) => `${k}=${v}`).join('\n') + '\n', { mode: 0o600, flag: 'wx' });
      console.log('Created .env with independent random initial login passwords.');
    }
    fs.chmodSync(envPath, 0o600);
  }
  const cfg = parseEnv(fs.readFileSync(envPath, 'utf8'));
  for (const key of ['DATABASE_URL', 'ADMIN_USER', 'ADMIN_PASSWORD', 'DESKTOP_PASSWORD', 'ADMIN_PATH', 'AUTH_KEY']) {
    if (!cfg[key] || /replace-with|change-me/i.test(cfg[key])) throw new Error(`Missing or placeholder value: ${key}; existing .env was not regenerated.`);
  }
  if (!/^[a-zA-Z0-9_-]+$/.test(cfg.ADMIN_PATH)) throw new Error('ADMIN_PATH must be a simple URL path segment.');
  if (mode === 'create-env' || mode === 'check-env') {
    console.log('Required configuration values are present (values hidden).');
    return;
  }
  const url = new URL(cfg.DATABASE_URL);
  if (!['postgres:', 'postgresql:'].includes(url.protocol)) throw new Error('Unsupported DATABASE_URL protocol.');
  const username = decodeURIComponent(url.username);
  const password = decodeURIComponent(url.password);
  const dbname = decodeURIComponent(url.pathname.slice(1));
  const literal = value => "'" + value.replaceAll("'", "''") + "'";
  const identifier = value => '"' + value.replaceAll('"', '""') + '"';
  if (mode === 'database') {
    if (process.getuid() !== 0) throw new Error('Database provisioning requires root.');
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || (url.port && url.port !== '5432')) {
      console.log('Existing nondefault database endpoint: skipping local role/database creation.');
      return;
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(username) || !/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(dbname) || !password) throw new Error('Local database URL must contain a username, password and simple database name.');
    const psql = sql => {
      const result = spawnSync('runuser', ['-u', 'postgres', '--', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-d', 'postgres'], {
        input: sql + '\n', encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'],
      });
      // Do not forward psql stderr: a failing CREATE ROLE could include a password.
      if (result.error || result.status !== 0) throw new Error('Local PostgreSQL provisioning failed; inspect PostgreSQL service logs. SQL and passwords are hidden.');
      return result.stdout.trim();
    };
    if (psql(`SELECT 1 FROM pg_roles WHERE rolname=${literal(username)};`) !== '1') {
      psql(`CREATE ROLE ${identifier(username)} LOGIN PASSWORD ${literal(password)};`);
      console.log('Created application database role.');
    } else console.log('Keeping existing database role and its password.');
    const owner = psql(`SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=${literal(dbname)};`);
    if (!owner) {
      psql(`CREATE DATABASE ${identifier(dbname)} OWNER ${identifier(username)};`);
      console.log('Created application database.');
    } else if (owner !== username) throw new Error('Existing database has a different owner; no ownership or data changes were made.');
    else console.log('Keeping existing application database.');
    return;
  }
  const { Client } = appRequire('pg');
  const client = new Client({ connectionString: cfg.DATABASE_URL, connectionTimeoutMillis: 10000 });
  let connecting = true;
  try {
    await client.connect();
    connecting = false;
    if (mode === 'check-db') {
      await client.query('SELECT 1');
      console.log('Database connection OK (credentials hidden).');
    } else if (mode === 'schema-state') {
      const result = await client.query("SELECT tablename FROM pg_tables WHERE schemaname='public'");
      const tables = new Set(result.rows.map(row => row.tablename));
      if (tables.size === 0) console.log('empty');
      else if (['fs_nodes', 'desktop_settings', 'auth_config', 'admin_users', 'desktop_workspace'].every(t => tables.has(t))) console.log('ready');
      else throw new Error('Existing database has an incomplete or different schema; refusing automatic schema changes.');
    } else if (mode === 'auth-state') {
      const admin = await client.query('SELECT count(*)::int AS n FROM admin_users');
      const desktop = await client.query("SELECT 1 FROM auth_config WHERE key='desk_hash' AND value <> ''");
      if (!admin.rows[0].n || !desktop.rowCount) throw new Error('Application login initialization has not completed.');
      console.log('Administrator and desktop login records are initialized (passwords hidden).');
    } else throw new Error(`Unknown mode: ${mode}`);
  } catch (error) {
    // pg error messages can contain usernames or database names; hide connection details.
    if (connecting || error.code || error instanceof TypeError) throw databaseFailure(error);
    if (/^(Existing database has|Application login initialization|Unknown mode:)/.test(error.message)) throw error;
    throw databaseFailure(error);
  } finally {
    await client.end().catch(() => {});
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
