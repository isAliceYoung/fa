// Only the DAC-protected internal listener may supply these derived headers.
export function privateHttpHeaders(req, origins) {
  const headers = { ...req.headers, connection: 'close' };
  for (const key of Object.keys(headers)) if (/^(forwarded|x-forwarded-|x-real-ip$|x-webdesktop-)/i.test(key)) delete headers[key];
  const https = [...(origins ?? [])].some(origin => origin.startsWith('https:') && new URL(origin).host === req.headers.host);
  headers['x-webdesktop-https'] = https ? '1' : '0';
  headers['x-webdesktop-client-ip'] = req.socket.remoteAddress || 'unknown';
  return headers;
}
