# Phase 3C-D1-R2：1 GiB Chromium 异常下载与 GUI 恢复专项

本轮只处理 D1 在 1 GiB 上的异常下载与恢复失败，未进入 Phase 4B、Claude、Playwright、剪贴板/拖拽、多 GUI、正式 HTTPS 或真实 VPS 部署。按用户指示，本轮在新分支 `feature/CC` 开发（自 `feature/private-deploy-isolation` 的 `d962567` 分出）；原分支、`main`、Beta 均未改动。

## 1. 基线

| 项目 | 值 |
|---|---|
| 上一轮受测代码 / 运行包 | `fb6823b4349f1bff19ad3791cc5dd7c5c904a125`，内部 tar.gz SHA256 `9024feaa…5cc12` |
| 上一轮报告提交 | `d9625672f2e0832cc2d9156f30fb979eedad9fb9` |
| 上一轮 CI | Validate `39814561061`、Staging `39814561274` |
| 上一轮必需回归 | 2324 / 7 / 0 / 173（7 项失败全部在 1 GiB 下载专项） |
| 本轮分支 | `feature/CC`；原分支保持 `d962567` |

所有 Git 网络操作显式使用 `http.https://github.com.proxy=http://127.0.0.1:7897`。

## 2. 上一轮 7 项失败的逐项归类（原始 Artifact）

读取的原始证据：Staging `39814561274` 的 `staging-install-evidence-1024-1-1024`(12442687914)、`-1024-1-0`(12443092724)、`isolated-install-evidence-1024-1-1024`(12442379590)、`-1024-1-0`(12443586670) 的 `download-bridge.json`、截图、`journal.txt`、`download-metrics.jsonl`、`download-origin.log`。

| # | 环境 / 用例 | 失败阶段（原始栈） | 已证实根因 | 类别 |
|---|---|---|---|---|
| 1 | staging 1G+Swap 断网 | `nativeDownloadUI` 第 67 行内层 2 s 选区等待 | 观察器在下载页标题出现后立即按 `Escape`，1 vCPU 上中止了原生 WebUI 加载（见 §4）；内层超时穿透外层 10 s 循环，只做了一次尝试 | 测试观察器 |
| 2 | staging 1G 无 Swap 断网 | 同上 | 同上 | 测试观察器 |
| 3 | staging 1G 无 Swap 取消 | 同上（第一次 `nativeDownloadUI`） | 同上 | 测试观察器 |
| 4 | staging 1G 无 Swap inode | 同上；随后 GUI 重开 45 s 超时 | 同上；重开被网关以 `waiting_resources` 在 11 ms 内拒绝（卷 critical 需 30 s 连续正常才恢复），前端按设计不自动重试 | 测试观察器 + 预期资源保护拒绝 + 测试恢复未等待 |
| 5 | isolated 1G+Swap 崩溃 | 第 75 行等待测试页标题 30 s | 地址栏 Enter 未导航；截图有 `Restore pages?`，见 §5 | 产品生命周期缺陷 + 测试导航 |
| 6 | isolated 1G 无 Swap 断网 | 同 #1 | 同 #1 | 测试观察器 |
| 7 | isolated 1G 无 Swap 空间耗尽 | 同 #1；随后 GUI 重开 45 s 超时 | 同 #4 | 测试观察器 + 预期资源保护拒绝 + 测试恢复未等待 |

没有新增 OOM kill 不被当作“无压力”的证据，也不把所有超时归为夹具：下文每一类都由独立实验区分。

## 3. 分离复现与对照（R2-A，固定运行包 fb6823b）

控制器提交与运行包分开记录。所有实验均在一次性 Debian 12 QEMU VM（1 vCPU / 1024 MiB / 10 GiB，原卷预算与 cgroup/Tasks/FD 限额不变）。

### 3.1 单项与连续（Run `39889230114`，控制器 `289441c`）

每项在全新 VM 中只执行“正常下载 + 一个故障”：

| 用例 | 1G+Swap | 1G 无 Swap |
|---|---|---|
| 正常下载 | 1/0 | 1/0 |
| 断网 | 1/1（白屏） | 2/0 |
| 原生取消 | 1/1（白屏） | 2/0 |
| SIGKILL 崩溃恢复 | 2/0 | 2/0 |
| 真实空间耗尽 | 1/1（白屏） | 2/0 |
| 真实 inode 耗尽 | 2/0 | 1/1（白屏） |
| 原顺序 29 项 | 26/3 | 27/2 |
| 原顺序 + 前置资源/UI 套件 | 24/5 | 26/3 |

结论：失败在单项中也出现，**不依赖前序状态污染**；是否失败带随机性。连续/污染运行另外暴露了 §5、§6 的问题。

### 3.2 无故障探针（Run `39894122714`，控制器 `8cd26a8`）

150 次新开原生下载页（每规格 30 次；安静阶段与原 200 ms SSH 轮询阶段）：

| 规格 | 持续白屏 | 内容稳定用时 |
|---|---|---|
| 1 GiB / 1 vCPU / Swap | 0/30 | 平均 1.0 s |
| 1 GiB / 1 vCPU / 无 Swap | 0/30 | 平均 1.0 s |
| 2 GiB / 1 vCPU | 0/30 | 平均 1.0 s |
| 1 GiB / 2 vCPU | 0/30 | 平均 0.34 s |
| 2 GiB / 2 vCPU | 0/30 | 平均 0.52 s |

页面加载时长只随 CPU 核数变化，与 RAM 无关；宿主 noVNC 画布比例与 VM 内私有 X11 `xwd` 比例一致（0.0234 / 0.0197），noVNC 链路忠实。

**无效证据保留**：探针第 2、3、5 轮（Run `39892546355`、`39893252290`、`39896118205`）的判定阈值或首个采样仍是上一标签内容，结果不作为结论使用。

### 3.3 白屏分层证据（Run `39897200048`，控制器 `a6159e4`）

使用原失败流程，在失败后 40 s 观察结束时读取私有 X11、DOM 与恢复动作：

| 规格 | 白屏次数 |
|---|---|
| 1 GiB / 1 vCPU（有/无 Swap，各两遍） | 7 / 12 |
| 2 GiB / 1 vCPU | 1 / 3 |
| 1 GiB / 2 vCPU | 0 / 3 |

每次白屏：宿主画布 0、**私有 X11 xwd 0**、一次性选区标记未被替换（**DOM 无可选内容**）、鼠标移动和标签切换无效、**F5 刷新后 xwd 0.155**。renderer 进程存活、浏览器 cgroup CPU≈0%、memory PSI≈0。

## 4. 白屏根因（已证实）

`nativeDownloadUI` 原实现：Ctrl+J → 观察到窗口标题含 “Download” → **立即 `Escape`** → 选区。Chrome 中 `Escape` 停止当前页面加载。原生下载页标题在 HTML 解析时即出现（≈0.4–0.6 s），而 1 vCPU 上其模块脚本约需 1.0 s；`Escape` 中止加载后留下“标题已设、组件未定义”的空文档：X11 白、DOM 空、只有刷新恢复。2 vCPU 页面在 0.35 s 内完成，故不受影响；无 `Escape` 的探针 150/150 正常。上一轮失败的标题出现时间（0.4–0.6 s）与成功（0.7–1.1 s）的分布也与此一致。

因此**白屏是测试观察器故障**，不是 Chromium 功能故障、Profile/卷问题或 1 GiB 内存压力；真正的区分因素是 CPU 核数（2 GiB 1 vCPU 同样复现）。

## 5. 产品缺陷：GUI 关闭时 Chromium 非正常退出（已证实，已修复）

原网关 `stop()` 先销毁 GUI IPC 并结束租约，Xvnc 与私有 D-Bus 随即被 PID 1 回收，而 Chromium 仍在运行：journal 几乎每次关闭都有 `FATAL:dbus/bus.cc … D-Bus connection was disconnected. Aborting.`（上一轮 2 GiB 10 次、1 GiB 4–14 次）。Profile `exit_type` 保持 `Crashed`（R2-B：6 次中 5 次），下一次启动必然出现 “Restore pages?” 气泡；证据显示它吞掉地址栏 Enter（崩溃用例导航失败）并影响原生菜单键盘选择。真实用户每次重开浏览器也会看到该提示。

最小修复（不改权限、预算、沙箱、认证或 IPC 协议）：

- `gui-runtime/browser-worker.py`：会话结束时只向 Chromium 浏览器主进程发送 SIGTERM，最多等待 3 s，超时记录 `browser_shutdown_timeout`，其余仍由 PID 1 回收。
- `src/lib/gui-gateway.mjs`：输入仍在 `stop()` 中立即关闭；存在浏览器时暂停显示 IPC 并保留租约，只读轮询 `webdesktop-browser@1` MainPID 归零（最多 4 s）后，再按原顺序销毁显示与租约。最坏情况仍在既有 7 s 停止断言窗口内。
- 回归断言：第一次普通 GUI 关闭后 `exit_type` 必须为 `Normal`/`SessionEnded`。SIGKILL 崩溃用例之后 Chromium 会有意保留 `Crashed` 直至恢复提示被处理，该值只记录不断言。

修复后：新运行包上 1 GiB 与 2 GiB 第一次关闭均为 `SessionEnded`，journal 不再出现 D-Bus abort。

## 6. 其他已证实的测试问题与修复

| 问题 | 证据 | 修复 |
|---|---|---|
| 地址栏丢失前缀 | 截图地址为 `://203.0.113.2/...`（`https` 丢失），30 s 等待注定失败 | 有界最多 3 次完整重输，Enter 仍只在复制到精确 URL 后按下 |
| 原生取消菜单选中“暂停” | 截图点击后菜单尚未绘制；结果 `Paused`；无 Restore 气泡时同样发生 | 用画布像素确认菜单已绘制（≤5 s）后再按键；仍要求 `Canceled` + 同任务 origin 提前断开 |
| inode 故障中下载反而完成 | 空闲 inode 0→2 后 8 MiB 全部送达、History state=1；Chromium Profile 与 Downloads 同卷，其 SQLite journal 删除释放 inode | 有界 wdterm 保持单元只在夹具目录内补占释放的块/inode，清理前先停止并报告补占次数与恢复后的块/inode |
| 故障后 GUI 重开 45 s 超时 | 网关 11 ms `waiting_resources`；卷 critical 30 s 迟滞；前端不自动重试 | 重开前有界等待真实 `/api/resources` 为 normal（≤120 s），尊重保护拒绝 |
| 从下载页导航失败 | 直接点击地址栏后仍复现：取消后卡片更新把焦点恢复到“复制链接”按钮并还原地址栏 | 每次原生观察结束后 Ctrl+W 关闭下载页（核对标题），不再从 WebUI 标签发起导航 |
| 观察器内层超时穿透 | 原栈 `until → until → nativeDownloadUI` | 选区未替换时在外层 20 s 内重试；精确名称、无其他卡片、拒绝旧剪贴板的判定不变 |

Profile 与访客卷：块/inode 耗尽确实同时影响 Chromium Profile 写入（History 等 SQLite journal），这是现有容量设计下的真实行为；本轮未扩大卷、未移动 Profile、未删除任何 Profile/History/Bookmarks/Sessions/用户下载，故障清理只删除夹具自建目录。

## 7. 资源观测

- 白屏期间：webdesktop 全部 cgroup CPU 合计约 2%，浏览器约 0%；memory/io PSI 降至 0；浏览器 `memory.events` high/max/oom 均 0。
- 1 GiB 执行层平时 460–500 MiB，接近 MemoryHigh（约 504 MiB），GUI 重启瞬间出现 high 回收事件；2 GiB 同位置 high 增量为 0。该压力真实存在但与白屏无因果关系（白屏时无回收事件）。
- R2 诊断 monitor 在 5% CPU 配额下每样本调用 11 次 systemctl，其自身节流会抬高系统 CPU PSI，因此 CPU PSI 读数不作为结论证据。
- 所有缺失字段记录为 `unavailable`；未调整任何 RAM/Swap/CPU/Tasks/FD 配额或告警阈值。

## 8. 修改文件

| 文件 | 类别 | 内容 |
|---|---|---|
| `gui-runtime/browser-worker.py` | **产品运行代码** | 会话结束先让 Chromium 自行退出（≤3 s） |
| `src/lib/gui-gateway.mjs` | **产品运行代码** | 显示/租约在浏览器单元停止后再释放（≤4 s 只读轮询） |
| `tests/download_bridge_ui.mjs` | 测试 | §4、§6 观察器/输入/恢复修复；时间线、画布比例、R2 诊断模式与单项模式；clean-exit 回归 |
| `tests/download_bridge_vm.py` | 测试夹具 | 只读 `diag`/`xwd`；monitor 增加 PSI/events.local；故障保持单元 |
| `tests/download_bridge_driver.py`、`tests/staging_vm_driver.py` | 测试 | `--d1-r2` 诊断入口（固定 fb6823b） |
| `tests/d1_r2_paint_probe.mjs` | 测试 | 无故障原生下载页探针 |
| `.github/workflows/d1-r2.yml` | CI | R2 诊断矩阵（仅 `[d1-r2]` 或手动） |
| `.github/workflows/staging.yml` | CI | 在 `feature/CC` 构建；下载门槛增加 1 GiB 有/无 Swap |

已知遗留：`staging_bundle.py`/`staging_install.py` 的部署分支常量仍为 `feature/private-deploy-isolation`。CI 使用 git bundle 与测试 provenance 不受影响；但若未来从 `feature/CC` 经 bootstrap 部署，需要先合并回原分支或审查该常量，本轮未改动。

## 9. 最终回归

### 9.1 修复轮次与中间失败（保留，不回填）

| 代码 | Run | 结果 | 处理 |
|---|---|---|---|
| `335dcf2`（仅测试修复，固定 fb6823b 运行包） | D1-R2 `39898984142` | 6 组 1 GiB 连续：原 7 类失败未再出现；`exit_type=Crashed` 5/6、崩溃用例导航 4/6（Restore 气泡）、取消选中“暂停” 1/6 | 证实需要产品修复 F2；菜单竞态另修 |
| `2ea571a`（F2 产品修复 + 测试） | Staging `39901621049`、Validate `39901621046` | Validate 8/8；三门槛均 28/1：1 GiB 崩溃用例从下载页导航失败；2 GiB 的 clean-exit 断言读到 SIGKILL 用例后的有意 `Crashed` | 门槛正确阻止矩阵；修正断言位置与导航入口 |
| `3e26fef` | Staging `39904302830`、Validate `39904302750` | Validate 8/8；2 GiB 门槛 29/0；1 GiB 两门槛 28/1（取消后卡片焦点恢复抢回地址栏） | 观察结束后关闭下载页 |
| **`7b6d34c`（最终）** | **Staging `39906683933`、Validate `39906683994`** | **14/14、8/8 全部 success** | 本节以下统计 |

### 9.2 最终固定受测代码

**`7b6d34c54ce1fa48355041175525a0dde20d18b7`**（`feature/CC`）。报告为其后单独的文档提交。

| 运行包 | 值 |
|---|---|
| Artifact | `staging-debian12-7b6d34c54ce1fa48355041175525a0dde20d18b7`，ID `12480977166`，到期 2026-10-30T05:38:50Z |
| 内部 tar.gz | 225979543 bytes，独立计算 SHA256 **`73788f37f88b2a1bfb6b2893a8254f905e09068178769b230c05a19fd624e0bf`**，与 sidecar、`staging-artifact.json` 一致 |
| 外层 ZIP | `734c4787…94ef34`，与 GitHub API digest 一致；不得替代内部摘要 |
| manifest | schema 1、commit 7b6d34c、Debian 12 / amd64 / Node 22.23.2 / ABI 127 / glibc 2.36；29617 条目；无 `.env`、`.git`、app 内测试路径 |

### 9.3 逐项统计（逐份 JSON 汇总，P / F / S / N）

| 类别 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| Ubuntu / Debian 常规 Python（222×2） | 444 | 0 | 0 | 0 |
| Node 资源/准入/IME/下载观察/RFB（81×2） | 162 | 0 | 0 | 0 |
| Debian 源码 DAC | 9 | 0 | 0 | 0 |
| Linux 访客/WS/文件/SSH 解析/终端边界/状态认证（18+12+28+3+5+8） | 74 | 0 | 0 | 0 |
| IME 真实 Chromium/xterm | 36 | 0 | 0 | 0 |
| booted systemd VM（9+9） | 18 | 0 | 0 | 0 |
| PTY VM（systemd 18 + PTY 28 + 终端 UI 4） | 50 | 0 | 0 | 0 |
| GUI VM 有/无 Swap（各 72：systemd 18、PTY 14、GUI 20、desktop-runtime 11、UI 9） | 144 | 0 | 0 | 0 |
| **Validate 小计** | **937** | **0** | **0** | **0** |
| 下载门槛 ×3（1G+Swap、1G 无 Swap、2G：29 + 重启 2 + 健康 40×2） | 333 | 0 | 0 | 36 |
| staging 安装 ×4 规格（29+2+健康 80+资源 10+UI 5） | 504 | 0 | 0 | 48 |
| isolated 安装 ×4 规格（上项 + 预检 12、故障恢复 1、模式 22、产物补审 12、终端 UI 4） | 708 | 0 | 0 | 48 |
| 旧 f195f90 兼容（健康 39×2 + 资源 4） | 82 | 0 | 0 | 54 |
| **Staging 小计** | **1627** | **0** | **0** | **186** |
| **必需回归合计** | **2564** | **0** | **0** | **186** |
| 独立资源可行性实验（不并入上行） | 6 | 4 | 0 | 0 |

- 186 项未执行 = 三门槛各 12（前后健康各保留 6 个范围外标签：外部管理员 SSH 往返、Claude 正式入口、网页剪贴板、拖拽、多 GUI、正式域名）共 36 + 八组当前安装矩阵各 12 共 96 + 旧兼容组 54（健康 12 + 旧包不具备的下载 31、新 UI 5、新资源 6）。生产 VPS、新版真实 Windows 搜狗人工验收等范围外项目不计入用例数。
- 构建/typecheck、Shell 语法、Python 编译与 Node 语法检查均退出 0，不计入用例数。
- 独立实验：无 Swap Chromium 320/384 smoke 通过、Claude 320/384/448 失败；有 Swap 仅 Claude 384 失败；全部 `security=observed-safe`、`production_usable=false`。Claude 正式入口保持关闭。

### 9.4 1 GiB 专项结果（新运行包）

| 规格 / 入口 | D1 下载 29 项 | 真实重启后 2 项 | 首次关闭 exit_type |
|---|---|---|---|
| 1G+Swap 门槛（staging） | 29/0/0/0 | 2/0/0/0 | SessionEnded |
| 1G 无 Swap 门槛（staging） | 29/0/0/0 | 2/0/0/0 | SessionEnded |
| 1G+Swap staging 安装矩阵 | 29/0/0/0 | 2/0/0/0 | — |
| 1G 无 Swap staging 安装矩阵 | 29/0/0/0 | 2/0/0/0 | — |
| 1G+Swap isolated 安装矩阵 | 29/0/0/0 | 2/0/0/0 | — |
| 1G 无 Swap isolated 安装矩阵 | 29/0/0/0 | 2/0/0/0 | — |
| 2G / 4G staging 与 isolated | 各 29/0/0/0 | 各 2/0/0/0 | — |

断网（原生 `File incomplete` + 同任务 origin `intentional-network-cut` + 未发布 404）、原生取消（`Canceled` + 同任务提前 peer-closed）、SIGKILL 崩溃与重开、GUI 关闭重开、真实空间/inode 耗尽与清理后重新下载、目录替换、硬链接、UID/DAC/挂载、双认证与撤销、Agent/后端重启与 Profile/书签保持均在新 SHA 上实际执行通过。

## 10. 阶段判断

- **D1-R2：PASS。** 7 项失败均已取得证据并归类：白屏为测试观察器中止页面加载（已证实）；地址栏丢字符、取消菜单、下载页焦点恢复为 1 vCPU 输入时序（已证实）；inode 夹具被同卷 Profile 释放 inode 破坏前提（已证实）；故障后 GUI 重开为预期资源保护拒绝 + 测试未等待（已证实）；Chromium 关闭时非正常退出为真实产品生命周期缺陷（已证实并修复）。
- **Phase 3C-D1：PASS（本轮验收标准 1–15 在新固定 SHA 上满足）。** 1 GiB 有/无 Swap、staging/isolated 均 29/29 + 重启 2/2；2/4 GiB 保持全部通过；Phase 4A-R1 安装、恢复、产物补审与权限校验无退化；未降低任何资源或安全要求。
- **不需要用户对最低资源要求作出决策**：1 GiB 1 vCPU 在现有预算下可通过；区分因素是 CPU 核数而非 RAM，但影响仅在测试时序，已在测试侧修正。
- 待验证假设（未作为结论）：原生下载页加载耗时与 1 vCPU 的关系仅在 CI 虚拟机测得，真实 VPS 的人工体验需另行授权验证。

## 11. 剩余风险与下一步建议

| 等级 | 事项 |
|---|---|
| MEDIUM | 部署分支常量 `feature/private-deploy-isolation` 与开发分支 `feature/CC` 不一致：manifest 与 bootstrap 校验按原分支；合并或部署前需决定合并方向或审查常量。 |
| MEDIUM | 产品修复使 GUI 关闭最多多等待约 4 s（输入仍立即停止）；最坏停止时间仍在既有 7 s 断言内，真实 VPS 体验未人工验证。 |
| LOW | Chromium Profile 与 Downloads 同卷，耗尽时 Profile 写入同样失败——现有容量设计下的真实行为，未扩卷、未移动 Profile。 |
| LOW | 观察器仍依赖 Chromium 155 原生页面布局与像素判定；布局变化需重新校准，不得放宽当前任务绑定。 |
| — | Claude 正式入口、Playwright、剪贴板/拖拽、多 GUI、正式 HTTPS 仍未开放；未连接或修改真实 Evoxt VPS。 |

建议下一轮：先由用户审查本报告与固定产物，决定 `feature/CC` 与 `feature/private-deploy-isolation` 的合并方式；随后可进入下一项 3C 子任务（例如最小文字剪贴板），本轮不自动启动。

## 12. 生产保护确认

未修改 `main`（仍 `5922e14`）、Beta 标签、`feature/private-deploy-isolation`（仍 `d962567`）、稳定 `install.sh`、生产密钥、数据库、用户文件、SSH、Deploy Key、仓库可见性或真实 Evoxt VPS；未调整 RAM/Swap/CPU/Tasks/FD 配额、告警阈值、Chromium namespace/seccomp、Socket/文件 DAC、访客卷或认证协议；自动更新保持关闭。
