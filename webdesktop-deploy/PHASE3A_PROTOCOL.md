# 3A：访客文件协议、身份及实验环境

本文保留 3A 验收时点的协议快照。当前 3A+ 的真实 systemd/WS 结果见 [PHASE3APLUS_REPORT.md](PHASE3APLUS_REPORT.md)，3B 文件扩展、容量与 SSH 策略见 [PHASE3B_FILES_REPORT.md](PHASE3B_FILES_REPORT.md)；以下“未迁移/未实测”不代表后续阶段仍未执行。

本阶段只提供最小文件能力。真实终端、GUI、极速浏览器、剪贴板及完整文件操作未迁移，开发分支将这些入口明确关闭；不允许后端以旧 UID 执行。不在生产机运行实验初始化脚本。

## 身份及文件权限

| 项目 | 权限边界 |
|---|---|
| wdsvc | 独立 system UID、独立私有主组、无附加组、nologin，HOME `/var/lib/webdesktop` 0700；后端认证/数据库仍在此身份 |
| wdguest | 另一 system UID 和私有主组、无附加组、nologin，HOME `/home/wdguest` 0700；只运行访客文件服务 |
| IPC 父目录 | `/run/webdesktop-guest`，root:wdsvc 0750，不能由访客创建或替换 |
| IPC socket | 固定 `/run/webdesktop-guest/guest.sock`，root:wdsvc 0660，由 systemd 创建，guest 从监听 FD 继承；后端不能选择别的目标 |
| 访客运行包 | `/usr/local/lib/webdesktop-guest` root:root 0755，仅 `agent.py`、`secure_fs.py` 两个 0644 文件；无认证、DB、配置、Git 或后端产物 |
| 应用与秘密 | root 管理，wdsvc 仅必要读取；Linux 测试把真实后端源码、`.git`、`.env` 设为 root:wdsvc，去掉 other 权限 |

运行服务验证当前确是 wdguest、服务与访客 UID/GID 均不同且非 0，拒绝附加组或活动 capability，调用 prctl 强制 NoNewPrivileges。systemd 模板再清空 capability，使用只读系统、私有临时目录/网络、仅 AF_UNIX、隐藏后端目录和限制资源。

实验脚本 `scripts/setup-guest-layer.sh --lab` 仅供可丢弃 Debian 12，记录自己创建的账号；未知已有账号/目录/产物不会被自动接管，不更改旧应用账号或数据所有权，不启动/启用服务、不配置 sshd。systemd 模板可以在实验机经核验后启动；CI 容器只验证 unit 语法，并用相同 UID、socket 所属权限和继承 FD 的方式运行实际服务，没有宣称测试了已启动的 systemd 沙箱。

## 固定协议 v1

Unix Socket 上使用单请求、关闭连接的 HTTP/1.1。服务端通过 SO_PEERCRED 只接收 wdsvc 的连接，不接受 root/guest 自选身份。无 TCP 监听，无通用命令、模块调用或 root 代理。

| IPC 操作 | 外部已认证 API | 内容 |
|---|---|---|
| GET `/v1/info` | GET `/api/sys/info?lite=1` | 访客身份、HOME 与标准目录信息 |
| GET `/v1/list?path=&hidden=` | GET `/api/sys/fs` | 列目录，最多 4000 项/2 MiB JSON |
| GET `/v1/read-text?path=` | GET `/api/sys/fs?read=1` | 文本读取，2 MiB 上限，保留 binary/tooLarge 字段 |
| GET `/v1/read?path=` | GET `/api/sys/raw` | 正常文件流，100 MiB 上限，64 KiB 分块 |
| POST `/v1/create` | POST `/api/sys/fs` | 创建普通文件/目录，文本最多 5 MiB |
| PUT `/v1/write` | PUT `/api/sys/fs` | 修改访客已有普通文件，先校验再截断，文本最多 5 MiB |

Next.js 的 denyDesktop 必须先验证 Cookie 与页面 session；失败即返回，不建立 IPC 连接。随后只重建允许的参数，绝不传 Cookie、Authorization、页面 access、管理员/数据库秘密或环境变量。guest-client 只接受固定 endpoint/method，检查 socket 元数据与服务身份；环境变量不能改变目标。

访客端也拒绝额外认证头、未知 query/body 字段，客户端不转发 Set-Cookie、Location、CORS 等响应头。文件响应带 CSP sandbox/nosniff/no-store。应用认证、云盘、设置、workspace、管理员及数据库 API 保持原实现；桌面认证不能成为管理员认证。

## 路径与故障处理

- 文件 API 范围为访客 HOME；`~`、合法相对路径或该 HOME 内的绝对路径可用。`/` 和其他系统目录在 3A 返回拒绝，保留文件系统图标但不假装可浏览整个主机。
- 拒绝 `..`、NUL、反斜杠、空组件；Linux openat2 以 HOME 目录 FD 为根，使用 BENEATH、NO_SYMLINKS、NO_MAGICLINKS。无 openat2 或不支持的架构安全失败，不降级。
- 所有读取/写入目标必须是 guest 拥有、nlink=1 的普通文件；目录项不解析 symlink，拒绝设备/FIFO/socket。写入不先 O_TRUNC，先 fstat 复核 owner/type/nlink 再截断。单文件修改可能在进程/磁盘异常时留下部分内容，3A 不承诺写事务。
- symlink/hardlink 的应用层检查之外，实际进程 UID 和目录 DAC 是独立的最终边界；即使在文件 API 之外执行普通用户程序，也无法读取 root/服务的 0600 文件。
- 列表/文本 JSON 有限额；写请求按实际流读取并限额，不只信任 Content-Length；访客端只接受长度固定的 JSON，不支持 chunked IPC 输入。目录递归、上传、ZIP 等不在 v1 内。
- 最多 4 个工作线程、每连接 10 秒超时；后端 IPC 10 秒空闲/30 秒总时限。文件流支持背压、取消与断线；头发出后若进程退出则中止响应流，不以服务权限重读文件。
- IPC 不存在、身份/权限错误或进程退出返回明确 GUEST_UNAVAILABLE / 503，超时 504。没有任何读写本地文件的回退。

## 测试与范围

实际 Debian 测试见 `tests/linux_guest_layer.py`：真实 wdsvc/wdguest、真实 Python agent 和 Unix socket、真实 Next.js 登录 Cookie/session、实际 PostgreSQL 和管理员认证。包括内核读写拒绝、保护文件链接攻击、正常文件编辑、未认证拒绝、IPC 停止不回退及后端不能被 guest 发信号。

`tests/linux_tunnel_parser.py` 使用临时 host key 和真实 Debian sshd 解析器，验证顶层末尾内联限制块不影响管理员、顶层 Match all 不能通用恢复全局段，以及早期 Include 的首值优先顺序。Include 的上下文返回行为必须以完整配置解析为准，不能据此推断所有 Include 都会报错。它不启动真实访客 SSH 隧道，不改任何运行中的 SSH。

本阶段不迁移生产目录，不接入旧一键安装器；源码获取仍是独立 root 流程。完整发布、缓存布局、备份/迁移及生产 systemd 接入留第四阶段。一个共享 guest 账号不是多租户隔离，不提供同 UID 之间的保密或磁盘配额隔离。

`nologin` 不能单独证明 SSH 转发被禁止。本阶段不更改运行中的 sshd；在后续 SSH/3B 实验环境开放网络前，需要以有效 sshd 规则明确拒绝 wdsvc/wdguest 的 SSH 认证/转发，仅允许经审核的 wdtunnel，且不能影响管理员。这里不能把锁定密码或 nologin 作为完整 SSH 隔离验收。
