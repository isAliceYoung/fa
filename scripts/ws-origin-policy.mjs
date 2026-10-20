// Trust server configuration, never Host/X-Forwarded-* as an origin allowlist.
export function publicOrigins(value) {
  if (value === undefined) return null;
  if (!value || value.length > 4096) throw new Error('Invalid WD_PUBLIC_ORIGINS');
  const list = value.split(',');
  if (list.length > 16) throw new Error('Too many public origins');
  for (const origin of list) {
    const url = new URL(origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== origin || url.username || url.password || url.hostname.includes('%')) throw new Error('Canonical HTTP(S) origins required');
  }
  return new Set(list);
}

export function validWebSocketSource(req, configured, hostname, port) {
  // Reject duplicates instead of relying on Node's header coalescing rules.
  const names = req.rawHeaders.filter((_, i) => i % 2 === 0).map(name => name.toLowerCase());
  if (names.filter(name => name === 'host').length !== 1 || names.filter(name => name === 'origin').length !== 1) return false;
  const origin = req.headers.origin, host = req.headers.host;
  if (typeof origin !== 'string' || typeof host !== 'string') return false;
  const ip = hostname.includes(':') ? `[${hostname}]` : hostname;
  const fallback = `http://${ip}${port === 80 ? '' : ':' + port}`;
  if (!(configured ?? new Set([fallback])).has(origin)) return false;
  // Proxies must preserve Host. Forwarded headers never widen this boundary.
  try { return new URL(origin).host === host; } catch { return false; }
}
