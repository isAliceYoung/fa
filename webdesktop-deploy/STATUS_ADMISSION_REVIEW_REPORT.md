# 第二轮审查修复：资源与 GUI 状态认证限流

开发分支 `feature/private-deploy-isolation`；起点为报告提交 `90495b19ba298400f0c4b1717da7d287188f5644`（已验收代码 `3dca35b7b6f90350484723fdbe3604dac0b7a5de`）。本次最终固定代码为 **`131ea566859d837473383425eac2b30bdb105282`**。本报告最后收尾提交只包含文档；可安装产物始终对应上述代码 SHA。

本轮仅处理两个状态接口的认证成本和查询预算隔离，不扩大 GUI 功能或部署范围。未连接用户真实 VPS。

## 1. 原问题及复现

原 `/api/resources` 在 `denyDesktop` 前增加全局每秒 12 次/并发 4 的计数；`/api/gui/status?sid=...` 在身份验证前使用同类共享计数。连续发送至少 12 次无 Cookie 或错误页面 Session 请求，紧接着的有效桌面请求可能得到 429。攻击者不能取得数据，但可抢占正常用户的状态查询预算。

新增真实 Next.js/PostgreSQL 测试对每个接口连续发送 36 个缺失凭据/错误页面请求，然后立即查询有效身份；另用两个并发线程持续发送随机伪造的完整身份，合法用户同时查询两个接口。这些测试覆盖共享预算竞争，不只检查孤立请求的状态码。

## 2. 实现和边界

`src/lib/status-admission.mjs` 使用同一进程 `globalThis[Symbol.for(...)]` 实例，让公共网关和编译后的 Next 路由共享有界状态。

| 层次 | 已由服务端记住的当前身份 | 未知、伪造或旧身份 | 作用 |
|---|---|---|---|
| 公共状态入口 | 并发 4、每秒 24 次 | 并发 2、每秒 8 次 | 在转发到 Next 前限制连接和请求成本；两个状态接口共用此层 |
| 实际身份校验 | 并发 2、每秒 24 次 | 并发 2、每秒 8 次 | 在 Next 内约束真实数据库校验；两个状态接口共用此层 |
| 完整认证后的查询 | resources、GUI 各自并发 4、每秒 12 次 | 认证失败不能进入 | 保留两个接口原查询预算，互不挤占 |

缺失 Cookie、缺失页面 Session、长度/格式错误、Cookie 载荷与页面 Session 不匹配，在有界字符串解析后直接 401，不发起数据库查询。载荷形状正确不表示身份可信：伪造签名、旧登录等仍走未知身份容量并执行原完整校验。限流 429；校验异常、截止时间或服务不可用 503。监测采样自身不可用也继续返回既有 503。

服务端仅在签发真实登录凭据或完整认证成功后，记住 **准确 Cookie token + 页面 Session 的 SHA256 摘要**；不凭客户端 sid、单独 Session 或客户端来源标头授予优先容量。摘要只是认证入口的容量路由提示，不能读取数据、跳过签名或数据库检查。新设备成功登录立即替换提示；晚到的旧身份验证不能覆盖新的已签发身份提示。401 仅清除与该失败身份相符的提示，不会清除另一新登录的提示。撤销后即使提示尚在，每次查询仍会读取当前数据库登录状态并拒绝旧身份。

只有一个摘要槽位，最多保留 5 分钟且不超过 token 到期；没有 Map/LRU、身份队列或按随机标识累积的状态。六个固定计数桶、最多四个执行中的认证工作及其计时器构成常量空间。并发释放幂等，工作请求在 finally 释放。每秒固定窗口允许边界短时突发，持续速率和实际数据库并发仍有上限。

认证响应截止 1500ms；超时或请求取消不提前释放仍在执行的真实数据库操作所占槽位，必须等实际 Promise 结束才释放。否则攻击者可重复取消绕过并发限制。超时后仍阻塞的数据库工作被标记为不健康，容量满时返回 503；恢复后解除标记。不会自动重试或积累无限队列。

GUI 进度查询通过固定私有 Unix HTTP `/api/guest/status-auth` 验证，实际校验限额在 Next 内执行。公共网关移除外部 `x-webdesktop-*` 标头，只有直接连接受 DAC 保护 Socket 的后端可添加固定内部标记；公开请求伪造标记仍被拒绝。GUI 状态读取继续校验对应的 Cookie、页面 Session 和 GUI sid。

不读取或信任 X-Forwarded-For、CF-Connecting-IP 等来源声明；既有 Host/Origin、私有 Socket、Cookie + 页面 Session 协议保持不变。无响应或日志记录原始凭据。

适用边界：服务重启后摘要为空；现有桌面启动前的 `/api/auth/session` 和正常认证心跳会重新建立提示。真实测试覆盖重启后在未知身份洪泛期间通过正常页面启动认证恢复优先容量。没有先完成页面启动认证的裸 API 客户端第一次请求仍可能遇到未知入口 429，需要重试。本轮不重构其他登录接口，也不宣称能消除 TCP/HTTP 带宽、事件循环或所有接口层面的拒绝服务；保障是在服务器合理容量内隔离这两个状态接口的预算。

## 3. 修改文件

| 文件 | 修改 |
|---|---|
| `src/lib/status-admission.mjs`、`src/lib/status-admission.d.mts` | 固定容量准入、真实认证计数、摘要提示、取消/超时、私有 GUI 认证客户端与类型 |
| `src/lib/auth.ts` | 在原成功登录/完整验证后观察摘要；原认证条件和数据库检查不变 |
| `src/app/api/resources/route.ts` | 分离认证准入与完整认证后的资源查询预算 |
| `src/app/api/guest/status-auth/route.ts` | 固定私有 GUI 状态身份校验入口 |
| `server.mjs` | 两接口公共准入、GUI 私有认证转发；移除原全局进度计数 |
| `webdesktop-deploy/scripts/staging_bundle.py` | 将新增独立运行模块纳入固定 SHA 产物 |
| `tests/status_admission_test.mjs` | 17 项有界状态、并发、超时、撤销和正常轮询测试 |
| `tests/linux_status_runtime.py` | 8 项真实 Next.js/PostgreSQL 洪泛、取消、锁等待、撤销和恢复测试 |
| `tests/staging_resource_runtime.mjs` | Chromium 冷启动期间同时洪泛错误身份并验证真实 GUI 阶段查询 |
| `tests/staging_ui_runtime.mjs` | 独立匿名客户端洪泛期间核对真实桌面每 5 秒采样；异地登录后检查旧 GUI 状态 401 |
| `.github/workflows/validate.yml` | Ubuntu/Debian 单元测试、真实状态认证测试、JSON Artifact 和 VM 后端模块打包 |
| `webdesktop-deploy/STATUS_ADMISSION_REVIEW_REPORT.md` | 本报告 |

共 12 个代码/测试/CI 文件及本报告。未修改原报告或既有失败证据。

- `611acca0b417fb502ea695c9c6cb67372b2773af`：最小实现和针对性/真实 Linux/网页回归。
- `131ea566859d837473383425eac2b30bdb105282`：只调整网页监测的观察窗口并保留样本时间证据；产品运行代码与前提交相同，但仍为本完整 SHA 重建产物并重新运行全部 CI。

## 4. 测试入口与新增用例

以下均为单行完整命令。Linux 用例需先按 workflow 准备可丢弃 Debian 环境、测试身份和数据库，不能在用户 VPS 上直接执行夹具。

```bash
node tests/status_admission_test.mjs
node tests/resource_monitor_test.mjs
python3 tests/run_tests.py --json /tmp/webdesktop-test-results.json
python3 tests/linux_status_runtime.py
npm run typecheck
npm run build
python3 tests/run_systemd_vm.py --pty
python3 tests/run_systemd_vm.py --desktop --swap-mib=0
python3 tests/run_systemd_vm.py --desktop --swap-mib=1024
```

17 项单元测试：原共享配额复现与一万次缺失凭据、未知入口并发/速率隔离、缺失或不匹配的双凭据零校验成本、伪造完整身份无优先容量、失败身份不计查询预算、记住身份仍逐次完整校验、新登录替换及旧拒绝隔离、晚到旧成功不覆盖新登录、实际认证并发保留、取消保留实际校验槽位、预先取消零校验、异常释放、查询并发及幂等释放、模拟 65 秒五秒监测/一秒 GUI 正常轮询、提示过期与恢复、两万个随机身份常量状态且无原始凭据、超时保持并发界限直到真实完成。

8 项真实 Linux 用例：

1. 正确 Cookie + 当前页面 Session；只有 Cookie、只有 Session、错误 Cookie/Session 均拒绝。
2. 两接口各 36 次缺失/错误页面请求后合法查询不出现共享预算 429。
3. 两线程随机完整伪造身份洪泛受到 429，合法查询持续正常。
4. 轮换伪造代理标头不能增加容量，伪造私有标记不能使用内部认证入口。
5. 新设备登录后旧身份被拒绝，旧请求不能抢占新用户的查询预算。
6. PostgreSQL 对 auth_config 加真实独占锁，四个认证查询阻塞；客户端断开并跨过旧 HTTP 超时继续攻击，pg_stat_activity 中实际锁等待认证查询始终不超过四个；解锁后恢复。
7. 真实 PostgreSQL 停止时 503，重新启动后恢复。
8. 真实 Next 进程重启，随机身份洪泛期间经既有页面启动认证重建优先容量。

容器没有 systemd 资源策略时，合法 `/api/resources` 返回原有 `available:false` 的采样 503，并非认证成功被误判为认证故障；测试严格检查响应结构。完整 staging VM 则要求真实资源 200、GUI 阶段及浏览器页面行为。

本地已完成：Windows/Git Bash 常规 190/0/0（通过/失败/跳过）、新单元 17/0/0、原聚合监测 13/0/0、类型检查与语法检查退出 0。常规报告另列整机安装/GUI 与未授权生产测试两类未执行，前者由 CI 独立 VM 任务覆盖；后者本轮始终未执行。

## 5. 最终 CI 证据

[Validate deployment](https://github.com/isAliceYoung/fa/actions/runs/39587833407)、[Staging Debian 12 artifact and installation](https://github.com/isAliceYoung/fa/actions/runs/39587833513)，固定代码 SHA 均为 `131ea566859d837473383425eac2b30bdb105282`。

最终两个 workflow 共 **15 个任务全部 success，退出码 0**；以下按有明确用例计数的测试组统计，不把 workflow 成功等同于独立资源实验成功。

| Validate 分组 | 通过 | 失败 | 跳过 | 未执行 | 退出码 |
|---|---:|---:|---:|---|---:|
| Ubuntu / Debian 常规 Python | 各 190 | 0 | 0 | 各报告另列整机/生产两类；整机由独立 VM 覆盖、生产未授权 | 0 |
| 原 Node 资源/尺寸 / 新状态准入 | 每环境 13 / 17 | 0 | 0 | 0 | 0 |
| Debian 源码 DAC | 9 | 0 | 0 | 0 | 0 |
| 真实 guest IPC/UID/认证/数据库 | 18 | 0 | 0 | 0 | 0 |
| SSH Include/Match 解析 | 3 | 0 | 0 | 0 | 0 |
| Next 真实 WS Upgrade/生命周期 | 12 | 0 | 0 | 0 | 0 |
| 文件流/事务/恢复/ZIP/容量 | 27 | 0 | 0 | 0 | 0 |
| 终端 UID 攻击 Agent/事务/内部 Next | 5 | 0 | 0 | 0 | 0 |
| 新真实状态认证隔离/数据库故障 | 8 | 0 | 0 | 0 | 0 |
| 独立 systemd VM prepare / reboot | 各 9 | 0 | 0 | 0 | 0 |
| PTY VM 系统权限 prepare / reboot | 各 9 | 0 | 0 | 0 | 0 |
| PTY 实际 WS/cgroup/撤销/故障 prepare / reboot | 各 14 | 0 | 0 | 0 | 0 |
| Chrome 真实终端 UI/刷新/接管 | 3 | 0 | 0 | 0 | 0 |

原有 Shell 语法、三个 Node 安全检查程序、Python 编译、Next 构建和 typecheck 退出 0；没有把未单独统计的内部断言伪造成用例数量。新真实状态测试 JSON 位于同一 run 的 Artifact `guest-3a-linux-results`（ID `12339793041`）中 `webdesktop-status-auth.json`；另有逐项日志。


最终 staging 的 7 个任务全部 success、退出 0。四种现代配置的压力/并发各 10 通过，网页各 5 通过，安装后/重启后健康各 39 通过，均 0 失败/0 跳过。每次健康报告另有 6 项未执行。

| 最终 staging 证据 Artifact | ID | 压力（通过/失败/跳过/未执行） | 网页 | 健康 / 重启后健康 |
|---|---:|---|---|---|
| 1024-1-1024 | 12340659057 | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 1024-1-0 | 12339993946 | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 2048-2-1024 | 12340678878 | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 4096-4-1024 | 12339784006 | 10/0/0/0 | 5/0/0/0 | 各 39/0/0/6 |
| 1024-1-1024-baseline（固定 f195f90） | 12340988088 | 4/0/0/6 | 新版 UI 5 项未执行 | 各 39/0/0/6 |

现代矩阵共 372 通过/0 失败/0 跳过/48 未执行；旧版对照 82/0/0/23，合计 454/0/0/71。健康未执行 6 项为管理员外部 SSH 往返（VM driver 另测）、Claude 正式入口、网页剪贴板、拖拽、多 GUI 共存和正式 HTTPS 域名。原始状态保持未执行；driver 的独立 SSH 和重启检查不将它们改写为通过。基线缺少新接口/压力机制和现代 UI，不虚记通过。

新增真实网页/冷启动证据如下；各规格网页均记录三次合法资源 200、约 5 秒一次，有效新样本和撤销后的旧 GUI 401。冷启动同时运行真实 PTY/文件 CRUD/认证和 GUI 进度查询；错误页面洪泛全部拒绝，合法 GUI 阶段查询均为 200。

| 配置 | 16 秒网页匿名请求拒绝数 | 冷启动错误页面拒绝数 | 冷启动观察到的 GUI 阶段举例 |
|---|---:|---:|---|
| 1GiB + Swap | 306/306 | 198/198 | resource_check、x11_ready、browser_spawned、websocket_ready |
| 1GiB 无 Swap | 304/304 | 204/204 | resource_check、gui_worker、browser_socket、browser_spawned、websocket_ready |
| 2GiB | 304/304 | 146/146 | resource_check、gui_socket、browser_spawned、websocket_ready |
| 4GiB | 304/304 | 120/120 | resource_check、gui_socket、browser_spawned、websocket_ready |

其中匿名无 Cookie/错误页面请求走低成本 401，不占用认证或成功查询桶；随机完整伪造身份的 429 和数据库并发界限由真实 Linux 8 项测试另行覆盖，不把仅有 401 的网页洪泛声称成所有伪造场景已证明。

最终参考 GUI 的无 Swap Artifact `12341516326`、有 Swap Artifact `12341776905` 均成功完成。每组真实断言如下（两环境分别执行，不以一组代替另一组）：

| 每个参考 GUI VM 的断言组 | 通过 | 失败 | 跳过 | 未执行 |
|---|---:|---:|---:|---:|
| systemd/权限 prepare / reboot | 各 9 | 0 | 0 | 0 |
| PTY prepare | 14 | 0 | 0 | 0 |
| GUI prepare / reboot | 各 10 | 0 | 0 | 0 |
| OSC 确认、真实 noVNC/PCManFM、共享目录、异地接管 UI | 4 | 0 | 0 | 0 |
| Chromium 原生沙箱/网络/DAC/故障/背压撤销/GTK/Qt/XIM | 11 | 0 | 0 | 0 |
| HTTPS/WSS、中文输入、profile、重开/刷新、撤销 | 5 | 0 | 0 | 0 |

最终 Validate 有明确计数的必需测试执行合计 733 通过/0 失败/0 跳过；与 staging 合计 **1187 通过/0 失败/0 跳过/71 未执行**。此总数不包含未单独计数的构建/语法/SSH driver 断言，不把常规报告中“整机/生产”两类环境说明重复加成具体用例；生产测试本轮始终未执行。

既有参考资源实验按原流程另行收集，最终结果不混入上述通过数：

| 最终资源实验 | smoke-only 通过 | 失败 | 跳过 | 未执行 | 结果 |
|---|---:|---:|---:|---:|---|
| 无 Swap，Chromium320/384、Claude320/384/448 | 2 | 3 | 0 | 0 | Claude 三档均为真实 Result=oom-kill/ExecMainStatus=9 |
| 有 Swap，同五档 | 5 | 0 | 0 | 0 | 均仅 passed-smoke-only，不能代替正式持续交互验收 |

这 10 项合计 7 smoke-only 通过/3 失败/0 跳过/0 未执行，所有 production_usable=false；没有因实验失败扩大预算、改断言或恢复 Claude 入口。最终有 Swap 实验与首轮及上一轮结果不同，不覆盖历史失败；完整原始采样、OOM、退出及原生沙箱证据在各自 Artifact 的 evidence.tar 中。

### 首轮新增网页观察器失败保留

`611acca` 的 [staging run 39586301394](https://github.com/isAliceYoung/fa/actions/runs/39586301394) 为 4 个任务成功、3 个失败。四组现代压力/并发测试均 10/0/0/0；1GiB+Swap 网页 5/0/0/0、前后健康各 39/0/0/6。1GiB 无 Swap、2GiB 和 4GiB 各网页 4 通过/1 失败，其余未执行为 0；它们在网页阶段停止，后续前后健康计划共 90 项/组未执行，不能沿用其他 run 的结果。

失败断言均为新观察器 `new Set(polls.map(p=>p.at)).size>=2`，此前已检查所有响应 200；3 组旧登录 GUI 状态均已实测 401。原采样器每 5 秒定时启动，但缓存有效期从异步读取完成后开始，两个定时源可能错相，11 秒内观察到的两次网页响应不一定跨越缓存样本。因此改为固定观察三个网页周期（16 秒），继续要求至少三次真实 200、有效新样本、相邻响应不超过 8 秒，并在断言前保存 received/at/available 证据；不改采样、缓存、告警或权限，不增加应用启动重试。

最终 2GiB 实测佐证该观察问题：三次响应间隔为 4997/4999ms，全部 200/available=true；前两次样本时间相同（1792580522326），第三次变为 1792580527397。同一窗口 304 个匿名请求被拒绝、旧 GUI 身份返回 401。不能把前两份相同样本误报为查询限流失效；网页请求仍按 5 秒执行，本轮没有改变底层采样时序。

对应失败 Artifact：1GiB 无 Swap `12340766537`、2GiB `12340426734`、4GiB `12339562295`，均保留 staging-ui.json、截图、journal 和已完成压力测试。首轮按既定 staging 测试计划合计 217 通过/3 失败/0 跳过/305 未执行（含旧版对照 82 通过/23 未执行；未执行不算通过）。这些属于新增测试观察器的失败，与既有资源实验另行记录。

同提交首轮 Validate run `39586301462` 的 8 个任务全部成功；其独立参考资源实验仍仅标记 collected：无 Swap Artifact `12339579335` 为 2 项 smoke-only 通过/3 项失败；有 Swap Artifact `12339694926` 为 2/3，均 0 跳过/0 未执行。无 Swap Claude 三档 OOM；有 Swap 两档 Chromium 缺少完整 native_verified 证据，Claude320 仍为 failed-resource-or-interaction（其 systemd Result=success，不能擅称 OOM），Claude384/448 仅 smoke-only。6 项实验失败均保留，未为它们改动测试、沙箱或预算。

上一轮已审查报告的 4 项实验失败继续原样保留在 `RESOURCE_GUI_FIX_REPORT.md`：无 Swap Claude 三档 OOM、有 Swap Chromium384 原生证据未完整。旧报告与旧 Artifact 不覆盖，不因新一轮结果不同而删除或反改。实验的 security_failed=0、unit Result=success 或 workflow success 均不能替代每项资源/交互通过；所有实验仍 production_usable=false。

## 6. 固定 Debian 12 产物

- Run：`39587833513`；Artifact：`staging-debian12-131ea566859d837473383425eac2b30bdb105282`；ID：`12339742975`。
- 到期时间：`2026-10-28T10:55:21Z`（UTC）。Artifact 保留 7 天，不等于长期 Release。
- 内部 `staging-runtime.tar.gz`：225,665,771 字节；SHA256：**`ed7c35230a49fc03a0b639bc1fac8c8a7063289ec93f9519a21fd6ec447b5f6e`**。
- 外层 GitHub ZIP SHA256：`e5f1aec02e2e8bf57396edfedc5f4d31ad0b1321dea943ff9b5882d3a343581c`，与内部 tar.gz 不同，不可混用。
- 独立下载后重新计算两个摘要；逐项核对 manifest 的 29,585 个文件哈希/大小及 31 个符号链接目标，29,616/29,616 一致。manifest 固定 Debian12 amd64、glibc2.36、Node22.23.2、ABI127、完整 commit SHA；新增准入模块已打包；未打包 `.env`、部署私钥或 `.git` 目录。CI 原有打包器继续扫描构建输出以拒绝测试凭据。

未来审核通过且用户另行授权全新 VPS 安装时，使用本内部摘要和固定 SHA；本轮不提供或执行真实 VPS 更新、重装、重启命令。Artifact 到期后须从受信任 CI 重建并重新校验，不能默认沿用相同字节摘要。

## 7. 范围和保留项

未修改 resource_policy.py、resource-monitor.mjs 的指标/压力阈值/告警算法、GUI gateway/运行层、Chromium 启动期限和生命周期、Xvnc/noVNC 尺寸、原生新标签页和搜索配置、Linux UID/DAC/systemd/network 隔离。认证 token 内容、Cookie 和页面 Session 协议未改变，未缓存授权结果。新限制只针对状态认证准入和查询预算。

原 IPv4-only、Swap 配置、PostgreSQL 恢复、自动更新禁用、取消欢迎文件/示例图片保持不变。Claude 正式入口、剪贴板、拖拽、多 GUI 共存仍未开放。本轮未进行服务器部署，不修改现有数据库、文件、生产 Deploy Key、仓库可见性、main 或 Beta。

收尾核对远端 main 和 Beta 解引用均保持 `5922e1408de91e56771f1ea0bf94a9df1b7028cd`；Beta 标签对象保持 `76f93a1682845306877552868f8a8a77cae156a9`。仅推送开发分支。

完成本轮代码、CI 和报告后停止，等待独立审核。
