# 第三阶段 3A 实施与验证报告

日期：2026-10-19。开发分支：`feature/private-deploy-isolation`。第二阶段验收基线：`f62a8c0131dd5b11d3584ff2c5ebb1e5da32d2fd`。

**交付范围：第二阶段收尾、独立访客文件执行层、最小已认证文件 API、真实 Debian 权限测试与后续调用清单。到此停止，等待 3A 验收；没有开始 3B/3C 或生产迁移。**

`main` 与 `v0.1.0-beta.1` 保持 `5922e1408de91e56771f1ea0bf94a9df1b7028cd`。未连接生产 VPS、改变仓库可见性、生成生产 Deploy Key 或恢复自动更新。没有删除既有数据库、源码或用户数据。取消欢迎文件和示例图片的 `src/lib/sys.ts` 修改保持第二阶段版本。

## 1. 第二阶段收尾

- SSH 模板改为**顶层 sshd_config 物理末尾内联**，所有全局指令、Include 和其他 Match 必须在它之前，不放进 Debian 早期 `sshd_config.d`。不再使用 `Match all` 作为通用全局段恢复手段。实际 Debian sshd 测试分别检查管理员有效配置、早期 Include 首值优先、Match all 对管理员条件配置的影响。仅使用临时测试 host key，未安装 SSH 配置或创建 wdtunnel。
- root 源码引导遇任意遗留 `.source-*` 即阻断再次获取，不自动删除未知数据；中断的 `.key-*` 同样阻断再次生成。克隆前至少要求 1 GiB 可用空间，克隆/checkout 期间监控 512 MiB 磁盘余量、512 MiB 暂存大小和 100000 项上限，超限终止进程组并保留现场。这是监测性预算，不能代替硬配额；采样间可能短暂超限，外部写盘仍可能耗尽空间。
- 严格 GitHub SSH 主机密钥验证、root 私有 Git 身份、只读授权流程和原子无覆盖发布机制保留。真实 GitHub Deploy Key 授权测试仍未执行。
- `PHASE2_REPORT.md` 记录已核实的历史 CI（142/142、Linux 7/7、3/3 Job）；删除跟踪的机器结果 JSON，新结果改存 CI Artifact。本轮真实源码权限测试扩展到 9 项。

## 2. Linux 身份、目录与 IPC

详细协议见 [PHASE3A_PROTOCOL.md](PHASE3A_PROTOCOL.md)。

| 对象 | 实现与边界 |
|---|---|
| wdsvc | 动态分配非 root system UID、独立私有 GID，无附加组，锁定密码/nologin；Next.js 继续进行 Cookie/session 认证和数据库操作 |
| wdguest | 另一独立非 root system UID/GID，无附加组、无 sudo、无有效/允许/环境 capability，真实/有效/保存 UID/GID 均为访客；强制 NoNewPrivileges |
| 源码、Git、配置 | root 管理，必要时仅 wdsvc 组读；真实测试对实际源码、`.git/HEAD`、`.env` 去掉 other 权限，并另建 root/service 0600 测试秘密 |
| 访客 HOME | `/home/wdguest`，wdguest 私有，0700；新目录 0700、新文件 0600 |
| IPC | `/run/webdesktop-guest` root:wdsvc 0750，固定 `guest.sock` root:wdsvc 0660；root 创建监听 FD，访客继承，不允许访客替换 socket |
| IPC 身份 | Unix `SO_PEERCRED` 必须为 wdsvc；非通用命令代理，没有可选 socket/执行用户/服务文件路径 |
| 运行包 | 仅两个 Python 标准库文件，root 管理；不包含认证、数据库、真实配置、Deploy Key 或私有后端源码 |

Next.js 先执行现有 `denyDesktop`，认证通过后重建允许的 IPC 参数。不转发 Cookie、Authorization、页面 access、完整环境或真实凭据。访客响应也不能设置 Cookie、重定向或 CORS。backend 只读取 socket 元数据，不以服务权限读取文件内容。

路径限定在访客 HOME，通过目录 FD 与 Linux `openat2(BENEATH|NO_SYMLINKS|NO_MAGICLINKS)` 打开；不支持时拒绝启动。拒绝路径穿越和符号链接，普通文件必须 guest 所有且 `nlink=1`，设备/FIFO/socket 不可读取。先验证文件描述符再截断，避免校验前破坏文件。目录列表、文本、请求体、文件流和并发均有限额。

访客最多 4 工作线程、连接 10 秒；backend 空闲 10 秒/总计 30 秒。Node 流按字节背压，中止/超时/进程退出都关闭连接；IPC 不可用为 503，超时 504，流中断后不能重读。没有 wdsvc 本地文件回退。写入不保证事务性，磁盘/进程异常可能留下访客文件的部分内容。

初始化只支持显式 `setup-guest-layer.sh --lab`，仅供可丢弃 Debian 12；拒绝接管未知账号、目录或不同产物。未启动服务、未变更旧应用目录，也未修改 SSH。systemd 模板包含能力清空、只读系统、私有网络/临时目录、仅 Unix socket、隐藏服务路径、96 MiB/16 tasks 限制；**真实 booted systemd 的沙箱和重启恢复未执行**，不能把 unit 语法通过算成运行验收。

## 3. 已实现与暂未实现的 API

| 已实现 API | 访客能力 |
|---|---|
| GET `/api/sys/info?lite=1` | guest 身份/HOME/标准目录 |
| GET `/api/sys/fs` | HOME 内目录列表 |
| GET `/api/sys/fs?read=1` | 文本读取，2 MiB 上限 |
| GET `/api/sys/raw` | 普通文件流，100 MiB 上限，64 KiB 分块 |
| POST `/api/sys/fs` | 创建访客文件或目录，文本最多 5 MiB |
| PUT `/api/sys/fs` | 修改已有访客普通文件，文本最多 5 MiB |

其余 sys 文件 PATCH/DELETE、上传、ZIP 下载、进程结束、完整监控，以及 GUI/浏览器 HTTP API，在认证后返回明确 503 `GUEST_FEATURE_PENDING`。三个旧终端/GUI/浏览器 WebSocket 不再加载原执行实现，未认证 401、认证后 503。这样开发分支的这些桌面功能暂时不可用；不得通过重新导入旧库恢复同 UID 执行。根文件系统浏览同样尚未开放。

认证、管理员、云盘、设置、workspace 与数据库模块未迁到 guest。完整依赖清单见 [PHASE3A_INVENTORY.md](PHASE3A_INVENTORY.md)，覆盖 PTY、GUI/Chromium/Claude/PCManFM/Fcitx、极速浏览器、剪贴板、图片转换、上传下载、ZIP、进程监控与环境辅助脚本。

## 4. 实际验证与证据

最终代码验证提交为 `54410117c53c9a96d3c49a3ee88daf4660bde53f`，[Actions 39240653958](https://github.com/isAliceYoung/fa/actions/runs/39240653958)，四项 Job 全部 completed / success。此前绿色基线 `1ceb095` 的 [Actions 39239824294](https://github.com/isAliceYoung/fa/actions/runs/39239824294) 同样全部通过。后续仅归档本报告的文档提交不改变受测代码。测试 JSON 只放本地忽略目录或 CI Artifact，不长期提交机器结果。

已核实三个 Artifact 均成功生成且未过期：[常规 Ubuntu 结果](https://github.com/isAliceYoung/fa/actions/runs/39240653958/artifacts/12181311244)、[常规 Debian 与 9 项源码权限结果](https://github.com/isAliceYoung/fa/actions/runs/39240653958/artifacts/12181386268)、[18 项访客与 3 项 SSH 结果](https://github.com/isAliceYoung/fa/actions/runs/39240653958/artifacts/12180289966)。Artifact 有保留期；阶段报告保留运行号及人工核实结论，机器明细以对应 CI 为准。

| 环境/命令 | 退出码 | 通过 | 失败/错误 | 跳过 |
|---|---:|---:|---:|---:|
| Windows：`python tests/run_tests.py --json .test-results/regular-3a.json`，自动发现 Git Bash 5.3.9 | 0 | 150 | 0/0 | 0 |
| Debian/Ubuntu CI：`python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json`（各一次） | 0 | 各 150 | 0/0 | 0 |
| Debian：`python3 tests/linux_private_repo.py` | 0 | 9 | 0/0 | 0 |
| Debian：`python3 tests/linux_guest_layer.py`，真实 Next.js + PostgreSQL 15 + Linux 用户 | 0 | 18 | 0/0 | 0 |
| Debian：`python3 tests/linux_tunnel_parser.py`，真实 sshd 解析器 | 0 | 3 | 0/0 | 0 |
| Windows/CI：`npm run typecheck`、`npm run build` | 均 0 | 命令成功，不伪计单测数量 | 0 | 0 |
| CI：所有跟踪 shell 的 `bash -n`、Python compileall、Node 检查、`systemd-analyze verify`、两次 lab 初始化 | 均 0 | 命令成功，不伪计单测数量 | 0 | 0 |

Windows 首次完整构建因缺 DATABASE_URL 失败；补充仅构建用的虚拟测试 URL 后成功，**本机没有据此声称数据库运行通过**。CI 用真实 PostgreSQL 初始化 schema、构建、登录与读写。开发中首次 Debian 客户端默认 Accept-Encoding 与严格协议不匹配，以及两轮 SSH 负例预期不准确，均已修正并重新运行；不把失败尝试计成通过。SSH 结论以 Debian 实际有效配置为依据，不能笼统声称所有早期 Include 或 Match all 都产生语法错误。

18 项访客实测包括：真实不同 UID/私有组；agent 无秘密环境/能力；**以 wdguest 启动的实际进程直接读取、改写、复制 root/service 文件、实际 auth.ts、.env、.git/HEAD 均被内核 PermissionError 拒绝**；普通路径、绝对路径、`..`、symlink、hardlink 拒绝；正常创建/编辑/列目录/文本及 1 MiB 流成功；真实未登录或仅 Cookie 请求拒绝；正常桌面/管理员认证与 PostgreSQL 云盘功能通过；guest 不能连接受保护 socket 或向 backend 发信号；停 IPC 返回 503、SIGSTOP 超时 504、32 MiB 流传输中退出中止响应而不回退。

**未执行（不是跳过/通过）：** booted systemd 单元实际启动与重启；真实 wdtunnel 登录、转发/SFTP/Shell；GitHub 测试 Deploy Key 授权握手；1 GiB/10 GiB 限额 VPS 全量安装与压力；真实 PTY/GUI/Fcitx/Chromium/Electron 沙箱；所有生产验收与目录迁移。容器实测证明 Linux DAC/UID/IPC 边界，不能证明这些未执行项。

复现流程在 `.github/workflows/validate.yml`：可丢弃 Debian 12 容器内安装 Node 22.23.2、Python、OpenSSH、PostgreSQL 15；`npm ci`、生成测试 `.env`/DB/schema、build/typecheck；两次 lab 初始化和 unit verify；把真实工作区设为 root 管理、wdsvc 可读、guest 不可读，再执行上述 Linux 测试。不要在既有安装或生产机照抄该 CI 权限准备步骤。

## 5. 资源与低配兼容性

最终代码运行实测空闲 RSS：guest **20632 KiB（约 20.1 MiB）**，真实 Next.js **198612 KiB（约 194.0 MiB）**；两个访客运行文件共 **16981 bytes**。这是 CI 容器快照，不是峰值、PostgreSQL 合计或 1 GiB VPS 实测，以对应运行 Artifact 为准。

访客端没有 npm 包、额外 DB、浏览器或 root worker，常驻成本较小；systemd MemoryMax 96 MiB 和线程/流上限控制单服务增长。原 IPv4-only、Swap、自适应构建并发、PostgreSQL 恢复、service 与一键安装核心模块保持未修改，并经原 Shell 测试回归。最低磁盘预算属于源码获取前置保护；并未额外保留无限源码版本。

**尚不能保证 1 GiB RAM/10 GiB 磁盘运行完整桌面。** Next 构建、PostgreSQL、浏览器、GUI 和 Swap/依赖缓存仍需合并预算及峰值实测；guest 文件累计写入无硬配额，3A 请求限额不等于磁盘总量限额。多个访客共享同一 wdguest 仍可互相读取/修改访客数据。

## 6. 进入 3B 的依赖和停止点

1. 用户验收 3A；保持现有未迁移执行入口关闭，不能回退同 UID。
2. 在可启动 systemd 的一次性 Debian 环境验证 socket 激活、沙箱、异常恢复和重启；完成后端服务受保护源码/配置与可写缓存的实际部署布局评审，生产切换仍留第四阶段。
3. wdsvc/wdguest 的 nologin/锁定密码不能证明 SSH 转发被禁止；未来开放 SSH/网络前需有效配置明确禁止其 SSH 认证/转发，仅审核过的 wdtunnel 可转发，并实测管理员与隧道。
4. 为完整文件/上传/ZIP/PTY 建立 guest 协议、取消/背压、磁盘配额/大小/时间预算、会话撤销与恢复语义，不能复制完整 Next 后端或环境到访客包。
5. GUI/浏览器需要重新审查网络、D-Bus、X11、原生依赖及 Chromium/Electron 沙箱；不能直接放宽 3A 沙箱后宣称安全。实际 Fcitx 和应用兼容测试属于后续阶段。

## 7. 文件清单（相对第二阶段验收提交）

新增：

- `guest-runtime/agent.py`、`guest-runtime/secure_fs.py`
- `src/lib/guest-api.ts`、`src/lib/guest-client.mjs`、`src/lib/guest-client.d.mts`、`src/lib/guest-ws.ts`
- `tests/guest_client_checks.mjs`、`tests/linux_guest_layer.py`、`tests/linux_tunnel_parser.py`、`tests/test_guest_contract.py`
- `webdesktop-deploy/scripts/setup-guest-layer.sh`
- `webdesktop-deploy/templates/webdesktop-guest.service`、`webdesktop-deploy/templates/webdesktop-guest.socket`
- `webdesktop-deploy/PHASE3A_PROTOCOL.md`、`webdesktop-deploy/PHASE3A_INVENTORY.md`、`webdesktop-deploy/PHASE3A_REPORT.md`

修改：

- `.github/workflows/validate.yml`、`.gitignore`、`src/instrumentation.ts`
- `src/app/api/sys/{fs,raw,info,upload,download,kill}/route.ts`（6 个文件）
- `src/app/api/browser/route.ts`、`src/app/api/browser/[sid]/route.ts`、`src/app/api/browser/[sid]/{frame,input,meta}/route.ts`（5 个文件）
- `src/app/api/gui/route.ts`、`src/app/api/gui/{apps,clipboard,icon,shortcut}/route.ts`、`src/app/api/gui/[sid]/route.ts`、`src/app/api/gui/[sid]/{clipboard,drop}/route.ts`（8 个文件）
- `tests/linux_private_repo.py`、`tests/run_tests.py`、`tests/test_private_repo.py`、`tests/test_tunnel_design.py`
- `webdesktop-deploy/scripts/private_repo.py`、`webdesktop-deploy/templates/sshd-wdtunnel.conf.example`
- `webdesktop-deploy/README.md`、`webdesktop-deploy/PRIVATE_DEPLOY.md`、`webdesktop-deploy/ISOLATION_PLAN.md`、`webdesktop-deploy/PHASE2_REPORT.md`

停止跟踪：`webdesktop-deploy/test-results-phase2.json`，由 CI Artifact 替代。没有删除应用源码、数据库或访客数据。
