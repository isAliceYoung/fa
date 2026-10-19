// Browser-side resumable upload state contains only an opaque operation ID.
// All filesystem access and authentication remain on the real server.
export async function uploadFingerprint(file, signal) {
  const parts = [];
  for (let offset = 0; offset < file.size; offset += 8 * 1024 * 1024) {
    if (signal?.aborted) throw new DOMException('Upload cancelled', 'AbortError');
    parts.push(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.slice(offset, offset + 8 * 1024 * 1024).arrayBuffer())));
  }
  const prefix = new TextEncoder().encode('wd-upload-v1\0' + file.size + '\0');
  const manifest = new Uint8Array(prefix.length + parts.length * 32);
  manifest.set(prefix); parts.forEach((part, index) => manifest.set(part, prefix.length + index * 32));
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', manifest)), n => n.toString(16).padStart(2, '0')).join('');
}

export async function uploadFile(dir, name, file, { signal, onProgress = () => {}, storage } = {}) {
  if (file.size > 100 * 1024 * 1024) throw new Error('单个文件不能超过 100 MiB');
  if (storage === undefined) { try { storage = globalThis.localStorage; } catch { storage = null; } }
  const fingerprint = await uploadFingerprint(file, signal);
  const key = 'wd.upload.v2.' + JSON.stringify([dir, name, file.size, file.lastModified, fingerprint]);
  let id;
  try { id = storage?.getItem(key); } catch { /* Upload still works without persistent browser storage. */ }
  let resumed = /^[a-z0-9]{8,40}$/.test(id || '');
  if (!resumed) id = Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
  try { storage?.setItem(key, id); } catch { /* In-memory retry remains available. */ }
  const base = '/api/sys/upload?' + new URLSearchParams({ dir, name, id, fingerprint, size: String(file.size) });
  const forget = () => { try { storage?.removeItem(key); } catch { /* Optional browser state. */ } };
  const inspect = async () => {
    const r = await fetch(base, { cache: 'no-store', signal });
    if (r.status === 404) return { offset: 0, complete: false };
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `上传状态不可用（${r.status}）`);
    if (!Number.isSafeInteger(data.offset) || data.offset < 0 || data.offset > file.size) throw new Error('上传断点不匹配，请先取消旧上传');
    return data;
  };
  const finish = async state => {
    onProgress(file.size);
    // A completed cancellation only releases metadata; it never deletes a file.
    try { if ((await fetch(base, { method: 'DELETE' })).ok) forget(); } catch { /* Keep ID to resolve an uncertain final response. */ }
    return state;
  };
  try {
    let state = resumed ? await inspect() : { offset: 0, complete: false };
    if (state.complete) return finish(state);
    let offset = state.offset;
    onProgress(offset);
    while (true) {
      if (signal?.aborted) throw new DOMException('Upload cancelled', 'AbortError');
      const end = Math.min(offset + 8 * 1024 * 1024, file.size);
      let acknowledged = false;
      let last;
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          const r = await fetch(base + '&offset=' + offset + (end === file.size ? '&done=1' : ''), { method: 'POST', body: file.slice(offset, end), signal });
          const data = await r.json();
          if (r.ok) { state = data; acknowledged = true; break; }
          if (![408, 409, 429, 500, 502, 503, 504].includes(r.status)) throw Object.assign(new Error(data.error || `上传失败（${r.status}）`), { permanent: true });
          last = new Error(data.error || '上传暂时不可用');
        } catch (error) {
          if (signal?.aborted || error?.permanent) throw error;
          last = error;
        }
        state = await inspect();
        if (state.complete || state.offset !== offset) { acknowledged = true; break; }
        if (attempt < 3) await new Promise((resolve, reject) => {
          const aborted = () => { clearTimeout(timer); reject(new DOMException('Upload cancelled', 'AbortError')); };
          const timer = setTimeout(() => { signal?.removeEventListener('abort', aborted); resolve(); }, 300 * (attempt + 1));
          signal?.addEventListener('abort', aborted, { once: true });
        });
      }
      if (!acknowledged) throw last || new Error('上传暂停');
      if (state.complete) return finish(state);
      if (!Number.isSafeInteger(state.offset) || state.offset <= offset || state.offset > file.size) throw new Error('上传断点异常');
      offset = state.offset;
      onProgress(offset);
    }
  } catch (error) {
    if (signal?.aborted) {
      try {
        const r = await fetch(base, { method: 'DELETE' });
        if (r.ok) {
          const data = await r.json();
          forget();
          if (data.completed) { onProgress(file.size); return { path: data.path, offset: data.offset, complete: true }; }
          throw new DOMException('Upload cancelled', 'AbortError');
        }
        if (r.status === 404) { forget(); throw new DOMException('Upload cancelled', 'AbortError'); }
      } catch (cancelError) { if (cancelError?.name === 'AbortError') throw cancelError; }
      throw new Error('已停止发送，取消尚未确认；重新选择同一文件可恢复');
    }
    throw error;
  }
}
