# 3D 阶段性测试安装器交付报告

本轮只准备代码、文档与可丢弃 CI VM 验证。**没有连接或安装用户新测试 VPS，也没有操作现有生产 VPS**。真实 VPS 的 Debian 版本、IPv4 SSH 方式与安装授权仍待用户确认。分支仅 feature/private-deploy-isolation，基线 3f6a877。

## 实现与复用

- 新 install-staging.sh 与 check-staging.sh 独立于稳定 install.sh；使用固定完整 SHA 和外部可信 Artifact SHA256，不取变化中的 HEAD。
- Debian 12 CI 构建并逐文件记录 Node 22.23.2、ABI127、glibc2.36、amd64；VPS 只校验并安装，不现场 Next build。运行包原生模块实际加载验证；源代码 bundle 独立 root 克隆、fsck、固定 checkout，禁用克隆 hooks。CI 的 env/测试账号密钥/数据库不打包。
- 原五层身份/文件卷/PTY/GUI/浏览器安装器继续同一套实现、运行协议、systemd/polkit/tmpfiles 模板；新共享 layer-context.sh 保留 --lab，增加 root 0700 授权目录、0600 状态与源码 SHA 校验的 --staging。单独传 --staging 不能绕过授权。
- CI 与 staging 共用 GUI 依赖清单；原 common.sh 的 IPv4-only 与 install-postgres.sh 的新集群恢复逻辑直接复用，稳定代码未改。staging 在首次 APT 安装前标记本次新建 PG 集群，未知旧集群预检拒绝。
- 安装状态带阶段、固定 SHA、产物 digest、管理员列表和初始 inventory；失败保留状态/日志/数据，不自动清理或接管。重复安装拒绝；只提供 start-service 失败后的有限 --resume-finalize，其余失败需人工审查/恢复快照。
- 默认 127.0.0.1:3000；独立 Next UDS。只允许显式 HTTP loopback Origin，支持不同 SSH 隧道端口。root 保存随机登录种子，不在输出/日志展示；四个执行/服务身份锁定 nologin、SSH DenyUsers 并核验实际 sshd 配置。

目录：/opt/webdesktop/source 为 root-only 固定源码/.git；/opt/webdesktop/app 为 root:wdsvc 编译包/.env；/etc/webdesktop-staging 为 root0700 的状态/清单/登录种子/脱敏日志；访客工作卷仍 /home/wdguest，原512 MiB ext4、nosuid/nodev/noexec；Agent 状态与 .gui 保持原私有挂载/DAC。各 Socket、租约守卫和网络命名空间沿用已验收设计。没有新增生产 Deploy Key；安装路径使用管理员核实的 CI 产物，不需要在 VPS 存 GitHub 密钥。

健康检查默认只读；live 模式要求明确接受替换当前桌面认证，通过真实 HTTP/WS、固定 IPC 测文件 API、实际 Bash、RFB/PCManFM/Fcitx、可见 Chromium 与原生 renderer、外网 HTTPS/本机端口拒绝、槽位复用/整树回收。读取 root 秘密只发生在可信验收控制器；不会把 Cookie、密码或后端环境传给访客 worker。新日志/报告路径使用独占创建，不覆盖已有文件。

## 安装与人工验收

完整前置条件、固定 SHA/产物选择、安装命令、状态命令、Windows 隧道及十项网页验收见 [STAGING_TEST_GUIDE.md](STAGING_TEST_GUIDE.md)。普通 installer 入口不会调用测试 GUI probe、Claude 研究器或 CI 账号/公钥生成代码。

预检会拒绝未知身份、旧安装/数据库、现有数据、未知本地服务、防火墙策略和占用端口。Debian resolver 的 DNS/LLMNR 端点必须与实际 systemd-resolved PID 一致。active/enabled unit 核验发行版 package 来源；已完成的 root growfs 另按固定生成路径、完整可执行字段白名单、无 drop-in、MainPID=0、Result=success 和发行版 growfs 二进制核对。该特殊识别依据 [systemd 252 生成实现](https://github.com/systemd/systemd/blob/v252/src/shared/generator.c#L471)，不启动/修改扩盘单元。所有授权参数从管理员 CLI 输入，不接受网页请求改变 UID、命令、路径或来源名单。

## 测试记录

最终验证代码为 **`f195f90c01f38fcf93c20ee616f9e62a15063018`**。[原有完整回归 39442300926](https://github.com/isAliceYoung/fa/actions/runs/39442300926) 八项任务全部成功；[独立 staging 39442300698](https://github.com/isAliceYoung/fa/actions/runs/39442300698) 构建、安装两项任务全部成功。以下是实际用例计数，不能将两条工作流的十个绿色任务当作十个功能用例。

| 最终代码上的测试组 | 通过 | 失败/错误 | 跳过 | 未执行 |
|---|---:|---:|---:|---|
| Ubuntu 常规 Shell/Python/格式测试 | 167 | 0 | 0 | 报告另列整机/生产范围，不在167中 |
| Debian 12 常规测试 | 167 | 0 | 0 | 同上 |
| Debian 源码真实 Linux DAC | 9 | 0 | 0 | 0 |
| Linux 访客隔离 / SSH解析 / 真实Next WS / 文件故障恢复 / 终端身份边界 | 18 / 3 / 12 / 27 / 5 | 各0 | 各0 | 各0 |
| PID1/systemd，安装后+重启后 | 9+9 | 0 | 0 | 0 |
| PTY VM：systemd、PTY协议、Chrome终端UI及重启 | 49 | 0 | 0 | 0 |
| GUI VM 无Swap：systemd、PTY、基础GUI、浏览器/输入UI及重启 | 72 | 0 | 0 | 0 |
| GUI VM 1GiB Swap：同上 | 72 | 0 | 0 | 0 |
| staging 全新安装后健康检查 | 39 | 0 | 0 | 6 |
| staging 实际重启后健康检查 | 39 | 0 | 0 | 6 |
| staging 四身份实际SSH拒绝，重启前+后 | 4+4 | 0 | 0 | 0 |

表中各已执行测试命令退出码均为0。staging驱动额外验证管理员实际SSH、缺少授权、未知用户/unit/vendor unit/数据保留、重复安装和无效resume；这些控制断言没有伪装成健康JSON内的额外用例。常规测试JSON的not_executed是范围说明字符串，不是已跳过用例；CI VM覆盖的部分另列，用户真实VPS、Windows人工验收与生产测试仍未执行。

**原有GUI工作流自动运行的资源实验另计，不计入上述72项功能测试：** 无Swap为2通过、3资源失败、0安全失败、0跳过/未执行；1GiB Swap为5次短时smoke通过、0失败、0跳过/未执行。无Swap的3项为Claude原320/384/448MiB预算下OOM，保留原始失败，未新增档位或修改该研究器；有Swap的短时smoke也明确 `production_usable=false`。staging运行环境不安装Claude，不由这些实验开放正式入口。

Windows早期常规测试161通过、0失败/错误、0跳过、退出0；随后最新11项产物边界与5项生成unit格式测试各自退出0。最终167项已在上述两套Linux CI完整执行，无缺Bash跳过。产物边界覆盖外层digest、固定SHA、平台、额外/重复文件、路径穿越、软链接逃逸/祖先、硬链接和文件损坏，格式测试不代替Linux权限测试。

最终 [staging安装证据](https://github.com/isAliceYoung/fa/actions/runs/39442300698/artifacts/12274122879) 的 `before.json` / `after.json` 各39通过、0失败、0跳过、6未执行，命令退出0。六项未执行是本地健康脚本不自行证明的外部管理员SSH，以及Claude、网页剪贴板、拖拽、多应用GUI、正式HTTPS；CI驱动另已完成真实管理员SSH往返与重启前后四身份共8次有效测试公钥登录拒绝，8/8成功，不篡改本地健康JSON的原始计数。此前 `1edc6f7` 的 [staging成功批次39440387743](https://github.com/isAliceYoung/fa/actions/runs/39440387743) 同样保留。

同一批次还通过缺少staging授权、未知用户、未知本地unit、未登记vendor unit、未知数据保留、重复安装拒绝、已完成安装误用resume不改变state/.env的控制断言。`reboot.json` 记录两个不同boot_id和管理员SSH成功；不是继承FD/容器模拟启动。

最终原GUI撤销用例在无Swap安装后/重启后分别4688/4696ms、有Swap分别4691/4695ms完成；PID+内核启动时间记录的原进程残留全部为0。修正的是将deactivating误当完成的检查器，没有扩大认证租约、cgroup预算或恢复高权限执行路径。

自动化新部署验证范围：真实HTTP/WS文件创建/读取/写入/回收站、真实Bash、RFB握手与可见PCManFM/Chromium窗口、Fcitx进程启动、实际renderer namespace/seccomp、同网络namespace的HTTPS与管理端口拒绝、GUI关闭/重开/整树退出、UID/DAC/私有Socket/信号拒绝与逐文件版本校验。**Windows网页鼠标/键盘、网页中的实际中文输入和真实VPS现场延迟仍待人工验收**；本轮staging不会把“Fcitx进程存在”改称新的中文输入现场通过。旧3C真实中文UI证据继续保留。

新增 CI：.github/workflows/staging.yml；复用 tests/run_systemd_vm.py --staging 的 Debian镜像校验、1CPU/1024MiB/10GiB VM、严格测试SSH主机密钥和最终销毁。驱动只在该VM中生成测试SSH管理员/密钥，验证未知数据拒绝、重复安装拒绝、管理员实际SSH、重启与实际健康检查；不接收用户VPS地址。

对应实际命令（VM命令只在可丢弃CI宿主执行；不是用户VPS上的操作）：

```bash
python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json
python3 tests/linux_private_repo.py
python3 tests/linux_guest_layer.py
python3 tests/linux_tunnel_parser.py
python3 tests/linux_ws_runtime.py
python3 tests/linux_files_runtime.py
python3 tests/linux_terminal_boundary.py
python3 tests/run_systemd_vm.py
python3 tests/run_systemd_vm.py --pty
python3 tests/run_systemd_vm.py --desktop --swap-mib=0
python3 tests/run_systemd_vm.py --desktop --swap-mib=1024
python3 tests/run_systemd_vm.py --staging
```

构建任务另执行Debian原生 `npm ci`、`npm run build`、`npm run typecheck`、staging_bundle.py，以及VPS环境实际sharp/node-pty加载。完整命令、systemd检查/日志、前后健康JSON、截图保留于CI Artifact；机器生成结果不纳入Git。

历史失败均保留，不改写为通过：

| 批次/提交 | 分类及处理 |
|---|---|
| dfb80da | CI构建夹具的Git所有权失败；install任务跳过，不能计作安装通过 |
| 9028144 | 云镜像缺Git，尚未调用安装器；明确获取受信任工具的最小前置准备 |
| c31c4bc、40e5c26、c816d3f | fresh预检对系统resolver端点/接口标记识别不足；按实际端点+服务PID识别，不放行任意监听 |
| f595c87、935d16d | **真实打包故障**：遗漏Turbopack目录软链接，PG模块别名加载失败；保留并安全重定位包内目录链接，无需改DB或扩大内存 |
| 0f96734、d43fa57 | 服务来源预检的模板实例与系统生成root-growfs兼容性；只读取元数据，固定执行字段白名单拒绝附加命令 |
| 1d12284 | 安装成功，健康37通过/2失败/6未执行；GUI清理事务尚未释放槽位时立即连接503，浏览器未启动。按明确停止状态限时重试503，服务失败/认证错误仍直接失败 |
| 1edc6f7 原全量无Swap GUI回归 | 9/10基础GUI用例通过，裸PID即时残留断言失败；检查器将deactivating当完成停止。改为明确MainPID/状态及PID+内核启动时间，记录撤销耗时，不调整认证租约或运行限制 |

未增加Claude内存档位、未调大cgroup或关闭沙箱。每次失败均定位具体错误后修改；没有靠循环重跑同一失败job或无限增加sleep判通过。GitHub取消接口不可用，旧全量运行未取消，历史状态保留。

## 资源预算与限制

VPS 无 npm/Next 构建，使用离机 Debian 12 编译产物；不安装 Claude 或编译工具链。产物压缩上限450 MiB、展开总量2 GiB、条目150000，整包与每文件都校验。预检至少6 GiB空闲，并在分配 Swap前保留3 GiB余量；普通10GiB系统盘很紧，拒绝靠删除用户数据腾空间。

父组 RAM768/High672 MiB、Swap640、CPU90%、320tasks；Next384/Swap128、PG128/Swap64、Agent96/Swap32、PTY192/Swap128、GUI512/Swap256，其中基础GUI192/64与Chromium320/192；可信守卫独立于工作负载父组。子上限不能相加当作预留，总量由父组约束。Swap按名义容量计入头部页，已有 active Swap保留，仅补足约1GiB，不扩大现有单元额度。

本轮不延伸 Claude 内存研究，不承诺同时运行多图形应用或重负载浏览器。健康 JSON 保存主机 RAM/Swap、磁盘、cgroup current/peak/swap/events/pressure、CPU与任务数；真实 VPS 的交互延迟和网页人工测量尚未执行，不借用 CI 数据冒称 VPS 现场结果。

最终 **`f195f90`** staging批次实际快照如下，属于本轮可丢弃VM而非用户VPS。RAM实际MemTotal约973.3MiB，Swap可用1023.996MiB（名义1GiB减头部页）。父组峰值包含测试期全部子组，不能再把子峰值相加。

| 测量 | 安装后检查 | 重启后检查 |
|---|---:|---:|
| workload memory.peak MiB | 571.6 | 570.6 |
| workload current，全部GUI关闭后 MiB | 407.6 | 404.8 |
| workload swap.current MiB | 11.8 | 1.9 |
| GUI slice memory.peak MiB | 448.4 | 448.3 |
| GUI slice残留tasks | **0** | **0** |
| Next memory.peak MiB | 124.8 | 89.2 |
| PG memory.peak MiB | 27.6 | 48.2 |
| 父组累计CPU秒 | 2.49 | 2.58 |
| 主机Swap使用快照 MiB | 24.5 | 4.6 |
| 系统盘可用 GiB | 4.37 | 4.37 |
| 原始512MiB卷的已用 MiB | 7.4 | 11.4 |
| 访客卷可用 MiB | 473.6 | 469.5 |

各记录的oom/oom_kill/oom_group_kill均为0。GUI/父组可见high/max回收事件（前后high16/1、max342/383），不能当作“完全没有内存压力”；父级memory.events也包含子级事件，不代表768MiB父限额已耗尽。GUI已无进程但仍记账约282–284MiB可回收文件页缓存，不能把memory.current当成进程RSS或宣称关闭后立即清空全部缓存。CPU为累计使用时间，非平均百分比；这里只验证短时轻量功能，不构成长时间交互性能保证。先前1edc6f7批次570.1/605.7MiB峰值继续保留在其Artifact，不覆盖历史。

最终产物实测：压缩225398270字节（约215.0MiB），tar共29589项，普通文件含manifest合计798816345字节（约761.8MiB）；上限不是实际用量。已下载核对GitHub外层ZIP digest，并计算内部tar.gz SHA256与构建元数据一致。产物与工具checkout必须固定以下**已验证代码SHA**，不要因后续文档提交而改用分支HEAD：

```text
commit: f195f90c01f38fcf93c20ee616f9e62a15063018
staging-runtime.tar.gz SHA256:
1967e83c167547b8acf78d32bb41acfb42c55d03e04e36d7c64e1736d86cf9f4
```

[构建产物下载](https://github.com/isAliceYoung/fa/actions/runs/39442300698/artifacts/12274037427) 保留7天。其外层ZIP SHA256为 `f6d68e132bb2802e81b57c12130097b836874313a2ef75bb2e59f48b54740836`，**不能**用来替代安装参数要求的内部tar.gz SHA256。过期需对同固定代码重新构建并验证。

## 影响、恢复与停止点

会修改测试机 APT 包、IPv4-only、Swap/fstab、四身份的 SSH DenyUsers（校验后reload）、专属 systemd/polkit/tmpfiles、512MiB文件卷，以及浏览器 veth/nft/NAT/IPv4 forwarding。要求 IPv4 管理入口，保留控制台与快照；已有 IPv6 SSH连接会阻止安装。不会改管理员密码/公钥或 flush 既有防火墙。无正式域名、生产部署、目录迁移、仓库可见性/DeployKey变更、稳定分支/Beta修改或自动更新恢复。

恢复策略见指南：先查明确失败阶段与 root 日志；仅完整配置后的 start-service 失败支持同 SHA有限收尾，不重新建库/格式化/覆盖。其他失败保留数据，优先回快照或另建授权全新测试VPS；没有通用卸载/升级/原地重装。不得删除状态以绕过全新检查。

Known limitation/未执行：用户真实VPS安装、Windows人工网页验收、正式HTTPS、Claude正式入口、剪贴板、拖拽、多应用GUI共存与长期运行压力。本轮完成代码/CI后停止，等待用户进一步授权。

本轮不修改仓库可见性。内核 DAC 保护本机部署源码与凭据，不会阻止用户取得已经公开在 GitHub 的源码副本；未来私有生产上线的仓库与 Artifact 访问控制仍需单独授权和验收。

## 修改文件与提交

相对3f6a877的完整26个新增/修改文件如下，稳定install.sh、bootstrap.sh、common.sh、install-postgres.sh、原IPv4/Swap恢复、取消欢迎文件/示例图片的逻辑及历史3C报告均未修改。

| 范围 | 文件 |
|---|---|
| 新CI | `.github/workflows/staging.yml` |
| GUI运行授权/共享依赖 | `gui-runtime/browser-network.sh`、`gui-runtime/debian12-packages.txt` |
| 复用测试夹具 | `tests/debian_gui_vm.sh`、`tests/linux_gui_runtime.mjs`、`tests/run_systemd_vm.py` |
| 新staging驱动/格式策略测试 | `tests/staging_vm_driver.py`、`tests/test_staging_bundle.py`、`tests/test_staging_policy.py` |
| 两个入口 | `webdesktop-deploy/install-staging.sh`、`webdesktop-deploy/check-staging.sh` |
| 共用授权 | `webdesktop-deploy/scripts/layer-context.sh` |
| 原层安装器适配 | `webdesktop-deploy/scripts/setup-browser-layer.sh`、`setup-guest-layer.sh`、`setup-guest-ssh-deny.sh`、`setup-guest-volume.sh`、`setup-gui-layer.sh`、`setup-pty-layer.sh`（均在同一scripts目录） |
| 新产物/安装/健康/真实协议验收 | `webdesktop-deploy/scripts/staging_bundle.py`、`staging_install.py`、`staging_check.py`、`staging_smoke.mjs`（同目录） |
| 后端单元 | `webdesktop-deploy/templates/webdesktop-staging.service` |
| 文档 | `webdesktop-deploy/README.md`、`webdesktop-deploy/STAGING_TEST_GUIDE.md`、`webdesktop-deploy/PHASE3D_STAGING_REPORT.md` |

代码提交依次为 `dfb80da`、`9028144`、`c31c4bc`、`40e5c26`、`c816d3f`、`f595c87`、`935d16d`、`0f96734`、`d43fa57`、`1d12284`、`1edc6f7`、`f195f90`，均仅开发分支。随后README/本报告为文档收尾提交，使用 `[skip ci]`，该文档提交未重新执行CI，不能称作新代码测试通过；安装仍使用上述f195f90产物。

本轮交付停止点：代码和CI准备完成；等待用户审查并确认新VPS的Debian版本、访问方式和安装授权，随后才进行真实VPS验收。
