// Explicit, user-initiated PNG image clipboard (gateway side, wdsvc).
// wdsvc never touches X11, files or image tools. It only checks a bounded PNG's
// structure and forwards bytes over the existing GUI session IPC in small
// chunks, one chunk at a time, so the IPC write buffer that keyboard/mouse
// input shares never approaches its 64 KiB budget.
import { createHash } from 'node:crypto';
import { crc32 } from 'node:zlib';

export const IMAGE_LIMITS = Object.freeze({
  maxBytes: 4 * 1024 * 1024, maxSide: 4096, maxPixels: 3840 * 2160, maxChunks: 10000,
  chunk: 16384, bodyMs: 60000, replyMs: 30000, windowMs: 60000, perWindow: 6,
});

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const DEPTHS = { 0: [1, 2, 4, 8, 16], 2: [8, 16], 3: [1, 2, 4, 8], 4: [8, 16], 6: [8, 16] };
const ANIMATION = new Set(['acTL', 'fcTL', 'fdAT']);
const CRITICAL = new Set(['IHDR', 'PLTE', 'IDAT', 'IEND']);

// Structure, every chunk CRC and dimension limits. Full zlib decoding is done
// by wdgui before the image reaches X11 (and before one leaves it).
export function pngInfo(buf) {
  if (!Buffer.isBuffer(buf)) return { error: 'INVALID_IMAGE' };
  if (buf.length > IMAGE_LIMITS.maxBytes) return { error: 'TOO_LARGE' };
  if (buf.length < 57 || !buf.subarray(0, 8).equals(SIGNATURE)) return { error: 'INVALID_IMAGE' };
  let pos = 8, count = 0, info = null, seenIdat = false, afterIdat = false, palette = false;
  for (;;) {
    if (pos + 12 > buf.length) return { error: 'INVALID_IMAGE' };
    const length = buf.readUInt32BE(pos), type = buf.toString('latin1', pos + 4, pos + 8);
    if (length > 0x7fffffff || pos + 12 + length > buf.length || !/^[A-Za-z]{4}$/.test(type)) return { error: 'INVALID_IMAGE' };
    if ((crc32(buf.subarray(pos + 4, pos + 8 + length)) >>> 0) !== buf.readUInt32BE(pos + 8 + length)) return { error: 'INVALID_IMAGE' };
    if (++count > IMAGE_LIMITS.maxChunks) return { error: 'INVALID_IMAGE' };
    const body = buf.subarray(pos + 8, pos + 8 + length);
    if (count === 1) {
      if (type !== 'IHDR' || length !== 13) return { error: 'INVALID_IMAGE' };
      const width = body.readUInt32BE(0), height = body.readUInt32BE(4);
      const [depth, color, compression, filter, interlace] = body.subarray(8, 13);
      if (!(width > 0 && width <= IMAGE_LIMITS.maxSide && height > 0 && height <= IMAGE_LIMITS.maxSide) || width * height > IMAGE_LIMITS.maxPixels) return { error: 'TOO_LARGE' };
      if (!DEPTHS[color]?.includes(depth) || compression || filter || interlace > 1) return { error: 'INVALID_IMAGE' };
      info = { width, height, color };
    } else if (type === 'IHDR') return { error: 'INVALID_IMAGE' };
    else if (ANIMATION.has(type)) return { error: 'UNSUPPORTED_FORMAT' };
    else if (type === 'PLTE') { if (seenIdat || palette || !length || length % 3 || length > 768) return { error: 'INVALID_IMAGE' }; palette = true; }
    else if (type === 'IDAT') { if (afterIdat) return { error: 'INVALID_IMAGE' }; seenIdat = true; }
    else if (type === 'IEND') { if (length || !seenIdat || pos + 12 !== buf.length) return { error: 'INVALID_IMAGE' }; break; }
    else if (type.charCodeAt(0) < 0x61 && !CRITICAL.has(type)) return { error: 'INVALID_IMAGE' };
    if (seenIdat && type !== 'IDAT') afterIdat = true;
    pos += 12 + length;
  }
  if (info.color === 3 && !palette) return { error: 'INVALID_IMAGE' };
  return { width: info.width, height: info.height, bytes: buf.length };
}

export function imageStatus(code) {
  return { OK: 200, EMPTY: 404, NON_IMAGE: 415, UNSUPPORTED_FORMAT: 415, TOO_LARGE: 413, INVALID_IMAGE: 400, INVALID_REQUEST: 400,
    BUSY: 409, GUI_INACTIVE: 409, RATE_LIMITED: 429, TIMEOUT: 504, UNAVAILABLE: 503 }[code] ?? 503;
}

// Query is exactly ?sid=<hex>&op=read|write; nothing else (no path, display, MIME or command).
export function parseImageQuery(rawUrl) {
  let url;
  try { url = new URL(rawUrl, 'http://localhost'); } catch { return null; }
  const keys = [...url.searchParams.keys()];
  if (url.pathname !== '/api/gui/image-clipboard' || keys.length !== 2 || new Set(keys).size !== 2) return null;
  const sid = url.searchParams.get('sid'), op = url.searchParams.get('op');
  if (!/^[a-f0-9]{24,64}$/.test(sid || '') || !['read', 'write'].includes(op)) return null;
  return { sid, op };
}

const CODES = new Set(['OK', 'EMPTY', 'NON_IMAGE', 'UNSUPPORTED_FORMAT', 'TOO_LARGE', 'INVALID_IMAGE', 'TIMEOUT', 'UNAVAILABLE', 'BUSY']);
// Worker reply/header. Only a successful read announces size + sha256.
export function validImageReply(m, n, op) {
  if (!m || m.t !== 'img' || m.n !== n || typeof m.ok !== 'boolean' || !CODES.has(m.code)) return null;
  const keys = Object.keys(m).sort().join(',');
  if (m.ok && op === 'read') {
    if (keys !== 'code,n,ok,sha256,size,t' || m.code !== 'OK' || !Number.isInteger(m.size) || m.size < 1 || m.size > IMAGE_LIMITS.maxBytes || !/^[0-9a-f]{64}$/.test(m.sha256)) return null;
    return { ok: true, size: m.size, sha256: m.sha256 };
  }
  if (keys !== 'code,n,ok,t' || (m.ok && m.code !== 'OK') || (!m.ok && m.code === 'OK')) return null;
  return { ok: m.ok, code: m.code };
}

// Collects one announced download in strictly sequential bounded chunks.
export class ImageAssembly {
  constructor(n, size, sha256) { this.n = n; this.size = size; this.sha256 = sha256; this.parts = []; this.received = 0; this.next = 0; }
  add(m) {
    if (!m || m.t !== 'imgc' || m.n !== this.n || m.i !== this.next || typeof m.d !== 'string' || Object.keys(m).length !== 4 ||
      m.d.length > Math.ceil(IMAGE_LIMITS.chunk / 3) * 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(m.d)) return false;
    const part = Buffer.from(m.d, 'base64');
    if (!part.length || part.toString('base64') !== m.d || this.received + part.length > this.size) return false;
    this.parts.push(part); this.received += part.length; this.next++;
    return true;
  }
  get complete() { return this.received === this.size; }
  result() {
    const data = Buffer.concat(this.parts, this.received);
    this.parts = [];
    if (createHash('sha256').update(data).digest('hex') !== this.sha256) return { code: 'INVALID_IMAGE' };
    const info = pngInfo(data);
    return info.error ? { code: info.error } : { code: 'OK', data, info };
  }
}

// One chunk at a time: write the next line only when the socket's write
// buffer is empty, so it never holds more than ~22 KiB of image data.
export async function sendImageChunks(socket, n, data, { aborted = () => false } = {}) {
  for (let i = 0, offset = 0; offset < data.length; i++, offset += IMAGE_LIMITS.chunk) {
    while (socket.writableLength > 0) {
      if (aborted() || socket.destroyed) return false;
      await new Promise(resolve => { const t = setTimeout(done, 200); function done() { clearTimeout(t); socket.off('drain', done); resolve(); } socket.once('drain', done); });
    }
    if (aborted() || socket.destroyed) return false;
    socket.write(JSON.stringify({ t: 'imgc', n, i, d: data.subarray(offset, offset + IMAGE_LIMITS.chunk).toString('base64') }) + '\n');
  }
  return true;
}

export class ImageRate {
  constructor(now = () => Date.now()) { this.now = now; this.events = []; }
  take() {
    const t = this.now();
    while (this.events.length && this.events[0] <= t - IMAGE_LIMITS.windowMs) this.events.shift();
    if (this.events.length >= IMAGE_LIMITS.perWindow) return false;
    this.events.push(t); return true;
  }
}

export const sha256Hex = data => createHash('sha256').update(data).digest('hex');
