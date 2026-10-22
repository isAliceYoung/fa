# Phase 3C-D1：Chromium Download → Guest Files

本报告限定于原计划 Phase 3C 的下载衔接，不代表完整 Phase 3C 或 Phase 4 完成。开发分支为 `feature/private-deploy-isolation`；真实 Evoxt VPS、main、Beta、生产数据及密钥均不在本轮操作范围。

## 1. 基线与审查依据

起点为报告提交 `b20838f9680e9d3f0bf8f91f4f4f6f32c16da6e1`，上一轮受测代码为 `5c9ceecfb3e8b07b449ac55c9080c16f0a9b7a76`，开始时工作区干净。已核对 ISOLATION_PLAN §7/9/10/11、PRODUCT_ARCHITECTURE、PHASE3C_CHROMIUM_REPORT、PHASE4A_ADAPTATION_REPORT、PHASE4A_R1_AUDIT_REPORT、ISOLATED_DEPLOYMENT_4A 及 RESOURCE_MANAGEMENT。

一 VPS 一个实际用户。wdsvc、wdguest、wdterm、wdgui 用来隔离服务及执行层，不是四个租户。普通工作目录可以共享，服务源码、认证/数据库配置、部署密钥、Agent 事务和 GUI 控制状态不能纳入共享。

## 2. 实际原状与已证实缺口

先用**未修改的 R1 固定产物**执行真实下载，而非依据模板推断：可见 Chromium 经 noVNC 打开受控 HTTPS 页面，下载 `d1-probe.txt`，文件由 wdgui 创建在 `/home/wdguest/Downloads`，286720 字节，SHA256 为 `f613cd5b445831ba9124070d981f3cf9cfbf8e27594d68c7d0236092c896fc9f`。Agent 列表、文本读取和导出均返回 200。

证据：[基线运行 39739673632](https://github.com/isAliceYoung/fa/actions/runs/39739673632)，Artifact `12409066747`。这证明直接共享已经成立，不能算本轮完整 D1 验收。

后续真实慢下载发现 `.crdownload` 还在增长时 raw 返回 200 和部分字节，例如 196608 字节；原实现缺少未完成下载保护。[诊断运行 39740509315](https://github.com/isAliceYoung/fa/actions/runs/39740509315) 的 Artifact `12408494874` 保留原失败记录。

Downloads 目录可被普通数据身份改名，但这**不等于已经证明 Chromium 跟随替换路径自动逃逸**。最初诊断错误地期待替换目录出现完成文件；随后 SQLite 独占锁使观察器失败；改用只读观察后，截图显示 Chromium 打开原生 “Save File” 对话框并等待用户选择位置，没有自动完成写入。见 [39741399770](https://github.com/isAliceYoung/fa/actions/runs/39741399770)、[39742890421](https://github.com/isAliceYoung/fa/actions/runs/39742890421)。这些是诊断失败/待判定记录，不能充当安全通过。没有据此额外创建 root 下载复制服务或目录锁定挂载。

## 3. 最小实现与信任边界

```mermaid
flowchart LR
    C[可见 Chromium / wdgui] -->|原生 HTTPS 下载| D[访客 Downloads]
    D -->|已完成普通文件| A[文件 Agent / wdguest]
    A -->|固定 Unix Socket| S[认证后端 / wdsvc]
    S --> F[网页文件管理器]
    F --> L[浏览器下载到本地设备]
```

- 复用现有共享目录，不增加下载 IPC、任意 URL 获取接口、root/wdsvc 文件复制或第二套文件服务。
- root 管理 `/etc/chromium/policies/managed/webdesktop-downloads.json`，仅固定 `DownloadDirectory=/home/wdguest/Downloads` 和 `PromptForDownloadLocation=false`。不修改 Chromium Profile，不添加调试端口、headless、忽略 TLS 或 `--no-sandbox` 参数。
- 浏览器 worker 启动时检查 Downloads 是预期属主/数据组的真实 02770 目录，并检查 root 管理的固定策略。未知状态拒绝启动，不修复或接管用户目录、不回退 APP/tmp。
- 文件 Agent 对路径分量中的 `.crdownload` 后缀（大小写不敏感）及 `.org.chromium.` 前缀作保守保留处理：列表隐藏，读取/文本/导出拒绝，复制/移动/重命名等不发布这些对象。直接读取返回 409 和固定 `DOWNLOAD_INCOMPLETE`。不删除残留文件，不读取 Profile 数据库作为生产状态 API。
- 文件管理器 Downloads 提示复用现有四秒列表刷新，说明原生进度、取消和失败详情在 Chromium Ctrl+J 查看。正常完成文件继续使用既有预览、重命名、复制、移动、回收站和下载接口。
- staging 初次预检提前拒绝未知 managed Chromium 策略；isolated 恢复清单冻结新增策略目录。原不可变 APP、来源/完整 SHA、ACL、未知条目、service/repair 校验不放宽。

**界限：**保留名称不是文件完整性的加密证明；同一实际用户仍能在 Bash 中主动改名、编辑普通文件。完成文件也不等于可信内容。合法最终文件若故意使用上述保留名称，Agent 会保守拒绝，可通过原生浏览器或受限终端更名。含未完成文件的目录导出/复制会拒绝，不静默丢弃它们后报告完整成功。

## 4. UID、目录及权限

基线 VM 实测账号 wdsvc 999/GID994、wdguest 994/GID993、wdterm 993/GID992、wdgui 992/GID991；编号只属于该 VM，代码按账号查询。账号主组与 systemd 实际运行主组必须分开解释：浏览器实际主 GID 是非敏感 wdguest 数据组，补充自身图形组，umask 0007。

| 对象 | 原有设计及本轮处理 |
|---|---|
| Downloads | wdguest:wdguest，02770；不改成 0777，不增加 ACL |
| 原生完成文件 | 基线实际 wdgui:wdguest，0660，普通文件 |
| 下载临时文件 | 保留 Chromium 原生生命周期，Agent 不作为完成文件发布 |
| wdterm | 通过既有非敏感数据组使用普通文件，不获得 wdsvc 组 |
| wdsvc | 只认证及固定 IPC；直接打开访客路径应由 Linux DAC 拒绝 |
| `.gui` / Profile | 原私有挂载及访问边界不变，不复制进 APP 或 Agent 包 |
| `.wd-state` | 原 Agent 私有事务目录及挂载边界不变 |
| 访客卷 | 原 nosuid/nodev/noexec、内核容量及 inode 限制不变 |
| managed 策略 | root 管理 0644 非敏感系统配置，受 isolated 配置冻结校验 |

没有改变 UID/GID 架构、源代码目录、PostgreSQL、Socket 权限、网络命名空间、资源公式或单元限额。

## 5. 测试方法与证据边界

`download_bridge_origin.py` 仅在可丢弃 VM 的独立“外部” namespace 提供固定 TLS 测试内容；VIP `203.0.113.2:443` 不添加浏览器访问宿主管理端口的例外。测试 CA 仅导入该测试用户的 NSS，保留已存在数据库，不覆盖 Profile。服务完成绑定后降为 nobody，仅提供固定内存内容，不读写下载目标、不转发 URL、不接收后端 Cookie。

实际动作由 CI 主机 Chrome → 登录桌面 → noVNC → **可见的受限 Chromium** 发出；没有在访客浏览器启用 CDP。root 观察器仅用于实验中的目录元数据、固定测试文件哈希、原生窗口标题、只读下载 History 和 cgroup 指标。History 的 immutable 只读观察可能滞后，仅作测试辅助，同时核对真实文件大小和哈希，不能作为生产完成判定接口。

正向文件只由 Chromium 写入。创建符号链接、硬链接、填充空间/inode、SIGKILL、服务重启及 root 私有替身文件都是单独标明的故障/攻击夹具，不冒充下载成功。测试 CA、私钥、管理员公钥、观察器和 fault 文件不进入普通运行包。

测试对原生取消、网络中断、进程崩溃、GUI 关闭分别记录。原生取消必须观察 state=2；SIGKILL 不能代替用户取消。下载测试失败仍保留失败，并继续既有健康检查、SSH 和真实 VM 重启，最终任务仍返回失败。

CI 先执行 `--download-focus` 的单台 2 GiB 端到端门槛，再放行原四规格 staging/isolated 矩阵。它复用同一安装引擎、Artifact、下载脚本和重启控制器，不是第二套安装器；针对性门槛未通过时，后续矩阵记未执行，不能算通过。历史 R1 探针改为手动诊断，避免过期 Artifact 成为以后每次分支推送的隐性依赖。

资源观察包含测试阶段的 memory.current、memory.peak、memory.swap.current/max、memory.events、cpu.stat/max、任务数、宿主 RAM/Swap、卷空闲字节和 inode。单独 root 观察器为测试夹具，64 MiB/5% CPU 有界，不修改任何 WebDesktop 单元预算；其开销纳入测试宿主。缺失内核计数应记缺失，不能写成零。

## 6. 当前验收结果

**Phase 3C-D1: PARTIAL / BLOCKED。** 不具备完整验收或直接部署条件，不进入其他 3C 子任务或 Phase 4B。

固定受测代码：`f30bd8036330911a77c2720ae733960cbb348e16`。实现提交始于 `69c758714fca14f3ea390dc6e9378e8813afa5f0`，随后为观察器、测试夹具和 CI 编排修正；报告另行提交，不把报告 SHA 当作已经跑过测试的代码 SHA。

[Validate 39760840114](https://github.com/isAliceYoung/fa/actions/runs/39760840114) 完成：7 个任务成功、1 个失败。失败项为无 Swap VM 的 XIM 测试，`tests/linux_desktop_runtime.mjs:90` 等待提交后的 `Documents/ime-xim.txt` 出现超时；此前截图有正确的“ni hao”及“你好”候选，不能据此宣称最终提交成功。GTK/Qt、原生浏览器中文网页输入、另一个有 Swap VM 的 XIM 均通过。没有修改该输入链路，也没有增添 sleep、放宽断言或重复完整 VM 以覆盖失败；原因未确定，不能断言只是夹具问题，也不能直接认定由下载策略引入。

[Staging 39760840176](https://github.com/isAliceYoung/fa/actions/runs/39760840176) 完成：两个构建任务成功，专项失败，下游安装矩阵没有展开（API 中为一个 skipped 占位任务，对应九个计划配置**未执行**）。不能把 workflow 的 skipped 占位当作九组测试通过，或混入用例的 skipped 数量。

专项 [Artifact 12419782033](https://github.com/isAliceYoung/fa/actions/runs/39760840176/artifacts/12419782033)，到期 2027-01-20 10:08:28 UTC：重启前 **18 passed / 1 failed / 0 skipped / 10 not_executed**，重启后 **2/0/0/0**；实际 Live 健康前后各 **40/0/0/6**，boot ID 不同、管理员 SSH 恢复、PTY 两个槽位与两个租约守卫均回收。

当前 SHA 的正向链路：实际 Chromium 生成 `d1-normal.txt`，94208 字节、wdgui UID992/数据GID993、0660，SHA256 `118f3df6ae4da7cca073070aa7949d2e6a9bd434ac9a021cf16213a92746440c`；真实文件管理器预览/改名/本机保存及 Agent 复制/移动/回收站还原/ZIP 保持字节一致。六类正常名字和四类危险名字均完成真实浏览器测试。符号链接冲突的原生界面明确为 **Failed - Insufficient permissions**，Agent 403、root 私有替身内容保持。正在增长的 `.crdownload` 实际 327680 字节、0660，被列表隐藏且 raw/download 409；并发 PTY 响应 1157ms、文件列表 32ms。书签语义哈希 `5569603e502a8840763f39ae70fc867fa989e558fe7e831e2cccd7ac71cd546f` 和完成下载经真实重启保持。

失败的网络用例：origin 日志显示 Chrome 请求、每次发送 65536 字节后 `intentional-network-cut`；按该固定文件名过滤后的原生界面明确为 **d1-reset.bin / Failed - File incomplete**。夹具只接受 network error/failed/interrupted 等文案，`network_failure=false`，因此仍超时失败。没有把这个超时改记为安全通过；该失败不能证明程序错误地发布了完成文件，因为后续“最终文件不存在”的断言尚未执行。下一步可针对实际 File incomplete 状态修正观察器，但必须同时保留 origin 断网证据和最终文件缺失断言，再用新固定 SHA 重测，不能回填本次通过。

后续十项**未执行**：原生用户取消、浏览器崩溃及重开、GUI 关闭中的下载、真实空间耗尽、真实 inode 耗尽、源码目录替换、硬链接攻击、下载专项完整 UID/挂载边界、下载专项认证撤销、刷新及 Agent/Next.js 重启。一般 Linux/DAC/认证回归的成功不能代替这些专项未执行项。脚本及 `.desktop` 下载用例已检查真实字节与 0660；最终 `no-automatic-service-execution` sentinel 检查位于未执行的完整边界项，不能将该安全断言单独宣称为已通过。

| 当前 SHA 测试类别 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| Ubuntu/Debian 常规 Python、资源及状态准入 | 512 | 0 | 0 | 0 |
| 网页终端 IME 模型 + 真实 Chromium/xterm/CDP | 96 | 0 | 0 | 0 |
| 私有源码、UID、IPC、认证、SSH、WS | 55 | 0 | 0 | 0 |
| 实际文件 Agent API | 28 | 0 | 0 | 0 |
| systemd / PTY / 终端 UI（含待执行矩阵终端项） | 68 | 0 | 0 | 16 |
| GUI/Chromium/原生输入法（含待执行矩阵 GUI） | 143 | 1 | 0 | 45 |
| 本次 D1 专项及实际重启 | 20 | 1 | 0 | 10 |
| 本次专项前后 Live 健康 | 80 | 0 | 0 | 12 |
| 下游安装/恢复/健康矩阵 | 0 | 0 | 0 | 1014 |
| 下游资源矩阵 | 0 | 0 | 0 | 90 |
| 下游 D1 矩阵 | 0 | 0 | 0 | 279 |
| **必需用例及显式计划合计** | **1002** | **2** | **0** | **1466** |
| 独立原有资源实验 | 7 | 3 | 0 | 0 |
| **含独立实验合计** | **1009** | **5** | **0** | **1466** |

统计按当前 Artifact JSON 和工作流预先定义的矩阵用例数汇总；缺失结果只记未执行，不导入上一轮的通过数。下游共 1444 个计划位置：当前 staging 四组各 138、isolated 四组各 189、旧版兼容组 136；其中包括原有健康范围保留、旧包不适用项，均不算成功。专项另外 10 项未执行和两次健康保留 12 项，合计 1466。常规 JSON 的两条文字范围说明（完整 VPS 另测、生产未授权）不是两项自动测试，不重复加计；长期、生产及未开发未来功能另在正文列出，不声称 1466 覆盖所有未来待办。

构建/类型检查：当前与旧基线产物两组、Validate build 与 guest 集成两组，共 **8 次命令退出 0**；不再混入上述用例数。脚本语法、compileall、Node 语法及原 Host/Origin/终端命令策略检查成功，日志保留。

四规格完整 D1/staging/isolated：**1 GiB+Swap、1 GiB无Swap、2 GiB、4 GiB 均 not_executed**；只有单独 2 GiB 专项实际执行了上述子集，不能将其当作完整 2 GiB 矩阵通过。上一轮 R1 1938/0/0/119、独立实验 6/4 是历史结果，不能据此认定本次 R1 安装恢复无回归。

当前独立实验为无 Swap Chromium 两项 smoke 通过、Claude 三项失败；有 Swap 五项 smoke 通过；全部 `production_usable=false`。保留此前 Claude OOM，不因一次 smoke 通过恢复正式入口，不在本轮继续研究更多预算。

证据索引（均属当前完整 SHA）：regular Ubuntu `12417962482`、Debian `12417991933`，guest Linux `12418057059`、基础 systemd `12418255844`、PTY/UI `12418431061`、网页 IME `12417344409`、有 Swap GUI `12419088120`、无 Swap GUI `12419680477`。原始日志、截图、health.tar、JSON、cgroup 指标保留在两个 workflow 的对应 Artifact（验证证据到期 2027-01-20）；报告不长期跟踪复制整份机器生成结果。

本次 CI 主机安装字体时，`fonts-noto-cjk` 的 61.2 MB 下载从 10:11:46 持续至 10:34:13 UTC，解释了依赖准备阶段的长等待；不是 WebDesktop 运行期 CPU/内存故障。专项随后确实完成安装、下载子集、健康检查和真实重启，最终因为网络用例断言失败退出，而非以依赖超时替代实际测试。没有重复发起另一轮完整矩阵、放宽超时或资源配额来隐藏这些结果。

### 6.1 已保留的开发期失败

- 最初基线夹具未匹配浏览器实际数据 GID、缺少 xrandr 观察依赖、原生页面就绪标题复用等问题，保留在对应诊断运行，不是产品成功记录。
- `2b6c635...` 的 History 观察器被 Chromium SQLite 独占锁阻止；`69c7587...` / `b758848...` 后续超时实际是原生保存对话框在等待用户，不是已证明的目录逃逸。[最终旧版诊断 39745263448](https://github.com/isAliceYoung/fa/actions/runs/39745263448) 成功记录 `browserEscaped=false`、`save_dialog=true`、Agent 400 拒绝；它使用 R1 旧包，部分文件 raw 200 正是修复前的缺口，不能算新代码通过。
- `b758848...` 首轮 D1 超时截图显示页面已经加载但未点击下载；当时先怀疑标题匹配要求尾随空格，但截图不足以单独证明原生窗口标题及唯一根因。后续检查发现 `d4ca01b...` 的 X11 观察器身份/namespace 与服务不一致，遗漏 wdgui 私有图形组，并把查询失败隐藏成没有窗口。已复用现有成功观察器的实际服务 UID/GID/补充组及 namespace，查询失败明确报错，不放宽 X11 权限；不能把早期标题假设当作已经独立验证的产品根因。
- `d4ca01b...` 的 1 GiB 有 Swap及 2 GiB staging CPU恢复用例失败：CPU告警正常触发、使用率回落，但恢复循环在约90s内制造227/242组含fsync的CRUD，产生持续 `io_wait`。改为每个独立5s采样点做一次同样的认证/文件/PTY响应检查；原90s期限、CPU触发、配额断言、30s恢复滞后与全局normal断言保留。资源公式、告警阈值和生产采样模块无修改。后续 `05e6b08...` 已出结果的这两类用例为10/0，但不替代最终固定提交的重跑。
- `05e6b08...` 的孤立部署 UI 恢复了上个测试保存的最大化终端，遮住桌面图标，D1 尚未执行；关闭窗口后未等PTY彻底回收又导致健康检查观察旧/消失PID。已改为处理现有窗口/任务栏，并等待PTY及租约守卫停止后交接。没有用强制点击穿透窗口、删除桌面设置或重置数据库规避问题。
- `522e9b59535b10e3038538bcf338fbbbef7a021c` 的专项运行 [39750160531](https://github.com/isAliceYoung/fa/actions/runs/39750160531)、Artifact `12413328619`：下载用例重启前 14/1/0/14，重启后 2/0/0/0；两次 Live 健康各 40/0/0/6。真实下载、网页文件管理预览/改名、本机下载、复制/移动/回收站/ZIP、六类合法文件名及四类危险文件名的原生清理均有通过证据。符号链接冲突没有新的最终文件或可见 History 行，原夹具只等数据库而超时；这不是明确的安全拒绝证据。后来改为真实文件/哈希及按固定文件名过滤的原生错误界面交叉验证，仍不允许把超时直接转换为通过。

以上原始截图、JSON、日志及旧报告不删除。最终表只使用最终固定提交的执行结果；测试夹具失败、程序故障、资源拒绝和未开发功能分开说明。

### 6.2 固定版本产物

| 字段 | 核实结果 |
|---|---|
| 运行 | [Staging 39760840176](https://github.com/isAliceYoung/fa/actions/runs/39760840176) |
| Artifact | [staging-debian12-f30bd8036330911a77c2720ae733960cbb348e16](https://github.com/isAliceYoung/fa/actions/runs/39760840176/artifacts/12417937453) |
| 到期 | 2026-10-29 10:10:57 UTC |
| 内层文件 | `staging-runtime.tar.gz`，225858858 字节 |
| 独立复算 SHA256 | `91ccd1043bae48469f4b1ca839e659ecbaa81dd71638baea60204c63f444b80e` |
| manifest | Debian 12 / amd64 / glibc 2.36 / Node 22.23.2 / ABI 127 / 完整 f30bd803... SHA |

下载 Artifact ZIP 后逐字节读取**内层 tar.gz**复算 SHA256，与 sidecar 及 staging-artifact.json 一致；没有用 GitHub ZIP digest 代替内层哈希。检查运行包无 `app/.env`、D1 root 观察器或 TLS 夹具路径；固定策略从 root 专属 source.bundle 的受跟踪源码安装。产物构建成功不等于安装/功能验收通过；当前包仅供审查与后续授权实验，不建议更新真实 VPS。

## 7. 保留范围与后续工作

Claude 正式入口、旧极速浏览器/Playwright、网页与原生 GUI 双向剪贴板、拖拽、多 GUI 共存和任意应用启动继续关闭或未完成。正式 HTTPS、长期容量增长、旧系统迁移及 Phase 4B 不属于本轮。

真实 Evoxt VPS 未连接、更新、重启或部署。新包的真实 Windows/VPS 人工复验仍需独立审查与授权，不能把 Linux CI 或此前输入法本机验证扩展为新包已在用户 VPS 验收。

## 8. 修改文件与职责

| 文件 | 职责及影响 |
|---|---|
| `guest-runtime/secure_fs.py` | 原生部分下载名称分类、拒绝读取 |
| `guest-runtime/file_ops.py` | 列表隐藏、路径/名称/递归操作拒绝部分下载 |
| `guest-runtime/agent.py` | 固定 409 错误码，不返回部分字节 |
| `gui-runtime/browser-worker.py` | 启动前核验共享 Downloads 和 root 策略 |
| `gui-runtime/download-policy.json` | 两项固定的原生下载策略 |
| `src/components/desktop/apps/sysfiles-app.tsx` | Downloads 内原生进度/取消提示 |
| `webdesktop-deploy/scripts/setup-browser-layer.sh` | 初次安装 root managed 策略，拒绝未知文件 |
| `webdesktop-deploy/scripts/staging_install.py` | 在安装副作用前拒绝未知策略目录 |
| `webdesktop-deploy/scripts/isolated_deploy.py` | 将新增 managed 配置纳入原恢复冻结清单 |
| `tests/test_download_contract.py` | 5 项可移植辅助测试，不冒充实际 Linux 测试 |
| `tests/linux_files_runtime.py` | 真实 Agent 409/隐藏/字节保留回归 |
| `tests/download_bridge_origin.py` | 固定内容、文件名、限速与断网 TLS 夹具 |
| `tests/download_bridge_driver.py` | 登录/SSH 隧道/实际重启/有界资源观察编排 |
| `tests/download_bridge_ui.mjs` | 真 noVNC、GUI Chromium、网页文件管理、认证撤销和故障操作 |
| `tests/download_bridge_vm.py` | root 限定实验观察器与明确的故障注入；不装入运行包 |
| `tests/download_probe_driver.py` | 复用私有外部测试网络、TLS 及普通 NSS 初始化 |
| `tests/download_probe_server.py`、`tests/download_probe_ui.mjs` | 保留旧版原生下载/目录替换诊断证据 |
| `tests/staging_vm_driver.py` | 复用既有安装，下载失败仍执行健康检查与真实重启 |
| `tests/staging_resource_runtime.mjs` | 修正恢复夹具自身制造 I/O 压力的采样频率，不改生产阈值 |
| `.github/workflows/download-probe.yml` | 手动旧产物诊断，不作为每次推送的过期依赖 |
| `.github/workflows/staging.yml` | 固定 SHA 产物、专项门槛及原四规格矩阵 |
| `.github/workflows/validate.yml` | 取消同分支过时运行，原安全测试不删减 |
| `webdesktop-deploy/PHASE3C_DOWNLOAD_BRIDGE_REPORT.md` | 本报告；文档提交与受测代码提交分开 |

未修改稳定 `install.sh`、bootstrap、数据库 schema、认证协议、WS/PTY/IME 实现、资源监视器和阈值、systemd 单元模板、网络规则或原生浏览器沙箱参数。新增策略通过既有 source bundle 进入安装清单；不把用户下载或 Profile 放入不可变 APP。

## 9. 可复核命令与验收边界

以下测试命令均为一行，仅用于对应 CI 的可丢弃 Debian 12 实验环境，不能在用户 VPS 上直接执行实验控制器。

```sh
python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json
```

```sh
npm run build
```

```sh
npm run typecheck
```

```sh
node tests/terminal_ime_test.mjs
```

```sh
python3 tests/run_systemd_vm.py --staging --download-focus --ram-mib=2048 --vcpus=2 --disk-gib=20 --swap-mib=1024
```

```sh
python3 tests/run_systemd_vm.py --staging --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=1024
```

```sh
python3 tests/run_systemd_vm.py --isolated --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=0
```

其余 2 GiB/2 CPU/20 GiB/1 GiB Swap、4 GiB/4 CPU/30 GiB/1 GiB Swap 使用 workflow 中原矩阵参数。1 GiB 的既有访客卷为 512 MiB；2 GiB、4 GiB 的原自适应卷预算分别为 1024、2048 MiB，本轮没有扩大任何预算。不得将这些规格都表述为 512 MiB。

本轮 Linux Chromium 的自动化回归不能代替 Windows 搜狗原生输入法测试；保留此前本机人工验证成果，新包的真实 VPS/Windows 验收未执行。正式用户手动验收应在独立授权部署之后检查 Chromium Ctrl+J、Downloads、文件预览/管理、本机保存、关闭重开和退出撤销，本轮不提供或执行真实服务器更新命令。

## 10. 明确保留的限制

- 文件 Agent 原有 100 MiB 单文件上限及其流式/ZIP/超时约束没有变化。浏览器能写入卷不表示任意大小都能通过 Agent 导出；不能据此声称已经支持无限大小文件。
- `.crdownload` 和 `.org.chromium.*` 的保守拒绝覆盖整个普通文件域；合法最终文件刻意使用这些名称时也会拒绝。同一实际用户可以在受限 Bash 中主动改名，名称保护不承担防止用户自愿编辑自身文件的职责。
- 下载进度、失败、原生取消和重试由 Chromium Ctrl+J 管理，没有新增网页下载任务 API。Agent 不自动清理浏览器残留；空间及 inode 仍由原内核卷上限兜底。残留可以消耗有限容量，不能解释为系统盘或内存可以无限增长。
- 同一用户能改名普通 Downloads 目录。启动预检拒绝未知目录；运行期的替换仍需结合 Chromium 原生行为、openat2 与 DAC 解释，不能把“目录能改名”直接当成源码泄露，也不能只凭超时宣称安全拒绝。
- 本轮没有完成长期下载/Profile 增长测试、真实互联网任意网站兼容、正式域名及真实 Windows 用户端新包验收。这些未执行范围不算自动化通过项。
- 只要本轮必需的异常路径、四规格或安装恢复回归存在失败/未执行，D1 门槛保持 `PARTIAL / BLOCKED`。独立 Claude 资源实验中的历史/本次 OOM 失败继续保留，不开放正式 Claude，也不将它们包装成 D1 正常下载成功。

## 11. 资源证据及适用范围

当前受测提交 `f30bd8036330911a77c2720ae733960cbb348e16` 的常规 GUI VM 为 Debian 12、1 vCPU、1024 MiB、10 GiB，分别有约 1 GiB/无宿主 Swap；它们运行原 GUI 回归，**不是四规格 D1 下载矩阵的替代结果**。

| 原 GUI 回归观察 | 1 GiB 有 Swap | 1 GiB 无 Swap |
|---|---:|---:|
| Artifact | 12419088120 | 12419680477 |
| 聚合 memory.peak（至 desktop-runtime after） | 603.0 MiB | 621.8 MiB |
| 可见 Chromium memory.peak | 320.0 MiB | 320.0 MiB（计数略超过限额的页粒度） |
| 可见 Chromium memory.swap.current | 12.68 MiB | 0 |
| Chromium pids.peak | 84 | 83 |
| PCManFM 300s 内本机 health 最大延迟 | 10.98 ms | 33.18 ms |
| desktop-runtime 聚合 oom/oom_kill | 0/0 | 0/0 |
| GTK/Qt/XIM | 通过/通过/通过 | 通过/通过/失败 |

`high` / `max` 事件存在，不能写成未发生压力；此表 0/0 仅指上述阶段的 oom/oom_kill，不掩盖后续独立 Claude 压测的 OOM。健康响应延迟不是端到端人工交互延迟。原实验中浏览器单元 MemorySwapMax 为 192 MiB、GUI 及聚合另有各自约束；宿主 1 GiB Swap 不代表每个单元可用 1 GiB。

历史 `522e9b5...` 的 2 GiB/2 CPU/20 GiB/1 GiB Swap 下载专项保留 131 个样本、202.0s：聚合 memory.peak 889.3 MiB、浏览器 506.0 MiB、GUI 147.7 MiB、Agent 17.5 MiB、PTY 6.1 MiB；聚合 pids.peak 163，采样区间 CPU 峰值约 147.6% 单核（两核环境），该专项各单元观察到的 swap.current 为 0、oom/oom_kill 为 0。采样不是无限精度峰值探测；memory.peak 为内核计数，CPU 为相邻样本差分。它未执行完异常用例，不能拿来证明空间/inode 耗尽通过，也不是最终 SHA 的资源验收。

当前 f30bd803... 的 2 GiB 专项有 70 样本、147.5s：聚合 memory.peak **907.38 MiB**、浏览器 **507.54 MiB**、GUI **150.16 MiB**、Next **168.48 MiB**、PostgreSQL **28.40 MiB**、Agent **16.09 MiB**、PTY **6.09 MiB**。聚合 pids.peak **159**、浏览器 **130**，相邻样本 CPU 峰值约 **142.6%** 单核；观察到的各 WebDesktop 单元 swap.current 为 0，memory.events 的 oom/oom_kill 起止均 0。宿主 SwapFree 从 1047512 降至 1046228 KiB，不能据 WebDesktop 单元为零就说整机没有使用 Swap。卷最少可用 983117824 字节、最少空闲 inode 32359，**没有触发 ENOSPC/inode 耗尽**。本次样本间隔约 2.1s（观察器自身受限），不声称每毫秒峰值或长时间稳定。实际 PTY/文件响应见 §6；未完成故障恢复，不能将这些数据外推为 1 GiB 下载验收。

因此尚不能给出本轮四规格下载压力、失败恢复和低配稳定性的完整结论；不因缺证据调高任何限额或建议直接更新真实 VPS。磁盘/CPU/Swap/任务及原始 memory.events 保存在相应 Artifact，而非用当前报告的摘要替代原始日志。

## 12. 风险、门槛与下一步

| 等级 | 问题及触发条件 | 证据与处置 | 门槛 |
|---|---|---|---|
| HIGH | 当前 SHA 无 Swap XIM 最终提交验证失败 | 12419680477 的 desktop-runtime.json、ime-xim-candidates.png；需要针对提交/焦点/进程退出收集完整状态，不能只加等待或关闭中文输入 | 阻塞本轮完整兼容验收 |
| HIGH | D1 异常路径与四规格部署/恢复尚未具备全部当前 SHA 成功证据 | 原生取消、网络中断、崩溃、关闭、空间/inode 耗尽、目录替换等必须按实际执行结果验收；不能用正常下载或历史 R1 PASS 代替 | 阻塞 D1 PASS 和新包真实部署建议 |
| MEDIUM | History 可滞后、原生下载 UI 与自动化焦点不同步 | 历史 522e9b5 的符号链接用例失败；只接受新文件及哈希、正在增长的临时文件、匹配用例的明确原生状态，不猜测 | 先收敛专项观察器，再放行矩阵 |
| HIGH | 下载观察器按“新 History ID”选择辅助行，尚未严格绑定当前文件 | f30bd803 的 unicode 文件正向实际哈希正确，但其附带 history 是上一 spaces 文件；本次正向通过依据真实新文件和预期哈希，不依据该错配行。未执行的负向用例未来可能错误接受旧失败行，必须先绑定当前目标及用例，不得把任意新 state=3 当作当前失败 | 阻塞后续完整异常矩阵接受 |
| MEDIUM | 部分文件残留和 Profile 增长消耗有限容量 | 既有内核卷兜底，不自动删除；完整 ENOSPC/inode 及恢复证据仍需补齐 | 容量验收前不能宣称长期稳定 |
| LOW | 合法保留名称和超过 Agent 上限的文件不可直接导出 | 明确的保守拒绝及既有资源约束，不调整限额规避 | 记录限制，不额外开放路径 |

没有在已执行检查中发现可以读取 root 私有源码或获得服务身份的新越权成功证据；这不等于尚未执行的所有下载负向场景都已证明安全。已有 UID/DAC/IPC、安全路径和 Chromium 原生沙箱检查继续保留。

下一步应继续**收敛 D1**：先在同一可丢弃设施补齐原生失败/取消观察与 XIM 失败状态，再执行专项及四规格 staging/isolated 安装、恢复、健康与重启矩阵。若是 CI 依赖/测试夹具问题，应独立修复和保留旧失败；若证实产品故障，应在本轮边界内修复或单列需授权的关联问题。不得通过删除断言、伪造数据库状态、放宽原生沙箱/网络/DAC、调高配额或启用旧高权限路径使测试变绿。

只有 D1 所有必需条件满足后，才适合独立设计下一项 3C 子任务（例如最小文字剪贴板）；本轮不实施。Claude、旧 Playwright、图片/文件剪贴板、拖拽、多 GUI 和完整应用启动均保持原未完成/关闭状态。

当前停止在 `PARTIAL / BLOCKED`，等待独立审查。没有对真实 Evoxt VPS 执行连接、更新、重启或部署；main、Beta、生产数据库、用户文件、密码、Deploy Key、仓库可见性及禁用自动更新状态均未修改。报告提交 SHA 以最终交付及 Git 记录为准，避免在文档内自引用无法实现的提交哈希。

只读核对远端引用：main 仍为 `5922e1408de91e56771f1ea0bf94a9df1b7028cd`；Beta tag object 仍为 `76f93a1682845306877552868f8a8a77cae156a9`，peeled commit 与 main 相同。开发及报告仅推送 `feature/private-deploy-isolation`，Git 网络调用显式使用现有本机代理。
