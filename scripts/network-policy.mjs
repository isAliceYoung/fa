import { isIP } from 'node:net';

export function bindAddress(value = process.env.BIND_HOST) {
  const host = value === undefined || value === '' ? '127.0.0.1' : value;
  if (typeof host !== 'string' || !isIP(host) || host.includes('%')) {
    throw new Error('BIND_HOST must be a literal IPv4 or IPv6 address');
  }
  const loopback = host === '::1' || (isIP(host) === 4 && host.split('.')[0] === '127');
  if (!loopback) console.error(`WARNING: BIND_HOST=${host} exposes WebDesktop beyond loopback; configure access restrictions before use.`);
  return host;
}
