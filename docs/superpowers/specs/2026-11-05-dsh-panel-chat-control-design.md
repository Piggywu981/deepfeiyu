# DSH 面板会话界面补齐（期1 · A 会话控制）设计规格

- 状态：待用户审查
- 日期：2026-11-05
- 基线：`e36f9ef`（分支 `main`，本规格只落文档，不改代码）
- 需求来源：用户报告「dsh 中的 deep 肥鱼界面和网页控制台的会话界面相差很多，缺少很多功能」+ 三轮澄清（范围 = A/B/C/D 全选、路线 = 四期增量、本期 = A 会话控制）
- 预期文档位置与本文件一致：`docs/superpowers/specs/2026-11-05-dsh-panel-chat-control-design.md`

---

## 1. 背景与差距（两份只读盘点的结论）

对照对象：网页控制台会话页（`app/static/index.html:217-287` + `app/static/app.js:640-1142`）vs DSH 插件中央面板（`dsh-plugin/client.js` 的 `DeepFeiyuPanel`，约 363-921 行）。两侧数据同源（都打控制台 API），差的是**操作面**与**展示面**。

| 分组 | 控制台有、面板没有 | 归期 |
|---|---|---|
| A 会话控制 | 会话级 AI 开关；「接管中」点击即恢复；多选批量（多选/全选/已选计数/设为人工/设为 AI）；线程头状态副标题（群聊/私聊 · AI 状态 · AI 判断拦截原因） | **本期**（拦截原因依赖 B 的 WS，期1 只做到「AI 状态」） |
| B 实时性 | `/ws` WebSocket 实时推送（消息、chats_dirty、status、accounts_dirty）；AI「思考中…/正在调用工具 N」实时进度；`policy` 拦截原因 | 期2 |
| C 消息富展示 | 图片内联缩略图+点开原图；表情渲染（★收藏/⬇下载）；引用消息渲染；AI 来源标 + ¥ 成本（含 tokens 提示）；思考过程/工具调用折叠卡；拍一拍居中；「已撤回」文字标签；会话头像；线程头账单行 | 期3 |
| D 发送增强 | 表情面板三 tab（微信表情插文本 / 收藏直发 / 标签墙）→ `send-emoji` | 期4 |

**不是差距（两侧都没有，明确不做）**：消息分页/加载更多（控制台硬编码 `limit=100`，`app/static/app.js:759`）、置顶/归档/删除、消息复制、图片/文件发送、@ 补全、日期分隔线、已读回执。

**面板已有、保持不动**：控制台启停、账号 chip 切换、今日统计、搜索会话、未读角标、4s 轮询、文本发送（`source=human`）、设置页四标签、心情挂饰。

## 2. 已确认决策

| 项 | 结论 |
|---|---|
| 交付路线 | **方案一：四期增量**（A→B→C→D），每期独立验收、独立回滚；本期只交付 A |
| 本期范围 | 线程头 AI 开关 + 接管徽标 + 恢复按钮 + 状态副标题；列表「接管中」可点恢复；多选批量 |
| 明确否决 | 方案二（A+B 合并一期：改动横跨三层、回滚粒度粗）；方案三（控制台加 REST + 1s 轮询：追不上 `agent_reasoning` 流、且要动 Python 本体，与插件「不改既有 Python 行为」定位冲突） |
| Python 侧 | **零改动**：只用控制台现成 API，不新增端点、不改既有行为 |
| 依赖 | **零新增**：不碰 `requirements*.txt`、`vendor/`、npm 依赖 |
| 面板操作审批 | 不走 Harness 审批——与既有 `send.text`（`source=human`，等价控制台人工点击）同一定位；写入 README 安全节 |
| 实时性 | 期1 维持 4s 轮询现状；期2 再引入 WS 桥 |

## 3. 技术事实（已核实）

1. **会话行字段现成**：`GET /api/chats` 每行含 `uid / name / is_group / ai_enabled / paused`（`paused` 由 `pause_until > now` 算出，`app/db.py:688-695`）——期1 不需要任何新读端点。
2. **三个控制台端点签名**（`app/main.py`）：
   - `POST /api/chats/{uid}/ai`，body `ToggleBody{enabled: bool}`（`:1067`，body 定义 `:1042`）
   - `POST /api/chats/{uid}/resume`，无 body（`:1075`）
   - `POST /api/chats/batch-ai`，body `BatchAIBody{uids: list[str], enabled: bool}`（`:1051`，body 定义 `:1046`）
   三者都只改库并 `bus.publish(chats_dirty)`，不触达微信通道。
3. **拦截原因只经 WS**：`policy` / `message` / `agent_reasoning` / `agent_tool` 事件由 `app/wechat/manager.py:638 / :614 / :1502 / :1237` 发布进进程内 `bus`，只经 `@app.websocket("/ws")`（`app/main.py:2131`）下发，REST 无对应端点——期2 必须走 WS 桥的依据。
4. **Node 24 有全局 `WebSocket`**：实测 `node -e "typeof WebSocket"` → `function`（v24.12.0）；浏览器侧原生有 `EventSource`。期2 的「Host 连控制台 WS → 同源 SSE 推 Client」不需要任何新依赖。
5. **`webServer` 服务支持注册任意路由**：`register(route)` / `registerUpgrade(route)`（Host Service 目录），面板现有 `/deepfeiyu` 前缀路由即由它注册（`dsh-plugin/lib/route.js:194`）。
6. **安装包与源码一致**：profile 里 `node_modules/deepfeiyu-dsh` 与 `dsh-plugin/` 全文件 MD5 比对 `diffcount=0`——改源码后 `install_bundle` 即可预览。
7. **面板发送已确立「等价人工」先例**：`route.js:67-76` 里 `send.text` 以 `source='human'` 转发，`sendGate` 参数解构但未使用（`route.js:45`）；面板不走审批是既有设计而非本次新增口子。
8. **控制台多选交互**（照抄对齐，`app/static/app.js:4461-4493`）：「接管中」徽标点击 = 就地恢复且**不打开会话**；多选模式点行 = 只切换选中、不打开会话；全选作用于**当前搜索过滤后的可见列表**；退出多选清空选中集。
9. **注册模式**：`lib/route.js` 用 `routes` 表（endpoint → 校验 + 转发），错误统一 `INVALID_ARGS`/`ConsoleDownError`/`ConsoleApiError` 分类返回；`lib/ops.js` 的 `call()` 统一带 `x-access-token` 与超时。新增三端点只加表项与三个 ops 函数。
10. **测试现状**：`tests/test_dsh_plugin.py` 30 个用例（静态一致性 + fake-ctx 行为测试 + `test_js_syntax`）；Python 侧行为由既有全量 pytest 覆盖。

## 4. 方案取舍

| 方案 | 结论 |
|---|---|
| **一：四期增量，本期 A** | **采用**：每期一个 PR 粒度、可单独回滚；A 无实时依赖、最快见效 |
| 二：A+B 合并一期 | 否决：副标题拦截原因确实要等 B，但合并后一次改动横跨 Host 路由 + WS 桥 + Client 事件三层，`client.js` 已 2278 行，验收与回滚粒度都变粗 |
| 三：控制台加 REST + 1s 轮询 | 否决：`agent_reasoning` 高频流 1s 轮询也追不上；要改 Python 控制台本体，回归面扩大到 Python 侧，与插件定位冲突 |

## 5. 设计（期1）

### 5.1 范围与非目标

**做**：线程头 AI 开关、接管徽标、恢复 AI 按钮、状态副标题；列表「接管中」可点恢复；多选批量全套。数据全部取自已有 `chats.list` 返回。

**期1 非目标**：拦截原因副标题（期2）、实时推送（期2）、图片/表情/引用/成本等富展示（期3）、表情发送（期4）、任何 Python 改动、任何新依赖、任何审批语义变化。

### 5.2 Host 端（`dsh-plugin/lib/route.js` + `lib/ops.js`）

`routes` 表新增三项，全部经既有 `handler`（`connection.requestRejection` 同源鉴权、POST-only、body ≤256KB、未知 endpoint 404、错误分类不变）：

| 面板端点 | 入参校验（不通过抛 `INVALID_ARGS`） | 转发 |
|---|---|---|
| `chat.ai` | `uid` trim 后非空字符串；`enabled` 必须是布尔 | `POST /api/chats/{uid}/ai` |
| `chat.resume` | `uid` trim 后非空字符串 | `POST /api/chats/{uid}/resume` |
| `chats.batchAi` | `uids` 为数组、≤200 项、每项 trim 后非空字符串、去重；`enabled` 布尔 | `POST /api/chats/batch-ai` |

`ops.js` 新增三个函数（复用 `call()`，默认 5s 超时，body 只发白名单字段）：

```js
chatAi: (uid, enabled) => call(`/api/chats/${encodeURIComponent(uid)}/ai`, { method: 'POST', body: { enabled } }),
chatResume: (uid) => call(`/api/chats/${encodeURIComponent(uid)}/resume`, { method: 'POST', body: {} }),
chatsBatchAi: (uids, enabled) => call('/api/chats/batch-ai', { method: 'POST', body: { uids, enabled } }),
```

控制台侧本机来源限制、访问令牌校验原样生效；插件不新增 shell 执行能力。

### 5.3 Client 端（`dsh-plugin/client.js`）

- **线程头**（`renderThread` 的 thread-head，现为「会话名 + 群聊标签 + 最近 N 条」）改为两行：
  - 行 1：会话名 + `群聊`/`私聊` 标签；
  - 行 2（副标题）：`AI 自动回复 | 人工接管中 | AI 已关闭`，行尾保留「最近 N 条」；
  - 右侧控件：`人工接管中` 徽标（`paused` 时显示）、`恢复 AI` 按钮（`paused` 时显示）、AI 开关（checkbox，`checked = ai_enabled`）。
- **会话列表**（`renderChatRow`）：`人工` 标签由纯展示改为**可点击**——点击即恢复该会话 AI 且不打开会话（事件先 `stopPropagation` 再处理，避免触发行选中/打开）；`AI`、`群` 标签保持展示；`paused` 行加 `title` 提示。
- **多选**：
  - 列表头新增「多选 / 退出多选」切换按钮；进入多选时显示工具条：`全选(取消全选) · 已选 N · 设为人工 · 设为 AI`；
  - 多选态点行只切换选中（高亮），不打开会话；正常态行为不变；
  - 全选作用于**当前关键词过滤后的可见行**（与控制台一致）；
  - 选中集 `Set<uid>`；`chats.list` 刷新后自动剔除已消失的 uid；退出多选清空选中集；
  - `设为人工` = `enabled:false`，`设为 AI` = `enabled:true`，成功后提示「已把 N 个会话设为…」（对齐控制台文案）。
- **状态来源**：`chat`（线程头）、`chats`（列表行）都已含 `ai_enabled` / `paused` / `is_group`，无新字段、无新读端点。

### 5.4 数据流与刷新

操作 → 面板 `call()` → Host 路由校验 → `ops` 转发 → 控制台改库并发布 `chats_dirty`（控制台自己消费）→ 面板操作成功后：

1. `loadChats()` 重拉会话列表；
2. **若被操作的 uid 是当前选中会话，必须同步更新 `chat` 状态**——线程头读的是 `chat || chats.find(...)`（`dsh-plugin/client.js:398`），`chat` 来自 `messages.list` 返回的 `chat` 字段、与列表是两份数据，只刷列表线程头会停留在旧状态。做法：把 `ai_enabled` / `paused` 的新值合并进 `chat`（乐观合并），随后由既有的 `messages.list` 4s 轮询（`dsh-plugin/client.js:522-536`）重拉自然校正；
3. 4s 轮询兜底收敛。期1 不引入 WS。

### 5.5 错误处理

- 控制台未运行 → 所有新按钮沿用 `running` 判断禁用；操作期间失败写行内 alert（复用 `sendError` 同款样式）+ 顶部 error bar，可关闭。
- 控制台返回 4xx/5xx → `ConsoleApiError` → `describeError()` 输出「控制台返回 <status>：<detail>」。
- 批量部分 uid 不存在 → 控制台返回 `changed` 数，面板提示「已把 N 个会话设为…」；`changed=0` 时同样如实提示。
- 切换账号 / 切换会话 → 清空选中集与行内错误，避免误操作到旧账号的 uid。

### 5.6 安全

- 不新增 shell、不改 Python、不加依赖；令牌只在 Host 内用于 `127.0.0.1`，不回显、不进日志。
- 三个端点只接受白名单字段，`uids` 上限 200 与控制台 `chats` limit 对齐，防滥用。
- 面板操作**不走 Harness 审批**：定位与既有 `send.text`（`source=human`）一致——等价于人走到控制台前点一下，且破坏性低于发消息（可随时改回）。写入 `dsh-plugin/README.md` 安全节与本规格第 8 节风险说明。

## 6. 改动清单（文件级）

| 文件 | 改动 |
|---|---|
| `dsh-plugin/lib/route.js` | routes 表 +3 项（`chat.ai` / `chat.resume` / `chats.batchAi`） |
| `dsh-plugin/lib/ops.js` | +3 个转发函数 |
| `dsh-plugin/client.js` | 线程头两行 + 三个控件；列表「接管中」可点；多选状态与工具条；重拉逻辑 |
| `dsh-plugin/README.md` | 面板组成、安全节（面板操作不走审批的定位说明） |
| `docs/使用说明.md` | §16.1「面板能做什么」同步 |
| `CHANGELOG.md` | 新段落（改动 + 验证状态） |
| `tests/test_dsh_plugin.py` | 新增端点注册/ops 路径/client 标记断言 + 失败路径用例 |
| **不改** | `app/**/*.py`、`dsh-plugin/index.js`、`lib/tools.js`、`lib/approval.js`、许可与法律文档 |

## 7. 测试计划

1. `tests/test_dsh_plugin.py` 新增：
   - `route.js` 源码含三个 endpoint 注册与对应 `INVALID_ARGS` 校验（uid 空、enabled 非布尔、uids 超 200/含空项）；
   - `ops.js` 源码含三个控制台路径与 `method: 'POST'`；
   - `client.js` 源码含多选/恢复 AI/AI 开关关键标记；
   - 沿用 fake-ctx 风格给三个新路由补行为用例（成功转发、控制台 down、参数非法三条路径）；
   - 既有 30 例不许改弱，`test_js_syntax` 自动覆盖语法。
2. `node --check` 覆盖 `client.js` / `lib/route.js` / `lib/ops.js`。
3. 全量 `pytest -q`：Python 侧零改动，只确认无回归；如实报告失败与未跑项。
4. 实机：`install_bundle` 重装 → 以 `application: applied` 为准 → 面板逐项操作，控制台会话页同步验证（同一数据源）。
5. 视觉与交互细节由用户最终验收（本仓库自动化测试不覆盖面板视觉）。

## 8. 风险、回滚与合规

| 风险 | 说明与对策 |
|---|---|
| 面板可改 AI 开关而不经 Harness 审批 | 与 `send.text` 同定位（等价控制台人工点击，破坏性更低）；README 安全节明示；服务端令牌/本机限制不变 |
| 乐观更新与控制台状态短暂不一致 | 操作后立即重拉 + 4s 轮询兜底，以控制台返回为准 |
| 批量误操作 | 多选有「已选 N」计数与全选可见范围限定；上限 200；切账号清空选中集 |
| `client.js` 继续膨胀（现 2278 行） | 期1 只加渲染函数与状态，不重构既有结构；重构留到确有必要时单独提出 |

- **回滚**：单提交完成，`git revert` 即可；无数据、无配置、无迁移。
- **合规**：不改 `LICENSE`/`EULA*`/`NOTICE`；不新增依赖与外联；提交内容不含密钥、真实聊天记录、本机绝对路径（跑 `tools/release_snapshot.py --scan-tracked .` 与 `git diff --check` 自查）。

## 9. 验收标准（可观察）

1. 面板线程头出现 AI 开关；切换后控制台会话页同会话标签/开关同步变化（同一数据源）。
2. 会话处于人工接管时，面板列表「人工」标签可点击，点后恢复 AI、控制台「接管中」消失，且**不打开该会话**。
3. 多选 → 全选 → 设为人工/AI：控制台对应会话标签批量变化，面板提示「已把 N 个会话设为…」。
4. 副标题显示 `群聊|私聊 · AI 自动回复|人工接管中|AI 已关闭`（期1 不含「AI 判断」）。
5. 控制台未运行时新按钮禁用且有可读提示，不静默失败。
6. `pytest tests/test_dsh_plugin.py -q` 与 `node --check` 全过；全量 `pytest -q` 结果如实报告（Python 侧零改动）。
7. `install_bundle` 后 `application: applied`，实机操作通过，视觉细节用户验收。

## 10. 非目标与后续分期概要

- **期2（B 实时桥）**：Host 用全局 `WebSocket` 连控制台 `/ws?token=`，经同源 `GET /deepfeiyu/stream`（SSE，复用 `requestRejection` 鉴权）推给 Client；断线重连，失败回落现有 4s 轮询。补齐：拦截原因副标题、消息即时上屏、AI 思考/工具实时进度、账号与会话状态即时刷新。到点另行细化设计。
- **期3（C 富展示）**：Host 代理图片/表情字节流（带令牌），前端渲染图片缩略图、表情收藏/下载、引用块、AI 标与 ¥ 成本、思考/工具折叠卡、拍一拍居中、已撤回标签、头像、账单行。
- **期4（D 表情发送）**：表情面板三 tab（微信表情插文本 / 收藏直发 / 标签墙）→ `send-emoji`。
- **期1 已知缺口**：副标题没有「AI 判断：<拦截原因>」，等期2 补上；这是分期的有意取舍，不是遗漏。

## 附录 A：Issue 草稿（供人类提交，AI 不代开）

> **标题**：DSH 面板会话界面与控制台差距补齐（分四期，期1 会话控制）
>
> **问题**：Harness 侧的 Deep肥鱼面板只有会话浏览与文本发送，控制台会话页的 AI 开关、人工接管恢复、多选批量操作都没有，用户必须切到完整控制台才能做这些操作。影响所有用面板日常管会话的人。
>
> **范围**：期1 只做会话控制（Host 三个转发端点 + 面板 UI）；不改 Python、不加依赖；实时消息/富展示/表情发送分属期2-4，另行提交。
>
> **验收标准**：见设计规格第 9 节七条。
>
> **风险**：面板操作不走 Harness 审批（与既有 `send.text` 同定位，等价控制台人工点击）；服务端令牌与本机限制不变。
>
> **被否决的替代方案**：A+B 合并一期（回滚粒度粗）；控制台加 REST + 1s 轮询（追不上高频事件且要动 Python 本体）。
