# Phase 3C-C1：双向纯文字剪贴板 — 实施与验收报告

- 分支：`feature/CC`（只在此分支开发和提交；`feature/private-deploy-isolation` 保持 `d962567` 未动）
- 起点：D1-R2 报告提交 `e67ee80`
- **受测固定 SHA：`a46a40d0273af3bd5fa2c0528602b67a7cd7588e`**
- **结论：PARTIAL**。C1 功能和安全边界在全部 11 个真实 VM 配置上通过（重启前 164/165、重启后 22/22）。但固定 SHA 上的必需回归没有全部通过：D1 下载回归在 4 个安装配置中偶发失败，Validate 的 1 核无 Swap GUI 任务有 1 项 Chromium profile 持久化断言失败。按验收标准“最终固定 SHA 的必需回归全部通过”，不能判 PASS。详见第 16–18 节。

---

## 1. 现有禁用剪贴板架构与迁移缺口

迁移前（D1-R2 / Phase 4A-R1）：

| 层 | 状态 |
|---|---|
| Xtigervnc | `-AcceptCutText=0 -SendCutText=0`，RFB 双向 CutText 关闭 |
| `rfb_input.py` `ClientInput` | 丢弃客户端 RFB type 6（ClientCutText） |
| 旧接口 `/api/gui/clipboard`、`/api/gui/[sid]/clipboard`、`/api/gui/[sid]/drop`、`/api/sys/fs` PATCH `pasteFromVnc` | 返回 503 `GUEST_FEATURE_PENDING` |
| 旧 `gui.ts` 高权限执行路径 | 已移除，未恢复 |

缺口：用户无法在本机浏览器与远程原生 GUI（Chromium、PCManFM 等）之间复制文字。

本次没有采用以下做法：
- 开放 RFB CutText：只支持 Latin-1；SendCutText 会在远程剪贴板一变化就自动推送；Extended Clipboard 解析复杂。
- 恢复旧接口或高权限执行路径。

## 2. 最终协议与数据流

新增一条**用户显式触发**的专用通道，复用已存在的 GUI 会话 IPC，不新增监听端口、进程或服务。

```
浏览器（两个按钮） --POST /api/gui/text-clipboard--> server.mjs（wdsvc，公网网关）
   --> gui-gateway.clipboard()：绑定当前 GUI 会话、重新认证与续租、单请求、限频
   --> 现有会话 IPC：{t:'clip',op:'read'|'write',n,text?}
   --> wdgui worker.py：独立线程运行 text_clipboard.handle()
   --> 固定 /usr/bin/xclip -selection clipboard（私有 X11 :100）
   <-- {t:'clip',n,ok,code,bytes?,text?}（只有成功的 read 带 text）
```

- 请求体只允许两种形状：`{sid,op:'read'}` 或 `{sid,op:'write',text}`。多一个键就返回 400（`src/lib/text-clipboard.mjs` `parseClipboardBody`）。
- 错误码固定：`EMPTY 404`、`NON_TEXT 415`、`TOO_LARGE 413`、`INVALID_UTF8/CONTROL_CHARACTERS/INVALID_REQUEST 400`、`BUSY/GUI_INACTIVE 409`、`RATE_LIMITED 429`、`TIMEOUT 504`、`UNAVAILABLE 503`。
- 网关严格校验 worker 的回复（`validReply`）：编号必须匹配；只有成功的 read 可以带 text；不允许多余字段。不合规时按协议违规关闭整个 GUI 会话。超时后迟到的那一条回复会被丢弃。

## 3. wdsvc 与 wdgui 的实际权限界限

| 身份 | C1 中能做什么 | 不能做什么 |
|---|---|---|
| wdsvc（网关） | 校验 HTTP 请求、校验 Cookie 和页面会话、通过已有 IPC 转发有界文本 | 不连接 X11，不运行 xclip，不读写文件，不执行命令（E2E 验证 wdsvc 被拒绝访问 X11 socket 和 Xauthority） |
| wdgui（worker） | 在自己的私有显示 `:100` 上运行固定参数的 `/usr/bin/xclip`，只操作 CLIPBOARD | 不接受路径、命令、显示号、MIME 类型等参数；不操作 PRIMARY |
| wdguest / wdterm | 无变化 | E2E 验证二者都不能连接 X11 socket、不能读取 Xauthority |

E2E 实测：xclip 进程所有者全部是 `wdgui`，cgroup 为 `webdesktop-gui@1.service`；Xtigervnc 仍为 `AcceptCutText=0`、`SendCutText=0`，且关闭 TCP 监听。

## 4. 本地浏览器 API 权限处理

- **粘贴文字到远程：** 用户点击按钮后调用 `navigator.clipboard.readText()`。浏览器拒绝或不支持时，显示手动文本框，用户自己粘贴后点“发送到远程”。
- **从远程复制文字：** 成功读取后调用 `navigator.clipboard.writeText()`。失败时显示一个已全选的只读文本框，用户手动 Ctrl+C。
- Clipboard API 需要安全上下文（HTTPS 或 localhost）；读取通常需要用户授权，各浏览器提示方式不同。C1 不尝试绕过浏览器权限。
- 不轮询剪贴板，不自动同步，不拦截全局 Ctrl+C/Ctrl+V，不影响 xterm.js 和文件管理器原有的复制粘贴。
- 剪贴板内容不写入 localStorage、sessionStorage、数据库或日志（E2E 检查了 storage 和 journal）。

## 5. X11 CLIPBOARD 的 UTF-8 支持与数据类型限制

- 只读写 `UTF8_STRING`，只操作 CLIPBOARD 选区。
- 读取前先查 `TARGETS`。只要包含以下任一类型就返回 `NON_TEXT`，即使同时提供了 UTF8_STRING：`text/uri-list`、`x-special/gnome-copied-files`、`x-special/mate-copied-files`、`x-special/nautilus-clipboard`、`application/x-kde-cutselection`、`application/x-qt-image`、`text/x-moz-url`、`application/x-moz-file`、`image/*`。只有 `text/html` 而没有 UTF8_STRING 时，也返回 `NON_TEXT`。
- 严格 UTF-8 解码。非法字节返回 `INVALID_UTF8`；控制字符只允许 TAB、LF、CR，其他（含 ESC、NUL、DEL、C1 控制符）返回 `CONTROL_CHARACTERS`。
- 浏览器侧校验 `isWellFormed()`，拒绝孤立代理项。

## 6. 长度、超时、频率和资源限制

| 项 | 值 |
|---|---|
| 文本上限 | 16384 字节 UTF-8，超出拒绝，**不截断** |
| HTTP 请求体 | 1–65536 字节，读取超时 5 秒 |
| 网关等待 worker | 4 秒（`TIMEOUT`） |
| xclip | 2 秒；读取输出最多 MAX_BYTES+1 字节 |
| 并发 | 每个 GUI 会话同时 1 个（`BUSY`）；全局在途 4 个（429） |
| 频率 | 每个会话 10 次 / 10 秒（`RATE_LIMITED`） |
| 常驻资源 | 无新增服务或端口。写入后保留 1 个 xclip 选区所有者进程（wdgui，计入现有 GUI cgroup 的 TasksMax） |

RAM/Swap/CPU/TasksMax/FD/cgroup、告警阈值、Chromium 沙箱、Socket/文件 DAC、guest 卷、认证协议、下载目录、profile、GUI 关闭顺序（包括 D1-R2 的 Chromium 正常关闭修复）均未修改。

## 7. 双向真实 GUI 正向测试

E2E：`tests/clipboard_bridge_ui.mjs`。宿主机真实 Chrome → noVNC → 一次性 VM 中的 wdgui X11；只用随机、非生产的测试文本。

| 用例 | 内容 |
|---|---|
| 网页 → 原生 Chromium | 简体、繁体、中英混排、emoji 和扩展区汉字、多行加 TAB、引号/HTML/`$(…)`/反引号/路径文本，粘贴到远程 Chromium 文本框，以窗口标题中的字符数和 SHA-256 前缀校验 |
| 16 KiB 边界 | 5461 个“汉”加 1 个 ASCII 字符，恰好 16384 字节，完整到达 |
| 原生 Chromium → 网页 | 远程页面文本 Ctrl+A、Ctrl+C，点“从远程复制文字”，本地剪贴板内容逐字一致 |
| PCManFM 地址栏双向 | 把含中文和繁体的路径粘贴到地址栏并跳转；先用接口覆盖远程剪贴板，再从地址栏 Ctrl+C 读回，确认读到的文字真正来自 PCManFM 的复制 |
| 重启后 | 真实重启 VM 后，PCManFM 双向复制粘贴仍然可用 |

## 8. 中文、Unicode 与边界测试

- 单元测试（Python 6 项、Node 7 项）覆盖：Unicode 原样往返、16384 字节恰好通过、超出时拒绝且不截断、非法 UTF-8 和孤立代理项、控制字符、TARGETS 拒绝列表、协议形状精确匹配、固定程序和选区。
- E2E 覆盖上述全部字符类型，以及 16 KiB 边界的真实 X11 往返。

## 9. 安全负向测试

| 用例 | 结果期望（均已通过） |
|---|---|
| 远程为 uri-list / gnome 复制文件 / PNG / 仅 HTML / 非法 UTF-8 / 控制字符 / 超长 | 分别返回 NON_TEXT / INVALID_UTF8 / CONTROL_CHARACTERS / TOO_LARGE，本地剪贴板不被改动 |
| 在 PCManFM 中复制文件 | NON_TEXT，不导出文件 |
| 本地超长、控制字符、空文本 | 被拒绝，远程剪贴板哈希不变 |
| 接口认证与形状 | 匿名请求 401；错误的页面会话 401；跨域 Origin 403；非 POST 405；超大请求体 413；错误 Content-Type 415；原始非法 UTF-8 400；带 path/display/mime/exec 的请求 400；他人 sid 409 |
| 并发与频率 | 并发 3 个请求得到 `[200,409,409]`；连续 12 次中第 11、12 次返回 429；窗口过后恢复 |
| 不执行、不当路径、不记日志、不落盘 | `$(touch …)`、反引号、`.env`、`/root/.ssh` 等字符串原样往返；受保护文件哈希不变；没有生成 C1-EXECUTED 哨兵文件；随机 token 不出现在 localStorage/sessionStorage 和 journal（token 通过 stdin 传入，避免 sudo 记录 argv 造成自匹配） |
| 旧接口 | 8 个旧剪贴板、拖放、启动接口仍然是 503 `GUEST_FEATURE_PENDING` |

## 10. 会话撤销与 GUI 生命周期

- 页面刷新：旧 sid 返回 409；新 GUI 会话使用新的 X server，旧剪贴板不带入。
- 关闭 GUI、Chromium 崩溃、后端重启：旧会话均返回 409，xclip 所有者进程被清理；崩溃后 Chromium 可以用新会话恢复；后端重启后可以重新打开并正常使用。
- 登录撤销或新设备登录：旧页面读写均返回 401。
- 网关 `stop()` 会立即以 `GUI_INACTIVE` 结束挂起的剪贴板请求；每次请求前还会 `verify(s)`（重新认证并续租）。

## 11. Chromium D1 回归情况

D1-R2 在 `7b6d34c` 通过。本阶段的 D1 测试结果（每个配置 29 项加重启后 2 项）：

- 门禁 3 个配置在固定 SHA 上全部 29/29 + 2/2。
- 安装矩阵中有 4 个配置失败（见第 16 节）。失败的配置和用例每轮都不同，类型有三种：首次导航停在 New Tab、空间耗尽用例的下载未完成、重启前后下载历史缺失。
- 判断为环境偶发而不是 C1 回归，依据如下：
  1. C1 的产品改动不涉及 Chromium 的导航、下载或关闭流程（worker 只在收到 `clip` 消息时启动线程；gateway 的关闭顺序没有变化）。
  2. 同一 SHA 在门禁和其他安装配置中全部通过，失败位置随机。
  3. D1-R2 阶段本地留存的证据里也有同类现象（`d1-r2-reset-1024-1-0-1` 的源站日志只有启动一行，没有任何下载请求）。
- 但这只是判断，没有用不含 C1 的基线跑同等次数来证明，所以本报告不把 D1 记为通过。
- 本阶段对 D1 测试脚本做了两处只增强稳健性的改动（`tests/download_bridge_ui.mjs`，8 行），断言未改：页面就绪标题没出现时，最多重新导航 3 次；激活下载链接前按一次 Escape，以关闭崩溃用例留下的“Restore pages”气泡。最后一轮的失败中，有一例截图里没有任何气泡（`staging 1024-1-0`），说明这两处改动没有解决全部偶发问题。

## 12. 1 GiB 有/无 Swap、2 GiB、4 GiB 结果（固定 SHA）

| 配置 | 类型 | D1（重启前 / 后） | C1（重启前 / 后） | 健康检查（2 次） | Job 结论 |
|---|---|---|---|---|---|
| 2048-2-1024 | 门禁 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| 1024-1-1024 | 门禁 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| 1024-1-0 | 门禁 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| staging 4096-4-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| staging 2048-2-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| isolated-4a 2048-2-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| isolated-4a 1024-1-1024 | 安装 | 29/29、2/2 | 15/15、2/2 | 40/0、40/0 | 通过 |
| staging-baseline 1024-1-1024（f195f90） | 基线 | 不运行 | 不运行 | 39/0、39/0 | 通过 |
| staging 1024-1-0 | 安装 | 0 过 1 败 28 未执行、0 过 1 败 | 15/15、2/2 | 40/0、40/0 | **失败** |
| staging 1024-1-1024 | 安装 | 28/29、1/2 | 14/15（原生 Chromium → 网页，测试导航超时）、2/2 | 40/0、40/0 | **失败** |
| isolated-4a 4096-4-1024 | 安装 | 28/29（空间耗尽用例下载未完成）、2/2 | 15/15、2/2 | 40/0、40/0 | **失败** |
| isolated-4a 1024-1-0 | 安装 | 4 过 1 败 24 未执行、2/2 | 15/15、2/2 | 40/0、40/0 | **失败** |

另外，isolated-4a 各配置的原有套件（12、22、10、5、4 项）全部通过；PTY、终端、输入法、文件管理器回归全部通过。

## 13. 构建、安装、重启与产物审计

- 运行时包：Artifact `staging-debian12-a46a40d…`（ID 12511709291，225,260,799 字节，zip SHA-256 `a7c32815cf590c70380d10bb75ef3591c977e2b30a023d2400585cb90158343b`）。全部 11 个使用它的 VM job 下载后都校验了同一个摘要。
- 打包清单：`staging_bundle.py` 的 roots 和 `validate.yml` 的后端 tar 列表都加入了 `src/lib/text-clipboard.mjs`。新增回归测试：沿 `server.mjs` 的相对 import 递归，要求每个模块都出现在两份清单中（去掉修复时测试会失败）。
- GUI 包：`setup-gui-layer.sh` 安装 `text_clipboard.py`；gateway 的包完整性检查和 `linux_gui_runtime.mjs` 的精确文件集合同步更新；xclip 原本就在 `install-gui.sh` 的基础包中，生产依赖没有增加。
- 测试专用的 `clipboard_vm.py`、观察器和夹具只在一次性 VM 中复制到 `/root`，不进入运行时包。
- 所有配置都完成了真实 VM 重启，重启后复测通过（见第 12 节）。
- 已知阻塞未改动：`staging_bundle.py` 和 `staging_install.py` 中的 `BRANCH = 'feature/private-deploy-isolation'` 常量，以及 SHA/bootstrap 校验均保持原样，因此从 `feature/CC` 走正式部署路径仍会被拦截。

## 14. 修改文件、受测代码 SHA 和报告 SHA

受测代码 SHA：`a46a40d0273af3bd5fa2c0528602b67a7cd7588e`。相对 `e67ee80`：19 个文件，+975/−19。

| 类别 | 文件 |
|---|---|
| 产品（新增） | `gui-runtime/text_clipboard.py`、`src/lib/text-clipboard.mjs` |
| 产品（修改） | `gui-runtime/worker.py`、`src/lib/gui-gateway.mjs`、`server.mjs`、`src/components/desktop/apps/basic-gui-app.tsx`、`webdesktop-deploy/scripts/setup-gui-layer.sh`、`webdesktop-deploy/scripts/staging_bundle.py`（仅增加一项 root） |
| 测试（新增） | `tests/clipboard_bridge_ui.mjs`、`tests/clipboard_vm.py`、`tests/test_text_clipboard.py`、`tests/text_clipboard_test.mjs` |
| 测试（修改） | `tests/download_bridge_driver.py`、`tests/download_bridge_origin.py`、`tests/download_bridge_ui.mjs`、`tests/linux_gui_runtime.mjs`、`tests/test_staging_bundle.py` |
| CI | `.github/workflows/staging.yml`（新增 `[c1-gate]`，门禁和安装超时 60 分钟）、`.github/workflows/validate.yml`（`[c1-gate]` 跳过重型 job；后端 tar 增加模块） |

提交序列：`aed1fd1` → `b32c53a` → `e42d09f` → `c26867f` → `14cce0e` → `bdff8fc` → `a46a40d`（固定），本报告单独提交（`[skip ci]`）。

## 15. CI Run 与 Artifact

| 提交 | Staging Run | Validate Run | 结果摘要 |
|---|---|---|---|
| aed1fd1 `[c1-gate]` | 39935737342 | 39935737287 | 门禁 3/3 失败：运行时包缺少 `text-clipboard.mjs`，服务无法启动 |
| b32c53a `[c1-gate]` | 39938746954 | 39938747208 | D1 29/29；C1 11/6/11 过，测试脚本问题；健康检查 browser@1 仍为 failed |
| e42d09f `[c1-gate]` | 39945555246 | 39945555200 | D1 全过；C1 14/15；PCManFM 按键时序问题、GUI 启动频率限制 |
| c26867f `[c1-gate]` | 39947954150 | 39947954159 | 2 个门禁全过；1024-1-1024 首次导航偶发失败 |
| 14cce0e | 39950881008 | 39950881068 | 门禁全过，安装 8/9；Validate 3 个 VM job 因 tar 缺少模块失败 |
| bdff8fc | 39969474159 | 39969474305 | Validate 全过，门禁全过，安装 7/9（D1 偶发） |
| **a46a40d（固定）** | **39978166929** | **39978166995** | 门禁 3/3，安装 5/9，Validate 7/8 |

链接：`https://github.com/isAliceYoung/fa/actions/runs/39978166929`、`https://github.com/isAliceYoung/fa/actions/runs/39978166995`。每个 VM job 都上传了证据 Artifact（`download-smoke-evidence-*`、`staging-install-evidence-*`、`isolated-install-evidence-*`、`gui-desktop-one-core-swap-*`），其中包括 `clipboard-bridge.json`、`clipboard-reboot.json`、`download-*.json`、失败截图、journal 和健康检查结果。证据中不含剪贴板文本、密码或令牌（失败详情中的随机 token 和密码已替换为占位符）。

## 16. Passed / Failed / Skipped / Not executed（固定 SHA）

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| C1 E2E 重启前（11 个配置 × 15） | 164 | 1 | 0 | 0 |
| C1 E2E 重启后（11 × 2） | 22 | 0 | 0 | 0 |
| D1 重启前（11 × 29） | 263 | 4 | 0 | 52 |
| D1 重启后（11 × 2） | 19 | 2 | 0 | 1 |
| 健康检查（12 个配置 × 2 次；基线每次 39 项） | 958 | 0 | 0 | 144 |
| Validate 任务 | 7 | 1 | 0 | 0 |
| 安装矩阵 job | 5 | 4 | 0 | 0 |
| 门禁 job | 3 | 0 | 0 | 0 |
| 本地：`tests/run_tests.py` | 229 | 0 | 0 | 2 |
| 本地：`text_clipboard_test.mjs` / `test_text_clipboard.py` | 7 / 6 | 0 | 0 | 0 |
| 本地：`npx tsc --noEmit` | 通过 | — | — | — |

说明：
- 健康检查的“Not executed”是原有的固定未执行项：外部管理员 SSH、Claude 正式入口、旧版网页剪贴板项、文件拖放、多应用共享 GUI、正式 HTTPS 域名，与 C1 无关。其中旧版网页剪贴板项由 C1 E2E 覆盖，但健康检查本身未改。
- D1 的未执行项是 D1 套件在首个失败后停止造成的。
- 本地 `run_tests.py` 的 2 项未执行，是完整 VPS 安装和生产 VPS 测试（未授权）。
- Validate 失败项：`gui-systemd-debian12-swap-0` 中 `desktop_ui_runtime.mjs:67`“访客书签必须写入持久 profile”失败。该测试在 `bdff8fc` 上通过，测试本身与 C1 无关。

## 17. 剩余安全风险与未实现功能

- **D1 / GUI 回归的偶发失败：** 本轮没有解决，也没有量化基线失败率。在证明与 C1 无关之前，不应把本 SHA 当作发布候选。
- **xclip 选区所有者进程：** 远程写入后会保留一个 wdgui 进程持有选区，直到被其他所有者替换或 GUI 会话结束。资源受现有 cgroup 约束。
- **手动回退框：** 浏览器拒绝剪贴板权限时，文本会短暂出现在页面 DOM 中（不持久化），属于用户可见的正常交互。
- **浏览器兼容性：** 依赖 Clipboard API 和安全上下文；不同浏览器的权限提示不同，部分浏览器只能使用手动回退。
- **未实现：** 图片剪贴板、文件剪贴板、拖放、PRIMARY 选区、自动同步、多 GUI 并行、Playwright/Claude 正式入口。
- **生产部署阻塞：** `BRANCH` 常量指向 `feature/private-deploy-isolation`（第 13 节），未修改。

## 18. 是否达到 C1 PASS

**否，C1 状态为 PARTIAL。**

| 验收条件 | 结果 |
|---|---|
| 本地网页 → 原生 GUI 纯文本 | 满足 |
| 原生 GUI → 本地网页纯文本 | 满足（固定 SHA 上 11 个配置中 10 个通过，1 例为测试导航超时） |
| 中文及 Unicode | 满足 |
| 长度及数据类型限制 | 满足 |
| 用户主动触发、无后台读取 | 满足 |
| 双重认证与会话绑定 | 满足 |
| 登录撤销后不可访问 | 满足 |
| 不执行命令、不读额外文件 | 满足 |
| 旧的图片、文件、拖放入口继续关闭 | 满足 |
| 1 GiB 有/无 Swap 正常运行 | C1 满足；D1 有偶发失败 |
| D1-R2 下载与 Chromium 生命周期回归保持通过 | **未满足**（固定 SHA 上 4 个安装配置失败） |
| Phase 4A-R1 权限、部署和恢复机制不退化 | 满足（isolated 原有套件全部通过、健康检查 40/0、真实重启通过） |
| 最终固定 SHA 的必需回归全部通过 | **未满足** |

C1 PARTIAL 不代表 Phase 3C 完成。下一步建议：先用不含 C1 的 D1-R2 最终代码，在同一矩阵上测出 D1 和 GUI profile 用例的基线偶发率。如果基线同样失败，就单独立项稳定这些测试（导航、空间耗尽、History/Bookmark 刷盘），然后在新的固定 SHA 上重跑完整回归，再把 C1 升级为 PASS。
