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
// Explicit, user-initiated text-only clipboard. Nothing is read or written in
// the background, nothing is stored (no history, storage or logs).
const CLIP_MAX = 16384;
const clipMessages: Record<string, string> = {
  EMPTY: "远程剪贴板为空或没有可粘贴的文字。", NON_TEXT: "远程剪贴板不是纯文字（文件、图片或富文本），已拒绝。",
  TOO_LARGE: "文字超过 16 KiB 上限，已拒绝，未截断。", INVALID_UTF8: "文字编码无效，已拒绝。", CONTROL_CHARACTERS: "文字包含不支持的控制字符，已拒绝。",
  BUSY: "上一次剪贴板操作尚未完成，请稍后重试。", RATE_LIMITED: "剪贴板操作过于频繁，请稍后重试。", GUI_INACTIVE: "图形会话已结束或已不是当前会话。",
  UNAUTHORIZED: "登录已失效，请重新登录。", TIMEOUT: "远程剪贴板响应超时，请重试。", UNAVAILABLE: "远程剪贴板暂时不可用。", INVALID_REQUEST: "请求无效。",
};
type ClipManual = { mode: "paste" | "copy"; text: string } | null;
function localTextProblem(text: string) {
  if (!text) return "EMPTY";
  if (!text.isWellFormed()) return "INVALID_UTF8";
  for (const ch of text) { const o = ch.codePointAt(0)!; if ((o < 0x20 && o !== 9 && o !== 10 && o !== 13) || (o >= 0x7f && o <= 0x9f)) return "CONTROL_CHARACTERS"; }
  return new TextEncoder().encode(text).length > CLIP_MAX ? "TOO_LARGE" : null;
}
export function BasicGuiApp({ win }: { win: Win }) {
  const sidRef = useRef("");
  const [clipBusy, setClipBusy] = useState(false);
  const [clipNotice, setClipNotice] = useState<{ code: string; text: string } | null>(null);
  const [clipManual, setClipManual] = useState<ClipManual>(null);
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
    let progressAbort: AbortController | undefined;
    async function progress() {
      if (disposed || finished || polling || !sid) return;
      polling = true;
      const abort = new AbortController(); progressAbort = abort;
      const timeout = setTimeout(() => abort.abort(), 3000);
      try {
        const response = await fetch("/api/gui/status?sid=" + sid, { cache: "no-store", signal: abort.signal });
        if (response.ok) {
          const data = await response.json();
          if (disposed) return;
          if (data.state === "waiting_resources") setStage(data.reason === "telemetry_unavailable" ? "资源状态暂不可用，请稍后重试" : "正在等待服务器资源，请关闭暂时不用的应用后重试");
          else if (data.state === "stopping") setStage("图形环境正在回收，请稍后重试");
          else if (data.stage && labels[data.stage]) setStage(labels[data.stage]);
        }
      } catch { /* WebSocket is authoritative; no automatic reconnect loop. */ }
      finally { clearTimeout(timeout); polling = false; progressAbort = undefined; }
      if (!disposed) setElapsed(Math.floor((performance.now() - started) / 1000));
    }
    const progressTimer = setInterval(() => { if (!disposed && !finished) setElapsed(Math.floor((performance.now() - started) / 1000)); void progress(); }, 1000);
    void (async () => {
      try {
        const path = "/novnc/core/rfb.js";
        const module = await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ path);
        if (disposed || !host.current) return;
        sid = Array.from(crypto.getRandomValues(new Uint8Array(12)), value => value.toString(16).padStart(2, "0")).join("");
        sidRef.current = sid;
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const box = host.current.getBoundingClientRect();
        const { width, height } = module.boundedDesktopSize(box.width, box.height);
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
    return () => { disposed = true; sidRef.current = ""; clearInterval(progressTimer); progressAbort?.abort(); rfb?.disconnect(); };
  }, [attempt, allowed, browser, win.id]);
  // Session ended, closed or replaced: drop any temporary clipboard text.
  useEffect(() => { if (state !== "open") { setClipManual(null); setClipNotice(null); } }, [state]);
  async function remoteClipboard(op: "read" | "write", text?: string) {
    const sid = sidRef.current;
    if (!sid || state !== "open") return { code: "GUI_INACTIVE" } as { code: string; text?: string };
    try {
      const response = await fetch("/api/gui/text-clipboard", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(op === "write" ? { sid, op, text } : { sid, op }) });
      const data = await response.json().catch(() => ({}));
      return { code: typeof data.code === "string" ? data.code : "UNAVAILABLE", text: typeof data.text === "string" ? data.text : undefined };
    } catch { return { code: "UNAVAILABLE" }; }
  }
  async function sendToRemote(text: string) {
    const problem = localTextProblem(text);
    if (problem) { setClipNotice({ code: problem, text: problem === "EMPTY" ? "没有可粘贴的文字。" : clipMessages[problem] }); return; }
    setClipBusy(true);
    try {
      const result = await remoteClipboard("write", text);
      setClipNotice(result.code === "OK" ? { code: "OK", text: "已写入远程剪贴板（" + new TextEncoder().encode(text).length + " 字节），请在远程程序中按 Ctrl+V 粘贴。" } : { code: result.code, text: clipMessages[result.code] ?? clipMessages.UNAVAILABLE });
      if (result.code === "OK") setClipManual(null);
    } finally { setClipBusy(false); }
  }
  async function pasteToRemote() {
    setClipNotice(null);
    let text: string;
    try { text = await navigator.clipboard.readText(); }
    catch {
      setClipManual({ mode: "paste", text: "" });
      setClipNotice({ code: "LOCAL_DENIED", text: "浏览器未允许读取本机剪贴板。请在下方输入框中按 Ctrl+V 粘贴，再点击“发送到远程”。" });
      return;
    }
    await sendToRemote(text);
  }
  async function copyFromRemote() {
    setClipNotice(null); setClipManual(null); setClipBusy(true);
    try {
      const result = await remoteClipboard("read");
      if (result.code !== "OK" || result.text === undefined) { setClipNotice({ code: result.code, text: clipMessages[result.code] ?? clipMessages.UNAVAILABLE }); return; }
      try {
        await navigator.clipboard.writeText(result.text);
        setClipNotice({ code: "OK", text: "已复制远程文字到本机剪贴板（" + Array.from(result.text).length + " 字）。" });
      } catch {
        setClipManual({ mode: "copy", text: result.text });
        setClipNotice({ code: "LOCAL_DENIED", text: "浏览器未允许写入本机剪贴板。下方文字已选中，请按 Ctrl+C 复制后关闭。" });
      }
    } finally { setClipBusy(false); }
  }
  return <div data-testid="basic-gui" data-status={state} data-first-frame-ms={firstFrameMs ?? ""} className="flex h-full flex-col bg-slate-950 text-slate-200">
    <div className="flex items-center gap-2 px-3 py-1 text-xs text-slate-400">
      <span className="min-w-0 flex-1 truncate">{browser ? "Chromium · 可访问外部网站" : "PCManFM · 访客图形环境"} · 剪贴板仅限手动纯文字 · 未开放拖拽</span>
      <button data-testid="gui-clip-paste" className="rounded bg-slate-800 px-2 py-0.5 text-slate-200 disabled:opacity-40" disabled={state !== "open" || clipBusy} title="读取本机剪贴板中的纯文字并写入当前远程图形会话" onClick={() => void pasteToRemote()}>粘贴文字到远程</button>
      <button data-testid="gui-clip-copy" className="rounded bg-slate-800 px-2 py-0.5 text-slate-200 disabled:opacity-40" disabled={state !== "open" || clipBusy} title="读取当前远程图形会话剪贴板中的纯文字并复制到本机" onClick={() => void copyFromRemote()}>从远程复制文字</button>
    </div>
    {state === "open" && (clipNotice || clipManual) && <div data-testid="gui-clip-panel" className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-xs text-slate-200">
      {clipNotice && <div data-testid="gui-clip-notice" data-code={clipNotice.code}>{clipNotice.text}</div>}
      {clipManual && <div className="mt-2 flex items-start gap-2">
        <textarea data-testid="gui-clip-manual" className="h-20 min-w-0 flex-1 rounded bg-slate-950 p-1 text-slate-100" value={clipManual.text} readOnly={clipManual.mode === "copy"} autoFocus
          onFocus={event => { if (clipManual.mode === "copy") event.currentTarget.select(); }}
          onChange={event => setClipManual(current => current && current.mode === "paste" ? { mode: "paste", text: event.target.value } : current)} />
        {clipManual.mode === "paste" && <button data-testid="gui-clip-send" className="rounded bg-sky-700 px-2 py-1 disabled:opacity-40" disabled={clipBusy} onClick={() => void sendToRemote(clipManual.text)}>发送到远程</button>}
        <button data-testid="gui-clip-close" className="rounded bg-slate-700 px-2 py-1" onClick={() => { setClipManual(null); setClipNotice(null); }}>关闭</button>
      </div>}
    </div>}
    <div ref={host} className="relative min-h-0 flex-1 overflow-hidden" />
    {(!allowed || state !== "open") && <div className="absolute inset-10 grid place-items-center bg-slate-950/95 text-center">
      <div>{!allowed ? "此阶段仅开放固定的 PCManFM，暂不支持其他启动命令。" : state === "connecting" ? stage + "（已等待 " + elapsed + " 秒）" : "图形会话已结束、启动超时或不可用。" + stage}
        {allowed && state === "closed" && <button className="ml-3 rounded bg-sky-700 px-3 py-2" onClick={() => setAttempt(value => value + 1)}>重新打开</button>}
      </div>
    </div>}
  </div>;
}
