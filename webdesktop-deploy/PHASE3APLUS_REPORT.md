# 3A+ 运行时验证与 3B 门槛

本轮在 `feature/private-deploy-isolation` 开发，3A 验收基线 `54da753`。3A+ 初始实现提交 `a7e6a59`；[真实 CI 39243772455](https://github.com/isAliceYoung/fa/actions/runs/39243772455)。本文在最终交付前补齐虚拟机结果；在 booted systemd 和 WS 两项均通过前，不开始 3B 文件代码。

## WebSocket 边界

删除 guest-ws 的 `_getActiveHandles()`、轮询、监听器移除和 on/once 等方法改写。采用 `server.mjs` 显式公共 HTTP/Upgrade 入口，同进程中的 Next.js 只绑定独立随机 loopback HTTP 端口。普通 HTTP 以 Node 流转发，背压/取消沿连接传播；公共 Upgrade 永不转发到 Next 的 Upgrade listener。

三个旧 WS 地址只调用内部固定 `/api/guest/ws-reject` HTTP 路由，由现有 denyDesktop 真实验证 Cookie+页面 session；无效 401，有效但尚未实现 503，数据库异常或认证超时 503。5 秒握手总时限，任何异常关闭连接，不发 101，不加载旧 terminal/gui/browser 执行库。未知 Upgrade 路径也拒绝。

Next 可以重复、延迟注册自己的内部 listener，不影响公共拒绝入口。测试在真实 Next 的内部 Server 上重复/延迟注册 sentinel，若收到 Upgrade 即记录失败；未将内部实现或任意生产测试开关开放给请求参数。独立入口增加一次同进程 loopback HTTP 转发；未额外启动第二个 Node 进程。开发 HMR WebSocket 同样暂不开放，未来需要明确单独路由；不借此恢复旧桌面 WS。

package.json start/dev 和 scripts/start.sh 改用该入口；保留端口校验、既有监听者核对与 service 安装流程。PostgreSQL Pool 增加 idle error handler，数据库停机不因未处理 EventEmitter error 导致 Node 退出，不打印凭据。认证算法与数据库数据格式未修改。

`python3 tests/linux_ws_runtime.py`：**7/7 通过、0 失败、0 错误、0 跳过，退出 0**。每类覆盖三个真实 TCP Upgrade 地址；包括无认证、错误 Cookie、错误页面 session、真实新登录撤销旧 session、有效 session、真实 PostgreSQL 停止/恢复、重复/延迟 listener、Node 重启。原真实访客 18/18、源码权限 9/9、SSH parser 3/3、常规 150/150 和构建均通过；Windows Git Bash 常规 150/150、退出 0、无跳过。

## systemd VM 方法

新增独立 CI job，下载 Debian 官方 Bookworm genericcloud 镜像并校验 HTTPS 发布的 SHA512。QEMU 创建一次性 1 GiB RAM、10 GiB 虚拟磁盘，不连接任何已有 VPS。由临时 cloud-init 配置创建测试管理员和测试 SSH host key，SSH 严格核对预置 host public key；私钥随临时目录销毁、不进 Artifact、不使用生产密钥。

VM 中必须 `/proc/1/comm=systemd`，实际启用 socket 并触发 service；运行 systemctl status/show、systemd-analyze verify/security，记录命令、输出、退出码与 mountinfo。nsenter 仅为 root 测试仪器，进入实际服务 mount namespace 后再降为 wdguest 检查 HOME 可写和保护路径不可读，不由 agent 执行高权限命令。

测试真实 socket UID/GID/mode、不同 Linux 用户连接、实际进程四类 UID/GID、capabilities、NNP、ProtectHome tmpfs/BindPaths/只读系统、崩溃/终止/启动失败及恢复。随后 VM 真正 reboot，确认 boot ID 改变、运行目录重建、socket 激活和管理员 SSH 再次有效。`security` 的评分是检查结果，不是“绝对安全”的证明。

Artifact `booted-systemd-debian12` 保存镜像来源/哈希、serial、journal、systemctl/analyze 记录、前后 boot ID 和两轮测试 JSON。机器结果不提交仓库。

真实 VM 的初次有效失败（提交 30b2c9b，[39246226789](https://github.com/isAliceYoung/fa/actions/runs/39246226789)）为 7 项中 2 通过、5 错误、0 失败、0 跳过，退出 1；重启轮未执行。更早的 a7e6a59 运行因测试 socket 没设超时被 CI 25 分钟取消，不计通过。journal 明确显示 openat2 返回 ENOSYS，原因是 RestrictSUIDSGID 的 seccomp 规则，而非内核太旧。修正为明确 RestrictSUIDSGID=no、native ABI；仍强制原 openat2 路径边界、NNP、空能力、独立 UID、只读系统/隐藏源码/私有网络等，不降级为 openat。文件 API 不提供 chmod/SUID 创建，受控创建模式 0600/0700；后续持久访客卷还须 nosuid/nodev。参考 [systemd 官方说明](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml) 与 [对应 seccomp 实现](https://github.com/systemd/systemd/blob/main/src/shared/seccomp-util.c)。测试客户端补充 Unix socket 自身的超时，不能仅给 HTTPConnection 设置超时后再替换 socket。

## 当前文件与后续设计

新增：`server.mjs`、`src/app/api/guest/ws-reject/route.ts`、`tests/ws_runtime_entry.mjs`、`tests/linux_ws_runtime.py`、`tests/run_systemd_vm.py`、`tests/debian_systemd_vm.sh`、`tests/linux_systemd_runtime.py`、本报告、`PTY_GUI_NEXT_DESIGN.md`。

修改：`.github/workflows/validate.yml`、`package.json`、`scripts/start.sh`、`src/instrumentation.ts`、`src/lib/guest-ws.ts`、`src/db/index.ts`、`tests/linux_guest_layer.py`、`tests/test_guest_contract.py`。原 IPv4-only、Swap、PostgreSQL 首次恢复脚本及取消欢迎/示例文件逻辑未修改。main/Beta 未移动，无生产操作、自动更新、生产目录迁移。

PTY 的创建/关闭、双向帧、resize、刷新重连、撤销租约、cgroup 子进程清理、背压与资源限制，以及独立 GUI/browser 网络/X11/D-Bus/沙箱设计见 [PTY_GUI_NEXT_DESIGN.md](PTY_GUI_NEXT_DESIGN.md)。这些是后续方案，不声称已恢复 PTY/GUI。旧三个 WS 与 GUI/浏览器 HTTP 仍明确禁用。
