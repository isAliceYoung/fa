# 3A+ 与 3B 首个文件里程碑交付

日期：2026-10-19。分支 `feature/private-deploy-isolation`。验收起点 `54da753`。本轮只完成 3A+ 和 3B 文件功能；PTY 是设计/接口准备，3C 和第四阶段未实施。最终 CI 的提交、运行号和统计见后补证据段，不把开发中的失败或未运行项计成通过。

## 1. 提交边界与 3A+ 门槛

- `a7e6a59`：显式 WebSocket 公共入口、真实握手测试、Debian systemd VM 测试。
- `6113c9d`：修正实际 systemd 的 RestrictSUIDSGID/openat2 冲突；[39247166692](https://github.com/isAliceYoung/fa/actions/runs/39247166692) 五项 Job 全绿。真实 Next WS 7/7，systemd 首次和 reboot 后各 7/7，均退出 0、无失败/错误/跳过。
- `d929beb`：归档通过门槛的证据，随后才开始 3B。
- `72f46fb`：独立 guest 文件事务/上传/ZIP 和有内核容量限制的实验访客卷。
- `de23d01`：修正 openat2 元数据查询的 O_PATH 标志组合；加入真实 systemd socket 文件事务。
- `997d89b`、`6f335d2`：上传 413 分类、持久发布意图、显式 SSH 身份拒绝及安全测试。
- `d1c1e65`：浏览器上传控制器的断点查询、丢失响应恢复与取消按钮；实际 HTTP 测试。

3A+ 细节及失败历史见 [PHASE3APLUS_REPORT.md](PHASE3APLUS_REPORT.md)。最终源码和机读结果以对应提交的 Artifact 为准，不提交测试结果 JSON。

## 2. 当前 Linux 与 IPC 边界

wdsvc、wdguest 是不同非 root system UID/GID，无共享敏感附加组。Next 仍做真实 Cookie/页面 session 认证及 PostgreSQL 操作；只有通过认证才向固定 Unix socket 发操作。guest 只运行 root 管理的 `agent.py`、`secure_fs.py`、`file_ops.py`，没有认证/数据库模块、后端源码、服务秘密或完整 npm 依赖。额外 root 只读 `ssh-denied` 文件是公开角色策略标记，内容仅账号名。

固定 `/run/webdesktop-guest/guest.sock` 仍 root:wdsvc 0660，父目录 0750；guest 继承 systemd listener FD，SO_PEERCRED 只接受 wdsvc。backend 只读取固定 socket 元数据，不打开用户指定文件，不传 Cookie/Authorization/access/完整环境，故障没有本地文件 fallback。

保留 `/v1/*` 固定 URI，info 的 protocol=2 表明新增文件操作与上传二进制分帧能力。JSON 有固定字段和长度；只有 `/v1/upload` 接受固定长度或严格 chunked 二进制，拒绝扩展、trailer、重复 framing 字段及超限数据。不是任意命令/路径代理。

路径始终锚定 HOME 目录 FD，openat2 使用 BENEATH、NO_SYMLINKS、NO_MAGICLINKS、NO_XDEV。拒绝穿越、符号链接、硬链接文件、特殊设备及非 guest 所有目标；跨文件系统拒绝并保留源，不回退复制后删除。`.wd-*` 是事务保留命名，API 不访问；HOME 与 Desktop/Documents 等固定目录不可删除/移动。

## 3. 恢复的文件 API

| 外部 API（全部先认证） | guest 操作与行为 |
|---|---|
| GET `/api/sys/info?lite=1` | guest 身份、HOME 和标准目录 |
| GET `/api/sys/fs`、`?read=1` | 列目录/文本读取；列表新增 storage.capacity/available/kernelBounded |
| POST/PUT `/api/sys/fs` | 创建/编辑；临时文件写入、fsync、原子发布，不先截断原文件 |
| PATCH `/api/sys/fs` | `copyPaths/destDir/cut` 复制或移动，`path/name` 重命名，`path/moveTo` 移动，`path/restore` 还原 |
| DELETE `/api/sys/fs` | 默认移回收站，`permanent=1` 永久删除，`emptyTrash=1` 清空回收站 |
| POST `/api/sys/upload?dir=&name=&id=&offset=[&done=1]` | 单文件/分块二进制上传；mkdir=1 建嵌套目录 |
| GET `/api/sys/upload?dir=&name=&id=` | 查询已提交 offset、complete、path，支持进程重启后的续传 |
| DELETE `/api/sys/upload?dir=&name=&id=` | 取消未完成数据，或释放已完成状态；**不会删除已发布文件** |
| GET `/api/sys/raw` | 单文件流；继续使用原安全路径 |
| GET `/api/sys/download` | 单文件下载，目录为 ZIP；访客进程在卷内生成后通过 FD 流出 |

浏览器继续用 8 MiB 分块；上传控制器只在本地保存“目录/名称/大小/修改时间 → 随机上传 ID”，不保存 Cookie 或服务凭据。重新选择同一未修改文件时查询服务器断点；网络丢失最终响应会查询状态，避免重复发布。取消按钮停止当前/队列发送，并显式取消已知上传。若文件已提交，取消只释放状态，保留已保存文件。

刷新后仍须按原认证设计重新登录，再重新选择文件；浏览器不偷偷读取本地文件。目录批量选择仍保留原顶层重名策略，不承诺整批刷新续传时对已完成文件自动去重。禁用浏览器持久存储时仍能当前页面上传/重试，但跨刷新恢复需要保留操作 ID。

## 4. 原子性、并发、取消与失败

- API 写操作使用单实例 flock 与线程锁，锁等待 2 秒后 409；正常文本编辑发布完整新文件，原文件在临时写失败时保留。发布前复核目标版本；不承诺与未来任意同 UID 程序的写入具有完整事务隔离。
- 复制整棵树先验证，再在访客卷暂存，fsync 文件/目录后无覆盖原子发布。已知并发变化拒绝。多源批次部分完成返回 409 + partial/paths/count，已完成目标和未完成源都保留。
- 移动使用 renameat2(NOREPLACE)，不覆盖同名文件；跨设备拒绝。删除不是整棵树事务：先隔离再删除，失败保留剩余项并返回 recovery 标识；磁盘满时显式永久删除可直接删除已经验证的目标以回收空间。没有未经请求的自动清理。
- 回收站保存 guest 范围内的原路径；还原重新验证路径，原位置冲突返回 409。元数据不是特权信任根，不能用于访问 HOME 外路径。
- 每块写入必须匹配已提交 offset；中断/超限回退到上一个提交点，不随意 truncate 客户端给出的偏移。发布意图在 rename 前持久化，重启可通过目标 inode 判断已完成；丢响应后先查询状态，不能盲目从 0 覆盖。
- ZIP 使用安全 FD 读取，拒绝链接、穿越、Windows 非便携路径和未知内部目录；条目、深度、明文和压缩体积有限额。生成完成后 unlink 临时档案并保持 FD，完成/取消关闭 FD 就释放磁盘，不把整个档案放内存。
- 64 KiB 流块与背压，取消向 IPC 传递；guest 连接 10 秒空闲、读上传约 20 秒检查时限、文件流 30 秒，backend 10 秒空闲/30 秒总计。JSON 请求体读取 10 秒超时 408。复制/ZIP/遍历有 15 秒检查预算；正常内核 IO 阻塞不能由 Python 时钟硬中断，backend 仍会在期限内断开且不降级。

## 5. 实际容量和资源限制

新实验环境使用预分配 **512 MiB ext4 loop 卷、16384 inode**，挂到新建的空 `/home/wdguest`，包含文件、回收站、上传状态、未完成块及 ZIP 暂存。nosuid/nodev/noexec，root 独占 backing image；guest 无法扩容/替换卷。卷配置和 systemd mount unit 在重启后恢复。文件 agent 拒绝无独立容量限制、无 nosuid 或无 root SSH 拒绝策略的启动，不退回普通大目录。

只提供显式 `setup-guest-volume.sh --lab`；拒绝未知 image/unit、非空 HOME、未知挂载，不迁移任何旧数据，不自动重新格式化或删除。至少 1 GiB 主机空闲才分配 512 MiB；现有一键安装器没有调用此脚本。新卷占 10 GiB VPS 约 5% 容量，是预留而不是扩容承诺。

单文件 100 MiB、块 16 MiB、文本 2 MiB、文本写 5 MiB；单目录 4000 项，递归 4000 项/32 层/100 MiB，最多 64 上传状态。完成后应释放状态；异常留下的状态/暂存仍受卷容量与 inode 限制，不自动清理未知数据。耗尽返回 507，用户可显式删除已知文件；不影响 root 源码、服务配置及 PostgreSQL 所在文件系统。

文件单元保留 96 MiB/16 tasks 和最多 4 工作线程；增加的代码仍为 Python 标准库，没有新服务进程或 npm 依赖。实测 RSS、cgroup MemoryCurrent 和 guest 包字节数以最终 Artifact 为准。**不能把空闲快照当峰值**；完整 Node+PostgreSQL+GUI 在 1 GiB 上的压力/Swap 组合仍未验收。批量高并发/磁盘故障可能触发限额或重启，应按状态恢复，不给 wdsvc 文件读写权限。

## 6. SSH、WebSocket 与未恢复入口

为防通过文件 API 写 authorized_keys 后另行进入 SSH，实验卷初始化增加 root 全局 `DenyUsers wdsvc wdguest`，只用于新实验环境。完整 sshd -t/-T 验证，保持管理员其他有效配置，拒绝接管未知旧 DenyUsers；验证完成后才 reload。它是全局指令的早期 Include，**不含 Match**，与 wdtunnel 必须顶层末尾内联的 Match 设计不同。真实 VM 用有效测试公钥分别尝试两个账号的转发登录，必须拒绝；管理员在前后和 reboot 后仍能连接。没有修改生产 SSH，wdtunnel 的生产配置仍未执行。

仍拒绝：三个 `/api/{term,gui,browser}/ws`，无认证 401、有效认证 503；全部 GUI/极速浏览器 HTTP、系统进程 kill、非 lite 系统监控、VNC 剪贴板粘贴。文件 API 只开放访客 HOME，整机根目录浏览尚未恢复。没有重新加载旧 `browser-ws.ts`、`term-ws.ts`、`gui.ts` 或启动 GUI/Chromium/Claude。

## 7. 测试命令和待补齐的最终证据

实际流程定义在 `.github/workflows/validate.yml`。常规 Shell 在 Windows 用真实 Git Bash、在 Debian/Ubuntu 用 Bash 执行。容器文件测试与 booted VM 分开，不用继承 FD 容器替代 systemd。只有可丢弃 CI 文件测试容器使用 privileged 以准备 loop 卷和注入系统调用故障；被测 agent 始终实际 wdguest/空能力/NNP，真正 unit 的能力边界在独立 VM 验证。

| 命令 | 验证范围 |
|---|---|
| `python tests/run_tests.py --json .test-results/regular-3b.json` | 150 项常规（本机） |
| `python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json` | 常规 Debian/Ubuntu 各一次 |
| `python3 tests/linux_private_repo.py` | 9 项真实 root/非 root 私有源码保护 |
| `python3 tests/linux_guest_layer.py` | 18 项真实 UID/权限、认证、DB、IPC 故障 |
| `python3 tests/linux_ws_runtime.py` | 7 项真实 TCP Upgrade 矩阵与生命周期 |
| `python3 tests/linux_files_runtime.py` | 文件、流、取消、断点、原子性、ZIP、真实 ENOSPC、真实进程崩溃 |
| `python3 tests/linux_tunnel_parser.py` | 3 项真实 Debian sshd 配置解析 |
| `python3 tests/run_systemd_vm.py` | VM 首次启动和 reboot 后的 systemctl/analyze/内核权限及实际 SSH |
| `npm run build`、`npm run typecheck`、`node tests/guest_client_checks.mjs` | 构建、类型、真实流对象大小与读取超时 |

文件测试包括在提交后元数据发布前用 strace 注入 SIGKILL（跟踪真实工作线程），真实服务重启后恢复文件且不重复发布；没有 Mock UID/认证/文件系统。浏览器上传控制器通过 Node 原生 File/fetch 对真实 Next 服务验证丢响应与取消；**实际浏览器点击、拖拽、视觉和多页面批量恢复未执行**。

未执行数量按类别明确列出：生产操作（全部禁止）；完整一键新 VPS 安装与迁移；PTY 实际执行；GUI/Chromium/Electron/Fcitx；完整桌面 1 GiB 压力；硬断电持久性/恶意同 UID 的完全事务隔离。它们不计通过或跳过。开发期间失败、取消、后续步骤未执行另行保留 CI 日志，最终成功记录不能抹掉失败历史。

## 8. 下一步 PTY 门槛

先验收本文件里程碑，再按 [PTY_GUI_NEXT_DESIGN.md](PTY_GUI_NEXT_DESIGN.md) 实现独立 guest PTY 包、输入/输出/resize/重连、短租约撤销、cgroup 全子进程清理、背压和持久存储/临时内存限额。当前 noexec 访客卷不等于可运行任意访客二进制，PTY 前需明确可执行文件位置或单独评审该挂载选项，不能放宽 nosuid/NNP、源码 DAC 或服务身份边界。GUI 另设单元与网络/X11/D-Bus 策略；不取消整个文件 agent 沙箱。

保持 main/Beta、生产 VPS、仓库可见性、生产 Deploy Key 不变；自动更新未恢复。IPv4-only、Swap、PostgreSQL 首次恢复及原 service 安装核心脚本未修改，原 Shell 回归继续跑。取消欢迎文件/示例图片的 sys.ts 保持验收版本。没有开始第四阶段一键安装衔接或旧目录迁移。

## 9. 本轮文件清单

3A+ 新增：`server.mjs`、`src/app/api/guest/ws-reject/route.ts`、`tests/{ws_runtime_entry.mjs,linux_ws_runtime.py,run_systemd_vm.py,debian_systemd_vm.sh,linux_systemd_runtime.py}`、`webdesktop-deploy/{PHASE3APLUS_REPORT.md,PTY_GUI_NEXT_DESIGN.md}`。

3B 新增：`guest-runtime/file_ops.py`、`tests/linux_files_runtime.py`、`src/components/desktop/{upload-file.mjs,upload-file.d.mts}`、`webdesktop-deploy/scripts/{setup-guest-volume.sh,setup-guest-ssh-deny.sh}`、本报告。

修改：`.github/workflows/validate.yml`、`package.json`、`scripts/start.sh`、`guest-runtime/{agent.py,secure_fs.py}`、`src/instrumentation.ts`、`src/db/index.ts`、`src/lib/{guest-ws.ts,guest-api.ts,guest-client.mjs,guest-client.d.mts}`、`src/app/api/sys/{upload,download}/route.ts`、`src/components/desktop/paste.tsx`、`tests/{guest_client_checks.mjs,linux_guest_layer.py,run_tests.py,test_guest_contract.py}`、`webdesktop-deploy/{README.md,PHASE3A_PROTOCOL.md,PHASE3A_INVENTORY.md}`、`webdesktop-deploy/scripts/setup-guest-layer.sh`、`webdesktop-deploy/templates/webdesktop-guest.service`。
