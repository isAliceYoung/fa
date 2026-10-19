"use client";

// 当前页面使用内存会话；只为同一标签页的刷新保留一次性、两分钟恢复记录。
// Cookie 是 HttpOnly，另加此页面独有的标识可防止同浏览器其他标签偷用新 Cookie。
let currentSession = "";
const REFRESH_KEY = "wd.same-tab-refresh";

export function clearRefreshSession() {
  try { sessionStorage.removeItem(REFRESH_KEY); } catch { /* Optional reload recovery. */ }
}

export function takeRefreshSession(): string | null {
  try {
    const raw = sessionStorage.getItem(REFRESH_KEY);
    clearRefreshSession();
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (!raw || navigation?.type !== "reload") return null;
    const saved = JSON.parse(raw);
    return saved.context === window.name && saved.context?.startsWith("wd-tab-") && typeof saved.sid === "string" && saved.sid.length <= 128 && saved.until > Date.now() && saved.until <= Date.now() + 120000 ? saved.sid : null;
  } catch { return null; }
}

export function getDesktopSessionId() {
  return currentSession;
}

export function withDesktopAccess(url: string) {
  if (!currentSession) return url;
  return `${url}${url.includes("?") ? "&" : "?"}access=${encodeURIComponent(currentSession)}`;
}

export function installDesktopSession(sessionId: string, onInvalid: () => void) {
  currentSession = sessionId;
  clearRefreshSession();
  if (!window.name.startsWith("wd-tab-")) window.name = "wd-tab-" + crypto.randomUUID();
  const saveRefresh = () => {
    try { sessionStorage.setItem(REFRESH_KEY, JSON.stringify({ sid: sessionId, context: window.name, until: Date.now() + 120000 })); } catch { /* Login remains usable without storage. */ }
  };
  window.addEventListener("pagehide", saveRefresh);
  const nativeFetch = window.fetch;
  let active = true;
  let fired = false;

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, window.location.href);
    const protectedApi =
      url.origin === window.location.origin &&
      url.pathname.startsWith("/api/") &&
      !url.pathname.startsWith("/api/ops/") &&
      !url.pathname.startsWith("/api/auth/login") &&
      !url.pathname.startsWith("/api/health");

    let options = init;
    if (protectedApi) {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      headers.set("X-Desktop-Session", sessionId);
      options = { ...init, headers, cache: init?.cache ?? "no-store" };
    }

    const response = await nativeFetch(input, options);
    // 修复：只有明确的 401 才踢人，503 等服务器临时错误不应踢
    if (active && !fired && protectedApi && response.status === 401) {
      fired = true;
      onInvalid();
    }
    return response;
  };

  let channel: BroadcastChannel | null = null;
  if (typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel("webdesktop-exclusive-session");
    channel.onmessage = (event: MessageEvent<{ sessionId?: string; ts?: number }>) => {
      // 保证只有真正的新登录才踢人：如果收到的是自己刚发的消息（时间戳接近）不处理
      if (active && event.data?.sessionId && event.data.sessionId !== sessionId && !fired) {
        fired = true;
        onInvalid();
      }
    };
    // 发送时带时间戳，避免陈旧消息误触发
    channel.postMessage({ sessionId, ts: Date.now() });
  }

  return () => {
    window.removeEventListener("pagehide", saveRefresh);
    active = false;
    if (window.fetch !== nativeFetch) window.fetch = nativeFetch;
    channel?.close();
    if (currentSession === sessionId) currentSession = "";
  };
}
