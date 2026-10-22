# Phase 4A-R1：安全补审与失败测试收敛

日期：2026-10-22。仅 `feature/private-deploy-isolation`。

**Phase 4A-R1: PASS（授权的安全补审与已验证功能子集）。** 四规格双入口、旧版兼容及全部必需回归通过。独立Claude资源实验仍有4项失败，正式入口继续关闭。**4A已验证子集技术矩阵通过，不代表完整Phase3C或原计划完整Phase4通过。**

## 1. 基线、范围与证据口径

起点工作区干净，HEAD 与远端分支均为报告提交 `3c3796c3571ebe8a31aed52a6189566c4769d723`，运行代码为 `e976ead282bef74924321a7cf2fd745717e1c442`。重新读取 ISOLATION_PLAN §7/9/10/11、PRODUCT_ARCHITECTURE、PHASE4A_ADAPTATION_REPORT、ISOLATED_DEPLOYMENT_4A、PHASE3C_CHROMIUM_REPORT、PHASE3D_STAGING_REPORT、RESOURCE_MANAGEMENT、TERMINAL_IME_FIX_REPORT，并检查实际实现。

重新通过 GitHub API 核对历史 [Validate 39700931994](https://github.com/isAliceYoung/fa/actions/runs/39700931994) 8/8 成功、[Staging 39700931942](https://github.com/isAliceYoung/fa/actions/runs/39700931942) 9成功/2失败，原 Artifact 仍可获取。历史 **1702 passed / 2 failed / 0 skipped / 293 not_executed**、独立实验 **7/3/0/0** 保留在原报告，不充当本轮结果。

本轮两个代码提交：

- `2d3c500060ddb312510499d968cc4b3373b949d8`：运行产物元数据/ACL/未知条目校验、真实负向补审、CPU时间线和状态机回归。
- **最终受测代码 `5c9ceecfb3e8b07b449ac55c9080c16f0a9b7a76`**：仅修正新增诊断夹具的 cpuset 继承读取和异常清理。

报告随后单独提交，报告 SHA 以 Git 历史与最终回复为准；不得把文档 SHA 当作部署包 SHA。所有新结果对应最终受测代码，旧版本对照及首轮失败另外标明。

一 VPS 一个实际桌面用户。wdsvc/wdguest/wdterm/wdgui 是权限身份，不是多租户。未创建动态 UID 池、额外用户数据库或多租户调度。本轮仅 CI 可丢弃 Debian12 VM，从未连接真实 Evoxt VPS；用户 VPS 仍是此前 `131ea566...`，新 IME 未部署。

## 2. 产物权限补审与最小修复

### 2.1 风险成立的精确边界

旧 `verify_artifact()` 对普通文件检查 root、普通文件类型、不可组/其他写、大小及 SHA；APP 根有 `owned()`/ACL 检查，bootstrap 也保护父目录，但没有逐文件 ACL、完整子目录元数据或额外条目检查，且允许 0750→0755、0640→0644 的只读权限漂移。

**单独增加普通文件命名读 ACL，不等于访客已经能读它。** 原 root:wdsvc0750 父目录阻止三个访客遍历；实际 open(2) 仍被拒绝。这一项为 `confirmed_safe`（当前父目录不变的范围）。访客也不能自行更改 root 文件的 ACL/模式；本问题不是已经证明的免认证提权。

但在 root 管理的发布树发生组合权限漂移时，恢复器原有校验确有缺口：把 DEPLOY 和 APP 改成0755，再给 server.mjs 增加三个访客的只读命名 ACL，内容/大小/哈希不变，旧 verifier 接受，wdguest/wdterm/wdgui 实际打开文件均得到 `[read=true, write=false]`。新 verifier 拒绝该状态。此为已复现的服务端源码读取边界破坏，不能以“没有写权限”当作安全。测试只报告布尔结果，不输出文件内容或凭据。

可复核例：最终代码 isolated4GiB [Artifact 12397034083](https://github.com/isAliceYoung/fa/actions/runs/39711127801/artifacts/12397034083)，`health.tar/staging-results/phase4a-artifact-audit.json`，case `composed traversal mode drift plus file ACL actually exposes protected server code before fix`。测试用 `git show e976ead...:webdesktop-deploy/scripts/isolated_deploy.py`，AST 只提取原 `verify_artifact()`，在相同 VM/文件树运行原函数，不运行旧安装/恢复流程，也不以 mock 代替内核权限。

### 2.2 新校验策略（resolved）

- DEPLOY、APP 与全部真实子目录要求 root:wdsvc0750、无访问或默认扩展 ACL；不跟随目录链接遍历。
- 清单普通文件逐个 `reject_acl()`，root:wdsvc、单硬链接，精确0640/0750（由原包可执行位及原发布规则确定），继续验证类型/大小/SHA。
- 合法清单 symlink 继续要求 root 属主、精确原目标、包内目标规则；不把 Linux symlink 的0777当作文件访问模式，不跟随它枚举第二套条目。
- 文件集合严格为 app 清单加 `.env`；目录集合从清单父路径推导。`.env` 继续由冻结配置的哈希/属主/模式/ACL核验。未知文件、目录、特殊条目或缺项全部拒绝，保留现场，绝不自动删除、重新基线化或递归接管。
- APP 是不可变发布目录；用户/数据库/Profile/临时状态在原明确数据目录。不放开 `.next`、node_modules 或整个 APP 写权限，不新增缓存例外。

没有把必要运行目录改成 root0700。实际 wdsvc 可读 server.mjs、不可写，三个访客在还原后的正常树都不能读写。公共系统文件和 `/usr/local/lib/webdesktop-*` 最小访客运行包权限不变。新校验仍是 root 维护入口的检查，不是对 root 攻陷的防篡改签名或持续监控。

### 2.3 12项真实负向/正向测试

`tests/phase4a_artifact_audit.py` 仅允许 root、wd-lab、显式 `--disposable-vm` 和原 VM 标记；不进入普通安装流程或运行包。

| 检查 | 旧实现/已有保护 | 新实现要求 |
|---|---|---|
| 未改树、wdsvc实际读、访客实际读写 | 必要正常行为 | 接受，服务可读但不能写，访客拒绝 |
| 命名文件 ACL，mode/SHA相同 | 原函数接受；单项仍受父目录保护 | 拒绝 |
| 嵌套目录命名 ACL | 原函数未检查该目录 | 拒绝 |
| 嵌套目录默认 ACL | 原函数未检查该目录 | 拒绝 |
| 额外普通文件 | 原函数忽略 | 拒绝并保留 |
| 额外空目录 | 原函数忽略 | 拒绝并保留 |
| 普通文件替换为 symlink | 原函数已拒绝 | 继续拒绝 |
| 全部31个合法 Node/Next链接 | 原规则允许 | 继续接受 |
| 内容相同但普通文件改成0644 | 原函数接受；父目录仍挡住 | 拒绝 |
| 父目录0755 + 文件命名读ACL | 原函数接受，三访客实际可读 | 拒绝 |
| `-O`、`-OO` 的未知文件拒绝 | 不应绕过关键判断 | 都退出1，不删除未知条目 |
| 所有故障清理后恢复 | 不覆盖数据/正常权限 | 接受，三访客实际读写仍被拒绝 |

另保留原22项恢复模式、优化解释器的归档负向测试与安全代码无 assert 检查。`file_record/configurations/load_state/validate_configuration/recover/reject_acl` 已逐项复核：配置文件/目录原有 ACL 检查、冻结配置集合、身份表、单元绑定、实际网络、服务启用状态和依赖拒绝逻辑继续保留；没有复制第二套恢复实现。

## 3. 4GiB CPU失败根因与修正

### 3.1 原始时间线（resolved：测试观察范围）

生产 `PressureState.observe()` 正常：5s采样；首次 desired=warning 只设置 pending/since；持续10s才升级；恢复正常需连续30s。CPU策略是 PSI some avg10≥40且CPU使用≥85%，或节流≥20%，本轮完全不改。

旧4核执行配额实际产生约86–87%总CPU使用，节流约12–13%，尚未达到独立节流20%分支；需先等PSI avg10升到40。旧22秒负载窗口在两份失败Artifact中只覆盖两次合格采样，未覆盖升级所需10秒。`cpu_wait` reason不是level已经升级的证据。

| 旧入口/Artifact/Job | 合格采样1（UTC） | 合格采样2 | 实际合格跨度 | level |
|---|---|---|---:|---|
|isolated /12393014815 /119570615932|00:47:22.557|00:47:28.325|5.768s|normal|
|staging /12393760501 /119570615964|00:49:25.067|00:49:30.071|5.004s|normal|

之后负载已清理并进入另一个故意OOM测试；不能拿后来的 critical/oom 充作CPU成功。旧原始JSON、完整采样继续在原Artifact，CPU-only固定节选在 `tests/fixtures/phase4a-cpu-baseline.json`，含完整epoch与原CPU指标/状态。

新增6项确定性模型测试，合计资源模型19项：两个原序列重放不到10s不升级；两个序列用明确模拟的后续合格样本验证10s升级/30s恢复；重复同一采样时间不按次数升级；压力中断重置计时。补造的后续模型输入明确不是实际VM观测。

### 3.2 测试夹具修正，不改变生产策略

负载、会话和CPU限额不变。从负载开始起最多 `enterMs+8*sampleMs=50s`，按不同真实采样观察到完整转换立即结束，不重复启动压力、不无限重试、不增加固定sleep。保留并加强：

1. 执行cgroup的真实 `nr_throttled` 必须增加。
2. API真实level必须warning/critical且含cpu_wait。
3. 至少3次合格不同采样且跨度≥10s。
4. 压力中继续认证、文件CRUD和真实PTY输入；保留p95<2500ms、max<5000ms。
5. 退出负载后最多 `recoverMs+12*sampleMs=90s` 观察恢复正常，并持续验证响应。
6. 原受保护服务OOM不增加、fork/磁盘/保护503/OOM回收断言全保留。

`cpuPressure.timeline` 记录原始 `/proc/pressure/cpu`、三个cgroup的cpu.stat/cpu.max、cpuset实际继承来源、systemd CPUQuota、API sampleAt/usage/throttling/PSI/level/reasons，以及对不同采样用同一生产类重放的 pending/since。重放是模型状态，未冒充直接读取生产实例内部字段。`recovery` 保留解除后的每个不同采样，`latencies` 按phase保留并发响应。全部在测试Artifact，生产接口没有新增字段。

driver总超时420→600s仅容纳有限的额外恢复观测，不改应用启动期限、GUI生命周期或资源策略。

### 3.3 本轮首批新夹具失败（保留，不混入最终结果）

代码2d3c500的 [Staging39710236480](https://github.com/isAliceYoung/fa/actions/runs/39710236480) 新诊断假设每层都有 `cpuset.cpus.effective`，Debian该子树未启用cpuset，导致ENOENT；初始化在try/finally外又残留PTY，后续fork/OOM/清理三项连带失败。4GiB staging Artifact12397445590为资源6通过/4失败；isolated2GiB Artifact12397550431为产物12/12、模式22/22、资源6通过/4失败，后续UI/健康未执行。此为新增测试夹具故障，不是CPU算法、资源策略或生产PTY故障。

5c9ceec只修夹具：从实际最近祖先记录effective cpuset及source，根级仍缺则失败；没有激活控制器、改变配额或伪造本层指标。初始化纳入PTY finally清理。两套最终CI均重新执行新完整SHA。旧2d3c500包虽曾独立核验，不作为最终交付包。

首轮Validate39710236444及同SHA重复push记录39710235529/39710235454保留，最终统计只采用下节固定工作流，不把重复执行相加或用新版结果覆写旧失败。

### 3.4 最终4GiB真实结果

实际4核，执行/PTY `cpu.max=340000 100000`，父组 `380000 100000`；cpuset从 `/sys/fs/cgroup` 继承 `0-3`，有效核数4。systemd未显式设置period时可能show infinity，实际内核100ms周期以cpu.max为证，不据show字符串误算配额。

| 最终入口/Artifact | 首次cpu_wait UTC | 第二次 | 升级warning | 合格跨度 | 总观察 | 解除至normal | 全阶段响应p95/max |
|---|---|---|---|---:|---:|---:|---|
|staging /12396559816|02:31:10.389|02:31:15.950|02:31:21.480|11.091s|26.515s|35.384s|236.7/335.1ms|
|isolated /12397034083|02:32:48.275|02:32:53.876|02:32:58.917|10.642s|20.526s|36.476s|298.8/377.5ms|

isolated这次PSI升得更快，所以总观察可少于22秒；这不推翻原失败证据，恰好说明固定短窗口受采样相位/压力爬升影响。最终isolated执行层nr_throttled增加204次，原配额真实生效。没有证据支持需要修改生产算法/阈值或资源限额。

完整新旧时间线见相应Artifact的resource-runtime.json，不只截取三条成功采样。4GiB isolated后续产物12/12、模式22/22、资源10/10、GUI5/5、终端UI4/4、重启前后健康各40/40，原每次6项not_executed保留；boot settlement为running/exit0/1658ms，管理员SSH及数据标记保留。

## 4. 最终CI、统计与资源矩阵

固定代码 `5c9ceecfb3e8b07b449ac55c9080c16f0a9b7a76`：

- [Validate39711127913](https://github.com/isAliceYoung/fa/actions/runs/39711127913)
- [Staging/isolated39711127801](https://github.com/isAliceYoung/fa/actions/runs/39711127801)

最终 [Validate39711127913](https://github.com/isAliceYoung/fa/actions/runs/39711127913) **8/8任务成功**，[Staging39711127801](https://github.com/isAliceYoung/fa/actions/runs/39711127801) **11/11任务成功**。已独立下载17份测试证据ZIP，核对API摘要和工作流head_sha，再逐份JSON汇总。不是从19个绿色任务推断功能用例数。

| 测试类别 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
|常规（215×2、资源模型19×2、准入模型17×2）|502|0|0|0|
|安装/恢复/健康/数据保留|858|0|0|108|
|权限/安全边界（原100+真实产物补审12×4）|148|0|0|0|
|PTY/GUI/Chromium（含实际终端UI）|250|0|0|5|
|IME模型与真实Chromium/xterm专项|96|0|0|0|
|真实资源限制/压力必需验收|84|0|0|6|
|**必需用例合计**|**1938**|**0**|**0**|**119**|
|既有独立GUI/Claude资源实验|6|4|0|0|
|**全部显式用例/实验合计**|**1944**|**4**|**0**|**119**|

构建/typecheck以及Bash语法、Python编译、Node helper/OSC/Origin/HTTP边界步骤均退出0，作为命令检查，不虚增用例。IME96=模型30×2+浏览器36；实际Bash输入另在PTY UI计数，不重复添加。真实Windows本机验证沿用已验收的独立成果，不冒称本轮重新人工执行；真实VPS新版IME仍未执行。

按工作流：Validate **892/0/0/0**；staging兼容矩阵 **462/0/0/71**；isolated矩阵 **584/0/0/48**。119=九组前后健康的108项原范围保留+旧包不支持的六项新资源测试+五项新网页UI；先前4GiB中断的额外缺项已经实际执行，不改写旧报告。生产、长期、未开发3C等未编写/未授权范围另外明示，119不代表项目所有未来待办的数量。常规JSON内两条范围说明是文字，不是两项跳过/新增通过用例。

### 独立实验失败，不能被工作流成功掩盖

无Swap：Chromium320/384MiB两项smoke通过，Claude320/384/448MiB三项资源/交互失败；有Swap：四项smoke通过，**Claude320MiB一项失败**。两组security_failed均0、全部production_usable=false。故本轮独立合计是**6/4/0/0**，与历史e976的7/3不同，不能沿用旧数字或声称所有显式测试零失败。

新增的有Swap失败证据为 GUI Artifact12398500226 的 `evidence.tar/wd-systemd-evidence/gui-resource-matrix.json`：Claude320MiB/MemorySwapMax192MiB/TasksMax160，pids.peak=160、pids.events max=1、thread_exhaustion=true；memory.swap.current最大201326592字节，memory.events oom/oom_kill均0，原生沙箱验证通过，服务未崩溃且退出后cgroup为空，但resource_stable_smoke=false。因此分类为**独立未开放功能的真实资源上限拒绝**，不是本轮CPU断言失败、测试夹具误报、当前Chromium功能回归或新安全边界破坏。不能因存活或有Swap就记为可用。未调高Tasks/内存/Swap，未重跑来抹平该失败，未进一步开展Claude资源研究；其正式可用性保持blocked，返回3C另行授权解决。

### 最终证据索引

|Validate39711127913证据|Artifact ID|
|---|---:|
|Ubuntu常规|12397261404|
|Debian常规及真实源码权限|12397161595|
|Linux身份/IPC/文件/WS/认证|12397366661|
|PID1/systemd安装及重启|12397481161|
|PTY/systemd/Next/终端UI|12397447164|
|IME A/B及真实Chromium/xterm|12397076902|
|GUI/Chromium/Fcitx/HTTPS与独立实验，无Swap|12397519578|
|同上，有Swap|12398500226|

安装九份证据ID见下表。所有证据保留JSON、journal/串口、systemd状态/security、boot_id/SSH与截图；可在对应run的Artifacts下载，默认90天，到期2027-01-20。部署包仅7天，见§5。机器生成汇总/摘要在忽略目录 `phase4a-r1-artifact-index.json`、`phase4a-r1-ci-summary.json`、`phase4a-r1-resource-table.json`；原始证据以CI Artifact为准，不将易过期机器结果长期纳入Git。

### 4.1 安装矩阵（已逐份核验）

Staging/isolated运行39711127801的两个构建和九个VM任务 **11/11成功**。

| VM RAM/CPU/系统盘/Swap | staging Artifact；P/F/S/N | isolated Artifact；P/F/S/N |
|---|---|---|
|1024MiB/1/10GiB/1024MiB|12397248167；95/0/0/12|12397597734；146/0/0/12|
|1024MiB/1/10GiB/0|12396629525；95/0/0/12|12397038082；146/0/0/12|
|2048MiB/2/20GiB/1024MiB|12396719189；95/0/0/12|12397087604；146/0/0/12|
|4096MiB/4/30GiB/1024MiB|12396559816；95/0/0/12|12397034083；146/0/0/12|
|固定旧f195f90应用兼容，1024MiB/1/10GiB/1024MiB|12396804565；82/0/0/23|不适用，不重复计数|

P/F/S/N分别为passed/failed/skipped/not_executed。isolated146=预检12+实际首次失败恢复1+模式22+产物补审12+资源10+GUI5+终端UI4+两次健康40×2；staging95=资源10+GUI5+健康80。旧包82=原适用资源4+健康39×2；它不具备新资源/GUI能力，六项不适用资源场景及五项新网页UI仍未执行，不伪装成当前应用通过。

所有新入口重启前后健康均40/0/0/6，bootstrap及独立四UID、DAC/IPC、loopback、PG/VNC不公网监听、Chromium原生namespace/seccomp/联网和本机管理端口拒绝、Fcitx/PCManFM/文件/Bash、Profile保留/撤销/回收见原JSON和截图。四个isolated boot settlement均running/exit0（1GiB有Swap2037ms，无Swap1514ms，2GiB1696ms，4GiB1658ms），没有用service先启动后伪称自动启动成功。其后再执行限定service恢复与健康检查。

22项模式测试保留数据库fixture行、凭据/seed/env哈希、卷UUID、访客文件、Profile标记；涵盖健康四模式PID不变、停止后恢复、缺失精确单元repair、修改/未知配置不收养、disable后service不enable、网络变化不重建、包/构建/Git获取/密钥工具拒绝包装、优化解释器拒绝与秘密文件/信号权限。仅最终start-service失败有安全恢复，早期部分安装仍blocked；不因本轮通过扩大repair能力。

每个健康报告的六项not_executed原样保留：脚本不自行证明外部管理员SSH，以及Claude、网页剪贴板、拖拽、多GUI、正式HTTPS。driver另有真实管理员SSH、四身份带有效测试公钥仍被拒绝、真实boot_id变化的原始记录，不将它们覆盖进健康JSON或重复虚增显式测试数。

### 4.2 实际资源与低配回归

下表来自四个最终isolated VM的API采样和前后内核cgroup记录；包含故意fork/内存故障，**不是持续整机安装峰值或长期业务基准**。主机名义1GiB与MemTotal约973.3MiB有差别，表中上限用实际有效容量计算。

|规格|平台/执行MemoryMax MiB|平台/执行Swap上限 MiB|平台/执行内核memory.peak MiB|采样最大平台Swap MiB / tasks|业务p95/max ms|系统盘空闲GiB /访客镜像MiB|
|---|---:|---:|---:|---:|---:|---:|
|1GiB + Swap|781/525|640/262|731.7/506.2|2.2/113|208.7/243.9|4.36/512|
|1GiB 无Swap|781/525|0/0|705.4/506.2|0/116|207.1/232.4|5.36/512|
|2GiB + Swap|1723/1275|861/637|910.1/731.5|0.2/124|217.1/231.4|13.79/1024|
|4GiB + Swap|3447/2679|1023/1023|982.2/837.9|0.5/143|298.8/377.5|23.20/2048|

主机Swap有的为1024MiB，无Swap组为0；单元仍各自有更低上限，不能说每个服务能用1GiB。CPU、内存、Swap、Tasks、FD、卷预算没有扩大。控制层本地OOM未增加；执行子树的故意OOM按原断言记录、清理和恢复，不把它算作异常控制面OOM，也不隐去事件。1GiB仍只证明已开放单应用子集的短时可用，不保证Claude、多GUI、长期缓存或任意负载。

新校验额外目录扫描和集合比较为O(清单条目数)，只在root维护校验中执行，不加入每个HTTP请求。已有哈希审计后的PSI settle要求继续保留，真正critical时GUI仍保护性503。没有用扩大内存/Swap或删保护来解决维护开销。

## 5. 最终部署包与独立校验

包名 `staging-debian12-5c9ceecfb3e8b07b449ac55c9080c16f0a9b7a76`，[Artifact12397265997](https://github.com/isAliceYoung/fa/actions/runs/39711127801/artifacts/12397265997)，运行39711127801，到期 **2026-10-29T02:26:02Z**。

**内部tar.gz SHA256：`0acbfe2664314a4565a2554fe2ebc7959e4e384cd805339de7f5317fce971a64`**，225776461字节。

外层ZIP SHA256 `0a81f21158bbf35d5eb5e6c2044ebd3a8044bfa2ed5fc0fa0728624a48b010be` 已与GitHub API独立比对；不能替代安装器要求的内部摘要。独立下载流式检查29585普通文件逐项大小/SHA、31链接精确目标、29616条目集合，确认Debian12/amd64/Node22.23.2/ABI127/glibc2.36。编译.next包含既有IME桥，app没有.env/.git/tests；原秘密扫描和平台核验保留。源码bundle仍root-only。此为供应链完整性证据，不是生产可用性声明。

本机验证记录与下载包在忽略的 `.test-results/phase4a-r1-final-runtime-verified.json` 等文件，机器生成大文件不提交Git。未来审核/安装必须使用相同代码SHA和对应包，不能把报告SHA或变化的HEAD代入。包过期后需独立重建/审查，不启用自动更新。

## 6. 全部修改与测试命令

| 文件 | 改动/权限及影响 |
|---|---|
|webdesktop-deploy/scripts/isolated_deploy.py|只改恢复前不可变APP校验；无安装/网络/UID/数据库新增副作用|
|tests/phase4a_artifact_audit.py|仅可丢弃VM的真实ACL/DAC与固定旧函数对照12项|
|tests/staging_resource_runtime.mjs|完整CPU状态转换、原始诊断、有限恢复、异常清理|
|tests/staging_vm_driver.py|调用独立补审，测试总超时600s；不改产品超时|
|tests/resource_monitor_test.mjs|新增6个确定性回归，合计19项|
|tests/fixtures/phase4a-cpu-baseline.json|两份旧4GiB失败的CPU-only固定真实节选；是测试夹具，不是新结果|
|webdesktop-deploy/PHASE4A_R1_AUDIT_REPORT.md|本报告，独立文档提交|

没有修改 src、server.mjs、resource_policy.py、生产监控/阈值/告警、稳定install.sh、bootstrap、PostgreSQL恢复模块、服务模板或访客执行器。未增加依赖、权限或限额；保留IPv4-only、Swap、原有恢复、默认loopback、关闭自动更新、欢迎/示例文件不再生成以及IME辅助模式。历史报告未改。

测试环境：Windows/GitBash本机逻辑；Ubuntu及Debian12容器常规；Ubuntu CI宿主运行真正PID1/systemd的可丢弃Debian12 QEMU VM，镜像固定20261018-2635且核对既有SHA512。身份、cgroup、网络namespace、重启、真实Chromium/Fcitx不以mock/继承FD替代。

单行命令（VM命令只供已准备依赖/固定包的可丢弃CI宿主，不是用户VPS安装指令）：

`python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json`

`node tests/resource_monitor_test.mjs`

`node tests/status_admission_test.mjs`

`node tests/terminal_ime_test.mjs`

`npm run typecheck`

`npm run build`

`python3 tests/run_systemd_vm.py --isolated --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=1024`

`python3 tests/run_systemd_vm.py --isolated --ram-mib=1024 --vcpus=1 --disk-gib=10 --swap-mib=0`

`python3 tests/run_systemd_vm.py --isolated --ram-mib=2048 --vcpus=2 --disk-gib=20 --swap-mib=1024`

`python3 tests/run_systemd_vm.py --isolated --ram-mib=4096 --vcpus=4 --disk-gib=30 --swap-mib=1024`

staging同四组改为--staging，旧f195f90兼容单独1GiB/1CPU/10GiB/1GiBSwap。VM中补审命令为 `python3 -I /root/phase4a-artifact-audit.py --disposable-vm`，模式测试为 `python3 -I /root/phase4a-modes.py --disposable-vm`，均由driver明确安装测试夹具后调用，不进入普通VPS运行包。

本机2d3c500逻辑215/0/0、资源模型19、准入17、IME30及typecheck退出0；最后提交只改诊断夹具，最终Linux同SHA重新执行，不能把本机结果充作最终VM证据。

## 7. 剩余事项与阶段门槛

| 事项 | 状态/级别 | 当前证据与后续条件 |
|---|---|---|
|产物ACL/模式/未知条目|resolved / MEDIUM（原缺口）|真实旧函数接受+三个UID可读，新函数拒绝；依赖root-only发布/校验可信，不能抵御root被攻破|
|4GiB CPU观察窗口|resolved / MEDIUM（夹具）|实际配额/PSI/持续10s/恢复时间线；不改算法/限额|
|首轮新增cpuset诊断故障|resolved / LOW（夹具）|真实ENOENT与PTY连带失败保留；记录实际祖先来源并finally清理|
|Claude正式运行/持续交互|disabled + incomplete / HIGH|既有OOM/资源研究不代表正式可用；回3C解决，不开放入口|
|旧Playwright/极速浏览器、跨环境剪贴板/图片文件复制、拖拽、多GUI、下载桥、任意应用/进程管理|disabled/incomplete / HIGH|原计划目标未删除；旧端点认证后503/旧WS拒绝，不允许wdsvc执行回退；回3C实现和真实测试|
|仓库当前Public|unresolved / HIGH（最终源码私密目标）|本轮只读API private=false；本机DAC不能阻止从公开GitHub另行复制；独立授权事项，不改可见性|
|正式HTTPS域名/代理上线|not_executed / HIGH（公网交付）|只保留模板/隔离测试，不部署域名；上线前独立验收|
|完整发布/升级/备份/旧数据迁移|incomplete / HIGH（完整Phase4）|候选入口与稳定入口保持分离；未知/早期失败保留，不是通用升级器或旧数据迁移器|
|长期运行与Profile/容量增长|not_executed / MEDIUM|短时矩阵不替代整日soak；不扩卷、不加预算|
|真实VPS新版IME|not_executed / LOW（独立人工待办）|既有本机Windows搜狗验证有效；本轮模型/Chromium/PTY回归不代表真实VPS已更新/验收|

**最终门槛结论：R1 PASS；4A已验证功能子集的完整技术矩阵PASS；完整Phase3C仍incomplete，原计划完整Phase4仍blocked。** 必需项1938/0/0/119与独立实验6/4严格分开；后者不属于当前开放子集，不据此开放Claude。4A/R1是执行管理子标签，不重写原计划阶段定义；4GiB缺项补齐只解除该子集的技术阻塞，不自动补足Claude/剪贴板/Playwright等原阶段3前置。原Phase4A报告面向完整交付的PARTIAL/BLOCKED不被倒写为历史通过。

下一步建议先独立审核本报告、固定SHA和原Artifact；若接受已验证功能子集，可另行授权其下一子阶段的恢复/发布工作，但不能宣称完整产品阶段4通过。按原全功能目标应返回3C，优先浏览器下载与Agent衔接，再独立审查剪贴板/拖拽/多应用及Claude资源可行性；具体范围需用户下一轮授权。本轮不启动这些工作，不要求先通过操作真实VPS来验证本补审。

main仍5922e1408de91e56771f1ea0bf94a9df1b7028cd；Beta标签对象76f93a1682845306877552868f8a8a77cae156a9，解引用同main。未连接真实VPS、修改生产密钥/SSH/数据库/密码/访客文件、仓库可见性或稳定部署。完成报告及开发分支推送后停止，不进入4B或继续3C。
