// Terminal bytes are untrusted requests, never commands or authorization.
const guestPath = value => typeof value === 'string' && value.length <= 512 && /^~(?:\/[^\x00-\x1f\x7f\\]*)?$/.test(value) && !value.split('/').some(part => part === '.' || part === '..');
function decode(value) {
  try {
    if (value.length > 1024 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) return null;
    const bytes = Uint8Array.from(atob(value), c => c.charCodeAt(0));
    if (btoa(String.fromCharCode(...bytes)) !== value) return null;
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch { return null; }
}
export function terminalAction(data) {
  if (typeof data !== 'string' || data.length > 2048) return null;
  const parts = data.split(';');
  if (parts[0] === 'gui' && parts.length === 3 && decode(parts[1]) === 'pcmanfm' && decode(parts[2]) === '~') return { app: 'gui', path: '~', label: 'PCManFM', cmd: 'pcmanfm' };
  if (parts.length === 3 && parts[0] === 'open' && ['dir', 'file'].includes(parts[1]) && guestPath(parts[2])) return { app: parts[1] === 'dir' ? 'sysfiles' : 'sysedit', path: parts[2], label: parts[2] };
  return null;
}
