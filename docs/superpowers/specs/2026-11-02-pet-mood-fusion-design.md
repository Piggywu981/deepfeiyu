# 桌宠与心情双端融合设计规格（Deep肥鱼控制台 × DeepSeek Harness）

- 状态：待用户审查
- 日期：2026-11-02
- 基线：`1a72b56`；工作区另有未提交的「可选功能依赖一键安装」与「关联 DSH 插件」改动，本设计基于该工作区，不改动它们
- 需求来源：用户口头需求「在 DSH 插件模式下，桌宠、心情……怎样融合比较好？」+ 六轮澄清（融合目标、隔离粒度、判断归属、桌宠形态、隐私边界、交付范围）
- 预期文档位置与本文件一致：`docs/superpowers/specs/2026-11-02-pet-mood-fusion-design.md`

---

## 1. 背景与问题

Deep肥鱼目前有两套“桌宠 / 心情”：

| | 控制台内建（`app/pet`、`app/mood`） | 上游 DSH 插件（`dsh-dafeiyu`、`dsh-feiyu-mood`） |
|---|---|---|
| 驱动 | 微信消息、回复回合、发送状态 | Harness 会话事件 |
| 状态 | `data/agent.db`（按微信账号 / 槽位） | 插件自己的存储 |
| 展示 | 控制台网页挂饰 + 独立桌面宠物窗口 | Harness 挂饰 + 独立桌面宠物窗口 |
| 注入 | 微信自动回复提示词 | Harness 会话提示词 |

两者互不读取、互不注入，同时开启就是“两只宠物、两套心情”。用户要的融合是：**一套状态、双端读写、一只宠物**，而不是让两套东西并存。

## 2. 已确认决策

| 项 | 结论 |
|---|---|
| 融合目标 | 统一一套模型，双端读写 |
| 隔离粒度 | 按微信账号隔离；DSH 绑定其中一个账号 |
| 心情判断归属 | **DSH 自己判断**（用 DSH 模型），把判断结果写回控制台共享库 |
| 桌宠 | **一只**，接收微信与 Harness 两端事件 |
| 隐私边界 | 允许 DSH 的“短理由”带来历进入微信（开关默认开）；原文不传、不落库；群聊降级 |
| 交付范围 | 完整闭环：控制台桥接层 + DSH 设置页 + DSH 右下角挂饰 + 宠物事件 + 提示词注入 |
| 上游 bundle | 不再作为融合载体；安装入口保留但标注“独立版，不共享状态” |

## 3. 技术事实（已核实）

1. **DSH 事件钩子可用**（inspect `Event`）：
   - `session/event`：`user/message`、`assistant/message`、`turn/start|end`、`tool/call`、`tool/result`、`error` 等，post-commit 触发；
   - `agent/turn-stopping`：一轮即将结束（serial 事件，可监听）；
   - `agent/error`、`agent/status`：步骤/回合错误与运行状态。
2. **DSH Host 能调模型**：`ctx.get('llm').stream(GenerateOptions)`，`GenerateOptions` 需要 `provider/model/messages`；默认模型由 `ctx.get('agentDefaultModel').currentSelection()` 给出（provider + model）。
3. **DSH Host 能注入 Harness 提示词**：`ctx.get('systemPrompt').context({ name, order, text })`（`text` 可为 `(context) => string`，随组装重新求值）。
4. **控制台心情已按来源记录**：`mood.service._run` 落库时写 `source`；`mood_events` 有 `source` 列；`inject_block` 已有“来历只对产生它的会话开放，群聊降级为只说心情”的逻辑。
5. **`mood_slots` 写列固定**：`store._write_slot` 只写固定列，任意新字段不会持久化。因此**不新增数据库列**：跨端来源改为从 `mood_events` 最新一行推导（新增只读查询）。
6. **控制台宠物事件模型**：`bus` 事件 `agent_turn(type=start)` / `agent_tool(tool_start|done)` / `log(level=error)` / `status(connected)` → `PetReducer.on_event`；气泡文案由 `bubble.message_for` 生成。
7. **配置**：`app/config.py` 的 `DEFAULTS` 已有 `mood`、`pet` 段，`load()/save()` 做深合并；新增 `dsh` 段不需要迁移。
8. **鉴权**：控制台写入只允许本机（`config.LOCAL_HOSTS`，可选访问令牌）；DSH 自定义路由必须继续用 `ctx.connection.requestRejection`。
9. **上游两个 bundle 不可改**：它们是第三方 npm 包，状态/判断都在包内；融合不能复用它们的存储，只能自建桥接。

## 4. 方案取舍

| 方案 | 结论 |
|---|---|
| A：控制台唯一状态源 + DSH 判断/事件/UI | **采用**：规则集中在一处、不漂移，DSH 侧故障不污染状态；Harness 原文不出 Host |
| B：控制台唯一状态源 + 控制台判断 | 否决：需要把 Harness 回合文本发到控制台 HTTP，隐私面更大，也与“DSH 自己判断”的选择相反 |
| C：双端对等存储 + 同步协议 | 否决：两套规则漂移、冲突合并、测试翻倍，收益不足 |
| 直接扩展/魔改上游两个 bundle | 否决：第三方包不可改；fork 后维护成本高、许可与升级风险大 |

## 5. 架构

```
微信侧
  AccountSession/Agent ──► mood.judge(微信) ──► apply_judged_delta(source=wechat) ──► agent.db
          └─ bus(agent_turn / agent_tool) ──► PetReducer ──► 桌宠（控制台进程）
          └─ inject_block（微信提示词）

Harness 侧（dsh-plugin Host）
  session/event 累积最近一轮 user/assistant 文本
  agent/turn-stopping ──► llm.stream(prompt 来自控制台) ──► POST /api/dsh/mood-event
                                                              └─► apply_judged_delta(source=dsh)
  tool/call|result, turn/end, agent/error ──► POST /api/dsh/pet-event ──► bus ──► 同一只桌宠
  systemPrompt.context ◄── GET /api/dsh/state（60s 刷新，注入 Harness 提示词）

DSH Client
  settings.section「心情与桌宠（融合）」 ──► Host 代理路由 ──► 控制台 /api/dsh/*
  shell.overlay 右下角心情挂饰 ──► 同一代理路由（20s 轮询）
```

**状态源边界**：所有心情数值、事件、注入额度、绑定关系都在控制台；DSH 不持久化业务状态（只保留内存里的会话文本缓存与限流计数）。

## 6. 控制台侧改动

### 6.1 配置段

`app/config.py` 的 `DEFAULTS` 新增：

```python
"dsh": {
    "account_id": "",              # 绑定账号；空 = 未绑定（编辑器默认跟随当前激活账号）
    "judge_enabled": True,         # 是否接受 DSH 判断结果
    "pet_events_enabled": True,    # 是否让 Harness 事件驱动桌宠
    "cross_reason_enabled": True,  # 是否允许 Harness 来历进入微信注入
},
```

- 绑定账号非法/被删时，读取侧回落到 `config.active_id()`，并在 `GET /api/dsh/state` 标记 `boundFallback: true`。
- 开关只影响行为，不删数据。

### 6.2 新模块 `app/dsh_bridge/`

- `api.py`：`APIRouter(prefix="/api/dsh", tags=["dsh"])`；所有端点沿用 `app/pet/api.py` 的本机来源校验（`_require_local` → 403「DSH 融合接口只允许在本机控制台执行」）。
- `service.py`：绑定解析、状态组装、参数校验、限流；不直接碰 SQL，复用 `mood.settings` / `mood.store` / `mood.service` / `pet.supervisor` / `bus`。
- `main.py`：`from .dsh_bridge.api import router as dsh_router`，在 `pet_router` 之后 `include_router`。

### 6.3 心情服务重构（`app/mood/service.py`）

- 从 `_run` 中抽出 **`apply_judged_delta(account_id, slot_id, delta, *, source, reason_limit=60, chat_uid="", now=None)`**：入参是已经判断好的 `{moodDelta, dims, confidence, reason}`，内部执行与微信完全一致的显著阈值、`model.apply_delta`、事件落库（`kind="judge"`、`source=source`）。`_run` 改为“判断 → 调 `apply_judged_delta`”，行为不变（现有 `tests/test_mood_*` 必须继续通过）。
- 新增 `mood.store.last_event_source(account_id, slot_id) -> {"kind","source","reason"} | None`：按 `mood_events` 最新一行读取，用于跨端来历判断（不新增列）。
- `inject_block` 增加跨端分支（顺序在现有“群聊/别的会话降级”之前）：
  - `last_source == "dsh"` 且 `config["dsh"]["cross_reason_enabled"]` 为真、且当前是**私聊**、且注入模式为“说心情 + 刚才那件事” → 保留 event 模式；理由只取 `last_reason` 的前 60 字（说明来源为“刚才在 Harness 里”）。
  - 群聊一律降级为 label；开关关闭时也降级为 label。
  - 微信来源的来历**永不**进入 Harness 提示词（只给数值），这是有意的非对称。
- `state_payload()` 增加 `lastSource` 字段（只读快照），供 DSH 侧展示。

### 6.4 宠物事件

- `PetReducer.on_event` 增加接收 `data.origin`（`"wechat"` / `"dsh"`）与 `data.title`（Harness 会话标题，截断 40 字），映射规则不变：
  - `agent_turn(start)` → `THINKING`
  - `agent_tool(tool_start)` → `WORKING` + 活动名；`agent_tool(done)` → 成功脉冲
  - `log(level=error)` / `agent/error` → `ERROR`
- `bubble.message_for` 增加 `source_label`：`origin == "dsh"` 时气泡前缀「Harness」并可带会话标题；微信侧维持「会话名」。
- 防抖/优先级：Host 侧对 `tool_start` 做 3 秒防抖；桥接层再加一层同类事件最小间隔 1 秒，防连发；`turn_end` 只在没有 error 帧时上报成功脉冲；桌宠队列满时丢帧（既有行为）。

### 6.5 HTTP 端点契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/dsh/state` | 绑定账号、账号列表、共享心情快照、宠物状态、开关、最近 20 条双源时间线、`boundFallback` |
| POST | `/api/dsh/config` | 更新绑定/三个开关；账号必须存在；返回新状态 |
| POST | `/api/dsh/mood-event` | ← DSH 判断结果；返回 `{applied, skipped, mood:{...}}` |
| POST | `/api/dsh/pet-event` | ← DSH 任务事件；返回 `{accepted}` |
| GET | `/api/dsh/timeline` | 双源时间线（`limit` 1–100，默认 20） |
| GET | `/api/dsh/judge-prompt` | `{version, system, schema}`：`system` 是按绑定账号人设渲染好的判断系统提示（已把来源固定为 `dsh`），`schema` 描述期望的 JSON 字段；DSH 只负责拼本轮对话文本并解析结果 |

`POST /api/dsh/mood-event` 请求体：

```json
{
  "mood_delta": -4,
  "dims": {"happy": 1, "sweet": 0, "calm": 0, "worry": 2, "heartache": 0, "sad": 1, "jealous": 0},
  "confidence": 72,
  "reason": "用户把一个麻烦的 bug 交给她，她认真查完了",
  "session": {"id": "…", "title": "修复登录崩溃"}
}
```

校验：`mood_delta` ∈ [-10,10]；`dims` 七项各 ∈ [-10,10]；`confidence` ∈ [0,100]；`reason` 去控制字符、截断 60 字；`judge_enabled=false` → 返回 `applied=false, skipped="judge-disabled"`，不落库。写入的 `chat_uid=""`（账号全局槽位），`source="dsh"`。

`POST /api/dsh/pet-event` 请求体：

```json
{"kind": "tool_start", "tool": "read", "session": {"id": "…", "title": "修复登录崩溃"}}
```

`kind` ∈ `turn_start | tool_start | tool_done | turn_end | error`；`pet_events_enabled=false` 或桌宠未启用/未运行 → `accepted=false`，不报错。

### 6.6 控制台 UI

P2 小改（非闭环必需）：设置页「心情」区域下方加一行只读状态「DSH 已绑定账号 X · 跨端来历：开」，方便从控制台确认融合状态；不新增编辑入口（编辑在 DSH 设置页）。若实现成本超预期可省略，不影响验收。

## 7. DSH 插件侧

### 7.1 Host `lib/fusion.js`

- `createFusion({ ctx, config, api })`，在 `index.js` 的 `apply` 里创建并挂 `ctx.effect` 清理。
- **会话文本缓存**：监听 `session/event`，按 `session.id` 保留最近一条 `user/message` 文本与最近一条 `assistant/message` 文本（各截断 2000 字，内存 LRU 上限 32 个会话）。不写文件、不进日志。
- **判断触发**：监听 `agent/turn-stopping`；若 `config.dsh.judge_enabled`（从控制台状态缓存）且绑定账号存在：
  - 读取控制台 `GET /api/dsh/judge-prompt`（缓存 5 分钟；控制台不可达则本轮跳过），得到 `{system, schema}`；
  - 取 `ctx.get('agentDefaultModel').currentSelection()` 的 `provider/model`；
  - `ctx.get('llm').stream({ provider, model, system, messages: [{ role:'user', content:[{type:'text',text: transcript}] }], maxTokens: 300, temperature: 0 })`，其中 `transcript` 是本轮 user/assistant 文本（各截断 2000 字）；汇总文本后解析 JSON（`moodDelta/dims/confidence/reason`）；
  - 校验后 `POST /api/dsh/mood-event`；失败只记 `ctx.logger.warn`，不重试超过 1 次。
  - 限流：同一账号最小间隔 90 秒、每日最多 40 次判断（内存计数）；未绑定/未启用/空回合直接跳过。
- **宠物事件**：监听 `tool/call`、`tool/result`、`turn/end`、`agent/error`，经 3 秒防抖后 `POST /api/dsh/pet-event`；控制台不可达就丢弃。
- **Harness 提示词注入**：`ctx.get('systemPrompt').context({ name: 'deepfeiyu.mood', order: 0, text: () => cachedMoodLine })`。`cachedMoodLine` 每 60 秒从 `GET /api/dsh/state` 刷新，格式「（你此刻的心情：72/100，略偏开心；刚才：…）」：
  - DSH 来源的来历可以直接用；微信来源只保留数值/情绪词，不带来历；
  - 控制台不可达时返回空字符串（不阻塞 Harness 对话）。
- **清理**：`ctx.effect(() => dispose)` 里清监听、定时器、prompt context，避免热重载后重复注入。

### 7.2 路由扩展（`lib/route.js`）

新增 `fusion.state`、`fusion.config`、`fusion.timeline` 三个同源 POST 端点，代理到控制台 `/api/dsh/*`；与现有 `deps.*` 一样直接透传控制台 body，`config` 的字段做白名单校验（`account_id` 必须来自控制台账号列表，开关只接受布尔）。

### 7.3 Client 设置页

在现有 `settings.section` 设置页「关联 DSH 插件」区块下方新增「心情与桌宠（融合）」：

- 绑定账号下拉（来自 `/api/dsh/state` 的账号列表；显示当前激活标记）；未绑定时说明“绑定后 Harness 与微信共享同一份心情”。
- 三个开关（DSH 判断 / 宠物反映 Harness / 允许 Harness 来历进微信），直接写 `fusion.config`。
- 当前状态：数值、七项情绪、最近一次理由、`boundFallback` 提示。
- 双源时间线（最多 20 条）：标注 `微信` / `Harness` / `自然平复`，不展示 Harness 原文。
- 说明文案：桌宠由控制台进程渲染，这里只控制与查看；「关联 DSH 插件」里的两个上游插件是独立版，不参与共享。

### 7.4 Client 挂饰

- 注册 `shell.overlay`（list slot，`id: deepfeiyu-mood`，`order` 取靠后），自绘 React 组件：
  - 右下角圆形挂饰：心情数值 + 按数值/主导情绪映射的 SVG/emoji 表情（不引入上游 PNG，避免再处理素材许可与静态路由）；
  - 20 秒轮询 `fusion.state`；控制台不可达时半透明显示「—」；
  - 点开：小面板显示七维、最近理由、双源时间线；不提供编辑（编辑在设置页）；
  - 位置固定右下角，不做拖动（YAGNI）。

### 7.5 上游 bundle 的摆放

现有「关联 DSH 插件」安装入口保留，但文案改为「独立版（不与控制台共享状态）」；融合开启时提示“两者同时开会出现两套心情/两只宠物，建议只开融合”。

## 8. 隐私与规则

| 方向 | 允许内容 | 开关 |
|---|---|---|
| DSH → 共享心情库 | 判断结果 + ≤60 字理由；不传原文、不落库 | `judge_enabled` |
| DSH → 微信注入 | 数值 + 情绪词 + 最多一句 Harness 来历 | `cross_reason_enabled`（默认开）；群聊永远只给数值 |
| 微信 → Harness 注入 | 只给数值/情绪词，不带微信来历 | 不可配，固定 |
| 微信 → 共享心情库 | 既有行为（按账号/槽位） | 既有 `mood` 开关 |
| 宠物事件 | 会话标题截断 40 字；工具名；不传消息正文 | `pet_events_enabled` |

## 9. 失败与降级

- 控制台未运行：DSH 设置页显示「控制台未运行」+ 启动按钮（复用现有 `console.start`）；判断/事件直接丢弃并记日志，不阻塞 Harness。
- 判断模型失败 / JSON 非法：丢弃本轮，不改心情；连续失败 3 次后暂停 5 分钟（内存退避）。
- 控制台 4xx/5xx：DSH 侧只记日志，不重试风暴；`fusion.state` 错误原样展示给设置页。
- 绑定账号被删或未绑定：状态回落当前激活账号并标记 `boundFallback`；DSH 判断仍用回落账号，设置页提示重新绑定。
- 插件卸载/热重载：清理监听、定时器、prompt context；旧实例的挂饰随 slot 注销。
- 桌宠未启用：宠物事件接口返回 `accepted=false`，不影响心情。

## 10. 测试计划

- **Python** `tests/test_dsh_bridge.py`
  - 六个端点：形状、本机来源 403、参数校验、未知账号、`boundFallback`；
  - `apply_judged_delta` 与 `_run` 规则一致：无变化不入库、显著/非显著、`minor`、来源列、额度；
  - `inject_block` 跨端规则：开关开/关、私聊/群聊、DSH 来源带短理由、微信来源不带理由进 Harness；
  - `store.last_event_source` 只读查询；
  - 宠物事件映射与 3 秒防抖、`pet_events_enabled` 关闭、桌宠未运行；
  - 账号被删后的回落。
- **Python 既有**：`tests/test_mood_*.py`、`tests/test_pet_*.py` 全量不回归。
- **DSH** `tests/test_dsh_plugin.py`
  - `fusion.*` 路由、监听事件名、prompt context 注册、内存缓存与清理的静态断言；
  - `node --check` 覆盖新文件；
  - 临时假 ctx 脚本（不入库）验证：事件缓存、判断结果解析、限流、控制台不可达降级（不真调模型）。
- **手工验收**
  1. DSH 一轮对话结束 → 控制台时间线出现 `source=dsh` 事件；DSH 设置页与挂饰 20 秒内更新。
  2. Harness 工具调用 → 桌宠进入 WORKING 并回落；气泡带「Harness」。
  3. 微信私聊回复时，`cross_reason_enabled=true` 注入一句 Harness 来历；关闭后只剩数值；群聊永远只有数值。
  4. 控制台停掉后 DSH 设置页显示启动按钮，Harness 对话不受影响。
- **不实测**：真实模型判断的语义质量（只验证结构与规则）；真实微信发送受本机通道限制。

## 11. 非目标

- 不修改/复用上游 `dsh-dafeiyu`、`dsh-feiyu-mood` 的状态或代码；
- 不做 DSH 侧第二个独立桌面宠物窗口；
- 不支持同时绑定多个微信账号；
- 不迁移历史微信/Harness 聊天内容；
- 不做跨设备同步（仅本机）。

## 12. 实施分期与验收标准

**P1：数据层 + 桥接 + 设置页**
- 控制台 `dsh` 配置、`app/dsh_bridge/`、心情重构、宠物事件接入；
- DSH Host `fusion.js`（判断、宠物事件、限流）、`fusion.*` 路由；
- DSH 设置页绑定/开关/状态/时间线。
- 验收：`tests/test_dsh_bridge.py` 与既有 mood/pet 测试全绿；手工验收第 1、2、4 条。

**P2：挂饰与提示词注入**
- DSH `systemPrompt.context` 心情注入、`shell.overlay` 挂饰；
- 上游 bundle 文案调整、控制台只读状态行（可选）。
- 验收：手工验收第 3 条；Harness 回复中能体现共享心情（语义质量不作硬性断言）。

## 13. 风险与回滚

- **模型成本**：DSH 判断会额外消耗 token；用 90 秒间隔 + 每日 40 次 + 300 maxTokens 控制，开关可关。
- **隐私**：Harness 来源进微信是用户明确选择的；默认只在私聊、开关可关；只传 ≤60 字理由，不传原文。
- **热重载重复注入**：prompt context 与事件监听必须在 effect disposer 里清理。
- **回滚**：控制台侧新增模块与 `dsh` 配置段可整体删除；DSH 侧新增文件与路由可删；两处都不改动既有 mood/pet 行为（`apply_judged_delta` 重构要求既有测试全绿作为保护）。
- **数据**：不新增表/列，不改既有 `mood_slots`/`mood_events` 结构；回滚不需要数据迁移。
