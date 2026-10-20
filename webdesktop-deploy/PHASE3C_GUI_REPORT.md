# 3C-GUI 基础阶段报告

状态：3C-GUI 基础功能已实现，基础运行与权限验收通过；Claude实验触发受限单元OOM，资源验收未通过，不能宣称全部CI成功或Claude已可用。本轮范围仅为独立 GUI 身份、Xvnc/Openbox/PCManFM 与后续应用可行性实验；没有全面应用迁移、第四阶段安装器迁移或生产部署。完成报告后停止，等待审查。

## 基线与安全收尾

基线 `3eba112`，仅开发 `feature/private-deploy-isolation`。`ccdc926` 修复 OSC 777 与 WebSocket 来源策略，原六项 CI 在 [39352323077](https://github.com/isAliceYoung/fa/actions/runs/39352323077) 全部成功。`779b676` 加入基础 GUI 与单核 VM，`7b1406f` 加入浏览器/Electron/网络实验，`46ac214` 修正单核 CPU 节流测试假设，`6d1fc54` 增加真实 TLS 反向代理验证。实现、测试与文档的完整文件及提交清单在文末。

OSC 777 不再执行任意命令或自动打开窗口。白名单只有固定 `pcmanfm`（初始目录只能为 `~`），或访客范围内的文件/目录请求；不支持 URL、任意 Shell、参数、绝对路径、路径穿越、非法 UTF-8/base64。每个终端实例最多提出一项请求，回放/输出洪泛不会排队无限提示。输出只能显示不抢焦点的请求条，用户主动点击“审阅请求”再确认才打开；取消/忽略不执行。输入回车不能直接确认尚未审阅的请求。前端确认不替代后端认证或固定启动策略。

所有公共 WebSocket Upgrade 都必须提供唯一 Host 与 Origin。Origin 必须精确匹配服务端 `WD_PUBLIC_ORIGINS`，Host 必须匹配该来源的 authority；不从客户端 Host 或 Forwarded/X-Forwarded-* 推导信任。未配置时只允许实际绑定地址/端口对应的 HTTP 来源，缺失/错误来源返回 403；通过来源检查之后仍须 Cookie + 页面 session 双验证。反向代理需保留原 Host，例如显式配置 `WD_PUBLIC_ORIGINS=https://desktop.example`，代理设置 Host 为原始域名，转发 Upgrade/Connection；改写成内部 Host 的代理安全拒绝。SSH 隧道本地端口不同于服务端时，须把实际浏览器来源（例如 `http://127.0.0.1:18890`）加入固定配置。没有通配符或动态 Host 兜底。

## 身份、目录与协议

| 身份 | 执行内容 | 共享范围 |
|---|---|---|
| wdsvc | Next 认证/数据库；独立 PTY/GUI 租约守卫 | 独占敏感服务组；不得执行访客 GUI/Bash |
| wdguest | 文件 Agent | 访客数据 UID；独占 0700 `.wd-state` |
| wdterm | Bash/PTY 数据平面 | 运行 GID 为非敏感 wdguest 数据组 |
| wdgui | Xvnc/Openbox/PCManFM；独立应用探测 | 第四个独立 UID，账号自身私有组；执行单元仅使用 wdguest 数据 GID |

wdgui 不加入 sudo、postgres 或 wdsvc 组，密码锁定、nologin，实际 sshd DenyUsers 加入 wdgui；固定只读预检读取实际 sshd 有效配置，不依赖旧 marker。新增实验脚本拒绝已有身份、运行包、单元或未知 SSH 配置；不接管已有安装。管理员 SSH 与账号拒绝测试覆盖重启前后。

`/usr/local/lib/webdesktop-gui` root:root 0755，worker/guard/Openbox 配置/ready 均 root 0644，访客不可修改。该运行包只有两个 Python 标准库文件与公开 Openbox 配置，不包含 Next、认证/DB 模块、.env 或 node_modules。私有后端源码继续 root:wdsvc，部署测试密钥 root 0600。

固定 IPC 为 `/run/webdesktop-gui/{session,lease}-1.sock`：root:wdsvc 0660，父目录 root:wdsvc 0750，SO_PEERCRED 只允许 wdsvc。worker 接受一条服务连接后关闭监听 FD，应用子进程关闭继承 FD。启动协议只有 `open`、固定 app（desktop/pcmanfm）和有界宽高；不接受客户端 cmd/cwd/UID/env/desktopFile。之后只转发有界 base64 RFB 字节，不传 Cookie、认证信息或服务环境。

共享工作目录仍是 512 MiB、16384 inode、nosuid/nodev/noexec 的原实验卷。文件 Agent 只增加固定 wdgui UID 作为普通共享数据所有者，保留 openat2、NO_XDEV、单链接与事务约束。`.gui` 为 wdgui:wdguest 0700，root 绑定挂载保护目录名；配置/profile/cache 也占用这个有界卷。它与 Agent 的 `.wd-state` 分离，终端不能读取或重命名私有状态。0600 私有用户文件不会因共享数据组而被越权修复。

## 最小显示与生命周期

只有 `/api/gui/ws` 的固定 PCManFM/桌面协议恢复；HTTP `/api/gui`、apps/icon/shortcut/clipboard/drop 等仍返回未迁移。前端使用新的 BasicGuiApp/noVNC，不恢复旧 `gui.ts` 或后端 GUI 执行链。浏览器/极速浏览器仍未开放。

单 GUI 槽位，最大 1280×800；前端固定 1024×640。Xvnc 使用 `-rfbport -1` 与 0600 私有 Unix RFB socket、`-nolisten tcp`；X11 `:100`、0600 随机 Xauthority、会话 D-Bus 都在服务私有 `/tmp`，不挂载管理员显示或系统 D-Bus。VNC 的 None 认证只存在于这个私有 socket，公开 WS 另行认证；不开放无认证 TCP VNC。网页剪贴板双向传输、动态分辨率和 AllowOverride 禁用。

GUI 服务 PrivateNetwork/AF_UNIX、ProtectHome=tmpfs、ProtectSystem=strict、NNP、空能力集、只读 cgroup、禁用 namespace 创建；/run 隔离。worker 启动时实际尝试 Internet socket，若内核未阻止则拒绝运行。Openbox 使用 root 配置，不加载用户自动启动命令。PCManFM 是普通访客进程，读不到源码、.git、.env、部署密钥或服务账号进程内部。

wdsvc 租约守卫独立 systemd 单元，10 秒未 renew/EOF/错误停止固定 GUI 单元；BindsTo 确保守卫崩溃后 PID 1 继续回收整个 GUI cgroup。每批鼠标键盘输入重新认证，5 秒周期续验；数据库异常关闭输入、不续期；没有高权限回退。GUI WS 关闭即回收该图形会话，当前不提供刷新后原 GUI 会话续接，PTY 原有短时重连保持。停止未确认的槽位隔离保留，不复用活进程或依赖负 PID 信号。

polkit 仅追加 wdsvc stop 固定 GUI@1 的权限，随后仍由原 PTY deny-by-default 规则处理其他操作；不能 start/创建瞬态单元/改属性/控制其他系统服务。没有 sudo 授权。

## Debian 12 真实权限证据

资源证据批次使用 Debian 12、内核 `6.1.0-54-cloud-amd64`（Debian `6.1.190-1`）、systemd `252.39-1~deb12u2`、TigerVNC `1.12.0`。`systemd-analyze verify` 同时验证 GUI/lease 两组 service/socket、状态挂载和 slice，退出0；`systemd-analyze security --no-pager webdesktop-gui@1.service` 实际评分2.8 OK，完整逐项输出保存在 Artifact。评分用于配置核对，不能替代下面的权限攻击测试。

该批次实际 UID/GID：wdsvc=999/994，wdguest=994/993，wdterm=993/992，wdgui=992/991；GUI执行时GID=993而不是991，且没有敏感补充组。检查真实 `/proc` 的 UID/GID、NNP=1、capability 全0、seccomp，实际尝试 IPv4/IPv6 socket 创建；不是只读模板或模拟 setuid。账号数值由该 VM 分配，不能写死到生产脚本。

分别以 wdgui 和其他访客身份，在命名空间外尝试直接连接 GUI/lease socket，再在真实服务内检查管理端点不可达。源码、.git、.env、root 测试密钥的读取/写入/复制、服务进程信号、ptrace、/proc/FD、Agent事务状态篡改均被内核拒绝；拒绝后再次核对敏感字节未变化。可正常访问的数据经真实 RFB 键鼠创建目录、Agent读写、GUI读写交叉验证，网页创建目录的独立 `stat` 结果为 `wdgui:wdguest:2770`。

Socket 启动、认证失效/PG停止、worker被KILL、Next与worker被STOP且guard被KILL的故障组合均检查 PID 1 整 cgroup回收及重新打开。重启前后 boot ID 从 `7d33373a-218e-4986-9504-3e48af2104c9` 变为 `1872634d-76ce-4fc9-837c-2c5eb90ed627`，管理员SSH均成功，四个受限身份各两次使用合法测试密钥仍被 sshd 拒绝。该测试仅发生在 Actions 的可丢弃 QEMU VM。

## 实际测试与失败历史

最终实现/测试提交 `6fa2f9a90d857b1d9459878cf6a2aea9a94ee1bb`，对应 [Actions 39375458095](https://github.com/isAliceYoung/fa/actions/runs/39375458095)：七项任务中六项成功，GUI组合任务因Claude资源实验失败而失败。不是七项全绿。基础GUI的56项全部通过；应用实验8通过/1失败。结果按实际执行批次报告，重启后的重复执行不是不同测试定义；浏览器全面迁移和生产安装另列未执行，不计为通过。文档交付不改变该已实测运行代码。

| 环境与实际命令 | 通过 | 失败/错误 | 跳过 | 本批次未执行 | 退出码 |
|---|---:|---:|---:|---:|---:|
| Windows Git Bash：`python tests/run_tests.py --json .test-results/regular-3c.json` | 151 | 0 | 0 | 2范围（见下） | 0 |
| Ubuntu、Debian：`python3 tests/run_tests.py`（分别） | 各151 | 0 | 0 | 各2范围（见下） | 0 |
| Debian：`python3 tests/linux_private_repo.py` | 9 | 0 | 0 | 0 | 0 |
| Debian：`python3 tests/linux_guest_layer.py` | 18 | 0 | 0 | 0 | 0 |
| Debian：`python3 tests/linux_tunnel_parser.py` | 3 | 0 | 0 | 0 | 0 |
| 真实 Next/PG/HTTPS 代理：`python3 tests/linux_ws_runtime.py` | 12 | 0 | 0 | 0 | 0 |
| Debian：`python3 tests/linux_files_runtime.py` | 27 | 0 | 0 | 0 | 0 |
| Debian：`python3 tests/linux_terminal_boundary.py` | 5 | 0 | 0 | 0 | 0 |
| booted 2 vCPU Debian VM：`python3 tests/run_systemd_vm.py`，systemd首次9+重启9 | 18 | 0 | 0 | 0 | 0 |
| booted 2 vCPU PTY VM：`python3 tests/run_systemd_vm.py --pty`，systemd 9+9、PTY 14+14、UI 3 | 49 | 0 | 0 | 0 | 0 |
| booted 1 vCPU GUI VM：`python3 tests/run_systemd_vm.py --gui` 内基础批次：systemd 9+9、前置PTY14、GUI10+10、UI4 | 56 | 0 | 0 | 0 | 各子批次0；父驱动因下行实验最终为1 |
| 同一GUI VM：`bash tests/prepare_gui_probes.sh` / Node `tests/linux_gui_probes.mjs` | 8 | 1 | 0 | 0 | 准备0；实验1 |

常规JSON的 `not_executed` 明确列出两个验收范围：①完整VPS安装与全面GUI验收（组件VM另测）；②未经授权的生产VPS测试。它们不是已定义的两个 unittest，但也不能写成“所有工作未执行=0”；上表照实列2范围。基础GUI的实际验收覆盖本报告限定能力，不等于“全面GUI验收”。后续应用登录、中文输入、剪贴板等未实施范围见后文，不计为通过。

另有两个可移植 Node 检查脚本 `terminal_actions_checks.mjs`、`ws_origin_checks.mjs`（不混入151项 unittest 数量），Shell/Python/Node 语法检查、构建、类型检查以及 nftables 的 Debian 内核干运行检查。SSH 合法测试密钥拒绝检查为基础VM4次、PTY VM6次、GUI VM8次，预期 SSH 子命令退出255、驱动退出0；与管理员重启前后成功登录分别核验。

最终CI原始证据：[GUI/资源/原生沙箱/重启/截图](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12242768406)、[PTY/systemd/终端UI](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12242743491)、[独立基础systemd](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12243192724)、[Linux权限/HTTP/WS/文件](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12242997173)、[Ubuntu151项](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12243516510)、[Debian151项及源码权限](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12242896902)。机器生成JSON、截图和日志保存在CI Artifact，不长期跟踪到源码；构建测试后端单独仅保留一天，排除.env和密钥，且不会进入访客运行包。

首次 1 vCPU VM 在 PTY 前置测试为 13 通过/1 失败：硬件只有一核时，100% 单核聚合 CPU 配额未发生 CFS 节流，旧双核断言不适用，GUI 测试未执行。未提高任何配额；单核检查实际 cpu.max，双核原测试继续要求真实节流。该失败保留在 Actions 39353336663。

Actions 39353967606 的 GUI 内核批次10/10通过；网页批次1通过/1失败/2未执行。noVNC 实际协商 QEMU ExtendedKeyEvent，旧断言只认普通 KeyEvent；截图复核还发现模拟快捷键大小写不正确，以及异步等待未可靠核对目录，不能把该轮算作网页创建目录已验收。修正后用同步等待循环精确匹配文件 API 返回的目录名，并由独立 SSH stat 核对 wdgui:wdguest 所有者。随后组合测试曾因未显式结束其自建PTY、导致保留会话占槽而失败，已修正测试清理，不改变正式重连语义。

`c214ea1` 的 Actions 39357070230 已取得 GUI 首次/重启各10项、网页4项、GUI SSH8次成功；应用实验因 nftables 内联嵌套定义语法错误停止，Chromium/Claude 探测当轮未执行。规则拆出后增加 `nft -c` 前置检查；中间语法失败导致依赖任务跳过的运行也保留，不计为通过。Claude包最初路径识别失败的运行同样没有产生 sandbox 通过结论。

后续应用实验暴露了 Fcitx 测试工具的生命周期问题：其前台进程不能用等待退出的方式判定“已启动”；过早调用 remote 还会触发 D-Bus 自动激活竞争实例。修正为等待私有总线名称归属，再查询无输入焦点时合法的 0/1/2 状态。Actions 39359063969 的基础验收全部通过，但实验为3通过/6失败，浏览器未启动；更早一次5通过/4失败包含未观察到 renderer 的空集断言，不能算 native sandbox 已验证。当前检查明确要求观察到真实 renderer、不同 user/pid namespace 及增加的 seccomp filter；输入法失败也不得阻断独立浏览器观察或被隐去。

`c2b94b7` 的基础验收全部通过，实验控制器在 Chromium 正常结束后尝试再次停止已经被 systemd GC 回收的瞬态单元，导致实验报告未写出、Claude当轮未执行。探测单元随后增加限时保留属性，控制器先取证再显式停止并确认无剩余进程，45秒运行上限不变；正式 GUI 单元没有采用此测试属性。`08339ec` 的 Actions 39361121283 为六项任务成功、一项GUI组合任务失败：基础56项通过，应用实验7通过/2失败。Chromium已经真实证明sandbox，Claude因官方程序拒绝调试端口参数而未产生renderer。`eaf702b` 移除Claude调试参数，仅以root只读进程取证，不改厂商代码或绕过调试授权，也不启用 `--no-sandbox`；之后的结果单列为最终实验批次。

## 资源预算与测量范围

基础 GUI 固定 160 MiB/64 MiB Swap、48 tasks、60% CPU，256 FD/进程，/tmp 32 MiB+/var/tmp 4 MiB；仅一槽。独立守卫 24 MiB/4 tasks/10% CPU。已覆盖的故障均验证整 cgroup 清理，CORE=0、日志速率有界。没有因失败提高内存或取消隔离。

新增 QEMU Debian 12 环境为 1 vCPU、1024 MiB RAM、10 GiB 系统盘。后端在 CI 宿主构建后装入；这不证明低配 VPS 可零 Swap 构建。分别记录 Next+PG+文件 Agent+PTY、追加 Xvnc/Openbox、PCManFM、关闭后状态；cgroup memory.peak、100 ms 采样内存/任务峰值、CPU 时间、整机 MemAvailable、Swap、登录/健康响应与磁盘使用分别报告，不混算 RSS/共享页与内核记账。

以下数据来自 `c2b94b7` 的 [真实单核 VM Artifact 12237252444](https://github.com/isAliceYoung/fa/actions/runs/39359999649/artifacts/12237252444)；该轮基础 GUI 全部通过，随后实验取证失败单独记录，不能把整个 job 写为成功。之后仅修改实验取证，不改变本表所测正式 GUI 单元。首次启动/真实重启后各测一次：

| 阶段 | GUI cgroup 当前/生命周期峰值 MiB（首次；重启） | 整机 MemAvailable MiB（首次；重启） | 登录耗时 ms（首次；重启） |
|---|---|---|---|
| Next + PG + Agent + 一个真实 PTY，无 GUI | 无 GUI 进程 | 560.24；587.70 | 71.18；62.40 |
| Xvnc + Openbox + 私有 D-Bus | 83.39/83.65；113.21/113.46 | 518.11；544.79 | 56.53；61.14 |
| 加单个 PCManFM | 111.52/112.59；137.04/137.31 | 496.24；523.68 | 49.00；48.64 |
| 暂停接收 RFB 的背压场景 | 106.85/112.59；136.59/140.54 | 464.30；479.32 | 653.70；387.05（该场景后立即请求） |
| 关闭并回收后 | 61.78/112.59；85.45/140.54，tasks=0 | 493.58；499.24 | 未另测 |

GUI 内核任务峰值12/13，100 ms采样任务峰值10/10；不同指标不混同。GUI cgroup memory.events 的 oom/oom_kill 均0，Next 与文件 Agent 均0，整机未出现 CONSTRAINT_NONE OOM。首次运行 PTY slice 中已有的 oom=1 来自前置真实资源耗尽故障注入，重启后的 PTY 计数为0，不能误报为 GUI 引起的 OOM。

最终 `6fa2f9a` 另行加入100 ms独立进程计数：首次/重启观测进程峰值为7/6，任务采样峰值10/10；对应GUI CPU采样峰值79.03%/81.33%，累计CPU约2.31秒。进程数与内核线程/任务数分列，不拿TasksMax或线程峰值冒充进程峰值；100 ms采样也不能保证捕获所有短命子进程。原始细目在最终GUI Artifact的gui-prepare.json/gui-reboot.json。

每阶段约1.55秒观察窗内，GUI 平均 CPU 为单核的0.10–0.15%；包含启动/操作的100 ms采样最高83.12%/81.24%，整个 GUI 测试累计 CPU 约2.31秒。短采样窗跨越 CFS 周期可高于60%配额，不代表取消配额；实际 cpu.max 与节流统计保存在证据中。60%是时间配额，不是任意滑动100 ms绝不超过60%的承诺。守卫空闲 CPU 窗口为0，其启动/超时CPU另受10%限制。

测试期间整机最低 MemAvailable 为428.33/438.02 MiB。基础阶段 SwapTotal=0、SwapUsed=0；这是在不增加 Swap 的条件下测得，未修改原安装器 Swap 策略。关闭后确认 wdgui 无进程，cgroup 中仍有61.78/85.45 MiB可回收文件缓存记账，不能声称全部内存立即归零。10次健康请求范围3.11–7.67 ms；这是短测，不是持续负载延迟保证。

基础图形软件安装后根盘使用约3.30 GB，可用约6.57 GB；访客文件系统可用约495.75 MB，GUI profile约50,402 B。基础 GUI 跑通并不意味着160 MiB还足以同时加入浏览器或 Electron：重启后内核记账峰值已到140.54 MiB，需要独立预算与逐项验收。UI Chrome 在 CI 宿主开启原生 sandbox，只操作 VM 内的 noVNC；不能把宿主 Chrome 资源冒充访客浏览器资源。

## Chromium / Claude / Fcitx5 实验

这些实验不向桌面提供应用启动入口。单独 root 管理的探测单元使用 wdgui、NNP、空能力集、严格文件权限与固定 320 MiB/128 tasks/70% CPU 上限，按顺序运行 Chromium 和 Claude；不同时运行，不改变基础 GUI 的 AF_UNIX/PrivateNetwork 限制。仅实验单元允许 Chromium 原生 sandbox 必需的 user/pid/net/mnt namespace。

独立 root 创建的 veth 网络 namespace，主机 INPUT 拒绝来自该 veth 的连接，FORWARD 拒绝 IPv6、私网、loopback、link-local 等地址，仅允许外部 HTTP(S)/DNS。实际测试外部 HTTPS 和主机 PostgreSQL/Next/SSH/metadata 地址；探测单元 /run 隔离，拒绝 PostgreSQL/内部 Next/系统 D-Bus Unix Socket。该规则仍需后续真实部署网络与公网回流评审，不是完整生产网络方案。

Claude 固定官方 APT `2.42304.2`，校验公开 APT key SHA256 与软件包 SHA256，并由签名 APT 元数据验证；只提取包，不执行维护脚本或启用 updater。实际应用及 renderer 记录 UID/NNP/能力、user/pid/net namespace 与 seccomp，检测 --no-sandbox 等不安全参数。Chromium 同时读取 chrome://sandbox 并访问测试 HTTPS 页面。私有 X11/D-Bus/Fcitx5 启动与状态探测不等于已验收中文输入法集成。

最终 `6fa2f9a` 的 [Actions 39375458095](https://github.com/isAliceYoung/fa/actions/runs/39375458095) / [Artifact 12242768406](https://github.com/isAliceYoung/fa/actions/runs/39375458095/artifacts/12242768406) 已实际执行完整9项实验：8通过、1失败、0跳过、0未执行，Node退出1。失败项是Claude没有存活完整25秒观察窗，不是隐藏或跳过；其单元实际 `Result=oom-kill / ExecMainStatus=9`，memory.events记录oom=5、oom_kill=2、oom_group_kill=1，内存上限仍为320 MiB。内核日志明确记录该单元CONSTRAINT_MEMCG，没有主机CONSTRAINT_NONE全局OOM；两个探测单元停止后都无剩余被观察进程。前一批 `eaf702b` 的 [Artifact 12243133570](https://github.com/isAliceYoung/fa/actions/runs/39375152413/artifacts/12243133570) 同样为8/1，Claude同样因OOM退出，没有用重跑隐藏该问题。

Chromium与Claude均观察到真实renderer：UID=wdgui、NNP=1、能力为0、不含 --no-sandbox，并有不同于主进程的user/pid namespace及更多seccomp filter。Chromium chrome://sandbox/ 同时报告 Namespace、PID/Network namespaces、Seccomp-BPF/TSYNC 为启用；Yama broker/non-broker保护显示No，不能宣传所有沙箱指标均开启。Chromium当前使用真实headless renderer，此结果不等于其图形窗口、GPU/音视频或全套网页功能已验收。

| 独立实验单元（各包含自身Xvnc/Fcitx） | cgroup峰值 | 内核任务峰值 | CPU累计 | profile首次增长 | 结果 |
|---|---:|---:|---:|---:|---|
| Chromium | 320.06 MiB，max事件1699，OOM=0 | 105 | 3.49 s | 4,498,911 B | 原生沙箱已证实，观察窗内存活，但已碰内存限额 |
| Claude | 320.18 MiB瞬时记账，上限仍320 MiB | 90 | 6.60 s | 4,640,398 B | 原生renderer沙箱已证实；OOM回收，资源可用性失败 |

内存峰值的短暂页级越界是内核记账数据，不是把限额提高到320.18 MiB。实验按顺序运行而不是同时运行；最低整机MemAvailable为356.26/266.42 MiB，无Swap。512 MiB访客卷没有扩大，两个profile与基础GUI累计约9.19 MB，卷剩余487.31 MB；这些只是一次短启动与失败前的增长，不证明长期容量足够。软件安装后根盘约5.10 GB已用、4.78 GB可用。

**Claude当前明确被资源问题阻塞，不能恢复正式桌面入口。** 后续应先定位工作集/缓存/启动峰值和可回收部分，制定全服务聚合预算，验证OOM与profile恢复；没有因本轮失败提高上限、取消隔离、修改厂商代码或使用 --no-sandbox。Chromium与Claude的登录、长期profile、完整交互以及实际中文输入仍未执行。

## 尚未迁移及下一步

- Chromium 浏览器标签/导航/下载、极速浏览器/CDP/音视频；Claude 正式用户会话与凭据存储。
- Fcitx5 GTK/Qt/XIM 的实际中文输入、候选框、热键与重启恢复。
- 网页双向剪贴板、文件拖拽、图片转换、VNC 文件复制粘贴、应用 launcher/快捷方式/图标服务。
- GUI 重连/完整刷新保留原会话、多应用共享桌面与应用退出的细化策略。
- 生产 egress/本机管理地址与 IPv4-only 兼容性评审；长期 profile/缓存/ENOSPC/inode 与内存预算。

下一阶段应逐项审查可行性证据，再选择一个应用恢复；坚持固定 IPC、独立 UID、原生 renderer sandbox、内核容量和 cgroup 上限。未获进一步授权前不全面迁移、不扩大访客卷、不进入第四阶段或生产部署。

| 下一步能力 | 恢复前必须完成 |
|---|---|
| Chromium 图形窗口 | 另设固定可联网应用单元与聚合预算，受控共享访客 X11/Xauthority/私有 D-Bus；基础 GUI 继续 AF_UNIX/PrivateNetwork。复验原生 namespace/seccomp、主机公网/私网/Unix 管理入口拒绝、证书/DNS/断网与退出回收，再恢复固定浏览器协议。不能开放任意命令或 CDP 到网页。 |
| Claude Desktop | 使用已验证签名及固定摘要包；先验证真实 renderer 的 native sandbox、登录/注销、离线与网络失败，明确用户令牌只进访客私有 profile。保留源码/DB/部署密钥隔离，不复用旧包装器的不安全参数或启用 updater。 |
| Fcitx5 | 不只检查 D-Bus 名称：在实际 GTK/Qt/XIM 应用输入中文，验证候选框、焦点、热键、关闭/重连与进程回收；只连接当前访客私有会话总线。 |
| 剪贴板/拖拽/图片 | 逐方向设计经认证的显式动作、类型/字节/时间与并发预算，取消及失效即停止；文件仍走 Agent IPC，不让 wdsvc 直接打开路径。默认不恢复 RFB CutText。 |
| 容量与并发 | 在原512 MiB卷测完整首次启动、登录、浏览/下载、缓存淘汰、重复启动、磁盘满与inode满；测试全部单元并发的聚合内存/任务/CPU和登录延迟。短期 profile 增量不能用来承诺长期容量，也不据此放宽卷上限。 |

## 全部修改文件与提交

共45个文件（含本报告），实现与测试提交按先后列出；最终文档提交可由 `git log` 核对。本阶段仅向开发分支推送，均显式使用 `http.https://github.com.proxy=http://127.0.0.1:7897`。交付前已读回远端 main 与 Beta：main / Beta解引用均为 `5922e1408de91e56771f1ea0bf94a9df1b7028cd`，Beta标签对象仍为 `76f93a1682845306877552868f8a8a77cae156a9`。

与 `3eba112` 比较，原 `install.sh`、`bootstrap.sh`、`scripts/start.sh`、`src/lib/sys.ts` 没有改动；原 IPv4-only、Swap、PostgreSQL 首次恢复和 service 安装语义由151项回归继续验证。未生成欢迎文本/示例图片，未启用自动更新。所有账号、SSH、systemd、网络变更只在一次性实验 VM/容器发生；没有连接生产VPS、操作生产Deploy Key、修改可见性、迁移旧部署目录、数据库或用户文件。

```text
.github/workflows/validate.yml
guest-runtime/secure_fs.py
gui-runtime/guard.py
gui-runtime/openbox.xml
gui-runtime/worker.py
scripts/ws-origin-policy.mjs
server.mjs
src/components/desktop/apps/basic-gui-app.tsx
src/components/desktop/apps/systerm-app.tsx
src/components/desktop/desktop.tsx
src/components/desktop/taskbar.tsx
src/components/desktop/terminal-actions.d.mts
src/components/desktop/terminal-actions.mjs
src/components/desktop/window.tsx
src/lib/gui-gateway.mjs
tests/cdp_gui_probe.mjs
tests/debian_gui_vm.sh
tests/debian_pty_vm.sh
tests/gui_probe_network.nft
tests/gui_probe_worker.py
tests/gui_ui_runtime.mjs
tests/linux_guest_layer.py
tests/linux_gui_probes.mjs
tests/linux_gui_runtime.mjs
tests/linux_pty_runtime.mjs
tests/linux_ws_runtime.py
tests/prepare_gui_probes.sh
tests/run_systemd_vm.py
tests/terminal_actions_checks.mjs
tests/ws_origin_checks.mjs
tests/ws_proxy_runtime.mjs
webdesktop-deploy/PHASE3A_INVENTORY.md
webdesktop-deploy/PHASE3C_GUI_PREPARATION.md
webdesktop-deploy/PHASE3C_GUI_REPORT.md
webdesktop-deploy/PTY_GUI_NEXT_DESIGN.md
webdesktop-deploy/README.md
webdesktop-deploy/scripts/check-guest-ssh.sh
webdesktop-deploy/scripts/setup-gui-layer.sh
webdesktop-deploy/templates/webdesktop-gui-lease@.service
webdesktop-deploy/templates/webdesktop-gui-lease@.socket
webdesktop-deploy/templates/webdesktop-gui-state.service
webdesktop-deploy/templates/webdesktop-gui.rules
webdesktop-deploy/templates/webdesktop-gui.slice
webdesktop-deploy/templates/webdesktop-gui@.service
webdesktop-deploy/templates/webdesktop-gui@.socket
```

```text
ccdc926 fix: require configured WebSocket origins and review terminal output actions
779b676 feat: add separate-UID basic Xvnc Openbox PCManFM execution with bounded GUI units
7b1406f test: probe native browser Electron sandboxes and isolated GUI egress on one-core Debian
46ac214 test: distinguish hardware CPU saturation from cgroup throttling on one-core VM
6d1fc54 test: verify real TLS reverse-proxy Upgrade with preserved Host and strict source policy
a0afab9 test: verify extended VNC key events and GUI file and service boundaries
06fe0e3 test: verify exact GUI-created directory and native uppercase shortcut through browser
56dadee test: close owned PTY sessions explicitly between GUI resource scenarios
a4e3b09 fix: refresh GUI authentication status on disconnect and preserve probe service hardening
50cfbfc test: distinguish native renderer seccomp filters and wait for private IME readiness
c214ea1 test: use verified Claude package ELF layout and complete basic GUI reboot before probes
9e7a108 test: prove direct GUI socket DAC denial and exact guest runtime packaging
a6f971e fix: validate isolated GUI nftables policy before runtime experiments
85436aa fix: delimit nested nftables chain definitions with explicit newlines
5e6ad44 fix: supervise foreground Fcitx and require actual renderer observations in probes
b374e8a test: measure staged one-core CPU usage and baseline login latency for GUI budget
c2b94b7 fix: wait for private IME bus ownership before querying an unfocused input context
c9e2cf2 test: observe browser sandbox independently of private IME feasibility
981160d test: retain transient probe evidence until bounded cgroup cleanup is verified
08339ec test: navigate the observed Chromium target explicitly to native sandbox status
eaf702b test: observe stock Claude renderers without rejected debugger switches
6fa2f9a test: distinguish sampled GUI processes from kernel task peaks
```

## 参考

TigerVNC 的 TCP 关闭参数与 Unix Socket、剪贴板参数以 [Debian 12 Xtigervnc 手册](https://manpages.debian.org/bookworm/tigervnc-standalone-server/Xtigervnc.1.en.html) 为依据，并实际检查监听/交互；Chromium 的机制参考 [Chromium Linux sandbox 源码说明](https://chromium.googlesource.com/chromium/src/+/main/sandbox/linux)。Electron 的窗口选项可能影响 renderer sandbox，不能只看 UID 或“窗口能打开”，参考 [Electron Process Sandboxing](https://www.electronjs.org/docs/latest/tutorial/sandbox)；实际结论仍以本报告对应的内核/进程证据为准。
