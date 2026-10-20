# 3C：可见 Chromium、输入法与低配资源研究

本轮开发与验证完成，停在审查点，未继续全面 GUI 迁移或生产安装。基线 `d3e7d0c`，仅 `feature/private-deploy-isolation`；代码/测试验收快照为 `9a1518d2692af1b6356c44ad9c284f43f1957ef1`，不代替用户阶段验收。历史 [基础 GUI 报告](PHASE3C_GUI_REPORT.md) 与其 Claude 320 MiB/无 Swap OOM 记录保持不变。

## 产品与安全设计

先提交 [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)：一 VPS 一用户，独立数据库、密码、数据与未来 HTTPS 子域名。普通工作目录可以共享，Agent 事务状态、租约守卫和服务端敏感权限仍各自隔离。未变更生产 VPS、main、Beta、仓库可见性、生产 Deploy Key 或旧数据库/用户文件。

基础 GUI 保留独立 wdgui UID；新增联网 Chromium 也属于该用户的数据域，但使用独立 systemd 服务及网络 namespace。PCManFM/Xvnc/Openbox/Fcitx 私有显示服务继续 `PrivateNetwork=yes`、`RestrictAddressFamilies=AF_UNIX`；没有为了联网取消整个 GUI 服务的限制。

实测 VM 的账号为 wdsvc **UID 999 / GID 994**、wdguest **994 / 993**、wdterm **993 / 992**、wdgui **992 / 991**。UID 与 GID 是不同编号空间，不能混为同一身份。代码按账号查询，不把这些数字作为跨 VPS 常量。GUI 运行主 GID 为非敏感数据组 wdguest 993，额外使用自身图形组 wdgui 991；没有 wdsvc 994、sudo 或 postgres 组。普通工作目录允许共享，`.gui` 0700、Agent `.wd-state` 0700 和服务秘密目录分别保留私有权限。

| 入口/资源 | 设计 |
|---|---|
| 公共入口 | `/api/gui/ws?app=chromium`，固定应用，Cookie + 页面 session + 固定 Host/Origin 验证 |
| 后端到浏览器 | `/run/webdesktop-browser/session-1.sock`，父目录 root:wdsvc 0750，Socket root:wdsvc 0660，SO_PEERCRED 只接受 wdsvc |
| 浏览器协议 | 只有一条 `{t:"open"}` 与 ready 响应；不接受 URL、命令、UID、cwd、env、CDP 请求 |
| 浏览器运行文件 | root 管理的最小 Python 标准库 worker、网络脚本、nft 策略、DNS 与版本标记；不含后端源码/认证/DB 模块/凭据 |
| 私有显示挂载 | root 管理 8 MiB tmpfs；固定 ipc 子目录 wdgui 0700、X11 子目录 root:wdgui 1770，只绑定这两个路径，不共享宿主整个 /run 或 /tmp |
| 显示凭据 | 独立 Xauthority 0600，显示 :100、私有 D-Bus；root 管理的 D-Bus 配置不包含全局自动激活目录 |
| 网络 | root 创建 veth/network namespace/nft；仅允许外部 TCP 80/443 与 DNS，拒绝全部宿主入站、私网/link-local/元数据及 IPv6；没有 guest CAP_NET_ADMIN |
| 原生浏览器沙箱 | 启用 user/pid namespace 与 renderer 额外 seccomp；没有 --no-sandbox、headless 或调试端口 |
| 生命周期 | 浏览器 BindsTo 基础 GUI，固定 IPC 断开/进程崩溃/认证撤销后整 cgroup 回收；未确认停止前不复用槽位 |

Chromium 建立沙箱时可能短暂在只映射 wdgui 的独立 user namespace 内持有该 namespace 的完整能力，之后只保留局部 SYS_ADMIN 或继续丢弃。测试必须区分局部能力与宿主能力：主进程和 renderer 要求 CapEff=0；仅接受 zygote、非宿主 namespace、wdgui 单 UID 映射和限定能力掩码。另启动恶意 namespace root，在**相同宿主挂载 namespace** 中尝试读写五个真实敏感文件、连接三个服务 Socket、对 wdsvc 进程发送信号权限探测，要求全部被内核拒绝。没有放宽 systemd 的宿主能力集。

Linux 的能力作用域与文件 UID/GID 映射规则见 [user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)；本轮仍以实际 DAC 攻击测试作为证据，不用文档推断代替测试。

GUI Upgrade 的 AbortSignal 贯穿准备过程；预握手断线、晚到的资源和停止竞态统一回收。清理等待 prepare 完结、systemd stop 成功及独立 guard 退出后才释放槽位。认证续验与输出背压分离；数据库异常停止接收授权输入，不续租，无 wdsvc 执行回退。

GUI 与 guard 去除固定 1 小时 RuntimeMaxSec，保留 10 秒单调时钟租约和 5 秒续验。**配置允许长时间使用，不表示已经完成整日 soak 验收**。本轮目录/缩略图压力观察 5 分钟。刷新结束旧 GUI，新建认证会话；profile 持久化，原窗口未保存状态不恢复。PTY 已通过的短时重连保持。

## 中文输入

固定访客环境设置 GTK_IM_MODULE、QT_IM_MODULE、XMODIFIERS，首次创建拼音/英语 profile，不覆盖现有用户配置。Fcitx 由 worker 显式监督，私有 D-Bus 不竞争自动启动；异常重启有 3 次/60 秒上限。没有接入管理员 X11/D-Bus。

测试通过 RFB/noVNC 真正输入 `nihao`，捕获候选框、数字 1 选词、中英文切换及实际控件文本；覆盖 GTK、Qt、XIM、跨窗口焦点、Fcitx 重启和 GUI 结束回收。浏览器另经真实桌面页面验证输入。剪贴板取证由 root 测试工具读取**访客内部 X11 selection**，并不开放网页/宿主剪贴板功能。

## 资源预算

目标与对照均为真实可丢弃 Debian 12 QEMU VM、1 vCPU、1024 MiB RAM、10 GiB 系统盘、原 512 MiB 访客卷。目标主机配置约 1024 MiB Swap，对照 0；构建在 CI 宿主完成，运行数据不能冒称本机 1 GiB 构建验收。原安装器 Swap 自动配置未修改。

下面是本轮**实验环境**聚合预算，尚未迁入生产安装器。各子上限有重叠，不是独占预留；由父 slice 限制真实总和。

| 单元 | RAM 上限 | MemorySwapMax | 其他 |
|---|---:|---:|---|
| 实验总 webdesktop.slice | 768 MiB，High 672 | 640 MiB | CPU 90%，320 tasks；以实际约 973 MiB MemTotal 留出约 205 MiB 给 OS/SSH/独立守卫 |
| Next | 384 MiB | 128 MiB | 不变更执行身份/源码权限 |
| PostgreSQL 实验实例 | 128 MiB，High 96 | 64 MiB | 仅全新实验 DB，不能外推大数据业务容量 |
| 文件 Agent | 96 MiB | 32 MiB | 保留原文件实现与事务协议 |
| 全部 PTY | 192 MiB | 128 MiB | 原双槽位预算保持 |
| 全部 GUI | 512 MiB，High 448 | 256 MiB | CPU 80%，192 tasks |
| 基础 GUI | 192 MiB | 64 MiB | 64 tasks |
| 可见 Chromium | 320 MiB | 192 MiB | CPU 70%，128 tasks，512 FD |
| 独立可信守卫，每个 | 24 MiB | 0 | 不放入不可信工作负载父 slice，保护清理能力 |
| Claude/Chromium 单独研究 | 320/384/448 MiB 阶梯 | 192 MiB | 包含研究用 Xvnc/Openbox/Fcitx；Claude 160 tasks、Chromium 128；不与正式 GUI 同时运行 |

有主机 Swap 不等于每个服务可以用 1 GiB。无 Swap 对照中的 MemorySwapMax 只是配置上限，实际 swap.current 仍为 0。早期 Claude 128 tasks 观察到 pthread_create/EAGAIN，不能因窗口存活就计为可用；研究单元限定增加到 160，仍受总 320 tasks 约束，正式 Chromium 和其他服务任务预算不变。后续统计同时检查 pids.events 与线程耗尽日志，不仅检查 OOM。文件/上传/回收站/profile/cache 共用有界卷；浏览器 16 MiB 磁盘缓存参数不是卷配额。没有扩大或格式化原卷。

资源采集包括实际单元限制、memory.current/peak/swap.current/events/pressure、CPU、任务数、host meminfo/swaps、磁盘、关闭后进程、HTTP 响应和 X11 往返。X11 往返不是 Claude 页面 JS/登录操作的完整响应延迟，不能单凭它宣布可用。

这里的主机 RAM/Swap 指被测 Debian VPS VM，非 GitHub runner。宿主 UI Chrome 只做交互驱动，不计入 VPS 内存。独立 Chromium 资源研究使用仅在实验 network namespace 内的固定 CDP 作为 root 测量工具；正式可见 Chromium 入口没有 CDP，Claude 研究也未添加调试参数。不能把实验 CDP 工具装进访客运行包。

## 实际测试结果

最终代码批次 [9a1518d / CI 39418021685](https://github.com/isAliceYoung/fa/actions/runs/39418021685) 的 **8 项任务全部成功**。以下按执行环境分别记录，不把跨环境重复运行累加为唯一测试数量。所有列出的功能套件退出码为 0；资源控制器退出 0 的含义另列。

| 实际执行套件 | 通过 | 失败/错误 | 跳过 | 未执行测试 |
|---|---:|---:|---:|---:|
| Ubuntu 常规 Shell/Python | 151 | 0 | 0 | 0 |
| Debian 12 常规 Shell/Python | 151 | 0 | 0 | 0 |
| Debian 源码/测试 Git 密钥真实权限 | 9 | 0 | 0 | 0 |
| Debian 访客 UID/文件/IPC/Next/DB | 18 | 0 | 0 | 0 |
| Debian SSH 配置解析 | 3 | 0 | 0 | 0 |
| 真实 Next Upgrade、TLS 代理与认证 | 12 | 0 | 0 | 0 |
| 真实文件流、事务、ZIP、ENOSPC/恢复 | 27 | 0 | 0 | 0 |
| 终端 UID 对 Agent/状态/内部服务攻击 | 5 | 0 | 0 | 0 |
| 单独 Debian systemd 启动与重启 VM | 18 | 0 | 0 | 0 |
| 单独 Debian PTY/systemd/cgroup/终端 UI VM | 49 | 0 | 0 | 0 |
| GUI VM，无 Swap：基础 56 + 新运行时 11 + 桌面 UI 5 | 72 | 0 | 0 | 0 |
| GUI VM，约 1 GiB Swap：同上 | 72 | 0 | 0 | 0 |
| 无 Swap 资源研究：5 个短时实验 | 3 | **2** | 0 | 0 |
| 约 1 GiB Swap 资源研究：5 个短时实验 | 4 | **1** | 0 | 0 |

两组资源研究均完整采集，安全观察失败 0、全局 OOM 0，所有研究进程树停止后为空。**三项资源失败均为 Claude 单元 OOM，仍是失败**；研究控制器退出 0 仅表示采集完整且未发现不安全执行。不能将 CI 成功解释为 Claude 可正式使用。其他 Node 策略检查、Shell 语法检查、Python 编译检查、Next build/typecheck 也退出 0，未换算成上表的 Python 测试数。

真实 GUI VM 均完成 PID 1/systemd 启动与重启、UID/Socket/DAC、systemd-analyze verify/security、原生 renderer 沙箱与内核拒绝测试。每组基础 56 项由 systemd 18、PTY 前置 14、GUI 重启前后 20、基础桌面 UI 4 组成；8 个 SSH 身份拒绝探测另在日志记录，不重复计入 56。原始证据：[有 Swap Artifact 12262878639](https://github.com/isAliceYoung/fa/actions/runs/39418021685/artifacts/12262878639)、[无 Swap Artifact 12263618785](https://github.com/isAliceYoung/fa/actions/runs/39418021685/artifacts/12263618785)。

| 本轮图形验收项 | 两组 VM 结果/证据 |
|---|---|
| 可见 Chromium、外部 HTTPS 导航/键盘输入 | 通过；chromium-https-visible.png；正式入口无 CDP/headless/no-sandbox |
| 原生 namespace/seccomp、管理端口与内部 Socket 拒绝 | 通过；desktop-runtime.json、实际 /proc、网络连接与 namespace root DAC 探测 |
| Fcitx 拼音候选、数字选词、中英切换 | 通过；chromium-pinyin-candidates.png、chromium-chinese-input.png，实际文本为 `你好hello` |
| GTK/Qt/XIM、焦点切换、IME 重启/回收 | 通过；ime-gtk/qt/xim-candidates.png 与对应运行时记录 |
| 关闭/重复启动、书签 profile 持久化、刷新 | 通过；真实 noVNC 输入、Bookmarks 文件及 chromium-reopened.png；刷新会结束原窗口 |
| Upgrade 提前断线、背压撤销、worker 崩溃后槽位复用 | 通过；独立守卫、systemd MainPID 与 cgroup 清理断言 |
| 并发真实 PTY、文件请求、其他设备登录撤销 | 通过；HTTPS UI 与旧 PID+启动时间身份清理断言，合法新会话不算残留 |
| PCManFM 目录/缩略图压力 | 通过 300 秒；不声称整日运行已测 |

未执行的范围明确保留：正式域名/DNS/证书续期及 Nginx/Tunnel 上线、生产测试、第四阶段完整安装迁移、1 GiB VM 本机构建、Claude 账号登录后聊天/长期交互、Chromium 与 Claude 同时运行、整日 soak、长期 profile 增长和未来剪贴板/拖拽。它们不是跳过后计作通过的测试。此前测试夹具、监听时序与资源失败均保留于原 CI；没有删除失败记录。最新测试额外核对实际远端地址投递、书签落盘和旧进程内核启动时间，避免输入队列/新页面恢复造成错误结论。

最终收尾只修改本报告、HTTPS 文档、README 与准备说明；以 `[skip ci]` 提交，不重跑耗时 VM。该文档提交的 CI **未执行，不计通过**；上述八项结论只对应已完成的 `9a1518d`。

实际测试入口（Linux 权限/VM 命令只用于一次性测试环境）：

```bash
python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json
node tests/terminal_actions_checks.mjs
node tests/ws_origin_checks.mjs
node tests/http_boundary_checks.mjs
node tests/network_policy_checks.mjs
python3 tests/linux_private_repo.py
python3 tests/linux_guest_layer.py
python3 tests/linux_tunnel_parser.py
python3 tests/linux_ws_runtime.py
python3 tests/linux_files_runtime.py
python3 tests/linux_terminal_boundary.py
python3 tests/run_systemd_vm.py
python3 tests/run_systemd_vm.py --pty
python3 tests/run_systemd_vm.py --desktop --swap-mib=0
python3 tests/run_systemd_vm.py --desktop --swap-mib=1024
npm run build
npm run typecheck
```

VM 驱动运行前需按 `.github/workflows/validate.yml` 准备 QEMU/KVM、Node/宿主 Chrome，并取得同批次构建的 `.test-results/pty-backend.tar.gz`。驱动自己创建/销毁 Debian VM；上面的命令不是生产安装命令。运行包、UID/systemd/卷/网络配置均在 VM 内准备。宿主 UI 测试参数从 stdin 接收；只使用临时测试账号和测试密钥。

Windows 本地已实际执行 `python tests/run_tests.py --json .test-results/regular-3c-desktop.json`，Git Bash 下 151 通过、0 失败、0 错误、0 跳过，退出 0。该脚本列出的两个“未执行范围”为完整整机安装/GUI（另用 VM 组件验收）和未授权生产测试，不能当成两项通过测试。新增系统权限、Sandbox、图形交互与资源结论取自 Linux/VM，不能用 Windows 的 Shell 测试替代。

## 分阶段整机资源记录

资源基线取自完整成功的 [30e2f79 / CI 39413862702](https://github.com/isAliceYoung/fa/actions/runs/39413862702)，[有 Swap 原始数据](https://github.com/isAliceYoung/fa/actions/runs/39413862702/artifacts/12260654570)、[无 Swap 原始数据](https://github.com/isAliceYoung/fa/actions/runs/39413862702/artifacts/12261937052)。这里三项基线都在 **768 MiB 总 RAM / 640 MiB 总 Swap 上限实际生效后**测量，每项均启动真实 PTY、分配 24 MiB，并实际请求文件 Agent。数值是单次阶段观察，不是长期容量保证。

| 1 GiB RAM + 约 1 GiB Swap 阶段 | 工作负载父组 current MiB | 父组 swap.current MiB | 主机 MemAvailable MiB | GUI tasks | 父组 CPU 单核占比，约 1.5 秒窗 | 密码登录响应 ms |
|---|---:|---:|---:|---:|---:|---:|
| Next + PG + 文件 Agent + 活跃 PTY | 172.6 | 1.4 | 577.7 | 0 | 0.4% | 61.3 |
| 增加 Xvnc/Openbox/私有 D-Bus/Fcitx | 343.1 | 9.4 | 497.9 | 6 | 11.9% | 49.1 |
| 一个 PCManFM 窗口 | 359.2 | 9.4 | 498.2 | 9 | 11.0% | 50.1 |

无 Swap 三项相应父组 current 为 **168.0 / 340.8 / 370.7 MiB**，MemAvailable **578.7 / 490.6 / 477.9 MiB**，密码登录约 **37.4 / 33.1 / 30.4 ms**；所有实际 swap.current 为 0。CPU 观察窗口包含启动后的加载工作，不是长期空闲平均。

同批次可见 Chromium 的单次快照：GUI 聚合 current **447.5 MiB**、Swap **7.6 MiB**、85 tasks；整个工作负载父组 current **579.1 MiB**、Swap **35.4 MiB**，主机可用约 **433.1 MiB**。该快照用于原生进程/窗口检查；真实 UI 另保持 PTY 和文件请求并发，15 次文件列表响应 **12–20 ms**。应用和父组 memory.events、pressure、current/peak/Swap 全部保存在对应 JSON，不能把某个瞬时数当成全阶段峰值。

PCManFM 的 160 张有效 PNG / 刷新压力观察 **300 秒**后健康检查仍成功。关闭 GUI 后已检查进程树为空；slice 中可能保留可回收文件页。memory.current 包含这些页，memory.peak 为该 cgroup 本次 VM 的累计值，后一次快照不能当作全新测试的独立峰值；也不能把父组和子组再次相加。

安装 Chromium、Qt/GTK 测试工具、固定 Claude 研究包及 1 GiB Swap 后，10 GiB 系统盘可用约 **3.26 GiB**；512 MiB 原始访客卷的 ext4 实际可用容量小于标称值，研究结束使用约 **76.6 MiB**、剩余约 **404.4 MiB**。没有自动清理源码、数据库或用户数据来腾空间。尚需给未来源码构建临时文件、日志与升级保留空间；本轮构建在 CI 宿主执行，**不构成 1 GiB VPS 本机构建验收**。10 GiB 更适合严格受控的轻量运行，不能承诺长期双应用缓存与构建并存而无需维护。

## API 状态

| 入口 | 本轮状态 |
|---|---|
| `/api/gui/ws` | 固定 desktop/PCManFM；安装 root 浏览器运行包后增加固定 chromium；始终双认证/固定来源/有界 IPC |
| `/api/term/ws` | 保留真实 wdterm Bash/PTY，未恢复旧 wdsvc 执行库 |
| `/api/sys/fs`、`/api/sys/upload` | 保留已通过的 wdguest 文件/上传 API；没有重写文件模块 |
| `/api/sys/download`、`/api/sys/raw` | 保留 Agent 下载/ZIP/原始流；不成为 wdsvc 文件路径读取或图片转换回退 |
| `/api/sys/info?lite=1` | 保留有界访客统计 |
| `/api/gui`、`/api/gui/[sid]`、apps/icon/shortcut | 未迁移，认证后仍 503 |
| `/api/gui/clipboard`、`/api/gui/[sid]/clipboard`、`/api/gui/[sid]/drop` | 未迁移，认证后仍 503 |
| `/api/browser/ws`、旧 `/api/browser` 与 sid/input/frame/meta | 极速浏览器旧链路仍拒绝；不等同于新的 Chromium GUI |
| `/api/sys/kill`、完整进程监控 | 未开放后端特权进程控制 |

Claude 正式 app 枚举和任意 .desktop/cmd 启动均未加入。GUI 单槽位仍可能因已有图形窗口占用而拒绝新窗口；测试关闭的是前一用例保存的测试窗口，没有删除数据库或用户文件。新设备允许恢复自己的工作区，因此撤销验收比较旧进程的 PID + 内核启动时间，不把新认证会话的合法进程误判为旧进程残留。

## Claude 独立资源研究结果

**最终 `9a1518d` 批次**再次说明 320 MiB 不能作为 Claude 可用预算：即使主机有 1 GiB Swap，该单元使用满 192 MiB Swap 后仍 OOM。384 MiB 也用满单元 Swap，短时存活不构成稳定性承诺。下列峰值取该批次有效 cgroup 采样；全部未出现安全检查失败或整机 OOM。

| 主机 Swap | 应用 / RAM 上限 MiB | 采样 RAM 峰值 MiB | 单元 Swap 峰值 MiB | tasks 峰值 | HTTP / X11 p95 ms | 实验结果 |
|---|---|---:|---:|---:|---|---|
| 0 | Chromium 320 | 298.7 | 0 | 99 | 12.4 / 22.9 | 短时通过 |
| 0 | Chromium 384 | 294.2 | 0 | 96 | 11.3 / 22.3 | 短时通过 |
| 0 | Claude 320 | 320.1 | 0 | 90 | 6.8 / 未达采样阶段 | OOM，失败 |
| 0 | Claude 384 | 384.2 | 0 | 97 | 56.4 / 32.0 | OOM，失败 |
| 0 | Claude 448 | 448.4 | 0 | 120 | 32.1 / 41.1 | 短时通过 |
| 约 1024 MiB | Chromium 320 | 305.3 | 17.0 | 102 | 12.7 / 9.7 | 短时通过 |
| 约 1024 MiB | Chromium 384 | 254.7 | <0.1 | 104 | 8.9 / 6.6 | 短时通过 |
| 约 1024 MiB | Claude 320 | 320.5 | **192.0** | 126 | 9.7 / 未达采样阶段 | **OOM，失败** |
| 约 1024 MiB | Claude 384 | 384.3 | **192.0** | 146 | 14.7 / 8.6 | 短时通过 |
| 约 1024 MiB | Claude 448 | 448.0 | 116.3 | 130 | 11.0 / 9.1 | 短时通过 |

当前判断仍是：目标 VPS 可用于轻量 Chromium 的已测场景，**不足以保证 Claude 稳定日常使用**；正式 Claude 入口保持阻塞。最终批次无 Swap 448 曾存活，不能抹去前批次同档 OOM；矩阵不是各例独立冷启动，更不能据此宣布无 Swap 更好。进入下一次研究前应独立冷启动复测，并增加真实长期交互和页面性能指标。

以下固定数据来自 `d0612fe` 的 [Debian 对照批次 39407748661](https://github.com/isAliceYoung/fa/actions/runs/39407748661)，不是将整项失败 CI 改称成功：该批次的桌面 UI 用例因测试工作区旧窗口占用槽位而失败，下面两个独立资源矩阵已完整采集。原始 JSON、日志、进程 smaps、阶段截图、memory.events/PSI、systemd 与磁盘记录分别在 [无 Swap Artifact](https://github.com/isAliceYoung/fa/actions/runs/39407748661/artifacts/12258547542) 和 [1 GiB Swap Artifact](https://github.com/isAliceYoung/fa/actions/runs/39407748661/artifacts/12258263686)。后续运行重复验证不覆盖这批历史数值。

两组均为 1 vCPU、1024 MiB RAM、10 GiB 盘、原 512 MiB 访客卷，Next + PostgreSQL + 文件 Agent + 24 MiB 活跃 PTY 并发。单个研究单元包含 Xvnc/Openbox/Fcitx 与一个应用；320/384/448 是 **RAM** 上限，另有 **192 MiB MemorySwapMax**。每例最长观察 90 秒，实际点击原版 Claude 的 Get started，未填写账号或提交登录。

| 主机 Swap | 应用/单元 RAM 上限 | 观测 RAM 峰值 MiB | 单元 Swap 峰值 MiB | tasks 峰值 | HTTP / X11 p95 ms | 结果 |
|---|---|---:|---:|---:|---|---|
| 0 | Chromium 320 | 320.0 | 0 | 94 | 15.4 / 11.2 | 短时实验通过 |
| 0 | Chromium 384 | 274.9 | 0 | 98 | 9.8 / 11.5 | 短时实验通过 |
| 0 | Claude 320 | OOM 前采样，见原始 JSON | 0 | 99 | 13.8 / 未达采样阶段 | OOM，失败 |
| 0 | Claude 384 | 384.1 | 0 | 92 | 31.7 / 未达采样阶段 | OOM，失败 |
| 0 | Claude 448 | OOM 前采样，见原始 JSON | 0 | 148 | 10.4 / 16.5 | OOM，失败 |
| 约 1024 MiB | Chromium 320 | 311.1 | 13.2 | 94 | 17.4 / 23.4 | 短时实验通过 |
| 约 1024 MiB | Chromium 384 | 262.2 | <0.1 | 98 | 12.0 / 17.0 | 短时实验通过 |
| 约 1024 MiB | Claude 320 | 320.1 | **192.0** | 134 | 18.5 / 44.3 | 存活及登录窗通过，但明显内存压力 |
| 约 1024 MiB | Claude 384 | 384.1 | 169.6 | 133 | 11.0 / 26.6 | 短时登录窗实验通过 |
| 约 1024 MiB | Claude 448 | 448.0 | 102.7 | 131 | 13.4 / 19.5 | 短时登录窗实验通过 |

资源实验共 **7 项短时通过、3 项失败、0 跳过、0 未执行实验**；安全观察失败 0，无全局 OOM，十个研究单元停止后 cgroup 都没有残留进程。采集控制器退出 0 不代表无 Swap Claude 已通过，或 Claude 可以正式开放。OOM 后 cgroup 可能已经移除，最终 memory.peak/events 不一定还能读取；保留此前有效采样与 `Result=oom-kill` / `ExecMainStatus=9` 及内核 OOM 记录，不用 0 代替缺失计数。

有 Swap 的三例均见到原版 **Sign In** 页。320 档单元 Swap 已满，memory PSI `full avg10` 峰值 **26.04%**，即使 X11 往返很快也不能据此判为稳定可用。384/448 档该压力值为 **5.67% / 3.96%**；平均 CPU 约 **12.4% / 11.7%** 单核，320 档约 21.9%。主机总 Swap 使用峰值约 **323.1 MiB**，不同于单元上限 192 MiB。没有把剩余主机 Swap 当成该应用可随意使用的额度。

448 档的进程末次 PSS 观测为：Electron 主进程约 **124.1 MiB**，renderer 单进程约 **23.4–142.6 MiB**，辅助进程约 **0.2–42.7 MiB**。各 PID 的末次观测时间不同，不能把它们相加当作并发总量；总量以 cgroup 采样为准。登录窗口实验的整个 profile（含配置、数据与缓存）约 **13.4 MiB**，Chromium 研究 profile 约 **8.2 MiB**。这不是长期缓存增长结论，不能证明原卷足够保存长期 Claude/浏览器数据与文件事务。

448 档运行中的可用主机内存最低约 **224.8 MiB**；关闭两秒后工作负载父组 memory.current 约 **313.9 MiB**，应用进程树为空。残留的可回收文件页缓存不等于进程泄漏。320 档恰好存活是当前观测下限，**不作为推荐预算**；448 MiB RAM + 192 MiB 单元 Swap + 160 tasks 是更合理的后续研究起点，且不能直接套入现有双单元 GUI 后宣称正式集成完成。

结论：1 GiB + 1 GiB Swap 已证明轻量 Chromium 与 Claude 登录窗口的实验可行性；**尚不能承诺 Claude 聊天负载、长时间交互或与 Chromium 同时运行稳定**。正式 Claude 入口保持禁用。2 GiB RAM 是后续日常负载验证的候选配置，本轮没有实测 2 GiB，不能称为已测最低配置。

复跑存在冷热缓存和内存页记账差异：例如 `ff17c16` 的无 Swap 对照中，384 MiB 曾完成短时窗口，而 320/448 仍 OOM。矩阵顺序运行，文件页可能共享或在子 cgroup 消失后归入父组；父组总预算与整机 meminfo 继续记录这些内存，不能只看子单元推断严格、单调的物理 RAM 下限。这是保留多个批次、拒绝将一次存活称为长期稳定的原因；后续应以独立冷启动及真实账号负载再验收。

## HTTPS 准备

完整配置契约见 [HTTPS_ACCESS.md](HTTPS_ACCESS.md)。默认 127.0.0.1:3000、内部私有 Socket、显式 WD_PUBLIC_URL/WD_PUBLIC_ORIGINS；网关净化客户端转发/私有头，HTTPS Cookie 不信任客户端协议声明。条件退出撤销防止旧页面误撤销并发新登录。提供固定 Host 的 Nginx 模板；没有正式 DNS、证书/Tunnel 或生产部署验收。

## 尚未迁移与下一步

- Claude 正式桌面入口保持关闭，需独立实际登录流程、持续交互与资源/回收验收。
- 极速浏览器旧 WS、完整 GUI HTTP 启动器、任意桌面文件/应用命令、图标提取尚未迁移；不导入旧 wdsvc GUI 执行路径。
- 网页双向剪贴板、文件拖拽、桌面与浏览器上传下载桥、图片转换、完整 Fcitx 托盘设置仍待后续审查。现有安全文件上传下载 API 保持，不等于浏览器下载桥已经实现。
- 目前只有一个显示槽位，PCManFM 与浏览器入口按固定会话使用；完整共享桌面多应用窗口与刷新保留显示尚未完成。
- 512 MiB 卷未证明足够长期保存 Chromium + Claude profile、大文件暂存与回收站。需实际长期缓存增长、ENOSPC、inode 与子目录预算研究，不自动扩卷。
- 继续保持 IPv4-only、Swap 自动配置、PostgreSQL 首次失败恢复、禁用自动更新和取消欢迎/示例文件修改。第四阶段安装器/目录迁移与生产 VPS 不在本轮范围。

仓库可见性按要求保持不变；2026-10-20 实际查询仍为 Public。本阶段证明的是 VPS 内核对部署文件与凭据的访问隔离，不能阻止获取已经公开的 GitHub 源码副本。最终私有上线仍需另行授权并验证仓库访问控制，不能将本阶段实验当作最终私有生产部署验收。

下一次获准的 3C 建议先实现同一显示上的固定应用启动与短时刷新续接，再单独验收剪贴板/拖拽。续接需要拆分显示生命周期与单个 RFB 桥接连接：原 Xvnc/应用仅在有限离线宽限内存活，新视图重新进行 RFB 握手；不能回放任意旧协议字节假装恢复。宽限不免除后台认证续验，新设备登录立即撤销；数据库/租约失效、超时或不能确认停止仍回收整树。应用启动保持固定枚举、独立网络/资源单元，不接受任意命令。该设计未在本轮实施。

## 修改文件与提交

代码与测试提交如下；最终报告提交另计，测试对应 SHA 在结果段单独标明。

```text
9b2905d docs: define one-user-per-VPS HTTPS desktop and explicit swap budget constraints
abd33fc feat: add bounded visible Chromium plane and private IME display mounts with hardened auth lifecycle
f0950f4 test: run real graphical input and resource matrices on Debian with and without swap
c74cabd fix: preserve shell boundaries in network validation and verify refresh cleanup
d6f38a0 fix: use the same restricted DNS resolver for browser namespace diagnostics
3bd2ff0 fix: supervise private IME without competing D-Bus activation and canonicalize default HTTP ports
437f62f test: correct graphical TLS fixture and inspect namespace-scoped sandbox capabilities
eecd699 test: bound combined desktop resources and verify graphical crash recovery
a1fcb5b test: await completed HTTPS login and validate Chromium identity UID mapping
98f6541 test: attack source DAC as namespace root and exercise stock Claude login action
d0612fe test: reject thread exhaustion and reserve aggregate RAM for operating system
3bd3269 test: target Chromium window controls and await the actual visible application
0d3e513 test: await renderer sandbox readiness and close prior disposable workspace windows
ff17c16 fix: acknowledge Chromium startup only after its actual private X11 window is visible
efb0b7c test: respect bounded graphical input rate and log credential-free rejection reasons
30e2f79 test: measure all baseline desktop stages with actual PTY and per-unit swap counters
6888a11 test: distinguish revoked process identities from newly restored authenticated windows
a4952f9 test: include non-dumpable renderers in bounded revocation identity checks
9a1518d test: verify delivered remote browser address and await persistent bookmark writes
```

完整修改清单（A 新增，M 修改；共 45 个文件）：

```text
M	.github/workflows/validate.yml
A	gui-runtime/browser-network.sh
A	gui-runtime/browser-worker.py
A	gui-runtime/dbus.conf
M	gui-runtime/worker.py
A	scripts/http-boundary.mjs
M	server.mjs
M	src/app/api/auth/logout/route.ts
M	src/components/desktop/apps/basic-gui-app.tsx
M	src/components/desktop/window.tsx
M	src/lib/auth.ts
M	src/lib/gui-gateway.mjs
M	tests/debian_gui_vm.sh
A	tests/desktop_capture.py
A	tests/desktop_lab.mjs
A	tests/desktop_ui_runtime.mjs
A	tests/gui_resource_worker.py
A	tests/http_boundary_checks.mjs
A	tests/ime_fixture.py
A	tests/linux_desktop_runtime.mjs
M	tests/linux_files_runtime.py
A	tests/linux_gui_matrix.mjs
M	tests/linux_gui_runtime.mjs
M	tests/linux_ws_runtime.py
A	tests/namespace_dac_probe.py
A	tests/prepare_desktop.sh
M	tests/run_systemd_vm.py
M	tests/ws_proxy_runtime.mjs
M	webdesktop-deploy/ISOLATION_PLAN.md
M	webdesktop-deploy/PHASE3C_GUI_PREPARATION.md
A	webdesktop-deploy/PRODUCT_ARCHITECTURE.md
M	webdesktop-deploy/README.md
A	webdesktop-deploy/scripts/setup-browser-layer.sh
M	webdesktop-deploy/scripts/setup-gui-layer.sh
A	webdesktop-deploy/templates/nginx-webdesktop.conf.example
A	webdesktop-deploy/templates/webdesktop-browser-network.service
A	webdesktop-deploy/templates/webdesktop-browser.nft
A	webdesktop-deploy/templates/webdesktop-browser@.service
A	webdesktop-deploy/templates/webdesktop-browser@.socket
A	webdesktop-deploy/templates/webdesktop-display.service
M	webdesktop-deploy/templates/webdesktop-gui-lease@.service
M	webdesktop-deploy/templates/webdesktop-gui.slice
M	webdesktop-deploy/templates/webdesktop-gui@.service
A	webdesktop-deploy/HTTPS_ACCESS.md
A	webdesktop-deploy/PHASE3C_CHROMIUM_REPORT.md
```
