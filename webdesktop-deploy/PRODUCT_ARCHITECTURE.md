# 产品目标与第三阶段架构约束

本文件自 `d3e7d0c` 后的3C开发生效，补充原隔离方案；旧阶段报告保留历史事实。

## 产品形态

一台VPS只服务一个用户。每台机器有独立的WebDesktop、PostgreSQL、密码、文件和应用配置，不与其他VPS共享数据库或身份。未来 `https://desktop1.example.com`、`https://desktop2.example.com` 分别指向不同VPS。普通用户打开HTTPS网址、输入桌面密码即可使用，不需要SSH或GitHub账号；管理员使用独立SSH凭据维护。

同机终端、PCManFM、Chromium和Claude属于同一用户的数据域，可以通过非敏感数据组共享普通工作目录。不是同机多租户系统，不要求这些应用之间建立租户边界。独立UID仍用于保护文件Agent的事务状态、可信租约守卫和服务控制面；不能因此合并到wdsvc或共享其敏感组。

## 不可放宽的边界

- wdsvc负责Next认证及数据库，root管理部署、只读运行包、网络和systemd；访客不得读取、修改、复制私有源码、.git、.env、数据库凭据或Deploy Key，也不能控制这些身份的进程。
- 真实文件操作继续通过文件Agent；PTY继续wdterm；图形应用继续wdgui。所有公开操作先验证Cookie与页面session，固定IPC失效安全失败，无旧后端执行回退。
- 图形应用可共享专用访客X11/Xauthority/D-Bus，但不能接入管理员显示、系统D-Bus或整个宿主/run、/tmp。联网应用使用root管理的网络namespace，拒绝本机全部管理入口、私网和元数据地址；原生user/pid namespace和seccomp必须实测，不用--no-sandbox解决兼容性。
- 普通共享目录不包括Agent私有事务状态、GUI私有profile或服务配置。访客卷仍为512 MiB、nosuid/nodev/noexec；解释执行用户脚本与原PTY语义保持。
- Next默认127.0.0.1:3000，内部Next继续DAC保护的Unix Socket。HTTPS代理/Tunnel显式配置可信来源、保留Host，不信任客户端Forwarded声明。SSH本地隧道保留为管理员/调试兼容方式，不是普通用户访问前提。

## 资源与验收

目标运行环境1 vCPU、1024 MiB RAM、约1024 MiB Swap、10 GiB系统盘；同时保留无Swap对照。原安装器Swap自动配置不变。主机Swap总量与单元MemorySwapMax分开报告，不能假定所有服务都能独占主机Swap。

基础图形、联网浏览器及可信服务分别记账，并设置聚合上限。只在一次性实验环境按320/384/448 MiB阶梯研究Claude，不同时无分析地抬高所有限额。记录RAM/Swap/current/peak/swap.current/events/CPU/tasks/磁盘/交互延迟及关闭后残留；避免OOM但严重卡顿不算可用。保留上轮320 MiB、无Swap的失败日志和报告。

本轮优先可见Chromium、Claude可行性、真实Fcitx输入、GUI生命周期与HTTPS模板。不自动开放Claude入口，不全面迁移剪贴板/拖拽，不进行第四阶段安装器迁移或生产部署。只开发feature/private-deploy-isolation，main、Beta、生产数据/密钥/可见性均不变。
