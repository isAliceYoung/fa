# Phase 4A 候选部署入口：仅已验证功能子集

本入口不是第 3 阶段全部完成或完整生产安装器通过验收的声明。原计划正式阶段仍以 ISOLATION_PLAN.md 第 7、9、10、11 节为准；4A/后续子阶段只是本轮执行管理标签。稳定 `install.sh`、main 与 Beta 不替换。当前用户 VPS 仍是 `131ea566...`，不能把新代码或 CI 当成已部署。

## 独立入口与共用实现

`install-isolated.sh` → `scripts/isolated_deploy.py` 是明确 opt-in 的候选入口。首次安装调用 `staging_install.Install` 的同一个安装引擎，固定 Layout 选择 isolated。staging 原入口仍只选择 staging；不接受用户自定义 UID、systemd 名称、安装路径、命令或环境。

| 部分 | staging | 4A 候选 |
|---|---|---|
| 状态/日志/种子/清单 | `/etc/webdesktop-staging`，root0700 | `/etc/webdesktop-isolated`，root0700 |
| 后端服务 | webdesktop-staging.service | webdesktop-isolated.service |
| 已验证根目录 | `/opt/webdesktop/source`、app | 相同，不允许两种安装共存或互相收养 |
| 首次源码 | 已核验 Artifact 内的 Git bundle | 必须先经既有 bootstrap 获得的 root0700 私有 checkout，与 Artifact 同完整 SHA |
| 访客层 | 既有 Agent/PTY/GUI/browser 单元、最小运行包 | 完全复用，不另建执行体系 |
| 资源公式与上限 | resource_policy.py | 同一公式，只映射后端单元名；不扩大限制 |
| 恢复 | 原有限 finalize | 固定文件/身份/单元/网络记录核验，保留凭据与数据 |

4A 首次安装只认可父目录中唯一的已登记 bootstrap `source`；其 `.git/webdesktop-source.json`、真实 origin、分支、完整提交及干净状态必须一致。私有仓库授权、只读 Deploy Key、GitHub 主机密钥由原 `bootstrap.sh prepare/check/clone` 负责，继续严格 host checking；4A 不再生成密钥，不自动获取源码、不现场构建。

取得引导 checkout 前应独立可信获取 bootstrap；其默认源码位置即 `/opt/webdesktop/source`。构建在隔离的 Debian12 CI 完成，仅使用一次性测试数据库/种子；产物经现有清单和秘密扫描器校验。管理员必须从可信 CI 独立核实完整 SHA 与内部 tar.gz SHA256，不能从下载文件自身推导信任。尚没有新增签名/attestation 或本机 wdbuild 构建通道，不声称满足全部最终供应链/构建发布目标。

## 模式与可恢复范围

| 模式 | 全新机器 | 已登记安装 | 禁止/限制 |
|---|---|---|---|
| auto | 必须 hostname 授权、`--accept-verified-subset`、管理员清单、完整 SHA、Artifact、独立摘要；复用完整预检/安装 | 完整校验并恢复已登记服务 | 不迁移、不更新源码/产物、不过期切 HEAD |
| install | 同 auto | 健康时只校验，停止时按既有配置恢复 | 不重新生成密码、建库、格式化或覆盖 Profile |
| service | 拒绝，没有已知安装就停止 | 验证 root 产物/来源、身份、配置/单元/Socket、实际网络状态和依赖后启动已知 Socket/后端 | 不 APT/npm/build/Git fetch/clone/pull/key/host trust；不 daemon-reload、不 enable、不 SSH reload、不改 IPv4/Swap/挂载/网络 |
| repair | 拒绝 | 上述核验；仅允许重建**缺失的**已冻结后端单元，daemon-reload 后验证并启动 | 改动过的单元/配置不覆盖；缺包、网络、卷、数据库故障不自动接管/重建 |

首次安装完成配置和单元后，root 保存 `recovery.json`：非敏感文件哈希/属主/模式、固定源身份、身份表、实际单元来源与 drop-in、网络规则摘要和路由。它不是 root 被攻破后的防篡改签名；不能删除或重新生成以收养未知安装。

仅已启用后台、最终 `start-service` 激活阶段的失败或中断可恢复；enable 本身失败或管理员 disable 时明确停止，不在 service 中偷偷 enable。此前部分安装、损坏包、失配配置、异常 UID/组、失踪网络/挂载/数据库依赖均停止，保留全部数据及阶段日志，要求独立审查/快照恢复。并不提供通用安装断点续跑、卸载、原地升级或旧数据迁移。重装必须另行授权全新系统，不能删 state 来伪装全新机器。

Python 输入/清单/权限/状态检查改为不可被 `-O/-OO` 移除的运行时验证；共享层授权与卷预算读取同样不依赖 assert。

## 范围及副作用

首次安装会安装 APT 依赖、保留原 IPv4-only 行为、按计算补足 Swap/fstab、创建独立身份、为四身份添加经有效配置验证的 SSH 拒绝规则并 reload SSH、创建有界数据卷和 cgroup、安装已验收 systemd/polkit、创建浏览器 veth/nft/namespace。只在明确授权的新机器做这些操作；当前任务不连接真实服务器。

默认后端 127.0.0.1:3000；VNC 无公网 TCP、PG 仅 loopback、内部 Next UDS；运行进程 wdsvc/wdguest/wdterm/wdgui 各自独立。配置、数据库和访客数据保持原隔离。随机登录种子仅保存在受保护目录，不打印；不存在可猜测默认密码。自动更新仍关闭。

已有单账号布局、staging 布局、未知用户/单元/数据/规则一律拒绝；不隐式 chown、删除、清库或转换原卷。首次权限发布只处理新解包的 app；既有 bootstrap checkout 保持 root-only。

未迁移 GUI/Playwright/剪贴板/拖拽/应用命令仍拒绝（HTTP 双认证后 503，旧 WS 拒绝），不因新入口开放。Claude、多 GUI 共存、下载衔接、正式域名、整日 soak/容量增长仍未验收。HTTPS 生产访问、完整功能体验及生产升级必须另行审查，不能因本子集安装成功而声明完整 Phase 3C/4 完成。

## 测试与使用边界

`check-isolated.sh` 复用同一个只读/明确 live 健康控制器；live 会替换当前登录，需显式确认标志，不默认运行。完整参数可只读查询：

```bash
sudo bash /opt/webdesktop/source/webdesktop-deploy/install-isolated.sh --help
sudo bash /opt/webdesktop/source/webdesktop-deploy/check-isolated.sh --help
```

本轮只交付代码和一次性 VM 测试。不要在现有 Evoxt 或 staging VPS 上尝试新入口，不提供把现有服务器就地更新的指令；用户独立审核并授权后才能为全新机器给出绑定受测产物的完整安装命令。

CI 的 `tests/run_systemd_vm.py --isolated` 复用既有 QEMU/controller 和 staging driver，新增四规格矩阵。root 的测试 provenance、管理员公钥、`phase4a_fixture` 表、测试 Deploy Key 替身、工具拒绝包装器、故障注入仅由 tests 创建，不在普通安装入口调用，也不复制到访客运行包。常规私有 Git 测试只用模拟授权/临时测试密钥，不能称生产私有仓库现场授权已执行。

验收结果、固定受测 SHA、报告 SHA、实际失败及未执行项目以 PHASE4A_ADAPTATION_REPORT.md 为准。
