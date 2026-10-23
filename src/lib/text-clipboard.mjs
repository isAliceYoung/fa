// Explicit, user-initiated text-only clipboard bridge (gateway side, wdsvc).
// Pure validation shared by the public route and tests. The gateway only
// forwards bounded UTF-8 text over the existing GUI session IPC; it never
// touches X11, files or commands.
export const CLIPBOARD_LIMITS = Object.freeze({ maxBytes: 16384, maxBody: 65536, bodyMs: 5000, replyMs: 4000, windowMs: 10000, perWindow: 10 });

const ALLOWED = new Set([0x09, 0x0a, 0x0d]);
export function textProblem(text) {
  if (typeof text !== 'string') return 'INVALID_REQUEST';
  if (!text) return 'EMPTY';
  if (!text.isWellFormed()) return 'INVALID_UTF8';
  for (const ch of text) {
    const o = ch.codePointAt(0);
    if ((o < 0x20 && !ALLOWED.has(o)) || (o >= 0x7f && o <= 0x9f)) return 'CONTROL_CHARACTERS';
  }
  if (Buffer.byteLength(text, 'utf8') > CLIPBOARD_LIMITS.maxBytes) return 'TOO_LARGE';
  return null;
}

// Exact body shape only: {sid, op:'read'} or {sid, op:'write', text}.
export function parseClipboardBody(raw) {
  let body;
  try { body = JSON.parse(raw); } catch { return { error: 'INVALID_REQUEST' }; }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { error: 'INVALID_REQUEST' };
  const keys = Object.keys(body).sort().join(',');
  if (typeof body.sid !== 'string' || !/^[a-f0-9]{24,64}$/.test(body.sid)) return { error: 'INVALID_REQUEST' };
  if (body.op === 'read' && keys === 'op,sid') return { sid: body.sid, op: 'read' };
  if (body.op === 'write' && keys === 'op,sid,text') {
    const problem = textProblem(body.text);
    return problem ? { error: problem } : { sid: body.sid, op: 'write', text: body.text };
  }
  return { error: 'INVALID_REQUEST' };
}

// Reply from the wdgui worker: fixed fields, text only on a successful read.
const CODES = new Set(['OK', 'EMPTY', 'NON_TEXT', 'TOO_LARGE', 'INVALID_UTF8', 'CONTROL_CHARACTERS', 'TIMEOUT', 'UNAVAILABLE', 'BUSY']);
export function validReply(m, n, op) {
  if (!m || m.t !== 'clip' || m.n !== n || typeof m.ok !== 'boolean' || !CODES.has(m.code)) return null;
  const extra = Object.keys(m).filter(k => !['t', 'n', 'ok', 'code', 'bytes', 'text'].includes(k));
  if (extra.length) return null;
  if (m.ok && op === 'read') return typeof m.text === 'string' && !textProblem(m.text) ? { ok: true, code: 'OK', text: m.text } : null;
  if ('text' in m) return null;
  return { ok: m.ok && m.code === 'OK', code: m.code };
}

export function clipboardStatus(code) {
  return { OK: 200, EMPTY: 404, NON_TEXT: 415, TOO_LARGE: 413, INVALID_UTF8: 400, CONTROL_CHARACTERS: 400, INVALID_REQUEST: 400,
    BUSY: 409, RATE_LIMITED: 429, GUI_INACTIVE: 409, TIMEOUT: 504, UNAVAILABLE: 503 }[code] ?? 503;
}

export class ClipboardRate {
  constructor(now = () => Date.now()) { this.now = now; this.events = []; }
  take() {
    const t = this.now();
    while (this.events.length && this.events[0] <= t - CLIPBOARD_LIMITS.windowMs) this.events.shift();
    if (this.events.length >= CLIPBOARD_LIMITS.perWindow) return false;
    this.events.push(t); return true;
  }
}
