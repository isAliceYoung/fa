# 3D：全新 Debian 12 测试部署与人工验收

仅用于得到明确授权的全新测试 VPS。当前先交付代码与 CI；**尚未连接用户新 VPS，不代表真实 VPS 已安装/验收**。不运行稳定版 install.sh，不迁移旧数据库/目录，不配置正式域名，不安装 Claude，不启用更新。

## 安装前需要确认

管理员下一步确认 Debian 12 amd64、IPv4 SSH 地址/端口及凭据、全新无数据、1 vCPU/1 GiB RAM/10 GiB 磁盘，并授权安装。先保留云平台控制台/快照和现有 SSH 会话，再操作。网络入口仍是 127.0.0.1:3000；普通产品用户未来使用 HTTPS，本次管理员以 SSH 隧道验收。

本轮安装器要求系统盘至少 6 GiB **剩余**空间，名义 1 GiB RAM（MemTotal 至少 900 MiB）、systemd PID 1、cgroup v2。已有用户须逐一列为管理员；未知系统用户、本地自定义 unit、旧 WebDesktop/PG 数据、已有防火墙策略、占用端口都会停止。默认只接受已有 TCP 22 监听；使用其他 SSH 端口或供应商自定义服务的机器先提交 inventory 审查，不为通过测试直接删除服务/防火墙。

准备可信的 root 管理工具 Git checkout（只读来源、GitHub 主机密钥验证沿用 [PRIVATE_DEPLOY.md](PRIVATE_DEPLOY.md)）；不在此创建生产 Deploy Key。也可在管理员电脑从已核实仓库产生完整 Git bundle、核对 SHA 后传入。不得把含密钥的整个 .git 配置目录从 CI 复制到 VPS。

原版云镜像若尚无 Git/Python，获准后先安装获取受信任工具所需的最小依赖：`sudo apt-get update && sudo apt-get install -y --no-install-recommends git ca-certificates python3`。这是获取/验证安装器 checkout 的前置条件，不是提前安装 WebDesktop；CI 同样明确执行这一步。

取得指定开发提交的绿色 **Staging Debian 12 artifact and installation** Actions 结果。下载 `staging-debian12-完整SHA`，解压外层 GitHub ZIP 得到 staging-runtime.tar.gz、staging-runtime.sha256、staging-artifact.json。**SHA256 应从已核对仓库、分支、完整 SHA 的受信任 CI 摘要独立确认**，不能仅信任陌生下载包内自带的校验文件。GitHub Artifact 的 ZIP digest 与内部 tar.gz digest 是两个值，不要混用。构建产物保留 7 天，过期应对同一固定 SHA 重新构建和验收，不悄悄切换 HEAD。

构建环境为 Debian 12 amd64、glibc 2.36、Node 22.23.2 / ABI 127；实际 VPS 不执行 npm ci/Next build。清单逐文件校验，保留源代码 bundle、固定 SHA 与 Node/原生依赖；拒绝目录穿越、逃逸软链接、硬链接、重复/多余文件和超预算解包。不包含 CI .env、测试密码、测试密钥、数据库或 Claude；打包同时检查构建输出未包含 CI 随机凭据。

## 获准后使用的命令

以下命令**本轮没有在用户 VPS 执行**。使用受信任 checkout 替换工具路径；将两个值替换为核实的完整 SHA 和内部 tar.gz SHA256，不可原样运行占位符。工具 checkout 的 HEAD 必须等于该 SHA，所有祖先目录与文件需 root 管理、不可被访客写入。

```bash
sudo bash /root/fa/webdesktop-deploy/install-staging.sh --inventory

# 先在管理员手中固定值，不使用 git rev-parse origin/HEAD 自动追随分支。
WD_STAGE_COMMIT='替换为40位完整提交SHA'
WD_STAGE_DIGEST='替换为CI摘要中的64位tar.gz-SHA256'

sudo bash /root/fa/webdesktop-deploy/install-staging.sh \
  --commit "$WD_STAGE_COMMIT" \
  --artifact /root/staging-runtime.tar.gz \
  --artifact-sha256 "$WD_STAGE_DIGEST" \
  --authorize-new-vps '替换为实际hostname' \
  --admin root \
  --origins 'http://127.0.0.1:3000,http://127.0.0.1:18890'
```

如果机器有合法 debian 管理账号，追加 `--admin debian`；其他管理员同理，不能为了安装而删除未知用户。完整工具 checkout 可先 `git checkout --detach 固定SHA` 并检查干净。安装器不向 GitHub创建密钥、不从移动中的分支在线更新源码；来源是该固定开发提交 CI 制作的 source.bundle，并与输入 SHA 严格比较，root 克隆到 `/opt/webdesktop/source`。私有仓库由管理员受信任下载 CI 产物即可，VPS 不需要 GitHub 账号/生产 Deploy Key。

流程：只读预检 → 外部校验和/内部清单校验与有界解包 → 记录专属授权状态 → APT 依赖 → 原 IPv4-only 设置、补齐约 1 GiB Swap → root 源码与编译包 → 共用层安装器建立身份/卷/Socket/守卫/网络 → 独立随机 DB/登录配置 → cgroup → systemd 校验/启动/HTTP 与认证初始化检查。编译包为 `/opt/webdesktop/app`，root:wdsvc，只让后端读取；源码目录 root-only。访客运行包保持最小 Python/配置文件，不含 Next/认证/DB 源码。

密码只在 `/etc/webdesktop-staging/login.json`（root 0600）与后端受保护 `.env` 中保存。管理员在私人本地终端查看，**不要把内容粘贴到聊天、截图或工单日志**。网页改密码后初始种子不代表当前密码；健康检查使用初始种子，改密后的登录检查失败应明确处理，不能重置数据库解决。

## 状态与自动化检查

```bash
sudo bash /opt/webdesktop/source/webdesktop-deploy/check-staging.sh \
  --json /root/staging-readonly-01.json

# 会用测试桌面密码登录、撤销原登录，并启动/关闭 GUI；仅在空闲测试机使用。
sudo bash /opt/webdesktop/source/webdesktop-deploy/check-staging.sh \
  --live --allow-session-replacement --json /root/staging-live-01.json
```

每次输出使用新文件名；不会覆盖已有结果。默认只读检查不能证明 GUI 正常，未执行功能单独计数；live 检查通过真实 HTTP/WS → 后端认证 → 固定 IPC，不直接替代 wdguest 文件 API。它会在访客根目录创建独有 `staging-health-*` 文件并通过 API 移入回收站，不删除未知用户数据。输出 JSON 和摘要分别计通过、失败、跳过、未执行。CLI 退出 0 只表示已执行检查无失败，**不表示未执行项也通过**。尚未开发的功能标为 Known limitation。

SSH 本地配置检查不能代替远端管理员实际登录。CI VM 会真实检查管理员 SSH 与四个身份即使临时持有有效测试公钥也不能登录，并在重启后复核；该测试密钥由 VM 驱动临时生成且销毁，不在部署产物或普通健康检查中安装。

## Windows 网页人工验收

经管理员授权部署完成后，Windows 另开终端建立隧道：

```powershell
ssh -N -L 127.0.0.1:18890:127.0.0.1:3000 管理员账号@测试VPS的IPv4
```

保留正确 SSH 主机密钥核验。浏览器打开 `http://127.0.0.1:18890`；不同端口必须在安装时 `--origins` 列入实际来源。不要用 `localhost` 与 `127.0.0.1` 相互替代；不要开放公网 3000/5900/5432。当前只接受显式 HTTP loopback 来源，正式 HTTPS 留待后续。

| 操作 | 预期与记录 |
|---|---|
| 输入独立桌面密码登录 | 出现桌面；记录登录耗时，截图勿带密码 |
| 主目录创建文件夹与文本、上传小文件 | 文件保存于有界访客卷；真实 Agent 操作，非数据库虚拟文件假象 |
| 复制、移动、重命名、删除到回收站并恢复 | 内容一致；下载后在 Windows 比对内容/大小，可用 Get-FileHash；不要覆盖真实文件 |
| 下载单文件和测试目录 ZIP | 文件名正常、ZIP 可展开；记录响应时间 |
| 终端 `whoami; pwd; printf '你好\n'` | 实际 wdterm Bash，ANSI/UTF-8/Ctrl+C/resize 正常 |
| 终端 `cat /opt/webdesktop/app/.env` 与源码路径 | Permission denied；不要自行修改系统权限来“修复”拒绝 |
| 先关闭其他 GUI，再打开 PCManFM | 可看到共享普通工作文件，创建独有测试目录，不能访问源码/秘密 |
| 关闭 PCManFM，打开浏览器 | 真正 Chromium 窗口；访问 https://example.com 并导航/输入 |
| 在网页文本框 Ctrl+Space，输入 nihao，按数字 1 | 候选词可见，输入“你好”；切回英文、切焦点再输入 |
| 关闭 Chromium，再次打开 | 书签/profile 已保存；旧进程树回收；不承诺恢复未保存标签页 |
| 刷新网页 | PTY 有限时间恢复原 Bash；GUI 结束旧进程后重新创建，profile 保留，未保存窗口状态不保留 |
| 另一个浏览器隐私窗口/另一设备经独立管理员测试隧道登录 | 旧页面锁定，旧 PTY/GUI 权限撤销；普通产品用户未来无需 SSH |
| 关闭 GUI 后检查资源，再重启 VPS | 管理员 SSH 正常；重新运行健康检查；无旧 GUI/Chromium 残留，挂载和 Socket 恢复 |

每项记录结果、时间、截图或下载哈希；失败按程序故障、测试夹具、资源上限、Known limitation 分类。不同设备测试隧道各自的浏览器实际来源必须已配置，不能临时放宽 Host/Origin 校验。

## 资源与安全观察

用健康 JSON 的 cgroup/meminfo/swaps/disk 数据，另在管理员终端观察：

```bash
free -m
swapon --show --bytes
df -h / /home/wdguest
systemd-cgtop -b -n 1
systemctl show webdesktop.slice webdesktop-staging.service webdesktop-gui.slice \
  -p MemoryCurrent -p MemoryPeak -p MemorySwapCurrent -p MemoryMax -p MemorySwapMax -p TasksCurrent
journalctl -k --grep='oom\|Out of memory' --no-pager
```

父 workload RAM 768 MiB/High 672、Swap 640、CPU 90%、320 tasks；Next 384/Swap128、PG128/Swap64、Agent96/Swap32；PTY192/Swap128；GUI512/Swap256（基础192/64、Chromium320/192）。子上限有重叠，父组控制实际总量，独立守卫不挤入工作负载父组。主机 1 GiB Swap 不等于每服务 1 GiB，明显卡顿不能算可用。

初步磁盘预算：OS/供应商基础文件约 1.5–2 GiB，原生兼容编译包最多 2 GiB（实际以 CI 清单为准）、GUI/PG/字体约 1–1.5 GiB，Swap约1 GiB，访客原始卷512 MiB，上传产物最多450 MiB，再留日志/数据库/维护余量。10 GiB 较紧，不在 VPS 编译；不安装 Claude/编译工具链。APT 缓存/上传包/解包元数据保留供诊断，不自动删除未知文件。只有已知自建目录的清理由管理员逐项审查；不要递归删除 `/opt` 或 `/var/lib`。

## 影响与恢复

会安装 APT 包、建立四个锁定 nologin 身份与专属路径、创建 512 MiB ext4 镜像并挂载、补齐 Swap并写 fstab、启用 IPv4-only、启用 browser veth/network namespace/nft NAT、打开 IPv4 forwarding、写专属 systemd/polkit/tmpfiles，以及添加四个身份的 SSH DenyUsers 后校验并 reload。**IPv6-only SSH 可能失联，不适用本测试入口**。不改管理员密码/公钥，不 flush 原有防火墙；预检发现既有规则即停止。浏览器仅外部 TCP80/443/DNS，拒绝本机/私网管理服务，Xvnc 无公网 TCP。

安装失败打印明确阶段，专属 state.json 与已脱敏 install.log 保留。先查看记录阶段、systemctl status 和 root journal，修复外部包源/资源问题前不重复跑完整安装；已存在安装会直接拒绝。**本阶段没有通用卸载、升级、原地重装或任意阶段自动重试**。

仅在失败阶段为 `start-service`、源码 SHA 一致且全部配置已完成时，可人工诊断后执行有限收尾：

```bash
sudo bash /opt/webdesktop/source/webdesktop-deploy/install-staging.sh \
  --resume-finalize --commit '替换为原40位SHA'
```

该命令只重启/验证后端，不重新建库、格式化卷、覆盖源码或重置凭据。其他阶段失败保留全部数据，优先恢复安装前快照或另建全新测试 VPS，重新核对授权与 Artifact；不要通过删除状态标记冒充“全新”。原 PostgreSQL 初始化标记与恢复代码保留，新 staging 在首次安装前建立专属初始化标记，仅允许恢复本次新建集群，未知旧集群预检拒绝。

Known limitation：Claude 正式入口、网页剪贴板/拖拽、多应用 GUI 共存、完整应用启动器/图标提取、GUI 刷新原进程续接、正式 HTTPS 域名未实施。本阶段停止等待真实测试 VPS 安装授权，不用放宽隔离使未开发功能看起来可用。
