import { denyDesktop } from "@/lib/auth";
import { resourceSnapshot } from "@/lib/resource-monitor.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const holder = globalThis as unknown as { __wdResources?: { active: number; start: number; count: number } };
const limits = holder.__wdResources ??= { active: 0, start: 0, count: 0 };
export async function GET(req: Request) {
  const now = Date.now();
  if (now - limits.start >= 1000) { limits.start = now; limits.count = 0; }
  const headers = { "Cache-Control": "no-store", "Retry-After": "5" };
  if (limits.active >= 4 || limits.count >= 12) return Response.json({ error: "资源状态请求繁忙，请稍后重试" }, { status: 429, headers });
  limits.active++; limits.count++;
  try {
    const denied = await denyDesktop(req);
    if (denied) return denied;
    const data = await resourceSnapshot();
    return Response.json(data, { status: data.available ? 200 : 503, headers });
  } finally { limits.active--; }
}
