export function uploadFile(dir: string, name: string, file: File, options?: { signal?: AbortSignal; onProgress?: (offset: number) => void; storage?: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null }): Promise<{ path?: string; offset: number; complete: boolean }>;
export function uploadFingerprint(file: File, signal?: AbortSignal): Promise<string>;
