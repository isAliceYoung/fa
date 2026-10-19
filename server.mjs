import http from 'node:http';
import next from 'next';
import { pathToFileURL } from 'node:url';
import { bindAddress } from './scripts/network-policy.mjs';
import { listenInternal } from './scripts/internal-http.mjs';

// Separate listeners in one process. Next owns only the private HTTP listener.
export async function startGateway({ port = Number(process.env.PORT || 3000), hostname, dev = false } = {}) {
  hostname = bindAddress(hostname);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid port');
  const app = next({ dev, hostname: '127.0.0.1', port: port || 3000 });
  await app.prepare();
  const handle = app.getRequestHandler();
  const internal = http.createServer((req, res) => { Promise.resolve(handle(req, res)).catch(() => res.destroy()); });
  let target;
  try { target = await listenInternal(internal); }
  catch (error) { await app.close(); throw error; }
  const server = http.createServer((req, res) => {
    const upstream = http.request({ socketPath: target, path: req.url, method: req.method, headers: { ...req.headers, connection: 'close' } }, incoming => {
      res.writeHead(incoming.statusCode, incoming.headers);
      incoming.on('error', () => res.destroy());
      incoming.pipe(res);
    });
    upstream.on('error', () => { if (!res.headersSent) res.writeHead(503); res.end(); });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  });
  const sockets = new Set();
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  server.on('upgrade', (req, socket) => {
    let finished = false;
    let upstream;
    const finish = (code) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (!socket.destroyed) socket.end(`HTTP/1.1 ${code} ${code === 401 ? 'Unauthorized' : 'Service Unavailable'}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
      upstream?.destroy();
    };
    const timer = setTimeout(() => finish(503), 5000);
    socket.on('error', () => { clearTimeout(timer); upstream?.destroy(); });
    socket.on('close', () => { clearTimeout(timer); upstream?.destroy(); });
    try {
      const url = new URL(req.url, 'http://localhost');
      if (!['/api/term/ws', '/api/gui/ws', '/api/browser/ws'].includes(url.pathname)) return finish(503);
      const access = url.searchParams.get('access') || '';
      if (access.length > 128 || (req.headers.cookie || '').length > 8192) return finish(401);
      upstream = http.request({ socketPath: target, method: 'GET', path: '/api/guest/ws-reject?access=' + encodeURIComponent(access),
        headers: { cookie: req.headers.cookie || '', connection: 'close' } }, response => {
        response.resume();
        finish(response.statusCode === 401 ? 401 : 503);
      });
      upstream.on('error', () => finish(503));
      upstream.end();
    } catch { finish(503); }
  });
  server.on('clientError', (_error, socket) => socket.destroy());
  try { await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, hostname, resolve); }); }
  catch (error) { internal.close(); await app.close(); throw error; }
  return { server, internal, async close() { for (const socket of sockets) socket.destroy(); await new Promise(resolve => server.close(resolve)); internal.closeAllConnections(); await new Promise(resolve => internal.close(resolve)); await app.close(); } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startGateway({ dev: process.argv.includes('--dev') }).then(gateway => {
    console.log(`WebDesktop listening on ${gateway.server.address().port}`);
    for (const name of ['SIGINT', 'SIGTERM']) process.once(name, () => { gateway.close().finally(() => process.exit(0)); });
  }).catch(() => { console.error('WebDesktop startup failed'); process.exit(1); });
}
