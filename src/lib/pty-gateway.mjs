import fs from 'node:fs/promises';
import os from 'node:os';
import http from 'node:http';
import net from 'node:net';
import { createHash } from 'node:crypto';
import { spawn, execFile } from 'node:child_process';
import { WebSocketServer, WebSocket } from 'ws';

const PACKAGE = '/usr/local/lib/webdesktop-pty';
const HISTORY = 256 * 1024;
const HIGH = 512 * 1024;
const LOW = 128 * 1024;
const FRAME = 65536;
const json = value => Buffer.from(JSON.stringify(value) + '\n');

export function checkTerminalAuth(socketPath, cookie, access) {
  return new Promise(resolve => {
    let finished = false;
    const finish = status => { if (!finished) { finished = true; clearTimeout(timer); resolve(status); } };
    const request = http.get({ socketPath, path: '/api/guest/pty-auth?access=' + encodeURIComponent(access),
      agent: false, headers: { cookie, connection: 'close' } }, response => {
      let count = 0;
      response.on('data', chunk => { count += chunk.length; if (count > 2048) request.destroy(); });
      response.on('end', () => finish([200, 401].includes(response.statusCode) ? response.statusCode : 503));
      response.on('error', () => finish(503));
      response.resume();
    });
    const timer = setTimeout(() => { finish(503); request.destroy(); }, 1500);
    request.on('error', () => finish(503));
  });
}

export async function terminalGateway(socketPath) {
  const ready = await fs.lstat(PACKAGE + '/ready').catch(error => { if (error.code !== 'ENOENT') throw error; return null; });
  if (!ready) return null;
  const identity = os.userInfo();
  if (identity.username !== 'wdsvc' || !identity.uid) throw new Error('PTY backend must be wdsvc');
  for (const name of ['', '/worker.py', '/guard.py', '/ready']) {
    const info = await fs.lstat(PACKAGE + name);
    if (info.uid !== 0 || info.isSymbolicLink() || (name ? !info.isFile() || info.nlink !== 1 || (info.mode & 0o777) !== 0o644 : !info.isDirectory() || (info.mode & 0o777) !== 0o755)) throw new Error('Unsafe PTY package');
  }
  if ((await fs.readFile(PACKAGE + '/ready', 'utf8')) !== 'pty-v1\n') throw new Error('Unknown PTY protocol');
  const sessions = new Map();
  const occupied = new Set();
  const wss = new WebSocketServer({ noServer: true, maxPayload: FRAME, perMessageDeflate: false });
  const stopUnit = slot => new Promise(resolve => execFile('/usr/bin/systemctl', ['--no-ask-password', 'stop', `webdesktop-pty@${slot}.service`],
    { env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' }, timeout: 5000, maxBuffer: 4096 }, error => resolve(!error)));
  const auth = (cookie, access) => checkTerminalAuth(socketPath, cookie, access);

  function closeClient(session, message = '终端会话已结束') {
    const ws = session.client;
    if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ t: 'error', message })); ws.close(1013); }
  }
  function stop(session) {
    if (session.stopping) return;
    session.stopping = true;
    clearInterval(session.timer); clearInterval(session.flow); clearTimeout(session.detachTimer);
    if (session.exited) session.client?.close(1000);
    else closeClient(session);
    session.ipc?.destroy();
    session.guard?.stdin.end(); // EOF is revocation, not detach.
    // Slot remains occupied until systemd confirms the entire old cgroup is gone.
    const guardDone = session.guardClosed ? Promise.race([session.guardClosed, new Promise(resolve => { const timer = setTimeout(() => resolve(false), 6000); timer.unref(); })]) : Promise.resolve(true);
    session.cleanup = Promise.all([stopUnit(session.slot), guardDone]).then(([stopped, guardClosed]) => {
      if (stopped && guardClosed) { sessions.delete(session.key); occupied.delete(session.slot); }
      // Failed cleanup deliberately quarantines the slot; never reuse a live
      // previous incarnation or kill a PID that may have been recycled.
    });
  }
  async function verify(session) {
    if (session.stopping || session.blocked) return false;
    const status = await auth(session.cookie, session.access);
    if (session.stopping || session.blocked) return false;
    if (status === 200) { session.guard.stdin.write('renew\n'); return true; }
    session.blocked = true;
    closeClient(session, status === 401 ? '登录已失效，终端权限已撤销' : '认证服务不可用，终端输入已停止');
    if (status === 401) stop(session);
    // On DB/error, no renew. Independent guard expires within ten seconds even
    // if Node or the same-UID worker is stopped or no longer trustworthy.
    return false;
  }
  function output(session, bytes) {
    session.rate += bytes.length;
    session.history.push(bytes); session.historySize += bytes.length;
    while (session.historySize > HISTORY) { session.historySize -= session.history.shift().length; session.gap = true; }
    if (session.client?.readyState === WebSocket.OPEN) session.client.send(bytes, { binary: true });
    if (session.rate >= 1024 * 1024 || session.client?.bufferedAmount > HIGH || (!session.client && session.historySize >= HISTORY - 16384)) session.ipc.pause();
  }
  async function create(key, cookie, access, cols, rows) {
    const slot = [1, 2].find(value => !occupied.has(value));
    if (!slot) throw new Error('Terminal capacity');
    occupied.add(slot);
    const session = { key, slot, cookie, access, history: [], historySize: 0, rate: 0, epoch: Date.now(), client: null, blocked: false, stopping: false };
    sessions.set(key, session);
    try {
      session.guard = spawn('/usr/bin/python3', ['-I', '-B', PACKAGE + '/guard.py', String(slot)],
        { cwd: '/', env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' }, stdio: ['pipe', 'pipe', 'ignore'] });
      session.guardClosed = new Promise(resolve => {
        session.guard.once('close', () => resolve(true));
      });
      session.guard.stdin.on('error', () => stop(session));
      session.guard.on('error', () => stop(session));
      session.guard.on('exit', () => stop(session));
      await new Promise((resolve, reject) => {
        let data = ''; const timer = setTimeout(() => reject(new Error('Lease guard timeout')), 3000);
        session.guard.once('exit', () => { clearTimeout(timer); reject(new Error('Lease guard failed')); });
        session.guard.stdout.on('data', chunk => {
          data += chunk;
          if (data === 'READY\n') { clearTimeout(timer); resolve(); }
          else if (data.length > 128) { clearTimeout(timer); reject(new Error('Invalid guard')); }
        });
      });
      const directory = await fs.lstat('/run/webdesktop-pty');
      const endpoint = `/run/webdesktop-pty/session-${slot}.sock`;
      const info = await fs.lstat(endpoint);
      if (!directory.isDirectory() || directory.uid !== 0 || directory.gid !== identity.gid || (directory.mode & 0o777) !== 0o750 || !info.isSocket() || info.uid !== 0 || info.gid !== identity.gid || (info.mode & 0o777) !== 0o660) throw new Error('Unsafe PTY socket');
      session.ipc = net.createConnection(endpoint);
      session.ipc.on('error', () => stop(session));
      session.ipc.on('close', () => stop(session));
      await new Promise((resolve, reject) => {
        let incoming = Buffer.alloc(0), initialized = false;
        const timer = setTimeout(() => reject(new Error('PTY startup timeout')), 3000);
        session.ipc.once('connect', () => session.ipc.write(json({ t: 'open', c: cols, r: rows })));
        session.ipc.once('error', () => { clearTimeout(timer); reject(new Error('PTY unavailable')); });
        session.ipc.on('data', chunk => {
          try {
            incoming = Buffer.concat([incoming, chunk]);
            if (incoming.length > 2 * FRAME) throw new Error('PTY frame limit');
            let index;
            while ((index = incoming.indexOf(10)) !== -1) {
              if (index > FRAME) throw new Error('PTY frame limit');
              const message = JSON.parse(incoming.subarray(0, index)); incoming = incoming.subarray(index + 1);
              if (!initialized) {
                if (message.t !== 'ready') throw new Error('PTY initialization failed');
                initialized = true; clearTimeout(timer); resolve(); continue;
              }
              if (message.t === 'o' && typeof message.d === 'string' && /^[A-Za-z0-9+/]*={0,2}$/.test(message.d)) {
                const bytes = Buffer.from(message.d, 'base64');
                if (!bytes.length || bytes.length > 16384 || bytes.toString('base64') !== message.d) throw new Error('PTY output limit');
                output(session, bytes);
              } else if (message.t === 'exit' && Number.isInteger(message.code)) {
                session.exited = true;
                if (session.client?.readyState === WebSocket.OPEN) session.client.send(JSON.stringify({ t: 'exit', code: message.code }));
                stop(session);
              } else throw new Error('Invalid PTY output');
            }
          } catch { clearTimeout(timer); reject(new Error('Invalid PTY IPC')); stop(session); }
        });
      });
      if (session.stopping) throw new Error('PTY exited');
      session.guard.stdin.write('renew\n');
      let checking = false;
      session.timer = setInterval(async () => {
        if (checking || session.stopping || session.blocked) return;
        checking = true; try { await verify(session); } finally { checking = false; }
        if (session.client?.readyState === WebSocket.OPEN) session.client.ping();
      }, 5000);
      session.flow = setInterval(() => {
        if (Date.now() - session.epoch >= 1000) { session.epoch = Date.now(); session.rate = 0; }
        if (session.rate < 1024 * 1024 && session.client?.readyState === WebSocket.OPEN && session.client.bufferedAmount < LOW) session.ipc.resume();
      }, 50);
      session.detachTimer = setTimeout(() => stop(session), 120000);
      return session;
    } catch (error) { stop(session); throw error; }
  }
  return {
    async prepare(url, cookie) {
      if ([...url.searchParams.keys()].some(name => !['sid', 'access', 'cols', 'rows'].includes(name)) || [...url.searchParams.keys()].some(name => url.searchParams.getAll(name).length !== 1)) throw new Error('Fixed terminal parameters required');
      const sid = url.searchParams.get('sid');
      const access = url.searchParams.get('access') || '';
      const cols = Number(url.searchParams.get('cols') || 80), rows = Number(url.searchParams.get('rows') || 24);
      if (!/^[a-f0-9]{24,64}$/.test(sid || '') || !Number.isInteger(cols) || cols < 10 || cols > 500 || !Number.isInteger(rows) || rows < 2 || rows > 200) throw new Error('Invalid terminal parameters');
      const key = createHash('sha256').update(cookie).update('\0').update(access).update('\0').update(sid).digest('hex');
      const existing = sessions.get(key);
      if (existing) { if (existing.stopping || existing.blocked || !existing.ipc) throw new Error('Session unavailable'); return existing; }
      return create(key, cookie, access, cols, rows);
    },
    upgrade(req, socket, head, session) {
      wss.handleUpgrade(req, socket, head, ws => {
        session.client?.close(1000);
        session.client = ws;
        clearTimeout(session.detachTimer);
        if (session.gap) ws.send(JSON.stringify({ t: 'gap' }));
        for (const chunk of session.history) ws.send(chunk, { binary: true });
        session.ipc.resume();
        const queue = [];
        let queued = 0, busy = false;
        const drain = async () => {
          if (busy) return;
          busy = true; ws.pause();
          try {
            if (!(await verify(session)) || session.client !== ws) return;
            while (queue.length) {
              const message = queue.shift(); queued -= message.length;
              if (session.ipc.writableLength + message.length > FRAME) throw new Error('IPC backpressure');
              session.ipc.write(message);
            }
          } catch { stop(session); }
          finally { busy = false; if (!session.stopping && !session.blocked) ws.resume(); }
        };
        ws.on('message', (raw, binary) => {
          if (session.stopping || session.blocked || session.client !== ws) return;
          try {
            if (binary || raw.length > FRAME) throw new Error('Invalid input');
            const message = JSON.parse(raw.toString('utf8'));
            if (!message || Array.isArray(message) || typeof message !== 'object') throw new Error('Invalid input');
            if (message.t === 'i') {
              if (Object.keys(message).sort().join() !== 'd,t' || typeof message.d !== 'string' || Buffer.byteLength(message.d) > 16384) throw new Error('Input budget');
            } else if (message.t === 'r') {
              if (Object.keys(message).sort().join() !== 'c,r,t' || !Number.isInteger(message.c) || !Number.isInteger(message.r) || message.c < 10 || message.c > 500 || message.r < 2 || message.r > 200) throw new Error('Resize budget');
            } else if (message.t === 'kill' && Object.keys(message).length === 1) { stop(session); return; }
            else throw new Error('Unknown input');
            const encoded = json(message);
            if (queue.length >= 64 || queued + encoded.length > FRAME) throw new Error('Input queue budget');
            queue.push(encoded); queued += encoded.length; void drain();
          } catch { stop(session); }
        });
        ws.on('error', () => ws.terminate());
        ws.on('close', () => {
          if (session.client !== ws || session.stopping) return;
          session.client = null;
          session.detachTimer = setTimeout(() => stop(session), 120000);
        });
      });
    },
    async close() {
      for (const session of sessions.values()) stop(session);
      for (const client of wss.clients) client.terminate();
      await Promise.allSettled([...sessions.values()].map(session => session.cleanup));
      wss.close();
    },
  };
}
