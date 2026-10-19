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
  if (req.method !== "POST" && req.method !== "PUT") return pendingGuestFeature(req);
  try {
    const b = await boundedJson(req);
    const body = req.method === "POST" ? { dir: b.dir ?? "~", name: b.name, kind: b.kind, content: b.content ?? "" } : { path: b.path, content: b.content };
    return guestRequest(req.method === "POST" ? "/v1/create" : "/v1/write", { method: req.method, body, signal });
  } catch (error) {
    return Response.json({ error: error instanceof RangeError ? "内容过大" : "请求无效" }, { status: error instanceof RangeError ? 413 : 400 });
  }
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
