import { denyDesktop } from "./auth";

// Invoked by the private Next HTTP listener; never performs an Upgrade.
export async function rejectGuestUpgrade(req: Request) {
  try {
    const denied = await denyDesktop(req);
    return denied ?? Response.json({ code: "GUEST_FEATURE_PENDING" }, { status: 503 });
  } catch {
    return Response.json({ code: "AUTH_UNAVAILABLE" }, { status: 503 });
  }
}
