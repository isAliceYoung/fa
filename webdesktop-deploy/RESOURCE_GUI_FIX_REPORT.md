# 真实 VPS 测试后的资源与 GUI 改进报告

本报告对应 `feature/private-deploy-isolation`。起点4ed395ef66c7b4f436edec990113a0af4238f38c，用户真实 VPS 已验收版本为祖先f195f90c01f38fcf93c20ee616f9e62a15063018。用户提供的只读31/0/12、live39/0/6及Windows人工结果属于用户实测；本轮代理未连接该VPS，也没有原地更新或重置。

本轮交付：自适应预算、认证聚合监测/预警、文件复制误报修复、GUI等比尺寸/原生最大化、Chromium原生新页及推荐搜索策略。固定代码3dca35b的15个CI任务全部成功，四种现代VM验收通过；另有4项既有资源实验失败，未记为通过。捕获并修正真实X11窗口探测竞态，但原VPS两分钟等待未复现、根因未定。真实VPS与Windows人工复验尚未执行，完成后停止待审查。

## 1. 提交与修改范围

- d84dcb35ebbbc53d6491c8ccddda793343a88b88：统一资源策略、监测、GUI阶段、协议限额与UI修复。
- 921754179a6a7eb3b9b1142a19a367cfc9cfb414：真实压力/并发、固定旧版对照、桌面自动化与安装矩阵。
- 39270f1a7d87f7de3e7cdc5e0b4571c5bfc8924a：压力恢复证据、登录hydration防护、真实菜单定位、进程身份回收检查、等比尺寸与操作说明。
- 147a32f9081a18bcba3fdc105ff9ff117d8d6803：仅改CI观察器/输入夹具和压力证据采集；复用此前已验证的地址栏确认流程，原产品代码不变。
- 31eeda2f74dfe595a355f7358cc0579c28447506：实际PTY槽位识别、只清理已记录的预期OOM失败状态、远端鼠标坐标和缩放后中文输入证据。
- d0506cfcb671d3b67abd2b0e9f0adf22fcac49f0：旧HTTPS UI回归改按认证后的关闭状态等待，不以固定2.5秒假定回收结束。
- 55f144cdda6f23a189288da9b174a481ce42aad9：异地登录测试主动关闭新设备合法恢复的窗口，验证worker/browser/guard全部停止后交接健康检查。
- 9fa0eaff3cb9195164536d57fb3f7f003f51e1fe：缩放指针测试点击中性文件区，避免误点历史下拉框持有GTK输入抓取，并核验真实Documents/Downloads导航。
- f8729a59be07548eee91e2b411fb510140d823fe：受保护journal增加有界启动失败/停止原因，窗口探测退出证据，热启动改连续三次。
- 05f0663c74fa15377d3f612013dc8710c128cc59：记录GUI子进程退出，刷新回归先等待工作区恢复再决定是否重开。
- ea7cf4ecbd6d45d4a63fcc62dc9a9a3c0bb1e551：真实输入测试检查文档焦点与私有Fcitx状态后输入；观察器仅CI使用，不给网页开放D-Bus。
- a37b9ab811ed956cac47385fd614e8198dc97ab4：根据真实BadWindow日志只重试销毁窗口引起的固定探测错误，最多两次且共用原4秒期限。
- 8cc79cd：修正CI输入法HTML夹具的UTF-8声明；在本地真实Chrome复现无声明时windows-1252导致脚本中文比较失真，并验证UTF-8修复，不更改产品输入法。
- 200334fd193a2ebdc54de1c7b9d6dc2fff464159：旧GUI回归的journal进一步捕获X_GetWindowAttributes同类BadWindow，纳入同一有界重试，不接受其他X错误。
- 3dca35b7b6f90350484723fdbe3604dac0b7a5de：旧HTTPS回归按真实异步X selection内容等待地址栏，不以固定150ms断言复制完成；仅地址就绪观察允许暂时无selection，最终内容仍严格核对。

39270f1之后未调整产品预算、权限或启动期限；后续产品修改仅包括有界诊断与上述窗口探测竞态修复，其余为测试夹具。没有因CI失败提高资源上限、关闭沙箱或删除安全断言。

完整修改清单共48个文件（相对仓库根，含报告收尾）：

| 范围 | 文件 |
|---|---|
| CI | `.github/workflows/staging.yml`, `.github/workflows/validate.yml` |
| 访客运行层 | `guest-runtime/file_ops.py`, `gui-runtime/browser-worker.py`, `gui-runtime/openbox.xml`, `gui-runtime/rfb_input.py`, `gui-runtime/worker.py` |
| 后端 | `server.mjs`, `src/lib/gui-gateway.mjs`, `src/lib/resource-monitor.mjs`, `src/lib/resource-monitor.d.mts`, `src/app/api/resources/route.ts` |
| 桌面 | `src/components/login-screen.tsx`, `src/components/desktop/apps/basic-gui-app.tsx`, `apps/monitor-app.tsx`, `apps/sysfiles-app.tsx`, `desktop.tsx`, `paste.tsx`, `resource-status.tsx`, `sys-store.tsx`, `taskbar.tsx`（后八项同在desktop目录） |
| noVNC | `public/novnc/core/rfb.js`, `core/util/desktop-size.mjs`, `WEBDESKTOP_PATCHES.md`（均在public/novnc） |
| 部署 | `webdesktop-deploy/scripts/resource_policy.py`, `setup-browser-layer.sh`, `setup-guest-volume.sh`, `setup-gui-layer.sh`, `staging_bundle.py`, `staging_check.py`, `staging_install.py`（均在scripts目录） |
| 测试 | `tests/desktop_capture.py`, `desktop_ui_runtime.mjs`, `gui_ui_runtime.mjs`, `linux_gui_runtime.mjs`, `resource_monitor_test.mjs`, `run_systemd_vm.py`, `staging_resource_runtime.mjs`, `staging_ui_probe.py`, `staging_ui_runtime.mjs`, `staging_vm_driver.py`, `test_resource_policy.py`, `test_rfb_input.py`（均在tests） |
| 文档 | `webdesktop-deploy/README.md`, `RESOURCE_ADAPTATION_PLAN.md`, `RESOURCE_MANAGEMENT.md`, `STAGING_TEST_GUIDE.md`, `RESOURCE_GUI_FIX_REPORT.md`（均在webdesktop-deploy） |

稳定install.sh、bootstrap.sh、原IPv4-only/Swap/PG恢复代码、旧数据库和用户文件均不迁移。源码生成欢迎文件与示例图片的取消继续保留。main、Beta、仓库可见性、生产Deploy Key与自动更新状态不改变。

本轮末次核对远端main及Beta解引用均为 `5922e1408de91e56771f1ea0bf94a9df1b7028cd`，Beta标签对象为 `76f93a1682845306877552868f8a8a77cae156a9`，与开始时一致。没有连接真实VPS、改变仓库可见性或创建Deploy Key。

## 2. 资源预算

公式、1/2/4 GiB理论预算及覆盖边界见 [RESOURCE_MANAGEMENT.md](RESOURCE_MANAGEMENT.md)。实际安装依据MemTotal和祖先cgroup计算，不把标称1 GiB当1024 MiB可用内存。执行层聚合预算让Chromium、PTY共享空闲余量，控制层和OS仍有余量；CPU随有效核心数扩展。无Swap额度为零，父子Swap上限不相加。

2/4 GiB矩阵采用20/30 GiB系统盘，以验证更大访客卷；不宣称10 GiB盘可容纳所有配置。新卷按RAM扩展并保留依赖安装后3/4 GiB系统剩余空间；现有卷不改。小机512 MiB仍严格有界，回收站/profile同样占空间。

最终轮实际内核MemTotal及生成预算如下，内存单位MiB；Chromium单元上限等于共享执行上限，不能与GUI/PTY上限相加：

| 标称配置 | 实际RAM | 平台 / 执行 | 基础GUI / 单PTY | 平台 / 执行Swap | 平台 / 执行CPU | 新访客原始卷 |
|---|---:|---:|---:|---:|---:|---:|
| 1核1GiB、1GiB Swap、10GiB盘 | 973.3 | 781 / 525 | 192 / 160 | 640 / 262 | 95% / 85% | 512 |
| 1核1GiB、无Swap、10GiB盘 | 972.3 | 780 / 524 | 192 / 160 | 0 / 0 | 95% / 85% | 512 |
| 2核2GiB、1GiB Swap、20GiB盘 | 1979.5 | 1723 / 1275 | 256 / 384 | 861 / 637 | 190% / 170% | 1024 |
| 4核4GiB、1GiB Swap、30GiB盘 | 3926.3 | 3446 / 2678 | 384 / 832 | 1023 / 1023 | 380% / 340% | 2048 |

GUI/PTY/浏览器仍有各自TasksMax、FD、协议预算和系统UID进程数等保护；4GiB fork实验在64个任务即遇到保留的PTY LimitNPROC=64，而单PTY cgroup TasksMax=125，不能宣称一定能用满每一个上限。UID级rlimit仍是防fork失控的独立保护，也可能限制同UID多个会话的总任务；本轮并未宣称任意高并发工作负载已适配。管理员仅可在计算边界内显式降低，不提供无限制选项。

## 3. 硬边界与性能预算

保持有限MemoryMax、SwapMax、TasksMax、CPUQuota、FD、输出/协议/并发限额以及整树回收。MemoryHigh、权重和应用子上限属于性能/抗失控策略，不是身份安全凭据；内核UID/DAC、私有Socket、namespace、固定命令、原生Chromium沙箱仍是安全边界。MemoryLow与OOM优先级是尽力保护，不能替代平台总上限和真实压力验收。

只允许安装时降低安全计算上限；没有随瞬时内存波动自动重配或硬件升级后的静默调参。旧lab配置保留已验收的有限参考预算，现代staging通过统一drop-in和实际cgroup断言验收，避免两套安装步骤。

## 4. Chromium 启动与并发

不能确认用户原VPS“两分钟”现象的唯一原因：没有那次阶段日志，也未连接服务器。新增认证、systemd激活、X11、D-Bus、IME、Openbox、Chromium窗口、WS和真实像素flush首帧计时，保留原有有限超时。以阻塞式受限窗口等待代替反复派生探测进程，明确WM和PCManFM就绪。

确认的本轮程序故障：05f0663、run39572183651的1GiB+Swap journal在08:58:35记录 `browser_window_failure`：探测退出1，`browser_exit=null`，X11报 `BadWindow (invalid Window parameter)`、`15 (X_QueryTree)`；网关在browser_spawned阶段2545ms安全关闭。说明活着的Chromium被窗口树探测竞态误判，既不是OOM，也不是两分钟等待。修复只在同一4秒期限内对该特定错误重新观察，最多两次；其他错误和子进程异常照常失败。原始失败截图、journal和结果保留在Artifact12332609394。[xdotool原始实现](https://github.com/jordansissel/xdotool/blob/master/xdo_search.c)在遍历动态窗口树时查询窗口属性；此源码用于解释实测日志，未把推断冒充用户VPS根因。

05f0663的NoSwap旧GUI回归Artifact12333103400同时记录 `3 (X_GetWindowAttributes)` 同类已销毁窗口错误，因此最终只匹配上述两种已观测查询。a37b9ab同规格staging在09:10:34实际记录 `browser_window_probe_retry` attempt=1，随后真实窗口和Google策略检查成功，证明不是仅靠Mock模拟重试。没有忽略真实浏览器退出/其他错误/超时。

固定f195f90在同规格Debian VM独立构建对照。前一轮921证据：1 GiB+Swap旧版首批RFB数据冷/热3403/3062ms，新版4100/3795ms；并发文件/认证/PTY P95旧414、新235ms。2 GiB新版2094/2036ms、P95 284ms；4 GiB2677/2874ms、P95 276ms。**这组结果不证明新版本启动更快，更没有重现两分钟；首批RFB数据不是网页首帧。**不同CI宿主存在抖动，不能据单次结果外推真实VPS。最终轮指标与首帧截图以第7节指定Artifact为准。

测试同时执行真实终端输入、文件CRUD/目录和认证检查，P95阈值2500ms、单轮最长5000ms；超标即失败，不以进程存活/零OOM代替可用性。冷启动指新profile首次启动，热启动指同VM重复启动；随后网页自动化已经是热启动，另行标注。

最终轮数据（ms）：

| 配置 | 冷启动首批RFB | 连续三次热启动首批RFB | 并发检查P95 / 最大 | 网页实际Chromium首像素flush（热） |
|---|---:|---|---:|---|
| 固定f195f90旧版1GiB+Swap | 2258 | 2188 / 1974 / 2035 | 239 / 331 | 旧版无此埋点，未执行 |
| 新版1GiB+Swap | 4000 | 3773 / 3545 / 3649 | 230 / 254 | 2581 / 2654 |
| 新版1GiB无Swap | 3089 | 3301 / 2302 / 2367 | 210 / 234 | 2203 / 2498 |
| 新版2GiB+Swap | 3077 | 2636 / 2487 / 2604 | 231 / 270 | 1936 / 1867 |
| 新版4GiB+Swap | 2358 | 2297 / 2364 / 2164 | 324 / 380 | 1730 / 1670 |

旧版真实初始页为about:blank，新版为用户要求的chrome://newtab/；同时预算、窗口就绪和采样链均已改变。这是完整产品版本对照，不是单变量微基准。最终轮没有证明启动加速；不同runner结果也不能证明NoSwap必然更快或更多CPU必然降低每次请求延迟。原两分钟等待仍需获准新VPS的阶段证据。

新版1GiB+Swap冷启动累计阶段：认证65、资源检查69、租约675、X11 890、D-Bus 1050、IME1135、Openbox1397、浏览器创建1521、原生窗口3204、WS就绪3219ms；首批RFB4000ms包含测试客户端握手/观察等待。首像素flush另由真实网页noVNC事件计时，不把两种时钟/观测点拼接成一个时间线。

最终轮压力与健康前快照（包含故意fork/OOM；MiB）：

| 配置 | 平台内核memory.peak | 5秒采样平台/执行最大 | 采样宿主/平台Swap最大 | 采样任务最大 | 压力CPU使用最大 / 节流 | 系统盘剩余GiB |
|---|---:|---:|---:|---:|---:|---:|
| 1GiB+Swap | 748.5 | 699.6 / 503.6 | 20.4 / 2.3 | 114 | 88.5% / 11.1% | 4.31 |
| 1GiB无Swap | 733.1 | 690.1 / 502.8 | 0 / 0 | 114 | 88.4% / 11.5% | 5.31 |
| 2GiB+Swap | 921.9 | 792.6 / 599.4 | 2.5 / 0.3 | 131 | 87.7% / 10.2% | 13.74 |
| 4GiB+Swap | 1006.6 | 463.4 / 304.3 | 1.0 / 0.3 | 144 | 86.7% / 13.0% | 23.15 |

CPU百分比按有效核心归一；采样最大不等于精确峰值，瞬时OOM可发生在采样之间。平台memory.peak包含页缓存及测试故障，不是应用RSS；父子值不可相加。1GiB后端/PG/文件Agent内核峰值分别187.9/39.5/21.8MiB，三者local OOM和OOM kill均为0；故意用户进程故障在父层级累计OOM kill分别增加4/3/4/4，不能称整机“零OOM”。原始PSI、cpu.stat、memory.events/local、磁盘/inode与恢复样本全部保留在resource-runtime.json、before/after.json。

## 5. 监测、提示和恢复

5秒固定路径聚合采样，响应不含进程命令/环境/任意路径；接口Cookie+页面session双认证、并发4/每秒12次、单飞缓存。读取MemAvailable、PSI、cgroup current/限额/events增量、CPU使用/节流、Swap增长、磁盘和inode。首轮/异常unknown，历史累计high不当新告警，父local与后代事件分开。

持续10秒升级常规警告/严重，连续30秒恢复，通知冷却120秒；新增OOM立即严重并保留30秒。压力、Swap和磁盘规则详见策略文档。拒绝严重压力下的新GUI启动，不杀现有工作、不自动重复启动。实测需先观察故障告警出现，再验证恢复，不能读到旧缓存normal就通过。

## 6. 文件与GUI体验

复制/剪切不再调用禁用的VNC桥接，内部复制、剪切、粘贴、重命名、回收站仍由Agent执行；浏览器本机授权粘贴/图片复制与VNC桥接区分。导航移除不可访问根目录入口，不放宽DAC。

PCManFM原生最大化；初始/后续尺寸共用等比计算，640×480至1600×1000、前端300ms防抖、独立RFB解析200ms下限。完整帧/分片、单屏、无剪贴板、像素格式和越界拒绝均有测试。真实UI核对Xrandr=canvas尺寸、同worker PID、鼠标/键盘、Fcitx候选和中文结果，不重启Xvnc处理缩放。

每种现代规格保留 `staging-ui.json`（真实显示尺寸/指针坐标/首帧/回收状态）、`staging-pcmanfm-resize.png`、`staging-google-policy.png`、`staging-pinyin-candidates.png`、`staging-chinese-input.png`。截图来自host Chrome→公共认证WS→noVNC→wdgui真实窗口，没有给访客Chromium开放CDP。私有X selection只由CI root观察器读取布尔/哈希证据，不向网页提供内容桥接。

Chromium用原生chrome://newtab/、root管理Google recommended policy，不覆盖用户搜索选择和profile；没有CDP或no-sandbox。仍仅一个GUI槽位；刷新新建GUI、不恢复旧进程，认证撤销继续回收。

## 7. 测试与历史失败

最新代码CI：[Validate deployment](https://github.com/isAliceYoung/fa/actions/runs/39576705427)、[Staging Debian 12](https://github.com/isAliceYoung/fa/actions/runs/39576705356)，完整SHA `3dca35b7b6f90350484723fdbe3604dac0b7a5de`。两个workflow共15个任务全部success、退出0。**这不表示下述资源可行性实验全部通过：实验仍有4项失败，单独列出。**

最终staging安装workflow的7项任务全部成功，进程退出码0；现代四规格各为：压力/并发10通过、UI5通过、live健康39通过、重启后live健康39通过，均0失败/0跳过。每次健康另有6项未执行，详情见下文。固定旧版压力基准4通过/0失败/0跳过/6未执行，旧版本不存在的新监测与压力机制不冒充通过；旧版前后健康各39/0/0/6。

| 最终证据Artifact | ID | 压力/并发（通过/失败/跳过/未执行） | UI | 健康 / 重启后健康 |
|---|---:|---|---|---|
| staging-install-evidence-1024-1-1024 | 12335750260 | 10/0/0/0 | 5/0/0/0 | 39/0/0/6，各一次 |
| staging-install-evidence-1024-1-0 | 12334464540 | 10/0/0/0 | 5/0/0/0 | 39/0/0/6，各一次 |
| staging-install-evidence-2048-2-1024 | 12334464612 | 10/0/0/0 | 5/0/0/0 | 39/0/0/6，各一次 |
| staging-install-evidence-4096-4-1024 | 12335036622 | 10/0/0/0 | 5/0/0/0 | 39/0/0/6，各一次 |
| staging-install-evidence-1024-1-1024-baseline | 12334771236 | 4/0/0/6 | 基线未执行，不虚记5项通过 | 39/0/0/6，各一次 |

健康未执行6项：外部管理员SSH往返（由VM driver单独真实检查，非健康脚本自行证明）、Claude正式入口、网页剪贴板、拖拽、多应用共享GUI、正式HTTPS域名。driver另保存真实管理员SSH、四个账号即使持有有效测试公钥也被拒绝、重启前后的证据；不把健康中的未执行改写为通过。上表现代矩阵所列检查总计372通过、48未执行，旧版82通过、23未执行（含跳过执行的5项现代UI）；0失败/0跳过。这里的跳过指测试结果skipped，基线workflow的条件跳过步骤并非通过；不与下面常规/单元测试混成一个不明分母。

已完成：Ubuntu与Debian常规各190通过/0失败/0跳过；Node聚合状态/尺寸各13通过；Debian源码DAC9通过；systemd启动/重启各9通过。类型与构建通过。未执行项属于独立环境任务，不混入通过数。

| 最终Validate已结束分组 | 通过 | 失败 | 跳过 | 未执行 | 退出码 |
|---|---:|---:|---:|---|---:|
| Ubuntu常规Python / Debian12常规Python | 各190 | 0 | 0 | 报告另列整机/生产两类，前者由独立VM任务覆盖，后者未授权 | 0 |
| Node聚合监测/尺寸 | 每环境13 | 0 | 0 | 0 | 0 |
| Debian实际源码DAC | 9 | 0 | 0 | 0 | 0 |
| 实际guest IPC/UID/认证/数据库 | 18 | 0 | 0 | 0 | 0 |
| Debian SSH Include/Match解析 | 3 | 0 | 0 | 0 | 0 |
| Next真实Upgrade认证/生命周期 | 12 | 0 | 0 | 0 | 0 |
| 文件流/事务/恢复/ZIP/容量 | 27 | 0 | 0 | 0 | 0 |
| 终端UID攻击Agent/事务/内部Next | 5 | 0 | 0 | 0 | 0 |
| 独立systemd VM启动 / 重启 | 各9 | 0 | 0 | 0 | 0 |
| PTY VM系统权限启动 / 重启 | 各9 | 0 | 0 | 0 | 0 |
| PTY实际WS/cgroup/撤销/故障，启动 / 重启 | 各14 | 0 | 0 | 0 | 0 |
| Chrome终端UI/刷新/接管 | 3 | 0 | 0 | 0 | 0 |

Shell语法检查、三个既有Node安全检查程序、Python编译、Next构建/typecheck亦退出0，未将未统计的内部断言编造成用例数量。

最终GUI参考实验VM两个任务均success：无Swap Artifact12336166491，有Swap Artifact12334883769；它们继续使用旧lab有限参考预算，现代staging自适应预算由上表单独验收。

| 每个GUI参考VM的断言组（两种Swap各执行一次） | 通过 | 失败 | 跳过 | 未执行 |
|---|---:|---:|---:|---:|
| 系统权限prepare / reboot | 各9 | 0 | 0 | 0 |
| PTY prepare夹具 | 14 | 0 | 0 | 0 |
| 基础GUI prepare / reboot | 各10 | 0 | 0 | 0 |
| OSC确认、noVNC、PCManFM共享文件及接管UI | 4 | 0 | 0 | 0 |
| 原生Chromium沙箱/网络/DAC/崩溃/背压撤销/GTK/Qt/XIM | 11 | 0 | 0 | 0 |
| HTTPS/WSS、中文、profile、重开/刷新、认证撤销 | 5 | 0 | 0 | 0 |

既有资源实验原样保留，没有新增Claude档位或开放入口。收集阶段标记为`collected`，不是全通过：

| 实验组 | smoke-only通过 | 失败 | 跳过 | 未执行 | 结果与边界 |
|---|---:|---:|---:|---:|---|
| 无Swap，Chromium320/384、Claude320/384/448MiB | 2 | 3 | 0 | 0 | Chromium两项短时探测通过；Claude三项Result=oom-kill，保留失败 |
| 1GiB宿主Swap，同上五项 | 4 | 1 | 0 | 0 | Chromium320及Claude三项仅短时通过；Chromium384完整原生renderer证据未确认，仍失败 |

有Swap的Chromium384探测`native_verified=false`、`security=observed-safe`、Result=success，无OOM、健康P95约19ms、X11 P95约16ms；这不满足完整原生renderer沙箱证据，因此保持failed-resource-or-interaction，不擅自归因为OOM、不卡顿或安全已证明。正式受限浏览器的真实内核/网页断言在两种参考VM及四种现代staging均通过；该独立实验未复验定位，本轮不扩大研究范围。Claude即使短时没有OOM，也不等于真实持续交互验收：320档Swap峰值192MiB已触顶，启动memory PSI full约12%、CPU PSI some约67%，不得据此启用正式入口。资源实验合计6短时通过/4失败/0跳过/0未执行，security_failed=0也不能代替每项完整证明。所有实验production_usable=false。

CI实际入口（只用于可丢弃测试环境，依赖/测试包准备见对应workflow；每条命令独占一行）：

```bash
python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json
node tests/resource_monitor_test.mjs
python3 tests/run_systemd_vm.py
python3 tests/run_systemd_vm.py --pty
python3 tests/run_systemd_vm.py --desktop --swap-mib=0
python3 tests/run_systemd_vm.py --desktop --swap-mib=1024
python3 tests/run_systemd_vm.py --staging --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=1024
python3 tests/run_systemd_vm.py --staging --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=0
python3 tests/run_systemd_vm.py --staging --ram-mib=2048 --vcpus=2 --disk-gib=20 --swap-mib=1024
python3 tests/run_systemd_vm.py --staging --ram-mib=4096 --vcpus=4 --disk-gib=30 --swap-mib=1024
python3 tests/run_systemd_vm.py --staging --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=1024 --baseline
```

以上VM命令在Actions临时Linux runner执行，不是本Windows工作区有常驻VM，也不是用户VPS安装命令。SSH测试用户/临时公钥、浏览器观察器、压力文件/故意OOM以及一次性测试DB均只存在于可丢弃测试环境。

历史证据保留：

- d84 staging run39558271170：1 GiB+Swap安装在resource-policy阶段因4 GiB磁盘预留过度保守停止；4 GiB第一次PCManFM可见检查失败38/1/6。随后分析依赖安装后剩余空间及窗口就绪，改3/4 GiB分档与明确原生窗口等待，非删数据/加无限sleep。
- d84 validate run39558271013：GUI包严格白名单漏登记新增root只读解析模块；已补白名单，权限断言保留。
- 921 staging run39561416529：4种新规格压力各9通过，但网页复制菜单选择器忽略图标文字，UI各0通过/1失败/4未执行；截图显示菜单存在。旧版对照4通过，旧报告按类别计未执行，最终改逐项列出。
- 921 validate run39561416498：NoSwap UI输入早于hydration，密码状态丢失；新增就绪前禁用输入。Swap守卫故障测试把deactivating当完成，并直接以PID存在断言；改同一有限7秒内核对完整停止及PID/starttime身份消失。均不是扩大运行资源或放宽认证来过测试。
- 921 OOM恢复测试发现可能读旧缓存normal，最终轮必须先真实见到OOM告警再恢复。早期9通过不冒充已证明这条更强断言。
- 392 staging run39564623906：2/4 GiB及NoSwap压力10/0/0/0；1 GiB+Swap压力8通过/2失败（磁盘回收后的旧45秒测试期限、随后CPU告警要求恰为warning而忽略仍处critical的情况）。原始历史保留，不能用新版结果抹掉。UI三种规格均2通过/3失败/0跳过/0未执行：复制及异地登录撤销通过；root观察器漏装xrandr；新地址栏夹具漏复用既有远程输入确认，真实截图显示data:首字符丢失，导致输入法页面未加载。修正测试依赖与实际地址栏哈希确认，不放宽RFB协议。恢复观察按PSI avg10衰减加30秒迟滞及采样周期使用有限80秒；不延长产品启动超时。采样按at去重，保留各压力阶段而非被重复缓存挤出。若仍失败按实际指标处理，不继续加sleep。
- 147 staging run39566161205：1/2 GiB及NoSwap压力10通过，4 GiB压力9通过/1失败；journal证明终端合法使用槽位2，但检查器硬编码槽位1。2 GiB UI5通过，其他已执行UI4通过/1失败，低配chrome://policy截图已显示全部Google推荐策略，但固定选择发生在WebUI仍加载时，改有限期内重新选择并验证真实内容。随后健康检查发现预期故意OOM保留failed状态，专门记录并只reset已确认oom-kill的测试单元；普通健康检查仍严格失败。
- 31 staging run39567768517：四种新规格压力均10通过；4 GiB UI5通过、健康39通过且重启后39通过。2 GiB UI5通过，健康35/4/0/6：新设备按既有产品行为恢复自己的浏览器窗口，测试关闭context后立即交接健康检查，根本原因是测试未等待新窗口清理。1 GiB两种UI4/1/0/0，严格原生尺寸检查暴露指针夹具误点PCManFM历史下拉框、输入被GTK弹出菜单抓取；截图保留，改中性区域点击并核验真实目录标题，而非放宽尺寸断言。d050轮2/4 GiB均完整安装/重启成功，低配同一夹具失败保留。
- 392 validate run39564623964：NoSwap整项成功，Swap基础GUI10×2、UI4和原生浏览器/IME内核11项均通过；旧HTTPS回归2通过/1失败/2未执行，固定2.5秒后重开仍碰到stopping。改使用已经受认证保护的明确关闭状态；不自动重连、不延长产品启动期限。既有资源可行性实验按原矩阵继续保留，资源失败另计，不把收集成功当应用可用。
- 9fa staging run39569507280：2/4GiB与NoSwap完整成功，1GiB+Swap压力9通过/1失败（热启动503），下游UI及健康未执行。f872/05增加诊断后定位上述真实X11竞态；不是用下一次成功抹除这个失败。
- f872 staging run39571767388：四种规格压力均10通过（各含三次热启动），2/4GiB及NoSwap完整成功；Swap UI4通过/1失败。截图显示输入法测试在页面标题出现后立刻切换/输入，前几个字符丢失，未出现候选；后加实际文档焦点、私有只读Fcitx状态和中文提交观察，不增加固定sleep。05同规格UI4/1，但失败为已捕获BadWindow，中文测试通过。两种不同原因分别保留。
- d050旧HTTPS UI曾3通过/1失败/1未执行：刷新时读取尚未hydration的空DOM，误判没有恢复窗口；05先等待工作区就绪和恢复窗口明确open/closed，再按已有“重新打开”操作重开，不恢复旧进程或扩大租约。
- a37 staging run39573717331：四规格压力各10通过，UI各4通过/1失败；真实截图均已输入“你好”，失败在新夹具标题断言。独立Chrome实测无charset的data:text/html按windows-1252解析，脚本字面量中文失真，而真实输入为Unicode；加charset=utf-8后断言成功。这是可重现的测试夹具错误，不是输入法资源限制或关闭隔离的理由。下游健康/重启未执行，保留原始失败Artifact。05 Validate的NoSwap旧HTTPS UI为2/1/0/2，journal为上述BadWindow；Swap整项成功。f872旧HTTPS同类失败也保留。

- a37 Validate run39573717197的NoSwap旧HTTPS UI为0/1/0/4，地址观察在固定150ms读取时X selection尚未建立（`target STRING not available`）；并非访客网页剪贴板开放失败。改为有限10秒等实际地址一致，超时仍失败；未添加新的固定sleep或重复执行整个场景。
- 200 Validate run39575625202的Swap旧HTTPS UI初次导航实际读到`ps://example.com`，不是完整HTTPS地址，0/1/0/4；保留首字符缺失的失败，不将错误页面算成功。最终3dca两种完整HTTPS回归均5通过，现代四种UI均5通过；没有据单轮成功宣称所有高速自动输入时序问题永远消失。

尚未实施功能：Claude正式入口、Claude Code、网页VNC剪贴板、拖拽、多应用GUI共存、完整跨登录进程恢复、正式域名。没有在本轮偷偷开放。用户VPS新版本实测和Windows人工复验未执行；没有授权。

## 8. 风险与后续边界

小机更大执行上限允许利用空闲资源，也可能增加Swap/磁盘写入；父组有限上限和前置告警仍不能让任意重网页变流畅。内存压力夹具故意产生用户单元OOM，应与后台/PG/Agent OOM分别统计。CPU配额保留管理余量但不是硬实时保证；低CPU/IO的宿主争用仍可能导致延迟。

窗口上限和窄视口最小分辨率会保留缩放，不能宣称任意4K画质；性能结果仅覆盖实际测试网页与负载。旧版启动对照没有新阶段/遥测字段，按未执行处理。

空进程树下cgroup memory.current仍可能包含可回收文件缓存，不应要求关闭应用后该值归零。用真实UID进程、单元MainPID/状态、后续压力下回收及控制链响应共同判断，不把缓存记账当作残留应用。4GiB实验没有把所有应用同时压到聚合内存上限，不外推为重网页/大量标签/多应用稳定性验收。

## 9. 下一次部署与停止

只提供固定代码SHA对应、Debian12 amd64/Node22.23.2 ABI127的CI产物，不在1 GiB VPS现场构建。具体单行安装/检查步骤见 [STAGING_TEST_GUIDE.md](STAGING_TEST_GUIDE.md)。下一次须由用户批准新的全新测试机或单独核准的快照恢复方案；不得直接覆盖已运行/opt/webdesktop，不删除状态标记冒充全新。

产物内部tar.gz SHA256必须从可信CI摘要独立核对；外层GitHub ZIP digest另列，不能混用。保留期7天，过期应重新构建同一固定SHA并重验。失败保留root状态/脱敏日志；仅start-service提供有限finalize恢复，其他阶段优先用事前快照/另建测试机，不格式化未知数据。

已下载核对的固定3dca35b产物：Artifact ID12333994439，`staging-debian12-3dca35b7b6f90350484723fdbe3604dac0b7a5de`；外层ZIP SHA256 `8f541c117182559a6dfdcedf692597d2aff8b0e9306d9b399de9f562283f6b97`，内部tar.gz SHA256 `e759e4f690d5ffc3e9a1b73a99187e854cfce35eac9d03493793f294f203798a`，压缩包225610294字节，归档内容约762MiB。29604项内部清单、29573个普通文件哈希全部匹配，其余31项为包内符号链接；未发现.env/login.json/部署私钥文件，CI另检查随机凭据不嵌入构建。兼容Debian12 amd64、glibc2.36、Node22.23.2 ABI127。2026-10-28到期；本段固定值不能用于其他SHA产物。

仅获准后的全新机器使用（替换hostname，按inventory追加合法管理员；本轮未执行）：

```bash
git -C /root/fa checkout --detach 3dca35b7b6f90350484723fdbe3604dac0b7a5de
sudo bash /root/fa/webdesktop-deploy/install-staging.sh --commit 3dca35b7b6f90350484723fdbe3604dac0b7a5de --artifact /root/staging-runtime.tar.gz --artifact-sha256 e759e4f690d5ffc3e9a1b73a99187e854cfce35eac9d03493793f294f203798a --authorize-new-vps '替换为实际hostname' --admin root --origins 'http://127.0.0.1:3000,http://127.0.0.1:18890'
```

报告收尾提交只修改文档，部署工具checkout和运行产物仍固定上述已验收代码SHA；不要将更晚的文档HEAD与这份产物混用。checkout须为获准新机上root管理且干净的工具副本，不能对已有部署或未知改动执行切换。

本轮代码与CI完成后停止等人工审查，不自动连接、修改、重置真实VPS，不进入生产迁移或其他功能开发。
