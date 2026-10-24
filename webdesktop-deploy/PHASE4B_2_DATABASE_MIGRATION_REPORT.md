# Phase 4B-2：数据库版本化迁移、备份验证与兼容性测试 — 实施与验收报告

- 分支：`feature/CC`（只在此分支开发和提交）。`feature/private-deploy-isolation` 仍为 `d962567`，`main` 仍为 `5922e14`，`v0.1.0-beta.1` 未改动。
- 起点：4B-1 报告提交 `769f93d`（4B-1 固定受测代码 `57d4d7d`）。
- **受测固定 SHA：`d5f279b2292bf8a2b9bc644fb624e7d901b1725e`**（代码树 `2cc937e87a8870e7ab6859d521654a935cb44af3`）
- **结论：**
  - **4B-2 数据库迁移专项：PASS**
  - **4B-1 发布安全补验（含 4 GiB）：PASS**
  - **完整 WebDesktop 集成验收：PARTIAL**
  - 三者分别判定，见第 18 节和第 20 节。
- 本轮没有连接任何真实 VPS，也没有访问任何真实数据库或生产密钥。所有数据库操作都只在 CI 一次性容器和一次性 VM 中进行。

---

## 1. 当前分支与固定代码 SHA

| 项 | 值 |
|---|---|
| 代码提交 | `8ae8969` → `9e22684` → `c50ab95` → `d5f279b`（固定受测） |
| 报告提交 | 单独提交（`[skip ci]`），不含代码 |
| 网页更新按钮 | 仍禁用；`src/lib/deploy.ts`、`/api/deploy`、`update-panel.tsx` 本轮均未修改 |

## 2. 原有数据库初始化流程与修改原因

修改前的实际调用关系（以代码为准）：

```
首次安装（staging_install.Install.install；isolated 共用同一引擎）
  install-postgres.sh        PostgreSQL 15 集群 + IPv4-only 恢复（未改）
  bootstrap.cjs create-env   随机 .env：DATABASE_URL / ADMIN_* / DESKTOP_PASSWORD / AUTH_KEY（未改）
  bootstrap.cjs database     创建 wd_app 角色和 wd_db（保留已有角色和库；未改）
  bootstrap.cjs schema-state 只判断“没有表”或“五个表名都在”（未改，继续作为第一道检查）
  drizzle-kit push --force   ← 自动同步 schema，可能删列删表（已替换）
  启动后端 → instrumentation.register()
     → ensureAuthTables()：运行时执行 create table if not exists auth_config/admin_users（← 已移除）
       并在 admin_users 为空时写入初始管理员
     → 桌面密码未设置时写入初始桌面密码
  bootstrap.cjs auth-state   确认管理员和桌面密码记录存在（未改）
隔离恢复（isolated_deploy recover）：不做任何数据库结构操作（未改）
每次认证请求：getCfg/setCfg/... → ensureAuthTables()
稳定版 install.sh：确认空库后执行 push --force（稳定脚本，按要求未改）
旧 scripts/update.sh：CREATE TABLE IF NOT EXISTS + push --force（旧自动更新已禁用，未启用、未修改）
CI 构建 / Validate：在一次性 CI 库上执行 push --force（已改为迁移）
```

为什么要改：
- `push --force` 会让当前代码里的 schema 覆盖数据库，不能用于升级路径，也无法审计。
- 运行时建表让“谁负责数据库结构”变得不明确。

## 3. 初始迁移 SQL 与 schema 对照

- `drizzle/migrations/0001_initial.sql`：由项目锁定的 drizzle-kit 0.31.10 根据 `src/db/schema.ts` 生成，**逐字节保留**，没有手工修改。
- `drizzle/migrations/0001_initial.schema.json`：经过审核的规范化结构描述，覆盖五个表的每一列：
  - 类型（`format_type`）、是否可空、默认值表达式、identity/generated、排序规则；
  - 每个约束（PK、`admin_users_username_unique`、`fs_nodes_parent_id_fs_nodes_id_fk … ON DELETE CASCADE`）及其 `pg_get_constraintdef`；
  - 每个索引的 `pg_get_indexdef`；
  - 两个 serial 序列（integer，1..2147483647，cache 1，OWNED BY `admin_users.id` 与 `fs_nodes.id`）；
  - 行级安全，以及所有对象是否属于应用角色；
  - public 下的视图、触发器、规则、策略、函数、自定义类型、扩展，以及额外 schema（必须为空）。
- 这份描述是手写的，在真实 PostgreSQL 15 上执行 0001 后与系统目录逐项比对一致（CI `db-migrations` job 和四个 VM 规格都通过）。
- 确定性测试另外核对：描述中的列集合与 `schema.ts` 里的 `pgTable` 定义逐表一致。
- `.gitattributes` 把迁移文件和测试夹具设为 `-text`，避免换行转换改变摘要。实施过程中在 Windows 上实际遇到过一次，见第 17 节。

## 4. 迁移版本与摘要格式

`drizzle/migrations/manifest.json`（字段固定，不允许多余字段）：

```json
{ "schema": 1, "format": "webdesktop-db-migrations", "migrations": [
  { "id": "0001_initial", "previous": null, "kind": "initial", "transactional": true, "maintenance": false,
    "sha256": "<SQL 文件 SHA256>", "fingerprint": "<规范化结构描述的 SHA256>" } ] }
```

- 版本 ID 格式为 `NNNN_name`，唯一，按顺序排列。`previous` 必须精确接上前一个版本。第一个必须是 `initial`，之后只能是 `expand`。
- `transactional` 必须为 true，`maintenance` 必须为 false（需要维护窗口的迁移不支持自动执行）。
- SQL 和结构描述的 SHA256 都必须与清单一致，结构描述还必须是规范化 JSON。
- **完整性链**：`drizzle/migrations` 已加入 `staging_bundle` 的打包根目录。每个文件都在内层 manifest 中按文件记录 SHA256，而内层 manifest 的摘要受 4B-1 签名发布清单保护。所以**没有修改** 4B-1 已签名字段的含义，也没有扩展签名协议。
- 以 root 运行 CLI 时，迁移目录及其所有父目录必须为 root 所有、不可被组或其他用户写入。CLI 只接受 `/opt/webdesktop/app`，或 `/opt/webdesktop/releases/<sha>/app`（为 4B-3 预留）。
- 不接受任何用户提供的 SQL 路径或 SQL 正文。

**SQL 允许清单**（写死在代码中，经过审查）：
- 只允许以下四类语句：`CREATE TABLE`、`ALTER TABLE … ADD COLUMN`（若 NOT NULL 必须带 DEFAULT）、`ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY|UNIQUE|CHECK`、`CREATE [UNIQUE] INDEX`（不允许 CONCURRENTLY）。
- 以下内容在接触数据库**之前**就被拒绝：DROP、RENAME、ALTER … TYPE、TRUNCATE、INSERT/UPDATE/DELETE、COPY、GRANT/REVOKE、事务控制语句、函数和 DO 块（`$` 美元引号）、VACUUM、LOCK、CONCURRENTLY（返回 `NOT_TRANSACTIONAL`）。
- 外键定义中的 `ON DELETE/UPDATE` 动作不算数据修改。

## 5. 旧数据库基线识别逻辑

| 状态 | 判断 | 允许的操作 |
|---|---|---|
| `empty` | public 下没有任何表、序列或其他对象 | `init` |
| `legacy-exact` | 没有迁移记录，且结构指纹**完全等于** 0001 | `baseline` |
| `recorded` | 记录表结构精确；记录是本发布链的前缀，每条的 id/sha/previous/指纹/方法都一致；当前结构等于最后一条记录的指纹 | `migrate`（有待执行的版本时） |
| `partial` | 没有记录，只有部分应用表 | 拒绝 |
| `mismatch` | 表名相同，但列、类型、可空性、默认值、约束、索引或序列不同 | 拒绝 |
| `unknown` | 没有记录，且存在未知表或对象 | 拒绝 |
| `drift` | 有记录，但实际结构与记录的版本不一致（包括多出一张表） | 拒绝 |
| `record-corrupt` | 记录表被篡改：摘要、方法、previous 不对，或为空 | 拒绝；记录表结构异常时直接 `RECORD_CORRUPT` |
| `unknown-version` | 记录里有本发布不认识的更新版本 | 拒绝 |

- 诊断只输出对象名，例如 `unexpected tables.admin_users.constraints.admin_users_username_key`，不输出数据。
- 基线登记只创建记录表并写入一行 `method='baseline'`，**不修改任何业务数据**。
- 不会通过补建表、删列或重置认证配置来消除差异。
- 实测：旧 `update.sh` 风格的建表语句（PostgreSQL 默认约束名 `*_key`/`*_fkey`）被判为 `mismatch` 并拒绝。现有 schema 和所有旧部署都由 `push --force` 创建，被识别为 `legacy-exact`（第 9 节）。

## 6. `ensureAuthTables()` 的处理

- 调用路径：`instrumentation.register()`（Next 启动时），以及 `getCfg/setCfg/delCfg/getSecret`、登录、改密等认证函数。每个进程只执行一次（Promise 缓存）。
- 修改内容：
  - 删除两条运行时 `create table if not exists`；
  - 改为只读检查 `to_regclass('public.auth_config')` 和 `to_regclass('public.admin_users')`，缺表时直接报错“数据库结构未初始化：请由管理员执行版本化迁移”，网页请求返回 503，绝不建表；
  - 管理员初始化（`admin_users` 为空时用 `ADMIN_PASSWORD` 写入，无默认密码）和桌面密码初始化保持不变。
- 兼容性：
  - 所有已知旧部署都已经有这两张表（由 push 创建），不受影响；
  - CI 的 build/Validate 流程先用迁移建库再启动，`auth-state` 检查通过；
  - 四个 VM 规格上管理员和桌面登录正常。

## 7. 迁移执行身份、事务与锁

- **执行入口**：`node webdesktop-deploy/scripts/db_migrate.cjs status|init|baseline|migrate|backup|verify-backup <app 目录>`。
  - 只读的 `status` 以外，所有修改都要求 root。
  - 连接身份是 `.env` 中的应用角色（数据库所有者），不使用超级用户执行 DDL。
  - 没有 HTTP 入口。
- **锁**：`pg_try_advisory_lock(0x57444231, 2)`，拿不到锁时立即返回 `BUSY`。
- **事务**：每个迁移单独一个事务，依次执行：
  1. `SET LOCAL search_path=public`，并设置 lock_timeout 10 秒、statement_timeout 300 秒；
  2. 第一个迁移或基线时创建记录表；
  3. `LOCK TABLE webdesktop_schema_migrations IN ACCESS EXCLUSIVE MODE`；
  4. 校验当前最后一条记录等于 `previous`；
  5. 执行 SQL，写入记录；
  6. 重新描述系统目录，指纹必须等于该版本审核过的描述，否则 `SCHEMA_MISMATCH` 并回滚；
  7. COMMIT。
  任何错误都会 ROLLBACK，版本不会前进。
- **空间**：`init`/`baseline`/`migrate` 要求 PostgreSQL 卷至少有 512 MiB 可用。
- **输出**：只有固定代码、对象名和非敏感 JSON，没有 SQL 正文、连接串或行数据。
- **`status` 的计划输出**：`state`、`current`、`target`、`pending`、`compatible`、`migration_allowed`、`baseline_allowed`、`init_allowed`、`maintenance_window`、`problems`，以及固定说明“程序回滚不会回滚数据库；expand 迁移只向前，上一版程序必须保持兼容”。
- **安装器**：`staging_install` 在 `schema-state == empty` 之后执行 `db_migrate.cjs init`，工具内部再次确认库是空的。以下行为保持不变：已有库拒绝、失败后不能重复执行破坏数据、不重新生成密码或 AUTH_KEY、不重置账号、PostgreSQL IPv4 初始化与恢复。

## 8. 全新安装测试（场景 A）

- **CI 真实 PG15**：空库执行 `init` 后状态为 `recorded/0001_initial`，指纹与审核描述完全一致；再次 `init` 返回 `NOT_EMPTY`，数据不变。
- **VM（isolated 1 GiB 有/无 Swap、2 GiB、4 GiB）**：
  - 安装器通过 `init` 建库，记录中 `0001_initial` 的方法为 `applied`，安装日志里没有 drizzle-kit；
  - 真实后端上桌面登录、`GET/POST /api/fs`、`PUT /api/settings`、`PUT /api/workspace` 和后台管理员登录全部正常。
- **staging 4 GiB**：同样通过 `init` 安装，健康检查两次 40/0。

## 9. 现有数据库保留测试（场景 B）

- **真实旧安装**：staging-baseline 用旧安装器（`f195f90`，`push --force`）安装，然后用本轮工具执行 `--legacy`，3/0：
  - 识别为 `legacy-exact`；
  - `baseline` 记录 0001（method=baseline），所有表的行摘要不变，管理员哈希、secret、desk_hash 不变，旧程序照常登录读取；
  - 再次 baseline 或 init 均被拒绝。
- **CI 真实 PG15**：
  - `drizzle-kit push --force` 建库并写入数据后，baseline 前后行摘要一致；
  - 六种变体都被拒绝，诊断只含对象名，`auth_config` 数据保持原样，记录表不会被创建：mismatch（`update.sh` 风格）、partial（只有认证两表）、unknown（多一张表）、类型改变、缺唯一约束、可空性漂移。

## 10. V1→V2 实际迁移（场景 C、D）

- **测试版本 V2** `0002_test_probe`：
  - 内容是新表 `wd_migration_probe`、可空列 `desktop_settings.probe_note`、索引 `fs_nodes_parent_id_probe_idx`；
  - **只存在于** `tests/fixtures/db-migrations-v2`，有独立清单；
  - 产品迁移目录只有 `0001_initial`，测试会断言这一点。
- **VM 在线库**（真实安装的 wd_db，正在运行的 V1 后端）：
  - 迁移到 `0002_test_probe`，用 V1 列计算的行摘要和凭据都不变，迁移用时 0.03–0.06 秒；
  - 重复执行不产生任何变化（记录数和指纹都不变）；
  - V1 发布对这个库执行 `status` 得到 `unknown-version`，`migrate` 返回 `MIGRATION_REFUSED`。旧发布不会对它不认识的新版本做任何操作。

## 11. 旧程序与新 schema 的兼容性

- VM：数据库迁移到 V2 之后，**正在运行的 V1 后端**通过 HTTP 完成以下操作，全部成功：
  - 桌面登录、`GET /api/fs`、创建文件（`fs_nodes` INSERT）；
  - 写设置（`desktop_settings` UPSERT，表里此时已多出新列）；
  - 保存工作区（`desktop_workspace` UPDATE）；
  - 后台管理员登录、退出登录。
- CI：用 V1 的列清单执行 INSERT、UPSERT、UPDATE，在 V2 上同样成功。

## 12. 备份与真实恢复证据

`db_migrate.cjs backup`（root）：
- 先确认结构已知（`recorded`/`legacy-exact`），否则 `BACKUP_REFUSED`。
- 目录 `/var/lib/webdesktop-db/backups` 必须为 root 0700；存在 `.partial` 文件时拒绝（`STALE`）；可用空间必须不少于库大小 + 512 MiB，否则 `DISK_SPACE`，不写任何文件。
- 由 postgres 身份执行 `pg_dump -Fc`，输出写入 root 以 `O_EXCL|O_NOFOLLOW` 0600 打开的 `.partial` 文件，fsync 后重命名。
- **只有真实恢复通过才算备份成功**：
  - 创建独立数据库 `wd_restore_check_<随机>`（属于应用角色）；
  - root 打开备份文件，把可随机读取的描述符交给 `pg_restore --exit-on-error`，postgres 拿不到备份路径；
  - 用应用身份连接临时库，结构指纹和所有表的行摘要都必须与源库一致；
  - 最后删除临时库。

VM 证据（四个规格）：
- 备份约 11.6 KB，root 0600 / 目录 0700，`restore_verified=true`，五张表一致，测试结束时没有残留的临时库。
- 截断一半的备份：`verify-backup` 返回 `RESTORE_FAILED`，没有残留临时库，在线数据不变。
- 空间不足（fallocate 占满）：`DISK_SPACE`，目录中没有新文件。
- 中断：存在遗留 `.partial` 时，下一次备份返回 `STALE`，不覆盖也不删除。这是用预置 `.partial` 文件模拟的；数据库很小，pg_dump 不到 1 秒就完成，无法可靠地在中途杀掉。
- **数据库回滚只能靠恢复**：
  1. 停止后端，把 V2 库改名，新建空库；
  2. 由 root 用描述符把迁移前的备份 `pg_restore` 回来；
  3. 执行 `install-isolated.sh --mode service` 恢复服务；
  4. 状态回到 `0001_initial`，所有表的行摘要与备份前快照一致，管理员哈希、secret、desk_hash 一致；
  5. 真实登录和读写正常；
  6. 最后删除被保存的 V2 库。
- 备份文件内容、密码和连接串不进入 CI Artifact 或日志，证据中只有大小和 SHA256。

## 13. 失败注入、空间不足与并发（场景 E、F、G）

- **E 注入失败**：`0003_test_fail` 先合法地 `ADD COLUMN probe_fail`，然后对不存在的表建索引（42P01）。
  - 整个事务回滚：指纹不变，`probe_fail` 列不存在，记录不前进（仍为 0002），行摘要不变；
  - 后端照常服务。
  - CI 与四个 VM 规格结果一致。
- **F 并发**：
  - 测试另开会话持有 `desktop_settings` 的 ACCESS EXCLUSIVE 锁，证明迁移 A 确实正在执行中（advisory 锁已持有）；
  - 此时迁移 B 立即返回 `BUSY`；释放锁后 A 完成；
  - 记录精确为 `0001_initial, 0002_test_probe`。
  - CI 与 VM（从备份恢复出的独立库）结果一致。
- **G 未知或被篡改的库**：
  - VM：已记录的库被多加一张表，或删掉外键，判为 `drift`；记录摘要被改为全 0，判为 `record-corrupt`；`migrate` 都被拒绝，不做任何修补。
  - CI：记录方法被伪造、出现未来版本、记录为空、记录表被加列，都被拒绝。
  - 清单篡改（SQL 或描述摘要、previous、重复 ID、非事务、需要维护窗口、未知字段、摘要一致但内容是 DROP）在接触数据库前就被拒绝。
- **真实环境中的发现**：VM 中早先的 4A 测试（`phase4a_modes.py`）在在线库留下了 `phase4a_fixture` 表。工具准确报出 `unexpected tables.phase4a_fixture`，判为 drift，并拒绝迁移和备份。测试把这一点作为第一条断言，再由测试自己清理这张表，产品从不删表。

## 14. C2 测试竞态修正结果

- 修改位置：`tests/image_clipboard_ui.mjs` 的 busy probe（实际位于 C2 测试中，而非 `download_bridge_ui.mjs`）。只改测试的同步和判定方式，生产端的并发上限和数据类型限制都没有改。
- **顺序测试**（全局只允许一张图在传输）：原本就通过刻意保持上传未完成来提供证据，没有改动。
- **真正的竞争测试**（同一会话中图片和文字争用一个槽）：
  - 不再预设哪一方获胜；
  - 要求两者确实重叠，所有响应都属于允许的结果（OK、BUSY、RATE_LIMITED、EMPTY、NON_TEXT）；
  - 若图片获胜，重叠期间的文字请求必须出现 BUSY；若文字获胜，图片必须得到 `409 BUSY`。
- 结果：固定 SHA 完整回归的三个门禁，C2 都是 13/0（重启后 1/0）。三次都是图片获胜，期间文字分别被拒 25、33、41 次。上一轮的竞态失败没有再出现。

## 15. 4 GiB 4B-1 发布安全补验

在固定 SHA 上运行 `[4b2-gate]`（Staging 40083834460，success）：

| 配置 | 4B-1 VM | 重启后来源校验 | 安装前拒绝 | 4A-R1 审计 | 恢复模式 | 注入失败恢复 | 健康检查 |
|---|---|---|---|---|---|---|---|
| **isolated 4096-4-1024** | 19/0 | 1/0 | 15/0 | 12/0 | 22/0 | 1/0 | 40/0、40/0 |
| **staging 4096-4-1024** | — | — | — | — | — | — | 40/0、40/0（签名来源 + `init` 安装） |
| isolated 1024-1-1024 / 1024-1-0 / 2048-2-1024 | 各 19/0 | 各 1/0 | 各 15/0 | 各 12/0 | 各 22/0 | 各 1/0 | 各 40/0 两次 |

这一专项不依赖 D1 GUI 门禁就能开始。它的通过只说明发布安全，**不**代表整个产品的集成验收通过。

## 16. 资源结果

数据库操作（VM，固定 SHA，`/proc` 采样）：

| 配置 | 备份（含真实恢复验证） | 峰值 RSS | 峰值 FD | 期间最低 MemAvailable | 占用磁盘 | V2 迁移 | OOM |
|---|---|---|---|---|---|---|---|
| 1 GiB 有 Swap | 0.63 s | 62 MiB | 31 | 555 MiB | 16 MiB | 0.055 s | 0 |
| 1 GiB 无 Swap | 0.47 s | 60 MiB | 31 | 533 MiB | 16 MiB | 0.033 s | 0 |
| 2 GiB | 0.60 s | 62 MiB | 31 | 1545 MiB | 16 MiB | 0.059 s | 0 |
| 4 GiB | 0.43 s | 62 MiB | 31 | 3322 MiB | 16 MiB | 0.040 s | 0 |

- “占用磁盘”包含临时恢复库在操作期间的开销。测试库数据量很小，所以这些数值主要反映固定开销。
- 生产数据量更大时，所需磁盘按“库大小 + 512 MiB”预检。
- 资源限制和服务配额都没有调整。

## 17. CI Run 与 Artifact

| 提交 | Staging | Validate | 结果 |
|---|---|---|---|
| 8ae8969 `[4b2-gate]` | 40080280987 | 40080280962 | 结构描述 SQL 中 `text \|\| "char"` 运算符不唯一（我的缺陷）；db-migrations job 因编译原生模块缺 make 失败 |
| 9e22684 `[4b2-gate]` | 40080759263 | 40080759212 | **db-migrations 11/0**；场景 B 3/0；isolated 因 `phase4a_fixture` drift（正确拒绝）连锁失败 |
| c50ab95 `[4b2-gate]` | 40082543689 | 40082543601 | 数据库 VM 8/6：恢复验证把备份路径交给 postgres，读不了 root 0700 目录（我的缺陷） |
| **d5f279b `[4b2-gate]`** | **40083834460** | **40083834489** | **全部通过**（第 15 节） |
| **d5f279b 完整回归（workflow_dispatch，只触发一次）** | **40085733078** | **40085735949** | 门禁 1/3（第 18 节），安装矩阵未调度；Validate 8/9 |

实施中发现的另一个问题：Windows 上 `git apply` 当时还按旧的 `text=auto` 写出迁移 SQL，换行被转成 CRLF，导致摘要不一致。提交前通过 tree hash 比对发现，已改为直接写入原始字节；新的 `.gitattributes` 规则会防止再次发生。

Artifact：
- `db-migrations-results`（CI 真实 PG15）；
- `isolated-install-evidence-*`：`health.tar` 内含 `db-migrate-vm.json`、`release-trust*.json`、`phase4a-*.json`；
- `staging-install-evidence-1024-1-1024-baseline`：含 `db-baseline-legacy.json`；
- `download-smoke-evidence-*`、`gui-desktop-one-core-swap-*`。

## 18. Passed / Failed / Skipped / Not executed（固定 SHA `d5f279b`）

**4B-2 数据库专项（PASS）：**

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| CI 真实 PG15 `db_migrate_test.mjs`（两次 Validate 各一次） | 22 | 0 | 0 | 0 |
| VM 数据库套件（4 个规格 × 14） | 56 | 0 | 0 | 0 |
| VM 场景 B（旧安装器建的库） | 3 | 0 | 0 | 0 |

**4B-1 发布安全补验（PASS）：**

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| 4B-1 VM（4 × 19）/ 重启后（4 × 1） | 76 / 4 | 0 | 0 | 0 |
| 安装前拒绝（4 × 15）/ 4A-R1 审计（4 × 12）/ 恢复模式（4 × 22）/ 注入失败恢复（4 × 1） | 60 / 48 / 88 / 4 | 0 | 0 | 0 |
| 健康检查（门禁 run：isolated 4 + staging 4 GiB，各 2 次；baseline 2 次 × 39） | 478 | 0 | 0 | 72 |

**完整集成验收（PARTIAL）：**

| 范围 | Passed | Failed | Skipped | Not executed |
|---|---:|---:|---:|---:|
| 门禁 job | 1 | 2 | 0 | 0 |
| 安装矩阵 job | 0 | 0 | 0 | 9 |
| Validate job | 8 | 1 | 0 | 0 |
| D1 重启前 / 后 | 62 / 4 | 1 / 0 | 0 | 24 / 2 |
| C1 重启前 / 后 | 45 / 4 | 0 / 0 | 0 | 0 / 2 |
| C2 重启前 / 后 | 39 / 2 | 0 / 0 | 0 | 0 / 1 |
| 门禁健康检查 | 199 | 1 | 0 | 76 |
| Validate GUI（desktop-ui swap-0 / swap-1024，gui-ui × 2） | 15 | 1 | 0 | 2 |

失败明细，以及历史问题在本 SHA 上是否重现：

1. 2 GiB 门禁的重启前健康检查中，“浏览器访问外网 HTTPS”一项：`https://example.com` 返回 **503**（来自外部站点）。D1/C1/C2 已全部通过，但该门禁因此在重启前停止，重启后的测试未执行。这是**新观察到的外部依赖问题**，与 4B-2 无关；不重跑，记录在案。
2. 1 GiB 有 Swap 门禁：D1“native filename chinese”下载状态超时，D1 停止，24 项未执行。属于**历史 D1 偶发类型**（下载状态或导航超时），本 SHA 上重现。
3. Validate swap-0：`desktop_ui_runtime.mjs:67` 书签写盘断言，**历史问题，本 SHA 上重现**（上一轮出现在 swap-1024）。
4. 本 SHA 上**未重现**：D1 空间耗尽、下载历史缺失、C1“原生 Chromium → 网页”、C2 竞态（已修正）。

本地：Windows Node 24 上 `db_migrate_test.mjs` 无数据库部分 4/0（7 项需要数据库，记为未执行）。之后 `db_migrate.cjs` 又有两处修正，由 CI 真实 PG15 和 VM 覆盖。Linux 本机按要求没有运行重型测试。

## 19. 修改的文件与 SHA

固定受测 SHA：`d5f279b2292bf8a2b9bc644fb624e7d901b1725e`。相对 `769f93d`：27 个文件，+1311/−34。

| 类别 | 文件 |
|---|---|
| 新增（产品） | `webdesktop-deploy/scripts/db_migrate.cjs`；`drizzle/migrations/{0001_initial.sql,0001_initial.schema.json,manifest.json}` |
| 修改（产品） | `src/lib/auth.ts`（移除运行时建表）；`webdesktop-deploy/scripts/staging_install.py`（`push --force` → `db_migrate init`）；`webdesktop-deploy/scripts/staging_bundle.py`（打包迁移目录）；`.gitattributes` |
| 新增（测试） | `tests/db_migrate_test.mjs`、`tests/db_migrate_vm.mjs`、`tests/ci_db_init.mjs`、`tests/fixtures/db-migrations-{v2,fail}/*`（仅用于测试） |
| 修改（测试） | `tests/image_clipboard_ui.mjs`（C2 竞态）、`tests/staging_vm_driver.py`（数据库 VM 套件、场景 B） |
| CI | `staging.yml`（构建用迁移建库，`[4b2-gate]`）、`validate.yml`（新增 `db-migrations` job，build 用迁移建库，`[4b2-gate]`） |

未修改：稳定版 `install.sh`/`install-webdesktop.sh`、`scripts/update.sh`（旧自动更新，仍禁用）、`bootstrap.cjs`、`install-postgres.sh`、`release_trust.py`、`isolated_deploy.py`、资源策略、systemd/polkit 模板、`src/lib/deploy.ts`、`/api/deploy`、`update-panel.tsx`。

## 20. 是否可以进入 4B-3

**技术上可以开始，需要用户授权。**

- 4B-3 可以直接使用的基础：
  - 可信来源（4B-1）；
  - 不可变版本目录（4B-1）；
  - 可查询、可验证的数据库版本（`status` 计划输出）；
  - 只做 expand 的事务迁移执行器；
  - 经过真实恢复验证的备份；
  - “数据库回滚只能靠恢复备份”的实测流程。
- 尚未实现、留给 4B-3 的：
  - root 更新单元和 polkit 放行；
  - 备份 → 迁移 → 程序切换 → 健康检查 → 失败处理的协调流程；
  - `/opt/webdesktop/app` 的切换与 `.env` 迁移；
  - 版本保留策略。
- 历史 GUI/D1 偶发问题和外部 HTTPS 依赖仍会让完整集成验收停留在 PARTIAL，建议另行处理。

## 21. 真实 VPS 是否完全未修改

**完全未修改。**
- 没有连接 Evoxt 或任何真实 VPS，没有访问真实数据库、用户文件、生产 SSH 或 Deploy Key。
- `main`、Beta、旧开发分支和仓库可见性都没有改动，也没有使用生产签名私钥。
- 网页自动更新仍关闭，没有新增任何网页可调用的 SQL、Shell 或 root 接口。
- C6 仍为 DEFERRED。
