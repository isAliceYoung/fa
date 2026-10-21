# Windows 搜狗终端 IME 诊断与最小修复

报告日期：2026-10-22（Asia/Kuala_Lumpur）。分支仅 `feature/private-deploy-isolation`。

## 1. 代码与交付范围

- 原部署代码：`131ea566859d837473383425eac2b30bdb105282`；本轮起点报告：`c0defa78822c418e62b5655a5508f59d19690527`。
- 最小前端修复与测试：`44142f3b28ab31f68d28de61776ac833f8cda23d`。
- CI 镜像获取修正及本轮固定构建代码：`72e06b92ec37b6185150be04ecdd69c0418f5afb`。
- 本报告为随后单独提交的文档，不把文档提交冒充运行产物 SHA。代码、CI、固定产物核对均已完成；未部署 VPS。

完整变更文件共 15 个（含本报告）：

| 文件 | 内容 |
|---|---|
| `src/components/desktop/apps/systerm-app.tsx` | 连接兼容桥；断线/销毁取消待发内容，未连接时禁止输入 |
| `src/components/desktop/terminal-ime.mjs` | 局部、固定状态、保留 xterm 正常路径的确认提交桥 |
| `src/components/desktop/terminal-ime.d.mts` | 公开 Terminal API 类型 |
| `tests/terminal_ime_test.mjs` | 30 项事件模型测试 |
| `tests/terminal_ime_diagnose.mjs` | 原版 xterm A/B 的 12 组观察 |
| `tests/terminal_ime_browser.mjs` | 真实 Chromium、xterm、WebSocket 36 项回归 |
| `tests/terminal_ime_lab.mjs` | 本机 loopback 独立回显服务，不执行命令 |
| `tests/fixtures/terminal-ime-lab.html` | 独立 A/B 页面 |
| `tests/fixtures/terminal-ime-lab.mjs` | 有界脱敏事件元数据与公开桥的对照 |
| `tests/fixtures/terminal-ime-native-trace.json` | 用户真实 Windows 关键元数据摘要，含第一候选失败 |
| `tests/pty_ui_runtime.mjs` | 真实受限 Bash、认证 WS 的中文与拼音一次提交验证 |
| `tests/run_systemd_vm.py` | 必要 CI 夹具修复：固定 Debian 日期镜像及摘要 |
| `.github/workflows/validate.yml` | 接入事件模型、真实浏览器与诊断 Artifact |
| `webdesktop-deploy/TERMINAL_IME_DIAGNOSIS.md` | 修改前诊断快照与真实用户实测补充 |
| `webdesktop-deploy/TERMINAL_IME_FIX_REPORT.md` | 本报告 |

未改 package.json/lock、terminal-input 分帧、PTY/WS 协议及后端执行层；没有升级依赖或修改已安装 node_modules/.next。

## 2. 根因与修改前证据

用户早期 Chrome/Edge、搜狗/微软、记事本/终端、粘贴的对照已排除一般 UTF-8/Bash 不支持中文的解释。随后用户在 Windows Chrome 155 上运行完全独立的 xterm 6.0.0 A/B 页面，提供完整脱敏记录：

| 真实搜狗同词空格确认 | A：screenReaderMode=true | B：false |
|---|---|---|
| compositionstart/update/end、keydown | 均未出现 | 均未出现 |
| beforeinput → input | 事件 10/11，43822/43826ms | 事件 8/11，51013/51014ms |
| 输入状态 | insertText、可信、isComposing=false、length=2、keyDownSeen=false | 同左 |
| textarea | 从 0 增至 2 | 从 0 增至 2 |
| 最终 onData / 输入帧 / 回显 | 0 / 0 / 0 | 各 1 次 |

原版 `@xterm/xterm/src/browser/CoreBrowserTerminal.ts:1196` 的 `_inputEvent()` 要求 `!screenReaderMode` 才从无组合生命周期的 insertText 触发数据。搜狗这轮没有进入 CompositionHelper，辅助模式又禁止直通，文本停在 textarea，未进入既有 onData/分帧/WS。此结论基于完整 A/B，不能把早期截图或所有搜狗事件泛化为“compositionend 必然缺失”。

生产入口对应 `systerm-app.tsx:83` 的 `screenReaderMode:true` 与 onData；新逻辑集中在 `terminal-ime.mjs:13` 的 confirmed、`:14` 的 flush、`:20` 的 beforeinput、`:26` 的 input、`:35` 后的组合/生命周期处理。连接状态接入在 `systerm-app.tsx:187` 与 `:220`。

本机 Chrome、Edge 的原版 CDP/合成实验各 12 组稳定观察（0 断言错误），可复现直接 insertText 的 A=0/B=1；普通组合正常时两边各一次。另有整体 textarea 替换及已开始组合但缺少结束的合成缺陷，它们与本次真实搜狗事件不同，不冒充本次根因，也不把“复现丢失”计作兼容性通过。

## 3. 方案与无障碍行为

保持 `screenReaderMode:true`。公开 textarea 上只有可信、成对匹配、非 composing、无控制字符的 insertText，且终端已连接、聚焦、允许输入、没有按键/组合处理时才候选补发。xterm 先处理；其 onData 只要已触发，桥不再发送。最终通过公开 `Terminal.input(data,true)` 进入原 onData → terminalInputFrames → 已认证 WebSocket → wdterm PTY/Bash，不额外创建输入通道。

状态为固定几个布尔值、一个至多 16384 UTF-16 单元的 pending edit、两个至多一次的可取消任务（补发与组合结束冷却），没有 Map、无限队列、轮询、重试或全局键盘监听。原 4096 单元帧边界与代理对保护、WS 背压原样保留。较大的该类兼容补发拒绝处理，不改变原粘贴通道。后续 Enter/Ctrl 输入前先提交已确认文本；不会把确认键擅自变成额外空格、汉字或回车。

失焦、组件销毁、WS 断线取消 pending，重连不重放未发编辑；初始和断线期间 disableStdin=true。正常组合、微软拼音、普通英文、粘贴继续由 xterm 负责。私有 core/_keyDownSeen/CompositionHelper 仅在隔离诊断页读取，正式桥不依赖私有接口，也不记录真实键盘内容。

拒绝方案：永久关闭 screenReaderMode 会移除真实 accessibility tree 和 aria-live，成本明确；简单监听所有 input 会重复或提前发送；项目内改私有 CompositionHelper 影响更广；上游历史 issue/未发布 PR 不等于适用稳定修复。核对时官方 npm 稳定版仍是 6.0.0，锁文件保持不变，不为此升级 beta。

无障碍 DOM 在本机 Chrome/Edge 与自动化中仍存在；这证明配置/结构保留，**NVDA/Narrator 实际朗读未执行**。

## 4. 真实 Windows 人工结果与失败保留

用户在 `/fixed` A（辅助模式 true）确认：

- 搜狗空格、数字 1 各一次“你好”，对应元数据 onData/帧/回显各一次。
- 第一候选错误过滤 ASCII，未选词 Enter 的原始拼音 insertText(length=5) 被丢弃，保留为失败证据。
- 最终候选接受已确认的无控制字符 ASCII；用户刷新后复测“未选词 Enter 保留 nihao”和“空格选好你好再 Enter 换行”，回复“正常”。没有把直接 Enter 强行解释为选中文。
- 微软空格、数字 1 正常组合各一次中文；直接 Enter 的 5 字符原始拼音沿正常 compositionend 路径发送一次。用户描述的 nih 为切换输入法时未进入中文的普通英文，随后已退格，不当成兼容失败。

验证级别分别列示：

1. 事件模型：模拟 Target/可信标志，验证状态机和取消，不声称驱动原生 IME。
2. 真实 Chromium/xterm 集成：CDP、剪贴板及明确合成故障注入；CI 另走真实 Next.js/认证 WS/受限 Bash。
3. 原生 Windows 搜狗/微软人工：仅上述独立本机回显页已执行；**新版真实 VPS、原生 Edge 全套、NVDA/Narrator，以及原生 IME 全场景矩阵未执行**。本轮从未连接或修改用户 VPS。

| 场景 | 自动化（事件模型/真实浏览器/PTY） | 本轮原生 Windows 人工 |
|---|---|---|
| 搜狗空格、数字 1 选你好 | 确认事件模型、真实浏览器一次提交 | 已执行，各一次；有脱敏事件记录 |
| 未选词直接 Enter 提交原始 nihao | 模型和真实浏览器；真实 Bash 验证不额外插入回车 | 最终候选正常；第一候选失败保留 |
| 选好你好后 Enter | 模型验证确认文本先于回车；真实浏览器和 Bash | 用户确认正常 |
| 微软拼音正常组合 | CDP 正常组合及模型所有权检查 | 空格/数字中文、Enter 原始拼音均有正常元数据 |
| 英文、中英文切换、中文标点 | Chrome/Edge 回归 | 早期用户有英文/标点正常对照；最终修复完整快切人工未执行 |
| Ctrl+C/D/Z、中文粘贴 | Chrome/Edge 实际键盘/剪贴板；原 PTY UI 回归 | 最终修复原生人工未执行，早期中文粘贴对照正常 |
| 取消候选、切焦、断线/刷新/销毁 | 模型、真实浏览器，实际 PTY 重连/撤销 | 最终修复原生人工未执行 |
| 长中文、emoji/代理对 | 模型与真实浏览器分帧 | 原生人工未执行 |

初次自动浏览器故障注入结果 32 通过/2 失败：测试夹具曾用 eval，被诊断页 CSP 拒绝。改为固定 action 枚举后最终 36/0/0/0，额外断言注入已执行；没有放宽 CSP。原初始记录 `.test-results/terminal-ime-browser-initial.json` 保留，不将夹具失败说成应用故障或删除。

## 5. 命令与本机回归

以下均在仓库根目录，单行执行，不涉及 VPS：

```powershell
node tests/terminal_ime_lab.mjs
node tests/terminal_ime_diagnose.mjs
node tests/terminal_ime_browser.mjs
node tests/terminal_ime_test.mjs
python tests/run_tests.py --json .test-results/terminal-ime-regular-windows.json
npm run typecheck
```

Windows Chrome 为自动寻找的默认浏览器；Edge 可单行指定：

```powershell
$env:IME_TEST_CHROME='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'; node tests/terminal_ime_browser.mjs .test-results/terminal-ime-browser-edge.json; Remove-Item Env:IME_TEST_CHROME
```

| 本机最终组 | 通过 | 失败 | 跳过 | 未执行 | 退出码 |
|---|---:|---:|---:|---|---:|
| Windows/Git Bash 原常规 | 190 | 0 | 0 | 报告另列整机与未授权生产两类环境说明 | 0 |
| IME 事件模型 | 30 | 0 | 0 | 0 | 0 |
| Chrome 实际 xterm/WS | 36 | 0 | 0 | 0 | 0 |
| Edge 实际 xterm/WS | 36 | 0 | 0 | 0 | 0 |
| 原版 Chrome / Edge A/B | 各 12 组观察 | 各 0 观察断言错误 | 不计兼容通过数 | 原生 IME 由用户另测 | 0 |
| TypeScript / Python 夹具语法 | 不虚构用例数 | 0 | 0 | 0 | 0 |

30 项模型和 36 项/浏览器覆盖正常组合、无 composition 生命周期的已确认文本、乱序配对、重复 input/连续相同合法编辑、取消、未完成组合、空格/数字关键序列、英文/中文/标点/emoji、长帧、Ctrl+C/D/Z、粘贴、焦点、断线、刷新、销毁、两种辅助模式及一次提交顺序。原生选词的键盘行为以用户记录为准，合成序列不替代人工验收。

## 6. 完整 CI 与 Debian 固定产物

固定代码 `72e06b92ec37b6185150be04ecdd69c0418f5afb`：[Validate 39634756756](https://github.com/isAliceYoung/fa/actions/runs/39634756756)、[Staging 39634756573](https://github.com/isAliceYoung/fa/actions/runs/39634756573)。两个 workflow 共 **15 个任务全部 success、退出码 0**：Validate 的 scripts、debian12-scripts、build、guest-3a-debian12、systemd-debian12-vm、pty-systemd-debian12-vm、gui-systemd-debian12-swap-0/1024 共 8 个；staging 两个构建及五个安装共 7 个。已确认两 run 的 head_sha 完全一致。

新增浏览器 JSON 在 Validate Artifact `terminal-ime-chromium-evidence`（ID `12361360882`），原版 12 组观察/0 断言错误，修复版 36/0/0/0。真实 Linux 认证/文件/状态证据在 `guest-3a-linux-results`（ID `12361366181`）；独立 systemd VM 在 `booted-systemd-debian12`（ID `12360508183`），image.json 确认固定镜像和摘要，reboot.json 确认不同 boot ID 及管理员 SSH 前后正常。

PTY Artifact `pty-systemd-next-ui-debian12`（ID `12360838436`）：系统权限 prepare/reboot 各 9/0/0，PTY 真实 WS/cgroup/撤销/故障各 14/0/0/0，Chrome 真实终端 UI 4/0/0/0。新增中文回归使用真实桌面登录、xterm、已认证 WS、wdterm Bash：printf 实际输出 `IME_你好`，捕获中文帧恰好一次；ASCII 拼音恰好一帧，Enter 之前没有擅自生成 CR；辅助阅读树存在。截图 `terminal-ime.png` 与 `terminal-ui.json` 同包保存；驱动是 CDP，不称为原生 Windows 搜狗。

已完成的 Validate 分组（失败/跳过均为 0，退出码均为 0）：

| 分组 | 通过 | 未执行 |
|---|---:|---|
| Ubuntu / Debian Python 常规 | 各 190 | 各另列整机/生产环境说明；整机由 VM 覆盖，生产本轮未执行 |
| 资源/尺寸 Node / 状态准入 Node | 每环境 13 / 17 | 0 |
| 新 IME 事件模型 | 每环境 30 | 0 |
| Debian 源码 DAC | 9 | 0 |
| guest IPC/UID/认证/数据库 | 18 | 0 |
| SSH Include/Match 解析 | 3 | 0 |
| 真实 Next.js WS Upgrade/生命周期 | 12 | 0 |
| 文件流/事务/恢复/ZIP/容量 | 27 | 0 |
| 终端 UID 攻击 Agent/事务/内部 Next | 5 | 0 |
| 状态认证洪泛/取消/数据库恢复 | 8 | 0 |
| 新真实 Chromium IME/xterm/WS | 36 | 0 |
| 独立 systemd prepare / reboot | 各 9 | 0 |
| PTY VM 系统权限 prepare / reboot | 各 9 | 0 |
| PTY WS/cgroup/撤销/故障 prepare / reboot | 各 14 | 0 |
| Chrome 真实终端 UI/刷新/接管（含新 IME） | 4 | 0 |

Shell 语法、原 Node 安全检查、Python 编译、Next build/typecheck 和 HTTP/认证就绪检查均退出 0，不虚构内部用例数量。

两个参考 GUI Artifact：无 Swap `12365100045`、有 Swap `12364135773`。两边逐项 JSON 均已独立下载；每组 **72 通过/0 失败/0 跳过/0 未执行**，包括：

| 每个 GUI VM 的必需断言组 | 通过 | 失败 | 跳过 | 未执行 |
|---|---:|---:|---:|---:|
| systemd/权限 prepare / reboot | 各 9 | 0 | 0 | 0 |
| PTY prepare | 14 | 0 | 0 | 0 |
| GUI prepare / reboot | 各 10 | 0 | 0 | 0 |
| OSC 确认、noVNC、PCManFM、共享目录及登录接管 UI | 4 | 0 | 0 | 0 |
| Chromium 沙箱/网络/DAC/故障/背压撤销/GTK/Qt/XIM | 11 | 0 | 0 | 0 |
| HTTPS/WSS、实际中文、profile、重开/刷新、撤销 | 5 | 0 | 0 | 0 |

Validate 的明确用例合计 **830/0/0/0**，不将常规报告的整机/生产环境说明重复计为具体用例；真实生产测试始终未执行。

最终 staging 全部五组安装、重启和管理员 SSH driver 检查已成功（两个构建任务也成功）。逐项 JSON 已独立下载核对：

| 配置及 Artifact ID | 压力/并发（通过/失败/跳过/未执行） | 网页 | 安装后 / 重启后健康 |
|---|---|---|---|
| 1GiB + 1GiB Swap，`12362135255` | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 1GiB 无 Swap，`12361163172` | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 2GiB，`12362480633` | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 4GiB，`12360664356` | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 固定 f195f90 旧版对照，`12360628691` | 4/0/0/6 | 新版网页 5 项未执行 | 各 39/0/0/6 |

现代矩阵合计 372/0/0/48，对照 82/0/0/23，合计 **454 通过/0 失败/0 跳过/71 未执行**。健康未执行项为管理员外部 SSH 往返、Claude 正式入口、网页剪贴板、拖拽、多 GUI 共存、正式域名；driver 另有真实 VM SSH 结果，不反改健康 JSON 的未执行。旧版没有新压力机制/现代网页入口，亦不算通过。各现代网页的资源监测、文件复制、PCManFM 动态尺寸、Chromium 原生新标签/Google 策略、Fcitx5 拼音与英文切换、异地登录撤销各 5 项回归均通过。

最终必需回归合计 **1284 通过/0 失败/0 跳过/71 未执行**，统计来自逐项 JSON、常规与 Node 作业日志。相比前轮 1187，新增两环境模型 60、浏览器 36、真实 PTY UI 1，共 97 项；原版 A/B 观察、构建/语法和不另计数的 SSH driver 不重复算作通过。下列独立资源实验另计，不掩盖失败。

### 固定 staging 产物独立校验

- Run：`39634756573`；Artifact：`staging-debian12-72e06b92ec37b6185150be04ecdd69c0418f5afb`；ID：`12361660053`。
- 到期：`2026-10-28T16:28:14Z`（UTC；本地 10 月 29 日 00:28:14）。7 天 CI Artifact，不是长期 Release。
- 内部 `staging-runtime.tar.gz`：225,701,956 字节。
- 内部 SHA256：`ea75549a44715eb022a6f7dc2ff03e24c9f1470a90c0a2c8ef7beb9c0993bd76`。
- 外层 ZIP SHA256：`039b04275c613af189d538f84b2a57c002fcfca112782e13f6733e9f6dfbf4d1`，不得与内部摘要混用。
- 已独立下载并重新计算摘要，与 CI 文本一致；逐项校验 manifest 全部 29,585 个文件的大小/哈希、31 个符号链接目标，29,616/29,616 一致。
- manifest 固定完整 commit、Debian12 amd64、glibc2.36、Node22.23.2、ABI127；实际 `.next` JS 中已核对新桥标记。未将独立诊断服务器/页面作为 app/tests 运行文件打包，没有 app/.env 或 app/.git。`source.bundle` 是原有 root 专属源代码交付物，仍含该提交的源码历史，不能交给访客；不声称它不含测试源码。CI 原有打包器继续拒绝构建输出中的测试秘密。
- 摘要一致只表示文件完整，运行验收以本报告另列的真实安装矩阵和 PTY/GUI 结果为准。审核并获得新授权前不部署。

首轮 `44142f3`：[Validate 39634421127](https://github.com/isAliceYoung/fa/actions/runs/39634421127)、[Staging 39634421128](https://github.com/isAliceYoung/fa/actions/runs/39634421128)。共 6 个任务成功/9 失败/0 跳过；9 个失败任务为独立 systemd、PTY、两组 GUI、五组安装，日志全部在启动 VM 前触发 `Debian image checksum mismatch`，exit 1。新增真实浏览器 IME、Ubuntu/Debian 常规与模型、Linux UID/文件/WS/认证、build/typecheck 已成功；未启动的系统权限/GUI/安装断言不能算通过。按与最终相同的具体回归计划，首轮执行 618 项通过，余 737 项未执行（含原计划的 71 项限制），9 个镜像校验失败另按基础设施任务记录，不混成应用回归失败或隐去。10 项独立 GUI 资源实验亦未执行。

夹具之前分别下载可变 latest 的 SHA512SUMS 和 qcow2，二者实际不匹配；CDN 版本不同步为可能原因，未声称已证明 CDN 内部机制。修正固定官方 `20261018-2635/debian-12-genericcloud-amd64-20261018-2635.qcow2`，既校验官方清单符合固定 SHA512，又校验下载内容；不重试碰运气，不接受新未知摘要，不降低安全断言。官方来源：[Debian 日期版 SHA512SUMS](https://cloudfront.debian.net/cdimage/cloud/bookworm/20261018-2635/SHA512SUMS)。

既有资源实验未修改，只按原流程独立收集：

| 本轮资源实验（原五档） | smoke-only 通过 | 失败 | 跳过 | 未执行 | 结果 |
|---|---:|---:|---:|---:|---|
| 无 Swap，Artifact `12365100045` | 2 | 3 | 0 | 0 | Chromium320/384 smoke-only；Claude320/384/448 均 Result=oom-kill、ExecMainStatus=9 |
| 有 Swap，Artifact `12364135773` | 5 | 0 | 0 | 0 | 五档均仅 passed-smoke-only |

10 项合计 **7 smoke-only 通过/3 失败/0 跳过/0 未执行**；两组 security_failed=0，所有 production_usable=false。若把必需回归与独立实验计数合并，明确用例总计 **1291 通过/3 失败/0 跳过/71 未执行**，但两类验收含义不同。没有增加档位、放宽 MemoryMax/MemorySwapMax、删除断言或恢复 Claude。旧 `RESOURCE_GUI_FIX_REPORT.md` 的 4 项失败、`STATUS_ADMISSION_REVIEW_REPORT.md` 各轮失败均保留；新结果不覆盖历史。workflow success、原生沙箱证明与 smoke-only 不能代替正式 Claude 长期交互验收。

## 7. 限制与后续人工复核

只修复已证实的无组合生命周期确认路径。已开始但永不结束的组合、xterm 整体替换截断、按住修饰键等不同事件模型不猜测提交；超过 16384 单元的该类补发不支持；没有 beforeinput 的孤立 input 不补发。没有扩大到完整 IME 重写。

需要后续审核、另行授权部署后，在真实桌面补测 Windows Chrome/Edge 的搜狗和微软：空格、数字 1、未选词 Enter、选词后 Enter、中英文快速切换、长中文/emoji、标点、Ctrl+C/D/Z、Ctrl+Shift+V、候选取消/切焦、断线/刷新不重放。仅用固定词，不输入密码或工作内容；正式桌面无持续诊断日志。屏幕阅读器真实朗读另测。

本轮未修改资源预算/告警、认证协议/撤销/限流、GUI 生命周期、Chromium 沙箱、系统权限、IPv4-only、Swap、PostgreSQL 恢复、自动更新禁用及欢迎文件/示例图片取消状态。main/Beta、真实 VPS、数据库、用户文件、部署密钥均不操作。代码和 CI 完成后停止等待审核，不自行部署。
