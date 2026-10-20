"use client";

import { useEffect, useRef, useState } from "react";
import { withDesktopAccess } from "../client-session";
import type { Win } from "../wm-store";

type Rfb = { disconnect(): void; addEventListener(name: string, callback: () => void): void; scaleViewport: boolean; resizeSession: boolean; qualityLevel: number; compressionLevel: number; focus(): void };

export function BasicGuiApp({ win }: { win: Win }) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState("connecting");
  const [attempt, setAttempt] = useState(0);
  const allowed = !win.desktopFile && (!win.cmd || win.cmd === "pcmanfm") && (!win.path || win.path === "~");
  useEffect(() => {
    if (!allowed) return;
    let disposed = false;
    let rfb: Rfb | undefined;
    setState("connecting");
    void (async () => {
      try {
        const path = "/novnc/core/rfb.js";
        const module = await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ path);
        if (disposed || !host.current) return;
        const sid = Array.from(crypto.getRandomValues(new Uint8Array(12)), value => value.toString(16).padStart(2, "0")).join("");
        const proto = location.protocol === "https:" ? "wss" : "ws";
        rfb = new module.default(host.current, withDesktopAccess(`${proto}://${location.host}/api/gui/ws?sid=${sid}&app=pcmanfm&w=1024&h=640`), { shared: false }) as Rfb;
        rfb.scaleViewport = true; rfb.resizeSession = false; rfb.qualityLevel = 5; rfb.compressionLevel = 6;
        rfb.addEventListener("connect", () => { if (!disposed) { setState("open"); rfb?.focus(); } });
        rfb.addEventListener("disconnect", () => { if (!disposed) { setState("closed"); void fetch("/api/sys/info?lite=1").catch(() => {}); } });
        rfb.addEventListener("securityfailure", () => { if (!disposed) setState("closed"); });
      } catch { if (!disposed) setState("closed"); }
    })();
    return () => { disposed = true; rfb?.disconnect(); };
  }, [attempt, allowed, win.id]);
  return <div data-testid="basic-gui" data-status={state} className="flex h-full flex-col bg-slate-950 text-slate-200">
    <div className="px-3 py-1 text-xs text-slate-400">PCManFM · 访客图形环境 · 暂不联网，未开放网页剪贴板与拖拽</div>
    <div ref={host} className="relative min-h-0 flex-1 overflow-hidden" />
    {(!allowed || state !== "open") && <div className="absolute inset-10 grid place-items-center bg-slate-950/95 text-center">
      <div>{!allowed ? "此阶段仅开放固定的 PCManFM，暂不支持其他启动命令。" : state === "connecting" ? "正在连接图形环境…" : "图形会话已结束或不可用。"}
        {allowed && state === "closed" && <button className="ml-3 rounded bg-sky-700 px-3 py-2" onClick={() => setAttempt(value => value + 1)}>重新打开</button>}
      </div>
    </div>}
  </div>;
}
