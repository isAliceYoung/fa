import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import { Readable, Transform, pipeline } from 'node:stream';

const SOCKET = '/run/webdesktop-guest/guest.sock';
const METHODS = new Map([
  ['/v1/info', 'GET'], ['/v1/list', 'GET'], ['/v1/read-text', 'GET'], ['/v1/read', 'GET'],
  ['/v1/create', 'POST'], ['/v1/write', 'PUT'],
  ['/v1/action', 'POST'], ['/v1/remove', 'POST'], ['/v1/mkdir', 'POST'], ['/v1/export', 'GET'],
]);
export const MAX_BODY = 5 * 1024 * 1024 + 65536;
const unavailable = (status = 503) => Response.json({ error: status === 504 ? '访客文件服务超时' : '访客文件服务不可用，请联系管理员', code: 'GUEST_UNAVAILABLE' }, { status, headers: { 'Cache-Control': 'no-store' } });

export async function guestRequest(endpoint, { method = 'GET', query = {}, body, stream, signal } = {}) {
  if (endpoint === '/v1/upload' ? !['GET', 'POST', 'DELETE'].includes(method) : METHODS.get(endpoint) !== method) return unavailable();
  if (stream && (endpoint !== '/v1/upload' || method !== 'POST')) return unavailable();
  try {
    const identity = os.userInfo();
    if (identity.username !== 'wdsvc' || !identity.uid || typeof process.getgid !== 'function') return unavailable();
    const [directory, socket] = await Promise.all([fs.lstat('/run/webdesktop-guest'), fs.lstat(SOCKET)]);
    if (!directory.isDirectory() || directory.uid !== 0 || directory.gid !== identity.gid || (directory.mode & 0o777) !== 0o750 ||
        !socket.isSocket() || socket.uid !== 0 || socket.gid !== identity.gid || (socket.mode & 0o777) !== 0o660) return unavailable();
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
    if (payload && payload.length > MAX_BODY) return Response.json({ error: '内容过大' }, { status: 413 });
    const path = endpoint + '?' + new URLSearchParams(query).toString();
    return await new Promise((resolve) => {
      let resolved = false;
      const request = http.request({ socketPath: SOCKET, path, method, agent: false,
        headers: endpoint === '/v1/upload' && method === 'POST' ? { 'Content-Type': 'application/octet-stream' } : payload ? { 'Content-Type': 'application/json', 'Content-Length': String(payload.length) } : {},
      });
      const finish = (response) => { if (!resolved) { resolved = true; resolve(response); } };
      const total = setTimeout(() => { finish(unavailable(504)); request.destroy(); }, 30000);
      total.unref?.();
      const abort = () => request.destroy();
      if (signal?.aborted) { clearTimeout(total); finish(unavailable()); return request.destroy(); }
      signal?.addEventListener('abort', abort, { once: true });
      const cleanup = () => { clearTimeout(total); signal?.removeEventListener('abort', abort); };
      request.setTimeout(10000, () => { finish(unavailable(504)); request.destroy(); });
      request.on('error', (error) => { cleanup(); finish(error instanceof RangeError ? Response.json({ error: 'Upload chunk exceeds 16 MiB' }, { status: 413 }) : unavailable()); });
      request.on('response', (incoming) => {
        const length = Number(incoming.headers['content-length']);
        const binary = endpoint === '/v1/read' || endpoint === '/v1/export';
        const limit = binary ? 100 * 1024 * 1024 : MAX_BODY;
        if (!Number.isSafeInteger(length) || length < 0 || length > limit || ![200, 400, 403, 404, 409, 413, 503, 504, 507].includes(incoming.statusCode)) {
          incoming.destroy(); cleanup(); return finish(unavailable());
        }
        let received = 0;
        incoming.on('data', (chunk) => { received += chunk.length; if (received > length || received > limit) incoming.destroy(new Error('Guest response limit')); });
        incoming.on('end', cleanup);
        incoming.on('close', cleanup);
        // Allow no guest-provided Cookie, redirect, CORS or other response headers.
        const headers = { 'Content-Type': binary && incoming.statusCode === 200 ? (incoming.headers['content-type'] || 'application/octet-stream') : 'application/json; charset=utf-8',
          'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': 'sandbox' };
        if (binary && incoming.statusCode === 200) headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + encodeURIComponent((query.path || 'file').split('/').pop() + (incoming.headers['content-type'] === 'application/zip' ? '.zip' : ''));
        finish(new Response(Readable.toWeb(incoming, { strategy: { highWaterMark: 65536, size: (chunk) => chunk.byteLength } }), { status: incoming.statusCode, headers }));
      });
      if (stream) {
        let count = 0;
        const limit = new Transform({ highWaterMark: 65536, transform(chunk, _encoding, callback) {
          count += chunk.length;
          callback(count > 16 * 1024 * 1024 ? new RangeError('Upload too large') : null, chunk);
        } });
        pipeline(Readable.fromWeb(stream), limit, request, () => {});
      } else if (payload) request.end(payload); else request.end();
    });
  } catch {
    return unavailable();
  }
}

export async function boundedJson(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error('JSON body required');
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > MAX_BODY) { await reader.cancel(); throw new RangeError('Request too large'); }
      chunks.push(value);
    }
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Object required');
    return value;
  } finally { reader.releaseLock(); }
}
