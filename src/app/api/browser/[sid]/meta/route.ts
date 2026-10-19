import { pendingGuestFeature } from "@/lib/guest-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) { return pendingGuestFeature(req); }
