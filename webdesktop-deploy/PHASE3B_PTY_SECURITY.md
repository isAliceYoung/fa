# 3B-PTY 安全收尾与实施门槛

当前文件记录开发中的设计与已取得的证据；不是 PTY 完成报告。Bash 入口仍关闭。仅在可丢弃 Debian 12 环境运行，未迁移生产目录。

## 已实测的同 UID 风险

提交 `b1f1a9f` 的 Actions [39263248434](https://github.com/isAliceYoung/fa/actions/runs/39263248434) 的 `guest-3a-linux-results` Artifact 包含 `webdesktop-same-uid-baseline.json`：与真实文件 Agent 相同 UID 的独立 Python 进程能够 SIGSTOP/SIGCONT Agent，读取 `/proc/PID/fd`、`environ`，读取并改写 Agent 私有事务目录中的测试记录。ptrace attach 返回 EPERM，不能将这一项限制误认为同 UID 安全边界。

该轮新增 WebSocket 组 9 通过、1 错误（测试环境缺少 `ss`），文件组因前置失败未执行。补齐 iproute2 后，提交 `01d02e9` 的 [39263591835](https://github.com/isAliceYoung/fa/actions/runs/39263591835) 五项 CI 全部成功。不得把首次失败或后续身份改动的待验证结果计为通过。

## 网络边界

`npm start` 与 `scripts/start.sh` 共用字面 IP 校验。默认 127.0.0.1，非 loopback 显式地址输出暴露提示；不接受域名、端口混入地址、空白和 IPv6 zone。保留 IPv4 SSH 本地转发。

Next 内部 HTTP 使用服务 HOME 内 `.webdesktop-http/next.sock`；目录服务 UID 0700、socket 0600。没有内部 TCP 回退。只回收经 inode 校验的同 UID 死 socket，活动实例或未知文件拒绝启动。真实 Next 测试检查进程仅有一个 loopback TCP listener，并验证 wdguest/nobody 对内部 HTTP 与 Upgrade 的连接被内核拒绝。崩溃后重新启动继续验证认证拒绝路径。

## 文件 Agent 与终端身份决定

保留文件 Agent 的 `wdguest` UID，新增交互 UID `wdterm`。`wdsvc` 仍独占服务身份、认证与数据库。wdterm 账号自身有独立 UID/私有组；执行单元只使用无敏感读取权限的 wdguest 数据组，不加入 wdsvc 或 sudo 组。新账号也由实际 sshd DenyUsers 禁止 SSH，包括仅请求端口转发的登录。

普通数据目录使用 setgid 2770、普通新文件 0660。文件模块保留 openat2、单链接检查、事务及容量限制，仅把可接收的数据所有者扩为这两个固定 UID。服务代码不接受客户端 UID。终端可访问普通数据；用户自行移除组权限的文件会安全拒绝，不越权修复。

事务目录 `.wd-state` 仍在原 512 MiB 文件系统上，所有者 wdguest、权限严格 0700，并由 root 建立绑定挂载。wdterm 无法读取、修改或重命名该挂载点。Agent 分别持有数据 root FD 和固定事务 root FD，各自的 openat2 仍禁止跨挂载与符号链接；两个 FD 属于相同设备，保留原子 rename 与 ENOSPC 语义。无复制未知数据、无增大文件卷。启动检查缺失挂载/错误权限即拒绝服务。

新增真实不同 UID 测试必须验证 signal/ptrace/proc/FD/socket/state 拒绝，以及 Bash 身份创建文件和 Agent 修改、再次由 Bash 修改的互通。真实 systemd 单元启动、重启、ENOSPC、SSH 及现有文件回归均通过后才开放 PTY。

## 拟实施的 PTY 控制平面（仍须实现与验证）

- 两个固定、root 管理的 systemd 会话单元，worker 与 Bash 均为 wdterm，数据组 wdguest。每个会话独立 cgroup，统一放入有限额的父 slice；禁止对 cgroup 的写权限。
- 同 UID Bash 可以攻击本会话 worker，因此 worker 不拥有认证状态、文件事务状态或租约续期权。worker 异常退出由 systemd 清理整个 cgroup；即使 Bash 暂停或篡改 worker，独立 wdsvc 租约守卫仍能请求 systemd 停止该固定单元。
- wdsvc 的控制权限必须只限于两个固定访客单元的 start/stop，拒绝修改属性、创建瞬态单元、控制其他单元、重载配置或执行任意命令。拟通过最小 polkit 规则校验 unit 与 verb；这不是授予访客 sudo。需在实际 Debian 12 systemd/polkit 中验证正负用例。
- 租约守卫使用无密钥白名单环境、独立进程和单调时钟；只从后端私有管道接收 renew/stop，不接收 Cookie。后端死亡/暂停或数据库失败不能无限延长 Bash 生命。Backend 的输入转发也必须重新认证、限制排队量并安全失败。
- 暂定 2 会话、父 slice 192 MiB/64 tasks/100% CPU；每会话 96 MiB/32 tasks/75% CPU、128 FD、16 MiB 临时目录；输出回放 256 KiB、有限 IPC/WS 缓冲。数值需要峰值与耗尽实测，不能只凭配置宣称 1 GiB VPS 验收。
- noexec 保持，固定系统 Bash/Python 可解释用户脚本；直接执行访客卷上的 ELF 或脚本受 noexec 限制。noexec 不表示禁止所有解释执行；安全边界仍为 UID、DAC、NNP、受限挂载与 cgroup。

参考：[systemd D-Bus 权限](https://www.freedesktop.org/software/systemd/man/257/org.freedesktop.systemd1.html)、[systemd 进程回收](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)。尚未实施的 polkit、PTY、WebSocket 正向连接、终端 UI、峰值资源、3C 详细设计不得记为已完成。
