# 第二阶段交付报告：私有源码引导与测试环境

日期：2026-10-19。开发分支：`feature/private-deploy-isolation`。

**状态：本地实施完成，等待第二阶段验收；未开始第三阶段。** 本报告不代表完整私有部署、Linux 权限或真实 VPS 安装已验收。

## 1. 范围与分支

- 以稳定提交 `5922e1408de91e56771f1ea0bf94a9df1b7028cd` 创建独立开发分支，未改写 `main` 或 `v0.1.0-beta.1`。
- 上一轮取消生成 `欢迎使用.txt`、`示例图片.png` 的修改已保留，并单独提交为 `9fb6463`。
- 第二阶段变更保存在本地开发分支，不推送 main、不合并、不改变仓库 Public/Private 状态。
- 本次没有连接任何 VPS、创建或使用生产 Deploy Key、改变系统账号/SSH 配置、迁移目录、删除数据库或已有用户文件。
- 真实密钥测试只生成临时测试用 Ed25519 key，使用本地测试仓库；测试 key 不用于连接 GitHub，测试结束清理临时夹具。

## 2. 修改文件

| 文件 | 交付内容 |
|---|---|
| `src/lib/sys.ts` | 保留已有修改：取消自动生成欢迎文件与示例图片，保留其他桌面功能；属于前置独立提交 |
| `webdesktop-deploy/bootstrap.sh` | 独立源码阶段入口，可显式准备 Debian 工具，调用 prepare/check/clone；不调用旧安装器 |
| `webdesktop-deploy/scripts/private_repo.py` | root 身份、Deploy Key、GitHub 主机密钥固定校验、私有授权、克隆/提交校验、锁、幂等和安全失败 |
| `webdesktop-deploy/PRIVATE_DEPLOY.md` | 可信分发、测试授权、使用步骤、恢复、只读权限核对及阶段限制 |
| `webdesktop-deploy/README.md` | 添加开发阶段入口与未完成隔离的状态说明，原稳定安装操作保留 |
| `webdesktop-deploy/ISOLATION_PLAN.md` | 纳入第一阶段文档，补充 wdtunnel、身份边界、通道测试和阶段状态 |
| `webdesktop-deploy/templates/sshd-wdtunnel.conf.example` | 受限 SSH 转发设计模板；本阶段不安装、不应用 |
| `tests/run_tests.py` | 自动发现 Git Bash、符号链接测试适配、严格跳过判定及 JSON 结果 |
| `tests/test_private_repo.py` | 40 项测试：模拟 Git/SSH 安全行为、临时真实 key 和本地真实 Git 克隆/固定版本/重用 |
| `tests/test_tunnel_design.py` | 2 项设计检查：隧道模板限制以及 bootstrap 不配置 SSH |
| `tests/linux_private_repo.py` | 7 项独立 Linux root/nobody 实际权限、真实 Git、链接、锁和原子发布测试，已加入 CI，本机未执行 |
| `.github/workflows/validate.yml` | 使用严格测试入口，安装测试所需 OpenSSH，在 Debian 容器追加实际权限测试，扩大 Python 编译检查范围 |
| `webdesktop-deploy/test-results-phase2.json` | 本机完整 142 项测试的机器可读结果 |
| `webdesktop-deploy/PHASE2_REPORT.md` | 本报告 |

未改动：`install.sh`、`common.sh`、`install-webdesktop.sh`、`install-system.sh`、`install-postgres.sh`、`run-modules.sh`、`deploy-service.sh`、现有 systemd 应用模板，以及原有 100 项测试。已与 beta 基线对照这些核心安装文件，无差异。

## 3. 关键实现

### 私有仓库引导

`prepare` 在 root 专用目录创建每机独立 Ed25519 key，已有有效 key 复用。仅输出公钥和 GitHub 授权步骤，不输出私钥。公钥缺失可从原私钥恢复；公钥孤立、键对不匹配或受保护文件异常均停止，不覆盖凭据。

`check` 使用固定 GitHub Ed25519 主机公钥、严格 SSH 检查、唯一 IdentityFile 和新建环境验证读取权限与 branch。关闭 agent/密码交互/用户与系统 Git 配置/hooks/fsmonitor；原始命令错误不直接输出。既不创建 GitHub Deploy Key，也不调用生产密钥。

`clone` 只由 root 获取源码，默认发布到 `/opt/webdesktop/source`，不经过 wdsvc/wdguest。代码暂存、提交及 branch 历史校验完成后原子发布，不替换已经存在的目标，包含空目标目录。已有本工具创建的源码按受保护清单验证后离线重用，不自动 fetch/pull/reset/checkout，不执行 npm 或任何应用源码脚本。失败暂存目录保留供检查；没有删除旧源码/数据库/用户数据的代码路径。

“只读”由 GitHub Deploy Key 授权设置决定，必须不勾选 Allow write access。读取成功只证明有读权限，不能证明没有写权限；程序没有通过推送尝试检查写权限。

### 账号与隧道补充

- `wdsvc`：后端、认证、数据库、已鉴权 IPC，不直接 SSH 登录。
- `wdguest`：真实文件/终端/GUI 执行，不持有服务凭据、部署密钥或服务组权限，不直接 SSH 登录。
- `wdtunnel`：访客自己的 SSH 公钥入口，仅转发到 `127.0.0.1:3000`，不提供 Shell、PTY、命令、SCP/SFTP、反向/Unix socket/agent/X11/TUN 转发，不需要 root 密码。

设计包含 MaxSessions 0、PermitOpen、AllowTcpForwarding local 和每个公钥的限制，并要求后续验证真实 sshd 有效配置与成功/拒绝通道；不能仅靠静态模板宣称 SSH 隔离已成立。配置已对照 Debian 12 手册核对，但未安装或重载任何 sshd。

SSH 动态转发与本地转发都可能使用 direct-tcpip，不能承诺阻止客户端创建 SOCKS 监听；目标限制保证其不能借此连接其他服务。允许端口承载桌面与后台路由，因此后台仍需独立管理员认证。

## 4. 测试环境及实际结果

环境：Windows、Python 3.8、Node.js 24.15.0、Git 自带 Bash 5.3.9；Bash 位于 `D:\Git\bin\bash.exe`。没有安装 WSL 或修改全局 PATH/系统环境变量。

发现 Git Bash 后，首次原测试运行是 99 通过、1 跳过。随后在测试进程设置 `MSYS=winsymlinks:sys`，使该符号链接测试实际执行；没有把跳过改成通过断言或移除原测试。

| 实际命令/检查 | 通过 | 失败/错误 | 跳过 | 说明 |
|---|---:|---:|---:|---|
| `python tests/run_tests.py --json webdesktop-deploy/test-results-phase2.json` | 142 | 0 | 0 | 原有 100 项 + 私有引导 40 项 + 隧道静态检查 2 项，退出码 0 |
| `python -m unittest discover -s tests -p 'test_private_repo.py' -q` | 40 | 0 | 0 | 最后的小范围加固后重跑相关测试，退出码 0；不是额外 40 个不同测试 |
| 遍历当前 16 个 Shell 脚本并逐一执行 `bash -n` | 16 个文件 | 0 | 0 | 包含新 bootstrap；仅语法检查 |
| `python -m compileall -q webdesktop-deploy/patches webdesktop-deploy/scripts tests` | 通过 | 0 | 0 | Python 编译检查 |
| `node --check webdesktop-deploy/scripts/bootstrap.cjs` | 通过 | 0 | 0 | 现有配置辅助脚本语法检查 |
| `git diff --check` / 暂存差异检查 | 通过 | 0 | 0 | 差异格式检查 |

原有恢复测试实际覆盖：service 不安装包/不构建/不拉源码/不改网络、已有源码/凭据/构建保持、已有服务绑定保护、IPv4-only 及无 IPv6 内核处理、Swap 保留和容量判断、PostgreSQL 初始化失败恢复。

测试中的 SSH 授权/主机握手失败响应来自模拟工具；实际密钥生成与本地 Git 操作使用真实程序。本地文件传输只存在于测试适配器，生产代码限定为 SSH。Windows 的 Bash 系统文件符号链接不是 Linux DAC 权限验证。

### 未执行（与通过/失败/跳过分开）

| 项目 | 状态及原因 |
|---|---|
| `python3 tests/linux_private_repo.py` 的 7 项测试 | **未执行**：本机无 Linux；在 Windows 调用时前置检查明确拒绝并退出，测试数量为 0，不记为 7 通过或 7 跳过 |
| 更新后的 GitHub Actions | **未执行**：开发分支未推送，本轮没有触发 CI |
| GitHub 测试仓库实际 SSH 授权、真实主机握手/只读权限核对 | **未执行**：本轮仅测试/模拟 key，不配置任何 GitHub Deploy Key |
| Debian 12 新 VPS 全量安装/重启/systemd | **未执行**：未连接或修改 VPS |
| 真实 wdtunnel 公钥登录、允许/禁止转发、SFTP/Shell 拒绝 | **未执行**：本阶段仅设计，不创建账号或配置 SSH |
| 第三阶段访客实际 UID、文件/API/进程隔离 | **未执行**：第三阶段尚未授权开始 |
| Chromium/Claude/PCManFM/Fcitx/noVNC 运行与中文输入 | **未执行**：无测试 VPS/图形服务 |
| 新的完整 Next.js 构建/TypeScript 检查 | **未执行**：本阶段未修改应用依赖，工作区未安装 node_modules；CI 中原 build job 保留 |
| 生产验收、目录迁移、生产 key、仓库可见性修改 | **未执行**：本次授权明确禁止 |

## 5. 已知风险与限制

1. **尚未建立运行时隔离。** 不能把新源码阶段接到旧的同账号运行流程，更不能通过给访客读源码权限来绕过新目录限制。
2. Linux 真实权限的 7 项测试虽已编写并配置进 CI，尚无执行结果；Windows 的模拟元数据检查不能替代它们。
3. GitHub “只读”属性需要管理员核对授权页面；当前自动检查仅验证读取能力。
4. 固定主机公钥轮换需要独立审查，发生变化安全停止；默认仅支持 GitHub SSH 22，没有不安全回退。
5. 失败暂存源码和中断密钥生成目录不自动删除，反复失败可能占用磁盘；清理必须先由管理员确认内容。
6. bootstrap 是独立于应用的两文件引导包，需要可信分发和完整性核对；没有使用私有仓库匿名下载假设。
7. wdtunnel 的 PAM/账号锁定状态、其他 Match 条件、有效配置和实际转发必须在隔离 Linux 环境验证，静态模板不是上线结论。
8. 一 VPS 一个共享 wdguest 的架构仍不提供访客之间的相互隔离；最终范围不包含浏览器必须交付给客户端的前端 JavaScript。

## 6. 第三阶段前的依赖与停止点

当前停止，等待用户验收第二阶段。验收后再开始第三阶段，所需条件包括：

- 一个非生产、可丢弃、可运行 systemd 的 Debian 12 测试环境，先跑 Linux 实际权限和后续 IPC/PTY/GUI 测试；普通容器不等于完整 systemd VPS。
- 对本阶段 bootstrap、SSH 信任根、源码目录政策及失败保留行为的代码验收。
- 如需真实 GitHub 授权测试，单独批准测试仓库及测试 key 的授权/撤销；不能复用生产 Deploy Key。
- 明确访客执行产物的构建边界及依赖清单，禁止包含数据库/认证/真实配置与完整后端源码。
- 将文件 API、终端、GUI、极速浏览器、剪贴板、导出和进程控制整体迁入 wdguest；后端只鉴权及固定 IPC 转发，故障不得回退为 wdsvc 执行。
- wdtunnel 先在隔离环境验证通道和认证，再作为后续部署阶段的受控步骤；生产 SSH 或迁移继续需要单独授权。

完整的部署目录切换、无秘密构建、两套服务和一键安装衔接属于后续阶段，本次没有提前实施。
