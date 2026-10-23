# Phase 4B：管理员手动更新（设计，不实现）

**状态：设计。** 本文件只给出方案、接口契约、信任边界和测试计划。本轮没有实现任何特权更新动作：`src/lib/deploy.ts` 仍是禁用空实现，`/api/deploy` 仍只返回“自动更新已禁用”，没有新增 root 执行接口，也没有启用旧的 `git pull + npm install + 原地覆盖 .next` 流程。

目标：在干净 Debian 12 VPS 上完成首次隔离安装后，管理员可以在 WebDesktop 网页内把程序更新到一个**已经验证的固定版本**，并在规定范围内回滚；数据库、用户文件、Chromium Profile、输入法配置、服务密钥、隔离身份和网络策略全部保留，不需要重装 Debian 或 VPS。第一版只做**手动更新**；后台自动检查、定时安装、无人值守升级不在本设计范围内，需要单独授权。

---

## 1. 现状与约束（来自现有代码）

| 事实 | 位置 | 对设计的影响 |
|---|---|---|
| 运行代码在单一目录 `/opt/webdesktop/app`（root:wdsvc 0750，不可变发布目录），`.env` 也在其中 | `staging_install.py`、`isolated_deploy.py` `verify_artifact()` | 需要引入版本化发布目录；`.env` 必须随切换原样保留 |
| 首次安装通过 `drizzle-kit push --force` 建 schema | `staging_install.py:372` | **不能用于升级**（可能删除列或表）；必须改为版本化、只向前的迁移 |
| 发布来源写死 `BRANCH='feature/private-deploy-isolation'` | `staging_bundle.py:17`、`staging_install.py:22`、`layer-context.sh:24` | 不能删除校验；改为显式、可审计的允许来源清单（§3） |
| 访客层运行包从源码安装到 `/usr/local/lib/webdesktop-{guest,gui,...}` | `setup-*-layer.sh` | 升级也要以清单方式替换这些文件，并能回滚 |
| `recovery.json` 封存非敏感文件哈希、身份、单元和网络摘要 | `isolated_deploy.py` `seal()` | 每次发布后必须以新版本重新封存，回滚时恢复旧的封存 |
| 管理员认证独立于桌面密码（`admin_users`、`wd_admin` Cookie、`X-Admin-Session`） | `src/lib/auth.ts` `requireAdmin` | 更新只对管理员开放，且需要再次输入密码（§2） |
| wdsvc 没有 Deploy Key、root shell、sudo 或安装目录写权限 | ISOLATION_PLAN §3、PRODUCT_ARCHITECTURE | 不能改变；特权动作由 root 管理的固定单元执行（§4） |

## 2. 管理员入口

- 桌面密码**永远不能**授予更新权限。只有通过 `requireAdmin` 的后台管理员会话可以查看状态、检查更新、下载或安装版本。
- **安装和回滚需要再次验证：** 管理员在确认对话框中重新输入后台密码，网关以现有的管理员密码校验逻辑验证，有效期 5 分钟，一次性使用，不写入任何存储。
- **CSRF：** 只接受 `POST` + JSON；校验 Origin 必须属于 `WD_PUBLIC_ORIGINS`；继续使用现有 `X-Admin-Session` 头。
- **审计：** 每个请求只写入 `{时间, 管理员 ID, 动作, 目标 SHA, 结果码}`，不记录密码、Cookie、令牌或下载地址中的凭据。
- **频率限制：** 检查每分钟最多 6 次；下载和安装同一时间只允许一个，且需排队。

## 3. 可信发布来源

### 3.1 发布清单（Release Manifest）

每个可安装版本由 CI 构建，并附带一份**签名的发布清单**：

```json
{
  "schema": 1,
  "repository": "github.com/isAliceYoung/fa",
  "channel": "testing",
  "branch": "feature/CC",
  "commit": "<40 位完整 SHA>",
  "artifact": { "name": "staging-runtime.tar.gz", "size": 225979543, "sha256": "<64 hex>" },
  "manifest_sha256": "<内部 manifest.json 的 sha256>",
  "platform": { "os": "debian12", "arch": "amd64", "node": "22.23.2", "abi": "127", "glibc": "2.36" },
  "installer": { "min": 2, "max": 2 },
  "migrations": { "from": ["0007"], "to": "0009", "reversible_to": "0007" },
  "units_changed": false,
  "layers_changed": ["gui", "guest"],
  "ci": { "run_id": 0, "validate_run_id": 0 },
  "created": "2026-10-23T00:00:00Z"
}
```

- **签名：** 使用离线保管的 Ed25519 发布密钥，以 OpenSSH 自带的 `ssh-keygen -Y sign/verify`（Debian 12 OpenSSH 9.2 已具备，无需新依赖）签名，命名空间固定为 `webdesktop-release`。
- **信任根：** VPS 上的信任根是 root 所有的 `/etc/webdesktop-isolated/release-signers`（allowed_signers 格式，root:root 0600），首次安装时由管理员写入。
- **校验链：** 签名 → 清单字段 → artifact 的大小和 SHA256 → 解包前对 tar 内 `manifest.json` 的哈希 → 现有 `verify_artifact()` 的逐文件校验（类型、属主、模式、ACL、大小、SHA）。任何一步失败都停止，不会部分安装。
- **传输不可信：** 下载链路（HTTPS、GitHub Releases、对象存储）只负责搬运字节，信任只来自签名和哈希。

### 3.2 允许发布来源清单（替代写死的分支常量）

root 所有的 `/etc/webdesktop-isolated/release-sources.json`（0600）：

```json
{ "schema": 1, "sources": [
  { "repository": "github.com/isAliceYoung/fa", "channel": "testing", "branches": ["feature/CC"], "signers": ["release-2026"] }
], "history": [ { "at": "...", "by": "root", "change": "add feature/CC to testing" } ] }
```

- 安装器和更新器都改为校验 `manifest.branch` / `channel` 是否属于该清单，**不再**使用代码里的常量；未登记的来源一律拒绝。
- **修改清单：** 只能由管理员通过 SSH 执行固定命令 `webdesktop-update sources add|remove`，每次修改追加到 `history`。网页不能修改发布来源。
- **兼容旧安装：** 已有安装在首次运行新安装器时，自动生成一个只包含 `feature/private-deploy-isolation` 的清单，行为保持不变；要加入 `feature/CC`，必须由管理员显式执行一次。
- **测试夹具：** `staging_vm_driver.py:60` 中的 provenance 同步改为从清单读取。

### 3.3 版本选择

网页只能从**已签名清单列表**中选择一个完整 40 位 SHA。不接受任意 Git URL、分支名、标签、文件路径或下载地址。“可安装版本”由 root 从固定渠道地址（root 配置，网页不可见也不可改）获取并验签后提供给网页。

## 4. 最小更新权限：固定的 root 单元

```
浏览器(管理员) → wdsvc /api/deploy → 只读状态文件 + 启动固定 systemd 实例
                                      ↓ (polkit 精确放行)
                  root: webdesktop-update-<动作>@<40hex>.service → /usr/local/lib/webdesktop-update/update.py
```

- **polkit 规则：** 只允许 `wdsvc` 对以下单元执行 `start`：
  - `webdesktop-update-check.service`（无参数）
  - `webdesktop-update-stage@<40 位小写 hex>.service`
  - `webdesktop-update-apply@<40 位小写 hex>.service`
  - `webdesktop-update-rollback@<40 位小写 hex>.service`

  正则全匹配；其他单元、`stop`/`restart`/`enable`/`mask` 等动作一律拒绝。这沿用项目已有的“polkit 只放行固定单元”做法。
- **实例参数：** 实例名是唯一参数。`update.py` 再次校验它是 40 位小写 hex，并且出现在已验签的可安装列表中；其余所有输入（渠道地址、签名者、路径、单元名、UID）都来自 root 配置文件。
- **执行程序：** `update.py` 是 root:root 0644 的固定 Python 标准库脚本，运行在 `NoNewPrivileges`、`ProtectHome=read-only`（例外是不触碰的访客卷）、`PrivateTmp`、限定 `ReadWritePaths` 的单元中，不读取 `/home/wdguest`、Profile 或数据库凭据以外的任何东西。
- **单实例：** 全局锁文件保证同一时间只运行一个更新动作，第二个动作直接失败并返回 `BUSY`。
- **wdsvc 的权限：** 只能读取 `/run/webdesktop-update/status.json`（root:wdsvc 0640，只含非敏感字段），以及启动上述实例。wdsvc 没有 Deploy Key、令牌或 root shell，也不能写安装目录。
- **下载凭据：** 如果渠道需要（例如私有 GitHub Releases 的只读细粒度令牌），保存在 `/etc/webdesktop-isolated/release-channel.json`（root 0600），绝不交给 wdsvc、构建账号或访客。

## 5. 不重装 VPS：保留什么

| 数据 | 位置 | 更新时的处理 |
|---|---|---|
| PostgreSQL 数据 | 数据库 | 只执行已登记的向前迁移；迁移前强制 `pg_dump` 备份（§6.4） |
| 网页认证配置、会话版本、密码哈希 | 数据库 `auth_config` 等 | 不修改；登录状态可能因后端重启而需要重新连接，但不会被重置 |
| 服务密钥 `.env`（AUTH_KEY、DATABASE_URL 等） | 当前在 app 目录内 | 逐字节复制到新版本目录，并保持相同属主、模式和记录的哈希；不重新生成 |
| 用户文件、回收站 | `/home/wdguest`（512 MiB 访客卷） | 不触碰，不格式化，不 chown |
| 云盘数据 | 数据库 | 同 PostgreSQL |
| Chromium Profile 与书签、下载记录 | 访客卷内 GUI 私有目录 | 不触碰；切换前按 D1-R2 的正常关闭顺序结束浏览器，保证写盘 |
| Fcitx5 用户配置 | 访客 HOME | 不触碰 |
| 隔离身份（UID/GID）、systemd 单元、nftables/veth/namespace、Swap、SSH 拒绝规则 | 系统 | 网页更新**不修改**。发布清单 `units_changed=true` 或需要改动身份/网络的版本，网页只显示“需要管理员通过 SSH 执行维护升级”，不提供安装按钮 |

## 6. 原子发布与回滚

### 6.1 目录布局（新增）

```
/opt/webdesktop/releases/<40hex>/app/        root:wdsvc 0750，不可变，对应一个已验证版本
/opt/webdesktop/releases/<40hex>/layers/     该版本的 guest/gui/pty/browser 运行包副本
/opt/webdesktop/app -> releases/<40hex>/app  root 所有的符号链接，用 rename(2) 原子替换
/var/lib/webdesktop-update/                  root 0700：状态、日志、下载缓存、备份
```

- `verify_artifact()` 和恢复器改为“解析 `/opt/webdesktop/app` 恰好一层 root 所有的符号链接，目标必须位于 `releases/` 下并且是 40 位 hex 名称”，其余逐文件校验保持不变。
- 最多保留 2 个版本（当前版本和上一个版本）。只有新版本发布并健康运行之后，才删除更早的版本。

### 6.2 阶段：check → stage → apply

1. **check：** 从固定渠道获取签名清单列表并验签，过滤平台、安装器版本和允许来源，写入可安装列表。不下载 artifact。
2. **stage（可在使用中执行，不影响运行）：**
   - 预检磁盘空间：可用空间至少为 artifact 大小 + 解包大小 + 数据库备份估算，再加 20% 余量；不足则停止。不删除旧版本或用户数据来腾空间。
   - 下载到 `/var/lib/webdesktop-update/downloads/<sha>.part`，限速可选，可断点续传。
   - 校验大小、SHA256 和签名，然后解包到 `releases/<sha>.staging/`（使用现有 `staging_bundle.extract` 的防路径穿越逻辑）。
   - 运行 `verify_artifact` 校验，然后 rename 为 `releases/<sha>/`。状态变为 `ready`。
3. **apply（需要短暂停机，单独确认）：**
   1. 前置检查：目标是 `ready`；当前没有其他更新动作；迁移起点与当前 schema 版本一致；安装器版本兼容。
   2. 通知并结束当前 GUI 会话，按 D1-R2 顺序正常关闭浏览器；结束 PTY 会话。
   3. 停止后端，访客层 socket 保持。
   4. `pg_dump` 完整备份到 `/var/lib/webdesktop-update/backups/<时间>-<旧sha>.dump`（root 0600），并校验其可读性。
   5. 按清单顺序执行迁移（§6.4）。
   6. 复制 `.env` 到新版本目录，设置属主和模式并校验哈希。
   7. 以清单替换方式安装 `layers_changed` 中的访客层文件：先备份旧文件，再逐个 rename，并校验哈希。
   8. 用 rename(2) 原子切换 `/opt/webdesktop/app` 符号链接。
   9. 启动后端，执行健康检查：`/api/health`、内部 Next socket、访客层 socket 握手，以及 `staging_check.py` 的只读子集（属主、ACL、单元、监听端口），限时 120 秒。
   10. 成功：以新版本重新 `seal()` 生成 `recovery.json`，记录 `current=<新sha>, previous=<旧sha>`。
   11. 失败：自动回滚（§6.3）。

### 6.3 回滚

- **自动回滚：** apply 第 9 步失败时：停止后端 → 切回旧符号链接 → 恢复旧访客层文件 → 启动 → 健康检查 → 恢复旧的 `recovery.json`。
- **手动回滚：** 只允许回到 `previous`（磁盘上仍保留的那个版本），并且只有在 `migrations.reversible_to` 覆盖旧版本 schema 时才可用。
- **数据库不回滚：** 程序回滚**不会**回滚数据库。迁移采用 expand/contract：
  - 一个版本只能添加列、表或索引（expand）；删除和改名（contract）至少推迟到下一个版本，并标记 `reversible_to`。
  - 如果某版本必须包含不可逆迁移，清单中的 `reversible_to` 会等于目标版本本身。这时网页的回滚按钮不可用，只能通过 SSH 由管理员用第 4 步的备份做人工恢复。
- **回滚失败：** 停在维护状态，保留全部现场和日志，提示管理员通过 SSH 处理；不会进一步自动操作。

### 6.4 数据库迁移

- 引入版本化迁移目录：`drizzle/migrations/NNNN_*.sql`，由 drizzle-kit `generate` 生成后人工审核并随发布清单签名。
- 首次安装也改为执行迁移，不再使用 `push --force`；在已有安装上首次运行时登记基线版本。
- **执行身份：** 迁移以数据库所有者身份执行，在单个事务中完成（`CREATE INDEX CONCURRENTLY` 等例外单独列出）；失败则整体回滚，不切换程序版本。
- **兼容测试：** CI 增加 schema 兼容测试：V(n) 的程序必须能在 V(n+1) 的 expand schema 上正常运行。

### 6.5 状态与日志

`/run/webdesktop-update/status.json`（root:wdsvc 0640）：

```json
{ "schema": 1, "state": "idle|checking|downloading|verifying|ready|applying|rolling_back|failed|maintenance",
  "current": "<sha>", "previous": "<sha>|null", "target": "<sha>|null",
  "step": "backup|migrate|switch|health|...", "progress": { "bytes": 0, "total": 0 },
  "available": [ { "commit": "<sha>", "channel": "testing", "branch": "feature/CC", "created": "...", "can_install": true, "reason": null, "can_rollback_to": false } ],
  "last": { "action": "apply", "result": "ok|error", "code": "HEALTH_TIMEOUT", "at": "..." },
  "log_tail": ["...（已脱敏的固定格式行，最多 200 行）"] }
```

- 日志只写固定格式的步骤行和结果码；不含 URL 查询参数、令牌、密码、`.env` 内容或数据库连接串。
- **页面断线：** 浏览器刷新或断线后，重新读取状态文件即可恢复显示进度；更新单元的执行不依赖网页连接。
- **后端重启：** apply 期间后端会重启，网页轮询会出现短暂 503，界面按“更新中，服务重启”显示，恢复后读取最终结果。

## 7. 网页接口契约（保留现有 `/api/deploy` 与 `update-panel.tsx` 的布局）

| 方法 | 请求 | 返回 | 说明 |
|---|---|---|---|
| GET `/api/deploy` | — | 状态文件的投影（§6.5） | 只要求管理员会话 |
| POST `/api/deploy` | `{action:"check"}` | 202 + 状态 | 启动 `webdesktop-update-check.service` |
| POST `/api/deploy` | `{action:"stage", commit}` | 202 / 400 / 409 | `commit` 必须是可安装列表中的 40 位 hex |
| POST `/api/deploy` | `{action:"apply", commit, password}` | 202 / 401 / 409 | 需要再次输入密码；目标必须是 `ready` |
| POST `/api/deploy` | `{action:"rollback", commit, password}` | 202 / 401 / 409 | 只能回到 `previous`，且必须满足 `can_rollback_to` |

- 请求体只允许这些键，其他键（URL、路径、分支、命令、单元名、UID）一律返回 400。
- 移除旧的 `config`、`autoUpdate`、`restartOnUpdate`、`intervalMin`、`force-apply` 字段；自动更新开关在界面上隐藏。
- **界面：** 显示当前版本（短 SHA、渠道、构建时间）、可安装版本列表、“检查更新”、“下载并验证”、“安装已验证版本”（需要输入密码并确认停机提示）、进度、脱敏日志，以及“回滚到上一版本”（可用时才显示）。

## 8. 专项验收（后续实施时，在一次性 Debian 12 VM 上实际执行）

| 场景 | 期望 |
|---|---|
| 安装 V1；创建数据库记录（云盘节点、设置）、用户文件、Chromium 书签、Fcitx 配置 | 基线数据就绪 |
| 网页 check → stage → apply 升级到 V2 | 成功；上述数据、`.env` 哈希、AUTH_KEY、登录可用性、身份、单元和网络摘要全部不变 |
| 下载中断、断点续传 | 续传成功或明确失败，不会残留半个版本 |
| 签名错误、artifact SHA 不符、清单文件数不符、未登记的分支或渠道 | stage 失败，运行中的版本完全不受影响 |
| 新版本无法启动（注入故障） | 自动回滚到 V1，健康，数据不变 |
| 迁移失败（注入故障） | 事务回滚，程序不切换 |
| stage 期间（未 apply）正常使用桌面 | 不中断 |
| apply 期间刷新页面或断网 | 状态可以恢复显示，最终结果正确 |
| 手动回滚到 V1（可逆迁移） | 成功；不可逆迁移时按钮不可用 |
| apply 后实际重启 VPS | 启动的是已发布的新版本，`recovery.json` 校验通过 |
| 越权测试：桌面密码会话、错误或过期的再次验证、伪造 Origin、任意 commit、路径、URL、单元名注入、wdsvc 直接启动其他单元、wdguest/wdterm/wdgui 启动更新单元 | 全部拒绝 |
| 磁盘空间不足 | 预检停止，不删除任何数据 |
| 资源 | 在 1 GiB 有/无 Swap 上，stage 的峰值内存和 CPU 记录在案，不影响正在使用的桌面 |

## 9. 实施拆分建议（每步单独授权）

1. **4B-1：** 允许发布来源清单（替换三处常量与测试夹具），签名清单生成（CI）与校验，`releases/` 目录与符号链接校验适配。
2. **4B-2：** 版本化数据库迁移（替换 `push --force`），以及兼容测试。
3. **4B-3：** root 更新单元、polkit 规则、`update.py` 的 check/stage/apply/rollback，以及状态文件。
4. **4B-4：** `/api/deploy` 与 `update-panel.tsx` 改造、再次验证密码。
5. **4B-5：** §8 的 VM 专项验收，然后在一台**测试** VPS 上做真实 V1 → V2 升级（Phase 5）。

## 10. 明确不做

- 不恢复旧自动更新、定时巡检或无人值守安装。
- 不新增任何可从网页调用的任意命令、任意 URL、任意路径或任意单元接口。
- 不在服务器上构建（不执行 npm 或 next build），只安装 CI 已构建并签名的产物。
- 不让 wdsvc 或访客持有 Deploy Key、发布令牌、sudo 或安装目录写权限。
- 网页更新不修改身份、单元、网络、Swap、SSH 或资源限额；这些变更需要管理员通过 SSH 执行维护升级。
