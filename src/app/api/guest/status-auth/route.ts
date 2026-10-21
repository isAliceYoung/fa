import { denyDesktop } from "@/lib/auth";
import { statusAdmission } from "@/lib/status-admission.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(req: Request) {
  const headers = { "Cache-Control": "no-store", "Retry-After": "1" };
  // Public gateway strips client-supplied x-webdesktop-* headers. This marker
  // exists only on the service's direct, DAC-protected Unix HTTP request.
  if (req.headers.get("x-webdesktop-status-auth") !== "gui") return new Response(null, { status: 401, headers });
  const grant = await statusAdmission.authorize("gui", req.headers.get("cookie"), req.headers.get("x-desktop-session"),
    async () => (await denyDesktop(req))?.status ?? 200, req.signal);
  try { return new Response(null, { status: grant.status, headers }); }
  finally { grant.release?.(); }
}
