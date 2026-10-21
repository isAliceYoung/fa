"use client";

import { useEffect, useRef, useState } from "react";
import { withDesktopAccess } from "../client-session";
import type { Win } from "../wm-store";

type Rfb = { disconnect(): void; addEventListener(name: string, callback: () => void): void; scaleViewport: boolean; resizeSession: boolean; resizeLimits: { width: number; height: number }; qualityLevel: number; compressionLevel: number; focus(): void };
const labels: Record<string, string> = {
  request: "正在建立图形请求", authenticated: "已验证登录，正在准备图形环境", resource_check: "正在检查服务器资源",
  lease_ready: "正在激活图形服务", gui_socket: "正在激活图形服务", gui_worker: "正在准备图形环境",
  x11_ready: "正在初始化桌面组件", dbus_started: "正在初始化输入法", ime_ready: "正在启动窗口管理器",
  openbox_started: "正在连接远程画面", gui_ready: "图形环境已就绪", browser_socket: "正在激活浏览器服务",
  browser_worker: "正在启动浏览器", browser_spawned: "正在等待浏览器原生窗口", browser_visible: "浏览器窗口已出现，正在连接画面",
  browser_ready: "正在连接远程画面", websocket_ready: "正在等待首帧画面",
};
export function BasicGuiApp({ win }: { win: Win }) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState("connecting");
  const [attempt, setAttempt] = useState(0);
  const [stage, setStage] = useState("正在准备图形环境");
  const [elapsed, setElapsed] = useState(0);
  const [firstFrameMs, setFirstFrameMs] = useState<number | null>(null);
  const browser = win.app === "browser";
  const allowed = !win.desktopFile && (!win.cmd || win.cmd === "pcmanfm") && (!win.path || win.path === "~");
  useEffect(() => {
    if (!allowed) return;
    let disposed = false, finished = false, polling = false, sid = "";
    const started = performance.now();
    let rfb: Rfb | undefined;
    setState("connecting"); setStage("正在建立图形请求"); setFirstFrameMs(null); setElapsed(0);
    const abort = new AbortController();
    async function progress() {
      if (disposed || finished || polling || !sid) return;
      polling = true;
      const timeout = setTimeout(() => abort.abort(), 3000);
      try {
        const response = await fetch("/api/gui/status?sid=" + sid, { cache: "no-store", signal: abort.signal });
        if (response.ok) {
          const data = await response.json();
          if (disposed) return;
          if (data.state === "waiting_resources") setStage(data.reason === "telemetry_unavailable" ? "资源状态暂不可用，请稍后重试" : "正在等待服务器资源，请关闭暂时不用的应用后重试");
          else if (data.stage && labels[data.stage]) setStage(labels[data.stage]);
        }
      } catch { /* WebSocket is authoritative; no automatic reconnect loop. */ }
      finally { clearTimeout(timeout); polling = false; }
      if (!disposed) setElapsed(Math.floor((performance.now() - started) / 1000));
    }
    const progressTimer = setInterval(() => void progress(), 1000);
    void (async () => {
      try {
        const path = "/novnc/core/rfb.js";
        const module = await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ path);
        if (disposed || !host.current) return;
        sid = Array.from(crypto.getRandomValues(new Uint8Array(12)), value => value.toString(16).padStart(2, "0")).join("");
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const box = host.current.getBoundingClientRect();
        const width = Math.max(640, Math.min(1600, Math.floor(box.width))), height = Math.max(480, Math.min(1000, Math.floor(box.height)));
        rfb = new module.default(host.current, withDesktopAccess(proto + "://" + location.host + "/api/gui/ws?sid=" + sid + "&app=" + (browser ? "chromium" : "pcmanfm") + "&w=" + width + "&h=" + height), { shared: false }) as Rfb;
        rfb.resizeLimits = { width: 1600, height: 1000 };
        rfb.scaleViewport = true; rfb.resizeSession = true; rfb.qualityLevel = 5; rfb.compressionLevel = 6;
        rfb.addEventListener("connect", () => { if (!disposed) { setStage("已连接，正在等待首帧画面"); rfb?.focus(); } });
        rfb.addEventListener("firstframe", () => { if (!disposed) { finished = true; setState("open"); setFirstFrameMs(Math.round(performance.now() - started)); } });
        rfb.addEventListener("disconnect", () => { if (!disposed) { setState("closed"); void progress().finally(() => { finished = true; }); } });
        rfb.addEventListener("securityfailure", () => { if (!disposed) { setState("closed"); finished = true; } });
        void progress();
      } catch { if (!disposed) { setState("closed"); finished = true; } }
    })();
    return () => { disposed = true; clearInterval(progressTimer); abort.abort(); rfb?.disconnect(); };
  }, [attempt, allowed, browser, win.id]);
  return <div data-testid="basic-gui" data-status={state} data-first-frame-ms={firstFrameMs ?? ""} className="flex h-full flex-col bg-slate-950 text-slate-200">
    <div className="px-3 py-1 text-xs text-slate-400">{browser ? "Chromium · 可访问外部网站" : "PCManFM · 访客图形环境"} · 未开放网页剪贴板与拖拽</div>
    <div ref={host} className="relative min-h-0 flex-1 overflow-hidden" />
    {(!allowed || state !== "open") && <div className="absolute inset-10 grid place-items-center bg-slate-950/95 text-center">
      <div>{!allowed ? "此阶段仅开放固定的 PCManFM，暂不支持其他启动命令。" : state === "connecting" ? stage + "（已等待 " + elapsed + " 秒）" : "图形会话已结束、启动超时或不可用。" + stage}
        {allowed && state === "closed" && <button className="ml-3 rounded bg-sky-700 px-3 py-2" onClick={() => setAttempt(value => value + 1)}>重新打开</button>}
      </div>
    </div>}
  </div>;
}
