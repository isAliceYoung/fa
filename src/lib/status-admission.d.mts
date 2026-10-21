export type StatusGrant = { status: number; release?: () => void };
export function statusIdentity(cookie: string | null, access: string | null): string | null;
export const statusAdmission: {
  remember(token: string, access: string, expires: number, issued?: boolean): void;
  ingressGrant(cookie: string | null, access: string | null): StatusGrant;
  authorize(scope: 'resources' | 'gui', cookie: string | null, access: string | null, validate: () => Promise<number>, signal?: AbortSignal): Promise<StatusGrant>;
};
