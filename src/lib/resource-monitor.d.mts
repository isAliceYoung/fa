export function resourceSnapshot(): Promise<{ schema: number; available: boolean; at: number; status: { level: string; message: string; reasons: string[]; notify: boolean }; [key: string]: unknown }>;
export function graphicalAdmission(): Promise<"unknown" | "busy" | "ready">;
