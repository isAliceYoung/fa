# 3C GUI 与浏览器准备设计（未实施）

本轮只实现 3B-PTY；没有启用 GUI、Chromium、Claude、Xvnc 或 Fcitx5 服务。下述为进入 3C 的依赖，不是验收结果。测试终端 UI 使用 CI 宿主机的沙箱 Chrome，不代表在 1 GiB VPS 中同时运行这些访客应用。

## 身份与进程模型

拟新增独立 `wdgui` 身份，和 wdsvc、文件 Agent 的 wdguest、PTY 的 wdterm 区分。共享用户数据仅通过无敏感配置权限的访客数据组；不得加入 wdsvc、sudo、postgres 或管理员的组。wdgui 需要明确添加到文件 API 可接收的固定所有者集合，未经测试不能把任意 UID 都当作访客数据。

Xvnc/Openbox、Fcitx5、独立用户 D-Bus、Chromium、Claude Desktop 由 root 管理的 GUI 单元启动，固定最小发行包只包含桥接、公开启动配置和必要应用运行文件；没有 Next、认证/DB 模块或服务 .env。启动路径、环境、Xauthority、D-Bus 地址固定，不继承后端 DISPLAY、Cookie、AUTH_KEY、DB 或 SSH_AUTH_SOCK。每个桌面一个有界 cgroup，退出/租约撤销时整个进程树回收。

文件 Agent 的 96 MiB、PrivateNetwork、AF_UNIX 继续保留。GUI 不复用该单元，也不能为了应用兼容直接取消所有 systemd 限制。PTY 用户不能通过 GUI 运行时 socket、D-Bus 或传递 FD 绕过会话生命周期；不能让长期 GUI 会话保留 PTY 的 activation/租约 FD。

## 通道和网络

| 组件 | 必需通道 | 约束与待测项 |
|---|---|---|
| Xvnc/Openbox | 私有 X11 Unix Socket、仅 GUI 用户持有的 Xauthority；远程图像流固定 IPC | 不监听公网 VNC/TCP X11，不接管理员显示服务；backend WS 仍先认证 |
| Fcitx5 | 同一访客 X11、会话 D-Bus、XMODIFIERS/GTK_IM_MODULE/QT_IM_MODULE | XIM/GTK/Qt 实际输入与重启恢复；不挂载系统 D-Bus 管理 socket |
| Chromium/Claude | DNS、对外 TCP/UDP、GUI X11、用户 D-Bus、有限 /dev/shm | 独立网络 namespace，阻止所有主机/loopback/私有和 link-local 管理地址；不能只隐藏 Next 端口 |
| 剪贴板/拖拽 | 有界文本/图像 IPC、访客文件路径 | 图像解码和格式转换在受限解码进程/GUI UID，禁止恢复 backend sharp/exec 文件路径 |

对外网络需 root 提供 veth/NAT 与默认拒绝规则，并覆盖主机所有已分配地址、IPv4/IPv6 映射、DNS 重绑定、经公网地址折回主机和 Unix Socket。不能只设置 HTTP 代理或防火墙 loopback 一条规则。继续尊重既有 IPv4-only；未获授权不重新启用 IPv6。真实测试必须连 Next 私有 Socket、PostgreSQL TCP/Unix、sshd、VNC、D-Bus 及本机其他管理端口；任何未覆盖服务都应默认拒绝。

## Chromium/Electron 原生沙箱

默认不使用 `--no-sandbox` 或关闭 renderer sandbox 的等效参数。先在 Debian 12 中验证原生 user namespace/seccomp sandbox，再确定仅 GUI 单元所需 namespace 许可；不改变文件 Agent/PTY 的 namespace 限制。NNP 与空能力继续保留，不能为 SUID helper 给访客广泛特权。若 Electron/AppImage 依赖 FUSE 或可执行用户卷，优先由 root 校验/提取到独立只读运行目录；不把私有后端包复制给它，也不直接解除访客卷 noexec。

必须分别记录 Chromium 与 Claude/Electron 的 sandbox 进程、UID、namespace、seccomp、崩溃、渲染器被终止后的清理和网络拒绝证据。检测参数和进程状态，不能只凭“窗口能打开”宣布沙箱有效。

## 512 MiB 卷与低配机器

当前卷还承担文件、上传分片、复制暂存、回收站和 ZIP。100 MiB 文件复制/上传或 ZIP 可同时占用源与暂存，GUI profile 和 Electron 用户数据还会继续增长；**未证明 512 MiB 足够长期同时存放 Chromium 与 Claude 数据**。本轮不扩大或重格式化它。

3C 先测空 profile、首次登录、单标签/多标签、下载、缓存增长、崩溃恢复、升级残留、ENOSPC 与 inode 耗尽，再决定子目录配额或独立有限卷。缓存启动参数不能代替内核容量限制；如需新卷或调整总容量，先提供数据和预算供审查。默认只考虑单 GUI 桌面与单浏览器实例，不承诺 Chromium 和 Claude 同时畅用。

1 GiB 内存需容纳已有 Next、PostgreSQL、文件 Agent、PTY、Xvnc/Openbox/Fcitx5 与浏览器。当前 PTY 峰值测试不能代替 GUI 峰值。需分别测单应用、组合、标签增长、图像/剪贴板压力，保留既有 Swap 且明确延迟；整机 OOM 或严重换页则降低并发或功能预算，不能默默解除 cgroup 限制。10 GiB 磁盘还需留操作系统、私有应用、构建/恢复空间和 Swap；浏览器/Claude 运行文件及其更新必须计入。

## 开始 3C 前的门槛

先审查 3B-PTY 的全部真实测试和阶段报告；确认明确的联网策略、独立 GUI UID、沙箱可行性及磁盘/内存数据，再开放 GUI WS。剪贴板、拖拽、图片转换、浏览器截图/音视频与应用 launcher 必须逐条从旧高权限实现迁出。仍不进入第四阶段生产迁移或一键安装器切换。
