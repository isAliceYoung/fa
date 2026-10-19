import { denyDesktop } from "@/lib/auth";
import { boundedJson, guestRequest } from "./guest-client.mjs";

// Bound admission before JSON buffering and retain it for the complete streamed
// response. The agent's four threads alone cannot bound memory inside Next.
const admissionHolder = globalThis as unknown as { __wdFileAdmission?: { active: number } };
const admission = admissionHolder.__wdFileAdmission ??= { active: 0 };
function admitted(handler: (req: Request) => Promise<Response>) {
  return async (req: Request): Promise<Response> => {
    if (admission.active >= 4) return Response.json({ error: "文件操作繁忙，请稍后重试", code: "GUEST_BUSY" }, { status: 429, headers: { "Retry-After": "1", "Cache-Control": "no-store" } });
    admission.active++;
    let released = false;
    const release = () => { if (!released) { released = true; admission.active--; } };
    try {
      const response = await handler(req);
      if (!response.body) { release(); return response; }
      const reader = response.body.getReader();
      const abort = () => { void reader.cancel().catch(() => {}).finally(release); };
      const finish = () => { req.signal.removeEventListener("abort", abort); release(); };
      if (req.signal.aborted) { await reader.cancel(); release(); return new Response(null, { status: 499 }); }
      req.signal.addEventListener("abort", abort, { once: true });
      const body = new ReadableStream<Uint8Array>({
        async pull(controller) {
          try { const item = await reader.read(); if (item.done) { finish(); controller.close(); } else controller.enqueue(item.value); }
          catch (error) { finish(); controller.error(error); }
        },
        async cancel() { try { await reader.cancel(); } finally { finish(); } },
      }, { highWaterMark: 65536, size: chunk => chunk.byteLength });
      return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
    } catch (error) { release(); throw error; }
  };
}

export async function pendingGuestFeature(req: Request) {
  const denied = await denyDesktop(req);
  return denied ?? Response.json({ error: "此功能的访客执行层尚未迁移，当前开发阶段暂不可用", code: "GUEST_FEATURE_PENDING" }, { status: 503, headers: { "Cache-Control": "no-store" } });
}

async function filesInner(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  const query = new URL(req.url).searchParams;
  const signal = req.signal;
  if (req.method === "GET") return guestRequest(query.get("read") === "1" ? "/v1/read-text" : "/v1/list", { query: { path: query.get("path") ?? "~", hidden: query.get("hidden") ?? "1" }, signal });
  if (req.method === "DELETE") return guestRequest("/v1/remove", { method: "POST", body: { path: query.get("path"), permanent: query.get("permanent") === "1", emptyTrash: query.get("emptyTrash") === "1" }, signal });
  if (!["POST", "PUT", "PATCH"].includes(req.method)) return pendingGuestFeature(req);
  try {
    const b = await boundedJson(req);
    if (req.method === "PATCH") {
      if (b.pasteFromVnc) return pendingGuestFeature(req);
      const body = Object.fromEntries(["path", "name", "moveTo", "restore", "copyPaths", "destDir", "cut"].filter(k => k in b).map(k => [k, b[k]]));
      return guestRequest("/v1/action", { method: "POST", body, signal });
    }
    const body = req.method === "POST" ? { dir: b.dir ?? "~", name: b.name, kind: b.kind, content: b.content ?? "" } : { path: b.path, content: b.content };
    return guestRequest(req.method === "POST" ? "/v1/create" : "/v1/write", { method: req.method, body, signal });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") return Response.json({ error: "请求读取超时" }, { status: 408 });
    return Response.json({ error: error instanceof RangeError ? "内容过大" : "请求无效" }, { status: error instanceof RangeError ? 413 : 400 });
  }
}

async function uploadInner(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  const q = new URL(req.url).searchParams;
  const query = Object.fromEntries(["dir", "name", "id", "offset", "done", "fingerprint", "size"].filter(k => q.has(k)).map(k => [k, q.get(k)!]));
  if (req.method === "POST" && q.get("mkdir") === "1") return guestRequest("/v1/mkdir", { method: "POST", body: { dir: q.get("dir") ?? "~", name: q.get("name") }, signal: req.signal });
  return guestRequest("/v1/upload", { method: req.method, query, stream: req.method === "POST" ? req.body : null, signal: req.signal });
}

async function downloadInner(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  return guestRequest("/v1/export", { query: { path: new URL(req.url).searchParams.get("path") ?? "~" }, signal: req.signal });
}

async function rawInner(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  return guestRequest("/v1/read", { query: { path: new URL(req.url).searchParams.get("path") ?? "~" }, signal: req.signal });
}

async function infoInner(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  if (new URL(req.url).searchParams.get("lite") !== "1") return pendingGuestFeature(req);
  return guestRequest("/v1/info", { signal: req.signal });
}

export const guestFiles = admitted(filesInner);
export const guestUpload = admitted(uploadInner);
export const guestDownload = admitted(downloadInner);
export const guestRaw = admitted(rawInner);
export const guestInfo = admitted(infoInner);
