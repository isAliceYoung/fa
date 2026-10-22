# Phase 4A：隔离部署适配与阶段门槛审查

日期：2026-10-22，Asia/Kuala_Lumpur。分支仅 `feature/private-deploy-isolation`。

**阶段结论：Phase 4A: PARTIAL / BLOCKED（完整正式交付门槛）。** 已实现已验证子集的独立候选部署/有限恢复入口；完整 Phase 3C 的原计划功能尚未全部迁移，不能宣称完整 Phase 3 或 Phase 4 通过。本轮仅推进授权的 4A 子集，不自动进入下一阶段。

## 1. 基线、原计划与阶段定义

开始时工作区干净，HEAD/报告 `6f6df95db35c583fe847d2630f6cbcec5bb3e951`，其受测代码 `72e06b92ec37b6185150be04ecdd69c0418f5afb` 与历史 Validate `39634756756`、Staging `39634756573` 对应。用户当前 VPS 仍运行 `131ea566...`；用户提供的 31/0/12、39/0/6 是该环境的既有验收，不改写为本轮提交结果。本轮未连接真实 VPS，未更新 IME。

已读原始 `ISOLATION_PLAN.md` 第 7、9、10、11 节以及 PRODUCT_ARCHITECTURE、PHASE3C_CHROMIUM_REPORT、PHASE3D_STAGING_REPORT、RESOURCE_MANAGEMENT、TERMINAL_IME_FIX_REPORT，并检查当前入口、路由、执行层和安装实现。原计划只有正式阶段 1–5，没有 3A/3B/3C/3D、4A/4B 的正式细分；后者为历次授权的实施管理标签，不替代原计划定义。

| 原计划要求 | 当前实际情况与门槛 |
|---|---|
| §7 auto/install 保留源码、构建、凭据和数据库；service 不安装/获取/构建/改网络；旧布局不隐式迁移 | 新候选入口按固定状态恢复，限定修复；稳定入口不改。早期失败不支持任意阶段自动续装，保留数据并阻塞 |
| §9 原功能保留，包括 Claude、极速浏览器、文字/图片/文件剪贴板、拖拽 | 多项仍 disabled/incomplete；不因图标隐藏或 HTTP503 宣称完成 |
| §10 阶段3“迁移所有真实文件、PTY、GUI、Playwright、进程和剪贴板路径” | 文件、PTY、固定 GUI 子集已隔离；完整阶段3 **incomplete**，正式全功能 Phase4 前置 **blocked** |
| §10 阶段4 服务/socket、配置/产物权限、旧测试、service 纯恢复、IPv4/Swap/PG 保留 | 4A 实现独立候选子集；本轮4GiB CPU断言失败，完整门槛blocked；不替换稳定install.sh |
| §11 正反向与越权验收，包含 Claude/剪贴板/拖拽/极速下载/长期生命周期 | 已执行子集与未执行功能分别列示；关闭旧路径只是安全防线，不算正向功能通过 |

3D 的全新安装、真实身份、重启和权限证据足以作为**用户明确授权的子集适配基础**，不补足原阶段3遗漏。完整正式交付仍需返回 3C 完成原功能或由用户另行审查产品范围，不能由本报告降低标准。

状态用语：implemented=代码存在；tested=有指明环境/提交的证据；disabled=明确关闭；incomplete=功能/安全/实现尚不完整；not_executed=未实际执行；blocked=有明确依赖阻止推进。这些不是可互换的“完成”。

## 2. 实际阶段矩阵

下表区分历史环境与本轮重新执行；最终固定SHA结果见§7，不沿用历史通过数。

| 阶段/能力 | 实现/开放状态 | 测试范围与限制 | 对完整后续阶段的影响 |
|---|---|---|---|
| 3A 固定 IPC、独立身份、认证后转发 | implemented，允许子集 | 历史真实 Linux/Next/UID 已 tested；本轮真实Linux/Next/VM tested | 必要前置；失效必须503无回退 |
| 3B 文件列表、读写、复制/移动/删除/回收站 | implemented，允许 | Agent/openat2/UID、路径攻击、ENOSPC、崩溃/恢复本轮现有套件tested | 必要前置 |
| 3B 单文件/分块/取消/续传、下载/ZIP | implemented，允许 | 文件协议/流背压/事务本轮现有套件tested；不等于 Chromium 下载桥 | 必要前置 |
| 3B 真实 Bash/PTY/resize/重连/租约/cgroup | implemented，允许 | 真 WS、PID/setsid/故障/撤销与 Chrome终端UI本轮tested | 必要前置 |
| 3C Xvnc/Openbox/PCManFM | implemented，单显示槽位 | 实际 RFB/noVNC、共享目录、关闭/重开、重启、撤销本轮tested | 已验证子集可适配 |
| 3C 可见 Chromium、namespace/seccomp、外网/管理端口拒绝 | implemented，固定入口 | 实际 renderer/网络/DAC/HTTPS UI本轮tested；不是旧 Playwright 浏览器 | 已验证子集可适配 |
| 3C Fcitx5 私有 D-Bus 与中文输入 | implemented，允许 | GTK/Qt/XIM、候选/选词/切换/重启及浏览器输入现有真实图形测试本轮tested | 已验证子集可适配 |
| 3C Claude 正式入口/登录后长期聊天 | disabled + incomplete | 资源研究 smoke/OOM 历史仍保留；正式持续交互 not_executed | 必须回 3C；阻塞完整原功能交付，不开放入口 |
| 3C 旧极速浏览器/Playwright 全路径 | disabled + incomplete | 老HTTP/WS拒绝本轮tested；正向操作/下载 not_executed | 必须回 3C，不能用 wdsvc 旧库补齐 |
| 3C 网页↔原生 GUI 文字/图片/文件剪贴板、转换/VNC 导出 | disabled + incomplete | 桥接 API503、RFB剪贴板过滤；正向互通 not_executed | 必须回 3C；Web 文件管理器内部复制不等于此功能 |
| 3C 文件拖拽到原生 GUI | disabled + incomplete | 旧 drop API503；安全正向流 not_executed | 必须回 3C |
| 3C 多个原生 GUI 共存、刷新保留窗口 | incomplete | 当前刷新结束旧 GUI；profile 保留；多应用共存 not_executed | 需回 3C 拆分显示/应用生命周期，不在部署中绕过 |
| 3C 任意应用/desktop 启动、动态图标、完整进程监控/kill | disabled + incomplete | 固定枚举与 OSC 用户确认保留；旧 apps/icon/shortcut/kill503 | 必须回 3C，不能接受任意后端命令 |
| 3C Chromium 下载与文件 Agent 衔接 | incomplete | 浏览器以 wdgui 仅写访客数据域；目录/属主/完成信号/交付/取消端到端未验收 | 必须回 3C，不能把 Agent 下载/ZIP 成功当浏览器下载桥成功 |
| 正式 HTTPS 子域名、证书续期、代理上线 | implemented 模板；not_executed 正式域名 | 已有隔离 TLS/Origin 测试；4A 参数仍只接受显式 loopback Origin | 阻塞公网正式上线，独立 HTTPS 子阶段验收 |
| 整日运行、长期缓存/Profile/容量增长 | incomplete + not_executed | 短时压力/容量/OOM 不代表长期体验 | 生产发布前需要 soak/增长预算；不自动扩卷或增限额 |
| 3D staging 安装、健康、重启、资源、真实 VPS 基线 | implemented + 历史 tested | 本轮CI已跑，4GiB CPU告警未通过；真实VPS仍131ea566 | 可复用工程基础，不等于新候选入口已验收 |
| 本轮 4A 新入口/有限恢复 | implemented + 部分tested | 1GiB有/无Swap、2GiB tested；4GiB资源1失败/后续未执行 | 仅子集工程适配；完整产品门槛 PARTIAL/BLOCKED |

当前 HTTP 关闭路径实际实现为 `pendingGuestFeature`：先桌面双认证，无身份401，合法身份503/GUEST_FEATURE_PENDING。公开旧 browser WS拒绝，server/instrumentation 不导入旧 term-ws/gui/browser 执行入口。拒绝行为已在当前SHA的真实请求中验证（4GiB后续缺项见§7），不只是扫描前端图标。普通文件/终端/UI行为维持既有权限。

## 3. staging 与候选正式部署入口

详见 [ISOLATED_DEPLOYMENT_4A.md](ISOLATED_DEPLOYMENT_4A.md)。`install-isolated.sh` 不改名冒充稳定安装器；独立参数/确认、状态目录、后端单元和恢复记录。共享 `staging_install.Install`、bundle 校验、资源算法、层脚本、systemd/polkit/Socket 模板、PG 初始化和 IPv4/Swap。没有复制第二套执行服务，没有去掉 --lab 检查；增加严格的 --isolated 授权上下文。

| 项目 | 共用/新增组件、职责与权限 |
|---|---|
| 私有 Git | 原 bootstrap.sh/private_repo.py，root 专属 Deploy Key/known_hosts/固定 SSH；4A 仅校验其本地 manifest、真实 origin/branch/commit，不再次获取 |
| 构建与产物 | 原 Debian12 CI/staging_bundle.py，固定 Node22.23.2/ABI127/glibc2.36/amd64，外部可信 SHA256+全清单；VPS不 npm/build，CI仅测试种子，不带生产密钥 |
| 安装 | 原 staging_install.py 新增固定 Layout；source/app仍/opt/webdesktop，isolated状态/etc/webdesktop-isolated root0700，后端webdesktop-isolated.service |
| 权限 | 原 wdsvc/wdguest/wdterm/wdgui，按实际 UID/GID 查询；source/.git root0700，app root:wdsvc0750/文件只读，.env0640；数据库/状态/数据域分开 |
| 运行层 | 原 Agent/PTY/GUI/browser及租约守卫，各最小包只含必要运行文件；root管理服务与网络，无通用root执行IPC |
| 资源 | 原 resource_policy.py 不改公式、不提限额；只在控制器将后台预算键映射为isolated单元名，卷预算同原算法 |
| 恢复 | 新 isolated_deploy.py，root0600 recovery.json 保存固定状态、身份、配置哈希/权限、实际单元/override及实际NFT/路由；拒绝重新基线化 |
| 检查 | check-isolated.sh选择同一staging_check.py的固定profile，live仍需显式会话替换确认 |
| 并发与保留 | 两入口共用锁；全新严格预检，已知状态恢复；失败保留日志/数据，不清理未知提取目录、源目录或文件 |

源码必须是 bootstrap 管理的唯一 `/opt/webdesktop/source`，父目录不得有其他数据，真实 Git origin 必须匹配固定仓库。入口的 Git 操作只检查本地对象/干净状态；不会用变动HEAD替代完整SHA。产物内 bundle 与固定SHA一致，root不重新克隆到未知目录。

bootstrap仍允许显式选取开发分支历史中的完整祖先提交（验证merge-base），所以后续报告提交不会迫使安装器使用变化中的分支HEAD。候选入口不会把报告SHA当作产物SHA，也不为“方便”重置现有checkout。未创建wdtunnel账号；本轮的loopback访问是管理员测试隧道，普通产品用户未来用HTTPS。不能将现有SSH解析测试解释为新VPS上已安装wdtunnel或已开放正式域名。

构建隔离的实测范围是已有 Debian CI预构建，不是原计划 wdbuild 本机构建完整流程。当前配置仍沿用已验证 app/.env（root:wdsvc0640），尚未迁至原计划目标 `/etc/webdesktop/service.env`；未做旧数据移动。root-only状态保存清单、登录种子、源bundle，不向访客分发。恢复记录不是对 root 攻陷的签名防护。

## 4. 四模式、失败与旧版边界

| 模式 | 全新 | 健康重复 | 停止/损坏 | 副作用边界 |
|---|---|---|---|---|
| auto | hostname授权+子集确认+已授权源码+固定artifact才安装 | 校验，不换密码/建库/格式化/换Profile/源码 | 仅恢复已知服务；早期不完整状态 blocked | 首次APT/IPv4/Swap/UID/SSH拒绝/卷/服务/网络均明示 |
| install | 同auto | 同auto，不覆盖 | 有冻结记录且仅最终start-service失败可恢复 | 不等于任意安装阶段通用续跑 |
| service | 没有已知安装则拒绝 | 校验并保持现有PID | 仅验证依赖后start已知socket/后端；缺文件、包、卷、PG、网络则停止 | 无APT/npm/build/Git获取/key/known_hosts/SSHreload/daemon-reload/enable/IPv4/Swap/网络修改 |
| repair | 没有已知安装则拒绝 | 同校验 | 仅缺失且有冻结原文的后台单元可精确重建并daemon-reload；已修改文件不覆盖 | 不自动装包、调限额、重建网络/卷/DB；其余须独立审查 |

密码、AUTH_KEY、数据库、用户文件与profile保持；rootseed文件不是用户在UI修改后的当前密码，恢复不重新写seed或覆盖DB密码。真正PG数据和访客数据不纳入不可变配置哈希；恢复检查器不会借此读取/备份整个用户卷。

旧单账号、staging与isolated不能互相收养。旧.conf、原同账号目录、未知身份/单元/规则/数据均拒绝；不 chown-R旧数据、不清库、不删目录、不回退同UID高权限路径。未来迁移仍须按原§7独立盘点、可验证备份/恢复、空间与停机窗口、保留旧副本另行设计，本轮不实施。

此拒绝保证针对新候选入口；稳定旧入口按要求原样保留，不能当成isolated布局的恢复/升级工具，两个入口不得混用。本轮没有声称给旧入口增加了新布局识别能力，也不提供在已有isolated机器调用旧安装器的操作步骤。

早期安装失败没有足够恢复证明，状态保持 failed/installing并阻塞，不能删state重跑。共享安装状态原子写入增加O_EXCL/O_NOFOLLOW，残余state.new也停止供审查。新入口对已生成、已封存、最后启动阶段的失败/中断提供有限恢复。不可恢复的情况建议保留日志、审查快照/重装授权，不自动卸载或回滚未知数据。

预检/包验证失败发生在状态目录创建之前，可能只有退出1与终端拒绝原因，没有install.log；这是避免预检改动系统的边界。取得已验证产物、建立状态之后，保护日志为`/etc/webdesktop-isolated/install.log`，按STAGE定位；检查时不要输出.env/login.json/私钥内容。保留失败提取目录和state.new供管理员审查，不自动删除来重试。

## 5. 安全问题与修正证据

### 可优化移除的断言（已修正）

旧 staging_install.py、staging_bundle.py、staging_check.py及层/卷授权在安全判断中使用assert。普通shell入口的python -I会忽略PYTHONOPTIMIZE环境变量，但**直接以python -O运行helper/调用extract**仍可移除关键检查；这不是远程root提权，属于部署校验可绕过问题。

本机隔离临时目录重放旧`6f6df95` bundle helper，对仅含无害字节的合成包同时提供错误摘要和错误提交：普通解释器exit1、未提取；-O exit0并提取。修复后-O/-OO均exit1且不提取。证据`.test-results/phase4a-assert-repro.json`，未运行旧系统安装器、未改真实VPS。

共享helpers中的75个安全assert已转为显式运行时ValueError检查；层授权和卷预算读取同步去除assert。22项真实优化子进程包测试重跑原11类正反向（-O/-OO），不是只扫描源码。原异常/安全断言没有删除，变更为不能优化掉的等价条件。资源公式不变。

### ACL、状态与配置边界

仅检查属主和mode无法识别命名/默认POSIX ACL；父目录默认ACL可能在chmod修改mask时改变敏感文件实际权限。本轮在新安装受保护根目录、源码/配置及恢复记录校验中拒绝未审查的extended ACL。此风险机理为源码/权限规则推导，不冒称已在用户VPS读到凭据；本轮真实VM预检和模式中的ACL反例均已拒绝。

首次预检还拒绝已有legacy防火墙规则/未知namespace；不合并未知规则。service只读比较已冻结NFT规则、namespace路由与sysctl，缺失不自动重建。Unix Socket实际uid/gid/mode在恢复前检查；缺失仅能通过已验证的固定socket单元创建，不开放任意root命令。

root-only源码和app清单保持校验；source bootstrap manifest/真实origin不符即拒绝。实际单元来源/DropInPaths限定受保护配置树，未知override或NeedDaemonReload时service停止，不加载未知配置。repair只恢复一个缺失的冻结后台单元，不覆盖已修改内容。

本轮没有改认证协议、GUI/PTY生命周期、资源预算/阈值、Chromium启动参数或sandbox、文件openat2策略。共享功能关闭入口增加真实匿名401/已认证503回归。IPC失败仍原503，不引入wdsvc本地文件/命令回退。

首轮新恢复器把符号链接的0777模式也套用普通文件“不可组写”条件，四组新入口均在首次安装成功后的重复校验被安全拒绝。修正按对象类型处理：链接要求root属主、精确清单目标和包内安全目标；普通文件仍要求不可组/其他写、文件类型/大小/哈希一致。未放行任意链接或可写代码。依据：[Linux symlink(7)](https://man7.org/linux/man-pages/man7/symlink.7.html)；Linux普通链接模式不用于访问控制且固定0777。本问题为本轮程序故障，非时序/资源问题，保留失败run并重新测试。

随后真实VM发现候选入口的root私有umask077使资源算法原定0755的非敏感策略目录实际变成0700：Agent读取`/etc/webdesktop-resources/policy.json`触发PermissionError/重启，资源接口503。这是程序可用性故障，不是资源不足或时序。修复仅对首次独占创建的非敏感预算目录落实原定0755；秘密状态/source/env权限不放宽，`resource_policy.py`及限额公式不变。新增四身份读取公开策略测试，并在安装/恢复报告就绪前，以wdsvc+空环境通过固定Socket请求`GET /v1/info`，验证实际wdguest UID及协议2；失败停止，无本地回退。原来HTTP健康成功不足以证明Agent就绪，故同时保留HTTP、所属PID和实际IPC检查。

service还必须确认后台已启用；管理员disable或enable自身失败时明确拒绝，不隐式enable。故障夹具限定为已enable后最终激活失败，并在真正重启后、手工恢复前检查自动启动，避免用恢复掩盖启动配置问题。缺失backend单元只允许repair恢复冻结原文，未改动单元权限或任意覆盖策略。

实际重启进一步发现polkit是Debian的D-Bus按需激活服务：后台/各挂载已自动启动，但polkit空闲inactive导致旧恢复器exit1。新恢复器将polkit与dbus的实际单元来源、drop-in和配置哈希纳入冻结校验；dbus必须active，polkit只允许active/inactive，failed仍拒绝，不主动启动或修改授权规则。新增真实测试：停止polkit→service保持其inactive且通过→wdsvc通过现有仅允许停止固定PTY单元的规则触发D-Bus激活→polkit实际active；重启后健康检查再测真实PTY/GUI。

另一次1GiB有Swap资源矩阵为9通过/1失败：热启动升级503。原始样本显示此前整套恢复故障审核反复哈希近3万文件，IO PSI full avg10约22%、memory full约9.8%，随后正常资源策略进入critical并执行30秒恢复滞后；不是认证失败、OOM放宽或源码权限问题。测试现在在冷启动前只观察既有资源接口，要求两个不同采样均derivativesReady/normal/无原因，最多80秒，记录实际等待；不反复启动GUI、不加sleep、不改阈值或上限。专门的磁盘/CPU/OOM压力与503拒绝测试保留。该失败仍是原run的真实失败；新装/大规模校验后立即打开GUI可能被保护性拒绝，是需说明的可用性边界。

### 输入包副本权限（安全修正）

只保护source/app不够：旧预检对输入tar.gz只检查root属主/不可写，允许0644。管理员若把包放在公开目录，普通账号可读取其中源码；此判断来自旧条件及Linux DAC，不冒称用户VPS发生过泄露。e976ead要求输入为绝对路径、root0600、单链接普通文件，所有父目录root管理/不可被非root写入/无未知ACL或符号链接；不自动chmod或清理未知输入。

新增真实VM反例先在公开测试路径建立root0644包，确认nobody实际能打开；新CLI在创建状态/身份前拒绝；只收紧该已知测试文件到0600后，nobody实际读取失败。实际安装输入位于root0700目录且文件0600。ZIP、tar.gz、Git bundle都须保持私有；预检保证所选输入及已知运行/源码/状态目录，不能扫描识别磁盘上任意管理员另存副本，也不会删除未知文件。CI夹具的公开副本是已知的一次性合成测试部署产物，不含生产凭据，测试后移除。

### 重启观察器边界

ba58422在1GiB无Swap/2GiB等组的SSH刚恢复时is-active返回3。2GiB原始证据12392712288的units.txt捕捉到backend仍inactive、guest socket仅启动2秒；serial.log随后明确出现PostgreSQL、isolated backend启动及multi-user.target到达。这是SSH就绪与系统启动完成的观察边界，不能算后端已经验证失败或成功。

f9bfc25只改夹具：重启后以`systemctl is-system-running --wait`只读等待启动事务完成（90秒边界），要求退出0/running，再检查后台已enable且自动active，最后才调用service与第二轮健康检查。不补启后台、不reset-failed、不增加固定sleep，不接受degraded；`boot-settlement.json`保留状态、退出码、实际耗时，日志收集加入isolated/PG单元。最终矩阵以此重新验证，旧失败仍保留。

## 6. 本轮提交与修改文件

代码提交：`4fa00eef2d062c2d30a4d59f2f92280b241e7364`（入口与最小恢复）、`cafd6664153adabacf834a9193bb473877100613`（按文件类型校验、真实故障/ACL测试）、`f569b42b96cf9dbd00e508810c7de38a1c487c37`（禁用状态/自动启动与激活故障边界）、`a0705766b0c8310f5966192f943a8d54f587a9bf`（非敏感策略目录与实际IPC就绪）、`3d2c27a9c6a409986fe69609436af0b53ddac065`（故障夹具恢复systemd缓存、重复安装元信息纠正）、`547f4887fec82f1d65dc453e7e3c661dda712aed`（GUI刷新夹具遵循现有会话恢复）。`ba584223774ccb1c7bc3e72fe0d386702ef4b9cc`（D-Bus按需激活的恢复边界、实际激活测试及审核IO后的有界采样基线）。另有`f9bfc256893bb403f23e5232fb367a2fdd1572b2`（等待systemd真实启动完成的只读夹具）、`e976ead282bef74924321a7cf2fd745717e1c442`（输入包root0600/父目录保护）。最终受测代码为e976ead；547f488之前的两个夹具修正只改测试，ba58422修改候选恢复器的polkit状态判定和配置冻结，不改认证协议/规则或资源策略。报告单独文档提交，不能拿报告HEAD替代运行artifactSHA。

| 文件 | 修改目的；涉及安装/权限/systemd/DB |
|---|---|
| install-isolated.sh、check-isolated.sh | 新独立候选入口，不修改稳定install.sh |
| scripts/isolated_deploy.py | 新固定profile控制/冻结恢复；root权限与systemd启动，保持数据库/数据 |
| scripts/staging_install.py | 同一引擎固定Layout、bootstrap来源、后台名映射、权限发布、显式校验、独占状态写入 |
| scripts/staging_bundle.py | 原包/平台/摘要/条目/文件检查不可被优化移除 |
| scripts/staging_check.py | 同一健康器选择固定profile；显式校验，身份/权限/资源/真实功能证据 |
| scripts/layer-context.sh | 固定staging/isolated上下文授权，不去掉lab守卫 |
| scripts/setup-guest-volume.sh | isolated同样使用已计算安全卷预算，读取检查无assert |
| scripts/staging_smoke.mjs | 关闭历史GUI/Playwright/剪贴板/kill路径的真实请求回归 |
| gui-runtime/browser-network.sh | 同一网络脚本验证固定profile状态；规则/限额不变 |
| tests/test_isolated_deploy.py | 优化解释器原包攻击矩阵与固定profile/状态策略 |
| tests/phase4a_modes.py | 仅一次性VM真实四模式、故障/数据保留、UID/PG信号与拒绝工具测试 |
| tests/run_systemd_vm.py、staging_vm_driver.py | 共用QEMU/严格测试SSH控制器，隔离profile四规格、真正重启、现有GUI+真实终端UI |
| tests/staging_resource_runtime.mjs | 保护事件目标按固定后台profile选择；压力/限额不变 |
| tests/desktop_ui_runtime.mjs | 刷新时等待既有有效会话恢复，不与短暂SSR密码框竞争；必须恢复桌面，不以重复登录掩盖回归 |
| .github/workflows/staging.yml | 保留旧5组，增加新4A四组；共用同批Debian产物 |
| README.md、ISOLATED_DEPLOYMENT_4A.md、STAGING_TEST_GUIDE.md、本报告 | 明示候选、差异、门槛、模式、恢复和审查证据 |

未改稳定install.sh、bootstrap.sh/private_repo.py、resource_policy.py、原PG恢复模块、既有数据库/用户文件、Next/认证/IPC协议、IME方案、历史报告、main/Beta。原IPv4-only、Swap、关闭自动更新、取消欢迎/示例文件保持。

## 7. 当前代码测试与原始证据（最终结果）

首批CI：[Validate 39648036599](https://github.com/isAliceYoung/fa/actions/runs/39648036599)、[Staging/4A 39648033371](https://github.com/isAliceYoung/fa/actions/runs/39648033371)，对应4fa00ee，状态/结果与最终批次分开补入。历史1284/0/0/71只属于72e06b9，不作为本轮测试数量。

### 7.1 失败与夹具历史，不作最终提交结果

| 代码 | Validate / Staging Run ID | 实际任务状态与失败原因 |
|---|---|---|
| 4fa00ee | 39648036599 / 39648033371 | Validate 7成功/1失败（HTTPS GUI刷新测试等待已被会话恢复移除的密码框）；Staging 7成功/4失败（新恢复器错误拒绝普通符号链接0777） |
| cafd666 | 39649367493 / 39649367314 | Validate 8成功；Staging 7成功/4失败。2GiB原始证据12368289523：预检11、故障恢复1、模式19通过，随后资源10失败；Agent策略目录权限错误，后续UI/健康/重启未执行 |
| f569b42 | 39650330461 / 39650330060 | Validate 7成功/1失败（同一刷新夹具竞争）；Staging 7成功/4失败（还原故障单元后未刷新测试systemd缓存，后续恢复被正确拒绝） |
| a070576 | 39650895788 / 39650895807 | Validate 8成功；Staging 7成功/4失败。2GiB证据12370616914：预检11、首次实际故障恢复1通过；模式13通过/1失败/7未执行，后续资源/UI/健康/重启未执行 |
| 3d2c27a | 39697597087 / 39697597063 | Validate 8成功；Staging 7成功/4失败，实际重启后的恢复器错误要求polkit立即active；不能并入最终e976ead统计 |
| 547f488 | 39697753636 / 39697753660 | Validate 8成功；Staging 7成功/4失败。新模式21项全通过；1GiB有Swap资源9通过/1失败（审核IO后热启动503），4GiB/1GiB无Swap在重启后被polkit空闲状态拒绝；2GiB在SSH刚恢复时backend is-active退出3，尚未完成启动排程/服务异常的区分；三组均未执行第二次健康检查 |
| ba58422 | 39699366290 / 39699366289 | Validate 8成功；Staging 8成功/3失败，三组isolated因SSH先于应用启动完成而过早检查，真实串口证据见下文；1GiB有Swap完整通过 |
| f9bfc25 | 39700764420 / 39700764529 | Validate 8成功；Staging 10成功/1失败，4GiB staging网页UI为4通过/1失败；该次观察失败保留，未用后续运行覆盖成成功 |

上述失败均保留Actions原记录和Artifact，不把中断后的测试补记为通过。`f569b42`与`a070576`的NeedDaemonReload不是资源目录故障：文件内容虽还原，unit/drop-in的mtime已改变。测试清理现在先确认原字节还原，再由**测试夹具**daemon-reload并确认NeedDaemonReload=no；产品service仍拒绝此状态、不自行daemon-reload。不通过sleep/重试或放宽检查解决。

GUI刷新两次失败定位于`desktop_ui_runtime.mjs`原第73行：SSR短暂显示密码框，异步`/api/auth/session`恢复会话后移除它，count()后fill()竞争。修正测试要求同标签页即时刷新恢复有效桌面；若恢复失败直接失败，不改认证、不重复登录、不增加超时。新设备登录撤销、旧GUI整树回收、profile保留等断言继续保留。

### 7.2 最终固定代码与计数口径

最终代码：`e976ead282bef74924321a7cf2fd745717e1c442`。本轮最终工作流：[Validate 39700931994](https://github.com/isAliceYoung/fa/actions/runs/39700931994) **8/8任务成功**；[Staging/isolated 39700931942](https://github.com/isAliceYoung/fa/actions/runs/39700931942) **9成功/2失败**。最终全部结束后已独立下载17份测试证据ZIP，逐份JSON汇总；不拿上表旧提交充当新提交结果。报告及两份输入包准备说明单独文档提交，不改变受测代码；报告SHA以承载本文的文档提交及最终回复为准。

| 测试类别 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| 常规回归（215×2、资源模型13×2、准入模型17×2） | 490 | 0 | 0 | 0 |
| 构建和类型检查 | 8次命令退出0* | 0 | 0 | 0 |
| 安装与恢复（预检/模式/故障及健康） | 698 | 0 | 0 | 268 |
| 权限与安全边界（源权限9、Linux执行/文件/认证73、systemd18） | 100 | 0 | 0 | 0 |
| PTY / GUI / Chromium（含实际终端UI） | 236 | 0 | 0 | 19 |
| IME专项模型/Chromium集成 | 96 | 0 | 0 | 0** |
| 资源限制/压力必需验收 | 82 | 2 | 0 | 6 |
| **必需用例合计** | **1702** | **2** | **0** | **293** |
| 既有GUI/Claude资源可行性实验（独立，不混入必需项） | 7 | 3 | 0 | 0 |
| **所有显式用例/实验合计** | **1709** | **5** | **0** | **293** |

*构建/typecheck共4组环境×2条命令，含旧f195兼容包；该行是命令退出检查，不叠加进用例总数。额外Bash语法、Python编译、Node helper、OSC/Origin/HTTP边界脚本均退出0，也不虚增案例。**IME96=两平台事件模型30×2+真实Chromium/xterm36；另有独立PTY UI及三组新入口UI的真实Bash输入测试，已在PTY类别计数，不重复加和。真实Windows搜狗本机人工属于此前已接受的证据，本轮未重做；真实VPS新版IME仍not_executed，单独待办而不是伪造为CI案例。

按工作流分解：Validate **880/0/0/0**；staging兼容矩阵 **376/1/0/156**；isolated矩阵 **446/1/0/137**。其中旧f195的82通过/23未执行是本轮重新执行的旧应用兼容范围，不是e976ead应用功能证明。293只覆盖明确定义的自动化计划及提前中断后的缺项；未开发/未编写的生产与3C场景另列，不声称293概括所有未来工作。

独立资源实验：无Swap组Chromium320/384MiB烟测通过，Claude320/384/448MiB三项资源/交互失败；有Swap组五项烟测通过，安全失败0，全部`production_usable=false`。有Swap烟测不代表Claude正式账号登录/持续聊天通过，也不删除历史4项失败/OOM记录，不开放正式入口。资源实验的3个失败加上必需CPU断言2个失败，所有显式失败为5，不能写“全部测试零失败”。

| Validate原始证据 | Artifact ID（run39700931994） |
|---|---:|
| Ubuntu常规 | 12392104172 |
| Debian常规/源权限 | 12392658866 |
| Linux身份/IPC/文件/WS/认证 | 12393240230 |
| 真systemd启动/重启 | 12393095350 |
| PTY/systemd/Next/终端UI | 12393596036 |
| IME A/B与真实Chromium | 12391894627 |
| 真GUI/Chromium/Fcitx/HTTPS/资源，Swap | 12393743383 |
| 同上，无Swap | 12393699381 |

证据含原始JSON、journal/serial、systemd security/status、真实boot ID/SSH及截图。对应Artifact可在上述run页面按名称下载；测试证据默认到期2027-01-20，部署包仅7天。中间一次本机下载EOF重试后成功，是证据传输故障，不计作程序测试或CI用例。机器生成汇总留在忽略目录`.test-results/phase4a-final-ci-summary.json`与`phase4a-final-artifact-index.json`供本机复核，Git只保存本报告的固定快照和CI定位信息。

统计只加和明确计数的测试用例；脚本语法检查、build/typecheck、原始SSH拒绝/真实boot ID变化和完整产物清单校验另列退出码/条目数，不虚增用例。Ubuntu/Debian常规JSON各保留两个范围说明（完整VPS与生产）；完整VPS由独立VM矩阵覆盖，生产未授权仍列独立未执行项，不把范围文字混作额外单元测试或跳过。

### 7.3 固定产物与独立核验

产物名称：`staging-debian12-e976ead282bef74924321a7cf2fd745717e1c442`；[Artifact ID 12393135327](https://github.com/isAliceYoung/fa/actions/runs/39700931942/artifacts/12393135327)；Staging run **39700931942**；到期 **2026-10-29T00:40:45Z**。外层ZIP API摘要为`f80207f393bcb3779ec6a67f406e78d7e41443f429aedd7dc928639d1c14552d`，独立下载比对一致；它不能替代安装参数要求的内部tar.gz摘要。

**最终内部tar.gz SHA256：`8fc9d1560460877b6945a1bee1b9031d0a6c379daa6d2d4cb4432177aac4af8e`**，225746378字节。已从GitHub独立下载，流式验证manifest完整SHA、29585文件的逐项大小/哈希、31个符号链接目标、29616条目的完整覆盖及无app/.env/.git/tests；确认Debian12/amd64、Node22.23.2、ABI127、glibc2.36。实际.next JS包含保留无障碍模式的IME桥。验证退出0，本地原始记录`.test-results/phase4a-release-runtime-verified.json`；原始包/清单以CI Artifact为准，未把机器生成产物长期加入Git。

此前547f488包也曾核验（225742919字节，SHA256 `53b61196fc26f9bb26be8c40ae38da5d019e7a531ccc66e745c8da2ca24d653a`）；它已被最终e976ead产物取代。旧a070576包也曾独立核验：225742311字节、SHA256`4baaa68a1eeaddddf2569f2ec9dc10eeda6adfcc676f62b3295ed7c442a10b20`，29585文件+31链接；它只证明旧包完整性，不是最终受测产物。

本机准备阶段Windows/Git Bash常规215/0/0、IME事件模型30/0/0/0、TypeScript退出0；这是本轮本机准备测试，不替代真实Linux身份或systemd。

执行入口均单行；以下VM命令只用于可丢弃CI宿主，需要workflow准备的工具/产物，不是连接用户服务器的命令：

```bash
python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json
node tests/terminal_ime_test.mjs
node tests/terminal_ime_browser.mjs
python3 tests/linux_private_repo.py
python3 tests/linux_guest_layer.py
python3 tests/linux_files_runtime.py
python3 tests/linux_ws_runtime.py
python3 tests/linux_terminal_boundary.py
python3 tests/linux_status_runtime.py
python3 tests/run_systemd_vm.py --isolated --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=1024
python3 tests/run_systemd_vm.py --isolated --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=0
python3 tests/run_systemd_vm.py --isolated --ram-mib=2048 --vcpus=2 --disk-gib=20 --swap-mib=1024
python3 tests/run_systemd_vm.py --isolated --ram-mib=4096 --vcpus=4 --disk-gib=30 --swap-mib=1024
npm run build
npm run typecheck
```

CI临时key、管理员账号、bootstrap provenance替身、故障wrapper、公开测试包、DB fixture只在tests创建，普通入口不安装这些夹具。VM镜像为固定Debian12 genericcloud 20261018-2635，控制器核验既有SHA512；QEMU/cgroup v2/systemd真实启动，不能解释为容器继承FD或真实用户VPS验收。

### 7.4 安装/恢复矩阵的实际结果

本节全为最终代码工作流39700931942的新执行结果；baseline行故意运行固定旧应用f195f90c01f38fcf93c20ee616f9e62a15063018作兼容对照，不将它解释为e976ead应用功能证明。

| 配置/入口 | Passed | Failed | Skipped | Not executed | 原始Artifact ID / 结果 |
|---|---:|---:|---:|---:|---|
| 1GiB/1CPU/10GiB/1GiB Swap，isolated | 134 | 0 | 0 | 12 | 12393625712，完成实际重启 |
| 1GiB/1CPU/10GiB/无Swap，isolated | 134 | 0 | 0 | 12 | 12394030687，完成实际重启 |
| 2GiB/2CPU/20GiB/1GiB Swap，isolated | 134 | 0 | 0 | 12 | 12393461741，完成实际重启 |
| 4GiB/4CPU/30GiB/1GiB Swap，isolated | 44 | 1 | 0 | 101 | 12393014815，CPU告警观察断言失败，后续UI/健康/重启未执行 |
| 1GiB/1CPU/10GiB/1GiB Swap，staging | 95 | 0 | 0 | 12 | 12393101975，完成实际重启 |
| 1GiB/1CPU/10GiB/无Swap，staging | 95 | 0 | 0 | 12 | 12393116623，完成实际重启 |
| 2GiB/2CPU/20GiB/1GiB Swap，staging | 95 | 0 | 0 | 12 | 12392734201，完成实际重启 |
| 4GiB/4CPU/30GiB/1GiB Swap，staging | 9 | 1 | 0 | 97 | 12393760501，同一CPU告警断言失败，后续未执行 |
| 旧f195f90应用兼容，1GiB/Swap | 82 | 0 | 0 | 23 | 12393291358；当前控制器实际重跑，非历史数字挪用 |

正常isolated每组：预检12、真实最终激活故障恢复1、模式/保留/权限22、资源10、网页GUI5、终端UI4、重启前后live健康各40通过/6未执行；共134通过/12未执行。四组均实际完成预检12、故障恢复1、模式22，4GiB随后资源9通过/1失败。正常staging每组10资源+5UI+2×40健康。

两组4GiB因真实失败提前退出，未生成UI/健康/重启成功报告。Not executed按固定计划补记：2×46项健康+5项UI=97，isolated再加4项终端UI=101；这是明确的覆盖缺项推导，不冒充生成了原始健康JSON。正常健康每次保留6个原始not_executed标签：脚本内外部管理员SSH、Claude、剪贴板、拖拽、多GUI、正式域名。CI控制器另有管理员实际SSH/四身份拒绝/boot ID记录，但不改写健康脚本计数。全部计数只统计显式案例，驱动命令退出/逐文件哈希另列。

CPU失败原文：`CPU PSI/throttling must be observable under actual quota contention`；[isolated job 119570615932](https://github.com/isAliceYoung/fa/actions/runs/39700931942/job/119570615932)、[staging job 119570615964](https://github.com/isAliceYoung/fa/actions/runs/39700931942/job/119570615964)。两者资源JSON均9/1，其他压力/回收/响应测试继续执行而非假装全部中止。isolated p95/max为262.5/313.5ms，staging为388.3/427.5ms；CPU样本出现过cpu_wait原因，但这不证明在专用22秒窗口内满足warning/critical观察条件。负载持续性、采样相位和告警滞后尚需独立定位；不把它武断归为纯夹具问题，不改公式/阈值、不追加sleep/重跑。**4GiB部署验收blocked**。

1GiB有/无Swap和2GiB重启后的`boot-settlement.json`分别为running/exit0，耗时2063/315/843ms；其后后台自动enable/active校验在service调用之前完成。实际SSH拒绝文件保留四账号各exit255，管理员SSH与boot ID变更可复核。systemd-analyze verify在安装日志中、security评分和最终状态在相应Artifact；评分本身不被当作“安全已通过”的替代条件。

## 8. 资源、IME、未执行项

1GiB/1CPU/10GiB有/无Swap、2GiB/2CPU/20GiB、4GiB/4CPU/30GiB使用同一个安全自适应函数；2/4GiB大卷以对应更大系统盘实测，不外推10GiB可容纳任意大卷。名义主机Swap与MemorySwapMax分开，父子限额不相加当预留。VPS不现场构建，原450MiB压缩/2GiB展开与主机预留上限不变。首次完整包和临时提取目录保留占空间，磁盘不足明确停止，不删用户文件；长期运行/更新空间仍需独立计划。

### 8.1 当前isolated四规格资源证据

| 名义配置 | 实测RAM / 业务/执行RAM上限 MiB | 主机Swap / 业务Swap上限 MiB | 访客镜像预算 MiB | 业务内核memory.peak MiB | 并发请求p95 / max ms | 系统盘样本剩余 GiB |
|---|---|---|---:|---:|---|---:|
| 1GiB，Swap | 972.3 / 780 / 524 | 约1024 / 640 | 512，健康核验 | 716.6 | 218.7 / 235.9 | 4.36 |
| 1GiB，无Swap | 973.3 / 781 / 525 | 0 / 0 | 512，健康核验 | 718.4 | 224.7 / 248.8 | 5.36 |
| 2GiB，Swap | 1979.5 / 1723 / 1275 | 约1024 / 861 | 1024，健康核验 | 897.1 | 221.6 / 270.2 | 13.79 |
| 4GiB，Swap | 3927.3 / 3447 / 2679 | 约1024 / 1023 | 公式2048；文件系统观测1961.9 | not_executed健康快照 | 262.5 / 313.5 | 23.20 |

1GiB有Swap的执行树MemorySwapMax仅262MiB、浏览器192MiB；2GiB分别637/425MiB，无Swap均为0。父子上限是共享天花板，不相加作为物理预留，也不把主机1GiB Swap当成每个服务各有1GiB。OS/控制面RAM预留分别为1GiB档192/256MiB、2GiB档256/448MiB；root层不放宽上限。

资源流采样（不是连续全程峰值）：业务memory.current最大686.9/660.8/744.9/435.5MiB，业务swap.current采样最大2.5/0/0.2/0.3MiB，任务数采样最大114/114/129/143，CPU归一化采样最大88.5/87.8/87.6/86.5%。原始memory.events、events.local、cpu.stat、压力和磁盘数据保留在healthy before/after与所有resource-runtime JSON；三组健康快照中后台、PG、Agent本地oom/oom_kill均0。父层继承的OOM计数包含专门注入的wdterm故障，不误称“整机无OOM”，也不将其转为未解释的生产崩溃。4GiB没有生成健康快照，不补造内核peak数据。

审核IO恢复等待为58.2/50.8/5.1/5.1秒；此后Chromium冷启动首帧2.95/4.35/3.02/2.70秒，三次热启动首帧分别2.28–2.45/3.50–3.69/2.50–2.67/2.65–2.85秒。它们是CI虚拟机、等待资源基线后、固定窗口/网站的短时结果，不包含用户实际VPS链路，不与用户此前约20秒冷启动作等价比较。1GiB在大规模代码校验后可能需要接近一分钟恢复；不能把Swap避免OOM或等待后的快照当作全天可用证明。

未单独采集APT/解包整个安装阶段的主机连续RAM峰值、全程任务峰值、整日缓存/日志增长、跨重启逐业务marker/备份一致性恢复演练；本轮证明安装实际完成、模式数据/UUID/凭据保留及重启后服务/认证/文件/终端/GUI可用，不能替代完整备份恢复/长期容量验收。

IME修复保持screenReaderMode=true；模型、真实Chromium/xterm、真实受限Bash+WS+终端UI在新部署矩阵重新验证。用户之前的Windows搜狗本机诊断人工结果仍为已验证独立成果，不因VPS未更新而否定；**本轮真实VPS新版IME及生产人工验收not_executed**，不能用CI代替。

仍未执行：真实Evoxt安装/更新/重启、真实生产Deploy Key/私有GitHub现场授权、正式HTTPS/DNS/证书续期、Claude正式交互、旧Playwright正向功能、原生剪贴板/拖拽/多GUI、浏览器下载桥、整日soak/长期缓存增长、旧数据迁移/回滚演练、本机wdbuild完整发布。关闭功能保持disabled；测试缺项不算通过。

既有Claude OOM、IME首次ASCII Enter/夹具CSP、Debian latest校验失败和此前staging观察器失败继续保留在原报告及run，不覆盖成成功。本轮若重现资源失败，按独立实验报告，不混入功能必需回归或修改限额。

## 9. 风险与停止门槛

| 级别 | 问题/触发条件与证据 | 阻塞与后续 |
|---|---|---|
| HIGH（验收阻塞） | 4GiB/4CPU两入口CPU告警观察断言失败，资源9/1；后续GUI/健康/重启缺项。当前并发响应仍正常，尚无证据指向权限越界 | 阻塞完整4A及4GiB部署验收；先独立定位负载/采样/告警关系，补齐失败路径证据，不降低阈值或重跑碰运气 |
| HIGH | 原§9/10/11的3C功能未全迁移，入口503/禁用并不是正向功能完成 | 阻塞全功能正式交付；回3C完成Playwright/剪贴板/拖拽/应用/多GUI/下载衔接；子集可独立审查 |
| HIGH | 仓库当前API只读查询Public；VPS DAC不能阻止复制公开GitHub源码 | 阻塞“私有源码不可外部取得”的完整生产目标；需用户单独授权仓库/Artifact访问策略，本轮不修改可见性 |
| HIGH | 未完成生产供应链/完整wdbuild发布、稳定入口替换、配置迁出app、原地升级/备份恢复/迁移 | 阻塞完整Phase4/正式运维交付；后续独立4子阶段设计与验证 |
| HIGH | 无Swap Claude三档历史OOM及短时研究不等于实际持续可用 | Claude正式入口保持blocked；回3C独立账号交互与资源验收，不能加内存/去沙箱凑通过 |
| MEDIUM | 新入口只恢复已封存最终启动阶段；包/网络/卷/PG或早期安装损坏无法自动修复 | 当前明确blocked，不自动接管；保留状态、日志与快照，后续受审查恢复子阶段补齐 |
| MEDIUM | 512MiB低配卷/缓存/回收站长期增长与系统盘升级空间未验收 | 阻塞长期容量承诺，需真实soak/增长/备份预算；不自动扩大卷 |
| MEDIUM | 全量审核IO后1GiB恢复基线需约51–58秒，期间新GUI可保护性503；长期安装峰值和逐业务跨重启备份一致性未单独验收 | 不将短时等待后的启动速度当生产承诺；后续完成运维窗口、容量/备份与恢复验证，不调大限制掩盖 |
| MEDIUM | 正式HTTPS、生产VPS新版IME人工未执行 | 分别阻塞公网上线/该环境IME验收；与已有本机IME通过区分，需新授权后人工复验 |
| LOW | 严格来源/配置/网络冻结会拒绝管理员手工改动或OS更新 | 预期安全失败，不能手删seal收养；后续制定受审查升级流程 |

没有证明root被攻破后的安全；root/OS/管理员信任是边界前提。闭源服务端保护不等于隐藏浏览器前端JS。所有输入ZIP/bundle副本也由管理员保护，不能宣称扫描了未知目录的每份副本。发现关键隔离破坏时对应适配必须停止，不能恢复旧wdsvc执行通道。

## 10. 验收建议与停止

完整Phase4A门槛为**PARTIAL / BLOCKED**，不因开始适配就倒推3C完成。1GiB有/无Swap和2GiB候选入口取得可复核的安装/模式/故障/真实重启/功能证据，4GiB未通过，当前CI不是全绿；不能建议完整4A通过或直接进入4B。未观测到现有已验证子集的跨UID取密/特权执行突破，不把它扩展为未执行场景的安全保证。

建议下一次先独立审查本报告和固定产物/故障证据，优先解决4GiBCPU告警观察与被阻断测试。修复范围先限于可复核的压力刺激/分阶段采样/测试夹具；若证据证明需要修改算法或阈值，再取得对应范围授权，不能以验收为由私改。若继续部署适配，须明确授权“已关闭未迁移功能的子集”范围，并先完成遗漏恢复/升级/配置分离与HTTPS准备审查；若目标是原计划全功能交付，则必须返回3C处理上述功能门槛。4B只是可能的管理标签，原计划未定义其正式内容，本轮不自行启动。

用户另行决定真实VPS新版IME人工测试；本轮不要求为代码细节授权、不连接服务器。当前数据库、密码、访客文件、SSH和生产密钥不动；不修改main、Beta或仓库可见性，不自动进入下一阶段。
