// Status endpoints only. A remembered digest routes capacity; it NEVER grants
// access. Every request still performs the existing fresh desktop DB check.
import { createHash } from 'node:crypto';
import http from 'node:http';

export const STATUS_LIMITS = Object.freeze({ windowMs: 1000, hintMs: 300000, authMs: 1500,
  ingressKnown: [4, 24], ingressUnknown: [2, 8], authKnown: [2, 24], authUnknown: [2, 8], work: [4, 12] });
const bucket = ([max, rate]) => ({ max, rate, active: 0, count: 0, epoch: 0 });
function enter(b, now) {
  if (now - b.epoch >= STATUS_LIMITS.windowMs) { b.epoch = now; b.count = 0; }
  if (b.active >= b.max || b.count >= b.rate) return null;
  b.active++; b.count++; let done = false;
  return () => { if (!done) { done = true; b.active--; } };
}

export function statusIdentity(cookie, access) {
  if (typeof cookie !== 'string' || cookie.length > 8192 || typeof access !== 'string' || !/^[A-Za-z0-9_-]{32}$/.test(access)) return null;
  const part = cookie.split(';').find(p => p.indexOf('=') > 0 && p.slice(0,p.indexOf('=')).trim() === 'wd_session');
  const token = part?.slice(part.indexOf('=')+1).trim();
  if (!token || token.length > 2000 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/.test(token)) return null;
  try {
    // Shape/mismatch rejection only, NOT signature verification or authority.
    const p = JSON.parse(Buffer.from(token.split('.')[0], 'base64url').toString());
    if (p.k !== 'd' || p.sid !== access || !Number.isFinite(p.exp) || p.exp <= Date.now()) return null;
  } catch { return null; }
  return createHash('sha256').update(token).update('\0').update(access).digest('hex');
}

export class StatusAdmission {
  constructor(now = Date.now) {
    this.now = now; this.current = null; this.unhealthyActive = 0;
    this.ingress = { known: bucket(STATUS_LIMITS.ingressKnown), unknown: bucket(STATUS_LIMITS.ingressUnknown) };
    this.auth = { known: bucket(STATUS_LIMITS.authKnown), unknown: bucket(STATUS_LIMITS.authUnknown) };
    this.work = { resources: bucket(STATUS_LIMITS.work), gui: bucket(STATUS_LIMITS.work) };
  }
  remember(token, access, expires, issued = false) {
    const key = statusIdentity('wd_session=' + token, access), now = this.now();
    if (!key || !Number.isFinite(expires) || expires <= now) return;
    // A late old authentication cannot overwrite a newer successful login.
    if (issued || !this.current || this.current.until <= now || this.current.key === key)
      this.current = { key, until: Math.min(expires, now + STATUS_LIMITS.hintMs) };
  }
  lane(key) { return this.current?.key === key && this.current.until > this.now() ? 'known' : 'unknown'; }
  ingressGrant(cookie, access) {
    const key = statusIdentity(cookie, access);
    if (!key) return { status: 401 };
    const release = enter(this.ingress[this.lane(key)], this.now());
    return release ? { status: 200, release } : { status: 429 };
  }
  async authorize(scope, cookie, access, validate, signal) {
    if (!Object.hasOwn(this.work, scope)) throw new Error('Fixed status scope required');
    const key = statusIdentity(cookie, access);
    if (!key) return { status: 401 };
    if (signal?.aborted) return { status: 503 };
    const releaseAuth = enter(this.auth[this.lane(key)], this.now());
    if (!releaseAuth) return { status: this.unhealthyActive > 0 ? 503 : 429 };
    let timer, abort, settled = false, timedOut = false;
    const completion = Promise.resolve().then(() => signal?.aborted ? 503 : validate()).then(status => {
      if (![200, 401].includes(status)) return 503;
      if (status === 401 && this.current?.key === key) this.current = null;
      return status;
    }, () => 503).finally(() => {
      settled = true; clearTimeout(timer);
      if (timedOut) this.unhealthyActive--;
      releaseAuth();
    });
    const interrupted = new Promise(resolve => {
      timer = setTimeout(() => { if (!settled) { timedOut = true; this.unhealthyActive++; } resolve(503); }, STATUS_LIMITS.authMs);
      abort = () => resolve(503); signal?.addEventListener('abort', abort, { once: true });
      if (signal?.aborted) abort();
    });
    let status;
    try { status = await Promise.race([completion, interrupted]); }
    finally { signal?.removeEventListener('abort', abort); }
    // Cancellation/timeout does NOT release an executing DB validation. Its
    // own finally above does, preventing repeated aborts from creating a queue.
    if (signal?.aborted || status !== 200) return { status: signal?.aborted ? 503 : status };
    const release = enter(this.work[scope], this.now());
    return release ? { status: 200, release } : { status: 429 };
  }
}
const symbol = Symbol.for('webdesktop.statusAdmission.v1');
export const statusAdmission = globalThis[symbol] ??= new StatusAdmission();

// Only the public GUI status handler uses this protected internal endpoint.
// The DB admission remains INSIDE Next even if this HTTP client disconnects.
export function checkGuiStatusAuth(socketPath, cookie, access) {
  return new Promise(resolve => {
    let done = false;
    const finish = status => { if (!done) { done = true; clearTimeout(timer); resolve(status); } };
    const request = http.get({ socketPath, path: '/api/guest/status-auth', agent: false,
      headers: { cookie, 'x-desktop-session': access, 'x-webdesktop-status-auth': 'gui', connection: 'close' } }, response => {
      let bytes = 0;
      response.on('data', chunk => { bytes += chunk.length; if (bytes > 2048) request.destroy(); });
      response.on('end', () => finish([200, 401, 429].includes(response.statusCode) ? response.statusCode : 503));
      response.on('error', () => finish(503)); response.resume();
    });
    const timer = setTimeout(() => { finish(503); request.destroy(); }, STATUS_LIMITS.authMs + 250);
    request.on('error', () => finish(503));
  });
}
