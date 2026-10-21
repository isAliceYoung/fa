import http from 'node:http';
import next from 'next';
import { pathToFileURL } from 'node:url';
import { bindAddress } from './scripts/network-policy.mjs';
import { listenInternal } from './scripts/internal-http.mjs';
import { terminalGateway, checkTerminalAuth } from './src/lib/pty-gateway.mjs';
import { publicOrigins, validWebSocketSource } from './scripts/ws-origin-policy.mjs';
import { guiGateway } from './src/lib/gui-gateway.mjs';
import { privateHttpHeaders } from './scripts/http-boundary.mjs';
import { startResourceSampling } from './src/lib/resource-monitor.mjs';
import { statusAdmission, checkGuiStatusAuth } from './src/lib/status-admission.mjs';

// Separate listeners in one process. Next owns only the private HTTP listener.
export async function startGateway({ port = Number(process.env.PORT || 3000), hostname, dev = false } = {}) {
  hostname = bindAddress(hostname);
  const configuredOrigins = publicOrigins(process.env.WD_PUBLIC_ORIGINS ?? process.env.WD_PUBLIC_URL);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid port');
  const app = next({ dev, hostname: '127.0.0.1', port: port || 3000 });
  await app.prepare();
  const handle = app.getRequestHandler();
  const internal = http.createServer((req, res) => { Promise.resolve(handle(req, res)).catch(() => res.destroy()); });
  let target;
  try { target = await listenInternal(internal); }
  catch (error) { await app.close(); throw error; }
  let terminals;
  let graphical;
  try { terminals = await terminalGateway(target); }
  catch (error) { internal.close(); await app.close(); throw error; }
  try { graphical = await guiGateway(target); }
  catch (error) { await terminals?.close(); internal.close(); await app.close(); throw error; }
  const stopSampling=startResourceSampling();
  const server = http.createServer((req, res) => {
    const ip = hostname.includes(':') ? `[${hostname}]` : hostname;
    const localHost = server.address().port === 80 ? ip : `${ip}:${server.address().port}`;
    const allowedHost = configuredOrigins ? [...configuredOrigins].some(origin => new URL(origin).host === req.headers.host) : req.headers.host === localHost;
    if (!allowedHost && !(req.url === '/api/health' && req.headers.host === localHost)) { res.writeHead(403); res.end(); return; }
    if(req.url?.split('?')[0]==='/api/resources'&&['GET','HEAD'].includes(req.method)){
      let access=req.headers['x-desktop-session'];
      try{access??=new URL(req.url,'http://localhost').searchParams.get('access');}catch{}
      const grant=statusAdmission.ingressGrant(req.headers.cookie||'',access);
      if(grant.status!==200){res.writeHead(grant.status,{'Cache-Control':'no-store',...(grant.status===429?{'Retry-After':'1'}:{})});res.end();return;}
      res.once('close',grant.release);
    }
    if(req.url?.split('?')[0]==='/api/gui/status'){
      const access=req.headers['x-desktop-session'],cookie=req.headers.cookie||'';
      if(req.method!=='GET'){res.writeHead(405);res.end();return;}
      if(typeof access!=='string'||access.length>128||cookie.length>8192){res.writeHead(401);res.end();return;}
      let sid;
      try{const u=new URL(req.url,'http://localhost');sid=u.searchParams.get('sid');if(!/^[a-f0-9]{24,64}$/.test(sid||'')||[...u.searchParams.keys()].some(k=>k!=='sid')||u.searchParams.getAll('sid').length!==1)throw new Error();}
      catch{res.writeHead(400);res.end();return;}
      const grant=statusAdmission.ingressGrant(cookie,access);
      if(grant.status!==200){res.writeHead(grant.status,{'Cache-Control':'no-store',...(grant.status===429?{'Retry-After':'1'}:{})});res.end();return;}
      res.once('close',grant.release);
      void(async()=>{try{
        const status=await checkGuiStatusAuth(target,cookie,access);
        if(res.destroyed)return;
        if(status!==200){res.writeHead(status,{'Cache-Control':'no-store',...(status===429?{'Retry-After':'1'}:{})});res.end();return;}
        res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
        res.end(JSON.stringify(graphical?.progress(sid,cookie,access)??{state:'unavailable',stage:'request',timings:{},workerTimings:{}}));
      }catch{if(!res.destroyed){res.writeHead(503);res.end();}}finally{grant.release();}})();
      return;
    }
    const upstream = http.request({ socketPath: target, path: req.url, method: req.method, headers: privateHttpHeaders(req, configuredOrigins) }, incoming => {
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
  server.on('upgrade', (req, socket, head) => {
    const requestStarted=Date.now();
    let finished = false;
    const pending = new AbortController();
    let upstream;
    const finish = (code) => {
      if (finished) return;
      finished = true;
      pending.abort();
      clearTimeout(timer);
      if (!socket.destroyed) socket.end(`HTTP/1.1 ${code} ${code === 401 ? 'Unauthorized' : code === 403 ? 'Forbidden' : 'Service Unavailable'}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
      upstream?.destroy();
    };
    const timer = setTimeout(() => finish(503), 10000);
    socket.on('error', () => { if (!finished) pending.abort(); clearTimeout(timer); upstream?.destroy(); });
    socket.on('close', () => { if (!finished) pending.abort(); clearTimeout(timer); upstream?.destroy(); });
    try {
      const url = new URL(req.url, 'http://localhost');
      if (!validWebSocketSource(req, configuredOrigins, hostname, server.address().port)) return finish(403);
      if (!['/api/term/ws', '/api/gui/ws', '/api/browser/ws'].includes(url.pathname)) return finish(503);
      const access = url.searchParams.get('access') || '';
      if (access.length > 128 || (req.headers.cookie || '').length > 8192) return finish(401);
      const execution = url.pathname === '/api/term/ws' ? terminals : url.pathname === '/api/gui/ws' ? graphical : null;
      if (execution) {
        void (async () => {
          try {
            const status = await checkTerminalAuth(target, req.headers.cookie || '', access);
            if (pending.signal.aborted || socket.destroyed) return;
            if (status !== 200) return finish(status === 401 ? 401 : 503);
            const session = await execution.prepare(url, req.headers.cookie || '', { signal: pending.signal,requestStarted,authenticatedAt:Date.now() });
            if (finished || socket.destroyed || pending.signal.aborted) { execution.cancel?.(session); return; }
            const recheck = await checkTerminalAuth(target, req.headers.cookie || '', access);
            if (recheck !== 200 || finished || socket.destroyed || pending.signal.aborted) { execution.cancel?.(session); if (!finished) finish(recheck === 401 ? 401 : 503); return; }
            finished = true; clearTimeout(timer);
            execution.upgrade(req, socket, head, session);
          } catch { if (finished) socket.destroy(); else finish(503); }
        })();
        return;
      }
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
  catch (error) { stopSampling();internal.close(); await app.close(); throw error; }
  return { server, internal, async close() { stopSampling();await graphical?.close(); await terminals?.close(); for (const socket of sockets) socket.destroy(); await new Promise(resolve => server.close(resolve)); internal.closeAllConnections(); await new Promise(resolve => internal.close(resolve)); await app.close(); } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startGateway({ dev: process.argv.includes('--dev') }).then(gateway => {
    console.log(`WebDesktop listening on ${gateway.server.address().port}`);
    for (const name of ['SIGINT', 'SIGTERM']) process.once(name, () => { gateway.close().finally(() => process.exit(0)); });
  }).catch(() => { console.error('WebDesktop startup failed'); process.exit(1); });
}
