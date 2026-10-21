# 自适应资源与 GUI 运行约束

适用范围：新建 Debian 12 staging 安装。`scripts/resource_policy.py` 是唯一预算计算入口；旧 lab 模板保留有限的参考上限，普通稳定安装器不接入这轮实验。不会自动调参、升级已有安装、扩容旧文件卷或更改生产服务器。

## 计算与权限

安装时读取 MemTotal、CPU affinity、全部可见 cgroup 祖先的 memory.max、memory.swap.max、cpu.max 与 cpuset，以及实际 Swap、系统盘剩余空间。有效容量取最严格上限；不足 900 MiB RAM 或 0.5 CPU 拒绝。不是根据一次 MemAvailable 的波动分配额度。虚拟机标称 RAM 与 MemTotal 不相等，实际值以 `/etc/webdesktop-resources/policy.json` 为准。

RAM 按 MiB 计算，ceil32 表示向上取整到 32 MiB：OS 余量为 min(2048,max(192,ceil32(RAM×0.12)))；控制层余量在 RAM≤1152 时为 256，否则为 min(1536,max(256,ceil32(RAM×0.16+128)))。平台总上限=RAM−OS 余量；用户执行共享上限=平台总上限−控制层余量。后者至少 480 MiB。控制层仍有独立有限硬上限，MemoryLow 是回收保护而非物理内存预留。

| 理论有效 RAM / CPU | 平台 RAM | 执行共享 RAM / Chromium 单元上限 | 基础 GUI | 单 PTY | 平台 Swap / 执行 Swap（宿主有 1024 MiB） | 平台 / 执行 CPU |
|---|---:|---:|---:|---:|---:|---:|
| 960 MiB / 1 | 768 | 512 | 192 | 160 | 640 / 256 | 95% / 85% |
| 1024 MiB / 1 | 832 | 576 | 192 | 192 | 640 / 288 | 95% / 85% |
| 2048 MiB / 2 | 1792 | 1312 | 256 | 416 | 896 / 656 | 190% / 170% |
| 4096 MiB / 4 | 3584 | 2784 | 384 | 864 | 1024 / 1024 | 380% / 340% |

CPU 百分比以一个 CPU 为 100%。父组与子组限额重叠，不能相加当成预留；Chromium 可借用空闲执行预算，但不能越过共享执行树。后台、文件 Agent、PostgreSQL 留在 `webdesktop.slice`；用户 PTY、GUI、浏览器在 `webdesktop-exec.slice` 的子树。可信租约守卫不在用户执行树，原有有限内存与任务上限保留。不同 UID、DAC、固定 IPC、网络隔离、NoNewPrivileges、能力集与源码保护不变。

仅执行父组设置 MemoryHigh=共享 RAM 的约 96%；控制父组 MemoryHigh=infinity，仍有 MemoryMax。后台/PG/Agent CPUWeight=1000，并配置 MemoryLow 与较低 OOMScoreAdjust；用户执行层更优先承受 OOM。该设计降低风险，不承诺任何负载下认证永不延迟。Swap 总父上限=min(有效 Swap,max(640,平台 RAM/2))；执行 Swap=min(父 Swap,max(256,执行 RAM/2))。无 Swap 时全部为零，主机 1 GiB Swap 不代表各服务均可独立使用 1 GiB。

新访客卷按 round(RAM/1024)×512 MiB 增长，下限512、上限16384，并受安装依赖后剩余磁盘约束：不足12 GiB空闲时保留3 GiB，否则保留4 GiB，再按256 MiB取整限制卷大小。无法容纳512 MiB则失败，保留状态和日志。2/4 GiB 资源矩阵分别使用20/30 GiB盘验证更大卷；不据此宣称10 GiB能容纳任意更大卷。初始预检仍要求6 GiB空闲，构建在CI完成。

安装参数 `--workspace-mib`、`--execution-memory-mib`、`--browser-memory-mib` 只能在安全计算上限内降低，不得借覆盖提高权限或消除硬上限。硬件升级后的既有安装不自动改变预算；后续受审查的维护流程才可调整，当前不提供原地迁移。

只读查看新机器计划：`sudo bash /root/fa/webdesktop-deploy/install-staging.sh --resource-plan`。安装后查看非敏感预算：`sudo cat /etc/webdesktop-resources/policy.json`。计划基于当时依赖安装前磁盘状态，最终安装在依赖安装后重新计算并保存。所有手工命令必须单行。

## 采样和告警

`GET /api/resources` 必须通过桌面 Cookie 与页面 session 双重认证；不接受路径/进程参数。固定读取 meminfo、memory/cpu/io PSI、指定平台/执行 cgroup、系统盘/访客卷 statfs。响应仅包含聚合指标，禁止命令行、环境、进程列表、任意 kill 和敏感路径。单进程后台每5秒采样、单飞缓存、固定文件字节上限；接口并发上限4、每秒12次；任务栏与 Monitor 共享一个客户端轮询，后台页面暂停。接口异常503且状态 unknown，不回退旧进程 API。

使用 MemAvailable、PSI、CPU 使用/节流增量、Swap 增长、卷空闲字节/inode、memory.events 增量。区分 local 与包含后代的事件；计数重置不产生负数或虚假 OOM，累计历史 high 不触发新告警。首轮导数尚未形成显示“正在采样”，缺失/异常不显示正常。

持续10秒才升级常规告警，恢复正常需连续30秒，同级通知冷却120秒；新 OOM 立即严重并记住30秒。示例：内存 PSI some≥5/full≥2 为警告、full≥8或有效可用内存<4%为严重；CPU PSI some≥40且使用≥85%，或节流≥20%为警告；I/O PSI some≥15/full≥5 为警告。Swap 高占用必须伴随增长或内存压力，不因旧冷页常驻单独报警。系统盘<256 MiB、访客卷<32 MiB或inode<64严重；一般低水位先警告。具体阈值和原因字段见采样模块测试。

新 GUI 启动在真实严重压力/遥测不可用时明确拒绝，提示稍后重试；不自动杀现有工作，不循环自动重开。任务栏显示正常/警告/严重/未知，Monitor 展示安全聚合值。受限单元发生故意 OOM 不等于后台 OOM，测试分别记录。

## GUI 启动与显示

固定阶段包含认证、资源检查、租约、GUI worker、X11、私有 D-Bus、IME、Openbox、原生窗口、WebSocket。前端额外记录首个真实像素矩形完成 display flush 的时间；RFB 握手数据/首批 payload 不是浏览器首帧。这些记录不包含 Cookie、密码、完整环境或任意命令。原启动期限不延长；GUI 长时间/网页刷新/撤销后的回收规则不变。

原生窗口准备使用受限等待，避免反复派生高频探测进程；PCManFM 默认由 Openbox 最大化。初始与后续尺寸复用同一函数，保持宽高比并限制640×480至1600×1000；前端300ms防抖，独立 RFB 解析器再校验200ms速率、单屏、分片与像素格式。小窗口仍需缩放，大屏幕不无限扩 framebuffer；缩放不重启 worker。剪贴板消息继续被丢弃，VNC网页桥接仍未迁移。

真实低配 CI 捕获过 Chromium 进程仍存活、但 xdotool 遍历已销毁窗口时发生 `BadWindow / X_QueryTree` 或 `X_GetWindowAttributes`，导致启动误判。只对这两种固定查询的BadWindow最多重试两次，共用原4秒总期限；不重试其他错误、进程退出或超时。启动阶段、停止原因及子进程异常仅向受保护 journal 输出有界诊断，不包含登录凭据或完整环境。该证据解释本轮偶发失败，不能据此认定用户此前两分钟等待的原因。

网页文件复制/剪切仅更新内部文件剪贴板，不调用禁用的 VNC 桥接。文件导航只呈现允许的访客目录，服务端 Linux DAC/openat2 仍是权限边界。Chromium 初始页用原生 `chrome://newtab/`；Google 使用 recommended policy，允许用户选择覆盖。保留 profile、书签与安全沙箱；无 CDP、任意启动命令或 `--no-sandbox`。

## 验证边界

常规测试验证公式、祖先限额、协议和告警状态机；真实 Debian VM 使用不同 UID、systemd/cgroup、真实 Bash/Chromium、磁盘/CPU/tasks/OOM 故障和恢复，并检查控制链延迟。固定 f195f90 基线独立构建，用于同规格冷/热启动对照；不能把旧版本不存在的新指标写成通过。真实 Windows 浏览器和用户 VPS 的更新后复验仍须用户授权，不能用 CI 取代。

原始 JSON、截图、journal、resource counters 放 CI Artifact，报告仅记录固定 run/SHA 和结论；历史失败不删除。参考：[systemd 251 resource control](https://www.freedesktop.org/software/systemd/man/251/systemd.resource-control.html)、[内核 cgroup v2](https://www.kernel.org/doc/html/v6.6/admin-guide/cgroup-v2.html)、[TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html)。
