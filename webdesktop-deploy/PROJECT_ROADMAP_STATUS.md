# WebDesktop 项目路线图：动态进度总表

本表是**动态状态表**，只记录已有提交与测试证据。原五阶段总体方案仍以 [ISOLATION_PLAN.md](ISOLATION_PLAN.md) 为准，产品形态与不可放宽的边界以 [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) 为准；各阶段的详细事实以对应报告为准。本表不替换、不覆盖任何旧审计报告，也不使用主观完成百分比。

- 更新日期：2026-10-24（Phase 4B-2 结束）
- 开发分支：`feature/CC`（唯一开发分支）；旧分支 `feature/private-deploy-isolation` 保持 `d962567` 不动
- `main`：`5922e14`；Beta：`v0.1.0-beta.1`（均未修改）
- 用户现有 VPS 运行的仍是旧版 `131ea566...`，本项目任何新代码都**未部署**到真实 VPS

状态口径：

| 标记 | 含义 |
|---|---|
| PASS | 有固定受测 SHA，且该轮报告的必需回归全部通过 |
| PARTIAL | 功能实现并有真实测试证据，但固定 SHA 的必需回归未全部通过，或范围只覆盖子集 |
| BLOCKED | 存在明确阻塞项，不能继续向下一个门槛推进 |
| 设计 | 只有设计文档，没有实现 |
| 未开始 | 没有实现，也没有设计文档 |

---

## 1. 原五阶段的实际状态

| 阶段（ISOLATION_PLAN §10） | 状态 | 证据 |
|---|---|---|
| 1. 设计与风险审查 | PASS | ISOLATION_PLAN.md（2026-10-19），基线 `5922e14` |
| 2. 私有仓库引导（bootstrap、只读 Deploy Key、root 克隆） | PASS（仅源码获取阶段） | PHASE2_REPORT.md；`tests/linux_private_repo.py` 7/7（Debian 12 CI） |
| 3. 执行层隔离 | PARTIAL（进行中） | 见第 2 节逐项；文件剪贴板、拖拽、多 GUI、Claude 尚未完成；C6 Playwright 已按用户决定暂缓（DEFERRED） |
| 4. 部署适配 | PARTIAL | 4A 候选入口为“已验证功能子集”；4B-1 可信发布来源/签名清单/版本目录基础完成（安全专项 PASS，集成 PARTIAL）；升级、迁移、回滚未完成（见第 5 节） |
| 5. CI 与真实 Debian 12 验收 | PARTIAL | CI 一次性 VM 基础设施可用；**真实 VPS 首次安装与升级从未执行**（见第 5 节） |

## 2. Phase 3 执行层隔离：已实现能力

身份：wdsvc（Next 认证、数据库、网关）、wdguest（文件 Agent）、wdterm（PTY）、wdgui（Xvnc/Openbox/PCManFM/Fcitx/Chromium）；各自独立 UID、固定 Unix Socket、SO_PEERCRED、systemd 沙箱。

| 子项 | 状态 | 最终受测代码 / CI | 报告 |
|---|---|---|---|
| 3A 文件 Agent 最小能力 | PASS | `54410117` / Actions 39240653958 | PHASE3A_REPORT.md |
| 3A+ WebSocket 认证 | PASS | 首启与重启各 7/7 | PHASE3APLUS_REPORT.md |
| 3B 文件功能（复制、移动、下载、ZIP、回收站） | PASS | `23980a2` | PHASE3B_FILES_REPORT.md |
| 3B PTY（wdterm Bash） | PASS | `712567e` / Actions 39278531809 | PHASE3B_PTY_REPORT.md |
| 3C 基础 GUI（Xvnc、Openbox、PCManFM、租约守卫） | 基础运行与权限 PASS；同轮 Claude 资源实验未通过 | 见报告（`ccdc926` Actions 39352323077 等） | PHASE3C_GUI_REPORT.md |
| 3C 可见 Chromium（原生 namespace/seccomp 沙箱、网络 namespace） | PASS（Chromium）；Claude 入口仍关闭 | `9a1518d` / Actions 39418021685 | PHASE3C_CHROMIUM_REPORT.md |
| Fcitx5 中文输入（GUI）与终端搜狗 IME 修复 | 已实现；终端修复为最小前端修复（`44142f3`），4A-R1 回归覆盖 | 见报告 | TERMINAL_IME_FIX_REPORT.md、PHASE4A_R1_AUDIT_REPORT.md |
| 3D staging 安装 | PASS（staging 范围） | `f195f90` / Actions 39442300926、39442300698 | PHASE3D_STAGING_REPORT.md |
| D1 Chromium 下载 → 文件 Agent → 本地设备 | PASS | `7b6d34c` / Staging 39906683933、Validate 39906683994（14/14、8/8） | PHASE3C_D1_1G_RECOVERY_REPORT.md（之前 `f30bd80` 为 PARTIAL，见 PHASE3C_DOWNLOAD_BRIDGE_REPORT.md） |
| C1 双向纯文字剪贴板 | **PARTIAL** | 功能 `a46a40d`；报告 `3eca337`；Staging 39978166929、Validate 39978166995 | PHASE3C_C1_TEXT_CLIPBOARD_REPORT.md |
| C2 双向 PNG 图片剪贴板 | **PARTIAL**（C2 功能与安全全部通过；固定 SHA 完整回归未全过） | 功能 `b827791`；C2 门禁 Staging 40021278627、Validate 40021278668；完整回归 Staging 40024316520、Validate 40024321806 | PHASE3C_C2_IMAGE_CLIPBOARD_REPORT.md |

### D1 已验证成果（摘要）

可见 Chromium 的原生下载落到 wdguest 的 Downloads，经文件 Agent 衔接到本地浏览器下载；断网、原生取消、SIGKILL 崩溃恢复、GUI 关闭重开、真实空间与 inode 耗尽、目录替换、路径穿越文件名等异常路径全部有真实 VM 证据；Chromium 正常关闭顺序修复（D1-R2）保证 Profile 与书签写盘。1 GiB 有/无 Swap、2/4 GiB、staging/isolated 在 `7b6d34c` 上全部通过。

### C1 状态说明（不得改为 PASS）

功能在 11 个真实 VM 配置上重启前 164/165、重启后 22/22 通过；但固定 SHA `a46a40d` 上 D1 下载用例在 4 个安装配置偶发失败，Validate 的 1 核无 Swap 有 1 项书签写盘断言失败，因此集成验收为 PARTIAL。C2 的固定 SHA `b827791` 上 C1 再次是 164/165、22/22（失败的是 1 GiB 配置的同一个用例，见第 3 节）。

### C2 状态说明

- 只支持静态 PNG，≤ 1000 KiB、单边 ≤ 4096、总像素 ≤ 3840×2160。1000 KiB 不是随意取值：xclip 在 ≥ 1 MiB 时使用 X11 INCR，在多个请求者并发时会挂起（已实测和二分）。
- 固定 SHA `b827791`：C2 在 11 个真实 VM 配置上重启前 143/143、重启后 11/11，三级验证（X11、桥字节、真实应用像素）两个方向全部通过，安全负向用例全部通过。
- 同一 SHA 的完整回归没有全部通过：D1 在 3 个 1 GiB 安装配置失败（重启前 316/319、重启后 20/22），C1 失败 1 项（164/165），Validate 1 核无 Swap 书签断言再次失败。失败类型都与 C1 固定 SHA 的历史失败相同，但没有证明与 C2 无关，所以 C2 也判 PARTIAL。

## 3. 已知延期问题（用户已同意延期，计划结合真实测试 VPS 人工取证）

| 问题 | 首次记录的提交 / 运行条件 | CI 证据 | 处理 |
|---|---|---|---|
| D1 首次导航偶发停在 New Tab（地址已校验，Enter 无效） | `c26867f`、`bdff8fc`、`a46a40d`；1 vCPU 为主 | Staging 39947954150、39969474159、39978166929 | 测试侧已加重新导航与 Escape，未根除；未证明与 C1 无关，也未证明有关 |
| D1 下载状态等待超时（`native filename chinese`，套件随即停止） | `d5f279b` 门禁 1024-1-1024 | 40085733078 | 历史 D1 偶发类型，本 SHA 重现；未调查到根因 |
| 健康检查“浏览器外网 HTTPS”：`https://example.com` 返回 503 | `d5f279b` 门禁 2048-2-1024（重启前） | 40085733078 | 新观察到的外部站点依赖；与 4B-2 无关；未重跑 |
| 崩溃用例后 “Restore pages?” 气泡抢焦点导致 Enter 丢失 | `bdff8fc`、`a46a40d` | 同上（isolated 1024-1-1024、staging 2048） | 同上 |
| 空间耗尽用例下载未完成 | `a46a40d` isolated 4096、`bdff8fc` staging 2048、`57d4d7d` 门禁 2048 | 同上 | 未调查到根因 |
| 下载历史缺失（重启前后 `history` 断言） | `a46a40d` staging 1024-1-1024；`b827791` staging 1024-1-1024、1024-1-0；`57d4d7d` 门禁 1024-1-0（重启前后都失败） | 39978166929、40024316520、40066396930 | 未调查到根因 |
| D1 崩溃用例“Bounded observable state timeout” | `b827791` isolated 1024-1-0 | 40024316520 | 未调查到根因 |
| C2 并发探测测试竞态（busy_probe：文字读取先占会话槽，图片按设计 409） | `57d4d7d` 完整回归 1024-1-1024 | 40066396930 | **已修正（4B-2，仅测试）**：改为真实竞争判定，不预设获胜方；`d5f279b` 三个门禁 C2 13/0，未重现 |
| C1“原生 Chromium → 网页”用例超时（`a46a40d`：测试导航超时；`b827791`：Ctrl+C 后 10 s 内 X11 CLIPBOARD 未出现该文本） | `a46a40d` staging 1024-1-1024；`b827791` isolated 1024-1-1024 | 39978166929、40024316520 | 只在 1 GiB 出现；C1 在 C2 之前运行，与图片操作无时间交叉；未调查到根因 |
| Validate `desktop_ui_runtime.mjs:67` 访客书签未写入持久 Profile | `a46a40d`、`b827791` gui-systemd swap-0；`57d4d7d` swap-1024（swap-0 通过）；`d5f279b` swap-0 再现 | Validate 39978166995、40024321806、40066400295、40085735949 | `bdff8fc` 上同一测试通过；两次固定 SHA 都失败，更像稳定的 1 核无 Swap 问题；未调查到根因 |
| 独立 Claude 资源实验 OOM（320/384 MiB） | Phase 3C Chromium / 4A-R1 | 见各报告 | Claude 正式入口继续关闭 |

处理原则：如实记录、不删断言、不改 skipped、不无限重跑；若后续发现新功能确实造成回归，必须修复。

## 4. 剩余 Phase 3C 子任务

C 编号只是执行管理标签，不替代原计划阶段编号，也不是不可调整的实施顺序。

| 标签 | 内容 | 状态 |
|---|---|---|
| C1 | 双向文字剪贴板 | 已实现，集成 PARTIAL |
| C2 | 双向 PNG 图片剪贴板 | 已实现，功能与安全通过，集成 PARTIAL |
| C3 | 文件剪贴板（PCManFM ↔ 本地） | 未开始；旧接口保持 503 |
| C4 | 文件拖拽 | 未开始；`/api/gui/[sid]/drop` 保持 503 |
| C5 | 应用启动器与多 GUI | 未开始；`/api/gui/apps`、`shortcut` 保持 503，GUI 单槽 |
| C6 | 旧 Playwright 极速浏览器迁移 | **DEFERRED / 用户决定暂缓（2026-10-24），不纳入当前开发和验收范围。** 不删除旧代码或历史设计；不重启旧浏览器服务；`/api/browser/*` 保持关闭；不分配 CI 开发任务；不阻止 4B-1 的独立技术验收；不记为 PASS。用户以后明确授权时再单独决定是否恢复 |
| C7 | Claude Desktop 可行性与受限运行 | 资源实验 PARTIAL；正式入口关闭 |
| — | 其他未迁移进程与 GUI 功能（进程控制、图标、启动文件等） | 按 ISOLATION_PLAN §8 清单逐项迁移 |

## 5. Phase 4：部署、升级、恢复与迁移

| 项 | 状态 | 说明 |
|---|---|---|
| 4A 候选隔离入口 `install-isolated.sh`（已验证功能子集） | PARTIAL / BLOCKED（完整交付门槛）；4A-R1 子集 PASS | PHASE4A_ADAPTATION_REPORT.md、PHASE4A_R1_AUDIT_REPORT.md |
| 有限恢复（service/repair 模式） | 已实现（子集） | 只恢复已登记安装的最终启动阶段；没有通用断点续跑 |
| `feature/CC` 发布来源（4B-1） | **安全专项 PASS；集成 PARTIAL** | 固定 SHA `57d4d7d`。三处分支常量保留，改为 `release_trust.py` 显式决策：无 root 策略时仅旧开发来源（原规则）；`testing-cc`/`feature/CC` 需 root 启用并登记 Ed25519 签名者，安装必须带规范化签名清单。门禁 Staging 40064428337（isolated 1G/1G 无 Swap/2G 全过）；完整回归 Staging 40066396930 门禁因历史 D1 与新观察 C2 测试竞态失败，安装矩阵（含 4 GiB）未执行。PHASE4B_1_TRUSTED_RELEASE_REPORT.md |
| 固定产物发布（CI 构建 + SHA256 + 清单） | 部分实现 | 产物记录真实分支；签名清单协议与验证已实现（4B-1），CI 只用一次性测试钥匙；没有正式签名钥匙/发布审批流程 |
| 管理员手动更新（网页按钮） | 设计 | PHASE4B_UPDATE_DESIGN.md；`src/lib/deploy.ts` 仍是禁用空实现 |
| 不可变版本目录 `/opt/webdesktop/releases/<sha>` | 4B-1 已实现（准备/识别/校验） | 不切换、不迁移、不重启、不删除；`app` 符号链接布局可识别但恢复器拒绝（4B-3 再实现切换） |
| 原地升级 V1 → V2、回滚 | 未开始（4B-3） | 当前重装是唯一路径；数据库回滚只能恢复备份（4B-2 已实测） |
| 数据库 schema 迁移框架（4B-2） | **数据库专项 PASS；集成 PARTIAL** | 固定 SHA `d5f279b`。`drizzle/migrations` 版本化迁移（SHA256 + 审核结构描述，受 4B-1 签名产物保护）；安装器 `push --force` → `db_migrate init`；运行时不再建表；旧库严格基线；只允许事务内 expand；advisory 锁；备份必须真实恢复验证。门禁 Staging 40083834460（isolated 1G/1G 无 Swap/2G/4G + staging 4G + 旧库基线全过）；完整回归 Staging 40085733078 门禁 1/3（历史 D1、外部 HTTPS 503），安装矩阵未调度。PHASE4B_2_DATABASE_MIGRATION_REPORT.md |
| 旧单账号部署的数据迁移 | 未开始 | ISOLATION_PLAN §7 的迁移流程尚未实现；新入口拒绝隐式迁移 |
| 卸载 | 未开始 | — |

## 6. Phase 5：验收计划

| 项 | 状态 |
|---|---|
| CI 一次性 Debian 12 VM（QEMU，1/2/4 GiB，有/无 Swap，staging/isolated） | 已具备，日常使用 |
| 全新真实测试 VPS 首次安装 | 未执行（未授权） |
| 真实 VPS V1 → V2 升级 | 未执行（依赖 Phase 4B） |
| Windows 浏览器人工功能测试 | 未执行；计划用于取证第 3 节延期问题 |
| 持久化、安全、长期稳定性（整日 soak、容量增长） | 未执行 |
| 正式 VPS 上线前验收 | 未执行 |

建议顺序：Phase 4B 最小可用的管理员手动更新应先于反复在真实 VPS 上重装，这样用户可以在一台测试 VPS 上持续升级并人工测试。

## 7. 当前版本是否允许部署正式 VPS

**不允许。** 理由：

1. Phase 3C 尚有 C3–C7 未完成，C1、C2 集成验收为 PARTIAL，存在第 3 节的未解释偶发失败（集中在 1 GiB 配置）。
2. `feature/CC` 已可通过显式授权的签名来源安装（4B-1），但没有正式签名钥匙与发布审批流程，测试钥匙不能登记到真实机器。
3. 没有原地升级和程序回滚能力（数据库版本化迁移与备份恢复已在 4B-2 实现，但更新执行器 4B-3 未实现）；任何问题仍只能重装。
4. 从未在真实 VPS 上执行过首次安装、升级或长期运行。
5. 仓库仍为 Public（历史记录），私有部署前提需要管理员另行处理。

满足 Phase 4B 最小可用更新、发布来源批准、并在一台**测试** VPS 上完成首次安装与 V1→V2 升级的人工验收之后，才适合讨论正式 VPS。

## 8. 后续任务清单

Phase 3C：C1（已实现，PARTIAL）、C2（已实现，PARTIAL）、C3 文件剪贴板、C4 文件拖拽、C5 应用启动器与多 GUI、C6 Playwright 迁移（DEFERRED，用户暂缓）、C7 Claude 受限运行、其他未迁移功能。

Phase 4：完整隔离部署适配、`feature/CC` 发布来源处理（4B-1 完成，PARTIAL 集成）、4B-2 数据库迁移（完成，数据库专项 PASS，集成 PARTIAL）、4B-3 更新执行器、4B-4 网页按钮、旧版数据迁移、固定产物发布、管理员手动更新、有界失败恢复及回滚。

Phase 5：CI 完整验收、全新测试 VPS 首次安装、测试 VPS 上 V1→V2 升级、Windows 浏览器人工功能测试、持久化/安全/长期稳定性检查、正式 VPS 上线前验收。
