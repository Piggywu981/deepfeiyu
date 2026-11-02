# 完整设置缝进插件设置 设计规格

- 状态：待用户审查
- 日期：2026-11-02
- 基线：`dsh-weiyu` @ `663252a`（工作区干净）
- 需求来源：用户「把整个完整控制台缝到dsh中，并把完整设置缝到插件设置中」+ 澄清（先做子项目 A：完整设置；页内 5 标签；含账号 CRUD 与长期记忆增删）
- 与其它子项目的关系：「完整控制台进 DSH」已拆为 A（完整设置）/ B（只读高频页 + 代理）/ C（逐页原生移植）/ D（智能体 SSE）。本规格**只覆盖 A**。

---

## 1. 背景与问题

控制台的「设置」页是日常唯一需要离开 DSH 去改的地方：模型档、人格预设、自动回复策略、账号与通道、依赖、更新、边界全在 `http://127.0.0.1:8765` 的网页里。DSH 插件设置页目前只有三块：可选功能依赖、关联 DSH 插件、心情与桌宠（融合）。

目标：在 DSH 的「设置 → Deep肥鱼」里完成控制台设置的全部常用操作，不用再打开控制台。

## 2. 已确认决策

| 项 | 结论 |
|---|---|
| 子项目顺序 | 先做 A（完整设置），其它页面后续单独规格 |
| 页面布局 | 一个设置页内的 5 个标签，懒加载 |
| 账号范围 | 账号 CRUD + 长期记忆增删 + 检测客户端/环境自检 + 访问令牌 |
| 数据层 | 显式 Host 路由转发控制台既有 API；插件不新增业务逻辑 |
| 写入方式 | 只发送窄补丁，绝不回传整份 `GET /api/settings` |
| 密钥 | 沿用控制台掩码契约，插件不新增密钥处理 |
| 代理 | 不做通用代理；每个端点单独登记 |
| 分期 | P1：标签壳 + 模型与人格 + 自动回复 + 更新与边界 + 依赖与融合迁移；P2：账号与通道 |

## 3. 技术事实（已核实）

1. **Client 单文件约束**：`dsh-plugin/client.js` 通过 `window.__ModuleLoader__.load({ factory(require) {...} })` 加载，`require` 只解析宿主模块，**不能 import 本地文件**。新增的通用控件与 5 个标签页必须都放在 `client.js` 内，按节组织。
2. **设置页控制规模**：控制台设置页 9 张卡片、约 78 个控件；其中的模型档是动态渲染（`ai.profiles` 列表 + `active_profile`）。
3. **配置读取**：`GET /api/settings` 返回 `config.public()`：
   - `ai.api_key` 不返回真值，只给 `configured` 与占位提示；
   - `ai.profiles` 是投影（`id/name/description/base_url/model/key_env/configured`）；
   - `webhook.token` 与 `server.access_token` 返回 `••••••••` + 后 4 位；
   - `accounts` 已含 `persona/wcferry/weixin4/webhook` 等编辑所需字段。
4. **配置写入**：`PUT /api/settings` 走 `config.save()`：
   - `ai.api_key` 为空或以 `•` 开头时不覆盖真值；真值只写 `.env`，不进 `config.json`；
   - `_unmask_secrets()` 把未修改的掩码串还原为上一次真值；
   - **不要回传 `ai.profiles` 数组**（会把公开投影写回配置）；切档只发 `{ai:{active_profile}}`。
5. **模型档切换语义**（控制台现状）：切档前若表单里的 `base_url/model` 与当前档不同，先 PUT 回当前档再切，避免未保存内容被吞；插件复刻这一行为。
6. **账号 CRUD**：
   - `POST /api/accounts` 新增；`PUT /api/accounts/{id}` 保存（通道在后台重启，约 10–30 秒）；`DELETE /api/accounts/{id}` 删除；`POST /api/accounts/{id}/activate` 切号。
   - 保存体形状：`{name, nickname, persona:{persona_name,system_prompt}, driver, enabled, wcferry:{host,port}, weixin4:{account_dir}, webhook:{callback_url,token}}`。
   - `driver` ∈ `auto | wcferry | weixin4 | webhook`。
7. **记忆**：`GET /api/accounts/{id}/memories` → `{items, stats}`；`POST` 体 `{content}`；`DELETE /api/accounts/{id}/memories/{mid}`。
8. **人格预设**：`GET /api/personas` → `{ok, personas}`；`POST` 体 `{name, persona_name, system_prompt}`；`DELETE /api/personas/{name}`；`POST /api/personas/sync_wiki`。
9. **诊断/实例**：`GET /api/diagnose`、`GET /api/instances`，只读列表。
10. **更新检查**：`GET /api/update/check?force=1` → 含 `git_repo / error / has_update / remote_short` 等字段。
11. **现有区块**：`DeepFeiyuSettingsPage` 里已有「可选功能依赖」「关联 DSH 插件」「心情与桌宠（融合）」三块，本次迁移到新标签下，不重写。
12. **鉴权**：插件路由先 `ctx.connection.requestRejection`；控制台侧继续强制本机来源（未设令牌时）与写操作本机限制。

## 4. 架构与数据流

```
DSH 设置页 Deep肥鱼
 └─ 标签壳（5 个 tab，懒加载）
     └─ call('settings.get' | 'accounts.*' | ...)   // 同源 POST /deepfeiyu/<endpoint>
         └─ Host 路由（lib/route.js）
             └─ 控制台 HTTP API（127.0.0.1:8765，带令牌）
                 └─ config.load/save、db、hub 等既有实现（唯一状态源）
```

- 控制台是唯一状态源；插件不缓存业务状态，保存成功后重拉该标签数据。
- 每个标签独立 `{loading, error, draft}`；一个标签失败不影响其它标签。
- 保存只发**窄补丁**：只包含本次表单涉及的字段。

## 5. 页面设计

### 5.1 标签一：模型与人格

内容：模型档 chips、Base URL、API Key（只写）、模型名、温度、视觉/联网/记忆开关、记忆 v2 参数（档案开关/上下文长度/条数/压缩阈值）、Embedding（开关/地址/模型/密钥环境变量）、系统提示词、人格名、人格预设。

- 档 chips 点击：若当前表单的 `base_url/model` 被改过 → 先 `settings.put {ai:{base_url,model}}` 回写当前档，再 `settings.put {ai:{active_profile:id}}`，然后重拉。
- 保存：`settings.put {ai:{base_url,api_key,model,temperature,vision_enabled,web_enabled,memory_enabled,system_prompt,persona_name}, memory:{profile_enabled,context_chars,max_items,compress_after_messages,embedding:{enabled,base_url,model,key_env}}}`。`api_key` 留空表示不改。
- 「测试连接」：`settings.testAi {base_url?,api_key?,model?}`（空值由控制台回落到已存配置）。
- 人格预设：列表当前人格文件；「应用」把 `persona_name/system_prompt` 填入草稿（不自动保存）；「保存为预设」→ `personas.add {name,persona_name,system_prompt}`；「删除」→ `personas.delete {name}`；「从图集同步」→ `personas.syncWiki`。

### 5.2 标签二：自动回复

内容：自动回复策略（总开关、默认全 AI 接管、群聊需 @、表情回复、拍一拍回复、秒回、仅白名单、白/黑名单、最短间隔、日上限、接管暂停、前缀、回复超时、超时兜底、拆分回复）+ 表情包/串子（回复必带表情、自动收集、仅 AI 娘、串子模式、串子站）+ 跨对话（开关、转告、串门、日上限、串门冷却分钟）。

- 保存：`settings.put {auto:{...}, ai:{emoji_with_reply,emoji_auto_collect,wiki_emoji_only,chuanzi_mode,chuanzi_site_emoji}, cross:{enabled,relay,visit,daily_limit,visit_gap_sec}}`。
- 白/黑名单为每行一个的 textarea，提交时按行 trim 过滤空行。
- 跨对话的 `visit_gap_sec` 在 UI 上是分钟，提交时 ×60（与控制台一致）。

### 5.3 标签三：账号与通道

内容：账号 tabs、「＋ 新增账号」、账号表单（名称、昵称、人格名、账号专属人设、驱动、启用、驱动专属字段）、保存/删除、长期记忆列表与添加、检测到的微信客户端、环境自检、访问令牌。

- 账号 tabs 来自 `settings.get` 的 `accounts`；当前激活账号有标记；点击切换编辑对象（不自动切号）。
- 「新增账号」：`accounts.create {name:"新账号"}` → 重拉 → 选中新账号。
- 「保存账号」：`accounts.update {account_id, body}`，body 形状见 §3.6；保存后显示「通道重启中（约 10–30 秒）」并提供刷新。
- 「删除账号」：二次确认 → `accounts.delete {account_id}`。
- 驱动字段按 `driver` 显示：`wcferry` → host/port；`weixin4` → 账号目录下拉（自动选择 + 实例列表）；`webhook` → callback_url/token；`auto` 不显示专属字段。
- 长期记忆：`memories.list {account_id}` 渲染 `items`；添加 `memories.add {account_id, content}`；删除 `memories.delete {account_id, mid}`，二次确认。
- 检测客户端：`instances.get` 只读列表；环境自检：`diagnose.get` 只读列表（状态 + 说明）。
- 访问令牌：掩码回显；保存 `settings.put {server:{access_token}}`，留空 = 清除（与控制台一致）。

### 5.4 标签四：依赖与融合

把现有三块原样迁入：可选功能依赖、关联 DSH 插件、心情与桌宠（融合）。不改变其行为与文案。

### 5.5 标签五：更新与边界

- 检查更新：`update.check?force=1`，显示当前版本、是否有更新、仓库入口；不是 git 安装时提示无法在线比对。
- 使用边界：静态文案（与控制台设置页一致：账号风险、版本限制、能力边界、数据、协议），协议处提供跳转 EULA 的入口（用宿主浏览器标签打开 `http://127.0.0.1:8765/eula`）。

## 6. Host 路由契约

在 `lib/ops.js` 新增控制台调用，在 `lib/route.js` 新增同源 POST 端点：

| endpoint | 控制台调用 | 请求体 | 说明 |
|---|---|---|---|
| `settings.get` | `GET /api/settings` | `{}` | 透传 |
| `settings.put` | `PUT /api/settings` | `{patch}` | 只允许对象；透传 |
| `settings.testAi` | `POST /api/settings/test-ai` | `{base_url?,api_key?,model?}` | 透传 |
| `accounts.create` | `POST /api/accounts` | `{name?}` | 透传 |
| `accounts.update` | `PUT /api/accounts/{id}` | `{account_id, body}` | id 非空校验 |
| `accounts.delete` | `DELETE /api/accounts/{id}` | `{account_id}` | id 非空校验 |
| `memories.list` | `GET /api/accounts/{id}/memories` | `{account_id}` | 透传 `{items,stats}` |
| `memories.add` | `POST /api/accounts/{id}/memories` | `{account_id, content}` | content 非空 |
| `memories.delete` | `DELETE /api/accounts/{id}/memories/{mid}` | `{account_id, mid}` | 两者非空 |
| `personas.list` | `GET /api/personas` | `{}` | 透传 |
| `personas.add` | `POST /api/personas` | `{name,persona_name,system_prompt}` | 透传 |
| `personas.delete` | `DELETE /api/personas/{name}` | `{name}` | name 非空 |
| `personas.syncWiki` | `POST /api/personas/sync_wiki` | `{}` | 透传 |
| `instances.get` | `GET /api/instances` | `{}` | 只读 |
| `diagnose.get` | `GET /api/diagnose` | `{}` | 只读 |
| `update.check` | `GET /api/update/check?force=1` | `{}` | 只读 |

- 现有 `accounts.list`、`accounts.activate` 保留（面板用），设置标签用 `settings.get` 里的 `accounts` 以拿到完整编辑字段。
- 错误：控制台不可达 → `CONSOLE_DOWN`（界面显示启动控制台）；4xx/5xx → `CONSOLE_HTTP`（取 `detail` 文案）。
- **不做通用代理**：端点逐个登记，未登记路径一律 404。

## 7. Client 结构

- `DeepFeiyuSettingsPage` 改为标签壳：
  - `const [tab, setTab] = React.useState('deps')`（默认「依赖与融合」，保持现有用户习惯）
  - 每个标签一个 `renderXxxTab()` + 一个 `loadXxx()`；首次打开该标签才拉数据，之后复用缓存直到手动刷新或保存。
- 通用控件（函数组件，放在 `client.js` 顶部区域）：`Field`、`ToggleRow`、`SelectField`、`TextArea`、`TabBar`、`StatusLine`、`DangerButton`。
- 草稿状态：每个标签一个 `draft` 对象 + `dirty` 标记；保存成功后以服务端返回的数据重置草稿；保存失败保留草稿并显示错误。
- 掩码字段：输入框显示占位符（`••••••••`），`onChange` 后记录"已修改"；未修改的掩码串原样提交，由控制台还原。
- 账号保存后的通道重启：显示一行「通道重启中（约 10–30 秒）」，提供手动刷新按钮；不自动轮询。
- 所有用户可见文案中文；类名统一 `dsh-deepfeiyu-` 前缀；颜色只用 `--dsw-alias-*` 令牌。

## 8. 安全

- 所有新端点走 `ctx.connection.requestRejection`，未通过 401/403。
- 控制台侧本机限制、写操作本机限制、访问令牌校验保持不变；插件不绕过任何闸门。
- 只发窄补丁；禁止把 `GET /api/settings` 的返回原样 PUT 回去。
- 密钥只写不读：Key 输入框不显示真值；掩码串未修改时原样回传。
- 删除账号/记忆需二次确认。
- 不新增 npm 依赖、不新增密钥落盘、不新增遥测/外联。

## 9. 失败与降级

| 场景 | 行为 |
|---|---|
| 控制台未运行 | 标签内显示「控制台未运行」+「启动控制台」；不阻塞标签切换 |
| 单标签加载失败 | 该标签内显示错误与重试；其它标签正常 |
| 保存失败 | 保留草稿并显示服务端错误；不重置表单 |
| 账号保存后通道重启 | 提示重启中，提供手动刷新；状态可能短暂 `starting` |
| 控制台返回 401/403 | 显示「控制台访问令牌不匹配」并提示检查令牌 |
| 版本检查不是 git 安装 | 显示「解压安装的副本不做在线比对」，不报错 |

## 10. 测试

- `tests/test_dsh_plugin.py` 补静态断言：
  - `lib/route.js` 含 16 个新 endpoint；`lib/ops.js` 含对应控制台路径；
  - `client.js` 含 5 个标签文案与 `TabBar`，且不出现「把整份 settings 原样 PUT」模式（断言例如不出现 `JSON.stringify(draft)` 直接发 settings.get 结果）。
- 新增 `tests/test_dsh_settings_contract.py`：
  - 从 `app.main.app.routes` 断言本规格用到的控制台路径全部存在（防路径拼写错误）；
  - 断言 `GET /api/settings` 返回的 `ai.api_key` 不含真值（用 TestClient + monkeypatch 配置）；
  - 断言掩码保存契约：PUT 掩码串不覆盖真值（复用现有 `_unmask_secrets` 行为）。
- 临时假 ctx 脚本（不入库）：验证 `settings.put` 窄补丁透传、`accounts.update` 校验、掩码串原样回传、错误映射。
- 手工验收（P1 覆盖第 1–3、6 条的 4 个标签部分；P2 完成后覆盖全部 5 个标签）：
  1. 标签都能打开、懒加载、刷新；
  2. 模型档切换与保存、测试连接；
  3. 自动回复保存后控制台页面值一致；
  4. 账号新增/保存/切号/删除；记忆添加/删除；诊断与实例列表；
  5. 掩码字段不显示真值、不修改保存后真值不变；
  6. 停掉控制台后标签显示启动入口，启动后恢复。

## 11. 分期与验收

**P1：标签壳 + 模型与人格 + 自动回复 + 更新与边界 + 依赖与融合迁移**
- 本阶段标签栏只有 **4 个**标签（模型与人格 / 自动回复 / 依赖与融合 / 更新与边界）；「账号与通道」标签在 P2 才加入标签栏。
- 验收：`settings.get/put/testAi` + `personas.*` + `update.check` 路由与表单可用；依赖/融合三块无回归；控制台停机显示启动入口；静态与契约测试通过。

**P2：账号与通道**
- 标签栏补齐第 5 个标签「账号与通道」。
- 验收：账号 CRUD、切号、记忆增删、实例/诊断展示、访问令牌掩码回显；手工验收第 4、5 条通过。

**实现计划拆分**：本规格是一份；实现拆成两份计划文档（P1 一份、P2 一份），各自独立可交付，P2 依赖 P1 的标签壳与控件组件。

## 12. 非目标

- 不移植控制台其他 13 个页面（会话/智能体/记忆/成长/心情/搭话/桌宠/朋友圈/表情包/能力/说明/协议）；
- 不做 iframe 内嵌；
- 不做离线编辑或本地草稿持久化；
- 不做多账号并发编辑/冲突合并；
- 不修改控制台既有 API 语义与鉴权；
- 不把「能力」页的开关并进设置（其不在控制台设置页内）。

## 13. 风险与回滚

- **client.js 体积**：现约 1500 行，P1+P2 预计 +1000 行左右。缓解：控件组件化、标签分节、避免重复表单代码；若继续膨胀，后续单独规格拆分（但受单文件模块约束，暂不拆）。
- **配置写坏风险**：窄补丁 + 不传 profiles + 掩码契约是主要防线；契约测试与手工验收兜底。
- **账号保存触发通道重启**：与控制台行为一致；UI 明确提示，不自动重试。
- **回滚**：新增路由与标签均为增量；删除对应端点/标签即可回到现状，不影响既有依赖/融合区块。
- **不新增数据迁移**：所有状态仍在控制台既有 `data/config.json` / SQLite 中。
