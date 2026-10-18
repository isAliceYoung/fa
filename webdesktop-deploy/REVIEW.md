# 首轮审查修订记录

这是上一轮针对 `8dadf86` 的记录。最新的恢复行为、验收范围和同步方式以 [RECOVERY_NOTES.md](RECOVERY_NOTES.md) 为准。

基于本地仓库提交 `8dadf86`，包含提供的五文件基础修复及以下补充。

## 已处理

- 显式安装 `iproute2`，保证 `ss` 可用。
- 小内存机器发现配置了 swap 但未启用时停止并提示检查，而不是静默跳过。
- 安装和查询使用现有 `/api/health`，结合数据库连接和登录记录检查。
- 安装完成信息不再打印密码查看命令；仅说明配置文件位置。
- 移除对 `package-lock.json` 的忽略并生成锁文件；首次安装只使用 `npm ci --include=dev`。
- 安装模式为 `auto`、`install`、`repair`。默认重跑保留已有应用构建、依赖和服务；不会先停止应用再尝试 npm 安装/构建。没有可用构建的活动服务、目录不匹配或配置缺失时停止，避免误操作。
- 取消公开默认管理员密码；首次初始化缺少 `ADMIN_PASSWORD` 时沿用已有的报错逻辑。
- 补充根目录和部署目录的换行规则，确保 Shell、Python、systemd、YAML 等使用 LF。
- 增加 GitHub Actions：脚本检查、重复部署保护测试、使用 PostgreSQL 15 的独立构建和健康检查。

## 本地验证

- 依赖锁定成功；执行 `npm ci --include=dev --ignore-scripts` 安装成功。
- Windows / Node.js 24.15.0 环境下 `npm run typecheck`、`npm run build` 通过。
- 八个部署保护测试通过，检查了已有文件保持不变且没有停机、重启或 npm 安装调用。
- Bash、Python、Node 辅助脚本语法检查通过。
- 锁文件与 `package.json` 匹配，并包含 Linux AMD64/ARM64 所需的可选构建依赖。

本地依赖安装跳过了生命周期脚本，因此这次构建结果不能替代 Debian 原生依赖安装和图形程序测试。CI 使用 Node.js 22.23.2 并正常执行依赖安装脚本；新工作流需要提交并推送后才会真正运行。

## 本轮边界

当前的重复部署保护是“保留应用、修复组件”，没有实现在线升级。真正升级仍需要独立构建目录、版本切换和回滚机制；不要删除保护逻辑后直接覆盖运行中的 `.next`。

Chromium 的 GUI 和无头路径仍有 `--no-sandbox`。Claude `.desktop` 是否绕过包装器、ARM64 包兼容性以及 Fcitx5 实际输入，均需要新 VPS GUI 会话验证。本轮没有未经测试地更换它们。

APT 更新系统组件时仍可能重启 PostgreSQL 等组件，所以 `repair` 不是全系统不停机保证。代理、Cloudflare Tunnel、域名和 Windows CLI 不在本轮范围内。

## 应用修改

完整补丁已经包含原五文件补丁，不要在同一工作区先应用旧补丁再应用完整补丁。

在 Windows 的原仓库中，将路径换成下载的完整补丁位置：

```powershell
cd F:\des11
git apply --check F:\webdesktop-review-complete.patch
git apply F:\webdesktop-review-complete.patch
git diff --check
git diff --stat
```

检查、提交并推送这些修改后，再使用全新 Debian 12 测试 VPS 执行安装和查询。安装器仍应作为待实测版本，不能把本地构建或静态检查当成正式 V1 验收。
