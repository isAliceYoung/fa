# Phase 4B-1：可信发布来源、签名清单与版本化发布基础 — 实施与验收报告

- 分支：`feature/CC`（只在此分支开发和提交）。`feature/private-deploy-isolation` 仍为 `d962567`，`main` 仍为 `5922e14`，`v0.1.0-beta.1` 未改动。
- 起点：C2 文档提交 `7a58a06`（C2 固定受测代码 `b827791`）。
- **受测固定 SHA：`57d4d7d582d3cbac2a82d2faba9b7f0c01195c1e`**（代码树 `c238f97cd9a27bad5cc62b2ab29c4ceea98c6f2e`）
- **结论：**
  - **4B-1 安全专项：PASS**（适用范围见第 11 节，4 GiB 未执行）。
  - **完整集成验收：PARTIAL**。
  - 二者分别判定，见第 14 节与第 17 节。
- 本轮没有操作任何真实 VPS、生产 SSH、Deploy Key、数据库或用户文件，也没有使用正式签名密钥。

---

## 1. 当前分支与基线

| 项 | 值 |
|---|---|
| 开发分支 | `feature/CC` |
| 本轮起点 | `7a58a06`（C2 报告） |
| 代码提交 | `1aca2e5` → `683026d` → `57d4d7d`（固定受测） |
| 报告提交 | 单独提交（`[skip ci]`），不含代码 |
| 网页更新按钮 | 仍禁用：`src/lib/deploy.ts`、`/api/deploy`、`update-panel.tsx` 本轮均未修改 |

## 2. 原有三处硬编码分支的处理

三处检查都没有删除，也没有改成“接受任意分支”，而是统一改为调用同一个显式信任决策（`webdesktop-deploy/scripts/release_trust.py`）。

| 位置 | 原逻辑 | 新逻辑 |
|---|---|---|
| `staging_bundle.py` `BRANCH` | 产物清单固定写入旧分支名；解包要求等于该常量 | 常量保留，作为旧开发来源。产物记录**真实构建分支**（`--branch`）。解包所需的分支和内层清单摘要只能来自调用方的信任决策，不读产物自己的声明 |
| `staging_install.py:22` `BRANCH` | 安装状态、bootstrap 记录、`.env` 都写固定分支 | 常量保留。安装在预检阶段、任何账号/软件包/数据变更之前调用 `resolve_install_source()`，结果写入安装状态；签名安装另存 `release_source`，以及 `release.json`/`.sig`（root 0600） |
| `layer-context.sh:24` | `r['branch']=='feature/private-deploy-isolation'` | 调用 `authorized_state()`：以**当前** root 策略重新证明已安装来源。旧分支安装仍要求分支等于旧常量，且旧来源未被停用 |

隔离恢复（`isolated_deploy.load_state`）也由“分支等于常量”改为 `authorized_state()`。签名安装的 `fixed_state()` 额外冻结 `release_source`，`configurations()` 额外冻结 `release.json`/`.sig`。旧安装的状态文件和封存记录（seal）逐字节不变。

**一处问题（已修复，`683026d`）：**
- 开机时，`webdesktop-browser-network.service` 调用的是复制到最小访客运行目录 `/usr/local/lib/webdesktop-guest/` 里的那份 `layer-context.sh`。
- 第一版按相对路径加载 `release_trust.py`，而访客目录里没有、也不应该有部署代码，导致第一轮 CI 三个 isolated 安装失败（运行 40059748173）。
- 修复：固定从 root 专属的已安装源码目录 `/opt/webdesktop/source/...` 加载，并逐级检查文件及父目录为 root 所有、不可被组或其他用户写入。新增静态测试固定该路径，并确保部署代码不进入访客运行包。

## 3. 发布来源信任模型

1. **仓库和渠道注册表写在代码里，经过审查才能改。**
   - 仓库固定为 `github.com/isAliceYoung/fa`。
   - `development-legacy` → `feature/private-deploy-isolation`：允许原有的“操作者独立 SHA256”方式，也可以使用签名清单。
   - `testing-cc` → `feature/CC`：**只**接受签名清单。
   - root 只能启用或停用这些条目、增删签名者。新增仓库、渠道或分支必须改代码。
2. **root 策略**
   - 位置：`/etc/webdesktop-release/`（root:root 0700），包含 `sources.json`（0600，规范化 JSON）和 `audit.jsonl`（0600，哈希链）。
   - 只能通过 `release.sh sources init|add-source|add-signer|remove-signer|remove-source|show` 修改，且必须是 root。
   - 每次修改都追加一条审计记录：时间、uid、sudo 用户、loginuid、动作、明细、上一条的哈希，以及**修改后策略内容的哈希**。
   - 加载时同时校验：版本号等于审计条数、`audit_head` 等于最后一条的哈希、最后一条记录的内容哈希等于当前策略内容。
   - 本地测试中，“只改策略、不写审计”的篡改最初未被发现，加入内容哈希后被拒绝。
3. **默认行为**
   - 没有策略目录时，只接受旧开发来源，并且只能用原规则，行为与 4B-1 之前完全一致。
   - 策略目录存在但不完整、权限不对、有未知文件或审计链不一致时，一律拒绝（fail closed）。
4. **其他约束**
   - 正在被已安装部署使用的来源或签名者不能删除（CLI 直接拒绝）。
   - 分支名、GitHub Actions 成功、产物自带的任何公钥或摘要文件，都**不**构成信任。

## 4. 签名清单格式与验证

**签名字节**是唯一的规范化编码：`json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=True)`，不带换行。

**验证方的解析要求：**
- 必须是 ASCII，不允许重复键，不允许浮点数、NaN 或 Infinity。
- 重新编码后必须与原字节完全相等。因此字段重排、空白或转义写法不同的文档，即使有有效签名也会被拒绝（已测试）。

字段（全部必需，不允许多余字段）：

| 字段 | 校验 |
|---|---|
| `schema`=1, `type`=`webdesktop-release` | 精确值 |
| `repository` | 必须为 `github.com/isAliceYoung/fa` |
| `channel` / `branch` | 渠道必须已注册，分支必须等于注册表；渠道还必须在 root 策略中启用 |
| `commit` | 40 位小写十六进制；必须等于安装时指定的 `--commit` |
| `artifact.{name,size,sha256}` | 文件名格式；大小必须等于实际文件；SHA256 必须等于实际文件，也必须等于独立传入的 `--artifact-sha256` |
| `unpacked_bytes` | 用于预先检查磁盘空间；解包后实际大小不得超过 |
| `inner_manifest_sha256` | tar 内 `manifest.json` 原始字节的 SHA256，在解包前校验 |
| `platform` | 必须精确等于 `debian12/amd64/Node 22.23.2/ABI 127/glibc 2.36` |
| `installer.{min,max}` | 必须包含当前安装器版本 1 |
| `signer` | 主体名必须属于该来源已登记的签名者 |
| `created` | UTC 时间格式（不参与信任判断） |

**签名方式**
- Debian 12 自带的 OpenSSH 9.2：`ssh-keygen -Y sign/verify`，Ed25519，命名空间固定为 `webdesktop-release`。
- 验证时只把**该来源下这一个已登记的公钥**写入临时 allowed_signers。要求退出码为 0，且输出精确等于 `Good "webdesktop-release" signature for <主体> with ED25519 key <登记指纹>`。
- 签名文件不超过 8 KiB，必须是标准 armor 格式。命名空间不对、换了钥匙、主体属于别的来源，都会被拒绝。

**`-O/-OO` 下仍然有效**
- 所有检查都是运行时 `require()`，没有 `assert`（AST 测试覆盖 `release_trust.py`）。
- 本地用 `-O/-OO` 子进程验证了篡改清单仍被拒绝；VM 中 `-O/-OO` 运行 `verify-prepared` 也拒绝未知文件。

## 5. 签名密钥管理与 CI 安全边界

- 仓库、运行包、VM 中都没有任何私钥；仓库里也没有任何真实发布公钥或私钥。
- **CI：**
  - 由宿主控制器（`tests/staging_vm_driver.py`）在一次性临时目录中生成三把**一次性测试密钥**：`ci`（testing-cc）、`legacy`（开发来源的第二版本 B）、`untrusted`（负向测试）。
  - 私钥只用于在宿主上签名，随临时目录销毁，**从不上传到 VM**。
  - VM 内由“测试管理员”通过 `release.sh sources init/add-source/add-signer` 显式登记公钥。VM 信任这把钥匙的唯一依据是这次 root 管理员操作，而不是产物或 CI。
- 工作流不使用任何 Actions secret，`permissions: contents: read` 不变。PR 和未受信任的分支拿不到任何正式签名能力，因为根本不存在正式密钥。
- **正式发布（未实现，需要单独授权）：**
  - 离线或受保护环境保管的 Ed25519 发布钥匙，配套独立的发布审批流程。
  - 管理员在目标机器上用 `add-signer` 登记其公钥和指纹，测试钥匙不登记。
  - 可以构建产物的工作流不得直接访问正式钥匙。

## 6. 完整 SHA 与产物验证链

```
root 策略（渠道启用 + 签名者公钥）
  → release.json 规范化 + 字段校验 + ssh-keygen -Y verify（已登记钥匙）
  → commit == --commit；artifact.sha256 == --artifact-sha256 == 实际 SHA256；size == 实际大小
  → 解包前：tar 内 manifest.json 字节 SHA256 == inner_manifest_sha256；分支/平台/提交一致
  → 原有整包预检（路径穿越、符号链接越界、硬链接/设备文件拒绝、条目集合一致、大小上限）
  → 逐文件 SHA256（解包时）→ verify_tree（属主、模式、ACL、链接、未知文件）
```

篡改测试（本地，真实签名）：
- 改了文件内容，并相应改了内层清单，外层摘要随之变化，签名清单不匹配，被拒绝。
- 签名清单写的是篡改后产物的外层摘要，但内层清单摘要仍是原值，解包前被拒绝，且不创建目录。
- `feature/CC` 产物走旧的无签名规则被拒绝；旧分支产物冒充 `feature/CC` 被拒绝；签名工具也拒绝给分支不符的产物签名。

## 7. 版本化目录与属主、权限、ACL 规则

`release.sh prepare --release-manifest … --artifact … --artifact-sha256 …`（root）：

| 路径 | 属主/模式 | 内容 |
|---|---|---|
| `/opt/webdesktop/releases/` | root:wdsvc 0750 | 只有登记为 prepared 的 40 位 SHA 目录 |
| `/opt/webdesktop/releases/<sha>/` | root:wdsvc 0750 | `app/`、`manifest.json`、`release.json`、`release.json.sig` |
| `…/<sha>/app/**` | 目录 root:wdsvc 0750；文件 root:wdsvc 0640/0750；链接 root，目标精确 | 不可变运行文件（不含 `.env`、源码） |
| `…/<sha>/*.json(.sig)` | root:wdsvc 0640 | 签名清单与内层清单原始字节 |
| `/var/lib/webdesktop-release/` | root:root 0700 | `state.json`（staging/prepared 记录）、`audit.jsonl`（哈希链）、`bundles/<sha>.bundle`（源码，0600） |

**准备流程**
1. 确认已安装部署处于 ready，并重新证明其来源。
2. 验证签名、来源和产物。
3. 获取全局锁（失败返回 BUSY）。
4. 预检：
   - 拒绝未完成的 `.stage-*`；
   - 拒绝未知名称；
   - 拒绝未登记的 40 位 SHA 目录项（包括符号链接）；
   - 目标已存在则拒绝（**从不覆盖**）；
   - `releases` 本身是符号链接则拒绝；
   - 磁盘可用空间必须不少于 `unpacked_bytes` + 768 MiB，否则 DISK_SPACE，什么都不写。
5. 在 `.stage-<sha>-*`（root 0700）中解包。
6. 设置权限并运行 `verify_tree`。
7. 以 `renameat2(RENAME_NOREPLACE)` 原子发布，然后记录为 prepared，再完整验证一次。

**中断**
- 半成品始终留在 0700 的 stage 目录中，记录为 staging，永远不会被识别为可安装版本，并会阻止下一次准备。
- 只能由管理员用 `discard-incomplete --commit <sha>` 显式删除这一个已登记的 stage。已发布的版本不能丢弃。

**识别**
- `verify-prepared` 和 `status` 只承认同时满足以下条件的目录：已登记、签名有效、来源仍被授权、内层清单摘要一致、整棵目录树逐字节一致、源码 bundle 摘要一致。
- `status` 只输出非敏感字段，`switching_supported=false`。

## 8. 不可变产物与恢复器兼容性

- 4A-R1 的整树校验逻辑原样移到 `staging_bundle.verify_tree()`。`isolated_deploy.verify_artifact()` 改为调用它（额外允许 `.env`），准备好的版本也用同一函数（不允许额外文件）。规则没有放宽。
- 4A-R1 补审的 12 项真实 ACL/DAC 测试（含历史旧函数对照）在三个配置上均为 12/0。
  - 测试工具调整：历史旧函数早于“按安装分支”的设计，所以只为其 `validate_bootstrap` 注入已安装的、已受信的分支，其余历史逻辑原样重放。
- 恢复模式 22/0 不变；新增的 4B-1 测试中，有已准备版本存在时，恢复也不受影响。
- **符号链接布局：**
  - 在 VM 中把 `/opt/webdesktop/app` 换成指向 `releases/<sha>/app` 的符号链接后，`status` 能识别为 `release-link`。
  - 但 `install-isolated.sh --mode service` **拒绝**（不支持的布局），不做任何转换；还原后服务恢复正常。
  - 本轮不实现从当前目录布局切换到版本目录布局，也不改写已安装系统的目录结构。

## 9. 旧分支与 CC 分支兼容性结果

| 场景 | 结果 |
|---|---|
| 旧分支 f195f90 产物，无策略文件，原 staging 安装器（staging-baseline 1 GiB） | 3 轮均通过，健康检查 39/0（两次） |
| `feature/CC` 产物，root 显式启用 testing-cc + 登记钥匙，isolated 安装 | 1 GiB 有/无 Swap、2 GiB 均安装成功，重启后来源重新证明通过 |
| `feature/CC` 产物，staging 安装（完整回归门禁） | 3 个配置安装成功，健康检查 40/0（两次） |
| `feature/CC` 产物不带签名清单（走旧规则） | 拒绝，未创建任何状态、账号或目录 |
| 签名损坏；同名主体但未登记的钥匙 | 拒绝，未创建任何状态、账号或目录 |
| 旧分支 f195f90 作为第二个版本 B（development-legacy，经审计登记的第二把测试钥匙） | 与版本 A 并存，各自独立验证通过 |

## 10. 安全测试证据

**本地确定性测试（`tests/test_release_trust.py`，28 项；以 root 身份同样通过）：**
- 规范化编码与严格解析；Ed25519 公钥解析。
- 策略默认值；初始化与审计；10 种策略篡改；权限、符号链接和多余文件；非 root 拒绝。
- 正在使用中的来源和签名者不能删除。
- 签名字段 9 种篡改；12 种未登记或格式错误的值（即使签名有效也拒绝）。
- 签名损坏、错误钥匙、属于其他来源的签名者、错误命名空间；字段重排后重新签名仍被拒绝。
- 产物链（外层摘要、内层清单、篡改产物）；分支不能互相冒充。
- 已安装状态重新证明；停用旧来源；输入文件权限；`-O/-OO`。
- 准备：不可变、不切换；两个版本并存；10 种篡改；未知/未登记/中断条目阻止准备；DISK_SPACE；BUSY；`releases` 是符号链接；布局识别；部署未就绪时拒绝。
- 层授权从固定的 root 源码目录加载信任模块。

**安装前拒绝（`phase4a-preflight`，每个 isolated 配置 15 项，其中 3 项为新增）：** 无签名、签名损坏、未登记钥匙，均在任何变更之前被拒绝。

**真实 VM（`tests/release_trust_vm.py`，在真实 `feature/CC` isolated 安装上运行，每个配置 19 项，另有重启后 1 项）：**

1. root 策略为 0700/0600；wdsvc、wdguest、wdterm、wdgui 实际 `open()` 读写均被拒绝，也无法在其中创建文件。
2. 四个身份运行 CLI 修改来源或读取状态均失败，策略哈希不变。
3. 已安装的 `feature/CC` 部署重新证明来源为 testing-cc / signed-manifest；层授权返回已安装提交。
4. `status` 中不含任何凭据：数据库连接串及其密码、AUTH_KEY、管理员和桌面密码、管理入口路径。
   - 测试工具修正（`57d4d7d`）：第一版把 `.env` 里的公开配置（如 `UPSTREAM_BRANCH`）也当作机密，导致误报；改为只比对真实凭据，至少 5 项，失败时只报键名。
5. 准备版本 A：通过验证且不可变；`/opt/webdesktop/app`、`.env`、凭据、`recovery.json`、数据库、访客文件和后端 PID 均不变。
6. DAC：wdsvc 可读不可写，三个访客身份不可读写，四个身份都不能在发布目录中创建文件，源码 bundle 四个身份都不可读。
7. 已存在的版本：再次准备被拒绝，目录树哈希不变。
8. 未登记钥匙、版本 B 的签名者尚未登记、被篡改的产物：均在任何写入之前被拒绝。
9. 未登记的 40 位 SHA 目录、未知名称、40 位 SHA 名称的符号链接、`releases` 本身被换成符号链接：均阻止准备，外部目录未被写入。
10. 用 fallocate 真实占满磁盘：DISK_SPACE，没有 stage 目录，也没有发布目录。
11. 在解包过程中 SIGKILL：stage 目录保留且未被识别，`verify-prepared` 拒绝，再次准备返回 STALE；安装不受影响；显式 discard 后恢复正常。
12. 并发：测量中的那次准备正常完成，另一次返回 BUSY。
13. 通过审计登记 legacy 签名者；版本 A 与 B 并存，内容不同，各自验证通过。
14. 版本 B 被篡改后拒绝且现场保留：未知文件、未知目录、命名 ACL、默认目录 ACL、权限漂移、硬链接、文件被换成符号链接、目录被替换。
15. `-O`/`-OO` 下 `verify-prepared` 仍拒绝，未知文件保留。
16. 篡改策略后，恢复器（service 模式）退出 1、层授权失败、`verify-prepared` 失败；还原后服务恢复。
17. 存在已准备版本时，恢复（service 模式）前后状态和后端 PID 均不变。
18. 符号链接布局能被识别，但恢复器拒绝，不做转换；还原后正常。
19. 最终：已安装部署、凭据、数据库、访客数据与开始时完全一致。

重启后：签名来源和两个已准备版本都重新验证通过，层授权正常。

## 11. 1 GiB、2 GiB、4 GiB 结果

`[4b1-gate]`（固定 SHA `57d4d7d`，Staging 40064428337，Validate 40064428168；均为 success）：

| 配置 | 4B-1 VM | 重启后 | 安装前拒绝 | 4A-R1 审计 | 恢复模式 | 健康检查（2 次） | 其他原有套件 |
|---|---|---|---|---|---|---|---|
| isolated 1024-1-1024 | 19/0 | 1/0 | 15/0 | 12/0 | 22/0 | 40/0、40/0 | 资源 10、staging-ui 5、terminal-ui 4、注入失败恢复 1，全过 |
| isolated 1024-1-0 | 19/0 | 1/0 | 15/0 | 12/0 | 22/0 | 40/0、40/0 | 同上，全过 |
| isolated 2048-2-1024 | 19/0 | 1/0 | 15/0 | 12/0 | 22/0 | 40/0、40/0 | 同上，全过 |
| staging-baseline 1024-1-1024（f195f90，旧规则） | — | — | — | — | — | 39/0、39/0 | 资源 4，通过 |
| **4 GiB** | **未执行** | | | | | | 完整回归因门禁失败未调度安装矩阵（第 13 节） |

准备一个版本的实测资源（版本 A / B，`/proc` 采样，含 ssh-keygen 子进程）：

| 配置 | 用时 | CPU | 峰值 RSS | 峰值 FD | 期间最低 MemAvailable | 磁盘占用 | OOM |
|---|---|---|---|---|---|---|---|
| 1 GiB 有 Swap | 32.1 s / 31.7 s | 24.3 s / 24.4 s | 87 / 86 MiB | 6 | 578 / 572 MiB | 852 / 851 MiB | 0 |
| 1 GiB 无 Swap | 31.5 s / 31.5 s | 24.6 s / 24.8 s | 92 / 92 MiB | 6 | 548 / 530 MiB | 852 / 851 MiB | 0 |
| 2 GiB | 23.9 s / 24.1 s | 21.2 s / 21.7 s | 91 / 92 MiB | 6 | 1533 / 1542 MiB | 852 / 851 MiB | 0 |

准备过程以流式方式解包，没有改动任何业务服务的内存、Swap、CPU 或任务数限制。需要注意：每个版本约 850 MiB 磁盘，这是后续保留版本数量和预检余量的设计依据。

## 12. 修改的文件与 SHA

固定受测 SHA：`57d4d7d582d3cbac2a82d2faba9b7f0c01195c1e`。相对 `7a58a06`：14 个文件，+2006/−76。

| 类别 | 文件 |
|---|---|
| 新增 | `webdesktop-deploy/scripts/release_trust.py`、`webdesktop-deploy/release.sh` |
| 修改（部署） | `staging_bundle.py`（branch、内层摘要、共享 `verify_tree`）、`staging_install.py`（来源决策、状态、`--release-manifest`）、`isolated_deploy.py`（重新证明来源、冻结签名清单、共享校验）、`layer-context.sh` |
| 测试（新增） | `tests/test_release_trust.py`、`tests/release_trust_vm.py` |
| 测试（修改） | `tests/staging_vm_driver.py`（一次性签名、策略登记、负向测试、VM 套件）、`tests/phase4a_artifact_audit.py`（历史函数分支注入）、`tests/download_bridge_driver.py`（`WD_SUITES=none`）、`tests/test_isolated_deploy.py`（无 assert 检查覆盖新模块） |
| CI | `staging.yml`（`--branch`、`[4b1-gate]`、isolated 下载 baseline 产物）、`validate.yml`（`[4b1-gate]` 跳过重型 job） |

未修改：`src/**`（含 `deploy.ts`、`/api/deploy`、`update-panel.tsx`）、`server.mjs`、资源策略、systemd/polkit 模板、稳定版 `install.sh`、`bootstrap.sh`、`private_repo.py`、访客执行器。

## 13. CI Run 与 Artifact

| 提交 | Staging | Validate | 结果 |
|---|---|---|---|
| 1aca2e5 `[4b1-gate]` | 40059748173 | 40059748191 | 3 个 isolated 安装在浏览器网络服务处失败（第 2 节缺陷）；baseline 和 Validate 通过 |
| 683026d `[4b1-gate]` | 40063116921 | 40063116881 | 签名安装、审计 12/0、恢复模式 22/0 通过；VM 套件第 4 项测试判定误报（第 10 节） |
| **57d4d7d `[4b1-gate]`** | **40064428337** | **40064428168** | **全部通过**（第 11 节） |
| **57d4d7d 完整回归（workflow_dispatch，只触发一次）** | **40066396930** | **40066400295** | 3 个门禁失败，因此**安装矩阵 9 个 job 未执行**；Validate 7/8 |

完整回归门禁明细（staging 布局、签名安装，每格为“重启前 / 重启后”）：

| 配置 | D1 | C1 | C2 | 健康检查 | 失败项 |
|---|---|---|---|---|---|
| 2048-2-1024 | 28/29、2/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | D1“actual volume space exhaustion”超时（历史：空间耗尽用例） |
| 1024-1-1024 | 29/29、2/2 | 15/15、2/2 | **12/13**、1/1 | 40/0、40/0 | C2 并发探测竞态（见下） |
| 1024-1-0 | 28/29、1/2 | 15/15、2/2 | 13/13、1/1 | 40/0、40/0 | D1 下载历史缺失（历史，重启前后各 1 项） |

Validate：`gui-systemd-debian12-swap-1024` 中 `desktop_ui_runtime.mjs:67` 书签写盘断言失败（与 C1、C2 固定 SHA 上 swap-0 失败的是同一个断言，这次出现在 swap-1024，swap-0 通过）。其余 7 个 job 通过。

Artifact：
- `isolated-install-evidence-*`：`health.tar` 内含 `release-trust.json`、`release-trust-reboot.json`、`phase4a-*.json`。
- `download-smoke-evidence-*`、`gui-desktop-one-core-swap-*`。

证据中不含私钥、凭据或 `.env` 内容。

## 14. Passed / Failed / Skipped / Not executed（固定 SHA `57d4d7d`）

**4B-1 安全专项：**

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| 本地 `test_release_trust.py` | 28 | 0 | 0 | 0 |
| 本地 `run_tests.py`（含上项） | 275 | 0 | 0 | 2 |
| VM 4B-1 套件（3 个配置 × 19） | 57 | 0 | 0 | 0 |
| VM 4B-1 重启后（3 × 1） | 3 | 0 | 0 | 0 |
| 安装前拒绝（3 × 15，含 4B-1 新增 3 × 3） | 45 | 0 | 0 | 0 |
| 4A-R1 审计（3 × 12） | 36 | 0 | 0 | 0 |
| 恢复模式（3 × 22） | 66 | 0 | 0 | 0 |
| 4 GiB 配置 | — | — | — | 全部 |

**完整集成验收（同一 SHA）：**

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| 门禁 job | 0 | 3 | 0 | 0 |
| 安装矩阵 job | 0 | 0 | 0 | 9 |
| Validate job | 7 | 1 | 0 | 0 |
| D1 重启前 / 后（3 个门禁） | 85 / 5 | 2 / 1 | 0 | 0 |
| C1 重启前 / 后 | 45 / 6 | 0 / 0 | 0 | 0 |
| C2 重启前 / 后 | 38 / 3 | 1 / 0 | 0 | 0 |
| 健康检查（门禁 3 × 2 次） | 240 | 0 | 0 | 36 |
| Validate GUI（desktop-ui swap-1024 / swap-0，gui-ui × 2） | 15 | 1 | 0 | 2 |

健康检查的 Not executed 是原有的固定未执行项；本地 `run_tests` 的 2 项是完整 VPS 安装和生产 VPS 测试（未授权）。

## 15. 新发现问题与历史问题

新发现：

1. **4B-1 自身缺陷（已修复）：** 层授权的信任模块路径（第 2 节）；策略内容未与审计绑定（本地发现，已修复）。
2. **C2 测试竞态（新观察到，未修改，记录在案）：**
   - 现象：完整回归 1 GiB 有 Swap 中 `busy_probe` 返回 `image=409, text_total=1`。
   - 原因：1000 KiB 图片正文在服务端还没读完时，测试已经每 10 ms 发起文字读取，有一次文字读取先拿到了会话剪贴板槽，于是图片按设计返回 BUSY。
   - 结论：被测规则（同一时间一个操作）是正确的；测试错误地假设图片一定先拿到槽。10 ms 间隔是 C2 阶段 `c7253e9` 引入的，4B-1 没有修改任何剪贴板代码。
   - 修复方向：让测试在确认图片已占用槽后再开始文字探测，或者两种结果都按“仅一个成功、另一个 BUSY”断言。属于 C2 测试修正，需要单独授权，本轮没有为此重跑。

历史问题（不改为通过，不删断言，不标 skipped，没有无限重跑）：D1 空间耗尽用例、D1 下载历史缺失、GUI 书签写盘断言（这次出现在 swap-1024）。这些都是 C1/C2 固定 SHA 上已经出现过的类型，本轮没有证明它们与 4B-1 无关；4B-1 没有改动下载、Chromium、Profile 或关闭顺序相关的代码。

## 16. 本轮仍未实现的升级功能

- **4B-2：** 版本化数据库迁移（仍是首次安装时 `drizzle-kit push --force`），以及兼容测试。
- **4B-3：** root 更新单元、polkit 精确放行、更新执行器 check/stage/apply/rollback、状态文件、`pg_dump` 备份、健康检查后自动回滚；从目录布局切换到版本目录布局，以及 `.env` 迁移。
- **4B-4：** `/api/deploy` 与 `update-panel.tsx` 改造、管理员再次验证密码。网页按钮**仍然禁用**。
- 正式签名钥匙与发布审批流程；版本保留和清理策略（每个版本约 850 MiB）。
- 真实 V1 → V2 升级、回滚，以及真实测试 VPS 验证。

## 17. C6 暂缓记录

已在 `PROJECT_ROADMAP_STATUS.md` 中把 C6（旧 Playwright 极速浏览器迁移）改为 **DEFERRED / 用户决定暂缓（2026-10-24）**：不删除旧代码和设计，不重启旧服务，`/api/browser/*` 保持关闭，不分配 CI 任务，不阻止 4B-1 验收，不记为 PASS。本轮没有触碰任何 C6 代码。

## 18. 是否可以进入 4B-2

**技术上可以开始，需要用户授权。**
- 4B-1 提供了 4B-2/4B-3 所需的基础：已批准来源、签名版本、固定版本目录、可信的当前版本和候选版本信息、可验证状态、审计记录。
- 建议在 4B-2 之前或同时补两件事：
  1. 一次包含 4 GiB 和完整安装矩阵的回归。本轮因门禁失败未执行。
  2. 修正第 15 节的 C2 测试竞态，避免它继续拦住安装矩阵。

## 19. 当前是否适合在真实测试 VPS 安装

**暂不建议，且当前没有授权。**
- 有利条件：`feature/CC` 已经能通过显式授权的签名来源安装（CI 一次性 VM 已证明）。
- 阻碍：
  1. 完整集成验收为 PARTIAL，1 GiB 下的历史偶发问题还没有根因；
  2. 没有正式签名钥匙和发布流程，测试钥匙不应登记到任何真实机器；
  3. 还不能原地升级，装上之后只能重装。
- 建议等 4B-3 最小可用更新完成后，再在一台测试 VPS 上安装，以便后续升级。

## 20. 是否支持原地升级；是否操作过真实生产系统

- **原地升级：不支持。** 本轮只完成候选版本的准备、识别和完整性校验。不切换 `/opt/webdesktop/app`，不迁移数据库，不重启服务，不删除旧版本。
- **真实生产系统：未操作。** 没有连接 Evoxt 或任何真实 VPS；没有改动生产 SSH、Deploy Key、数据库、用户文件、仓库可见性、`main`、Beta 或旧开发分支；没有使用正式签名私钥；旧自动更新仍然关闭。
