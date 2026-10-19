import { denyDesktop } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Auth stays inside Next/DB. No credential or bearer lease leaves the backend.
export async function GET(req: Request) {
  return await denyDesktop(req) ?? Response.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
}
