# 独立 HTTPS 子域名访问准备

本文件只定义下一次部署的配置契约，**本轮没有配置正式域名、证书、Tunnel 或生产代理，也没有接入第四阶段安装器**。产品是一 VPS 一用户，普通用户只输入桌面密码；SSH 是管理员维护入口。

## 固定来源

后端仍由 `server.mjs` 监听 `127.0.0.1:3000`；内部 Next 仍使用 wdsvc 私有目录中的 Unix Socket。代理只连接公共网关，不得连接内部 Socket。

单域名配置示例：

```ini
WD_PUBLIC_URL=https://desktop1.example.com
```

需要同时保留管理员 SSH 本地隧道时，显式列出浏览器实际访问的两个来源：

```ini
WD_PUBLIC_ORIGINS=https://desktop1.example.com,http://127.0.0.1:18890
```

`WD_PUBLIC_ORIGINS` 优先于 `WD_PUBLIC_URL`。二者均未设置时仅允许实际监听地址/端口的 HTTP 来源。不会根据浏览器 Host、Forwarded、X-Forwarded-Host 或 X-Forwarded-Proto 自动扩大来源集合。端口属于来源的一部分，不允许通配符。不同子域名分别配置到各自 VPS；这不是同机租户路由。

将配置写入服务端管理的环境，再由管理员在后续获准部署时重启服务。不能让网页请求写入这些变量。正常 HTTPS 使用受信任证书，本轮自动化中的自签名证书仅用于可丢弃测试。

## 代理契约

配置模板见 [nginx-webdesktop.conf.example](templates/nginx-webdesktop.conf.example)。需替换域名和证书路径，不可原样投入生产。代理应拒绝未知 Host，把已接受的固定域名传给网关，并传递 HTTP/1.1 Upgrade/Connection；不得改写成内部 `127.0.0.1:3000` Host。

Tunnel 同样需要外部 HTTPS 与固定公共 Host，回源目标是 `http://127.0.0.1:3000`。回源 Host 必须是配置中的公共域名。没有实施某个 Tunnel 提供商的安装或账号接入，也不承诺其默认配置已经满足该契约。

模板关闭响应和请求缓冲，设置 75 秒读写超时。已认证 PTY/GUI 每 5 秒发送 WS ping，并要求有限时间内的 pong；因此无需靠鼠标移动维持长连接。代理超时不是认证租约，不能在代理层延长失效登录的执行权限。

代理删除 Forwarded 和相关私有头；网关再次删除所有客户端 `Forwarded`、`X-Forwarded-*`、`X-Real-IP` 和 `X-Webdesktop-*`。只有网关根据服务端 HTTPS 来源配置生成的标记可进入受 DAC 保护的内部 Next Socket。Cookie 的 Secure 属性不采用客户端声明的协议。HTTPS Cookie 保留 HttpOnly、SameSite=Lax；HTTP 隧道兼容入口使用自己的来源配置。

IP 限速使用实际回源 TCP 对端，而不是未经验证的 X-Forwarded-For。经本机代理的用户可能共用限速计数；一 VPS 一用户下保持保守行为，未来如需真实来源 IP，应独立定义受信任代理边界，不能直接恢复任意 Forwarded 信任。

## 登录与连接生命周期

- HTTP Host 与 WS Host/Origin 均有固定允许集合。WS 必须同时通过 Cookie 和页面 session 验证；合法 Cookie 不能替代页面 session。
- 退出时条件删除服务器当前桌面 session，然后清除 Cookie；并发新登录不会被旧页面退出误删。数据库失败返回 503，不伪报成功撤销。
- 其他设备接管登录后，旧页面不能继续输入；PTY/GUI 的认证续验与独立守卫会回收执行进程。
- GUI 页面刷新结束旧显示及应用进程，重新认证后创建新显示。保存的 Chromium profile 可保留；当前不恢复原标签页和未保存窗口状态。PTY 的短时重连语义保持。
- 后端、代理或数据库断开时停止授权输入，不回退为 wdsvc 执行。网页剪贴板和拖拽尚未迁移。

## 本轮测试范围

`tests/ws_proxy_runtime.mjs` 经实际 TLS 代理验证 Host/Origin、双凭证、错误和缺失来源、Secure Cookie、伪造转发头及退出撤销。`tests/desktop_ui_runtime.mjs` 经真实 Chrome → TLS → 网关 → noVNC → 访客 Chromium 验证 HTTPS/WSS、文件与 PTY 并发、刷新和其他设备接管。`tests/http_boundary_checks.mjs` 核对私有头净化。

该测试代理不等于正式 Nginx/Tunnel 部署验收；真实 DNS、证书签发/续期、代理配置上线、长时间公网链路与生产回滚仍属于后续获准部署的验收项。
