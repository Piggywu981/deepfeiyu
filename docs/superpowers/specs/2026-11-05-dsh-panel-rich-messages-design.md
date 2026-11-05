# DSH 面板消息富展示（期3 · 完全对齐控制台）设计规格

- 状态：已获用户批准（设计七节一次通过）
- 日期：2026-11-05
- 基线：`e35173a`（分支 `feat/dsh-panel-chat-control`，与期1 同分支续作；期1 尚未收到实机验收回执，两期最终如何拆 PR 由仓库所有者决定）
- 需求来源：用户报告「表情、图片、文件、引用、聊天记录、特殊消息（如入群或被踢出等）仅显示占位符」+ 一轮范围澄清（选项：完全对齐控制台）
- 预期文档位置与本文件一致：`docs/superpowers/specs/2026-11-05-dsh-panel-rich-messages-design.md`
- 前置规格：`2026-11-05-dsh-panel-chat-control-design.md`（期1，已实现并提交 `923da8a`…`e35173a`）

---

## 1. 背景与差距（只读核查结论）

面板 `renderMessage`（`dsh-plugin/client.js:1032-1037`）有一条去重逻辑：**内容恰好等于 `[类型]` 时把内容置空、只显示类型标签**。叠加「非文本消息在库里存的就是摘要占位」，于是图片/表情/语音/位置/系统全变成一个标签，引用/文件/聊天记录变成一坨纯文本。控制台（`app/static/app.js:828-908`）则按类型分支渲染：图片内联、表情 gif、引用块，其余显示真实文本。

| 类型 | 库里存什么 | 控制台显示 | 面板现状 |
|---|---|---|---|
| 图片 | `[图片]`；真图在 `GET /api/chats/{uid}/messages/{msg_id}/image` | 内联缩略图 + 点开原图 | 仅「图片」标签 |
| 表情 | `[表情]` + `meta.emoji{url,md5,width,height}`；取图走 `GET /api/emoji/proxy` | gif + ★收藏/⬇下载 | 仅「表情」标签 |
| 引用 | 正文 `回复文本\n[引用 名：摘要]` + `meta.refer{type,displayname,svrid,reply,summary,emoji}` | 引用块，被引图片/表情显示原图 | 纯文本一坨 |
| 文件 | `[文件] <文件名>` | 纯文本（同左） | 有文件名时显示文本，恰为 `[文件]` 时被吞成标签 |
| 聊天记录 | `[聊天记录]\n<完整转录>`（多行） | 纯文本 | 同上，且多行未还原 |
| 特殊消息 | 模板文本（「xxx加入了群聊」）或 `[系统消息]` | 文本 | 模板文本可见；`[系统消息]` 被吞成标签 |

**结论：数据全在库里与现成 API 里，缺的是面板渲染 + 两个字节流代理。**

## 2. 已确认决策

| 项 | 结论 |
|---|---|
| 范围 | **完全对齐控制台**：图片缩略图+点开原图、表情 gif+★收藏/⬇下载（含已收藏状态）、引用块（含被引图片/表情）、文件/聊天记录/系统消息按控制台同款显示真实文本 |
| 明确否决 | 卡片美化（选项2）、完整期3（选项3，含 AI 标/成本/思考折叠/头像/账单行）、只做前三类（选项4） |
| 字节传输 | **方案 A：Host 注册同源 GET 路由**（否决 base64 POST 与直连控制台带 token，见第 4 节） |
| 顺序 | 期2（实时）仍排在后续；期4（表情发送）仍在期3 之后 |
| Python 侧 | 零改动：只用控制台现成 API |
| 依赖 | 零新增（Node 内建 `fetch` 取字节） |

## 3. 技术事实（已核实）

1. **面板去重逻辑**：`dsh-plugin/client.js:1037` `rawContent === '[' + typeText + ']' ? '' : rawContent`——对齐控制台后该逻辑应删除（图片/表情/引用走专门分支，其余类型控制台显示 `content || '[类型]'`）。
2. **摘要形态唯一出处**：`app/wechat/base.py:342 summarize_message_content`——图片 `[图片]`、表情 `[表情]`、文件 `[文件] {title}`、链接 `[链接] {title}｜{url}`、卡片 `[卡片] {title}`、系统 `parse_sysmsg_template` 或 `[系统消息]`、引用 `回复\n[引用 名：摘要]`。
3. **表情元数据**：`app/wechat/base.py:638 meta["emoji"] = info`（直发动画表情）；`app/wechat/manager.py:2565 "meta": {"emoji": info}`；`meta.refer.emoji` 为被引表情（`base.py:447`）。
4. **聊天记录转录**：`app/wechat/base.py:648 content = f"[聊天记录]\n{record}"`，`parse_record`（`base.py:456`）产出「发送者：内容」多行文本——前端必须 `pre-wrap`。
5. **引用元数据**：`parse_refermsg`（`base.py:403-416`）返回 `{type, displayname, svrid, reply, summary, emoji}`；被引图片靠 `svrid` 反查（`main.py:491-519` 的 `is_refer_image` 分支）。
6. **图片接口**：`GET /api/chats/{uid}/messages/{msg_id}/image`（`app/main.py:491`），支持引用图 svrid 反查；失败 404 JSON；成功 `FileResponse` + `Cache-Control: private, max-age=86400`。
7. **表情代理**：`GET /api/emoji/proxy?url=&md5=&download=`（`app/main.py:777-793`）；400（URL 非法）/502（下载失败）/200（`FileResponse`，`download=1` 时带 `Content-Disposition`）。URL 白名单在控制台 `app/emoji.py`。
8. **令牌规则**：`app/main.py:204-215`——`/api/*` GET 允许 `?token=` 查询参数（专为图片/下载链接设计）；Host 走 `x-access-token` 头即可。
9. **收藏接口**：`GET /api/emojis?limit&offset`（`main.py:553`，limit 上限 5000）与 `POST /api/emojis` body `EmojiBody{url,md5,width,height}`（`main.py:559`，按 md5 去重，`main.py:578` 返回 `{ok,emoji}`）。
10. **控制台渲染分支顺序**（照抄基准，`app/static/app.js:828-908`）：拍一拍居中 → 图片 → 表情 → 引用（text/link 且 `meta.refer`）→ 文本（`renderTextEmojis`）→ 其余 `esc(content || '[类型]')`。
11. **面板路由现状**：`dsh-plugin/lib/route.js` handler 是 POST-only（非 POST 405，`:164-167`）；`requestRejection` 在 method 检查**之前**（`:156-163`），天然保证 GET 也过同源鉴权。
12. **`call()` 不适合字节**：`lib/ops.js` 的 `call()` 按 JSON 解析响应，取字节必须新增独立 `fetchBytes`。

## 4. 方案取舍

| 方案 | 结论 |
|---|---|
| **A：Host 注册同源 GET 路由（`/deepfeiyu/image`、`/deepfeiyu/emoji`），`<img src>` 直引** | **采用**：浏览器原生 `loading=lazy` 与 `onerror` 回退；同源 cookie 过 `requestRejection`；令牌只在 Host 内用；与既有 `/deepfeiyu` 前缀同源 |
| B：POST 路由回 base64，前端转 data URL | 否决：体积 +33%、失去浏览器缓存、逐张闪烁，还逼实现维持 POST-only |
| C：`<img>` 直连 `http://127.0.0.1:8765` 带 `?token=` | 否决：令牌暴露给页面，违反「令牌不回显给 Client」红线（`dsh-plugin/README.md` 安全节） |

## 5. 设计

### 5.1 Host 路由（`dsh-plugin/lib/route.js`）

`handler` 在 `requestRejection` 之后按 method 分流：`GET` 查新表 `getRoutes`，其余走现有 POST 表（405、body 解析、错误分类全部不变）。

| GET 路由 | 参数校验（不通过 400 `INVALID_ARGS`） | 转发 |
|---|---|---|
| `GET /deepfeiyu/image?uid=&msg=` | `uid`、`msg` trim 后非空 | `GET /api/chats/{uid}/messages/{msg_id}/image` 字节 |
| `GET /deepfeiyu/emoji?url=&md5=&download=` | `url` trim 后非空；`download` 仅 `1`/`0` | `GET /api/emoji/proxy?...` 字节，透传 `Content-Disposition` |

| 新增 POST 端点 | 校验 | 转发 |
|---|---|---|
| `emojis.list` | `limit` 可选，夹到 1–500（缺省 500） | `GET /api/emojis?limit=` |
| `emoji.fav` | `url` 必填非空字符串；`md5` 字符串（可空）；`width`/`height` 仅接受有限数值 | `POST /api/emojis`（服务端 md5 去重） |

- GET 响应头：`content-type` 取上游（缺省 `application/octet-stream`）、`cache-control: private, max-age=86400`、上游有 `content-disposition` 则透传。
- 上游非 2xx → 原样回该状态（让 `<img onerror>` 接住），不写 JSON 包装。
- GET 不读 body；`new URL(req.url)` 解析 query；未知 GET 路径 404 `UNKNOWN_ENDPOINT`。

### 5.2 Host 传输（`dsh-plugin/lib/ops.js`）

新增 `fetchBytes(path, { timeoutMs = 10000 })`：`fetch(baseUrl() + path)` 带 `x-access-token`（复用 `getToken()`）→ 返回 `{ status, contentType, body: Buffer, disposition }`；连接失败抛既有 `ConsoleDownError`，非 2xx 不抛（由路由原样透传状态）；**单次上限 25MB**（超限回 502 文案），令牌不进日志不回显。

新增四个薄封装：`messageImage(uid,msgId)`、`emojiProxy(url,md5,download)`、`emojisList(limit)`、`emojiFav(payload)`（前两个走 `fetchBytes`，后两个走既有 `call()`）。

### 5.3 Client 渲染（`dsh-plugin/client.js` 的 `renderMessage` 重构）

分支顺序照控制台（事实 10），去掉现有去重逻辑：

1. **图片**：`msg_type === 'image' && id` → `h('a', { href: '/deepfeiyu/image?uid=' + encodeURIComponent(selectedUid) + '&msg=' + encodeURIComponent(id), target: '_blank', rel: 'noopener' }, h('img', { className: 'dsh-deepfeiyu-msg-image', src, alt: '图片', loading: 'lazy', onError: 回退 }))`；`onerror` 把气泡内容替换为 `[图片]`（对齐控制台 `app.js:867`）。
2. **表情**：`meta.emoji && meta.emoji.url` → `img` 源 `/deepfeiyu/emoji?url=&md5=`；悬停操作 ★收藏（`call('emoji.fav', …)` 成功后把 url 加进 `emojiFavs`）、⬇下载（`href` 追加 `&download=1`）；已收藏态 = `emojiFavs.has(url)`，初值由挂载时一次 `emojis.list` 比对 `url` 得出。
3. **引用**：`(msg_type === 'text' || 'link') && meta.refer` → 正文去掉尾部 `[引用…]`（正则同控制台 `app.js:884`）+ 引用块（`displayname` + `summary`；`refer.type == 3` 用 `refer.msg_id || msg.id` 拼 image 路由显示原图；`refer.emoji.url` 拼 emoji 路由）。
4. **其余类型**（文件/聊天记录/系统/语音/视频/位置/链接/卡片）：`content || '[' + typeLabel + ']'` 原样进气泡，正文 `white-space: pre-wrap` 还原聊天记录多行。
5. 保留现状：类型标签、左右气泡（`is_self`）、`sender_name`、撤回半透明、时间戳、空消息兜底。

新增状态：`const [emojiFavs, setEmojiFavs] = React.useState(() => new Set())`；挂载且控制台运行时 `call('emojis.list', {limit:500})` 一次（失败静默降级为「未收藏」显示，不弹错误、不阻塞渲染）；★收藏成功把 url 加进 `emojiFavs`，**失败时在按钮旁行内显示小字错误**（`.dsh-deepfeiyu-emoji-error`，样式同 `sendError`），不写入会话控制的 `chatFeedback`——那是线程头/列表操作的反馈通道，混用会让反馈跑到错误的位置。

### 5.4 CSS（`STYLES` 新增）

`.dsh-deepfeiyu-msg-image`（max-width 260px、圆角、`object-fit: cover` 懒加载占位）、`.dsh-deepfeiyu-msg-emoji`（max-height 120px）、`.dsh-deepfeiyu-msg-quote`（左边框缩进、次要色）、`.dsh-deepfeiyu-msg-quote-img`（限宽）、`.dsh-deepfeiyu-emoji-actions`（悬停显示，两个小按钮）、`.dsh-deepfeiyu-emoji-error`（12px 错误色，收藏失败行内提示）、`.dsh-deepfeiyu-bubble` 追加 `white-space: pre-wrap`。全部用 `--dsw-alias-*` token，浅色回落值随现有约定。

### 5.5 数据流 / 错误 / 安全

- 图片与表情是**无状态 GET**，不进 4s 轮询；消息轮询照旧，新消息带 `id`/`meta` 自然渲染出富内容。
- `<img onerror>` 本地回退占位；Host 上游 400/404/502 原样透传；`fetchBytes` 10s 超时抛 `ConsoleDownError` → 图片回退占位（不打断消息流）。
- **安全**：GET 全过 `connection.requestRejection`（`route.js` 现有顺序保证）；表情 URL 白名单仍在控制台侧；令牌不出 Host；25MB 上限；不新增遥测与外联；`download=1` 只是透传控制台既有头。

### 5.6 与期1 的边界

不动期1 的线程头、多选、接管徽标、`chat.*` 端点与 `chatFeedback` 语义；`renderMessage` 的重构不触碰 `renderThread` 结构。

## 6. 改动清单（文件级）

| 文件 | 改动 |
|---|---|
| `dsh-plugin/lib/route.js` | handler GET 分流 + `getRoutes` 两条 + POST 表两条（`emojis.list`、`emoji.fav`） |
| `dsh-plugin/lib/ops.js` | `fetchBytes` + 4 个薄封装 |
| `dsh-plugin/client.js` | `renderMessage` 分支重构、`emojiFavs` 状态、CSS |
| `tests/test_dsh_plugin.py` | 静态断言 + 扩展 `ROUTE_HARNESS`（GET 行为） |
| `dsh-plugin/README.md` | 面板组成、端点表、安全节一句 GET 代理说明 |
| `docs/使用说明.md` | §16.1 消息显示描述 |
| `CHANGELOG.md` | `[未发布]` 新条目（真实验证状态） |
| **不改** | `app/**/*.py`、`index.js`、`lib/tools.js`、`lib/approval.js`、`lib/fusion.js`、许可与法律文档 |

## 7. 测试计划

1. 静态断言：route 含 `getRoutes`、`'image'`、`'emoji'`、`'emojis.list'`、`'emoji.fav'` 与 GET 分流；ops 含 `fetchBytes`、`messageImage`、`emojiProxy`、`emojisList`、`emojiFav`；client 含三个渲染分支标记（`dsh-deepfeiyu-msg-image`、`meta.emoji`、`meta.refer`）与去重逻辑删除的反向断言（`rawContent === '[' + typeText + ']'` 必须**不**再出现）。
2. 行为测试（扩展 `ROUTE_HARNESS`，假 `fetchBytes`/`call` 层）：GET image 成功透传 content-type/cache-control；`uid` 缺失 400；`requestRejection` 非 0 时 GET 也 401；GET 打到 POST 端点仍 405；未知 GET 路径 404；`emoji.fav` 非法 body 400、合法 body 白名单转发；上游 404 原样透传。
3. `node --check`（经 `test_js_syntax`）+ 全量 `pytest -q` 如实报告。
4. 实机：重装判定（junction 已指向工作区，只需插件重载）→ 刷新页面 → 验收清单（第 9 节）。

## 8. 风险、回滚与合规

| 风险 | 对策 |
|---|---|
| 大图/多图同时加载占带宽 | `loading=lazy` + 浏览器同源缓存（`max-age=86400`）+ 25MB 单张上限 |
| 表情 CDN 拉取慢/失败 | 控制台侧 502 → 前端 `onerror` 回退 `[表情]`；10s 超时 |
| `emojis.list` 拉取失败影响面板 | 静默降级为未收藏态，不弹错误、不阻塞渲染 |
| 收藏写操作绕过审批语义 | 与期1 同定位：等价控制台人工点收藏，README 安全节已覆盖「面板操作不走审批」，本条属其延伸，追加一句说明 |
| `renderMessage` 重构引入回归 | 分支顺序照抄控制台；`test_js_syntax` + 静态断言 + 实机对照控制台逐类核对 |

- **回滚**：分支上单期提交序列，`git revert` 对应提交即可；无数据、无配置、无迁移。
- **合规**：不改许可与法律文档、不加依赖、不新增外联（所有请求仍只到 `127.0.0.1:8765`）；提交前跑 `tools/release_snapshot.py --scan-tracked .` 与 `git diff --check`。

## 9. 验收标准（可观察）

1. 图片消息显示缩略图，点击在新标签打开原图；图片不存在时气泡回退 `[图片]` 不报错。
2. 表情消息显示动图；★收藏后**控制台表情墙**出现该表情（同一数据源），面板按钮变已收藏；⬇下载触发浏览器下载。
3. 引用消息显示引用块（昵称 + 摘要），被引图片/表情直接显示原图；正文尾部不再出现 `[引用…]` 文本。
4. 文件、聊天记录（多行转录完整还原）、入群/被踢等系统消息显示真实文本，不再被吞成标签。
5. 控制台未运行时：消息区照常显示文本内容，图片/表情回退占位，不弹全局错误。
6. `pytest tests/test_dsh_plugin.py -q`、全量 `pytest -q`、`node --check` 全过并如实记录；脱敏扫描与 `git diff --check` 干净。
7. 插件重载 + 页面刷新后实机逐类对照控制台核对（视觉细节用户终验）。

## 10. 非目标与后续

- **期2（实时桥）**：Host WS → 同源 SSE，补拦截原因、消息即时上屏、AI 思考/工具进度——仍在队列。
- **期4（表情发送）**：表情面板三 tab → `send-emoji`——仍在队列（期3 的 `emojis.list` 转发可被期4 复用）。
- **不做**：`[微笑]`→Unicode 词表、卡片美化、拍一拍居中、AI 标与成本、头像、账单行、消息分页（控制台也没有）。
- **期1 遗留**：实机验收回执与 CHANGELOG「实机验证」行仍欠着，与期3 验收一并收口。

## 附录 A：Issue 草稿（供人类提交，AI 不代开）

> **标题**：DSH 面板消息显示占位符——图片/表情/引用富渲染与文件/聊天记录/系统消息对齐控制台
>
> **问题**：面板里图片、表情只剩类型标签，引用是一坨文本，文件/聊天记录/系统消息在内容恰好等于 `[类型]` 时被去重逻辑吞掉；控制台同一批消息能显示缩略图、动图、引用块与真实文本。用户必须切到完整控制台才能看。
>
> **范围**：只改 `dsh-plugin/`（Host 两个 GET 字节代理 + 两个收藏端点 + 前端渲染）；不改 Python、不加依赖。
>
> **验收标准**：见设计规格第 9 节七条。
>
> **风险**：GET 复用同源鉴权，令牌不出 Host；单图 25MB 上限；收藏是写操作但等价控制台人工点击（与既有定位一致）。
>
> **被否决的替代方案**：base64 POST（体积与缓存差）；`<img>` 直连控制台带 token（令牌暴露给页面，违反安全红线）。
