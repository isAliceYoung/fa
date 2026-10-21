"use client";
import { useResources } from "../resource-status";
import { fmtSize } from "./sysfiles-app";

export function MonitorApp() {
  const data = useResources();
  const memory = data.memory, cpu = data.cpu, swap = data.swap;
  return <div className="h-full overflow-auto bg-slate-950 p-4 text-sm text-slate-200" data-testid="resource-monitor">
    <p role="status" className="mb-4 rounded bg-white/5 p-3">{data.status.message}</p>
    {data.available && memory && cpu && swap ? <>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 tabular-nums">
        <dt>有效内存上限</dt><dd>{fmtSize(memory.effectiveLimit)}</dd>
        <dt>主机可用内存</dt><dd>{fmtSize(memory.hostAvailable)}</dd>
        <dt>WebDesktop 内存 / 保护上限</dt><dd>{fmtSize(memory.current)} / {fmtSize(memory.max)}</dd>
        <dt>访客执行内存 / 共享上限</dt><dd>{fmtSize(memory.executionCurrent)} / {fmtSize(memory.executionMax)}</dd>
        <dt>内存等待 PSI some / full</dt><dd>{memory.psi.some?.avg10.toFixed(1) ?? "—"}% / {memory.psi.full?.avg10.toFixed(1) ?? "—"}%</dd>
        <dt>有效 CPU 核数 / 当前使用</dt><dd>{cpu.effectiveCores} / {cpu.usagePercent.toFixed(1)}%</dd>
        <dt>CPU 等待 / 配额节流</dt><dd>{cpu.psi.some?.avg10.toFixed(1) ?? "—"}% / {cpu.throttledPercent.toFixed(1)}%</dd>
        <dt>主机 Swap 使用 / 有效额度</dt><dd>{fmtSize(swap.used)} / {fmtSize(swap.total)}</dd>
        <dt>WebDesktop Swap 使用 / 限额</dt><dd>{fmtSize(swap.workloadCurrent)} / {fmtSize(swap.workloadLimit)}</dd>
        <dt>本采样间隔 OOM / OOM kill 增量</dt><dd>{memory.eventsDelta.oom ?? 0} / {memory.eventsDelta.oom_kill ?? 0}</dd>
        <dt>父级本地 / 子树硬限额事件增量</dt><dd>{memory.eventsLocalDelta.max ?? 0} / {memory.eventsDelta.max ?? 0}</dd>
        <dt>访客任务数</dt><dd>{data.tasks?.execution ?? "—"}</dd>
        {Object.entries(data.disk ?? {}).map(([key, disk]) => <div key={key} className="col-span-2 mt-2 rounded bg-white/5 p-2">
          {key === "workspace" ? "访客工作区" : "系统磁盘"}：剩余 {fmtSize(disk.available)} / {fmtSize(disk.total)}；可用 inode {disk.freeInodes}
        </div>)}
      </dl>
      <p className="mt-4 text-xs leading-5 text-slate-400">每 5 秒共享采样一次。等待和节流按最近采样计算；内存包含可回收缓存。这里只显示聚合状态，未开放服务器进程列表或进程控制。</p>
    </> : <p>监测数据暂不可用，请稍后重试。不会将缺失数据判定为资源正常。</p>}
  </div>;
}
