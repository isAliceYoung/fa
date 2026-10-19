# PTY 与 GUI 后续设计（本轮不恢复执行入口）

## 已有调用链与不可复用的权限行为

`browser-ws.ts:installBrowserWs/attach` 当前旧实现依赖句柄扫描及监听器包装，分发 `/api/term/ws` 到 `term-ws.ts:termUpgrade`；后者通过 node-pty 在后端 UID 下执行 Bash，并调用 sys/ensureHome、proc-env/ensureBin、cleanEnv。`ensureBin` 可能安装依赖，cleanEnv 是黑名单；这些模块不能进入访客包。

旧终端协议：输入 `{t:"i",d}`、resize `{t:"r",c,r}`、kill `{t:"kill"}`；输出原始二进制和 exit JSON。现有边界为 10 sessions、256 KiB 回放、断线 120 秒、2 MiB/256 KiB 背压阈值、20 秒 ping、60 秒失联；5 秒认证续验。旧 kill 使用负 PID 的进程组信号，1.5 秒后 SIGKILL，存在 PID 重用和子进程脱离组的风险。不能直接恢复导入。

## 拟议独立终端包与协议

独立 `webdesktop-pty` 单元仍为 wdguest，独立 socket/root 创建监听 FD、SO_PEERCRED=wdsvc。发行包只有桥接代码、经固定版本构建的 node-pty、运行所需依赖及公开 Bash rc；禁止 auth/DB/Next 源码、完整 node_modules、服务环境与运行时 sudo/APT。安装与更新由 root 预先执行，访客不能修改运行代码。

backend 显式 WebSocket 入口完成真实 Cookie+页面 session 验证，只发送随机执行租约 ID（不是认证令牌）及受限操作。PTY worker 不接收 Cookie、数据库秘密或 AUTH_KEY。

| 操作 | 拟议字段及行为 |
|---|---|
| create | worker 生成不可猜 session ID，限定 guest HOME 内 cwd，cols 10..500、rows 2..200；固定 `/bin/bash -i` 和白名单环境；不接受客户端 UID、任意环境或启动 shell 命令字段 |
| attach | session + 同一租约 + 输出序号，需 backend 再认证；回放上限 256 KiB，超出发送 gap 标记，不伪装完整历史 |
| input/resize | 带序号的有界帧，输入最多 64 KiB/帧，resize 合并；写缓冲超限暂停读取/断开，禁止无限 Promise 排队 |
| output/ack | 原始字节帧和序号，IPC 与浏览器各自 512 KiB 高水位、128 KiB 低水位；同时暂停 PTY，不仅暂停前端发送 |
| detach | 网页刷新允许同租约 120 秒内恢复；租约续验期限先到则结束，超时清理进程及缓冲 |
| revoke/close | 立即关闭 WS，停止接收输入；租约撤销不可恢复；结束 session 的整个受控 cgroup，TERM/HUP 后 1.5 秒 KILL |
| exit/error | 有界状态，不包含服务路径/环境；worker 或 IPC 崩溃不转为 backend spawn，不自动重放输入 |

每 5 秒续验，租约最长 10 秒。明确认证无效立即撤销；数据库不可用时停止输入、关闭连接，不继续无限执行，租约过期清理。重连必须重新认证，旧 session 不能绑定新登录的租约。worker 重启全部旧 session 作废；backend 重启不重新赋予未知会话执行权。

进程归属必须由内核保证：wdguest、无附加敏感组、空能力、NoNewPrivileges、受保护的 root 管理运行代码。**不能仅靠终端 shell 的进程组保证 daemon/setsid 清理**；上线前须完成受控 cgroup 子树方案或 systemd 用户级瞬态 scope，由 root 预先配置有限委派，无系统服务控制权。若不能安全完成则 PTY 保持禁用。不得给 backend 任意 systemd-run/sudo 权限。

初始资源预算：并发 2 sessions、共享进程总数 64、总内存 192 MiB、CPU 100%、有限打开 FD；HOME 用内核容量限制，独立 `/tmp` tmpfs 限 32 MiB，不让 shell 在别处绕过持久磁盘预算。限制需要以真实 fork/setsid/磁盘耗尽测试校验，不视作已实现。

后续必须在真实 Next+Debian 下测试：Bash `id` 与 UID；中文/二进制/交互程序、Ctrl+C、resize；刷新续接与回放截断；撤销/数据库失败/租约过期；slow consumer/yes 背压；断 IPC/进程崩溃；fork/setsid 子进程清理和 PID 重用；读取源码/配置及向 wdsvc/root/PostgreSQL 发信号均失败；不同租约不能接管会话。当前三个 WS 仍拒绝，不创建 PTY。

## 3C 的独立 GUI/浏览器模型

文件 agent 继续保留 PrivateNetwork、AF_UNIX 和 96 MiB 限制，不承载浏览器。未来另建 `webdesktop-gui` 用户执行单元，可沿用 wdguest（共享访客数据的明确语义），或为 GUI 单独分配受限身份并显式共享访客目录；绝不加入 wdsvc 组。

GUI 单元单独拥有 Xvnc/Openbox、用户 D-Bus、Fcitx、Chromium/Claude/PCManFM 及只读最小运行包。只绑定访客 HOME、独立带容量限制的 runtime/tmpfs、X11/D-Bus Unix socket；源码/.git/.env/Deploy Key/服务配置仍被 DAC 和 mount namespace 隔离。图形 socket 不与 backend 或管理员的 DISPLAY/D-Bus 会话共用。

网络必须按功能单独配置：GUI/browser 允许必要 AF_INET/AF_INET6/AF_UNIX 与 DNS、对外访问，并用网络 namespace/过滤阻止连接 PostgreSQL、后端内部监听和其他本机管理服务。不能因为浏览器需要外网而取消整个服务沙箱；本机网络访问策略必须真实验证。保留系统 IPv4-only 政策，不偷偷重新启用 IPv6。

内存/TasksMax 需单独预算，96 MiB 不适用于浏览器。1 GiB VPS 先限制单 GUI 会话、单浏览器实例及并发标签，结合现有 Swap 测峰值；不能承诺当前 CI 空闲 RSS 代表完整桌面峰值。

Chromium/Electron 必须分别验证 user namespace、seccomp、SUID sandbox/helper、共享内存及 AppImage；先用正常 sandbox 参数启动并验证渲染器状态，不默认复制 `--no-sandbox`。实测失败须先明确必要的最小策略调整，不能给 GUI 读取后端或 root/sudo。图像解码、clipboard、拖拽、下载/ZIP、快捷方式与 profile 全部在该访客模型或文件 IPC 中完成。本轮不实施这些 GUI 单元。

参考：[Next.js 官方 custom server](https://nextjs.org/docs/app/guides/custom-server)；当前入口采用公开的 Next 自定义服务器 API，独立公共 Upgrade 入口与内部 HTTP listener，避免 Next 延迟注册监听器影响公共拒绝逻辑。
