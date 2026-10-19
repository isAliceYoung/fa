import { denyDesktop } from "@/lib/auth";
import { boundedJson, guestRequest } from "./guest-client.mjs";

export async function pendingGuestFeature(req: Request) {
  const denied = await denyDesktop(req);
  return denied ?? Response.json({ error: "此功能的访客执行层尚未迁移，当前开发阶段暂不可用", code: "GUEST_FEATURE_PENDING" }, { status: 503, headers: { "Cache-Control": "no-store" } });
}

export async function guestFiles(req: Request) {
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

export async function guestUpload(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  const q = new URL(req.url).searchParams;
  const query = Object.fromEntries(["dir", "name", "id", "offset", "done"].filter(k => q.has(k)).map(k => [k, q.get(k)!]));
  if (req.method === "POST" && q.get("mkdir") === "1") return guestRequest("/v1/mkdir", { method: "POST", body: { dir: q.get("dir") ?? "~", name: q.get("name") }, signal: req.signal });
  return guestRequest("/v1/upload", { method: req.method, query, stream: req.method === "POST" ? req.body : null, signal: req.signal });
}

export async function guestDownload(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  return guestRequest("/v1/export", { query: { path: new URL(req.url).searchParams.get("path") ?? "~" }, signal: req.signal });
}

export async function guestRaw(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  return guestRequest("/v1/read", { query: { path: new URL(req.url).searchParams.get("path") ?? "~" }, signal: req.signal });
}

export async function guestInfo(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  if (new URL(req.url).searchParams.get("lite") !== "1") return pendingGuestFeature(req);
  return guestRequest("/v1/info", { signal: req.signal });
}
