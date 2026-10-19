# 3B/3C 前调用路径及依赖清单

3A 验收后已完成 3A+ 并进入首个 3B 文件里程碑，当前 API/限制见 [PHASE3B_FILES_REPORT.md](PHASE3B_FILES_REPORT.md)。下表列出原调用链及迁移状态；未迁移的 HTTP/WebSocket 明确拒绝，不会仍以 wdsvc 执行。旧库保留用于后续审查，不被新文件 API 导入。

| 功能/前端入口 | 原调用链与权限操作 | 后续接口/依赖 |
|---|---|---|
| 真实终端 `apps/systerm-app.tsx` | `/api/term/ws` → `browser-ws.ts` → `term-ws.ts:termUpgrade/createSession` → node-pty Bash、cwd、进程组信号、断线/续验 | guest 端 PTY、二进制/JSON WS 桥、resize/kill/重连、认证失效后会话处理；node-pty 原生模块需独立打包 |
| GUI `apps/gui-app.tsx` | `/api/gui`、`/api/gui/[sid]`、`/api/gui/ws` → `gui.ts:createGui/startSession/guiUpgrade/stopGui/openUrl` → Xvnc/Openbox/dbus/Bash/应用、TCP VNC、组信号 | guest 全会话管理/本地画面桥；AF_INET/AF_INET6、D-Bus、X11、Fcitx 需要新沙箱评审，不能直接解除限制 |
| 浏览器 `apps/browser-app.tsx` | GUI preset browser → Chromium、持久 profile；极速模式 `/api/browser/*`、WS → `remote-browser.ts:createSession/handleInput` → Playwright/CDP、下载、启动/停止 | guest BrowserSession 协议、tabs/URL/frame/meta/input、下载目录；Playwright/Chromium/tiles/sharp 依赖，不能携带服务 process.env |
| 应用列表/图标/快捷方式 | `/api/gui/apps`、`icon`、`shortcut` → gui.ts listApps/findIcon/createShortcut/parseDesktop → `.desktop` 读取、图标路径、启动命令 | guest 端系统公共目录只读策略和图标服务；用户 .desktop 仅访客权限，不能特殊升级 |
| 文件复制/移动/删除/回收站 | 原 sys.ts 调用已由 `guest-api → guest-client → file_ops.py` 替换 | 3B 已实现固定文件事务；跨设备拒绝保留源，部分完成明确报告 |
| 分块上传 | 原 backend createWriteStream 已替换为受认证二进制 IPC | 3B 已实现独占暂存、提交偏移、断点状态/取消、原子发布、浏览器重试与取消；均 guest 操作 |
| 下载/ZIP | 原 backend createReadStream/python spawn 已替换为 guest FD 流 | 3B 已实现，含递归/链接/字节预算和 ZIP 磁盘暂存；不由 backend 读取路径 |
| 全局剪贴板 | `/api/gui/clipboard` → gui.ts getServerClip/setServerClipFiles/readVncClipboard/prepareClipPayload | guest 端 clip 状态与路径、MIME、cut/文件 URI，不接受服务文件路径 |
| VNC 粘贴/拖拽 | `/api/gui/[sid]/clipboard`、`drop` → setClipboardFilesForSession/setClipboardUploadedFile/triggerVncPaste/dropFile → python GI、xclip/xdotool | guest 会话归属、DISPLAY/临时目录、真实请求大小、取消、与上传/导出一致的路径策略 |
| 图片转换 | gui.ts ensurePngForFile → sharp 或 convert ImageMagick；CLIP_TMP_DIR=/tmp/wd-clip-files | guest 图片解码、尺寸/像素/内存/超时限制和隔离缓存；不能把任意路径交回 backend 转换 |
| 环境与辅助脚本 | proc-env.ts cleanEnv 黑名单/ensureBin → open/gui/wd-clip.py/bashrc；remote-browser.runCli 继承 process.env | 白名单环境、独立 guest 包；禁止导入 auth/DB，不复制整个 node_modules/Next 源码，禁用运行时 sudo/APT |
| 监控和进程结束 | `/api/sys/info` 非 lite → procfs.ts sampleSystem/snapshot，读取 /proc；`/api/sys/kill` → process.kill | guest 自己管理的 PID/进程身份控制、PID 重用、整机公共资源汇总与明细脱敏；不能仅排除当前 PID |
| WS 帧/画质/连接管理 | browser-ws.ts / tiles.ts、gui.ts 原背压/心跳/持续认证 | 保留已有帧协议、取消/拥塞控制；backend 继续做真实认证，guest 不接收 Cookie/token |

## 已关闭入口及剩余风险

`src/app/api/{gui,browser}/**/route.ts`、sys/kill、sys/info 非 lite、sys/fs 的 pasteFromVnc 仍先认证再返回 GUEST_FEATURE_PENDING / 503。三个原 WebSocket 地址由显式 server.mjs 入口与真实 Next 认证路由拒绝，启动过程不加载 browser-ws/term-ws/gui/remote-browser；没有句柄扫描或改写 HTTP 事件注册方法。

旧 lib 文件仍包含同 UID spawn、sudo 自动安装和环境黑名单等不安全实现，但不在当前对外执行链中。后续禁止单纯重新导入这些库恢复功能，必须先按此清单整体迁移到 guest UID，增加真实 Linux/GUI 测试并审计分发产物。

3B 恢复访客 HOME 内文件操作，整机目录浏览、PTY、GUI 和浏览器仍未开放。剪贴板中文件路径粘贴可走文件 IPC，但 VNC 全局剪贴板尚未迁移。当前不是完整桌面或可直接合并部署的最终版本。

## 最小依赖顺序

1. 验收 3A 的真实 UID/IPC/鉴权/DAC 与故障测试，以及独立运行包边界。
2. 先完成 guest 文件服务完整读写/临时文件/导出协议，统一路径与流语义。
3. 再迁 PTY 和会话认证撤销/重连，完成 guest 端原生依赖构建。
4. 迁 GUI/浏览器及图片/剪贴板/下载的全部路径；逐项恢复网络、D-Bus/X11 等必要沙箱能力，测试 Fcitx 与原应用兼容。
5. 第四阶段才处理真实发布目录、只读代码/缓存布局、一键安装衔接和经过备份验证的迁移；不以提前给 guest 读源码/服务配置来解决兼容问题。
