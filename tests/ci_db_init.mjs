// CI-only: initialize a throwaway CI database from the versioned migrations
// (same path as the installer), never with a schema push.
import 'dotenv/config';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const M = require('../webdesktop-deploy/scripts/db_migrate.cjs');
const pg = require('pg');
const client = new pg.Client({ connectionString: process.env.DATABASE_URL });
await client.connect();
try {
  const result = await M.run('init', client, M.loadManifest(new URL('../drizzle/migrations', import.meta.url).pathname));
  console.log(JSON.stringify({ state: result.state, current: result.current }));
} finally { await client.end(); }
