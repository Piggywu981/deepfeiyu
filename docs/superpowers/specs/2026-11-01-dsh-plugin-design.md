# Deep肥鱼 × DeepSeek Harness 插件设计规格（C 方案：原生面板 + Agent 工具）

- 状态：待用户审查
- 日期：2026-11-01
- 分支：`feat/dsh-plugin`（worktree：`.agents/worktrees/feat/dsh-plugin`）
- 基线：`v1.0.1` / `14597a7`
- 需求来源：用户口头需求「开一个新的 worktree，把本项目做成 deepseek harness 的插件」+ 两轮澄清（形态 C；B3 含发送；生命周期自动拉起 + 可切 attach；发送约束跟随 Harness 权限模式）

---

## 1. 背景与问题

Deep肥鱼是 Python/FastAPI + 原生 JS 的独立微信智能体控制台（默认 `http://127.0.0.1:8765`），本身已有 `plugins/`、`skills/`、MCP 能力系统。DeepSeek Harness（DSH）的「插件」是完全不同的概念：Cordis bundle 包（`package.json` + `cordis.patch.yml` + Host/Client JS），装进 profile 后全局生效。

用户希望**在 Harness 里使用 Deep肥鱼**，而不是重写项目：

- 在 Harness Web UI 里看到并使用 Deep肥鱼的控制台能力；
- 让 Harness 里的智能体可以查询与操作 Deep肥鱼（含发送微信消息）；
- 装进当前 `desktop` profile 后「装完即用」，可随 Harness 启停控制台。

## 2. 已确认需求

| 项 | 结论 |
|---|---|
| 插件形态 | C：A（Harness 内 UI）+ B（Agent 工具）组合 |
| 工具范围 | B3：只读 + 受控写 + **直接发送消息** |
| 新功能 | 允许按情况增加（不扩大为无关功能） |
| 控制台生命周期 | 自动拉起 + 可切 `attach`（附着已运行实例） |
| 发送约束 | 跟随 Harness 的权限模式（逐步审批 / 自动审批 / 完全批准），不另造一套策略 |

## 3. 技术事实（决定架构的硬约束）

1. **DSH bundle 形式**：`package.json`（`name/version/private/type:module/exports/dsh.bundle.patch`）+ `cordis.patch.yml` + Host `index.js` + Client `client.js`（`window.__ModuleLoader__.load`）。**无需构建、无需 npm 依赖、无需 install 脚本**；本地目录安装是 link checkout。
2. **页面/面板禁止 iframe**（`references/practices.md` 原文：iframe 文档拿不到宿主主题 token、明暗切换与 `ctx.locale`）。插件页面必须是注册在 slot 里的原生 React 组件。
3. **面板接入点**（`Slots.listSubTree` 实测）：`sidebar.panellist`（list，全局面板图标，`id` 对应 `main` 面板）+ `main`（keyed，"Central panel selected by sidebar entry id"）。注册同 id 即可出现在侧栏并获得中央页面。
4. **无构建数据通道（P0 实机结论）**：采用 **`webServer` 同源路由 + `ctx.connection.requestRejection` 鉴权 + 同源 `fetch`**：
   - Host：`ctx.webServer.register({ kind:'prefix', path:'/deepfeiyu', handler(req,res) })`；handler 内先 `ctx.connection.requestRejection({ headers: req.headers })`（401/403 直接拒绝），再读取 JSON body 并返回 `{ok:true,value}` / `{ok:false,error:{code,message,details}}`。
   - Client：`fetch('/deepfeiyu/<endpoint>', { method:'POST', headers:{'content-type':'application/json'}, credentials:'same-origin', body })`；浏览器 Cookie 由同源请求自动携带。
   - 曾尝试官方 **Connection 通用 RPC**（`ctx.connection.rpc.handle` / `rpc.call`，无 Typert schema）：实机激活即抛 `cannot get property "webServer" without inject`；根因在 `@deepseek-ai/dsh-client-connection/lib/index.js:871` 用带 inject 校验的 `owner.webServer`，经 Cordis shadow 上下文解析到未注入 `webServer` 的 fiber——库内部路径问题，本安装不可用，故改用上面的备选。
   - `@Remote` 需要构建期生成，本安装不含构建工具，**不采用**。
5. **官方浏览器标签页**：`ctx.sidebarRight.openTab('browser', { params: { url } })`（Desktop 用 Electron `<webview>`，Web 用 sandbox 化 iframe）可用于在 Harness 右侧栏内打开外部网页；由宿主实现，不是插件自己嵌 iframe。→ 用作「完整控制台」入口。
6. **子进程**：`@deepseek-ai/dsh-subprocess-local` 已挂载，`inject: ['subprocess']` 可用 `spawn(spec)` / `terminate()` / `waitForExit()` / `done`；服务 dispose 会终止其管理的进程。
7. **工具注册**：`ctx.tools.register(definition)`；`parameters` 直接传**完整 JSON Schema 形态**（`{type:'object', properties:{...}, required:[...]}`），`output.schema` 是受限 JSON Schema 子集，`output.render` 返回 `ContentBlock[]`，`execute(args, exec)` 直接 throw 表示失败。`defineTool` 来自 `@deepseek-ai/dsh-tools`，本插件不 import（避免新增依赖）；Registry 的 `schemas()` 只投影 schema 字段，P0 验证投影后模型可见 schema 正确。
8. **审批**：`ctx.approval.request({agent, toolName, reason, callId?, signal?})` 返回 `allowed-once | rejected | cancelled | unavailable`；策略 `ask` 走 answerer（Auto 模式即自动审批 answerer），`never` 确定性拒绝。权限预设 `ctx.permissionPresets.current(session)` / `resolve(name)` 给出 `{sandbox, approval}`。
9. **Config**：官方语法是 `@deepseek-ai/schemastery` 的 schema；本插件不 import 第三方包 → **不声明 `Config`**，在 `apply` 内对行 config 做手工默认值与校验，README 记录字段。
10. **基线测试**：Python 3.12 venv 全量 `pytest -q` = **9 failed, 1392 passed, 1 skipped**；失败全部为环境性（`vendor/` 子模块未初始化、未安装 `wecferry`/`wechatauto-replica`），与本次改动无关，PR 中如实列明。

## 4. 方案取舍

| 方案 | 结论 |
|---|---|
| iframe 内嵌完整控制台 | **否决**：practices.md 明确禁止，且拿不到主题/明暗/locale |
| `webServer` 路由 + `requestRejection` + 同源 fetch | **采用（P0 已验证）**：复用 Connection 的 Cookie/Host/Origin 鉴权，不依赖不可用的 Connection RPC |
| Typert `@Remote` 生成 Client 调用 | **否决**：需构建期工具，安装内不可用 |
| 完整移植 Python 后端为 Cordis/Node 插件 | **否决**：等于重写项目，违反「不引入新运行时」 |
| **原生 React 面板 + Connection RPC + 官方 browser 标签页** | **采用** |

## 5. 架构

### 5.1 组件

```
┌───────────────────────────── DeepSeek Harness（Electron/Web）─────────────────────────────┐
│  Client 半（dsh-plugin/client.js，无构建 React）                                          │
│   ├─ sidebar.panellist  : 「Deep肥鱼」图标（自绘 SVG，size/active owner props）             │
│   ├─ main[deepfeiyu]    : 原生面板（状态/账号/会话/消息/发送/统计/启停）                    │
│   └─ 完整控制台按钮      : ctx.sidebarRight.openTab('browser', {params:{url}})  ← 宿主 tab  │
│            │ 同源 fetch → /deepfeiyu 路由（requestRejection 鉴权）                          │
│  Host 半（dsh-plugin/index.js）                                                           │
│   ├─ 唯一操作实现层：fetch → http://127.0.0.1:8765 现有 API（携带访问令牌）                 │
│   ├─ 会话/审批桥：ctx.sessions + ctx.permissionPresets + ctx.approval                     │
│   ├─ 控制台监督：ctx.subprocess 拉起/停止/健康轮询 .venv python -m app.main                │
│   ├─ 8 个 Agent 工具（deepfeiyu_*）                                                        │
│   └─ RPC endpoints（面板与工具共用同一批操作函数）                                          │
└───────────────────────────────────────────────────────────────────────────────────────────┘
                                        │ HTTP 127.0.0.1:8765
                              ┌─────────▼─────────┐
                              │ Deep肥鱼控制台     │  身份闸门/禁发名单/本机限制/令牌校验
                              │ FastAPI + SQLite   │  继续在 Python 侧执行（不削弱）
                              └───────────────────┘
```

### 5.2 Host 插件（`dsh-plugin/index.js`）

- 入口：硬依赖 `export const inject = ['tools', 'subprocess', 'connection', 'webServer']`；`approval` / `sessions` / `permissionPresets` 通过 `ctx.get(...)` 可选获取（缺失时 agent 发送按 fail-closed 的 `ask` 处理）。`export function apply(ctx, config)`。
- 配置：`apply` 内规范化（见 §7）；非法值给出可读中文错误并使插件不激活。
- 控制台监督：
  - `mode: auto`（默认）：激活时先探测 `/api/health`；未运行且 `autostart`（默认 true）则用 `ctx.subprocess.spawn` 拉起 `[pythonPath, '-m', 'app.main']`，`cwd = projectDir`，`env` 继承 + `PYTHONUTF8=1`；`stdio.stdout/stderr` 收集模式（仅保留尾部用于错误诊断，不落日志、不进工具输出）。
  - 启动后每 500ms 探测、最多 60s；随后每 5s 刷新状态。退出异常记录 `lastError`（仅 stderr 尾部摘要，脱敏/截断）。
  - `mode: attach`：不 spawn，只探测。
  - 插件卸载时终止**自己拉起**的进程（`terminate()` + `waitForExit()`）；attach 的实例不动。
- 控制台 API 客户端：`fetch(baseUrl + path)`；读令牌优先级 `config.accessToken` → `DEEPFEIYU_ACCESS_TOKEN` 环境变量 → 项目 `data/config.json` 的 `server.access_token`（只读，不落日志/不回显）；超时 5s（普通）/ 60s（发送，因服务端节流可能排队）。
- 操作函数（面板与工具共用，单一实现）：`status/accounts/activate/chats/messages/send/stats/capabilities/console.start/console.stop/console.restart`。
- Agent 工具（命名 `deepfeiyu_*`，中文描述）：
  1. `deepfeiyu_status`（只读）
  2. `deepfeiyu_accounts`（只读；`action=activate` 受控写，不涉及消息发送）
  3. `deepfeiyu_chats`（只读）
  4. `deepfeiyu_messages`（只读）
  5. `deepfeiyu_send`（写，走 §6.2 审批映射）
  6. `deepfeiyu_stats`（只读）
  7. `deepfeiyu_capabilities`（只读：技能/插件/MCP 状态）
  8. `deepfeiyu_console`（写：`start|stop|restart|status`，本机进程控制）
- 面板数据路由（同源 HTTP，全部 `POST /deepfeiyu/<endpoint>`，JSON body；`requestRejection` 鉴权）：

| endpoint | payload | value |
|---|---|---|
| `state` | `{}` | `{phase, running, mode, port, version, title, lastError, startedAt, spawnedByPlugin}` |
| `console.start` / `console.stop` / `console.restart` | `{}` | 同 `state` |
| `accounts.list` | `{}` | `{accounts:[...]}` |
| `accounts.activate` | `{id}` | `{active}` |
| `chats.list` | `{keyword?, limit?}` | `{chats:[...]}` |
| `messages.list` | `{uid, limit?}` | `{chat, messages}` |
| `send.text` | `{uid, text}` | `{message}`（面板用户发送 = `source:"human"`） |
| `stats` | `{}` | `{stats}` |
| `capabilities` | `{}` | `{capabilities}` |

  错误统一 `{ok:false, error:{code, message, details}}`；`code` 为稳定标识（如 `CONSOLE_DOWN`、`SEND_REJECTED`、`INVALID_ARGS`）。
- 生命周期：所有注册（tools / `webServer.register` 路由 / 定时器 / 子进程）都在 `ctx.effect` 内并返回 disposer。

### 5.3 Client 面板（`dsh-plugin/client.js`）

- 模块：`window.__ModuleLoader__.load({ id: '@local/deepfeiyu-dsh', factory(require){ const React = require('react'); return { inject:['slots'], apply(ctx){...} } } })`；工厂无副作用；资源注册在 `apply` + `ctx.effect`。
- 注册：
  - `ctx.slots.register({name:'sidebar.panellist', id:'deepfeiyu', order:20, label: () => 'Deep肥鱼'}, DeepFeiyuIcon)`
  - `ctx.slots.register({name:'main', key:'deepfeiyu'}, DeepFeiyuPanel)`
- 面板 v1（单页）：
  - 头部：状态圆点 + 阶段文字 + 版本/端口；`启动/停止/重启` 按钮；`打开完整控制台` 按钮（`ctx.sidebarRight.openTab('browser',{params:{url}})`，服务不存在时降级 `window.open`）。
  - 账号条：账号 chips（名称、驱动、连接状态、当前账号高亮），点击切换。
  - 会话列表：搜索框 + 列表（名称、最后消息摘要、时间、未读标记、AI/人工状态）。
  - 消息区：选中会话最近消息（方向、发送者、时间、文本/类型标签）；轮询 4s。
  - 发送框：textarea + 发送按钮（用户操作 → `send.text`，即 `source:"human"`）；错误就地显示。
  - 今日统计：进出消息数 / AI 回复数。
- 数据：`fetch('/deepfeiyu/<endpoint>', { method:'POST', headers:{'content-type':'application/json'}, credentials:'same-origin', body })`，宿主自动带浏览器 Cookie；面板挂载时拉 `state`，之后按 4s 轮询（卸载/隐藏时清理定时器）。
- 样式：只用 `--dsw-alias-*` 主题 token，类名前缀 `dsh-deepfeiyu-`；不 import 任何 Harness Client 包、不写 DOM、不碰 `document.body`。
- 文案：中文；`ctx.locale` 可用则 best-effort 注册，不可用则内置字典兜底。

### 5.4 Python 侧改动（最小）

- `app/main.py`：`SendBody` 增加 `source: Literal["human", "dsh"] = "human"`；`send()` 透传 `source=body.source`。
  - `dsh` 走 `send_text` 既有的 `_throttled` 队列（全局最小间隔 + 会话冷却），**比新增发送路径更强**，不绕过限流；
  - 身份闸门 `_assert_send_allowed()`、`send.forbidden_wxids`、写操作本机限制、`LOCAL_HOSTS`、令牌校验均原样保留；
  - 非法 `source` 由 Pydantic 返回 422。
- 面板用户发送 → 仍为 `human`（与控制台人工点击等价，不排队）。

### 5.5 仓库内落点

- 新目录 `dsh-plugin/`（DSH bundle 必须是以 `package.json` 为根的独立目录；Python 侧不 import 它）。这是 AGENTS.md §8「不随意新建顶层目录」的**显式例外**，在 PR 描述中说明理由。
- 包名 `@local/deepfeiyu-dsh`，patch row id `deepfeiyu`，RPC channel `/deepfeiyu`。

## 6. 安全设计

### 6.1 不削弱既有红线

| 红线 | 处理 |
|---|---|
| 身份闸门 / 禁发名单 | 全部发送走 Python 既有 `send_text`（`_assert_send_allowed` 在服务端执行），插件无法绕过 |
| 本机来源限制 / 写操作本机限制 | 插件从 127.0.0.1 发起；`LOCAL_HOSTS`、`origin_guard` 不变 |
| 访问令牌 | 插件只读使用，不写入仓库、不打印、不回显给 Client、不进工具结果 |
| 限流 | agent 发送走 `source="dsh"` → 既有节流队列；面板发送等价人工 |
| 人工接管 | 发送后仍写 takeover pause（既有行为） |
| 不新增遥测/外联 | 插件只连 `127.0.0.1:<port>`；不引入第三方请求 |

### 6.2 Agent 发送审批（跟随 Harness 权限模式）

在 `deepfeiyu_send` 的 `execute(args, exec)` 中：

1. `session = ctx.sessions.get(exec.agent.id)`（取不到则回退 `ask`）；
2. `preset = ctx.permissionPresets.current(session)` → `ctx.permissionPresets.resolve(preset)` → `approval`：
   - `ask`（对应逐步审批 / Auto 自动审批）：调用 `ctx.approval.request({agent: exec.agent, toolName:'deepfeiyu_send', callId: exec.callId, reason, signal: exec.signal})`；仅 `allowed-once` 才发送；`rejected/cancelled/unavailable` 返回明确错误（fail closed）。
   - `never`（对应完全批准 / danger-full-access）：不弹审批，直接发送（仍受 §6.1 全部服务端闸门约束）。
3. 若 `permissionPresets` 不可用：默认走 `ask`（fail closed）。

### 6.3 其他

- RPC 只有 owner peer 可调（Connection 鉴权 + 本机 Cookie）；面板请求即当前页面身份。
- 工具结果会进入模型上下文：`deepfeiyu_messages` 返回聊天内容属于用户明确要求的能力；**不落插件日志**；工具描述提醒「返回内容可能含隐私信息」。
- `send` 入参校验：`uid` 非空、`text` 去空白后非空、长度上限 2000（工具侧），服务端仍会二次校验。
- 控制台 stderr 摘要仅保留最后 3 行、截断 500 字符；不写入文件。

## 7. 配置项（`cordis.patch.yml` 行 config，手工校验）

| 字段 | 默认 | 说明 |
|---|---|---|
| `projectDir` | 由包位置推断（`../`） | Deep肥鱼仓库根；可覆盖 |
| `pythonPath` | `<projectDir>/.venv/Scripts/python.exe`（非 Windows 为 `.venv/bin/python`） | 控制台解释器；不存在时报可读错误 |
| `mode` | `auto` | `auto` 自动拉起 / `attach` 只探测 |
| `autostart` | `true` | 仅 `auto`：激活即拉起 |
| `host` / `port` | `127.0.0.1` / `8765` | 控制台监听地址 |
| `accessToken` | `''` | 控制台访问令牌；空则回退环境变量与 `data/config.json` |
| `requestTimeoutMs` | `5000` | 普通请求超时 |
| `sendTimeoutMs` | `60000` | 发送超时（服务端节流可能排队） |
| `panelTitle` | `Deep肥鱼` | 面板标题 |

## 8. 文件清单

```
dsh-plugin/
  package.json          # name/version/private/type/exports/files/dsh.bundle+client
  cordis.patch.yml      # - insert: [{id: deepfeiyu, name: '@local/deepfeiyu-dsh', config: {...默认}}]
  index.js              # Host：配置、监督、API 客户端、操作、工具、Connection RPC
  client.js             # Client：panellist 图标 + main 面板 + browser tab 入口
  locale/en.json        # meta.title/description（Plugin Manager 展示）
  locale/zh.json
  icon.svg              # 自绘图标（≤256KiB）
  README.md             # 安装、配置、安全说明、卸载
app/main.py             # SendBody.source（最小改动）
tests/test_send_source.py      # source 语义与校验
tests/test_dsh_plugin.py       # bundle 静态一致性（manifest/patch/locale/icon/files/JS 语法）
docs/使用说明.md / CHANGELOG.md / README.md   # 用户可见文档
```

## 9. 测试与验收

### 9.1 验收标准

- [ ] `plugin_manager install_bundle` 返回 `application: applied`，无阻断性 `warnings`；`cordis_inspect_query(Config)` 能查到 `deepfeiyu` 行与生效配置。
- [ ] 侧栏出现「Deep肥鱼」图标；点击进入中央面板；面板能显示控制台状态、账号、会话、消息，并可启停控制台、切换账号、发送消息。
- [ ] 面板「打开完整控制台」在右侧栏打开 `http://127.0.0.1:8765`（Desktop webview）；服务缺失时降级为新窗口。
- [ ] 8 个 `deepfeiyu_*` 工具出现在工具列表；实机调用 `status`/`accounts`/`chats`/`messages`/`stats`/`capabilities` 成功。
- [ ] `deepfeiyu_send`：在 `ask` 模式下先弹审批、`allowed-once` 才发送；`never` 模式直接发送；未启动通道/命中禁发名单时报 Python 侧的可读错误（证明闸门未削弱）。
- [ ] Python：`pytest -q` 与基线一致（9 个环境性失败、其余通过），新增测试全绿。
- [ ] 文档：README/使用说明/CHANGELOG 已更新；`git diff --check` 通过；无密钥/真实聊天内容/本机绝对路径入提交。
- [ ] 不 push、不开 PR、不打 tag（需用户另行授权）。

### 9.2 测试

- `tests/test_send_source.py`：默认 human；`source="dsh"` 透传并走节流分支（mock `send_text` 断言 source）；非法值 422；空文本 400 不变。
- `tests/test_dsh_plugin.py`：JSON/YAML 结构、`files` 与真实文件一致、locale 含 `meta.title/description`、icon 存在且 ≤256KiB、工具名清单与 index.js 源码断言一致、`node --check`（无 node 时 skip 并打印原因）。
- 手工/实机：安装 → 刷新页面 → 面板与工具冒烟（见 §9.3）。

### 9.3 验证步骤（实机）

1. `.venv` 就绪（`run.bat` 首次安装或本 worktree 已建 venv）；控制台未运行时由插件拉起。
2. `plugin_manager install_bundle(target=<abs>/dsh-plugin)` → 读 `application/warnings`。
3. `cordis_inspect_query` 确认 `deepfeiyu` 行；刷新页面后 `Slots.listSubTree(root='main')` 确认 `deepfeiyu` occupant、`sidebar.panellist` 确认图标条目。
4. 调用各工具冒烟；观察审批弹出（ask 模式）。
5. `pytest -q` 与基线对比。
6. 视觉细节由用户确认（无浏览器控制时如实声明「视觉未验证」，只报 slot 注册与资源检查结果）。

## 10. 风险、回滚

| 风险 | 缓解 |
|---|---|
| Connection RPC 在 Client bundle 中不可用（`inject:['connection']` 假设） | 先做最小 spike；失败则降级为 `webServer` exact 路由 + `requestRejection` + 前端 `fetch` |
| `sidebarRight` 服务不可用 | 降级 `window.open(consoleUrl)`；面板核心功能不受影响 |
| 首次安装后页面未加载新 Client 模块 | 提示刷新页面；替换已装包需重启 Harness（HMR 边界） |
| `.venv` 不存在 | 状态置 `error` 并给出「先运行 run.bat」的可读提示，不自动下载依赖 |
| Python 发送节流导致工具超时 | `sendTimeoutMs` 60s；超时返回明确错误，不重试 |
| 插件影响其他会话/profile | 安装前征求批准；卸载用 `plugin_manager remove_bundle`；文档写恢复步骤 |

**回滚**：`plugin_manager` 卸载 bundle → 刷新页面 → 删除 `dsh-plugin/` 与 Python 改动提交（分支可整体丢弃）；不带 tag、不发布。

## 11. 实施阶段

1. **P0 spike**：最小 bundle 验证 `Connection RPC`（Host handle + Client call）、`main`/`sidebar.panellist` 注册、`sidebarRight.openTab` 可用性。
2. **P1 Host 核心**：配置、监督、API 客户端、操作、工具、RPC。
3. **P2 Client 面板**：图标/面板/轮询/发送/完整控制台入口。
4. **P3 Python + 测试 + 文档**：`SendBody.source`、两个测试文件、README/使用说明/CHANGELOG。
5. **P4 安装 + 实机验证 + 修复**：按 §9 验收。

## 12. 待验证假设（P0 已全部消除，见 §14）

- `connection.rpc.handle/call` 的实际可用性与卸载语义（`plans` 异步 disposer）。
- Client bundle `inject:['connection']` 是否被 ModuleLoader 接受。
- `sidebar.panellist` + `main` 同 id 的联动是否如 catalog 描述。
- `ctx.sidebarRight.openTab('browser', …)` 在本 profile 是否可用（Desktop）。
- `ctx.tools.register` 直接传完整 JSON Schema `parameters`（不经 `defineTool`）后，模型可见 schema 是否正确。
- `ctx.permissionPresets.current/resolve` 与 `ctx.approval.request` 在本 profile 的行为（ask/never 两态实测）。

## 13. 已否决的替代方案

- iframe 内嵌（禁止）。
- Typert `@Remote` / 构建期生成（安装不含工具链）。
- 自建 webServer 路由 + 自造 token（默认无鉴权，需重复实现鉴权）。
- 完整移植 Python 后端（重写项目，违反不引入新运行时）。
- 把 Python 控制台当 MCP server 接（需要另写 MCP server，且 UI 仍缺）。

## 14. P0 实测与验收记录（2026-11-01）

- **bundle 安装**：`plugin_manager install_bundle` → `application: applied`；`cordis_inspect_query(Config)` 可见 `include:deepfeiyu` 行（无 Config schema，`status: absent`）。
- **Client 注册**：启用 bundle 后**无需刷新页面**，`sidebar.panellist` 与 `main` 的 `deepfeiyu` occupant 均为 `active`（Client 模块随 HMR 模块图即时加载）。
- **数据通道**：`ctx.webServer.register + ctx.connection.requestRejection + 同源 fetch` 端到端验证通过（P0 探针由 Client 在模块 apply 时发 POST，Host 记录到 server 端时间戳与 payload）；**Connection RPC 的 `rpc.handle` 实测不可用**（见 §3 第 4 条）。
- **工具**：8 个 `deepfeiyu_*` 全部注册并实机调用；`deepfeiyu_send` 在完全批准模式下直达服务端并返回「微信通道未启动」，确认服务端身份闸门未被绕过；`deepfeiyu_console start/stop` 与 8765 端口监听状态一致；控制台未运行时错误可读。
- **面板**：数据、交互经用户实机验收通过（验收期间临时灌入 3 个合成演示会话 + 7 条消息，验收后已清除）。
- **未验证**：真实微信通道下发消息（本机无 wcferry/微信 4.x 通道）、`ask` 模式审批弹窗交互、完整控制台入口的侧栏打开行为（代码优先 `ctx.sidebarRight.openTab('browser')`，异常降级 `window.open`）。
- **测试**：`pytest -q` = **9 failed（环境性，与基线同集）/ 1408 passed / 1 skipped**；基线为 9 failed / 1392 passed / 1 skipped，新增 16 项即两个新测试文件；`release_snapshot.py --scan-tracked . --allow-no-policy` 通过。
- **冷启动修复（2026-11-01）**：Client 插件在页面 boot 阶段直接 `ctx.slots.register` 会抛 `slot "sidebar.panellist" is not declared`（父 slot 尚未声明），fiber 置 `failed`、web boot 审计 fatal（应用无法启动）；live 启用路径因 UI 已挂载而不复现。改为 `ctx.slots.inject(slotKey, () => ctx.slots.register(...))` 后冷启动验证通过。
- **冷启动调试方法**：审计日志只报 `entry: 状态` 不报底层异常；通过 Host 的 `clientModules.graph()/fetchBundle()` 与服务端拼接产物对比可验证产物；最终用「Client 阶段标记 + 同源 POST 回 Host 临时日志」拿到 `apply` 真实堆栈。
