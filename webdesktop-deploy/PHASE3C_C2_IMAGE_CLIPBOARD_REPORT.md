# Phase 3C-C2：双向 PNG 图片剪贴板 — 实施与验收报告

- 分支：`feature/CC`（只在此分支开发和提交；`feature/private-deploy-isolation`、`main`、`v0.1.0-beta.1` 均未改动）
- 起点：C1 报告提交 `3eca337`
- **受测固定 SHA：`b827791b189b68b0a75699aa5e403c3f0f79803c`**（代码树 `960a92cdef29333961b7b33eab0f39792b61ee70`）
- **结论：C2 功能通过、C2 安全通过；完整集成验收未通过，C2 判 PARTIAL**
  - 在固定 SHA 上，C2 在全部 11 个真实 VM 配置中的结果为：重启前 143/143、重启后 11/11。
  - 同一 SHA 的完整回归中，D1 在 3 个 1 GiB 安装配置失败（重启前 3 项、重启后 2 项），C1 失败 1 项，Validate 1 核无 Swap 的书签断言失败。
  - 这些失败类型都与 C1 固定 SHA 的历史失败相同，但都没有证明与 C2 无关。详见第 11、16、18 节。

---

## 1. 实施前状态与迁移缺口

| 层 | C1 之后的状态 |
|---|---|
| Xtigervnc | `-AcceptCutText=0 -SendCutText=0`，RFB CutText 双向关闭（本次不变） |
| C1 文字通道 | `/api/gui/text-clipboard`，16 KiB UTF-8 文本，用户显式触发 |
| 图片 | 无任何通道；旧接口 `/api/gui/clipboard`、`/api/gui/[sid]/clipboard`、`/api/gui/[sid]/drop` 仍返回 503 |
| 旧高权限 GUI API、任意路径图片导入 | 已移除，本次未恢复 |

## 2. 最终协议与数据流

新增一条用户显式触发的 PNG 通道。它复用现有 GUI 会话 IPC，不新增监听端口、服务或常驻进程。

```
本地浏览器                wdsvc（server.mjs + gui-gateway.mjs）            wdgui（worker.py + image_clipboard.py）
──────────               ─────────────────────────────────────            ─────────────────────────────────────
点击“粘贴图片到远程”
navigator.clipboard.read
→ image/png Blob
POST /api/gui/image-clipboard?sid=&op=write
Content-Type: image/png  → 查询/Origin/凭据/类型/长度 → 全局 1 张在途
                         → checkTerminalAuth（读任何图片字节之前）
                         → 读取 body（≤1000 KiB，60 s）→ pngInfo 结构+CRC
                         → 会话剪贴板槽（文字/图片共用，BUSY）+ 频率
                         → {t:'img',op:'write',n,size,sha256}
                         → {t:'imgc',n,i,d} × ⌈size/16 KiB⌉（逐块，前一块排空后再写）
                                                                           → 顺序、长度、SHA-256 校验
                                                                           → png_info：结构+CRC+完整解压+尺寸
                                                                           → /usr/bin/xclip -selection clipboard -in -target image/png（只写 stdin）
                         ← {t:'img',n,ok,code}
← 200 {code:'OK'}

点击“从远程复制图片”
POST ...&op=read（无 body）→ 同样的前置检查 → {t:'img',op:'read',n}
                                                                           → xclip -out -target TARGETS（≤4 KiB）
                                                                           → 拒绝文件/URI 目标、非 PNG、空
                                                                           → xclip -out -target image/png（≤1000 KiB，15 s）
                                                                           → png_info 完整校验
                         ← {t:'img',n,ok,code,size,sha256} + imgc 分块
                         → 校验顺序/长度/SHA-256 → pngInfo
← 200 image/png（attachment，CSP default-src 'none'，nosniff，no-store）
navigator.clipboard.write(ClipboardItem image/png)
```

- 图片不进入 C1 的 16 KiB 文字协议；IPC 帧上限（64 KiB）和输入背压阈值（64 KiB）没有调大。
- 每个 IPC 块 16 KiB 原始数据（base64 后约 22 KiB），只有在 socket 写缓冲为 0 时才写下一块。因此键盘/鼠标输入与图片共用的 IPC 缓冲最多约 22 KiB 图片数据，不会触发输入的 fail-closed 背压。
- 失败、超时、取消时，gateway 发送 `{t:'img',op:'cancel',n}`；worker 丢弃上传/下载状态，迟到的块被忽略。任何协议违规都会关闭本次 IPC 会话而不是继续解析。
- 图片字节只存在于请求内存、IPC 和 xclip 的 stdin/stdout 中。没有临时文件，没有数据库写入，没有缓存。

## 3. wdsvc 与 wdgui 的权限界限

| 能力 | wdsvc | wdgui |
|---|---|---|
| 认证、会话绑定、Origin、频率、并发 | 是 | 否 |
| PNG 结构与 CRC 检查（不解压） | 是 | 是 |
| 完整 zlib 解压与原始尺寸核对 | 否 | 是（流式，`max_length` 256 KiB） |
| 访问 X11 / 运行 xclip | **否**（E2E 断言 wdsvc、wdguest、wdterm 连接 X11 和读取 Xauthority 均被拒绝） | 是，固定 `/usr/bin/xclip`、固定参数、固定 display |
| 读写文件 | 否 | 否（只用管道） |
| 客户端可控参数 | 只有 `sid`、`op=read|write` 和 PNG 字节 | 无（路径、命令、程序、display、MIME 都不接受） |

查询字符串必须恰好是 `sid` 和 `op` 两个键。带 `path`、`display`、重复键或其他 `op` 的请求返回 400。

## 4. 本地浏览器权限处理

- 两个按钮：`粘贴图片到远程`（`gui-img-paste`）、`从远程复制图片`（`gui-img-copy`）。只在用户点击时调用 Clipboard API，不轮询，不自动同步。
- 读取本地剪贴板被拒绝或不可用时，显示一个粘贴区（`gui-img-dropzone`），用户在其中按 Ctrl+V 触发 `paste` 事件。
- 写入本地剪贴板被拒绝时，显示远程图片的预览（`gui-img-preview`，`blob:` URL），用户可自行另存或复制。关闭时撤销 object URL。
- 本地先检查 PNG 签名和 1000 KiB 上限，不合格的图片不会发送。
- 界面标注：“剪贴板仅限手动纯文字和 PNG 图片 · 未开放拖拽”。

## 5. 支持格式与数据类型限制

- 只接受静态 PNG（`image/png`）。允许所有合法颜色类型/位深组合，以及隔行（Adam7）。
- 拒绝：
  - APNG（`acTL`/`fcTL`/`fdAT`）→ `UNSUPPORTED_FORMAT`；
  - JPEG、GIF 等其他格式 → `UNSUPPORTED_FORMAT`（远程只有其他图片类型时）或 `INVALID_IMAGE`（签名不符）；
  - 文件列表或 URI 目标（`text/uri-list`、`x-special/gnome-copied-files` 等 7 种）→ `UNSUPPORTED_FORMAT`。这样图片通道不能被当作文件读取接口；
  - 纯文字剪贴板 → `NON_IMAGE`；空剪贴板 → `EMPTY`；
  - CRC 错误、截断、尾部多余数据、未知关键块、块顺序错误、调色板缺失、解压长度不符 → `INVALID_IMAGE`。

## 6. 大小、超时、频率和资源限制

| 项 | 值 | 位置 |
|---|---|---|
| PNG 文件大小 | ≤ 1000 KiB（1,024,000 字节） | 前端、server、gateway、worker 四处一致；两份单元测试断言 < 1 MiB |
| 单边 / 总像素 | ≤ 4096 / ≤ 3840×2160 | gateway 与 worker |
| 解压后原始数据 | 必须等于按 IHDR 计算的精确长度 | worker |
| IPC 块 | 16 KiB 原始数据，逐块等待排空 | gateway |
| 全局在途图片 | 1（第二个返回 429 `RATE_LIMITED`） | server |
| 每会话剪贴板操作 | 文字与图片共用一个槽，同时只允许 1 个（409 `BUSY`） | gateway/worker |
| 每会话频率 | 60 s 内 6 次图片操作 | gateway |
| 超时 | body 60 s；桥回复 45 s；每次 xclip 15 s；上传空闲 20 s | server/gateway/worker |

### 1 MiB 限制的实测依据（INCR）

最初的上限是 4 MiB。第一、二轮 2 GiB VM 上，≥1 MiB 的读取超过 120 s 仍未返回，并连带导致后续文字操作超时。复现与二分结果：

- xclip 0.13 拥有 ≥ 1,048,576 字节的选区时使用 X11 INCR 分段协议。只要有两个客户端同时请求（远程 Chromium、Fcitx5 与桥自身都可能请求），INCR 拥有者就会挂起。
- 本地（与 VM 相同的 xclip 0.13-2、TigerVNC 1.12.0）二分：1,047,552 字节正常，1,048,576 字节挂起。
- VM 内基准（每个配置都重测，见第 12 节）：64 KiB 约 17–19 ms，512 KiB 约 20–23 ms，1000 KiB 约 23–27 ms，1 MiB 在 30 s 超时。

因此上限定为 1000 KiB，留出余量；没有为了更大的图片去修改 xclip 或增加自定义 X11 选区实现。

## 7. 双向真实 GUI 正向测试（三级验证）

每个方向分别记录三件事：

1. **远程复制**：X11 CLIPBOARD 确实持有 `image/png`（root 观察器 `image-hash` 读取 SHA-256）。
2. **桥**：API 传输的字节与 X11 中的 SHA-256 一致。
3. **应用**：真实应用粘贴后解码出相同的像素（像素摘要）。

| 方向 | 测试图 | 结果 |
|---|---|---|
| 网页 → 远程 Chromium | alpha 96×64（alpha ∈ {0,128,255}）、wide 320×40 | 远程 X11 SHA 一致；远程 Chromium 粘贴页面解码出的像素摘要与本地一致 |
| 远程 Chromium → 网页 | alpha 96×64、small 24×16 | 远程 Chromium `clipboard.write` 后，API 字节与 X11 SHA 一致；本地 Chromium 读取后的像素摘要一致 |
| 重启后 | 桥往返 | 字节一致 |

测试图由 `putImageData` 生成，通道值只取 0/255，alpha 取 0/128/255。已在本地验证它们经过剪贴板往返后像素稳定。

## 8. 边界测试

- 恰好 1,024,000 字节的 PNG 和 3840×2160 PNG：接受，往返字节一致。
- 多 1 字节、单边 4097、像素数超 1：拒绝（413 `TOO_LARGE`）。
- 远程侧预置的超限图片（1,040,000 字节、5000×10）：读取被拒绝（`TOO_LARGE`），且不会进入 INCR 区间。

## 9. 安全负向测试

| 场景 | 结果 |
|---|---|
| 远程为文字、JPEG、APNG、GNOME 文件列表、URI 列表、损坏 PNG、超限 PNG、超大尺寸 | 分别返回 `NON_IMAGE` / `UNSUPPORTED_FORMAT` / `INVALID_IMAGE` / `TOO_LARGE`，本地剪贴板未被修改（哨兵图片保持不变） |
| 本地非法图片（非 PNG、损坏、超限） | 前端或服务端拒绝，远程剪贴板未被触碰 |
| 未登录、错误会话头、错误 Origin、错误 sid、多余查询键、`op=exec`、`path=`、`display=`、读请求带 body、GET | 401 / 401 / 403 / 409 / 400 / 400 / 400 / 400 / 400 / 405 |
| 认证前的大 body | 不读取，回复后 `Connection: close` 并销毁连接（修复了第一轮的 keep-alive 污染） |
| 并发 | 全局第二张图 429；同会话图片传输期间文字操作 409；连续 7 次图片操作：6 次 200，第 7 次 429 |
| 日志与存储 | journal 中没有 PNG 数据（搜索 `iVBORw0KGgo`）；localStorage/sessionStorage 中没有；受保护文件哈希不变；没有可执行文件被运行 |
| 进程身份 | xclip 只以 `wdgui` 身份运行在 `webdesktop-gui@1.service` 中，最多 1 个（选区拥有者） |
| VNC | 每个 Xvnc 的 AcceptCutText、SendCutText 和 TCP 均为关闭 |
| X11 隔离 | wdsvc、wdguest、wdterm 连接 X11 和读取 Xauthority 均被拒绝 |

## 10. 会话撤销与 GUI 生命周期

- 关闭 GUI 后，旧 sid 的图片请求返回 409，并且没有 xclip 进程残留。
- 重新打开 GUI 时会得到新的 X server，剪贴板为空（`EMPTY`）。新会话的写入和读取正常。
- 退出登录后，旧凭据的图片请求返回 401。
- 失败或取消不会关闭 GUI 会话。worker 的作业异常统一转为 `UNAVAILABLE`。
- 资源：每轮 3 次 1200×800 往返之后，GUI 单元任务数 6→7（xclip 选区拥有者），fd 63→68；关闭 GUI 后 xclip 进程为 0。

## 11. C1 文字剪贴板与 D1 回归

完整回归（`workflow_dispatch`，固定 SHA `b827791`，Staging 40024316520）在每个 VM 配置上按 D1 → C1 → C2 顺序运行三个套件。

**C1 文字剪贴板：** 11 个配置中，重启前 164/165，重启后 22/22。

- 唯一失败：isolated-4a 1024-1-1024 的“native Chromium to web text”。远程 Chromium 中按 Ctrl+A、Ctrl+C 后，10 s 内 X11 CLIPBOARD 没有出现该文本（`clipboard_bridge_ui.mjs:155`）。
- C1 固定 SHA `a46a40d` 上，同一用例也在一个 1 GiB 配置失败过（staging 1024-1-1024，当时是测试导航超时）。
- C1 套件在 C2 之前运行，失败发生时本 VM 上还没有任何图片操作。图片桥空闲时，worker 中只多了一次 `img.poll()` 调用。
- 这只说明 C2 运行时未参与，**不能证明**与 C2 代码无关。

**D1 下载：** 11 个配置中，重启前 316/319，重启后 20/22。

| 配置 | 失败用例 | 失败内容 |
|---|---|---|
| staging 1024-1-1024 | refresh Agent backend restart and profile persistence；重启后 profile and download permissions survive reboot | `history` 中没有已完成的下载记录（`download_bridge_ui.mjs:277`、`:194`） |
| staging 1024-1-0 | 同上两项 | 同上 |
| isolated-4a 1024-1-0 | browser crash preserves completed data and recovers | Bounded observable state timeout（`download_bridge_ui.mjs:153`） |

- 下载历史缺失在 C1 固定 SHA 上已出现过（staging 1024-1-1024）。崩溃用例的超时是新出现的位置，但仍属于 D1 的崩溃恢复流程。
- 三个失败都发生在 D1 套件内，当时 C1、C2 套件都还没有运行。
- C2 没有修改 Chromium、下载、Profile 或关闭顺序相关的代码。
- 同样，这些都**不能证明**与 C2 无关。

**与 C1 固定 SHA 的对比（同样 9 个安装配置 + 3 个门禁）：**

| 项 | C1 `a46a40d` | C2 `b827791` |
|---|---|---|
| 门禁 job | 3/3 | 3/3 |
| 安装矩阵 job | 5/9 | 5/9 |
| Validate job | 7/8（swap-0 书签） | 7/8（swap-0 书签，同一断言） |
| D1 重启前 | 263 过、4 败、52 未执行 | 316 过、3 败、0 未执行 |
| D1 重启后 | 19 过、2 败 | 20 过、2 败 |
| C1 重启前 / 后 | 164/165、22/22 | 164/165、22/22 |

C2 没有让历史失败变多，但失败集中在 1 GiB 配置，这一点仍需要单独调查。

## 12. 1 GiB 有/无 Swap、2 GiB 结果

C2 门禁（`[c2-gate]`，Staging 40021278627，固定 SHA）：

| 配置 | C1（重启前 / 后） | C2（重启前 / 后） | 1000 KiB 读 | 1 MiB 读 | 最低 MemAvailable | 最大 Swap 使用 | webdesktop.slice 峰值 | OOM |
|---|---|---|---|---|---|---|---|---|
| 2048-2-1024 | 15/0、2/0 | 13/0、1/0 | 23 ms | 30 s 超时 | 1188 MiB | 2 MiB | 897 MiB | 0 |
| 1024-1-1024 | 15/0、2/0 | 13/0、1/0 | 27 ms | 30 s 超时 | 303 MiB | 70 MiB | 746 MiB | 0 |
| 1024-1-0 | 15/0、2/0 | 13/0、1/0 | 27 ms | 30 s 超时 | 252 MiB | 0 | 731 MiB | 0 |

- 1 GiB 配置中 `webdesktop-exec.slice` 有 `memory.high` 回收事件（有 Swap 1421 次，无 Swap 937 次），没有 `max`/`oom`/`oom_kill` 事件。这是执行切片原有的 memory.high 限制，C1 同配置的运行中更多（2023 次、1616 次），不是 C2 引入的。
- RAM、Swap、CPU、TasksMax 限制都没有改动。
- 并发证据（三个配置一致）：`parallel_images` = [200, 429]，busy probe 中文字请求全部为 409（38 / 42 / 54 次），`image_burst` = 6×200 + 429。

完整回归（固定 SHA，Staging 40024316520），每格为“重启前 / 重启后”：

| 配置 | 类型 | D1 | C1 | C2 | 健康检查（2 次） | Job |
|---|---|---|---|---|---|---|
| 2048-2-1024 | 门禁 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| 1024-1-1024 | 门禁 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| 1024-1-0 | 门禁 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| staging 4096-4-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| staging 2048-2-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| isolated-4a 4096-4-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| isolated-4a 2048-2-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | 通过 |
| staging-baseline 1024-1-1024（f195f90） | 基线 | 不运行 | 不运行 | 不运行 | 39/0、39/0 | 通过 |
| staging 1024-1-1024 | 安装 | 28/29、1/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | **失败** |
| staging 1024-1-0 | 安装 | 28/29、1/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | **失败** |
| isolated-4a 1024-1-1024 | 安装 | 29/29、2/2 | **14/15**、2/2 | 13/13、1/1 | 40/0、40/0 | **失败** |
| isolated-4a 1024-1-0 | 安装 | 28/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | **失败** |

- 其他原有套件全部通过：
  - isolated-4a 各配置：preflight 12、artifact-audit 12、modes 22、resource-runtime 10、failure-recovery 1、staging-ui 5、terminal-ui 4。
  - staging 各配置：resource-runtime 10、staging-ui 5。
- Validate（40024321806）：8 个 job 中 7 个通过（scripts、debian12-scripts、build、systemd-debian12-vm、guest-3a-debian12、pty-systemd、gui-systemd swap-1024）。`gui-systemd-debian12-swap-0` 失败：`desktop_ui_runtime.mjs:67`“Actual guest bookmark must be flushed to its persistent profile”，和 C1 固定 SHA 上的失败完全相同。

## 13. 构建、安装、重启与产物审计

- `setup-gui-layer.sh` 安装 `image_clipboard.py`。gateway 的包完整性检查列表和 `linux_gui_runtime.mjs` 的精确文件集合同步更新。
- `staging_bundle.py` 的 roots 和 `validate.yml` 的后端 tar 列表都加入了 `src/lib/image-clipboard.mjs`（C1 的打包回归测试会检查这一点）。
- 生产依赖没有增加：xclip 在 C1 之前就已安装。
- 测试专用的 `clipboard_vm.py`（新增图片夹具、`image-hash`、`xclip-bench`、`gui-resources`）和观察器只复制到一次性 VM 的 `/root`，不进入运行时包。
- 所有门禁配置都完成了真实重启，重启后复测通过。
- 未改动：`BRANCH` 常量（`staging_bundle.py:17`、`staging_install.py:22`、`layer-context.sh:24`）以及 SHA/bootstrap 校验。从 `feature/CC` 走正式部署路径仍会被拦截；方案见 `PHASE4B_UPDATE_DESIGN.md`。

## 14. 修改文件与 SHA

受测代码 SHA：`b827791b189b68b0a75699aa5e403c3f0f79803c`。相对 `3eca337`：20 个文件，+1725/−36。

| 类别 | 文件 |
|---|---|
| 产品（新增） | `gui-runtime/image_clipboard.py`、`src/lib/image-clipboard.mjs` |
| 产品（修改） | `gui-runtime/worker.py`、`src/lib/gui-gateway.mjs`、`server.mjs`、`src/components/desktop/apps/basic-gui-app.tsx`、`webdesktop-deploy/scripts/setup-gui-layer.sh`、`webdesktop-deploy/scripts/staging_bundle.py` |
| 测试（新增） | `tests/image_clipboard_ui.mjs`、`tests/image_clipboard_test.mjs`、`tests/image_ipc_integration_test.mjs`、`tests/test_image_clipboard.py`、`tests/test_acceptance_harness.py` |
| 测试（修改） | `tests/clipboard_vm.py`、`tests/download_bridge_driver.py`、`tests/download_bridge_origin.py`、`tests/download_bridge_vm.py`、`tests/linux_gui_runtime.mjs` |
| CI | `.github/workflows/staging.yml`（`[c2-focus]`、`[c2-gate]`、`WD_SUITES`、超时 90 分钟）、`.github/workflows/validate.yml`（两处运行新单元/集成测试；标签跳过重型 job） |

提交序列：`6c8877f` → `732c5e3` → `44bf063` → `c7253e9` → `b827791`（固定）。三份文档另行单独提交（`[skip ci]`）。

## 15. CI Run 与 Artifact

| 提交 | Staging Run | Validate Run | 结果摘要 |
|---|---|---|---|
| 6c8877f（2 GiB） | 40002125138 | 40002125183 | C2 9/4，重启后 0/1：认证前拒绝导致 keep-alive 连接被污染（ECONNRESET）；权限用例撞频率限制；重启后测试源站不存在；4 MiB 读取超时 |
| 732c5e3（2 GiB） | 40007540809 | 40007540815 | C2 9/4，重启后 1/0：基准显示 ≥1 MiB 挂起 → 定位 INCR；一个用例缺少导航 |
| 44bf063 `[c2-focus]` | 40015690352 | 40015690029 | C2 12/1，重启后 1/0：上限改为 1000 KiB 后传输只需几毫秒，两个 `Promise.all` 上传不再重叠（测试计时问题） |
| c7253e9 `[c2-gate]` | 40018169556 | 40018169668 | 3 个门禁 C1 15/0+2/0、C2 13/0+1/0 全过，但驱动把 3600 s 传给只接受 ≤1800 s 的指标监视器，收尾失败（测试工具 bug） |
| **b827791 `[c2-gate]`** | **40021278627** | **40021278668** | 3 个门禁全部通过，Validate 通过 |
| **b827791 完整回归（workflow_dispatch）** | **40024316520** | **40024321806** | 门禁 3/3；安装 5/9（4 个 1 GiB 配置：D1×3、C1×1）；Validate 7/8（swap-0 书签）；C2 在全部 11 个配置通过 |

说明：第一次手动触发完整回归时，Windows 桥在返回前断开，导致重复触发；新运行通过并发组取消了先前的运行，我又取消了重复的那一对（40024004769、40024010063、40024047263、40024052947，均为 cancelled，没有任何测试结果）。之后只触发了一次。

Artifact：每个 VM job 上传 `download-smoke-evidence-*` / `staging-install-evidence-*` / `isolated-install-evidence-*`，其中包括 `image-clipboard.json`、`image-clipboard-reboot.json`、`clipboard-bridge.json`、`clipboard-reboot.json`、`download-*.json`、`download-metrics.jsonl`、journal 和健康检查。证据只保留大小、哈希、状态码和像素摘要，不含图片内容、密码或令牌。

## 16. Passed / Failed / Skipped / Not executed

固定 SHA `b827791`：

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| **C2 E2E 重启前（11 个配置 × 13）** | **143** | **0** | 0 | 0 |
| **C2 E2E 重启后（11 × 1）** | **11** | **0** | 0 | 0 |
| C1 E2E 重启前（11 × 15） | 164 | 1 | 0 | 0 |
| C1 E2E 重启后（11 × 2） | 22 | 0 | 0 | 0 |
| D1 重启前（11 × 29） | 316 | 3 | 0 | 0 |
| D1 重启后（11 × 2） | 20 | 2 | 0 | 0 |
| 健康检查（12 个配置 × 2 次；基线每次 39 项） | 958 | 0 | 0 | 144 |
| 其他原有 VM 套件（isolated-4a 4×66、staging 4×15、基线 resource-runtime 4） | 328 | 0 | 0 | 0 |
| Validate GUI 套件（gui-ui 2×4；desktop-ui swap-1024 5，swap-0 2 过 1 败 2 未执行） | 15 | 1 | 0 | 2 |
| 门禁 job | 3 | 0 | 0 | 0 |
| 安装矩阵 job | 5 | 4 | 0 | 0 |
| Validate job | 7 | 1 | 0 | 0 |
| 本地：`tests/run_tests.py` | 247 | 0 | 0 | 2 |
| 本地：`test_image_clipboard.py` / `image_clipboard_test.mjs` / `image_ipc_integration_test.mjs` | 17 / 8 / 5 | 0 | 0 | 0 |
| 本地：`npx tsc --noEmit` | 通过 | — | — | — |

C2 门禁（`[c2-gate]`，同一 SHA，Staging 40021278627）另有：C1 45/0、6/0；C2 39/0、3/0；健康检查 6 次全部 40/0。

说明：
- 健康检查的 Not executed 是原有固定未执行项（外部管理员 SSH、Claude 正式入口、旧网页剪贴板、文件拖放、多应用 GUI、正式 HTTPS 域名），与 C2 无关。
- 本地 `run_tests.py` 的 2 项未执行：完整 VPS 安装、生产 VPS 测试（未授权）。
- swap-0 desktop-ui 的 2 项未执行，是该套件在书签断言失败后停止造成的。

## 17. 新发现问题、历史遗留问题与剩余风险

新发现并已修复（本阶段内）：

1. **xclip INCR 并发挂起**（≥1 MiB）：上限降为 1000 KiB，并在各层加了断言。这是 xclip/X11 层面的限制，没有被绕过。如果将来需要更大的图片，需要单独设计（例如不经过 INCR 的分段方案），不在 C2 范围内。
2. 认证前拒绝时连接被复用 → 改为 `Connection: close` 并销毁连接。
3. worker 作业异常可能导致会话关闭 → 统一转为 `UNAVAILABLE`。
4. 测试工具：并发用例计时、监视器时长上限（已加静态测试防回归）。

剩余风险：

- **xclip 选区拥有者进程**：远程写入后会保留 1 个 wdgui 进程持有选区，直到被替换或 GUI 结束。受现有 cgroup 约束。
- **浏览器兼容性**：依赖异步 Clipboard API 的 `image/png` 支持；部分浏览器只能使用粘贴区/预览回退。
- **1 MiB 以上的图片**：设计上拒绝（`TOO_LARGE`），用户会看到明确提示。

历史遗留（首次出现于 C2 之前，继续延期）：见 `PROJECT_ROADMAP_STATUS.md` 的延期问题表（D1 偶发导航、Restore 气泡、空间耗尽用例、下载历史、Validate 书签持久化）。

本轮在这些问题上的新增证据：
- 下载历史缺失又出现在 2 个 1 GiB 安装配置。
- D1 崩溃用例在 isolated 1024-1-0 超时。
- C1“原生 Chromium → 网页”在 isolated 1024-1-1024 失败。
- Validate swap-0 书签断言在两个固定 SHA 上都失败。

所有失败都集中在 1 GiB 配置。这些问题都按原样记录，没有删除断言、没有改成 skipped、没有计为通过，也没有无限重跑：完整回归只运行了一次，此前因误操作被取消的几次运行没有产生任何测试结果。

## 18. 结论

1. **C2 功能测试：通过。**
   - 两个方向的三级验证（X11、桥字节、真实应用像素）、1000 KiB 和 3840×2160 边界、权限回退、文字与图片交替、关闭重开、注销撤销、重启后往返，在 11 个真实 VM 配置上全部通过（143/143、11/11）。
   - 文字剪贴板仍可用，C1 失败的那一项是历史上出现过的 1 GiB 用例。
2. **C2 安全测试：通过。**
   - 认证、会话绑定、请求形状、Origin、格式/文件目标拒绝、大小/尺寸/像素限制、并发和频率、日志与存储无图片数据、仅 wdgui 运行 xclip、wdsvc/wdguest/wdterm 无 X11 访问、VNC CutText 保持关闭、GUI 关闭后无桥进程残留，全部通过。
   - 没有改动 RAM/Swap/CPU/TasksMax，也没有放宽文件、socket、namespace 或 seccomp 限制。
3. **完整集成验收：未通过。**
   - 固定 SHA 上，安装矩阵 5/9、Validate 7/8。
   - 失败在 D1（3+2 项）、C1（1 项）和 Validate 书签（1 项），全部在 1 GiB 配置，类型与 C1 固定 SHA 的历史失败相同。
   - 按“最终固定 SHA 的必需回归全部通过”的标准，**C2 判 PARTIAL**，与 C1 相同，不判 PASS。

**是否可以进入真实测试 VPS 阶段：** 目前不进入。
- C2 本身已经具备在测试 VPS 上人工验证的条件。
- 但有三个阻碍：
  1. 完整集成验收未通过，1 GiB 配置的偶发失败还没有基线和根因；
  2. `BRANCH` 常量仍会拦截从 `feature/CC` 部署，需要先实施 PHASE4B_UPDATE_DESIGN.md 中的显式允许发布来源；
  3. 当前没有访问真实 VPS 的授权。
- 满足这三点后，可以在一台测试 VPS 上人工取证。

**下一个子任务（建议，未自动开始）：**
- **1 GiB 偶发失败的基线与根因调查。** 先用不含 C1/C2 的基线 SHA 在 1 GiB 安装配置上跑同等次数，比较失败率。这是让 C1、C2 从 PARTIAL 变为 PASS 的前提。
- 之后按路线图进入 Phase 4B-1（发布来源与签名清单），或 C3 文件剪贴板。
- 具体顺序由用户决定。
