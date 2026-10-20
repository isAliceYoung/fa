import { DESKTOP_COOKIE, clearCookieHeader, denyDesktop, revokeDesktopSession } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const denied = await denyDesktop(req);
  if (denied) return denied;
  try { await revokeDesktopSession(req); }
  catch { return Response.json({ error: "服务暂时不可用" }, { status: 503 }); }
  return Response.json({ ok: true }, { headers: { "Set-Cookie": clearCookieHeader(DESKTOP_COOKIE, req), "Cache-Control": "no-store" } });
}
