import { denyDesktop } from "@/lib/auth";
import { resourceSnapshot } from "@/lib/resource-monitor.mjs";
import { statusAdmission } from "@/lib/status-admission.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(req: Request) {
  const headers = { "Cache-Control": "no-store", "Retry-After": "5" };
  const grant = await statusAdmission.authorize("resources", req.headers.get("cookie"),
    req.headers.get("x-desktop-session") ?? new URL(req.url).searchParams.get("access"),
    async () => (await denyDesktop(req))?.status ?? 200, req.signal);
  if (grant.status !== 200) return Response.json({ error: grant.status === 401 ? "桌面登录已失效" : grant.status === 429 ? "资源状态请求繁忙，请稍后重试" : "服务暂时不可用，请稍后重试" }, { status: grant.status, headers });
  try {
    if (new URL(req.url).search) return Response.json({ error: "资源接口不接受路径或采样参数" }, { status: 400, headers });
    const data = await resourceSnapshot();
    return Response.json(data, { status: data.available ? 200 : 503, headers });
  } finally { grant.release?.(); }
}
