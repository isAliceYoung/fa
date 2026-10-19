import { guestFiles } from "@/lib/guest-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) { return guestFiles(req); }
export async function POST(req: Request) { return guestFiles(req); }
export async function PUT(req: Request) { return guestFiles(req); }
export async function PATCH(req: Request) { return guestFiles(req); }
export async function DELETE(req: Request) { return guestFiles(req); }
