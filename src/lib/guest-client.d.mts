export const MAX_BODY: number;
export function guestRequest(endpoint: string, options?: { method?: string; query?: Record<string, string>; body?: unknown; stream?: ReadableStream<Uint8Array> | null; signal?: AbortSignal }): Promise<Response>;
export function boundedJson(request: Request): Promise<Record<string, unknown>>;
