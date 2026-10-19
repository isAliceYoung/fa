import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';

// No TCP fallback: a local shell must cross the service user's DAC boundary.
export async function listenInternal(server) {
  if (process.platform !== 'linux') throw new Error('Protected internal HTTP requires Linux Unix sockets');
  const uid = process.getuid();
  const directory = path.join(os.homedir(), '.webdesktop-http');
  await fs.mkdir(directory, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
  const info = await fs.lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== uid || (info.mode & 0o777) !== 0o700) throw new Error('Unsafe internal HTTP directory');
  const socketPath = path.join(directory, 'next.sock');
  const existing = await fs.lstat(socketPath).catch(error => { if (error.code !== 'ENOENT') throw error; return null; });
  if (existing) {
    if (!existing.isSocket() || existing.uid !== uid) throw new Error('Unknown internal HTTP socket');
    // Remove only our own dead socket after a crash, never a live instance.
    await new Promise((resolve, reject) => {
      const probe = net.createConnection(socketPath);
      probe.setTimeout(1000, () => probe.destroy(new Error('Internal HTTP probe timeout')));
      probe.once('connect', () => { probe.destroy(); reject(new Error('Internal HTTP already running')); });
      probe.once('error', error => error.code === 'ECONNREFUSED' ? resolve() : reject(error));
    });
    const current = await fs.lstat(socketPath);
    if (current.ino !== existing.ino || current.dev !== existing.dev) throw new Error('Internal socket changed');
    await fs.unlink(socketPath);
  }
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socketPath, resolve); });
  await fs.chmod(socketPath, 0o600);
  return socketPath;
}
