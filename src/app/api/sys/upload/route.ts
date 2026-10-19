import { guestUpload } from "@/lib/guest-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) { return guestUpload(req); }
export async function POST(req: Request) { return guestUpload(req); }
export async function DELETE(req: Request) { return guestUpload(req); }
