# WebDesktop：新 VPS 一键部署

> **开发分支 3A 说明（2026-10-19）：** 第二阶段已验收，独立源码引导见 [PRIVATE_DEPLOY.md](PRIVATE_DEPLOY.md)。最小访客文件服务已建立独立 UID/Unix Socket 边界，实际 Debian 测试和限制见 [PHASE3A_REPORT.md](PHASE3A_REPORT.md)。终端、GUI、极速浏览器等尚未迁移，在开发分支返回暂不可用。下面的一键安装保持原稳定单账号流程，尚未接入新隔离服务；不要据此开放私有生产部署。实验初始化仅供可丢弃 Debian，未修改生产目录或 SSH。

3A 验收后的 WebSocket 入口修正、真实 Next 握手及 booted systemd 虚拟机验证见 [PHASE3APLUS_REPORT.md](PHASE3APLUS_REPORT.md)。运行入口统一为 `node server.mjs`（`npm start`/`scripts/start.sh`）；旧桌面 Upgrade 不会转发到 Next 的内部监听器。PTY 与 GUI 的后续协议和单元边界见 [PTY_GUI_NEXT_DESIGN.md](PTY_GUI_NEXT_DESIGN.md)，本轮不恢复这些执行功能。

3B 首个文件里程碑见 [PHASE3B_FILES_REPORT.md](PHASE3B_FILES_REPORT.md)：访客文件复制/移动/回收站、可恢复上传、下载和 ZIP 通过 IPC；新实验 HOME 使用独立 512 MiB 有界卷。只在新的一次性 Debian 实验环境依次使用 `setup-guest-layer.sh --lab` 和 `setup-guest-volume.sh --lab`；后者也核验并配置实验 SSH 拒绝服务/访客账号登录。拒绝接管旧 HOME/未知数据，不接入下文原安装器，不在生产机执行。

应用源码使用 <https://github.com/isAliceYoung/fa.git>，默认分支 `main`。目标是新的 Debian 12 VPS，包括 Google Cloud 实例；安装过程不依赖旧 VPS 的用户名、主目录或配置。

Windows 的 `F:\des11\webdesktop-deploy` 用来编辑和存放安装脚本。以下 Bash 命令均在 **新 VPS 的终端** 中运行。

## 一键安装

将整个 `webdesktop-deploy/` 上传到新 VPS，或在仓库中加入这些文件后克隆仓库。在包含该目录的位置执行：

```bash
sudo bash webdesktop-deploy/install.sh
```

如果已经直接以 root 登录 VPS，则使用：

```bash
bash webdesktop-deploy/install.sh
```

不需要填写旧服务器的用户名。脚本自动选择运行账号和主目录：

| 登录方式 | WebDesktop 运行账号 | 默认安装目录 |
|---|---|---|
| 普通用户通过 sudo 安装，例如 `alice` | 当前登录用户 `alice` | `/home/alice/webdesktop`（以系统实际主目录为准） |
| 直接以 root 登录 | 自动创建或复用 `webdesktop` | `/home/webdesktop/webdesktop`（以系统实际主目录为准） |
| 重复运行安装 | 读取上次记录的账号 | 读取上次记录的路径 |

这里的“运行账号”是 **Linux 系统用户**，用于运行 Node.js、终端、浏览器及图形程序；网页管理员用户名和桌面登录密码另行初始化，两者不是一回事。脚本新建的 Linux 账号密码被锁定，不添加 sudo 权限，也不设置 SSH 登录密码。

安装结束会显示实际安装路径、初始凭据保存位置及状态查询命令。安装器和状态查询脚本均不显示密码或密码查询命令。安装信息记录在 `/etc/webdesktop-deploy.conf`，不包含密码；辅助脚本安装到 `/usr/local/lib/webdesktop-deploy/`，避免脚本放在 `/root` 时普通运行账号无法读取。

## 安装范围

- Node.js：沿用成功记录中的 `22.23.2`，支持 amd64/arm64；下载后校验 SHA-256，失败时停止。
- PostgreSQL：本机数据库、独立应用角色和数据库、首次 schema 初始化。
- WebDesktop：克隆 `isAliceYoung/fa`、安装依赖、应用构建补丁、构建 Next.js、创建 systemd 服务。
- GUI：Xvnc、Openbox、D-Bus、剪贴板工具、Chromium 及中英文字体。
- Claude Desktop：沿用 `google_cloud.txt` 中成功的 APT 安装命令。
- Fcitx5：中文拼音、GTK/Qt 前端、Claude/Chromium 输入法包装器，默认 `Ctrl+Space` 切换，候选数 7。
- 资源自适应：自动检测可用 CPU 数、RAM 和已启用 Swap。先按 RAM 选择理想 Swap 总目标：小于 2 GiB 时为 4 GiB，2～不足 8 GiB 时为 2 GiB，8 GiB 及以上默认不新增；再按可用磁盘和 6 GiB 安装预算，从 4、2、1、0 GiB 中向下调整（不超过理想目标）。已有 Swap 优先保留，不自动停用、缩小或重新格式化 `/swapfile`。原生模块编译并发最多 4，且受 CPU 与内存限制。

Swap 文件创建在根分区。自动分配同时检查根分区与应用所在文件系统的 6 GiB 预算；两者在同一文件系统时，新增 Swap 只扣除一次。应用尚未克隆时检查其最近的已有父目录。应用位于独立分区且空间不足时也会警告并跳过新增 Swap。6 GiB 是安装规划预算，不是磁盘或内存一定够用的保证；低内存且不足 1 GiB 活动 Swap 的机器若因预算无法新增 Swap，会单独提示 Next.js 构建 OOM 风险，不强制分配。`--swap-gb 1` 等显式参数仍可指定总目标，已有 Swap 文件不会被自动扩容或覆盖。

自动模式发现已有 `/swapfile`、同名符号链接或对应 fstab 记录时，优先保留，不再重新选择更大的容量目标。已启用时输出普通保留信息；未启用时提示人工检查。磁盘预算不足或低内存且活动 Swap 不足的实际风险仍会提示。安装前磁盘预检查、Swap 预算和状态查询统一使用 `application_disk_path()` 确定实际应用所在文件系统。

服务仅监听新 VPS 的 `127.0.0.1:3000`，自动随系统启动。这一版不配置代理、Cloudflare Tunnel、域名、HTTPS、反向代理、公网防火墙或 Windows CLI。图形会话由应用按需创建，不另外创建固定显示号的 VNC 服务。

基础条件：Debian 12、amd64/arm64、可正常运行的 systemd、root 或 sudo 权限、能够访问软件源和代码仓库的网络。私密仓库需要提前为应用运行用户配置 GitHub 读取权限；不要把 token 放进脚本或仓库 URL。

## 查询状态

在原目录中执行，无需手动填写账号或路径：

```bash
sudo bash webdesktop-deploy/check.sh
```

即使删除了最初上传的脚本目录，也可以查询：

```bash
sudo bash /usr/local/lib/webdesktop-deploy/check.sh
```

查询只读取组件版本、构建结果、systemd 状态、监听地址、HTTP、配置、数据库连接及登录记录初始化状态。不安装、不重启、不修改数据库，也不显示密码。

磁盘容量检查覆盖根分区和应用所在文件系统：剩余 0 MiB 为 `FAIL`，不足 2 GiB 为 `WARN`。Swap 按实际启用区域检查：存在活动 Swap 为 `OK`；没有 Swap 且 RAM 不足 8 GiB 为 `WARN`，RAM 达到 8 GiB 时说明 Swap 可选并记为 `OK`。读取资源信息失败为 `FAIL`；警告本身不增加失败计数。

## 初始登录和之后修改

首次安装随机生成独立的管理员密码、桌面密码、数据库密码、`AUTH_KEY` 和管理员入口。管理员用户名默认 `admin`。初始凭据保存在应用目录受保护的 `.env` 中，权限为 `600`，由应用运行用户持有。凭据查询方法后续放入独立维护说明或凭据查询工具；当前安装和状态查询流程不显示凭据。

VPS 内部桌面入口为 `http://127.0.0.1:3000/`，管理员入口为 `http://127.0.0.1:3000/<ADMIN_PATH>`。这是新 VPS 内部地址，Windows 浏览器中的 `127.0.0.1` 指 Windows 自己；远程访问需要之后另行配置 SSH 转发或公网入口。

使用者和管理员之后通过程序已有的网页功能修改密码，修改结果保存在 PostgreSQL 中。`.env` 中的值仅用于首次初始化，不代表之后修改后的当前密码。重复安装不重新生成密码或 `AUTH_KEY`，不要通过修改 `.env` 重置已有账号。

## 可选参数

通常不需要任何参数。需要自行指定时才使用：

默认模式为 `auto`：没有构建时执行首次安装；检测到已有 `.next/BUILD_ID` 时，还要求 `.next/required-server-files.json` 存在，否则停止并报告构建不完整，不自动重建。构建文件齐全时保留应用文件，继续检查配置、数据库和 systemd。缺少服务时补全服务，绑定正确但未运行的服务尝试启动，正常运行的服务不停止或重启。成功还要求 `/api/health` 和登录记录初始化检查通过。可以明确指定模式：

```bash
sudo bash webdesktop-deploy/install.sh --mode install  # 首次安装或继续未完成的服务部署
sudo bash webdesktop-deploy/install.sh --mode repair   # 保留已有构建，检查并恢复匹配的服务
sudo bash webdesktop-deploy/install.sh --mode service  # 仅检查已有应用并恢复服务，跳过所有软件包安装模块
```

当前不提供原地升级模式。应用升级需要单独准备并验证新版本后再切换，避免破坏正在使用的 `.next` 和依赖目录。`repair` 也不是不停机维护保证：APT 更新 PostgreSQL 等系统组件时仍可能重启对应组件。

只需恢复 WebDesktop 服务时使用 `--mode service`。此入口复用安装锁、日志、已记录的运行用户和目录、磁盘与端口预检查，以及配置、数据库、服务绑定和健康检查；要求源码目录、两个构建标志文件、依赖、配置和运行所需工具已经存在。它只运行应用验证与服务恢复模块，不执行 APT、Node.js/PostgreSQL/GUI/Claude/输入法安装，也不拉取源码或重建应用。服务缺失时可补建，已停止的匹配服务可启动；活动但不健康的服务会报错供排查。不要直接将内部 `scripts/deploy-service.sh` 作为独立恢复入口。

```bash
# 指定运行账号；不存在时自动创建。deployuser 是示例名称，不是固定值。
sudo bash webdesktop-deploy/install.sh --user deployuser

# 关闭自动添加 swap，或跳过 Claude Desktop 安装
sudo bash webdesktop-deploy/install.sh --swap-gb 0
sudo bash webdesktop-deploy/install.sh --skip-claude

bash webdesktop-deploy/install.sh --help
```

`--install-dir` 只能指定所选账号家目录下的子目录；不指定时自动使用该账号的 `~/webdesktop`。`--repo`、`--branch` 可覆盖源码仓库和首次克隆分支。已有安装优先复用记录；显式指定 `--user` 或 `--install-dir` 才覆盖对应设置。

### IPv4-only 部署

普通安装模式（`auto`、`install`、`repair`）默认在安装早期、APT 下载之前关闭 VPS 的 IPv6，并通过 `/etc/sysctl.d/90-webdesktop-ipv4-only.conf` 在重启后保持设置。仍可显式指定 `--ipv4-only`。在 VPS 上执行：

```bash
sudo bash webdesktop-deploy/install.sh --ipv4-only
```

- 默认关闭 IPv6；`--allow-ipv6` 跳过本次网络修改，不会删除之前的配置或自动重新启用 IPv6。
- 内核不支持 IPv6 时自动跳过。
- 已有相同配置时不会重复写入；遇到符号链接或不同内容的配置文件会停止，交由管理员检查。
- 不修改 WebDesktop 的 IPv4 监听地址。
- IPv6-only 服务将无法访问。
- `--mode service` 始终跳过网络修改，包括显式传入 `--ipv4-only` 的情况。

写入配置和修改网络前会先检查 `all`、`default`、`lo` 三个内核控制项是否全部可写；任一不可用时停止，避免因已知缺失项而修改部分设置。`check.sh` 在检测到上述持久化配置文件时，额外验证三个控制项均为 `1`，且 `ip -6` 查询不到 IPv6 地址和路由；它只读取状态，不改变网络。内核没有 IPv6 支持时该项通过。

该功能不会配置代理，也不能替代浏览器的 WebRTC、DNS 和代理出口安全检查。

## 原安装记录的沿用方式

Claude 使用 `https://downloads.claude.ai/claude-desktop/key.asc` 签名公钥和记录中的 `https://downloads.claude.ai/claude-desktop/apt/stable` APT 源，再执行 `apt install claude-desktop`。没有自动切换其他社区构建来源。下载或 APT 校验失败会停止。

`patches/fix-deploy.py` 在构建前检查 `src/lib/deploy.ts` 的三处函数签名。已经修复则跳过；不替换函数实现，无法识别的版本会停止。首次构建保留开发依赖，因为 TypeScript 和构建工具需要它们。

Fcitx5 沿用记录中的环境变量与启动方式；已有输入法配置和其他 `/usr/local/bin` 包装器保留，不自动覆盖。

## 重复安装和故障排查

PostgreSQL 首次安装前，仅当 `/etc/postgresql/15/main/postgresql.conf` 尚不存在时，才创建初始化标记 `/var/lib/webdesktop-deploy/postgresql-15-main-initializing`。软件包或集群启动失败时保留标记，后续运行可继续初始化；总服务 `postgresql.service` 的活动状态不作为成功依据，最终必须通过 `127.0.0.1:5432` 的就绪检查，通过后删除标记。

IPv4 不通时，仅允许带标记的初始化集群自动恢复：保留默认监听值检查和配置备份，按具体 `15/main` 集群状态选择 `start` 或 `restart`；配置已是 `127.0.0.1` 时可直接继续启动。失败则恢复本次操作前的配置并保留标记。没有标记的已有集群，以及自定义的其他监听地址，不自动改写配置。旧版安装器失败后已留下配置、但没有标记的集群不会被自动认领，需要人工确认后处理。

已有源码不自动拉取、重置或切换分支；origin 与指定仓库不一致时停止。已有 `.env`、数据库、网页修改后的密码和输入法设置保留。只为空数据库初始化 schema；已有数据库结构不完整时停止，不自动删除或迁移数据。

重跑保留已有应用构建、依赖和数据库，不覆盖运行中的 `.next`。已存在的服务必须同时匹配安装目录和 Linux 用户；不匹配时停止。缺少服务时补全，正确绑定但停止或失败的服务会尝试启动；活动服务不执行停机或重启。健康或登录初始化失败时报告错误，不能仅凭 `BUILD_ID` 报告完成。首次安装没有可用构建时才安装依赖并构建。已有但无法读取的服务文件不会被覆盖。

```bash
sudo systemctl status webdesktop --no-pager
sudo journalctl -u webdesktop -n 80 --no-pager
```

安装日志位于 `/var/log/webdesktop-install/`，仅 root 可读。不要提交 `.env`、凭据或含真实密码的命令记录。所有 Shell 文件使用 UTF-8 和 LF 换行。

## 验证范围

本轮恢复与验收改进见 [RECOVERY_NOTES.md](RECOVERY_NOTES.md)。默认 `check.sh` 只读；`sudo bash webdesktop-deploy/check.sh --gui-smoke` 会额外创建临时的 Xvnc/Openbox/Chromium/Fcitx 会话，使用独立配置副本和浏览器 profile，结束后清理。这个测试验证程序启动和 Fcitx D-Bus 就绪，实际中文候选、输入和 Claude 登录仍需人工验收。

安装阶段会写入 `/var/lib/webdesktop-deploy/status`，仅记录模块、阶段、路径、时间和退出码，不保存密码。重试依据实际构建、服务和健康状态，而不是盲目相信阶段文件。安装前检查根文件系统、实际应用所在文件系统的可用空间及端口 3000；低于 8 GiB 仅警告，外来端口占用会停止且不杀进程。

可选 `--commit <完整40位SHA>` 固定首次克隆的源代码。已有目录如果与显式指定的 SHA 不一致会停止，不会自动 checkout/reset。省略时继续使用原有分支逻辑，实际提交号可用 `check.sh` 查询。Swap 总目标按 RAM、已有容量和磁盘预算自动调整，原生模块并行数仍按 CPU 和 RAM 限制；自动创建 swap 只对 ext2/ext3/ext4/XFS 启用，其他文件系统提示手工处理或使用 `--swap-gb 0`。

仓库应提交 `package-lock.json`；首次部署只使用 `npm ci --include=dev`，缺少锁文件时停止，避免现场解析出不同依赖版本。维护者更改依赖时重新生成并检查锁文件，不要手动填写传递依赖或完整性值。

`.github/workflows/validate.yml` 在 push、pull request 和手动触发时检查所有 Shell 文件、Python/Node 辅助脚本、重复部署保护测试，并在独立的 PostgreSQL CI 数据库上构建、检查 TypeScript、启动服务和请求 `/api/health`。CI 中的数据库账号与随机登录种子仅供隔离测试使用。

这仍是待真实新 VPS 验证的安装器。Chromium 沙箱、Claude 的 `.desktop` 启动入口与 Fcitx5 图形输入需要在 VPS 的真实 GUI 会话中确认；当前没有未经验证地更换这些启动行为。浏览器的 GUI 和无头启动路径都仍有 `--no-sandbox`，不能把通过语法或构建检查视为 GUI 安全验证。

本地检查覆盖 Bash/Node.js/Python 语法、补丁兼容与重复执行、随机密码、已有配置保留，以及不同登录场景的账号选择逻辑。尚未在你的新 VPS 上实际执行 APT 安装、完整 Next.js 构建或图形界面验证；实际结果以安装日志和 `check.sh` 为准。
