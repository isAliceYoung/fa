import http from "node:http";
import type { Duplex } from "node:stream";
import { desktopAuthedHeader } from "./auth";

// 3A: explicitly close all unmigrated execution channels. No legacy imports.
const paths = new Set(["/api/term/ws", "/api/gui/ws", "/api/browser/ws"]);
type Listener = (req: http.IncomingMessage, socket: Duplex, head: Buffer) => void;
const state = globalThis as unknown as { __wdGuestWs?: WeakSet<http.Server> };
const servers = (state.__wdGuestWs ??= new WeakSet());
function ours(req: http.IncomingMessage) { return paths.has(new URL(req.url ?? "/", "http://localhost").pathname); }
function attach(server: http.Server) {
  if (servers.has(server)) return;
  servers.add(server);
  const guard = (handler: Listener): Listener => function (this: unknown, req, socket, head) { if (!ours(req)) handler.call(this, req, socket, head); };
  const existing = server.listeners("upgrade") as Listener[];
  server.removeAllListeners("upgrade");
  server.on("upgrade", (req, socket) => {
    if (!ours(req)) return;
    void desktopAuthedHeader(req.headers.cookie, new URL(req.url ?? "/", "http://localhost").searchParams.get("access")).then((ok) => {
      if (!socket.destroyed) socket.end(`HTTP/1.1 ${ok ? "503 Service Unavailable" : "401 Unauthorized"}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n`);
    });
  });
  for (const handler of existing) server.on("upgrade", guard(handler));
  for (const name of ["on", "addListener", "prependListener", "once"] as const) {
    const original = (server[name] as (event: string, handler: (...args: never[]) => void) => http.Server).bind(server);
    (server as unknown as Record<string, unknown>)[name] = (event: string, handler: (...args: never[]) => void) => original(event, event === "upgrade" ? guard(handler as unknown as Listener) as unknown as (...args: never[]) => void : handler);
  }
}
export function installGuestWs() {
  const find = () => {
    const handles = (process as unknown as { _getActiveHandles?: () => unknown[] })._getActiveHandles?.() ?? [];
    const active = handles.filter((handle): handle is http.Server => handle instanceof http.Server);
    active.forEach(attach);
    return active.length > 0;
  };
  if (find()) return;
  let attempts = 0;
  const timer = setInterval(() => { if (find() || ++attempts >= 100) clearInterval(timer); }, 200);
  timer.unref();
}
