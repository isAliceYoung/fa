# 3B-PTY 阶段报告

状态：3B-PTY 已完成开发与本轮实验验收；停止于此，等待审查。没有开始 3C、第四阶段或生产部署。

## 范围与提交

基线 `406386d`，开发仅在 `feature/private-deploy-isolation`。安全收尾从 `b1f1a9f` 开始，身份/事务挂载修正于 `6f1aa0f` 通过前置真实测试；上传/下载改进 `42cc0a7`。PTY 与终端集成 `d70b106`，独立 systemd 租约 `5a95854`，关闭握手修正 `4c7eaf9`，启动预算 `b360aea`，宿主运行目录隔离 `91208b0`，Next 文件并发上限 `2fc18ef`。真实测试入口 `afc0d1d`，后续 PID 重用、UID/DAC、内存/CPU 与 UI 验证提交保留完整历史；没有重写或强制推送历史。

最终受测代码提交：`712567e48471119e140b0078462973628579591b`，对应 [Actions 39278531809](https://github.com/isAliceYoung/fa/actions/runs/39278531809)，六项任务全部 success。最终报告提交另列于交付消息，报告不自引用自身 SHA。测试通过/失败历史可在对应 Actions 中核对；机器生成结果只存 CI Artifact，不纳入长期版本跟踪。

后续修正分开提交：`8dca372` 修复真实 UI 中发现的中文粘贴重复，`50fc8b2` 保持无关 Cookie 改变时的会话身份，`e77eed3` 加入 UI Ctrl+C/真实 resize 验证，`57ff834` 验证三重故障下 PID 1 回收，`712567e` 按 UTF-8 大小及 surrogate 边界切分大段输入。最后一项通过真实 Bash 核对 24,580 字节中文/Emoji 内容，没有以放宽服务端帧上限解决问题。

没有修改 main、Beta、生产 VPS、仓库可见性、生产 Deploy Key、旧数据库或生产目录；没有启用自动更新。安装器 lib/modules、原 service 模板与取消欢迎文件/示例图片的 sys.ts 未改变。新 lab 脚本不是第四阶段迁移或生产安装入口。

## 安全决定与 Linux 权限

| 身份 | 授权与数据 | 不拥有的能力 |
|---|---|---|
| root | 私有源码获取、部署密钥、系统单元、受保护 socket/挂载初始化 | 不运行访客 Bash |
| wdsvc | Next 登录/数据库；私有内部 HTTP；可信 PTY 租约守卫 | 不直接打开访客请求路径，不执行访客 Bash；polkit 仅可 stop 两个固定访客单元 |
| wdguest | 文件 Agent；512 MiB 数据卷；0700 私有事务状态 | 无 sudo/root、无后端组、无登录 SSH、无任意执行协议 |
| wdterm | 实际 Bash 与 PTY worker；固定 wdguest 数据组 | 与 Agent/后端不同 UID；无服务敏感组、能力或 sudo；无法续期租约/控制其他服务 |
| wdtunnel | 原受限 SSH 转发设计保持，面向 WebDesktop loopback | 不借本轮变更开放 Shell、其他转发或生产 SSH 配置 |

同 UID 基线攻击已实测成功：SIGSTOP/SIGCONT、事务测试记录读写、proc FD 列表与环境读取；ptrace 在该内核中 EPERM，仍不足以构成边界。证据来自 `b1f1a9f` / Actions 39263248434 Artifact。最终使用 wdterm，与 wdguest 分离；额外不同 UID 测试在没有 mount namespace 隐藏的条件下直接读/写/复制真实源码、.git、.env、测试密钥与服务凭据，必须由内核拒绝且原文件不变。

数据目录 setgid 2770，新普通文件 0660，仅共享无敏感权限的数据组。文件 API 只认可两个固定数据 UID，继续拒绝硬链接、多链接文件、符号链接、路径穿越与跨挂载。`.wd-state` 0700 的终端侧 root 绑定挂载不可重命名/替换；Agent 在独立 norbind HOME 视图中仍以一个 root FD 执行事务，保持原子发布和 NO_XDEV。跨绑定挂载 rename 曾实际触发 EXDEV，已修复，不以复制或权限放宽兜底。

默认公网监听取消，三种启动方式共用 IP 校验并绑定 127.0.0.1。内部 Next 只在 wdsvc HOME 的 0700 目录/0600 Unix Socket 接收 HTTP，崩溃后只回收经 inode 校验的本账号死 socket。真实 Next 测试验证仅一个 loopback TCP listener，wdguest/nobody/wdterm 不能直接连内部 HTTP/Upgrade；没有隐藏 TCP 端口或回退。

## IPC、租约与终端

`server.mjs` 是明确的公共 HTTP/Upgrade 入口。`/api/term/ws` 先经私有 Next 路由校验 Cookie + 原页面 session，再分配固定槽位；没有恢复 term-ws/browser-ws/gui 的旧执行链。GUI/browser Upgrade 仍认证后拒绝。

PTY 运行包仅两个 Python 标准库文件，root 拥有、不可由访客修改，不包含 Next、认证、DB、服务 .env 或完整 node_modules。Socket root:wdsvc 0660，父目录 root:wdsvc 0750，服务检查 SO_PEERCRED。协议只含固定 open/输入/resize/关闭、ready/二进制输出/exit；客户端不能指定 UID、启动命令、任意 cwd 或环境。固定 `/bin/bash --noprofile --norc -i`、固定 HOME、白名单环境；子进程关闭继承的额外 FD，worker 在 Bash 出现前关闭 activation listener。

worker/Bash 同 UID，不被当作可信授权边界。授权、历史与输入队列在 wdsvc；独立 wdsvc systemd 租约单元只接收 renew，10 秒无续期/EOF/错误即停止整个 PTY 单元。PTY BindsTo 守卫；即使守卫崩溃，PID 1 也回收访客 cgroup。守卫不属于 Next 的进程树，后端 service 崩溃不会把唯一清理者同时杀死。polkit 不允许 start、瞬态单元、属性修改、重载、其他单元或访客身份操作。

每批输入重新认证，周期续验 5 秒。新登录之后的新输入立即拒绝；空闲会话在下次 5 秒续验撤销，再由 systemd 在停止期限内回收。数据库异常关闭输入、不续期，租约过期清理。WS/IPC 异常不回退高权限本地执行，不重放未确认输入。进程结束使用 systemd cgroup，不以旧 PID/负 PID 定位；槽位在停止确认和守卫连接结束前不复用。

WS 拒绝跨 Origin；输入 16 KiB/帧、帧上限 64 KiB、排队 64 KiB/64 帧；回放 256 KiB，溢出 gap 提示。IPC/WS 背压高水位 512 KiB、低水位 128 KiB；约 1 MiB/s/会话输出节流，断连暂停/截断历史而不无限缓存。20 秒未 pong 断开；无前端连接 120 秒清理。新会话全局 8 次/10 秒，超限可冷却恢复，低于系统单元启动预算。

同标签页完整刷新使用 pagehide 写入的一次性两分钟记录，并匹配窗口上下文与 reload 导航；恢复前仍由服务器验证 Cookie+原页面 session，不产生新登录。新标签页、普通导航和过期/撤销的记录不能靠 Cookie 单独恢复。记录是客户端页面标识，不传给 PTY；生产 Cookie/凭据不进入访客环境。

## 文件收尾与仍禁用功能

保持文件复制/移动、删除/回收站、创建/修改/列表/读取、单文件与分块上传、恢复/取消、下载/ZIP 的固定 IPC API。对应路径：`/api/sys/fs`、`/api/sys/upload`、`/api/sys/raw`、`/api/sys/download`、`/api/sys/info?lite=1`。新增 Next 并发上限 4，覆盖 JSON 读取前直到响应结束/取消，额外请求 429；认证与 PTY 续验不占这个文件限额。

浏览器上传以 8 MiB 分段 SHA-256 清单生成内容身份，storage key 包括完整内容指纹；Agent 对使用内容身份的事务绑定目标/大小/指纹并在原子发布前分段验证。旧无指纹 API 调用保持兼容，不提供其未声明内容的源文件匹配保证。实测同名同大小同时间但不同内容不复用断点、源内容改变不发布、陈旧/取消 ID 不补造缺失前缀；已提交偏移保留，明确取消才释放。下载名从 Agent 的固定格式元数据解析、校验并按 RFC 5987 编码；普通 `.zip` 文件不会变成双扩展名，Unicode/引号/目录 ZIP 名有真实响应测试。

SSH 健康检查由固定 root 只读预检读取实际 sshd -T/-t，覆盖服务/文件/终端身份及 IPv4/IPv6 匹配上下文。VM 还保留旧 marker 并临时移除已知实验拒绝规则，验证检查确实失败，再恢复配置；不把 marker 存在当作实际 sshd 生效。

仍返回不可用：GUI/浏览器 HTTP 与 WS、应用列表/图标/快捷方式启动、VNC 全局剪贴板/拖拽、图片转换、整机目录与 sys/info 非 lite、sys/kill、pasteFromVnc。PTY 当前不联网；/run 隔离且只能 AF_UNIX，不能通过本机解析/管理服务绕开。独立 GUI/Chromium/Claude/PCManFM/Fcitx5、原生沙箱与网络模型仅设计，见 PHASE3C_GUI_PREPARATION.md。

## 实测命令与结果

最终代码 CI：2026-10-19 UTC 的 [39278531809](https://github.com/isAliceYoung/fa/actions/runs/39278531809)，六项任务全部完成。下表按实际执行批次统计，重启后重复执行单独计数；不是不同测试定义的去重总数。跳过与未执行不计为通过。

| 环境/命令 | 通过 | 失败/错误 | 跳过 | 未执行 | 退出码 |
|---|---:|---:|---:|---:|---:|
| Windows Git Bash / `python tests/run_tests.py --json .test-results/regular-pty-delivery.json` | 151 | 0 | 0 | 0（阶段外另列） | 0 |
| Ubuntu、Debian / `python3 tests/run_tests.py`（分别） | 各 151 | 各 0 | 各 0 | 各 0（阶段外另列） | 各 0 |
| Debian / `python3 tests/linux_private_repo.py` | 9 | 0 | 0 | 0 | 0 |
| Debian / `python3 tests/linux_guest_layer.py` | 18 | 0 | 0 | 0 | 0 |
| Debian / `python3 tests/linux_tunnel_parser.py` | 3 | 0 | 0 | 0 | 0 |
| Debian real Next / `python3 tests/linux_ws_runtime.py` | 10 | 0 | 0 | 0 | 0 |
| Debian real Next / `python3 tests/linux_files_runtime.py` | 27 | 0 | 0 | 0 | 0 |
| Debian distinct UID / `python3 tests/linux_terminal_boundary.py` | 5 | 0 | 0 | 0 | 0 |
| booted Debian VM / `python3 tests/run_systemd_vm.py`，9 项首次+9 项重启 | 18 | 0 | 0 | 0 | 0 |
| booted Debian PTY VM / `python3 tests/run_systemd_vm.py --pty`，systemd 9+9 | 18 | 0 | 0 | 0 | 0 |
| 同一 PTY VM / Node `tests/linux_pty_runtime.mjs`，14 项首次+14 项重启 | 28 | 0 | 0 | 0 | 0 |
| 宿主沙箱 Chrome，经 SSH 隧道操作真实 VM / `tests/pty_ui_runtime.mjs` | 3 | 0 | 0 | 0 | 0 |

真实 systemctl/status/show、systemd-analyze verify/security、UID/GID/能力/NNP、挂载、SSH、Kernel/cgroup/OOM/PID 重用证据见 CI Artifact。SSH 合法测试密钥拒绝登录/转发：基础 VM 4 次、PTY VM 6 次全部符合预期，SSH 子命令退出为 255，测试驱动退出为 0；这些断言不混入上表 unittest 数量。构建、类型检查、Shell 语法检查、Python 编译检查均退出 0。

复现按 `.github/workflows/validate.yml` 的对应任务准备依赖：真实文件/Next 权限测试在一次性 Debian 12 privileged 容器执行；systemd/PTY 测试由 QEMU 启动完整 Debian 12 VM。`--pty` 需要前置构建产生的 `.test-results/pty-backend.tar.gz`，不可直接在生产机执行实验准备脚本。`run_systemd_vm.py --pty` 内实际调用 root 测试控制器运行 Node PTY 脚本，再从宿主浏览器经 SSH 隧道验收 UI，重启后重复内核/服务测试，最终销毁 VM。

测试工具明确另列两类未执行范围：完整 VPS 安装与 GUI 验收、未授权的生产 VPS 测试。本轮 systemd/PTY/UI 组件验收已实际执行，不能因此把上述完整安装或访客 GUI 标成通过。没有任何本轮计划内测试被跳过或未执行。

可复核证据（均在上述 Actions 页下载）：

- `regular-tests-ubuntu`（12198771014）、`regular-tests-debian12`（12197634030）：151 项结果与源码权限结果。
- `guest-3a-linux-results`（12197969497）：18/3/10/27/5 项结果。
- `booted-systemd-debian12`（12198361948）：真实 PID 1 systemd 首次/重启结果、管理 SSH 与拒绝服务账号 SSH。
- `pty-systemd-next-ui-debian12`（12199621303）：`evidence.tar` 内 `commands.txt`、首次/重启 systemd 和 PTY JSON、实际 `/proc` 身份及 cgroup；另有 `journal.txt`、`reboot.json`、`ssh-denials.json`、`terminal-ui.json`、`terminal-ui.png`、`terminal-reload.png`。
- `private-pty-test-backend` 仅是 1 天保留的私有 CI 后端构建夹具，不是访客运行包；不含 .env 或密钥，不部署到访客目录。

实测系统为 Debian GNU/Linux 12，内核 `6.1.0-54-cloud-amd64` / Debian `6.1.190-1 (2026-10-16)`，systemd `252.39-1~deb12u2`，cgroup v2。样本 UID：wdsvc=999/GID994，wdguest=994/GID993，wdterm=993/运行 GID993；数字由系统分配，不写死到客户端协议。worker 与守卫的实际/有效/保存 UID 一致，能力集全零、NoNewPrivs=1。File/PTY Socket 权限和父目录权限均有 stat 与连接正负检查。

`systemd-analyze verify` 退出 0；`security` 实测 Agent 3.8、PTY 2.8，均标记 OK。评分不是安全证明，报告保留其指出的未开启系统调用过滤等剩余项。私有事务 mount、ProtectHome/ProtectSystem、源码 DAC 与 socket 拒绝还分别由真实读写/连接测试验证。PTY VM boot ID 从 `5e2d2a67-ab2f-45a7-8c17-87754c42435c` 变为 `425d40f9-d397-4a75-ac85-0fde764b7a03`，管理员 SSH 前后成功。

曾失败的运行未删除或算作通过：`39263248434` 缺少 ss；双 bind 事务方案触发 EXDEV 后修正；`39268443398` 暴露暂停 WS 的关闭握手/故障等待问题；`39269548076` 暴露 systemd 默认启动限速；`39273353266` 的测试输入方式未发送中文，UI 后续项目未执行；`39274937515` 暴露真实重复粘贴与错误等待 networkidle，UI 为 1 通过/1 失败/1 未执行。修复后 `39276346563` 起多轮完整通过，最终代码以 `39278531809` 为准。

## 终端验收矩阵

| 项目 | 结果与实际依据 |
|---|---|
| Bash/真实 UID、ANSI、UTF-8、输入、Ctrl+C、resize | 通过；实际 WS 与 xterm UI，红色字符与中文一次显示，sleep 被 Ctrl+C 中断，最大化后 stty=34×114；大段中文/Emoji 字节数一致 |
| 页面断线、完整刷新、同一 Bash、有限回放 | 通过；WS 回放/gap，浏览器刷新前后 Bash PID 均为 4026；无关 Cookie 改变不重建 Bash |
| 无认证/错误 Cookie/错误页面/旧登录 | 通过；真实 Next 握手拒绝，不能创建访客会话；重复/延迟 Upgrade 注册和后端重启仍受网关控制 |
| 新设备登录与数据库故障 | 通过；新输入标记文件不出现，数据库停机返回 503、租约超时后 cgroup 清理，恢复数据库后可重新连接 |
| backend SIGSTOP、service 崩溃+worker SIGSTOP | 通过；独立守卫回收；额外 backend/worker 均 SIGSTOP 后 SIGKILL 守卫，仍由 PID 1 BindsTo 回收 |
| fork/setsid/后台 daemon、实际 PID 重用 | 通过；全部后代回收；首次实际复用 PID3604（4760 次 fork）、重启后 PID1115（3736 次），外部复用进程仍活着 |
| FD/内存/CPU/输出压力与文件并发 | 通过；实际 EMFILE/EAGAIN、guest cgroup OOM、CPU 节流，文件上传/下载取消/ZIP 取消并发仍健康，无 backend/global OOM |
| Root/服务/Agent 状态、FD、IPC、源码、密钥 | 通过；不同 UID 的实际拒绝，直接源码读/写/复制也失败，文件保持不变 |
| GUI/browser 旧 WS | 通过拒绝验收：继续 503，无旧高权限启动；GUI 功能本身未验收 |

## 资源、限制与后续依赖

PTY：两会话，父 slice 192 MiB/64 tasks/100% CPU；每会话 96 MiB/32 tasks/75% CPU、128 FD/进程，派生总 FD 上界不超过 8192（64 tasks × 128），无额外无限 FD 池。守卫各 24 MiB/4 tasks/10% CPU，私有临时目录 16+4 MiB，CORE=0，日志速率有界。访客卷仍 512 MiB/16384 inode/nosuid/nodev/noexec；Bash/Python 可解释脚本，直接运行该卷的 ELF/脚本被拒绝。

实际资源（首次 / 重启后，单位 MiB，1024² 字节；RSS 的 KiB 另注）：

| 指标 | 首次 | 重启后 |
|---|---:|---:|
| 压力窗口 Next cgroup memory.peak | 109.35 | 101.27 |
| Agent cgroup memory.peak | 23.52 | 33.45 |
| PTY 父 slice memory.peak（含故意 OOM） | 96.07 | 96.69 |
| 两个守卫各自 memory.peak | 7.05 / 7.02 | 7.08 / 7.06 |
| Next RSS，压力前→压力后（KiB） | 161612→175512 | 161700→173152 |
| 整机每 100 ms 采样的最低 MemAvailable（KiB） | 459352 | 462040 |
| PTY 父 slice CPU nr_throttled / throttled_usec | 27 / 647895 | 30 / 885018 |
| 系统盘使用 / 剩余（GiB） | 2.63 / 6.58 | 2.63 / 6.57 |
| 访客文件系统使用 / 可用（MiB） | 8.11 / 472.86 | 16.15 / 464.82 |

Next cgroup 峰值是在故障测试重启后、最后双 PTY+文件压力窗口所属生命周期的计数，不冒充从开机至关机的全程峰值；RSS 含共享页，与 cgroup 记账不能相加比较。整机可用内存采样覆盖 PTY 测试批次，最低约 448.59 MiB；100 ms 采样可能漏过更短峰值。两次 guest OOM 测试分别出现 oom_kill=4（日志/事件含进程组回收），内核约束均为 CONSTRAINT_MEMCG；Next/Agent/守卫 oom=0，没有 CONSTRAINT_NONE 整机 OOM。并发测试四条文件流占满时，第五条 429，取消可释放名额；两个 CPU hog 的实际节流计数增加。

VM 为 1024 MiB RAM、2 vCPU、10 GiB 系统盘、无活动 Swap；内核 MemTotal=995356 KiB。这里验证的是运行，后端在 CI 外部构建后装入，不是 1 GiB 零 Swap 完整构建或单核性能保证。访客镜像仍 512 MiB，文件系统容量 515067904 字节（格式化开销后），inode 16384。UI 浏览器在 CI 宿主运行，不计为访客 Chromium/Claude 的资源验收。两 PTY 与文件服务可在本次 1 GiB 样本中并发运行，不能据此承诺同时运行完整 GUI 栈。

已知限制：本轮终端不联网，固定初始 HOME，不执行客户端 startup cmd；用户可自行输入命令/cd。两个终端共享 wdterm UID 与访客 HOME，不提供不同访客租户之间的互斥信号/文件边界；本轮边界是访客与后端/Agent/系统身份的隔离。多个 UID 的 0600 私有用户文件不会被 Agent 越权读取，需用户明确调整共享权限。512 MiB 不证明足够保存完整浏览器/Claude profile；保留容量限制。Windows 不运行新 Linux 服务入口；源码检查/类型检查可在 Windows 执行。异常槽位若无法确认回收会隔离保留，避免误杀新进程；需修复已知单元后重启后端，不自动删除用户状态。

3C 前需审查本报告，验证 wdgui 与文件卷共享、独立网络 namespace/本机管理服务拒绝、X11/D-Bus/Fcitx5、Chromium/Electron 原生 sandbox、剪贴板/图片解码与 profile 实际增长/内存峰值。没有默认 --no-sandbox，没有扩大当前卷，也没有复制后端源码到访客包。第四阶段安装器/目录迁移、生产完整安装及 GUI 浏览器接受测试未执行且未授权。完成本轮后停止等待审查。

## 相对 406386d 的全部新增/修改文件

共 53 个文件（含本报告）；长期跟踪不包含机器生成 JSON、VM 镜像、密钥或截图。

```text
.github/workflows/validate.yml
guest-runtime/agent.py
guest-runtime/file_ops.py
guest-runtime/secure_fs.py
pty-runtime/guard.py
pty-runtime/worker.py
scripts/internal-http.mjs
scripts/network-policy.mjs
scripts/start.sh
server.mjs
src/app/api/guest/pty-auth/route.ts
src/components/desktop-client.tsx
src/components/desktop/apps/systerm-app.tsx
src/components/desktop/client-session.ts
src/components/desktop/terminal-input.d.mts
src/components/desktop/terminal-input.mjs
src/components/desktop/upload-file.d.mts
src/components/desktop/upload-file.mjs
src/components/login-screen.tsx
src/lib/guest-api.ts
src/lib/guest-client.mjs
src/lib/pty-gateway.mjs
tests/debian_pty_vm.sh
tests/linux_files_runtime.py
tests/linux_guest_layer.py
tests/linux_pty_runtime.mjs
tests/linux_systemd_runtime.py
tests/linux_terminal_boundary.py
tests/linux_ws_runtime.py
tests/network_policy_checks.mjs
tests/pty_ui_runtime.mjs
tests/run_systemd_vm.py
tests/test_network_policy.py
tests/ws_runtime_entry.mjs
webdesktop-deploy/PHASE3A_INVENTORY.md
webdesktop-deploy/PHASE3B_PTY_REPORT.md
webdesktop-deploy/PHASE3B_PTY_SECURITY.md
webdesktop-deploy/PHASE3C_GUI_PREPARATION.md
webdesktop-deploy/PTY_GUI_NEXT_DESIGN.md
webdesktop-deploy/README.md
webdesktop-deploy/scripts/check-guest-ssh.sh
webdesktop-deploy/scripts/setup-guest-layer.sh
webdesktop-deploy/scripts/setup-guest-ssh-deny.sh
webdesktop-deploy/scripts/setup-guest-volume.sh
webdesktop-deploy/scripts/setup-pty-layer.sh
webdesktop-deploy/templates/webdesktop-guest-state.service
webdesktop-deploy/templates/webdesktop-guest.service
webdesktop-deploy/templates/webdesktop-pty-lease@.service
webdesktop-deploy/templates/webdesktop-pty-lease@.socket
webdesktop-deploy/templates/webdesktop-pty.rules
webdesktop-deploy/templates/webdesktop-pty.slice
webdesktop-deploy/templates/webdesktop-pty@.service
webdesktop-deploy/templates/webdesktop-pty@.socket
```
