"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { useDialogs } from "./dialogs";
import { useWm } from "./wm-store";

type Psi = { some?: { avg10: number }; full?: { avg10: number } };
export type Resources = {
  available: boolean; at?: number;
  status: { level: "normal" | "warning" | "critical" | "unknown"; message: string; notify?: boolean; reasons?: string[] };
  memory?: { total: number; available: number; hostAvailable: number; effectiveLimit: number; current: number; max: number; executionCurrent: number; executionMax: number; psi: Psi; eventsDelta: Record<string, number>; eventsLocalDelta: Record<string, number> };
  cpu?: { effectiveCores: number; usagePercent: number; throttledPercent: number; psi: Psi };
  swap?: { total: number; used: number; workloadCurrent: number; growth: number };
  disk?: Record<string, { total: number; available: number; freeInodes: number }>;
  tasks?: { workload: number; execution: number };
};
const unknown: Resources = { available: false, status: { level: "unknown", message: "暂时无法获取服务器资源状态" } };
let current = unknown;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let inFlight: AbortController | null = null;
async function poll() {
  if (document.hidden || inFlight) return;
  const controller = new AbortController(); inFlight = controller;
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch("/api/resources", { cache: "no-store", signal: controller.signal });
    const data = await response.json();
    current = (response.status === 200 || response.status === 503) && data?.status?.level ? data : unknown;
  } catch { current = unknown; }
  finally { clearTimeout(timeout); inFlight = null; for (const notify of listeners) notify(); }
}
function subscribe(notify: () => void) {
  listeners.add(notify);
  if (listeners.size === 1) { void poll(); timer = setInterval(() => void poll(), 5000); }
  return () => { listeners.delete(notify); if (!listeners.size) { clearInterval(timer); timer = undefined; inFlight?.abort(); current = unknown; } };
}
export function useResources() { return useSyncExternalStore(subscribe, () => current, () => unknown); }
export function ResourceIndicator() {
  const data = useResources(), wm = useWm(), dialog = useDialogs(), shown = useRef<number | undefined>(undefined);
  const level = data.status.level;
  useEffect(() => { if (data.status.notify && data.at !== shown.current) { shown.current = data.at; dialog.toast(data.status.message); } }, [data.at, data.status.notify, data.status.message, dialog]);
  const text = { normal: "资源正常", warning: "资源紧张", critical: "资源压力严重", unknown: "资源状态暂不可用" }[level];
  return <button data-testid="resource-status" data-level={level} onClick={() => wm.open("monitor")} title={data.status.message} aria-label={text}
    className={`rounded px-2 py-1 text-xs ${level === "critical" ? "bg-red-950 text-red-200" : level === "warning" ? "bg-amber-950 text-amber-200" : "text-slate-300 hover:bg-white/10"}`}>
    <span aria-hidden="true">{level === "normal" ? "●" : level === "unknown" ? "?" : "⚠"}</span><span className="ml-1 hidden md:inline">{text}</span>
  </button>;
}
