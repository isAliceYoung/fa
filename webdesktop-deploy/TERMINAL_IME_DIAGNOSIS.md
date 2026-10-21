# Windows 终端 IME：第一阶段诊断记录与实测补充

本文前半部分保留修改前的诊断快照（其中“待收集”“尚未修改”指当时阶段），后附真实用户记录与最终方案；完整回归结果以随后提交的 `TERMINAL_IME_FIX_REPORT.md` 为准。

起点：开发分支 `feature/private-deploy-isolation`，`c0defa78822c418e62b5655a5508f59d19690527`。已验收运行代码 `131ea566859d837473383425eac2b30bdb105282`。本阶段尚未修改正式终端、依赖或 VPS。

## 证据边界

用户已在真实 Windows 搜狗/微软输入法、Chrome/Edge 与记事本/终端对照中发现搜狗候选确认后中文消失；用户捕获到最终 insertText/beforeinput/input，但现有证据不能断言 compositionend 缺失或所有 onData 均缺失。

代理本机有 Chrome 和 Edge，可运行真实浏览器/xterm.js；未发现运行中的搜狗进程或默认安装目录。以下自动化通过 CDP 输入管线和明确标记的合成 DOM 事件驱动，**不是实际搜狗或微软拼音人工验收**。真实用户 A/B 记录待收集。

## 本机独立 A/B 页面

仓库根目录单行启动命令（Node 和 npm 依赖已准备）：

```powershell
node tests/terminal_ime_lab.mjs
```

访问 `http://127.0.0.1:18891`。固定 loopback 绑定、固定静态文件白名单及同源 WebSocket；只有回显，没有 Shell、PTY、数据库或 VPS 连接。A/B 均加载实际 npm 的 xterm.js 6.0.0，仅 screenReaderMode 分别为 true/false。页面不属于 public，不加入运行安装包，也没有正式桌面键盘诊断开关。

只输入固定词“你好”、固定测试标点/emoji；不要输入密码或真实命令。在 A 输入 nihao 空格选词，再在 B 重复，复制脱敏 JSON。保存后重置，分别对照数字 1、Enter 确认、微软拼音。每一轮说明输入法和确认方式，避免覆盖上一轮记录。

元信息包括 compositionstart/update/end、keydown/keyup、beforeinput/input、onData、发送帧与回显；只保存长度、汉字标记、是否等于固定测试词、选择区、textarea 长度、仅在诊断页读取的 xterm 私有状态。keydown 只记录按键类别，不保存字母或完整按键序列；最多 256 条事件/终端。不读取 Cookie、Session、剪贴板历史、环境或用户文件。只有用户点击复制时才写入本轮脱敏记录。

## 原版浏览器重放结果

自动化入口（每条均为单行命令）：

```powershell
node tests/terminal_ime_diagnose.mjs
```

Windows 可用 IME_TEST_CHROME 指定另一已安装浏览器，Linux 自动寻找系统 Chrome/Chromium。本机 Chrome 和 Edge 各产生 12 组记录，原版行为断言错误均为 0。这里的“断言成功”表示稳定观察到原版行为，**已复现的丢失不能算兼容性通过**。

| 重放序列 | 驱动 | A true 的“你好”提交次数 | B false |
|---|---|---:|---:|
| 直接已确认 insertText，无组合事件，keyDownSeen=false | Chromium CDP | 0 | 1 |
| imeSetComposition 后 insertText，正常 compositionend | Chromium CDP | 1 | 1 |
| 原 textarea 有三个字符，组合整体替换，正常 compositionend | 明确合成 DOM | 0 | 0 |
| 组合已开始，最终非 composing insertText，但无 compositionend | 明确合成 DOM | 0 | 1 |
| 按住 Shift 导致 keyDownSeen=true，再 insertText | Chromium CDP | 0 | 0 |
| 组合取消 | Chromium CDP | 0（预期） | 0（预期） |

第一条脱敏事件：beforeinput/input 均为 insertText、length=2、containsChinese=true、isComposing=false、composed=true、keyDownSeen=false。A 没有 onData/输入帧；B 恰好各一次。正常组合路径则两边均出现 compositionend 及一次完整输入帧。因此已确认原版存在至少两种不同的丢失机制，仍需用户记录判断实际搜狗命中哪一种。

原始机器记录保留在 `.test-results/terminal-ime-baseline.json` 和 `terminal-ime-baseline-edge.json`，不长期提交机器生成结果。正式 CI 接入后应通过 Artifact 保存。

## 源码与上游核对

- `src/components/desktop/apps/systerm-app.tsx`：保持 screenReaderMode=true；onData 经 terminalInputFrames 转发，未发现按中文过滤。
- `src/components/desktop/terminal-input.mjs`：4096 UTF-16 单元分帧，边界不拆分有效代理对。本阶段未改该限制。
- 本地 npm 锁定安装版本 6.0.0；2026-10-21 查询官方 npm dist-tags：latest=6.0.0、beta=6.1.0-beta.315。没有进行依赖升级。
- `node_modules/@xterm/xterm/src/browser/CoreBrowserTerminal.ts:1196`：insertText 直通同时受 screenReaderMode、keyDownSeen、composed 和 keyPressHandled 约束。上述 CDP A/B 已直接证明辅助模式条件可拦截这类已确认输入。
- `CompositionHelper.ts:63` 在 compositionstart 记录 textarea 长度；`:170` 的延迟 finalize 从旧偏移截取新 textarea。当 TSF 整体替换旧内容时，即使存在 compositionend 仍可能截断。该机制不等同于“compositionend 缺失”。
- [上游 #3679](https://github.com/xtermjs/xterm.js/issues/3679) 提供历史 Sogou 平台事件差异，但不能代替当前浏览器实测。[#6049](https://github.com/xtermjs/xterm.js/issues/6049) 讨论整体替换截断。[PR #6060](https://github.com/xtermjs/xterm.js/pull/6060) 尚未合并，涉及另一套待提交组合协调，不能直接当官方稳定修复升级。

下一步：依据用户的脱敏搜狗记录选定最小修复，优先保留无障碍支持；不能仅凭上述两类可构造缺陷宣称已定位用户当前故障。本阶段未运行完整 CI 或生成新部署产物，未执行真实搜狗修复验收。

独立页面实际 DOM 对照：A 有 `.xterm-accessibility-tree` 与 `[aria-live]`；B 两者均无。因此永久关闭 screenReaderMode 会明确移除 xterm 提供的辅助阅读结构，不能当作无代价的兼容开关；是否使用真实屏幕阅读器可用还需专门人工验证，未执行。

## 真实 Windows 对照与结论（2026-10-22 补充）

用户在 Windows Chrome 155 + 搜狗输入法提交固定“你好”的完整 A/B 记录已收到。A 的事件 10/11 分别为 beforeinput/input（43822/43826 ms）：可信 insertText、isComposing=false、length=2、keyDownSeen=false，textarea 从 0 增至 2；这轮没有 compositionstart/update/end 或 keydown，最终没有 onData 或输入帧。B 的同等提交在事件 9/10 出现一次 onData/输入帧，事件 12 正常回显。现在可以确认用户实际命中 screenReaderMode 禁止 composition-free insertText 的分支，而不是仅从早期截图推测组合事件缺失。

修复保留 screenReaderMode=true，使用公开 textarea、element、onData 和 input API 建立局部兼容桥。只接受已聚焦且允许输入的终端上，可信且成对匹配的非 composing insertText；已有 keydown/keypress、活跃或正在结束的组合均由 xterm 原路径处理。观察到 xterm 已发送数据时不补发。最多暂存一次 16384 UTF-16 单元的无控制字符提交，使用一个可取消任务，不重试、不记录按键内容。断线、失焦、销毁取消待发内容，后续控制键前先完成已经确认的提交。原输入帧、WS、PTY 权限和背压限制保持不变。

第一候选修复的真实测试：搜狗空格（事件 8–13）、数字 1（19–24）各有一次中文 onData、帧和回显；微软正常组合路径的空格（109–118）、数字 1（150–159）各提交一次。搜狗未选词直接 Enter 由输入法提交的是 5 字符原始拼音，第一候选错误过滤 ASCII，事件 30/31 没有 onData。这项失败保留在 `tests/fixtures/terminal-ime-native-trace.json`，不能当作成功或凭空补成汉字/回车。微软同样的 Enter 原始拼音由正常 compositionend 路径发送一次（191–199）。

最终候选移除了 ASCII 过滤，仍保留可信事件、确认状态、匹配配对和控制字符检查。用户刷新 `/fixed` 后复测“未选词 Enter 保留 nihao”和“空格选好你好再 Enter 换行”，回复“正常”。这些是真实 Windows 输入法在独立本机回显页的证据，不代表新构建已部署到真实 VPS，也不代表所有输入法/屏幕阅读器场景已人工验收。

自动化另外覆盖中英文、emoji、粘贴、Ctrl+C/D/Z、取消、快速重复提交、失焦、断线和销毁；明确区分事件模型、真实 Chromium CDP 集成、原生输入法人工三层。保持活跃组合但永不发 compositionend、整体替换导致的 xterm 截断为不同机制，兼容桥不猜测这些文本。没有修改依赖、xterm 私有实现或部署后的文件。
