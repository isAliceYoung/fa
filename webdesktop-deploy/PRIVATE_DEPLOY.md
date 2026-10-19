# 第二阶段：私有仓库源码引导

当前开发分支：`feature/private-deploy-isolation`。稳定标签：`v0.1.0-beta.1`。第二阶段已验收；本文只描述 root 获取受保护源码的独立引导。后续 3A 最小文件隔离的实测与边界见 [PHASE3A_REPORT.md](PHASE3A_REPORT.md)，仍未构成可向不可信访客开放的完整私有部署。

## 分发与首次授权

在可信管理电脑准备两个文件，保持相对目录结构，通过管理员自己的 SSH/SCP 或其他可信渠道上传到**测试 Debian 12**。不能通过匿名 URL 下载尚无权限访问的私有仓库。

```text
webdesktop-deploy/
├── bootstrap.sh
└── scripts/
    └── private_repo.py
```

引导只依赖 Python 标准库、Git 和 OpenSSH 客户端，不依赖 Next.js、数据库或私有应用模块。它不下载或执行远端安装脚本，不修改管理员的通用 SSH 配置。管理员负责核对这两个文件的来源和完整性；若未来公开分发，需独立发布并签名/校验，不得包含私有应用代码。

以下命令是以后在**获准的测试 VPS** 上执行的步骤，本次开发未对任何 VPS 执行。当前验收要求只用测试密钥，生产 Deploy Key 仍未授权创建。

工具缺失时显式安装 Debian 工具，然后准备身份；已有工具可省略 `--install-tools`：

```bash
sudo bash webdesktop-deploy/bootstrap.sh --install-tools prepare
# 或：
sudo bash webdesktop-deploy/bootstrap.sh prepare
```

每台 VPS 独立生成 Ed25519 key；私钥存于 `/root/.ssh/webdesktop-fa/deploy_key`，只输出公钥，不输出私钥。将公钥添加到目标 GitHub 仓库的 Settings → Deploy keys → Add deploy key，**不勾选 Allow write access**。访客 SSH key、root 登录 key 和 Deploy Key 必须分别使用。

SSH key 本身不携带“只读”属性；只读权限由 GitHub Deploy Key 设置决定。程序验证读取权限，不做推送测试，也不能仅凭读取成功证明未误授予写权限，管理员必须核对 GitHub 页面。

完成测试仓库授权后，检查并获取源码：

```bash
sudo bash webdesktop-deploy/bootstrap.sh check
sudo bash webdesktop-deploy/bootstrap.sh clone
```

`prepare` 只生成/验证本地身份并展示公钥，不联网探测仓库；`check` 验证固定 GitHub 主机密钥、SSH 读取权限及 branch；`clone` 在成功核验后暂存、校验并原子发布源码。授权失败会在 clone 前停止，提示添加公钥后重跑，不安装应用。

默认仓库 `git@github.com:isAliceYoung/fa.git`、分支 `main`、源码 `/opt/webdesktop/source`。如果实际授权另一个测试仓库，三个步骤都传相同 `--repo`。同一凭据目录绑定一个仓库，不能静默改绑。

```bash
sudo bash webdesktop-deploy/bootstrap.sh prepare --repo git@github.com:OWNER/TEST_REPO.git
sudo bash webdesktop-deploy/bootstrap.sh check --repo git@github.com:OWNER/TEST_REPO.git --branch main
sudo bash webdesktop-deploy/bootstrap.sh clone --repo git@github.com:OWNER/TEST_REPO.git --branch main --commit 完整40位提交号
```

HTTPS GitHub URL 可作为参数输入，但会转换成不含账号密码的 SSH URL。拒绝 URL 凭据、其他主机、查询参数、无效 branch/commit 及不安全路径。指定 commit 必须是选定 branch 历史中的提交；本阶段不支持任意不相关或已被分支历史移除的 commit。

## 主机身份及凭据保护

- 固定使用 GitHub 官方公布的 Ed25519 主机公钥，代码内公钥与独立记录的 SHA-256 指纹一致才可使用；连接时 `StrictHostKeyChecking=yes`，仅允许该主机密钥算法。
- 专用 `known_hosts`、`ssh_config` 和仓库绑定记录都在 root 私有目录，不改全局 SSH 配置，不自动接受扫描结果，不在主机密钥变化时关闭校验。
- 使用明确 `/usr/bin/ssh`、专用 `-F` 配置和唯一 IdentityFile；关闭 SSH agent、密码交互、ProxyCommand、ProxyJump 和其他身份文件来源。
- Git 使用新建的环境字典，不继承令牌、SSH_AUTH_SOCK、GIT_CONFIG_COUNT 或服务秘密；禁用系统/用户 Git 配置、凭据 helper、hooks 和 fsmonitor，禁止非 SSH 传输。测试适配器使用的本地 file 传输仅存在于测试代码。
- 凭据目录 `0700`，私钥/配置 `0600`，拒绝符号链接、硬链接及不安全所有者或权限，不通过自动 chmod/chown “修复”未知已有凭据。
- 工具 stdout 仅按协议解析；异常 stderr 不原样输出，避免远端错误或 Git 配置泄露秘密。错误按授权、主机密钥、网络、分支、超时及文件权限分类。

官方来源：[Deploy Keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)、[GitHub 主机公钥与指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)。主机公钥轮换需要独立核验和代码审查，不自动更新信任根。

## 重复执行和失败恢复

- 同一凭据目录加非阻塞文件锁，防止并发执行相互覆盖。
- 有效私钥复用，绝不自动重新生成；私钥存在但公钥缺失时从原私钥恢复公钥，不轮换身份。只有公钥、键对不匹配、类型/权限异常时停止供管理员检查。
- 已有 `known_hosts`、SSH 配置或仓库绑定记录不一致时保留原文件并停止。
- clone 仅接收 root 管理的 `/opt/webdesktop/` 子目录；拒绝访客家目录布局。暂存目录位于受保护父目录，原子发布不替换已有目标，包含空目录也不替换。
- 获取前后 branch 提交不一致时停止，保留暂存目录供排查，避免悄悄获取另一版本。
- 已有本引导创建的 checkout 通过 `.git/webdesktop-source.json`、origin、branch 记录及 commit 验证后离线复用；不 fetch、pull、reset、checkout，不覆盖本地编辑，会报告 dirty 状态。
- 已有其他来源的 checkout、其他 branch 或不匹配 commit 均不自动接管；旧生产布局也不会迁移。
- 失败的 `.source-*` 保留；存在任意此类条目时停止新克隆，防止反复失败无限创建。管理员须先检查并人工归档或清理，不自动删除未知数据。新克隆要求 1 GiB 可用空间，克隆/checkout 监测 512 MiB 保留空间与源码预算。中断的 `.key-*` 也保留供检查。
- 代码校验完成前不发布目标目录，不执行 npm、应用安装器、源码脚本或 systemd 操作。

网络检查默认使用 GitHub SSH TCP 22。端口受阻时安全报错；本阶段没有自动切换 443、HTTP Token 或代理回退。

## 与原安装器及 service 模式的关系

原 `install.sh`、`scripts/install-webdesktop.sh`、`common.sh`、systemd 服务和 PostgreSQL/Swap/IPv4 模块未改动，新 bootstrap 不在其调用链中。

原 `service` 模式仍不获取 Git、不配置部署凭据、不安装包、不构建、不修改网络。新 bootstrap 是显式的独立源码阶段，不作为 service 模式前置条件。

**不要将受保护 checkout 交给现有运行账号，也不要为了跑通旧安装器给 wdguest 读源码的权限。** 后续第三/四阶段会实现独立访客执行层、无秘密构建账号、只读运行目录、两套服务及安全衔接。在这些工作完成前，不能声称私有源码已与网页终端和图形应用隔离。

`wdtunnel` 的权限边界、限制项和测试矩阵见 [ISOLATION_PLAN.md 第 14 节](ISOLATION_PLAN.md#14-补充访客-ssh-隧道账号-wdtunnel)。本阶段不创建此账号、不添加访客公钥、不安装模板、不重启或重载 sshd。

## 本地及 CI 测试

Windows 或 Linux 统一入口：

```text
python tests/run_tests.py --json .test-results/regular.json
```

Windows 自动从 Git 安装位置发现真实 Bash；设置进程级 `MSYS=winsymlinks:sys`，允许无需 Windows 管理员/开发者模式执行的 Bash 系统文件链接测试。它验证的是 Bash 的链接拒绝逻辑，不是 Linux DAC 权限。退出后不修改用户或系统环境变量。

结果单独报告 total、passed、failed、errors、skipped 和未执行范围；默认有跳过也返回失败。需要刻意接受跳过时必须显式用 `--allow-skips`，不得把它记为通过。

原有 100 项测试保留，覆盖 IPv4-only、Swap、PostgreSQL 恢复、重复安装与 service 语义。新增测试覆盖密钥重用/恢复、主机信任、SSH 环境、授权失败、branch/commit、暂存失败及不覆盖、错误脱敏和隧道模板静态约束。临时真 key 只在系统测试临时目录生成，测试结束删除；不接触任何生产 key，不联网使用这些测试 key。

Debian CI 容器另执行：

```bash
python3 tests/linux_private_repo.py
```

该脚本要求可丢弃的 root Linux 测试容器，只在 `/root/wd-phase2-test-only-*` 下创建夹具，使用本地测试仓库代替网络 GitHub 传输，验证真实 Git、root/nobody 读权限、符号链接/硬链接、锁以及不替换空目录的原子发布。它不创建服务/访客/隧道账号，也不代表真实 SSH 通道或完整 VPS 安装通过。

本机尚无运行中的 Linux/WSL；Linux 权限测试和 CI 工作流若未实际运行，验收记录必须明确写“未执行”。实际 GitHub 授权握手、完整 Debian 安装、systemd、原生 GUI、Fcitx 输入和 wdtunnel 实际通道测试同样需分别记录，不以静态或模拟测试替代。
