# 真实VPS验收后的资源与GUI修复计划

起点：feature/private-deploy-isolation，HEAD 4ed395ef66c7b4f436edec990113a0af4238f38c；已实测安装代码f195f90c01f38fcf93c20ee616f9e62a15063018为祖先。用户报告真实Debian12/960MiB/1CPU/1GiB Swap已完成39项live与Windows人工验收；本轮不连接或改动该VPS。

## 设计决定与拟修改范围

1. 新增 `scripts/resource_policy.py`，只由受信任安装过程计算资源和生成root管理的systemd drop-in。检测MemTotal、cgroup祖先memory.max/cpu.max/cpuset、affinity、Swap与磁盘；保存计算依据、版本及显式管理员覆盖。预算在安装时固定，运行时不按瞬时MemAvailable反复调整，也不修改现有部署。
2. 保留平台总量硬界限，为OS/SSH保留余量。新增执行子树汇总限制，PTY/GUI/Chromium共享其余量，避免多个会话叠加挤占后端；认证/数据库/文件Agent获得MemoryLow、CPUWeight与较低OOM优先级，独立租约守卫继续留在用户执行树之外。CPU按实际核/父quota计算，多核不再被固定90%限制。MemoryHigh靠近保护上限，不把可回收缓存的正常增长过早变成节流。
3. 1GiB保留经过验证的小机基础能力；2/4GiB使用可解释公式扩展。硬上限不是预留，子上限不可相加。Swap额度按实际有效容量分配，非主机每个服务各用1GiB。用户文件卷按RAM/可用磁盘计算并保留日志/维护余量，新安装才允许配置大小；不扩容/格式化已有卷。
4. 新增固定路径聚合采样模块及经过Cookie+页面session认证的资源接口。只读meminfo/PSI/限定cgroup计数/statfs，不返回进程命令、环境或任意路径；单飞采样、缓存、响应上限。累计events使用增量并保留local/子树区分；状态机含持续时间、迟滞、冷却与unknown状态。任务栏显示状态、Monitor只显示安全聚合项，任意kill/进程枚举继续拒绝。
5. 网关/worker/前端增加有界阶段耗时与状态，不记录Cookie或access。新高负载GUI启动在真实严重压力下拒绝，已有工作不中断。检查网关握手前等待和重复认证/初始化；保持启动与租约有限超时，不靠延长超时掩盖故障。
6. 修复sys-store复制/剪切仅保存网页文件剪贴板，不隐式调用未迁移VNC桥接。GUI分辨率在受限IPC/协议中校验上限和速率、前端防抖，原生应用默认最大化；不重启Xvnc来响应每次拖动。Chromium只首次初始化安全默认搜索与原生新标签页，保留已有profile/书签/搜索设置。受限文件导航只列访客工作目录。

拟修改：staging_install/check/bundle、层安装器/卷验证、必要systemd drop-in、server/gui-gateway、GUI/浏览器worker、taskbar/Monitor/BasicGui/sys-store/sysfiles/desktop、tests及对应CI。现有安全模板中的身份、挂载、网络、capability、NoNewPrivileges不放宽；原稳定install.sh/Swap/IPv4/PG恢复不改。

## 验证和风险

- 先增加可重复的预算/父cgroup/压力状态机/协议边界测试，再在真实Debian systemd VM验证运行值与不同UID安全。1/2/4GiB分别记录计算结果和真实执行范围，未跑规格必须标not_executed。
- 新staging VM覆盖Chromium冷/热启动时并发PTY、文件与认证响应、压力及恢复、恶意耗内存/CPU/tasks不影响控制链；继续保留原39项和安全回归，不移除失败记录。
- 新模板/文件必须进入固定SHA产物白名单，测试包与运行包不混用，凭据不打包；只推开发分支。最后构建新SHA产物，交付单行部署命令，停止等用户审查。
- memory.min/low不能代替OS总余量和执行汇总硬限额；PSI在无权限/内核不支持时不能报正常。低配实例未必适合重网页，多核/更大预算计算通过不代表实际性能通过。
- 动态RFB尺寸必须验证消息分片、握手和像素上限；配置原生浏览器默认值不得覆盖用户选择。测试若反复时序失败先审夹具，不无限sleep重跑。
