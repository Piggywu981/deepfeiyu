# 控制台智能体会话 × DSH 会话双向互通（含技能与工具全量透传）设计规格

- 状态：待用户审查
- 日期：2026-11-05
- 基线：`a098018`（分支 `main`，本规格只落文档，不改代码）
- 需求来源：用户提出「deepfeiyu 网络控制台中有一个"智能体"界面，我想让智能体会话和 dsh 会话双向互通（包括技能、工具双向互通）」+ 五轮澄清
- 预期文档位置与本文件一致：`docs/superpowers/specs/2026-11-05-agent-dsh-bidirectional-relay-design.md`

---

## 1. 背景与现状（只读盘点结论）

**控制台「智能体」页**：`app/static/index.html` 的 `agent` 分页 + `POST /api/agent/chat`（SSE，`app/main.py:1854`），会话落 `agent_conversations` / `agent_turns`，每个对话绑定一个微信账号，跑 `app/ai/agent.py` 的工具循环。工具来自两处：

- `build_tools(session, allow_send=..., ...)` —— 微信收发、关机、改配置等**会话绑定**工具；
- `app/capabilities/registry.py:124` `tools_for("console")` —— 本地 `SKILL.md` 技能、`plugins/` 插件、MCP 工具。

**DSH 侧**：`dsh-plugin/` 已给 Harness 智能体 8 个 `deepfeiyu_*` 工具（`lib/tools.js`）、面板路由（`lib/route.js`，经 `webServer.register` 挂在 `/deepfeiyu/*`）、依赖安装、心情与桌宠融合（`app/dsh_bridge/`）。

**方向性缺口**：目前**只有 DSH → 控制台一个方向**。控制台智能体既不能调用 DSH 的技能/工具，两个"会话"之间也没有任何消息通道；`app/static/app.js` 里除了余额挂件，完全没有 DSH 概念。

因此本需求要补的是：**① 控制台 → DSH 的转交通道（从零）；② 双向配对的会话模型；③ 技能与工具的双向透传。**

## 2. 已确认决策

| 项 | 结论 | 来源 |
|---|---|---|
| 互通形态 | **桥接对话**：控制台对话可转交给 DSH，DSH 会话可转交给控制台对话，两侧**各自保留独立历史**，消息接力互传 | 用户选 A |
| 控制台 → DSH 落点 | **专属配对会话**：每个控制台对话懒创建一个固定 DSH 会话，此后永远转交给它 | 用户选 A |
| DSH → 控制台落点 | **对称配对**：沿用同一张配对表，查不到就新建对话 + 新建配对会话一次回填 | 用户选 A |
| 触发方式 | **自主 + 手动并存**：agent 可自主转交，同时保留控制台按钮与 DSH 工具/命令入口；三道闸（hop 上限、总开关、审批不豁免） | 用户选 C |
| 工具互通方式 | **全量透传**：不设工具白名单，透传经**对方原有执行管线**执行，闸门一个不旁路 | 用户选 C |
| 上下文携带 | 每次带**对面最近 6 条消息的裁剪版**（仅 role+content，剔除图片/工具日志/reasoning），配对面持久故不带全量 | 用户确认 |
| 默认参数 | 超时 **180s**、`max_hops=2`，均进配置可调 | 用户确认 |

### 2.1 三条经维护者明确批准的例外（AGENTS.md §9 / §11.10 冲突处置）

> 本设计与 AGENTS.md 存在字面冲突。按 §11.10「先指出冲突并请 Maintainer 确认；安全与许可红线优先」，已逐条向维护者指出并获批准。**以下三条是白纸黑字的例外，不是实现时的默许。**

| # | 冲突条文 | 批准的豁免范围 |
|---|---|---|
| 1 | §9「插件 `trusted` 门槛、**MCP/技能默认只在控制台可用**」 | 仅对**本机 DSH 会话**开放控制台的 skills/plugins/MCP 工具，受独立开关 `dsh_relay.tools_enabled` 约束；**`auto`（微信自动回复）上下文的边界一字不动** |
| 2 | §9「发送身份闸门…」相关：DSH 侧审批 | DSH 审批策略当前为 `never`，控制台发起的 DSH 工具调用**跟随该策略、不弹审批**。此为用户主动设置的结果，非本设计削弱 |
| 3 | 全量透传意味着可调到**关机、重启、改配置**类控制台工具 | 维护者批准「全量就是全量」：**不设白名单**，控制手段 = 默认开关 + 本机令牌 + 设置页一键关闭 |

**未被豁免、必须继续生效的闸门**（本设计硬约束）：

- 控制台侧：`LOCAL_HOSTS` 本机来源限制、访问令牌校验、`send.forbidden_wxids` 禁发名单、人工接管、发送限流——全部经 `build_tools()` 既有开关 + `source="dsh"` 节流路径（`app/main.py:524` 注释已写明「dsh = Harness 智能体发送（走既有节流队列）」）；
- **`/ws` 准入不改**（§9 红线）：`ws_endpoint` 的 `pump()` 只外推、`receive()` 只等断开帧，本设计**不**把它改造成命令通道。

## 3. 技术事实（已核实）

1. **`/ws` 是单向的**：`app/main.py:2144` 的 `pump()` 只把 `bus` 事件发给客户端；`:2167` 的 `ws.receive()` 只等断开帧，**不处理任何入站消息**。故不能当双向命令通道。
2. **DSH → 控制台通道已存在**：插件现有 8 个工具就是经 `lib/ops.js` 的 `call()` 打控制台 HTTP，带 `x-access-token`。
3. **控制台侧鉴权是现成的**：`accessToken` + `LOCAL_HOSTS` 中间件（`app/main.py:198` `auth_middleware`、`:223` `origin_guard`）；`app/dsh_bridge/api.py:22` `_require_local` 是同类先例。
4. **DSH Host 侧可用原语**（经 `cordis_inspect_list` 实查，非猜测）：
   - `sessionController.create / prompt / follow / page` —— 创建、驱动、流式观察 DSH 会话；
   - `tools.schemas(scope?) / tools.execute(exec)` —— 列目录、执行工具（执行管线自带 guard/approval）；
   - `skills.list / get / snapshot`、`sessionSkillCatalog.list` —— 技能目录与正文；
   - `systemPrompt.context / section` —— 注入提示词上下文（融合功能已用此模式）；
   - `webServer.register(route)` —— 面板现有 `/deepfeiyu/*` 即由它注册（`dsh-plugin/lib/route.js:194`）。
5. **控制台技能端点已存在**：`GET /api/capabilities/skills/{name}`（`app/main.py:1223`）。
6. **配对关系的天然载体**：`agent_conversations` 已是控制台 SQLite，加列走既有 `user_version` 迁移（§8 要求可回滚）。
7. **既有规格约定**（沿用 `2026-11-05-dsh-panel-chat-control-design.md`）：`lib/route.js` 用 `routes` 表 + `INVALID_ARGS`/`ConsoleDownError`/`ConsoleApiError` 分类；`tests/test_dsh_plugin.py` 30 例 fake-ctx 风格；Node 24 有全局 `WebSocket`。

### 3.1 待实机验证（**不作为已成立事实**）

以下两条**尚未实测**，实现阶段必须先验证，验证不通过则设计需回头调整：

- **V1**：Host 侧插件能否直接调用 `sessionController.create` / `prompt`（这些方法标了 `@Remote`，主要为远端调用设计，Host 直调的可用性未验证）。
- **V2**：`tools.execute(exec)` 所需 `ToolExecutionInput` 的完整形状与最小可构造集（是否需要真实 `Agent` 实例）。

**降级预案**（若 V1/V2 不通过）：配对会话改用 `subagents.startActivation` + `subagents.prompt`（子会话激活路径，语义更接近"被外部驱动的一次回合"）；工具执行改为插件内**逐个包一层**已注册工具的白名单直调，而非通用 `tools.execute`——此时需回到 §2.1 第 3 条重新与维护者确认范围。

## 4. 方案取舍

| 方案 | 结论 |
|---|---|
| **一：桥接对话 + 配对会话 + outbox 长轮询** | **采用**：状态源唯一在控制台；控制台只提供读端点，发起方永远是插件，鉴权完全复用既有中间件 |
| 二：控制台主动调 DSH `webServer` 写路由 | **否决**：为本机 Python 开发者在 DSH 上新开一个对非浏览器开放的写路由，等于新挖攻击面；且 `webServer` 对非浏览器调用者的准入未验证 |
| 三：改造 `/ws` 为双向命令通道 | **否决**：撞 §9 `/ws` 准入红线，且扩大既有实时通道的攻击面 |
| 四：DSH → 控制台也走 outbox | **否决**：该方向是"调工具拿结果"，天然同步，走直连 HTTP 更简单；只有控制台 → DSH 需要跨进程等一个长回合 |
| 五：工具互通只做只读白名单（B 方案） | **否决（用户明确选择 C）**：会让"工具双向互通"名不副实；但须按 §2.1 记录其安全后果 |

## 5. 设计

### 5.1 总体架构与配对模型

**状态源唯一**（沿用 `app/dsh_bridge/service.py:1-5` 的原则：状态永远在控制台，桥接层只校验转发、不存业务状态）。

配对关系存控制台 SQLite：`agent_conversations` 加 `dsh_session_id` 与 `relay_hops` 两列（走 `user_version` 迁移）。一张表两个方向共用：

```
控制台对话 cid  ←——→  DSH SessionId
      ↑                      ↑
  由它发起转交时          由它发起转交时
  读 dsh_session_id      按它反查 cid
```

- 控制台 → DSH：读本对话 `dsh_session_id`，空则懒创建配对会话并回填。
- DSH → 控制台：按 `dsh_session_id` 反查 cid；查不到就新建控制台对话（标题 `来自 DSH：<会话标题>`）+ 新建配对 DSH 会话，一次回填。
- **删除控制台对话时不删配对 DSH 会话**（它是独立会话记录，删了丢历史），只解除绑定标记。

**两条通道，方向不对称，各走各的鉴权模型：**

| 方向 | 通道 | 鉴权 |
|---|---|---|
| **DSH → 控制台** | 插件调控制台 HTTP（**已存在**，现有 8 个工具即此路） | 既有：`accessToken` + `LOCAL_HOSTS` |
| **控制台 → DSH** | 插件**长轮询拉**控制台 `GET /api/dsh/outbox/pending`，执行完 `POST /api/dsh/result` 回写 | 既有：`accessToken` + `LOCAL_HOSTS`（新端点沿用同一套中间件） |

**为什么控制台 → DSH 走"插件拉"**：① 不在 DSH 上新开对本机 Python 开放的写路由（攻击面）；② 不动 `/ws`（红线）；③ **控制台永远是发起方**——权限判断、hop 计数、总开关全在控制台进程内裁决，插件只负责"把活干了再回话"，不拥有任何策略。

**代价**：0～1s 轮询延迟。相对于"另一个 agent 跑几十秒的一个回合"，可忽略。

### 5.2 双向数据流

**方向 A：控制台 → DSH**

```
控制台 SSE 回合内（智能体自主 或 手动按钮）
  ↓ ① 三道闸：dsh_relay.enabled / relay_hops 计数 / console_locks 互斥
  ↓ ② 组装 payload 写入 outbox（新表 dsh_relay）
  ↓ ③ 本回合挂起等 asyncio future（超时 180s）
插件长轮询 GET /api/dsh/outbox/pending ←── 0~1s 拉一次
  ↓ ④ 无 dsh_session_id 则 sessionController.create 懒建配对会话并回填
  ↓ ⑤ sessionController.prompt 驱动 DSH 会话跑一个完整回合
  ↓ ⑥ POST /api/dsh/result 回写
控制台唤醒 future → role=assistant、meta.relay="dsh" 落库 → SSE 推给前端
```

**方向 B：DSH → 控制台**

```
DSH 智能体调 relay 工具 / 手动命令
  ↓ ① 插件 POST /api/dsh/relay（新增端点，沿用 accessToken + LOCAL_HOSTS）
  ↓ ② 控制台同套三道闸；按 dsh_session_id 反查 cid，查不到就新建对话并回填
  ↓ ③ 控制台进程内同步跑一个 agent 回合（复用 agent_chat 组装逻辑，非流式）
  ↓ ④ 返回最终文本 → 插件作为工具返回值交给 DSH 智能体
```

**为什么 B 不走 outbox**：B 是"调工具拿结果"，天然同步；只有 A 需要跨进程等一个长回合。

**outbox 支持两类条目**（技能/工具查询不走完整转交，但复用同一条通道与鉴权）：

```
kind: "relay"        → 转交，跑完整 DSH 回合，慢（180s 超时）
kind: "query"        → 只读查询（技能正文 / 工具目录），拉到立刻回写，快
kind: "tools.list"   → 返回 DSH 工具目录
kind: "tools.execute"→ 执行一个 DSH 工具
```

### 5.3 上下文携带

每次只带**对面最近 6 条消息的裁剪版**——仅 `role` + `content` 文本，**剔除**图片引用、工具调用日志、reasoning；前置一行来源说明（「以下来自 Deep肥鱼 控制台对话〈标题〉」）。

理由：配对面是**持久**的，跨次转交时对面早已攒下自己的历史，每次带全量只会重复膨胀；首次转交对面为空，那 6 条即全部。

### 5.4 防循环与失败处理

| 情形 | 处理 |
|---|---|
| hop 超限（默认 `max_hops=2`） | 拒绝转交，把「已达转交上限」作为可读错误返回给发起方 agent |
| 同一回合内连环转交 | 天然互斥——控制台等 DSH 结果时 `console_locks` 占用中 |
| DSH 超 180s 未回 | 回合以 `note` 告知超时，outbox 标 `timeout`，**迟到结果按请求 id 直接丢弃** |
| 插件侧控制台未运行 | 拉不到结果 → 控制台侧自然超时，不新增错误路径 |
| 配对会话创建失败 | 返回可读错误，不回填 `dsh_session_id`，下次重试重建 |
| **控制台重启后 outbox 孤儿条目** | 按 `created_at` TTL 清理（默认 10 分钟）标 `abandoned`，不永久堆积——两进程生命周期不同步是本设计唯一新增的常态故障 |
| 插件轮询断连 | 指数退避重连（1s→2s→…→30s 封顶）；控制台只看到"暂时没人来拉" |
| 配对 DSH 会话被用户在 GUI 手动删除 | 插件 `prompt` 报错 → 回写 `result: error` → 控制台清空该对话 `dsh_session_id`，下次重建 |

### 5.5 技能互通

| 方向 | 机制 | 成本 |
|---|---|---|
| **控制台技能 → DSH** | 插件用 `systemPrompt.context`（`deepfeiyu.skills`，60s 刷新）注入技能目录摘要；正文按需调**已存在**的 `GET /api/capabilities/skills/{name}` | 新增一个 `deepfeiyu_skill` 工具，端点不写 |
| **DSH 技能 → 控制台** | 控制台 prompt block 注入 DSH 技能目录 + 新工具 `load_dsh_skill(name)` 读正文 | 走 outbox `query` 通道 |

### 5.6 工具全量透传

**两把带前缀的通用钥匙，各开一扇门**（前缀保证两侧工具名永不撞车）：

- **控制台侧**（DSH 智能体用）：`deepfeiyu_tool(name, args)` → `POST /api/dsh/tool-exec` → 控制台在**自己进程内**执行。
- **DSH 侧**（控制台智能体用）：`dsh_tools()`（列目录）+ `dsh_tool(name, args)`（执行）→ outbox `tools.list` / `tools.execute` → 插件在 Host 内调 `tools.schemas()` / `tools.execute(exec)`。

`dsh_tool` 的 schema 只需 `{name, args}`，目录靠 `dsh_tools()` 单独拉，避免把上百个工具名塞进静态 schema。

**闸门——全量透传，但一个都不旁路**（§2.1 的硬约束）：

关键事实：`build_tools()`（`app/ai/tools.py:51`）**本来就是唯一合流点**——它在 `app/ai/tools.py:1632` 已经把 `capability_registry.tools_for(tool_context)` 合并进来，`tool_context` 正是 `console` / `auto` 的分界。因此"经 `build_tools()` 拿工具"不是额外要求，而是**天然落在唯一正确的位置**上，且 `auto` 边界由既有参数一并保住。

- 控制台侧 `POST /api/dsh/tool-exec` **必须经 `build_tools()` 拿同一套工具与同一套开关**，而非直接调 `registry.tools_for()`：
  - 微信收发类 → 复用 `source="dsh"` 既有路径，身份闸门、`send.forbidden_wxids`、人工接管、限流**全部生效**；
  - `registry.tools_for("console")`（skills/plugins/**MCP**）→ 按 §2.1 第 1 条豁免，仅本机 DSH 会话 + 独立开关，**`auto` 上下文边界不动**。
- DSH 侧：`tools.execute(exec)` 自带 guard/approval；当前 `never` 策略 → 不弹审批（§2.1 第 2 条）。
- **唯一总闸门是开关**：按 C 的定义不设白名单，`deepfeiyu_tool` 理论上能调到关机/重启/改配置（§2.1 第 3 条）。控制手段 = 默认开关 + 本机令牌 + 设置页一键关闭。

### 5.7 开关矩阵（全部进控制台设置页，可独立开关）

| 能力 | 方向 | 默认 | 闸门 |
|---|---|---|---|
| 技能目录 + 正文 | 双向 | 开 | 本机 + 令牌，只读 |
| DSH 工具 | 控制台 → DSH | 开 | DSH `tools.execute` 管线 |
| 控制台工具（微信/会话类） | DSH → 控制台 | 开 | `build_tools` 既有开关 + `source=dsh` 节流 + 禁发名单 |
| 控制台工具（skills/plugins/**MCP**） | DSH → 控制台 | 开（**豁免已批准**） | 仅本机 DSH 会话，`auto` 上下文不动 |
| 自主转交 | 双向 | 开 | `max_hops` + 总开关 |

**配置键名**（写入 `data/config.json` 的 `dsh_relay` 段，全部有默认值、缺省即全开）：

| 键 | 默认 | 作用 |
|---|---|---|
| `enabled` | `true` | 总开关：关闭后**双向转交全部拒绝**（手动与自主都不放行） |
| `auto_relay_enabled` | `true` | 只关**自主**转交，手动按钮与工具入口保留 |
| `tools_enabled` | `true` | §2.1 第 1 条豁免的独立开关：关掉后 DSH 侧调不到控制台 skills/plugins/**MCP** |
| `max_hops` | `2` | 转交跳数上限 |
| `timeout_sec` | `180` | 控制台等待 DSH 结果的时限 |
| `context_messages` | `6` | 每次携带的对面消息条数 |
| `outbox_ttl_sec` | `600` | outbox 孤儿条目清理时限 |

## 6. 改动清单（文件级）

**控制台 Python 侧**

| 文件 | 改动 |
|---|---|
| `app/db.py` | `agent_conversations` 加 `dsh_session_id` + `relay_hops`；新增 outbox 表 `dsh_relay`；`user_version` 迁移（可回滚） |
| `app/dsh_bridge/relay.py` **（新）** | 三道闸裁决、outbox 读写、payload 组装、超时 future 等待、迟到结果丢弃、TTL 清理 |
| `app/dsh_bridge/api.py` | +4 端点：`GET /api/dsh/outbox/pending`、`POST /api/dsh/result`、`POST /api/dsh/relay`、`POST /api/dsh/tool-exec` |
| `app/main.py` | `agent_chat`（`:1854`）回合内接入转交（SSE 挂起等待）；新增手动转交端点。`console_locks` 定义于 `:60` |
| `app/ai/tools.py` | `build_tools`（`:51`）注入 `dsh_tools` / `dsh_tool`——复用其既有 `allow_send` 等开关与 `:1632` 的能力合流点 |
| `app/capabilities/` | prompt block 注入 DSH 技能目录 + `load_dsh_skill` 工具 |
| `app/static/` | 智能体页「转交给 DSH」按钮与转交徽标；设置页开关矩阵 |

**插件侧**

| 文件 | 改动 |
|---|---|
| `dsh-plugin/lib/relay.js` **（新）** | 长轮询循环、`kind` 分发、配对会话懒建、结果回写、退避重连 |
| `dsh-plugin/lib/ops.js` | `relay` / `toolExec` / `skill` 转发函数（复用既有 `call()`） |
| `dsh-plugin/lib/tools.js` | +3 工具：`deepfeiyu_relay`、`deepfeiyu_tool`、`deepfeiyu_skill` |
| `dsh-plugin/index.js` | 启停长轮询，卸载时清定时器（与既有 fusion 生命周期同模式） |
| `dsh-plugin/README.md` | 工具清单 + 安全节（含 §2.1 三条已批准豁免的明文） |
| `docs/使用说明.md` | 控制台页面表与「智能体」页说明同步 |
| `CHANGELOG.md` | 新段落（改动 + 验证状态） |
| `tests/test_dsh_relay.py` **（新）** | 三道闸、outbox 往返、超时丢弃、配对懒建与反查、迁移回滚 |
| `tests/test_dsh_plugin.py` | 沿用 fake-ctx 覆盖 `relay.js` 的 `kind` 分发与重连；源码标记断言 |

**不改**：`app/wechat/**` 通道逻辑、`app/static` 既有会话页行为、`/ws`、`LICENSE`/`EULA*`/`NOTICE`、`requirements*.txt`、`vendor/`。

### 6.1 交付顺序（供实现计划拆步）

范围较大但**是一个内聚功能**，不建议拆成独立规格；实现计划按下列依赖排序，每步可独立验证与回滚：

1. **V1/V2 实机验证**（§3.1）——先跑通，不通过则回头改设计，避免建在未验证假设上；
2. 数据库迁移 + 配对模型（`app/db.py`）；
3. `app/dsh_bridge/relay.py` 三道闸与 outbox + 4 个端点 + `tests/test_dsh_relay.py`；
4. 插件 `lib/relay.js` 长轮询与 `kind` 分发（打通方向 A 一条端到端路径）；
5. 方向 B：`deepfeiyu_relay` 工具 + `/api/dsh/relay`；
6. 技能互通（双向）；
7. 工具透传（`dsh_tools`/`dsh_tool` + `deepfeiyu_tool`）；
8. 前端按钮、转交徽标、设置页开关矩阵；
9. 文档与 CHANGELOG。

**第 4 步完成即可先验收"会话互通"主干**，技能与工具是其上的增量——若中途需要交付，第 4 步就是一个可用的最小闭环。

## 7. 测试计划

1. **`tests/test_dsh_relay.py`（新增）**：
   - 三道闸：总开关关闭 → 双向拒绝；hop 超限 → 拒绝且返回可读错误；`console_locks` 占用 → 409；
   - outbox 写入 → 拉取 → 回写 → 唤醒 future 完整往返；
   - **超时后迟到结果按请求 id 丢弃**（最易写错，必须有回归测试）；
   - 配对懒建 / 按 `dsh_session_id` 反查 / 删除对话只解绑不删会话；
   - `user_version` 迁移前后数据可回滚；
   - **经透传发微信时 `source=dsh` 节流与 `forbidden_wxids` 仍生效**（用测试证明，不靠叙述）；
   - MCP 豁免开关关闭后 DSH 侧调不到 MCP 工具；
   - 图片 / reasoning / 工具日志不跨进程（断言）。
2. **`tests/test_dsh_plugin.py` 新增**：沿用既有 fake-ctx 风格覆盖 `relay.js` 的 `kind` 分发三条路径与轮询失败重连；`route.js` / `ops.js` / `tools.js` 源码标记断言。**既有 30 例不许改弱。**
3. **`node --check`** 覆盖所有改动的 JS；**全量 `pytest -q`**。
4. **实机**：`install_bundle` → `application: applied` → 双向各跑一次 + 第二次复用配对验证 + 开关矩阵逐项开关。
5. 未跑项与环境性失败**如实写明，不声称全绿**；视觉与交互细节由用户最终验收。

## 8. 风险、回滚与合规

| 风险 | 状态与对策 |
|---|---|
| §9「MCP/技能默认只在控制台可用」被豁免 | **维护者已批准**（§2.1 第 1 条）；范围限本机 DSH 会话 + 独立开关，`auto` 上下文不动；写入 README 安全节 |
| `never` 策略下控制台发起的 DSH 工具调用不弹审批 | **维护者已批准**（§2.1 第 2 条）；系用户主动设置的结果，写入 README 安全节 |
| 全量透传含关机/重启/改配置，开关为唯一总闸 | **维护者已批准**（§2.1 第 3 条）；写入 README 安全节，设置页给出醒目提示 |
| 长轮询 0–1s 延迟 | 相对 DSH 回合耗时可忽略；不阻塞控制台事件循环（异步等待 future） |
| 两进程生命周期不同步 | outbox TTL 清理 + 指数退避重连（§5.4） |
| V1/V2 未验证（§3.1） | 实现首步即验证；不通过则走降级预案并**回头重新确认范围** |
| `client.js` / `app/main.py` 继续膨胀 | 控制台改动集中在新增的 `app/dsh_bridge/relay.py`，不往 `main.py` 大段堆逻辑 |

- **回滚**：数据库迁移可回滚（加列不删列，新表可 drop）；插件侧单提交可 `git revert`；开关默认态可一键关闭全部新行为。
- **合规**：不改 `LICENSE`/`EULA*`/`NOTICE`；不新增依赖与外联；提交内容不含密钥、真实聊天记录、本机绝对路径（跑 `tools/release_snapshot.py --scan-tracked .` 与 `git diff --check` 自查）。

## 9. 验收标准（可观察）

1. 控制台智能体页点「转交给 DSH」→ 本对话出现 `meta.relay="dsh"` 的 assistant 轮次，GUI 会话列表出现配对会话。
2. DSH 侧调 `deepfeiyu_relay` → 控制台出现（或续用）配对对话并拿到回答。
3. **第二次转交复用同一配对，不新建会话。**
4. hop 超限、总开关关闭时，两侧都得到**可读拒绝**而非静默失败。
5. `dsh_tool` 能执行 DSH 只读工具；`deepfeiyu_tool` 能执行控制台 `chats` 类工具。
6. **经透传发微信时 `source=dsh` 节流与 `forbidden_wxids` 仍生效**（测试证明）。
7. MCP 豁免开关关闭后，DSH 侧调不到控制台 MCP 工具（测试证明）。
8. 图片、reasoning、工具日志**不跨进程**（断言证明）。
9. `pytest tests/test_dsh_relay.py -q`、`pytest tests/test_dsh_plugin.py -q`、`node --check` 全过；全量 `pytest -q` 结果如实报告。
10. `install_bundle` 后 `application: applied`，实机双向各跑通一次，视觉细节用户验收。

## 10. 非目标与后续

- **非目标**：图片跨进程、reasoning/工具日志共享、`/ws` 改造、`auto`（微信自动回复）上下文的任何边界变化、跨机器部署、配对 DSH 会话的删除联动、消息分页。
- **后续可选**（本次不做，需另行提出）：转交时携带附件、配对会话在控制台侧的可视化时间线、按账号维度的批量配对策略。

## 附录 A：Issue 草稿（供人类提交，AI 不代开）

> **标题**：控制台智能体会话与 DSH 会话双向互通（含技能与工具全量透传）
>
> **问题**：控制台「智能体」页与 DSH 会话之间没有任何消息通道，控制台智能体也调不到 DSH 的技能与工具——目前只有 DSH → 控制台单向（8 个 `deepfeiyu_*` 工具）。用户需要在两侧都能把工作接力给对方，而不是手动复制粘贴。
>
> **范围**：新增配对会话模型（`agent_conversations` 加列）、控制台 → DSH 的 outbox 长轮询通道、DSH → 控制台的直连端点、技能双向共享、工具双向全量透传、三道闸与开关矩阵。不改 `/ws`、不改 `auto` 上下文边界、不动许可与法律文档。
>
> **验收标准**：见设计规格第 9 节十条。
>
> **风险**：与 AGENTS.md §9 存在三条字面冲突（MCP 边界、DSH `never` 审批、全量透传含关机），**已按 §11.10 逐条向维护者指出并获批准**，范围见规格 §2.1；未被豁免的闸门见 §2.1 末段。V1/V2 两项技术事实待实机验证（§3.1）。
>
> **被否决的替代方案**：控制台主动调 DSH 写路由（新攻击面）；改造 `/ws` 为命令通道（撞 §9 红线）；DSH → 控制台也走 outbox（该方向天然同步，多余）；工具只做只读白名单（用户明确选择全量透传）。
