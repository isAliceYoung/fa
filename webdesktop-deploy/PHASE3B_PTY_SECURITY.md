# 3B-PTY 安全收尾与实施门槛

当前文件记录安全收尾设计、已取得的前置证据与 PTY 的验收边界。仅在显式准备的可丢弃 Debian 12 systemd 环境开放新终端；没有运行包时仍返回 503，没有恢复旧执行路径。未迁移生产目录。最终运行时结果、全部文件和测试数量见 [PHASE3B_PTY_REPORT.md](PHASE3B_PTY_REPORT.md) 与其 CI Artifact。

## 已实测的同 UID 风险

提交 `b1f1a9f` 的 Actions [39263248434](https://github.com/isAliceYoung/fa/actions/runs/39263248434) 的 `guest-3a-linux-results` Artifact 包含 `webdesktop-same-uid-baseline.json`：与真实文件 Agent 相同 UID 的独立 Python 进程能够 SIGSTOP/SIGCONT Agent，读取 `/proc/PID/fd`、`environ`，读取并改写 Agent 私有事务目录中的测试记录。ptrace attach 返回 EPERM，不能将这一项限制误认为同 UID 安全边界。

该轮新增 WebSocket 组 9 通过、1 错误（测试环境缺少 `ss`），文件组因前置失败未执行。补齐 iproute2 后，提交 `01d02e9` 的 [39263591835](https://github.com/isAliceYoung/fa/actions/runs/39263591835) 五项 CI 全部成功。不得把首次失败或后续身份改动的待验证结果计为通过。

## 网络边界

`npm start` 与 `scripts/start.sh` 共用字面 IP 校验。默认 127.0.0.1，非 loopback 显式地址输出暴露提示；不接受域名、端口混入地址、空白和 IPv6 zone。保留 IPv4 SSH 本地转发。

Next 内部 HTTP 使用服务 HOME 内 `.webdesktop-http/next.sock`；目录服务 UID 0700、socket 0600。没有内部 TCP 回退。只回收经 inode 校验的同 UID 死 socket，活动实例或未知文件拒绝启动。真实 Next 测试检查进程仅有一个 loopback TCP listener，并验证 wdguest/nobody 对内部 HTTP 与 Upgrade 的连接被内核拒绝。崩溃后重新启动继续验证认证拒绝路径。

## 文件 Agent 与终端身份决定

保留文件 Agent 的 `wdguest` UID，新增交互 UID `wdterm`。`wdsvc` 仍独占服务身份、认证与数据库。wdterm 账号自身有独立 UID/私有组；执行单元只使用无敏感读取权限的 wdguest 数据组，不加入 wdsvc 或 sudo 组。新账号也由实际 sshd DenyUsers 禁止 SSH，包括仅请求端口转发的登录。

普通数据目录使用 setgid 2770、普通新文件 0660。文件模块保留 openat2、单链接检查、事务及容量限制，仅把可接收的数据所有者扩为这两个固定 UID。服务代码不接受客户端 UID。终端可访问普通数据；用户自行移除组权限的文件会安全拒绝，不越权修复。

事务目录 `.wd-state` 仍在原 512 MiB 文件系统上，所有者 wdguest、权限严格 0700，并由 root 在终端可见的挂载树中建立绑定挂载。wdterm 无法读取、修改或重命名该挂载点。Agent 由 systemd 提供独立 `norbind` HOME 视图，事务与数据使用同一个 root FD、同一个挂载，保留 openat2 的 NO_XDEV 和原子 rename。曾尝试双 root FD；真实 Linux 证明跨两个绑定挂载仍返回 EXDEV，已弃用。无复制未知数据、无增大文件卷；错误挂载视图会安全拒绝文件操作。

提交 `6f1aa0f` 的 [39265208484](https://github.com/isAliceYoung/fa/actions/runs/39265208484) 五项 CI 全部通过：18 项访客权限、10 项真实 Next WS/网络、23 项文件、4 项新增终端 UID 攻击、3 项 SSH 解析；实际启动 systemd 的 VM 首次与重启后各 9 项通过。未失败、未跳过。此门槛通过后才提交 PTY。

## PTY 控制平面（已实现，运行时验收单独记录）

- 两个固定、root 管理的 systemd 会话单元，worker 与 Bash 均为 wdterm，数据组 wdguest。每个会话独立 cgroup，统一放入有限额的父 slice；禁止对 cgroup 的写权限。
- 同 UID Bash 可以攻击本会话 worker，因此 worker 不拥有认证状态、文件事务状态或租约续期权。worker 异常退出由 systemd 清理整个 cgroup；即使 Bash 暂停或篡改 worker，独立 wdsvc 租约守卫仍能请求 systemd 停止该固定单元。
- wdsvc 的 polkit 权限仅允许 **stop** 两个固定访客单元；没有 start 权限。启动由 root 保护的 socket activation 触发。其他系统单元、StartTransientUnit、属性、unit 文件和重载均不授权；规则的正负行为必须实测。
- 租约守卫是独立的 wdsvc systemd 单元，使用无密钥环境、固定 Unix Socket、SO_PEERCRED 和单调时钟。只接收 renew，不接收 Cookie；EOF/错误/10 秒未续期撤销。PTY 通过 BindsTo 依赖守卫存活，守卫崩溃也由 PID 1 停止整个 PTY cgroup。它不与 Next 子进程一起被后端 KillMode 清理；后端死亡关闭连接，暂停则令租约过期。后端每批输入重新认证、排队有界，数据库异常立刻停止输入与续期。
- 2 会话、父 slice 192 MiB/64 tasks/100% CPU；每会话 96 MiB/32 tasks/75% CPU、128 FD，/tmp 16 MiB、/var/tmp 4 MiB；守卫各 24 MiB/4 tasks/10% CPU。输出回放 256 KiB、IPC/WS 高水位 512 KiB、每会话约 1 MiB/s 输出节流。阶段报告记录 1 GiB/2 vCPU VM 的真实峰值、fork/FD/内存耗尽、CPU 节流和双 PTY/文件并发测试；不把它扩大为完整 GUI 或低配机源码构建验收。
- noexec 保持，固定系统 Bash/Python 可解释用户脚本；直接执行访客卷上的 ELF 或脚本受 noexec 限制。noexec 不表示禁止所有解释执行；安全边界仍为 UID、DAC、NNP、受限挂载与 cgroup。

交互 worker 与 Bash 同 UID，所以该数据平面不承担授权；接受一条服务连接后即关闭 activation listener，避免 Bash 窃取监听 FD。worker 中的破坏只能影响其访客会话，不能变更服务侧租约或访问文件 Agent 私有状态。生产环境仍未启用新终端。当前 PTY 使用 PrivateNetwork/AF_UNIX，未开放出站网络；后续网络策略必须单独验收。

参考：[systemd D-Bus 权限](https://www.freedesktop.org/software/systemd/man/257/org.freedesktop.systemd1.html)、[systemd 进程回收](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)、[非递归绑定挂载](https://www.freedesktop.org/software/systemd/man/250/systemd.exec.html)。
