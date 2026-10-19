import { pendingGuestFeature } from "@/lib/guest-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) { return pendingGuestFeature(req); }
