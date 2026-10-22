# Phase 3C-D1 收敛补审：任务绑定、异常下载与 XIM

本轮只收敛 D1；基线报告 `8d0a0e86aacb4fed2e3dc44704552eb4a7034d98`、运行代码 `f30bd8036330911a77c2720ae733960cbb348e16`。旧报告与原始失败不改写。最终完整回归快照固定为 **fb6823b4349f1bff19ad3791cc5dd7c5c904a125**，最后执行代码/CI 变更为其父提交 **3307a1c654ced31081358fe5c47e9116691999a9**，快照仅额外纳入本报告草稿。最终测试结果见末节，不使用旧 SHA 冒充新快照成功。最终报告提交是后续仅文档提交，完整 SHA 以最终交付/Git 记录为准，避免文档自引用哈希。

## 下载观察规则

每次点击生成独立 32 位十六进制任务标识，并贯穿受控 HTTPS 链接、origin 请求/传输日志、History URL chain 和测试结果。History 必须同时具有新的 ID、非空 GUID、精确任务 URL、Downloads 内合法目标；已知目标还必须匹配文件名。多行歧义、缺 URL/GUID、未提交或查询错误均为 null/无可信记录，不能猜测状态。

正向继续以真实新文件、UID/GID、模式、字节数和 SHA256 为依据，根观察器不写正向文件。异常状态需明确原生界面或可信当前任务 History；网络中断另核对原始 HTTPS 断网记录；失败后继续检查目标路径、Agent 列表以及 raw/download 均未发布完整文件。

新增确定性观察规则测试 10 项，涵盖其他任务 state=3、旧任务同名、新 ID 错配、错误目标、歧义及缺失记录，以及 File incomplete 只能用于绑定当前任务的明确失败界面。

## 第一轮专项证据

测试控制器 `75c1d6d1ec3a2888559c6b3d5fea3aacd2a07754`，运行包仍为独立核验的 f30bd803 固定产物，二者不得混称同一 SHA 的新构建。

[专项运行 39769655957](https://github.com/isAliceYoung/fa/actions/runs/39769655957)，下载 Artifact `12422327619`：29 项全部执行，27 passed / 2 failed / 0 skipped / 0 not_executed，真实重启另 2/0/0/0。File incomplete + 精确任务 origin intentional-network-cut + 无最终文件 + Agent raw/download 404 已通过。真实空间/inode 耗尽、关闭恢复、目录替换、硬链接、DAC/挂载、双重认证撤销、Agent/Next 重启及 Profile 持久化均取得对应当前任务证据。

两项失败继续保留：取消点击打开了菜单却未选择取消，截图仍有下载进度；崩溃已导致 browser Result=signal、PID 归零及 GUI/租约停止，但通用 idle 夹具只接受 inactive，从而误拒绝了预期 failed 状态。后续修正仅限测试交互/观察，不改变服务生命周期。正常关闭仍要求 inactive；只有明确 SIGKILL 故障场景允许预期 failed，同时要求 MainPID=0、cgroup 无进程、GUI/租约 inactive 及成功重新启动。

## XIM 原始证据与假设检验

历史 f30bd803 无 Swap Artifact `12419680477` 的 journal 在 **10:24:19 UTC** 明确记录 `GUI input rejected: GUI queue budget`，与原 XIM 最终文件未创建相对应。

同一 1 vCPU/1 GiB/10 GiB/无 Swap 的 D1 对照 Artifact `12421654200`：候选截图正确；数字 1 后 script 记录的真实 Bash 回显包含 `printf '%s' '你好`；焦点仍是 Xterm，Fcitx 从 2 变 1。随后长 ASCII 命令尾部尚未收完（回显止于 `' >`），服务端再次记录 GUI queue budget 并关闭图形会话；没有执行回车，也没有 Bash 命令执行错误。这不支持“Fcitx 没有提交中文”的解释。

旧 `desktop_lab.mjs` 将每个字符的按下/释放拆成两个立即发送的 WebSocket 帧。原 `"' > /home/wdguest/Documents/ime-xim.txt"` 已超过 32 字符，可突发产生超过 64 帧，撞上原服务端 64 项队列保护；无 Swap/单核等时序使夹具更易暴露问题。修正方案为按原 RFB 流协议批量封装：每帧最多 4096 字节，固定测试文本最多 1024 字符，保留发送回调背压和原事件顺序。没有增加等待时间、扩大队列/资源限制或修改 Fcitx/沙箱。

R1 对照 Artifact `12422308101` 也在原始、状态检查变体及压力顺序之后全部复现相同现象：中文和焦点均正确，命令尾部只到 `> /home/w` 或更早，journal 在 11:31:48、11:31:53、11:38:06 UTC 记录相同 GUI queue budget。第一轮 R1/D1 各孤立两项失败、完整原顺序各 10/1；它们是故障诊断结果，不因为收集 job success 就计为通过。

这结合历史日志定位为测试交互故障：未经节流/合并的测试客户端输入撞上现有保护，既不是中文没提交，也不是降低保护才能解决的产品资源故障。原始突发发送保留为单独故障复现，不能算中文输入成功。修正后的正常测试仍须验证真实中文提交、焦点、命令、回车及最终文件恰为“你好”。第二轮控制器 `1316bae4615db1721b1bc5e3386819e47f6b1a54`、[专项运行 39772345098](https://github.com/isAliceYoung/fa/actions/runs/39772345098) 的 JSON、截图、固定测试命令回显与退出码已核对：R1 Artifact `12424276975` 与 D1 Artifact `12424235642` **各孤立 2/0/0/0、完整原顺序 11/0/0/0**。两者正常流程均显示候选、数字 1 提交“你好”、关闭输入法后仍聚焦 Xterm、完整命令尾部和 Enter 到达 Bash、无执行错误、最终文件精确为“你好”，正常流程不触发队列拒绝。刻意保留的旧突发发送两者仍触发 `queue_budget_rejected=true`；这是预期故障再现，未计入正常输入成功数。R1/D1 的生产 `gui-gateway.mjs` Git blob 完全相同：`6ae98a803c656e5ae601a510d1ef20a0d0976d5c`。

## CI 门槛与权限边界

- D1-A：正常下载到网页文件管理器及本机；受影响时专项重验。
- D1-B：单台 2 GiB 专项异常与安全边界；失败保留，独立用例继续收集，依赖状态必须清理并核验。
- D1-C：B 通过后再以新固定 SHA 构建并执行完整四规格安装、恢复、GUI、IME、PTY、资源及重启回归。

`[d1-focus]` 明确选择专项和轻量回归，跳过的重型 workflow/矩阵属于未执行，不能算通过；不改变最终验收标准。R1/D1 对照使用精确旧包 SHA256、同一 pinned Debian 镜像和原 lab 预算，控制器与运行代码分别记录；既有 Claude 安装/资源实验不在专项中新增运行。

所有变更限于 tests、CI、报告。没有新增 root/wdsvc 下载中转，没有修改 wdsvc/wdguest/wdterm/wdgui、DAC、资源策略、systemd 单元或 Chromium 沙箱。真实 VPS、main、Beta、生产数据和密钥均不操作。

## 第二轮下载与新 SHA 再验证中的失败

1316bae 控制器 + 固定 f30 包的 2 GiB 专项 Artifact `12423022487`：29/0/0/0、真实重启另 2/0/0/0，前后健康检查各 40/0/0/6。原生取消真实显示 Canceled（并非 Failed），同一任务 `a956176ab3a633cab43def012e4860b1` 的 Chromium origin 传输在 851968 字节 peer-closed，少于完整 8 MiB；最终路径/列表不存在，Agent raw/download 均 404。History 尚未可靠提交，明确记为 null，不伪造 state=2。崩溃后 browser failed/Result=signal、三个 cgroup 清空、GUI 与租约 inactive，重新下载及旧数据保持均通过。

随后以 `382057b42b8fade988658b649520bae93db17b0d` 构建新包并重新启动 D1-C 的门槛，未把 f30 的成功算作新 SHA 成功。[39773616592](https://github.com/isAliceYoung/fa/actions/runs/39773616592) Artifact `12424481240` 实际 **21 passed / 2 failed / 0 skipped / 6 not_executed**，重启另 2/0/0/0、前后健康各 40/0/0/6。两项失败为网络状态与空间耗尽状态的 UI 观察超时；真实截图分别已经显示 **Failed - File incomplete** 和 **Failed - Disk full**，精确任务 origin 断网及 Agent 未发布检查仍完成。截图证明 Ctrl+F 打开了浏览器页面查找浮层，而不是 Downloads 搜索过滤，复制文本含其他卡片，观察器正确拒绝使用这种不唯一证据。没有修改分类断言来接受混合结果。

该次完整四规格安装矩阵被安全门槛阻止，属于 not_executed。后续修正仅通过原生 VNC 鼠标点 Downloads 自带搜索框，保留“只含当前文件名”要求；异常恢复若再次失败增加单独诊断，不能静默将依赖后续用例算通过。只启动单台 2 GiB、固定 382057b 运行包的下载专项，未再次构建或启动完整矩阵。

## 修改清单和复用关系

- `.github/workflows/d1-focus.yml`：仅专项 2 GiB 下载及固定 R1/D1 无 Swap 对照；可用 `[download-only]` / `[xim-only]` 排除无关专项，不生成替代运行时。
- `.github/workflows/validate.yml`、`.github/workflows/staging.yml`：专项提交不调度昂贵安装矩阵；普通提交仍保持完整回归；新增 10 项观察规则和 5 项 RFB 分包测试。全量与专项并发组分开，避免新专项取消正在收集的全量证据。
- `tests/download_observation.mjs`、`tests/download_observation_test.mjs`：纯任务绑定和拒绝状态规则及攻击/歧义测试。
- `tests/download_bridge_ui.mjs`、`tests/download_bridge_vm.py`、`tests/download_bridge_origin.py`：原生用户交互、精确任务 URL/History、原始 TLS 传输、真实文件与 Agent 交叉验证；独立失败后只恢复已知槽位/验证旧文件，无法安全恢复就保留后续未执行。
- `tests/rfb_key_packets.mjs`、`tests/rfb_key_packets_test.mjs`、`tests/desktop_lab.mjs`：修正测试客户端的有界 RFB 流，保留旧突发实现用于故障再现；不改服务端 64 项队列/字节上限。
- `tests/xim_diagnostic.mjs`、`tests/linux_desktop_runtime.mjs`：固定测试词和固定命令的候选/选词/焦点/命令/回车截图和 Bash 回显；不记录真实用户键盘或环境变量。
- `tests/xim_focus_driver.py`、`tests/run_systemd_vm.py`、`tests/staging_vm_driver.py`：复用原有可丢弃 Debian VM、原 lab/systemd/身份和固定包校验，明确测试控制器 SHA 与运行包 SHA；不接受真实 VPS 目标。
- 本补审报告：保留基线报告、历史失败和阶段门槛；不长期提交机器生成 JSON/截图，原始证据保存在 CI Artifact。
- `tests/prepare_vm_host.sh`、`tests/test_vm_host_tools.py`：CI 宿主工具准备专用守卫、官方 HTTPS 包源/隔离临时索引，拒绝普通服务器和任意额外 APT 参数；不被 VPS 安装流程调用、不安装到访客执行包或 Next 应用运行目录。root 私有 Git checkout 仍包含 tests 源码，不将其解释为向访客分发测试工具或凭据。

运行时代码、部署脚本、systemd 模板、权限、资源预算、Chromium 参数及终端前端 IME 修复均无改动。RFB 是现有协议支持的数据流分包，未扩大后端命令或任意应用启动入口。

## 原生选区的进一步纠偏

`897336b3b2bd0bf117f8e71f9984545c3d8b264f` 专项 [39775735348](https://github.com/isAliceYoung/fa/actions/runs/39775735348)、Artifact `12424208791`：20/4/0/5、重启 2/0/0/0。截图中的搜索框为空，未过滤；当前单项 UI 读取仍可能取到其他卡片。按严格规则继续失败，并保留恢复失败 after inode exhaustion 的定位信息；不能用该次截图直接回填后续 5 项通过。

本机独立 Chrome 155 原生下载页实验确认每张 `downloads-item` 使用 Shadow DOM；在卡片文字上 Ctrl+A 的选区只覆盖那张卡片。独立试验仅用于观察器定位，不作为 Linux/VNC 原生取消验收，也没有连接用户 VPS。普通 `?search=` URL 在该版本未自动填充 Downloads 搜索，未将它当作可用方案。

`719e3f2a66bd2ea73c9c1fb57ec33142721bf7e5` 专项 [39780106785](https://github.com/isAliceYoung/fa/actions/runs/39780106785)、Artifact `12428265052`：**28/1/0/0**、重启 2/0/0/0。File incomplete、真实空间/inode 耗尽、崩溃/关闭及全部其他安全边界已通过。唯一失败仍是取消后复制原生状态：截图已明确显示 `d1-cancel.bin / Canceled`，但空白处点击未重建选区，一次性剪贴板标记仍未被替换，因而没有接受旧文本或猜测 state=2。该项仍计失败；进一步修正仅把选区锚定在当前任务的非可执行文件名文字上，并在观察异常时仍执行 origin/未发布检查。

新版观察器不再依赖下载搜索框或全页面 Ctrl+A：从最新原生卡片的文件名建立选区，要求真实新复制文本包含精确文件名且不含其他测试下载名；同时核对同一唯一任务 token 的 HTTPS 请求/中断记录、真实文件和 Agent。一次性选区标记只写入可丢弃测试显示的私有 X11 剪贴板，不能写下载文件、History 或任何正向下载内容。无法取得新选区时仍失败，不读取宿主用户剪贴板，不开放产品剪贴板功能。

## 第一次 D1-C 的关联失败

[Validate 39773616602](https://github.com/isAliceYoung/fa/actions/runs/39773616602) 的无 Swap GUI Artifact `12425382929` 包含完整 XIM `desktop-runtime` 11/0/0/0、原生 GUI/浏览器 UI 与真实重启证据；不再出现原来的 XIM 文件未创建。带 Swap GUI Artifact `12424815914` 在 OSC 审阅提示 UI 等待超时（0/1/0/3），尚不能从截图/日志判断输入发送、焦点或程序故障；不将其伪称为中文输入故障，也不修改生产 OSC、认证、GUI 生命周期、队列或资源限制来规避。后续依赖未运行，按计划项记 not_executed。完整矩阵仍受下载门槛阻止。

另发现旧脚本/.desktop 测试仅检查受保护 `/opt/.../D1-EXECUTED` 标记缺失，不能单独排除 wdgui 下的意外执行。本轮补强测试负载，同时尝试写普通访客 Documents 和受保护应用目录标记；先用真实 wdgui 检查普通目录可写，前后都要求两处标记不存在，并保留下载文件 0660/nosuid/nodev/noexec 检查。这只加强测试，不改任何应用启动或文件执行能力；历史结果不能倒推满足补强后的断言。History 目标还明确拒绝空名称、`.`、`..`，对应确定性攻击测试保持严格。

第一次完整回归受测 SHA **382057b42b8fade988658b649520bae93db17b0d** 的两条 workflow 原始证据逐项计数为 **997 passed / 3 failed / 0 skipped / 1500 not_executed**。其中 regular 542、IME 96、安全权限 55、文件 API 28、systemd/PTY 68、GUI/Chromium 105、下载 smoke 23、前后健康 80 项通过；3 项失败是下载 UI 2 项及带 Swap OSC UI 1 项。未执行包括门槛阻止的完整安装矩阵 1444 项、该 GUI 流程后续 38 项、smoke 后续 6 项及健康明确未支持的 12 项。这里的 1444 包含 8 个当前规格/入口组合与 1 个旧基线兼容组合，不能因为 workflow 显示一个 skipped matrix 占位 job 就记为一个已通过测试。

同次独立资源可行性实验为 **3/2/0/5**：无 Swap 的 5 项实际执行，其中 2 项失败；带 Swap 流程因更早 UI 失败而未进入 5 项资源实验。它们独立于上述必需回归，历史 Claude OOM 等失败保留，不因 workflow 中“收集成功”而改成产品可用。构建/类型检查单独核对 8 条命令退出码为 0，不混算为 8 个单元用例；两个 regular JSON 中的未执行描述是环境/生产范围标签，其实际 VM 用例在对应门槛处独立统计，生产 VPS 始终未执行。

以上均明确属于 382057b 的历史本轮运行，不是最终代码或四规格安装器通过的声明。原始 Artifact 清单及 JSON/日志可由两条 workflow 复核，机器生成的聚合文件未纳入 Git 长期跟踪。

## 收敛后的专项门槛

控制器 **07cd92f8d94b8261b101053be8d2a5554f046dd7**，固定运行包 **382057b42b8fade988658b649520bae93db17b0d**（独立核验内部 tar.gz SHA256 `ad7d37013e762f5aa490ee2daa6284d52916a59f43273d82ba940ee0a4a918cf`）。[专项运行 39802736757](https://github.com/isAliceYoung/fa/actions/runs/39802736757)、[Artifact 12438007564](https://github.com/isAliceYoung/fa/actions/runs/39802736757/artifacts/12438007564)：**下载 29/0/0/0，真实 VM 重启后 2/0/0/0，前后健康各 40/0/0/6**。下载 suite errors 为 `[]`，全部 29 项已实际执行。

原生取消任务 `7433b2b53edfdb3d0c7d25cee1c194dd`：新复制文本精确为 `d1-cancel.bin / Canceled / From https://203.0.113.2`，failed=false、其他下载名为空、selection_marker=false；同一任务 Chrome 请求对应传输 **720896 字节 peer-closed**，低于完整 8 MiB；最终路径和 Agent 列表无该文件，raw/download 均 404。History 仍没有可信已提交记录，明确 null；没有伪造 state=2、没有 SIGKILL 或网络错误冒充取消。

空间故障剩余 15 个可用块（约 60 KiB，采样时更低），真实 Chromium 报 Disk full、完整目标未发布，清理已知故障填充后恢复下载通过；inode 故障创建 32309 个测试文件后可用 inode=0，同样验证原生失败与恢复。崩溃、GUI 关闭、目录替换、硬链接、实际 UID/DAC/挂载、双重认证撤销、Agent/Next 重启及 Profile/书签持久化均通过各自用例。具体每项条件以 `download-bridge.json` 的 tests/native/resources 和原始图片/journal 为准，正常下载字节从未由 root 观察器创建。

该专项 D1-A/B 通过后，才冻结 da8ecd69 的测试加强并启动最终 D1-C。最终新包仍必须再次通过同一下载 smoke 门槛，随后才能调度 4 个规格 × staging/isolated 共 8 个当前安装/重启组合及 1 个旧基线兼容组合；专项通过不等于最终四规格通过。

## 执行命令与范围

所有命令为单行；Linux VM 命令只用于仓库既有可丢弃 QEMU/CI 夹具，要求先按 workflow 取回相应完整 SHA 的固定包并校验独立 SHA256，不能在真实 VPS 上直接执行测试夹具。

- Windows Git Bash 实际 Shell 测试：`python tests/run_tests.py --json .test-results/d1-convergence-local.json`，220/0/0，退出码 0；其中环境/生产范围描述不作为具体用例通过数。Bash 为 `D:\Git\bin\bash.exe`，没有用“找不到 Bash”跳过 Shell 测试。
- 任务绑定与错误分类：`node tests/download_observation_test.mjs`，10/0/0/0，退出码 0。
- 有界 RFB、按键顺序和 Unicode：`node tests/rfb_key_packets_test.mjs`，5/0/0/0，退出码 0。
- 原固定包 2 GiB 专项：`python3 tests/run_systemd_vm.py --staging --download-focus --fixture-runtime=382057b42b8fade988658b649520bae93db17b0d --ram-mib=2048 --vcpus=2 --disk-gib=20 --swap-mib=1024`，运行 39802736757，退出码 0。宿主为 Debian 12、2 CPU/2 GiB/20 GiB/1 GiB Swap；不能外推为 1 CPU/1 GiB/10 GiB 已完成下载验收。
- R1 XIM 原 lab 对照：`python3 tests/run_systemd_vm.py --xim-focus --fixture-runtime=5c9ceecfb3e8b07b449ac55c9080c16f0a9b7a76 --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=0`。
- D1 XIM 原 lab 对照：`python3 tests/run_systemd_vm.py --xim-focus --fixture-runtime=f30bd8036330911a77c2720ae733960cbb348e16 --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=0`。

两条 XIM 命令的收集进程退出码不能代替 JSON 成功判定；第一轮原失败都保留，第二轮孤立正常 2/0、原序列 11/0 才计通过。环境为相同 Debian 12 内核 `6.1.0-54-cloud-amd64`、Chromium `155.0.8059.79-1~deb12u1`、Fcitx5 `5.0.21-3`、Xterm `379-1`；`/proc/swaps` 仅表头，无实际 Swap。原 lab 聚合 MemoryMax 768 MiB、CPUQuota 90% 未改变；单元 MemorySwapMax 非零也不代表无 Swap 主机实际能交换内存。

跟踪 Shell 语法、Python 编译、Node 语法、三个 workflow YAML 与 `git diff --check` 均已检查。最终完整构建、typecheck、真实认证/PTY/GUI、安装恢复及资源限制以最终两条 CI 的固定 SHA 原始结果为准，不能把上述本机检查代替真实 Linux 权限/安装验收。

## 产品与权限停止边界

一 VPS 一个实际用户，wdgui 与 wdguest 仍只共享普通 Downloads/访客数据。没有 root/wdsvc 下载中转；没有修改 UID/GID、DAC、Socket、Agent 安全路径、PTY、cgroup、CPU/RAM/Swap/Tasks/FD、网络策略或 Chromium 原生沙箱。未知数据不清理，故障填充与选区标记只存在于可丢弃 VM 的固定测试路径/私有显示中。

Claude 正式入口、旧 Playwright/极速浏览器迁移、网页与原生剪贴板、拖拽、多 GUI 共存、正式 HTTPS 域名及长期容量增长仍不属于本轮完成项。已有文件大小/卷容量、未完成文件拒绝、单 GUI 槽位等限制不放宽。Windows 搜狗前端修复保持原样；本轮 Linux XIM 和 Chromium 模拟输入回归不扩大解释为新版真实 VPS 的 Windows 人工验收。

不执行真实 Evoxt 或其他用户 VPS 的 SSH、安装、更新、重启、网络/权限修改；不修改 main/Beta、生产 Deploy Key、仓库可见性、用户数据库、密码和访客文件，不恢复自动更新。完成本轮 CI/报告后停止，D1 是否通过以实际最终门槛为准，不进入其他 3C 子任务或 Phase 4B。

## 第二次回归的固定产物（da8ecd69）

- 代码：`da8ecd69af8a63064f329cb70f8ddeb82ca982d5`。
- [Validate 39806551922](https://github.com/isAliceYoung/fa/actions/runs/39806551922) 全部 8 个 job success；[Staging 39806551979](https://github.com/isAliceYoung/fa/actions/runs/39806551979) 下载门槛 job 因整体时限被取消，后续矩阵未执行，不能仅据产物生成就宣布通过。
- 名称：`staging-debian12-da8ecd69af8a63064f329cb70f8ddeb82ca982d5`；[Artifact 12437218445](https://github.com/isAliceYoung/fa/actions/runs/39806551979/artifacts/12437218445)。到期 **2026-10-29 15:39:03 UTC**。
- 内部 `staging-runtime.tar.gz`：**225915619 bytes**；独立流式计算 SHA256：**`8d9a221f15e50c54b79a5fb79ce0e7c8fa557bd2a200ea2f7d422ec464d472a2`**，与 sidecar 相符。该值不是外部 Artifact ZIP 的哈希。
- 下载后实际检查 manifest：schema=1、完整 commit/branch、Debian 12、amd64、Node 22.23.2、node_modules ABI 127、glibc 2.36 全匹配。旧 f30/R1/382057b 包仅用于上文明确标注的对照，不冒充此包。
- 产物仅供后续独立审查；本轮未将其传输、安装或启用于用户真实 VPS。

## 证据与工具的限制

- 原生 UI 观察使用真实 VNC 鼠标/键盘、固定 Chromium 155 布局和实际 canvas geometry；没有给访客开放 CDP、任意 URL 获取器或 root 文件转存。后续 Chromium 布局变化仍可能导致测试失败，必须看截图/新选区/任务记录，不得放宽“当前任务”条件。Shadow DOM 与搜索行为优先依据本轮本机/VM 实测；[Chromium manager](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/resources/downloads/manager.ts) 和 [SearchService](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/resources/downloads/search_service.ts) 仅供实现对照，不拿上游 main 替代实际 155 版本验收。
- `.github/workflows/d1-focus.yml` 依赖精确历史 Run/SHA/哈希的固定包，运行包保留期 7 天。过期后应从同一完整 SHA 重新构建并独立记录新哈希/Run，不自动使用 HEAD，不删除旧失败证据。测试证据 Artifact 的保留期与运行包不同（本轮多数为 90 天），以 GitHub 元数据为准。
- 下载实验的 CPU 为相邻采样差分，实际采样间隔受观察器自身开销影响；memory.peak 是内核计数，可能包括单元启动历史，不能宣称毫秒采样或无限期稳定。宿主 SwapTotal 与各单元 memory.swap.max 分别记录，不能用一个推断另一个；GUI/浏览器各次关闭会重建 cgroup，聚合与单元峰值不能简单相加。
- 未执行的真实生产 VPS、正式域名、Claude 正式持续交互、剪贴板/拖拽/多 GUI、长时间运行和无界 Profile/Downloads 增长不计入 D1 成功。有限访客卷仍是容量兜底；本轮只验证已知故障填充的释放和恢复，不自动删除普通用户下载或 Profile 来制造“容量正常”。

## 宿主软件源缓慢导致的 CI 准备取消

da8ecd69 的 Validate 实际必需回归为 **933/0/0/0**，两台 1 GiB GUI 各 desktop-runtime 11/0、GUI UI 4/0、desktop UI 5/0，先前带 Swap 的 OSC 提示失败本轮没有重现；没有对该生产处理逻辑或断言做任何修改，也未据一次重新通过声称已经定位旧 OSC 超时的根因。历史失败仍可复核。

独立资源实验 **7/3/0/0**，security_failed=0；3 项失败均为无 Swap 的 Claude 320/384/448 MiB 实验，前两项 oom-kill，448 MiB 存活但交互/资源标准未满足。所有 10 项的 production_usable 均 false。保留这些原有实验，不恢复 Claude 正式入口，不借有 Swap 的短期存活宣布可用。

Staging 原始日志明确：npm 在 **7 秒**安装完毕；随后 `azure.archive.ubuntu.com` 的 QEMU/字体包下载及重取耗时 **27 分 54 秒**（APT 报平均 15.5 kB/s），直到 16:07:52 UTC 改走官方 `https://archive.ubuntu.com`，16:08:32 才进入 VM 控制器。VM 实际启动并克隆正确代码，16:09:18 到安装 packages 阶段，16:10:18 达到既有 30 分钟 job 上限，被取消。**这不是下载观察断言失败，也不是 VM 从未启动**；GitHub 步骤状态滞后时的进度判断以本条原始时间线纠正。

该次 [Artifact 12440716797](https://github.com/isAliceYoung/fa/actions/runs/39806551979/artifacts/12440716797) 仅有部分准备/安装证据，没有 download-bridge/download-reboot/health 结果。其下载 31 项和前后健康 92 项，以及后续 9 个安装组合 1444 项均未执行；按完整计划汇总是 **933 passed / 0 failed / 0 skipped / 1567 not_executed**，另有 **1 个 CI job cancelled** 和未展开的矩阵 skipped 占位。取消不算通过，也不编造成 29 个程序断言失败；当时 D1-C 仍 BLOCKED。

修正只作用于可丢弃 Ubuntu 24.04 amd64 GitHub 宿主：`prepare_vm_host.sh` 保持相同 QEMU/ISO/字体包和 KVM 准备语义，用官方 archive/security HTTPS URL、现有 Ubuntu archive keyring 和独立新建临时 sources/indexes。APT TLS、签名、时间及包哈希检查不关闭；不改 `/etc/apt`、未知文件、Debian VM 的源或任何 VPS 配置。只对这次 APT 调用使用 IPv4、30 秒连接超时和 1 次重试；新准备步骤独立限制 8 分钟，**原有 job 30/40/60 分钟及任何产品 cgroup 限额均不增加**。[APT 官方选项说明](https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html) 为参数依据，真实 Ubuntu 执行另有以下证据。

控制器 **3307a1c654ced31081358fe5c47e9116691999a9**、固定运行包 da8ecd69/独立 SHA256 8d9a221f... 的 [专项 39812641296](https://github.com/isAliceYoung/fa/actions/runs/39812641296) 中，该准备步骤已实际完成于 **16:17:06–16:17:37 UTC（31 秒）**，随后才进入单台 2 GiB VM 下载测试。没有立即重跑四规格矩阵。新增守卫两项与完整本机测试 **222/0/0**、退出码 0；Bash 实际执行，无跳过。后续最终回归必须重建新完整 SHA 对应包并再次受同一下载门槛控制，不能将 da8ecd69 的 933 项直接冒充新 SHA 成功。

该专项最终 [Artifact 12441995575](https://github.com/isAliceYoung/fa/actions/runs/39812641296/artifacts/12441995575) 已核对：**29/0/0/0、重启 2/0/0/0，suite errors=[]**。补强的 execution_check 明确 guest_sentinel_writable=true，两处执行标记均 false，not_executed=true（这里字段指“负载没有执行”，不是用例未执行计数）；所有 29 项用例均实际执行。新宿主准备解决了软件源耗时，不依赖扩大超时、删掉字体/功能包、放松文件权限或更改 VM 资源预算。

本报告草稿随后纳入 Git，作为最终完整回归的新固定源码快照；待其新包/CI 完成后只更新本报告的实际结果和最终 SHA/Artifact，不再修改受测执行代码。

## 最终快照与完整回归

受测代码 **fb6823b4349f1bff19ad3791cc5dd7c5c904a125**；[Validate 39814561061](https://github.com/isAliceYoung/fa/actions/runs/39814561061)、[Staging 39814561274](https://github.com/isAliceYoung/fa/actions/runs/39814561274)。最终原始结果已逐项收集，不将旧 3307a1c/da8ecd69/382057b/f30/R1 的计数并入新快照。

最终运行包为 **`staging-debian12-fb6823b4349f1bff19ad3791cc5dd7c5c904a125`**，[Artifact 12440769493](https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/12440769493)，到期 **2026-10-29 16:31:49 UTC**。下载后独立流式计算内部 `staging-runtime.tar.gz`：**225932773 bytes**，SHA256 **`9024feaab7627a997c47978fe48fe9a68d2071f3f2ab0f69215f71013eb5cc12`**，与 sidecar 一致；manifest 的完整 SHA、branch、schema=1、Debian 12 amd64、Node 22.23.2、ABI 127、glibc 2.36 全匹配。这是最终新快照包，上文 da8ecd69 包不是本次替代品。

新包下载门槛 [Artifact 12443085785](https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/12443085785)：**29/0/0/0、实际重启后 2/0/0/0、前后健康各 40/0/0/6**，加强执行标记检查通过。D1-A/B 在最终 SHA 的新包上已重验，不单凭历史固定包放行；四规格结果见下列实际证据汇总。

该最终新包的网络中断任务 `6eaba5b1e9fc2973d404f6f796d8c24f` 原生新选区为 `d1-reset.bin / Failed - File incomplete`；同任务 6 次 Chromium HTTPS 尝试分别在 65536 字节 intentional-network-cut，原生失败、无完整文件、Agent raw/download 404 同时成立。原生取消任务 `b47114bf7ae7c058cc047aa1088e70c1` 的新选区为 Canceled、failed=false，origin 720896 字节 peer-closed，History 明确无可信提交记录，最终路径/Agent 均不发布。没有用别的下载 state=3、模拟 state=2 或浏览器 SIGKILL 替代当前取消。

最终 smoke 的崩溃用例实际 browser Result=signal/failed、MainPID=0，GUI/独立租约 inactive 且三个 cgroup 均空；重新打开后旧完整字节保持。真实空间填充剩 15 块、最低采样可用 28672 字节；真实 inode 填充 32308 个文件后可用 inode=0，两者均原生 Disk full、未发布、释放已知故障文件后恢复成功。实际 wdgui 可写的普通 Documents 执行标记和受保护应用目录标记均未出现，拒绝仅靠受保护目录不可写来推断“未执行”。同期 PTY 响应 1135 ms、文件 API 26 ms；这是该次有限样本，不外推所有规格或长期延迟。

16:37 UTC 核对 [GitHub 官方状态](https://www.githubstatus.com/api/v2/summary.json) 时，Actions 标为 degraded_performance（该组件自 15:28:04 UTC 更新），本轮出现作业排队及步骤状态更新滞后。报告按实际 Artifact/日志校正，不把 API 中暂时 queued/in_progress 推断为“程序未运行”或“程序失败”，也不为排队重复推送代码。状态观察仅说明当时的平台状况，不据此推定所有延迟均同因。

### 最终矩阵中的当前失败（不得回填通过）

1 vCPU / 1 GiB / 10 GiB / 1 GiB Swap 的 staging [Artifact 12442687914](https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/12442687914)：下载 **28 passed / 1 failed / 0 skipped / 0 not_executed**，真实重启后 2/0/0/0、图形 UI 5/0。唯一失败 `network interruption never publishes completed bytes`：`nativeDownloadUI` 第 67 行等待新选区标记替换超时，`d1-failure-1.png` 的 `chrome://downloads` 标题正确但内容区域白屏，未实际取得当前任务 File incomplete 文本。该证据不能判定为成功，也不足以单独确定是页面加载、资源压力还是测试交互问题。

同一次任务 `54c8807124a654a5e24de2c977125948` 仍有 6 次当前 Chromium 请求、每次 65536 字节 intentional-network-cut，finally 继续核对原生最终路径缺失、Agent 列表无完整文件、raw/download 各 404。独立用例恢复成功，后续原生取消、崩溃/关闭恢复、ENOSPC、inode、目录替换、硬链接、DAC、认证撤销和重启/Profile 均实际通过；没有因这一项失败把后续全部丢弃，也没有用这些通过反推失败项通过。suite-errors 的 CalledProcessError 是该断言导致的控制进程失败，属于同一个失败用例，不能重复加一。

下载采样期间 browser memory.events 的 oom/oom_kill 均为 0；聚合 cgroup 的历史 oom_kill=3 在下载前后未增加，不能把先前故障注入遗留计数当成这次白屏 OOM。浏览器采样 memory.current 最大 331517952 bytes、memory.peak 343408640、swap 最大 56115200；聚合 current 最大 771129344、peak 784445440、swap 最大 101302272，high 1391→3869。这提示确有回收压力，但不是具体白屏根因证明。原日志的 `Failed to adjust OOM score ... Permission denied` 也不等同发生 OOM kill。没有扩大 cgroup、Swap、超时或关闭沙箱来消除失败。

该项保持失败并阻止最终 D1 PASS。已完成多轮观察器纠偏和当前单次完整矩阵，不再盲目重跑整套矩阵或添加 sleep；后续应针对同规格、同固定包，先收集 Downloads 页面内容就绪、选区替换及当时 renderer 状态的专项证据，再决定最小修改。没有证据前不将其宣称为纯夹具问题或程序资源故障。

1 vCPU / 1 GiB / 10 GiB / **无 Swap** 的 staging [Artifact 12443092724](https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/12443092724)：下载 **21/3/0/5**，真实重启后 **2/0/0/0**。失败分别是网络状态、新取消动作前的卡片选区、inode 故障状态，均在第 67 行的新选区等待超时。inode 截图显示 Downloads 内容白屏和 Chromium 未正常退出后的 Restore pages 提示，不能把该白屏冒充 Disk full。实际空间故障原生 Disk full 已通过；inode=0 与 Agent 未发布检查已收集，但整项仍失败。原生取消尚未进入取消动作/其 finally 交叉验证，**该规格没有原生取消成功证据**，不能借用 2 GiB 结果补齐。

inode 的安全恢复在重新打开 GUI 时达到既有 45000 ms 边界，无法核验可用后续状态，因此目录替换、硬链接、DAC/挂载、认证撤销、Agent/Next/Profile 重启前检查这 5 项未执行；这些安全能力在其他已实际执行的环境另行统计。独立 VM 重启后的字节/Profile/权限保持 2 项确实执行并通过，不能据此将重启前 5 项回填。无 Swap 下载样本的 browser oom_kill 最大仍为 0，聚合历史 oom_kill=3；日志 16:47:59 的 PTY 故障注入 oom-kill 与下载页失败区分，既不能删除也不能武断解释白屏。

相同 1 GiB 有 Swap 的 **isolated** [Artifact 12442379590](https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/12442379590)：下载 **28/1/0/0**、真实重启后 **2/0/0/0**，失败为 `browser crash preserves completed data and recovers` 的准备阶段：第 75 行等待 crash fixture 页面标题超时，尚未执行该用例 SIGKILL 注入，crash_cleanup 缺失。截图仍在 Downloads，已有 Canceled 与 File incomplete 卡片以及 Restore pages 提示；不能将截图中的其他已结束任务作为本次 crash 用例成功。独立恢复后其余用例继续完成，恢复失败为空。2 GiB 和 4 GiB 的 staging/isolated 已执行组合中相同 29+2 项下载/重启通过，不能据此覆盖 1 GiB 当前失败。

最后的 1 GiB **无 Swap isolated** [Artifact 12443586670](https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/12443586670)：下载 **21/2/0/6**、重启 **2/0/0/0**。网络和空间故障均未取得原生新选区，空间故障截图为 Downloads 白屏及 Restore pages 提示，任务栏实际显示资源压力严重；空间剩 15 块、finally 的未发布检查完成。随后重新打开 GUI 达到既有 45 秒限时，inode 及后续 5 项共 6 项未执行。该次原生取消和真正 browser SIGKILL/cgroup 清空恢复各自有通过记录，不能用它们覆盖其他规格的失败或未执行项。

### 当前提交计数与原始证据索引

以下都是最终快照的执行次数，不是唯一功能数；顺序为 passed / failed / skipped / not_executed。Validate **8 个 job success**；Staging **8 个 job success / 4 个 job failure**（4 个失败为两个入口各自的 1 GiB 有/无 Swap），没有取消或未展开的当前矩阵。未标记专项的 d1-focus workflow 只产生跳过占位，不计为通过，也不额外重复本表已列具体测试范围。

| 类别 | Passed | Failed | Skipped | Not executed |
| --- | ---: | ---: | ---: | ---: |
| Ubuntu/Debian Python、资源/认证配额、下载观察、RFB | 546 | 0 | 0 | 0 |
| IME 事件模型和真实 Chromium/xterm/WS | 96 | 0 | 0 | 0 |
| 真实 Linux 源码、IPC、认证、SSH/内部服务边界 | 55 | 0 | 0 | 0 |
| 文件 Agent API | 28 | 0 | 0 | 0 |
| booted systemd、PTY、真实终端 UI | 84 | 0 | 0 | 0 |
| GUI/Chromium/XIM/图形 UI | 184 | 0 | 0 | 5 |
| 新包单台 2 GiB 下载及重启门槛 | 31 | 0 | 0 | 0 |
| 门槛前后健康 | 80 | 0 | 0 | 12 |
| 九组安装、模式、失败恢复、审计、前后健康 | 906 | 0 | 0 | 108 |
| 自适应资源、告警、故障约束 | 84 | 0 | 0 | 6 |
| 九组矩阵的下载及重启范围 | 230 | 7 | 0 | 42 |
| **必需回归合计** | **2324** | **7** | **0** | **173** |
| 独立既有资源可行性实验（不并入上行） | 8 | 2 | 0 | 0 |

173 项未执行严格拆分：10 组前后健康各 12 项（120）；旧基线不具备的下载 31、图形 UI 5、资源项 6（42）；当前两个无 Swap 下载失败后因恢复不安全而未执行 5+6（11）。健康中的 6 个未执行项是外部管理员 SSH 人工往返、Claude 正式入口、网页剪贴板、拖拽、多 GUI、正式域名；CI 驱动实际进行的 VM SSH 前后/重启检查另有日志，不能据此把面向用户真实服务器的人工检查改为通过。生产 VPS、长期容量/真实新版 Windows 搜狗部署验收等仍未执行，属于范围说明，不凭空增加未定义用例数。

构建/类型检查另列 **8 条命令退出码 0**：Ubuntu 构建与 typecheck、Debian 真实认证测试构建与 typecheck、Debian current 包构建与 typecheck、旧兼容包构建与 typecheck。包中的旧基线构建代码是 f195f90，不声称它是当前应用代码通过。两个 Python JSON 均 222/0/0；各 job 原始日志另确认 Node 19+17+30+10+5 项；真实 Chromium IME JSON 36/0/0/0，诊断基线的 12 个观察值不当作额外通过用例。Windows 实际输入法不由 Linux/模拟 DOM 结果代替；本轮未改 IME 前端。

| Debian 12 实际规格 / 入口 | Artifact ID | 完整组合 P/F/S/N | 其中下载 29 项 P/F/S/N | 重启下载 |
| --- | ---: | --- | --- | --- |
| 1 CPU / 1 GiB / 10 GiB / 1 GiB Swap staging | 12442687914 | 125/1/0/12 | 28/1/0/0 | 2/0/0/0 |
| 1 CPU / 1 GiB / 10 GiB / 无 Swap staging | 12443092724 | 118/3/0/17 | 21/3/0/5 | 2/0/0/0 |
| 2 CPU / 2 GiB / 20 GiB / 1 GiB Swap staging | 12442328996 | 126/0/0/12 | 29/0/0/0 | 2/0/0/0 |
| 4 CPU / 4 GiB / 30 GiB / 1 GiB Swap staging | 12442348852 | 126/0/0/12 | 29/0/0/0 | 2/0/0/0 |
| 1 CPU / 1 GiB / 10 GiB / 1 GiB Swap isolated | 12442379590 | 176/1/0/12 | 28/1/0/0 | 2/0/0/0 |
| 1 CPU / 1 GiB / 10 GiB / 无 Swap isolated | 12443586670 | 169/2/0/18 | 21/2/0/6 | 2/0/0/0 |
| 2 CPU / 2 GiB / 20 GiB / 1 GiB Swap isolated | 12443172911 | 177/0/0/12 | 29/0/0/0 | 2/0/0/0 |
| 4 CPU / 4 GiB / 30 GiB / 1 GiB Swap isolated | 12443455906 | 177/0/0/12 | 29/0/0/0 | 2/0/0/0 |
| 旧 f195f90 兼容 staging，1 GiB 有 Swap | 12442143124 | 82/0/0/54 | 不具备 31 项下载范围 | 未执行 |

上表 Artifact 均在 Staging Run **39814561274**，链接格式 `https://github.com/isAliceYoung/fa/actions/runs/39814561274/artifacts/ID`。当前八个组合均固定 fb6823b，旧兼容组合明确使用 **f195f90c01f38fcf93c20ee616f9e62a15063018**，不是修改 main/Beta。镜像为 Debian 12 genericcloud `20261018-2635`，各 image.json 保留原 URL、SHA512 和实际 CPU/RAM/Swap/disk；不是容器 FD 模拟。

Validate Run **39814561061** 的原始 Artifact：Ubuntu `12440619341`、Debian `12441087747`、真实用户/Next/认证 `12442535770`、booted systemd `12440884564`、PTY/systemd/Next/UI `12441997402`、IME Chromium `12441103102`、GUI 无 Swap `12443241605`、GUI 有 Swap `12443137533`。两台 GUI 的 desktop-runtime 各 11/0/0/0，真实候选、数字选词、Bash 文件结果均复核；gui-ui 各 4/0、desktop-ui 各 5/0。先前 OSC 等待超时本次未重现，但没有改该产品路径或删除历史失败。JSON、截图、journal、origin TLS 日志、systemd security、UID/挂载和 reboot 记录留于 Artifact，不长期跟踪生成结果。

### 资源事实和不能外推的结论

本轮 **没有修改任何产品预算**。下表来自下载期间 cgroup 实际采样；current 是采样最大，peak 是内核 memory.peak（可含前序故障注入/启动历史），Swap 是聚合 memory.swap.current 采样最大。CPU 为相邻样本差分占一个核心的百分比，可在多核超过 100；任务是聚合 pids.peak。各嵌套 cgroup 不是可以简单相加的独立占用。内存单位 MiB。

| 入口/规格 | 聚合 current / peak | 聚合 Swap | CPU 样本最大 | pids.peak | 同期 PTY / 文件 API ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| staging 1 GiB 有 Swap | 735.4 / 748.1 | 96.6 | 88.1% | 152 | 267 / 24 |
| staging 1 GiB 无 Swap | 727.1 / 746.9 | 0 | 88.3% | 143 | 1132 / 32 |
| staging 2 GiB | 954.8 / 963.4 | 0.3 | 178.7% | 184 | 1354 / 29 |
| staging 4 GiB | 651.9 / 1006.0 | 0.3 | 213.8% | 186 | 1091 / 22 |
| isolated 1 GiB 有 Swap | 746.3 / 749.4 | 78.7 | 84.7% | 157 | 1087 / 28 |
| isolated 1 GiB 无 Swap | 737.2 / 744.9 | 0 | 82.2% | 143 | 1139 / 29 |
| isolated 2 GiB | 927.6 / 943.7 | 0.1 | 172.0% | 170 | 1153 / 24 |
| isolated 4 GiB | 636.1 / 971.7 | 0.4 | 167.5% | 180 | 1109 / 20 |

各 metrics 记录每个单元 memory.max、memory.swap.max、memory.events、cpu.max、pids.max、宿主 MemAvailable/SwapTotal/SwapFree 和卷块数/inode。1 GiB 主机 SwapTotal 1048572 kB 不等于浏览器可用 1 GiB：有 Swap 浏览器 memory.swap.max=201326592 bytes，聚合=671088640；无 Swap 两者为 0。原自适应访客卷按环境产生不同实际容量，报告不将 2/4 GiB 卷或可用 ext4 块数冒充统一 512 MiB 配置；现有策略未改，真实块/inode 耗尽确实发生。1 GiB 无 Swap isolated 空间故障最低可用 28672 bytes，释放仅限本用例已知填充文件，普通下载/Profile 不被清理。

八个下载阶段 browser 的采样 oom_kill 未增加；聚合在下载前已有 3/4 个故障注入历史 kill，前后未增加，保留原始计数，不声称整次资源测试“从未 OOM”。1 GiB 的 high/reclaim、白屏和启动限时失败意味着**不能给出该规格异常恢复已稳定的结论**，即使正常下载/PTY/文件操作通过。2/4 GiB 的有限测试成功也不等于任意网页、无限下载或长期 Profile 增长已验收。

既有独立资源实验：有 Swap 5/0，无 Swap 3/2（Claude 320、384 MiB 失败；448 MiB 仅本次短期 smoke 通过），security_failed=0。全部 production_usable=false，未恢复 Claude 正式入口；da8ecd69 的 448 MiB 失败和更早 OOM 原证据仍保留，不能借本次存活抹去。它们是已有回归实验，不是本轮新增 Claude 功能或放宽资源限制。

### 最终阶段结论与停止点

**D1-A：tested；D1-B：tested（2 GiB 专项）；D1-C：incomplete / BLOCKED；D1 总体：PARTIAL / BLOCKED，未达到 D1 PASS。**

- HIGH：1 GiB 有/无 Swap 原生下载页白屏、选区/页面标题等待及故障后 GUI 恢复不稳定，7 个当前失败、11 个直接依赖未执行。阻止 D1 全规格验收；下一次仍应留在 D1，先同规格固定包专项分辨原生页面就绪/renderer 状态、Restore pages 焦点与资源压力，不能直接重跑所有矩阵或调整生产预算。
- MEDIUM：History 提交滞后与 Chromium 155 原生卡片布局依赖。当前已拒绝不可信/其他任务记录，缺失明确 null；未来观察器改动必须保留 task URL/GUID/ID/目标路径和真实文件/原生行为交叉验证，不能只看 state=3。
- MEDIUM：既有 Claude 无 Swap OOM/短期交互限制，正式入口保持关闭；不属于本轮新增实现或通过项目。
- LOW：CI 平台状态滞后、短保留运行包；以固定 Run/Artifact 原始证据和独立内包 SHA256 为依据，过期要按精确 SHA 重建，不能改用 HEAD。

本轮只交付测试/观察器/CI 安全子集和真实失败报告。最终报告提交仅文档，执行代码与受测 fb6823b 保持一致，完整文档提交 SHA 在交付回复与 Git 历史提供。已核验远端 main **5922e1408de91e56771f1ea0bf94a9df1b7028cd**、Beta tag object **76f93a1682845306877552868f8a8a77cae156a9**（peeled 为相同 main）未变；只向 feature/private-deploy-isolation 推送。没有连接或修改真实 VPS，没有进入新功能阶段、Phase 4B 或生产部署。提交本报告后停止并等待独立审查。
