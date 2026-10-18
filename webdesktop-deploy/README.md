# WebDesktop：新 VPS 一键部署

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

安装结束会显示实际安装路径、初始登录信息的查看命令及状态查询命令。安装信息记录在 `/etc/webdesktop-deploy.conf`，不包含密码；辅助脚本安装到 `/usr/local/lib/webdesktop-deploy/`，避免脚本放在 `/root` 时普通运行账号无法读取。

## 安装范围

- Node.js：沿用成功记录中的 `22.23.2`，支持 amd64/arm64；下载后校验 SHA-256，失败时停止。
- PostgreSQL：本机数据库、独立应用角色和数据库、首次 schema 初始化。
- WebDesktop：克隆 `isAliceYoung/fa`、安装依赖、应用构建补丁、构建 Next.js、创建 systemd 服务。
- GUI：Xvnc、Openbox、D-Bus、剪贴板工具、Chromium 及中英文字体。
- Claude Desktop：沿用 `google_cloud.txt` 中成功的 APT 安装命令。
- Fcitx5：中文拼音、GTK/Qt 前端、Claude/Chromium 输入法包装器，默认 `Ctrl+Space` 切换，候选数 7。
- 小内存机器：低于 4 GiB 内存且没有 swap 文件/配置时添加 2 GiB swap；保留已有 swap。

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

## 初始登录和之后修改

首次安装随机生成独立的管理员密码、桌面密码、数据库密码、`AUTH_KEY` 和管理员入口。管理员用户名默认 `admin`。应用目录的 `.env` 权限为 `600`，由应用运行用户持有；安装结束显示查看首次登录信息的确切命令。

VPS 内部桌面入口为 `http://127.0.0.1:3000/`，管理员入口为 `http://127.0.0.1:3000/<ADMIN_PATH>`。这是新 VPS 内部地址，Windows 浏览器中的 `127.0.0.1` 指 Windows 自己；远程访问需要之后另行配置 SSH 转发或公网入口。

使用者和管理员之后通过程序已有的网页功能修改密码，修改结果保存在 PostgreSQL 中。`.env` 中的值仅用于首次初始化，不代表之后修改后的当前密码。重复安装不重新生成密码或 `AUTH_KEY`，不要通过修改 `.env` 重置已有账号。

## 可选参数

通常不需要任何参数。需要自行指定时才使用：

```bash
# 指定运行账号；不存在时自动创建。deployuser 是示例名称，不是固定值。
sudo bash webdesktop-deploy/install.sh --user deployuser

# 关闭自动添加 swap，或跳过 Claude Desktop 安装
sudo bash webdesktop-deploy/install.sh --swap-gb 0
sudo bash webdesktop-deploy/install.sh --skip-claude

bash webdesktop-deploy/install.sh --help
```

`--install-dir` 只能指定所选账号家目录下的子目录；不指定时自动使用该账号的 `~/webdesktop`。`--repo`、`--branch` 可覆盖源码仓库和首次克隆分支。已有安装优先复用记录；显式指定 `--user` 或 `--install-dir` 才覆盖对应设置。

## 原安装记录的沿用方式

Claude 使用 `https://downloads.claude.ai/claude-desktop/key.asc` 签名公钥和记录中的 `https://downloads.claude.ai/claude-desktop/apt/stable` APT 源，再执行 `apt install claude-desktop`。没有自动切换其他社区构建来源。下载或 APT 校验失败会停止。

`patches/fix-deploy.py` 在构建前检查 `src/lib/deploy.ts` 的三处函数签名。已经修复则跳过；不替换函数实现，无法识别的版本会停止。首次构建保留开发依赖，因为 TypeScript 和构建工具需要它们。

Fcitx5 沿用记录中的环境变量与启动方式；已有输入法配置和其他 `/usr/local/bin` 包装器保留，不自动覆盖。

## 重复安装和故障排查

已有源码不自动拉取、重置或切换分支；origin 与指定仓库不一致时停止。已有 `.env`、数据库、网页修改后的密码和输入法设置保留。只为空数据库初始化 schema；已有数据库结构不完整时停止，不自动删除或迁移数据。

重跑会重新安装组件包、依赖并构建；运行中的本应用服务会在构建前停止。构建失败时保持停止状态，请修复后重跑。修改已有 systemd 单元前保留一次 `.webdesktop-original` 备份。

```bash
sudo systemctl status webdesktop --no-pager
sudo journalctl -u webdesktop -n 80 --no-pager
```

安装日志位于 `/var/log/webdesktop-install/`，仅 root 可读。不要提交 `.env`、凭据或含真实密码的命令记录。所有 Shell 文件使用 UTF-8 和 LF 换行。

## 验证范围

本地检查覆盖 Bash/Node.js/Python 语法、补丁兼容与重复执行、随机密码、已有配置保留，以及不同登录场景的账号选择逻辑。尚未在你的新 VPS 上实际执行 APT 安装、完整 Next.js 构建或图形界面验证；实际结果以安装日志和 `check.sh` 为准。
