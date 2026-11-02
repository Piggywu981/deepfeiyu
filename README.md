# Deep肥鱼 · 微信智能体（DeepFeiyu WeChat Agent）

**Deep肥鱼**是一个有独立人格的 AI 个体，掌管着自己的多个微信账号：消息实时接入 → 自己判断该不该回 → 调工具发消息。
带一个可操作的 Web 控制台，账号之间数据隔离，每个账号可以绑定不同的微信通道。

这一版把「记忆」重做成三层长期记忆 + 人物档案 + 可选语义检索，并加入能力系统：本机 `SKILL.md` 技能、`plugin.json` 插件、通用 MCP 服务器都可以挂到智能体上；控制台新增「记忆」与「能力」页。

日常怎么用、每个页面干什么、心情怎么调、出问题先看哪里，见 [docs/使用说明.md](docs/使用说明.md)。

> 许可证：**PolyForm Noncommercial License 1.0.0** —— 源码开放，仅限非商业的学习、研究与非商业交流；**不是** OSI 开源许可，禁止商业使用、销售、托管代运营。详见 [LICENSE](LICENSE) 与 [EULA.md](EULA.md)。

```
┌──────────────┐  消息   ┌─────────────────────┐   Function Calling   ┌────────────┐
│ 账号 A 通道   │ ──────▶ │  账号 A 会话/策略    │ ───────────────────▶ │ DeepSeek   │
│ wcferry:10086│ ◀────── │  独立 SQLite 命名空间 │ ◀─────────────────── │ 兼容模型   │
├──────────────┤         ├─────────────────────┤   流式输出 + 工具调用 │            │
│ 账号 B 通道   │ ──────▶ │  账号 B 会话/策略    │                      └────────────┘
│ webhook/Token│ ◀────── │                     │
└──────────────┘         └──────────┬──────────┘
                                    │ WebSocket / SSE
                          ┌─────────▼──────────┐
                          │ Web 控制台 :8765    │
                          │ 左上角切换账号       │
                          └────────────────────┘
```

---

## 1. 快速开始

```bat
run.bat
```

首次运行会做四件事，然后打开 <http://127.0.0.1:8765>：

1. 从 Azure 中国区 NuGet 镜像下载便携 Python 3.12.8（约 14MB，带 SHA256 校验）到项目内 `.python/`——不依赖也不写入系统 Python。
2. 建 `.venv` 并装**核心依赖**（`requirements.txt`）。微信 4.x、朋友圈、桌面宠物各自是独立一组（`requirements-weixin4.txt` 等），某一组装不上只会关掉那个功能并打印一行说明，**不会挡住启动**。
3. `git submodule update --init --recursive` 拉上游素材（桌宠 Helper、余额挂件）。没有装 Git 或网络不通会跳过，相关功能在控制台里给出可读报错。
4. 启动控制台。

已有 `.venv` 但不是 Python 3.12 时，脚本会停下来提示你删掉该目录重跑（`winsdk` 只有 3.12 及以下的预编译包）。

1. 「设置 → 模型」点选一档模型配置（默认 `DeepSeek Flash`、`MiMo v2.6 Flash`、`自定义`），填这一档的 API Key，测试连接。
2. 在「设置 → 账号与通道」选择真实通道（`wcferry`、`weixin4` 或 `webhook`），连接成功后即可收发消息；没有可用通道时不会启动驱动。
3. 「设置 → 账号与通道」新增账号，每个账号单独选驱动、填凭据。
4. 可选：在「设置 → 模型 → 长期记忆」配置 embedding 接口，开启语义召回；不配置也能用关键词检索。
5. 可选：打开「能力」页，放 `skills/`、`plugins/` 或在 `data/mcp.json` 里配 MCP 服务器；技能和外部工具默认只在控制台可用。

> Key 只写入项目根 `.env`（`DEEPSEEK_API_KEY` / `MIMO_API_KEY` / `CUSTOM_API_KEY`，按配置各自独立，已 gitignore），`data/config.json` 中不含密钥；也可直接用环境变量。没填 Key 的档在界面上标「未填 Key」，切过去会提示。记忆 embedding 的密钥同样只读环境变量（默认 `EMBEDDING_API_KEY`）。

---

## 2. 多账号

### 怎么用

- 左侧顶部是**账号切换器**，点一下切换；当前账号决定会话、统计、智能体操作对象。
- 「设置 → 账号与通道」可以新增 / 编辑 / 删除账号，每个账号独立配置：
  - **账号名称**：控制台里的显示名（不会同步到微信）
  - **微信昵称**：群里 `@这个昵称` 才触发自动回复
  - **驱动**：`wcferry` / `weixin4` / `webhook` / `auto`
  - **Hook 主机 + 端口**：多开时一个实例一个端口
  - **Webhook 回调 + Token**：多账号接入时用来区分来源
- 数据隔离：会话、消息、事件、token 用量都按 `account_id` 存储，互不串号。

### 多账号 / 多实例（可能违反平台规则）

> 微信官方不支持多开；多开、进程 Hook、本地数据库解密与界面自动化都可能违反《微信个人账号使用规范》。以下仅说明技术接入方式，是否使用由你自行评估。

程序会枚举**所有可见的微信窗口**（PID、版本、窗口位置），显示在「设置 → 账号与通道 → 检测到的微信客户端」。

- **微信 3.9.x 双开**：每个实例注入不同端口，在账号里分别填 `10086` / `10087`。
- **微信 4.x**：由内置的 `weixin4` 驱动支持——收消息走后台解密本地库，发消息走 UIA（左上角搜索栏搜昵称/备注 → 校验目标会话 → 粘贴 → 回车）。4.x 是 Qt 自绘界面，聊天主窗口没有可操作的 UIA 树，所以发送不靠操作聊天窗口，也不依赖通用 Hook；发送会短暂把微信顶到前台，结束后把原来的前台窗口还回去。
  4.x 双开的可行路径：
  1. 每个账号在 `weixin4.account_dir` 里钉死自己的数据目录，避免绑到同一个号；
  2. 每个账号接一个 **Webhook 网关**（协议服务 / 自建 Hook 服务），各自配回调地址与 Token；
  3. 并存安装 **3.9.10.27**，用 wcferry 分别绑定端口。

---

## 3. 微信通道

| 通道 | 能力 | 前置条件 | 适用 |
|---|---|---|---|
| `wcferry` | 收发文本/图片/文件、通讯录、群聊、本地库检索（具体能力取决于 wcferry） | 微信 PC **3.9.x** + `pip install wcferry` | 真机自动化 |
| `weixin4` | **收**：后台解密数据库；**发**：UIA 在左上角搜索栏搜昵称/备注 → 打开会话并校验目标 → 粘贴 → 回车；发送会短暂把微信顶到前台，结束（成功或失败）后自动把原来的前台窗口还回去，号主本来就在微信里则不抢 | 微信 PC **4.x** + `pip install --no-deps wechatauto-replica`、`pip install cryptography colorama uiautomation zstandard` | 4.x 真机 |
| `webhook` | 取决于你的网关 | 任意能收发的 HTTP 网关 | 协议服务 / 自建 Hook / 官方号 |

`driver: auto`：优先 `wcferry`（微信 3.9.x 且安装了依赖），其次 `weixin4`（微信 4.x 且读取依赖可用）；两者都不可用时不会启动通道，并在控制台提示缺少依赖；`webhook` 需要在账号里显式选择。

依赖装不上时不必手敲命令：控制台「设置 → 可选功能依赖」为微信 4.x、WCFerry、朋友圈、桌宠、B 站与核心依赖各留了一个「一键安装」按钮（固定白名单命令，不接受任意包名）；装完按提示重启控制台。

**weixin4 历史回填**：通道启动后会自动从解密库回填各会话最近的历史消息（默认 100 个会话 × 每个会话 50 条），应用离线期间聊的内容 AI 也能读到；重复回填自动去重。可在账号的 `weixin4` 配置里调 `history_chats` / `history_limit`（设 0 关闭）。回填只补「对方发的消息」，自己发的（含手机上回的）不导入。

### wcferry 多开

```json
{
  "id": "acc_work",
  "name": "工作号",
  "driver": "wcferry",
  "wcferry": { "host": "127.0.0.1", "port": 10086 }
}
```

每个账号的 `port` 必须对应各自客户端实例的注入端口，否则会连到同一个号上。

---

## 4. 能做什么 / 不能做什么

**能做**

- 多账号同时在线，消息按账号隔离入库
- weixin4 通道启动时自动回填离线期间的聊天记录，AI 重启后也能读到没在线时的消息
- 收到合并转发的聊天记录卡片时自动解析出完整对话内容（本地解密库自带，无需联网），AI 能直接读到并回应；解析不了的文件/链接会明确说明，而不是无响应
- 实时收发私聊、群聊消息，图片/文件/语音等类型会入库并标注
- 动画表情直接显示为图片，可收藏到表情面板、下载，或从面板发送（wcferry 走 hook 接口；weixin4 走剪贴板粘贴，GIF 自动按动画表情发出）
- 表情包自动打标：视觉模型看图 + 聊天上下文 + 梗百科（内置速查 + meme_kb.json 知识库 + `data/meme_dict.md` 用户私梗）产出标签，可按场景检索
- 表情墙批量导入导出：整库或选中导出为 zip（图片 + `manifest.json` 标签清单），可导入 zip 或一批散图还原/统一标签；按内容 md5 去重，同一表情的标签取并集
- AI 联网找表情：库里没有合适的表情时，AI 会自己联网搜图（Bing 图片搜索优先，斗图啦/发表情兜底），下载入库（内容 md5 去重）并自动打标，之后即可检索发送
- 串子模式：开启后 AI 回复按「串子」方式接话，默认每轮找机会串，会高级黑（捧杀/假中立/拉踩/阴阳怪气，只对准观点/方案/梗）。联网台词库由独立的「串子站表情包」开关控制（默认关闭）：开启后优先引用 searchanythingfun.xyz 台词库的台词和截图，每轮最多查站 1 次、只入库 1 张，配额不足自动回落本地；关闭只停查站和新入库，已入库的「串子站」截图照常检索、发送。正经/情绪/隐私/金钱场景收梗，只配安慰/倾听/正经向的图，不发串子图。站内截图自动入库并标记「串子站」，默认锁定。秒回时正文先发、站点台词不参与正文：开着「回复必带表情包」就由系统在文字后补一张图，关着则不强制配图，AI 想发/对方点名要时会整条输出 `[表情]` 向系统要一张
- 表情墙锁定：标签锁保护标签名不被「整理标签」改名/合并；表情锁冻结单张表情的标签、描述、AI 重打标和删除，发送/导出不受影响
- 串子模式的站点请求只发生在自动回复链路，且受「串子站表情包」独立开关控制（默认关闭）：控制台智能体不会自己查站（在控制台里 `find_emoji_online` 仍是原来的联网找表情来源），但会收到「串子模式已开启」的状态，可直接检索并发送库里已入库的「串子站」图；关掉串子模式即恢复改动前的表现，包括「仅 AI 娘表情包」过滤对每一条本地表情照常生效
- 形象图鉴（ai-moe-wiki）：接入本地的 AI 娘化图集仓库（默认 `../ai-moe-wiki`，`config.json` 的 `ai.moe_wiki_path` 配置，留空停用）——AI 认识图集里的角色（收到相关图片或提到角色名时自动带图鉴），立绘注册为自己的形象（`data/self/avatar.jpg`，识图时看得见自己），被问长相/想看立绘时可用 `send_moe_wiki_image` 发图
- AI 自动回复：私聊直回，群聊默认只回 `@昵称`
- 拍一拍消息：wcferry / weixin4 / webhook 都能识别「谁拍了拍谁」（含自定义后缀），控制台居中提示；被拍时 AI 可按策略回一句，群里只接拍自己的；可在「设置 → 自动回复策略」关闭
- 跨对话沟通（默认开、可关）：好友在聊天里明确要求「帮我给某某发条消息 / 转告某某 / 跟某某说一声」时，AI 会真的把话发到指定的人或群，再在当前会话回一句确认；号主本人在其他会话里聊天时，AI 也能自主决定去那个会话补一句话（同一会话有冷却、每日与每小时都有上限）。收件人按昵称/备注/群名解析，找不到或重名会让 AI 反问，不猜人；合并转发聊天记录里出现的发言人也可以按名字转告。每次跨对话发送都写事件留痕，消息标 `meta.cross`
- 自动回复拟人化：先等 2-4 秒聚拢对方的连发消息（文字+跟发的表情包）再一并回复；按每个联系人的表情包习惯决定是否在文字后跟一张（文字先发、表情 1-3 秒后垫后）；设置页可开「秒回」跳过聚拢窗口，收到即回
- 秒回提速：开「秒回」后所有消息都一轮出正文（含对方只发表情包的场景），表情在正文流式生成时并行挑选、发出后立刻垫上；形象图自动缩到 512px 随请求附带；正文生成期间新来的消息先攒着，本轮发完补一轮（补跑前重过规则，过期丢）
- 心情（默认关）：自动回复与控制台回合结束后各判断一次七项情绪，按可信度、显著性、日上限注入提示词；控制台「心情」页四页签（心情 / 会话绑定 / 判断与注入 / 挂饰），右下角挂饰可拖动、记忆位置、收起成小圆，多账号隔离
- 主动搭话（Phase 3，默认关）：按账号在白名单会话上做概率掷骰，掷中才让 AI 写一句开场白并真的发到微信；峰/谷命中线、没掷中加成、安静时段、冷却、间隔与账号/单会话日上限全部可调，控制台「搭话」页带试运行（只生成不发）、真发一条（不占自动额度）、手动补一条（占额度）与最近记录
- 桌面宠物（默认关）：桌角常驻一只透明置顶的小鲸鱼，动作只由运行状态决定（在想 / 在忙 / 这条回出去了 / 发送失败 / 微信没连上），语气跟着心情走（气泡措辞、待机节奏、瞬时脉冲）。素材需一次本地装配并校验官方包 sha1，缺 PySide6 或缺素材都不启动桌宠、也不影响微信；控制台「桌宠」页带预览与不可用原因
- 记忆 v2：分 `core`（长期事实/关系/偏好）、`episodic`（某次发生的事/约定）、`session`（对话压缩摘要，默认 7 天过期）三层；每次回复按「人物档案 + 核心事实 + 相关往事 + 最近会话」的字符预算注入，不再固定 12 条。支持置顶、重要度、置信度、作用域（全局/仅该会话）、手动增改删、JSON 导入导出、合并重复、衰减长期未用记忆
- 人物档案：从长期事实和往事里自动汇总关系、偏好、雷区、最近话题；记忆页可查看和重算。没有档案时自动退回原始记忆注入
- 记忆压缩：会话超过阈值时异步把较早的对话压成一条 `session` 摘要，避免上下文无限膨胀；压缩失败不影响回复
- 可选语义检索：配置任意 OpenAI 兼容 `/embeddings` 接口后，记忆按「关键词 + 余弦相似度 + 重要度 + 新近度」混合召回；不配置 embedding 也能用关键词检索。密钥只读环境变量（默认 `EMBEDDING_API_KEY`）
- 能力系统：扫描 `skills/`、`data/skills/` 下的 `SKILL.md`（系统提示只列名字+描述，模型需要时用 `load_skill` 读正文）；扫描 `plugins/`、`data/plugins/` 下的 `plugin.json`（声明式 HTTP 工具 + 显式 `trusted: true` 的 Python 插件）；连接 `data/mcp.json` 里的 MCP 服务器（stdio / Streamable HTTP），工具按 `mcp__服务器__工具` 暴露
- 能力安全默认：技能只读；插件和 MCP 默认只在控制台可用，自动回复要同时打开总开关并在条目里显式写 `auto_reply: true`。控制台「能力」页可查看连接状态、工具列表、技能正文、插件错误，编辑 `mcp.json` 并重载
- 智能体控制台：自然语言下指令 → 查会话、读上下文、发消息、发文件、全文检索、查 DeepSeek 余额与上一轮话费（作用于当前账号）
- 朋友圈配图读取（控制台）：按 `browse_wechat_moments` 返回的 `feed_id` 读取一条动态的配图，交给识图模型描述画面和图中文字（需在「设置 → 模型」开启识图）；可指定看第几张，原图存本地，可直接让智能体转发。自动回复只读朋友圈文字，不读配图
- 控制台对话支持历史会话、新建对话、模型与思考强度切换、思考过程展示，以及生成中停止回答
- 会话级开关、人工接管暂停、黑白名单、工作时段、限流、每日上限
- 微信窗口 / 版本 / Hook 可行性自检
- 更新检查：启动时自动对比 Gitee 远端版本（不阻塞启动），控制台侧边栏与「设置 → 软件更新」可手动检查

**不能做**

- 发朋友圈（微信 4.x 自绘页面，底层库不支持）、微信支付、小程序、语音/视频通话
- 微信 4.x 客户端的自动化（无可用 UIA/Hook，需走 Webhook 网关）
- 从窗口反查账号身份（需要你自己对应）

---

## 5. 自动回复策略

| 配置 | 说明 |
|---|---|
| `auto.enabled` | 总开关 |
| `auto.group_need_at` | 群里必须 `@昵称` 才回（昵称按账号取） |
| `auto.pat_reply` | 被拍一拍时是否回复（群里只接拍自己的，默认开） |
| `auto.only_whitelist` | 只回白名单会话 |
| `auto.blacklist` | 永不回复的会话 |
| `auto.min_interval_sec` | 同一会话最短回复间隔 |
| `auto.daily_limit` | 每日 AI 回复上限（按账号统计） |
| `auto.takeover_pause_min` | 你亲自回一句后 AI 退让 N 分钟 |
| `auto.working_hours` | 工作时段外不自动回复 |
| `auto.prefix` | 回复前缀（如 `[AI]`） |
| `auto.max_reply_chars` | 单条回复字数上限 |

### 跨对话沟通（转告代发 / 主动串门）

| 配置 | 说明 |
|---|---|
| `cross.enabled` | 跨对话沟通总开关，默认开；关掉后 AI 只能回当前会话 |
| `cross.relay` | 允许转告/代发：聊天里被点名收件人时把消息发到指定的人或群 |
| `cross.visit` | 允许主动串门：号主本人在别的私聊发消息时，AI 可自主插一句 |
| `cross.daily_limit` | 每账号每天跨对话发送总上限（默认 30，0 不限制） |
| `cross.visit_gap_sec` | 同一会话两次串门尝试的最小间隔（秒，默认 600） |
| `cross.visit_max_per_hour` | 每小时串门尝试上限（默认 6） |
| `cross.relay_max_chars` | 单条转告正文上限（字，默认 500） |

串门不会进入已关闭 AI 的会话、群聊、文件传输助手、黑名单/白名单外会话或非工作时段；
会话处于「人工接管暂停」时也不串门（把 `auto.takeover_pause_min` 设为 0 才能随聊随串）。

拦截原因会通过 WebSocket 推到界面（会话页副标题显示「AI 判断：群聊未 @ 我」）。

### 发送节流（降低打扰与频率）

| 配置 | 说明 |
|---|---|
| `send.enabled` | 发送层节流总开关（默认开） |
| `send.min_gap_sec` | 任意两次发送的全局最小间隔（秒，区间内随机取值，默认 `[6, 14]`） |
| `send.chat_gap_sec` | 同一会话两次发送的冷却（秒，默认 `[12, 25]`） |
| `send.direct_gap_sec` | 「对方冲我来」的回复改走这段短冷却（秒，默认 `[8, 12]`）：私聊、群里 `@昵称`、被拍一拍都算，全局最小间隔照旧生效 |
| `send.conversation_window_sec` | 连续对话窗口（秒，默认 60）：这个时间内 AI 回过、对方又接了话，群里没 `@` 的续答也算「在等回应」，同样走短冷却；冷场后重新插话仍按 `chat_gap_sec` |

节流只作用于机器发送（AI 自动回复、智能体工具调用），控制台人工发送不排队；
群里的顺嘴闲聊（没 `@` 你、也不在来回接话中）仍按 `chat_gap_sec` 慢慢来；
「文字后跟表情」的组合发送与分条续发走 `burst_gap_sec` 短间隔，不受上面两层冷却排队。
weixin4 通道的发送依赖 wechatauto，它自带一层「拟人节奏」（写与写之间再隔 2.5–6 秒、
120 秒内写满 6 次睡 30–75 秒），驱动启动时统一压到 `fast` 档，只保留鼠标轨迹与键击抖动；
要改回去设环境变量 `WECHATAUTO_RHYTHM=natural|calm|fast|off` 即可，驱动不会覆盖你的设置。
被平台限制后应暂停自动化，先用人工方式恢复；不要靠调参规避平台限制。


### 模型配置（profile）

「设置 → 模型」顶部是一档一档的模型配置（`ai.profiles[]`），每档各自记 Base URL、模型名和用哪把密钥：

| 档 id | 默认 Base URL | 默认模型名 | 密钥环境变量 |
|---|---|---|---|
| `deepseek-flash` | `https://api.deepseek.com` | `deepseek-flash` | `DEEPSEEK_API_KEY` |
| `mimo-v2.6-flash` | `https://api.xiaomimimo.com/v1` | `mimo-v2.6-flash` | `MIMO_API_KEY` |
| `custom` | 自填 | 自填 | `CUSTOM_API_KEY` |

- `ai.active_profile` 是当前生效档；`ai.base_url` / `ai.model` / `ai.api_key` 只是它的运行时镜像，每次加载按生效档重写，改字段永远只写进那一档。
- 换档点一下即保存并生效，微信自动回复与控制台对话一起跟着走；思考强度选项与请求参数按服务商自动切换（MiMo 用 `thinking.type`，DeepSeek 用 `reasoning_effort`）。
- 温度、`max_tokens` 和全局 `ai.price_*` 不随档切换；DeepSeek/未知模型按全局牌价估算，MiMo v2.6 flash/pro/pro-ultraspeed 自动改按官方人民币牌价，要覆盖可在该档补 `price_input_hit` / `price_input_miss` / `price_output`。
- 想加一档：直接在 `data/config.json` 的 `ai.profiles` 追加 `{id, name, base_url, model, key_env}`，重启后界面上就能选。
- 「智能体」页的模型下拉只在**当前档的接口**上换模型名（列表来自该服务商 `/models`，读不到时用内置兜底清单），不换服务商；余额查询仅 DeepSeek 可用，MiMo 没有 API Key 余额接口，每轮和今日仍按 MiMo 牌价本地估算。
- 旧的单模型配置升级时自动归位：原本用的是非 DeepSeek 地址就整包落到 `custom` 档并选中它，`config.json` 里的明文 Key 迁进该档的 `.env` 键。

---

## 6. 记忆 v2

旧版只有一张扁平记忆表和固定 12 条注入；v2 把它拆成三层，并加入人物档案、混合检索和压缩。

| 层 | 用途 | 默认注入 |
|---|---|---|
| `core` | 长期事实、关系、偏好、雷区 | 按重要度/相关度优先 |
| `episodic` | 某次发生的事、约定、近况 | 按相关度 |
| `session` | 一段对话的压缩摘要 | 最近优先，默认 7 天过期 |

**检索与注入**：`关键词 + 可选 embedding 余弦 + 重要度 + 新近度 + 置顶` 加权；先保置顶和长期事实，再按相关度补往事/会话；总字符数受 `memory.context_chars` 限制。没有配置 embedding 时自动只走关键词，不阻塞任何回复。

**写入**：对话结束后仍由 LLM 抽取 ADD/UPDATE/DELETE，但会带 `layer`、`subject`、`confidence`；完全相同的文本自动合并；长对话超过阈值时异步压缩成 `session` 摘要。手动「整理记忆」会清过期、合并重复、衰减 45 天未用的非置顶记忆、重算人物档案。

**写入分级与保留**：稳定信息（称呼、关系、长期偏好、雷区）自动进 `core`；一次性事件进 `episodic`，其中 `importance=1` 的非置顶抽取记忆默认 30 天过期（`memory.episodic_ttl_days`，0 关闭）。同一件事换说法会合并到已有记忆，不再新增。`access_count` 只由 `recall_memory` 真正检索时增加，注入上下文不计入。

**配置**（`data/config.json`）：

```json
"memory": {
  "context_chars": 1200,
  "max_items": 24,
  "profile_enabled": true,
  "compress_after_messages": 24,
  "compress_keep_recent": 12,
  "session_ttl_days": 7,
  "episodic_ttl_days": 30,
  "embedding": {
    "enabled": false,
    "base_url": "https://api.openai.com/v1",
    "model": "text-embedding-3-small",
    "key_env": "EMBEDDING_API_KEY",
    "timeout": 20
  }
}
```

启用语义检索时，在项目根 `.env` 写 `EMBEDDING_API_KEY=...`（或把 `key_env` 改成别的变量名）；密钥不进 `config.json`。本地模型/兼容网关也可以，只要提供 `/embeddings`。

**控制台**：「记忆」页分「记忆流 / 人物档案」两栏。记忆流可按层级、类型、置顶筛选，支持置顶、改层级、编辑、批量删除、JSON 导入导出；人物档案可查看关系和偏好，并按会话重算。工具侧新增 `remember` 的 `layer/subject/confidence` 参数，`recall_memory` 走混合检索。

## 6.5 自我进化（成长）

成长默认关闭，在控制台「成长」页点「启动成长」才对当前账号生效；关掉后提示词回到原样。

- **自我总览 + 特质库**：基础人设「大肥鱼」与安全底线固定，其余（口癖、观点、价值观、偏好、关系、习惯、自我叙事）可持续生长；特质库不设总量上限。
- **动态召回**：每轮按当前聊天对象和话题，只注入相关的一批特质；控制台对话只注入全局特质，关系特质只在对应会话注入。
- **夜间反思**：默认 03:30，每账号每天最多一次；关机则下次启动补跑；也可在成长页点「现在想想」。反思器不带工具，只产出 JSON。
- **风险闸**：低风险自动生效；立场跳变、身份变化、修改号主特质进「待批」；安全越界、假证据、命令句/链接/密钥直接拒绝。
- **审计与回滚**：所有变化写时间线，带 before/after；回滚冲突会提示，可选择强制。
- **配置**：`data/config.json` 的 `growth` 段；默认 `enabled=false`，`accounts` 是显式开启的白名单。可调 `reflection_hour/minute`、`min_new_messages`、`prompt_chars`、`summary_chars`、`manual_max_per_day`、`trait_decay_days`、`journal_keep_days`。

本期只做「心脏」：不写技能/插件/代码，不主动发消息；这些在后续切片接入。

## 7. 能力系统（MCP / 技能 / 插件）

能力系统默认开启，但外部工具默认只在**控制台**可用；自动回复要同时满足全局 `capabilities.auto_reply_enabled=true` 和条目自己的 `auto_reply: true`。这样避免群聊里的 AI 自发调用 shell/文件类工具。

### 7.1 技能：`SKILL.md`

递归扫描 `skills/`、`data/skills/`：

```text
skills/
  web-research/
    SKILL.md
```

```markdown
---
name: web-research
description: 先搜索再读正文，最后给出来源
---
1. 先用 web_search 找候选；
2. 用 fetch_page 读正文；
3. 回答时附上来源链接。
```

系统提示只放名字+描述，模型需要时调用 `load_skill` 读正文。技能只提供指令，不自动执行脚本；`list_skills` 可列出已安装技能。

### 7.2 插件：`plugin.json`

声明式 HTTP 工具（推荐）：

```json
{
  "name": "weather",
  "version": "1.0.0",
  "description": "天气查询",
  "auto_reply": false,
  "tools": [
    {
      "name": "get_weather",
      "description": "查城市天气",
      "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
      },
      "method": "GET",
      "url": "https://api.example.com/weather?city={city}",
      "headers": {"Authorization": "Bearer ..."},
      "timeout": 15
    }
  ]
}
```

工具名会加前缀：`plugin__weather__get_weather`。`url` / `headers` / `body` 支持 `{arg}` 占位；GET 的 URL 占位会自动 URL 编码。

需要本地 Python 能力时，manifest 写 `"trusted": true` 和 `"entry": "plugin.py"`，`plugin.py` 暴露 `register(add_tool)`：

```python
def register(add_tool):
    async def hello(args):
        return {"ok": True, "text": f"你好，{args.get('name', '世界')}"}
    add_tool("hello", "打个招呼", {"type": "object", "properties": {"name": {"type": "string"}}}, hello)
```

Python 插件与应用同权限运行，只装你信任的代码。

### 7.3 MCP：`data/mcp.json`

兼容 Claude/Cursor 的 `mcpServers` 结构；支持 stdio 和 Streamable HTTP：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/data"],
      "auto_reply": false
    },
    "remote": {
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ..."},
      "auto_reply": false
    }
  }
}
```

工具名格式：`mcp__filesystem__read_file`。MCP 服务器是独立进程/远程服务，拥有你给它的权限；只接可信来源。启动失败、超时、进程退出都会在「能力」页显示，不会拖垮主应用。

### 7.4 能力页与配置

「能力」页可以：查看 MCP 连接状态/工具列表、测试单个服务器、编辑 `data/mcp.json` 并立即重载、查看技能正文、查看插件工具和错误、开关能力总开关/控制台/自动回复/技能提示词。

```json
"capabilities": {
  "enabled": true,
  "skills_dirs": ["skills", "data/skills"],
  "plugins_dirs": ["plugins", "data/plugins"],
  "mcp_config": "data/mcp.json",
  "skills_in_prompt": true,
  "console_enabled": true,
  "auto_reply_enabled": false,
  "timeout_sec": 20,
  "max_tools": 40
}
```

## 7.5 DSH 插件（DeepSeek Harness）

`dsh-plugin/` 是一个**无构建**的 Cordis bundle，把 Deep肥鱼接进 DeepSeek Harness（下称 DSH）：DSH 侧栏出现「Deep肥鱼」原生面板，`设置 → Deep肥鱼` 里还有一页依赖环境（六个功能的依赖状态与一键安装）和「关联 DSH 插件」（一键安装上游的桌宠 `dsh-dafeiyu` / 心情 `dsh-feiyu-mood`，npm 最新版、白名单 + 二次确认；它们由 Harness 会话驱动，与控制台里的同名功能是两套），Harness 里的智能体拿到 8 个 `deepfeiyu_*` 工具，插件还能按需拉起/停止本仓库的控制台进程。它不重写本项目，也不新增运行时——Host 半（`index.js` + `lib/`）只通过 HTTP 调用既有的 `http://127.0.0.1:8765` API，Client 半（`client.js`）是注册在 DSH slot 里的原生 React 面板（不是 iframe）；面板数据走同源、复用 DSH 连接鉴权的插件路由，不新开对外端口，也不新增无鉴权数据通道。

**安装**

1. 先在仓库根目录跑一次 `run.bat`，确认 `.venv` 可用；插件不负责装 Python 依赖，`.venv` 缺失时面板会给出「先运行 run.bat」的可读错误。
2. 在 DSH 的插件管理界面里安装本地目录 `dsh-plugin/`；或让 DSH 里的智能体用 `plugin_manager` 安装本地目录（`action=install_bundle`，`target` 填 `dsh-plugin/` 的绝对路径）。
3. 安装后刷新 DSH 页面（首次安装要加载新的 Client 模块）；替换已装包后需要一次插件重载（在插件页把「Deep肥鱼」关一次再打开，或重启 Harness）才会加载新的 Host 代码。
4. 默认 `mode=auto`、`autostart=true`：插件激活时先探测控制台，没运行就用本仓库 `.venv` 的 Python 拉起 `-m app.main`；已经在运行则直接附着。只想附着、不许插件拉起进程，把 `mode` 改成 `attach`。

> 验证状态（2026-11-01）：bundle 已实机安装（`application: applied`），8 个 `deepfeiyu_*` 工具实机调用通过（并确认发送被服务端身份闸门拦截），面板数据与交互经用户验收；真实微信发送因本机无可用通道未验证，`ask` 模式的审批弹窗未实测（本次会话为完全批准）。自动化测试覆盖 Python 侧 `send` 的 `source` 语义与 bundle 静态一致性（manifest / patch / locale / icon / files / 8 个工具名 / `node --check`）。设置页「依赖环境」的 Client 半已在浏览器注册（`settings.section` 出现 occupant `deepfeiyu`，页面可见）；Host 半的 `deps.*` / `dshPlugins.*` 路由需要一次插件重载（插件页关/开一次，或重启 Harness）才会加载，重载后设置页按轮询自行恢复。设置页按钮（控制台依赖安装、关联 DSH 插件安装）的浏览器内点击验收仍待确认；「关联 DSH 插件」验证时未实际下载第三方 bundle。

**配置**（写在 Harness 的插件配置里；不是 `data/config.json`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `projectDir` | 由插件包位置推断（`dsh-plugin/` 的上一级） | Deep肥鱼仓库根，可覆盖 |
| `pythonPath` | `<projectDir>/.venv/Scripts/python.exe`（非 Windows 为 `.venv/bin/python`） | 拉起控制台用的解释器；不存在时报可读错误，不自动下载依赖 |
| `mode` | `auto` | `auto` 自动拉起；`attach` 只探测、不拉起 |
| `autostart` | `true` | 仅 `mode=auto` 时生效：插件激活即拉起 |
| `host` / `port` | `127.0.0.1` / `8765` | 控制台监听地址；与控制台 `server.host/port` 不一致时连不上 |
| `accessToken` | 空 | 控制台访问令牌；空则依次回退环境变量 `DEEPFEIYU_ACCESS_TOKEN`、`data/config.json` 的 `server.access_token` |
| `requestTimeoutMs` | `5000` | 普通请求超时 |
| `sendTimeoutMs` | `60000` | 发送超时；服务端节流可能排队，所以比普通请求长 |
| `panelTitle` | `Deep肥鱼` | 面板标题 |

**安全说明**

- **服务端闸门不削弱**：插件的所有发送都走 Python 既有 `send_text`，身份闸门（`_assert_send_allowed`）、`send.forbidden_wxids`、写操作只允许本机、未设令牌时的本机来源限制与令牌校验全部原样生效，插件绕不过去。
- **发送来源**：面板里人点「发送」等价控制台人工发送（`source=human`，不排队）；Agent 调 `deepfeiyu_send` 用 `source=dsh`，走既有发送节流（全局最小间隔 + 同会话冷却），不是新增的发送通道。
- **审批跟随 Harness 权限模式**：逐步审批 / 自动审批模式下，Agent 发送先走审批（只有 `allowed-once` 才发出）；完全批准模式下不弹审批，但仍受上面全部服务端闸门约束；没有审批服务时默认拒绝发送（fail closed）。
- **令牌不落盘**：访问令牌只由 Host 半在内存中读取（插件配置 → 环境变量 → `data/config.json`），不回显给面板、不进工具结果、不写日志、不写入仓库。
- **只连本机**：插件只请求 `http://127.0.0.1:<port>`，不引入遥测、外联请求或第三方上报；聊天内容只回给调用它的面板/智能体，不落插件日志。
- **面板数据路由有鉴权**：面板读数据走插件注册的同源路由，先复用 DSH 的浏览器 Cookie 与 Host/Origin 校验，未通过返回 401/403；不存在绕过 DSH 身份的无鉴权数据通道。
- **依赖安装不新增执行能力**：设置页的「一键安装」只是经同源路由调用控制台的 `/api/deps/*`，pip 命令与 requirements 路径由 Python 侧写死白名单；插件本身不执行 shell/pip，也不接受前端传入的包名。
- **第三方 DSH 插件安装有白名单**：设置页「关联 DSH 插件」只允许 `dsh-dafeiyu` / `dsh-feiyu-mood` 两个固定包（npm 最新版），前端只能传白名单 id，点击前二次确认；它经 Host 的 `pluginManager.installBundle` 安装，等价于用户在 DSH 插件页手动安装，不绕过 DSH 自身的版本兼容与构建脚本审批；Agent 工具仍只能走需要 danger-full-access 或审批的 `plugin_manager`。
- 插件管理的控制台子进程由插件自己拉起，卸载时只终止**自己拉起**的进程；`attach` 到的已有实例不动。

**卸载**

用 DSH 插件管理卸载（或让智能体 `plugin_manager remove_bundle`），然后刷新页面。回滚到不带插件：卸载后删除 `dsh-plugin/`，本次 Python 侧改动（`SendBody.source` 可选字段）不改变既有调用方行为，可随分支整体丢弃。卸载顺序建议先确认控制台已被插件停止或本就是你手工启动的实例，避免留下「已附着」的假象。

## 8. 目录结构

```
app/
  __init__.py          产品版本号 __version__ 的唯一来源（与 git tag 一致）
  main.py              FastAPI：多账号 REST + WebSocket + SSE
  config.py            配置与账号 CRUD（data/config.json）、本机来源白名单
  db.py                SQLite（按账号隔离的会话/消息/事件/用量，user_version 迁移）
  rules.py             回复策略
  bus.py               进程内事件总线
  update.py            更新检查（由 origin 推导远端，限 Gitee/GitHub）
  emoji.py             表情墙、取图与缓存
  markdown_lite.py     控制台用的极简 Markdown 渲染（EULA 与说明页共用）
  eula.py / helpdoc.py / usage.py / whale.py / pricing.py
  ai/
    llm.py             OpenAI 兼容流式客户端（DeepSeek 默认）
    embedding.py       可选 OpenAI 兼容 /embeddings 客户端
    memory.py          记忆 v2：分层、检索、档案、压缩、整理、写入分级与 TTL
    agent.py           Function Calling 循环
    tools.py           绑定到具体账号的微信工具集 + 能力工具挂载
    web.py             联网搜索/抓取/下载（内网拦截，逐跳复检）
    chuanzi.py / memes.py / tagger.py / vision.py / moe_wiki.py
  wechat/
    base.py            驱动接口
    wcferry_driver.py  微信 3.9.x 本地 Hook（支持 host/port）
    weixin4_driver.py  微信 4.x：后台解密收 + UIA 前台发
    webhook_driver.py  HTTP 网关（支持按账号路由）
    moments.py         朋友圈收发与互动
    throttle.py        发送节流（全局最小间隔 + 同会话冷却）
    ui_gate.py         前台 UI 独占锁（同一时刻只有一路 RPA 在操作微信）
    diagnose.py        环境自检 + 微信窗口枚举
    manager.py         AccountSession（单账号，含发送身份闸门）+ Hub（多账号）
  capabilities/        技能 / 插件 / MCP 统一注册表与上下文过滤
  mood/                心情：七项情绪、每轮判断、半衰回归、按额度注入提示词
  proactive/           主动搭话：掷骰引擎、发送前闸门、调度器与 REST
  growth/              自我进化：反思、提案、审批与成长记忆
  bili/                B 站 MCP 客户端与只读巡逻（工具集在代码侧过滤）
  pet/                 桌宠：素材装配、协议盖章、状态归约、气泡、子进程监管
  deps/                可选功能依赖：白名单安装命令、状态探测与增量日志（/api/deps）
  static/              控制台前端（原生 JS，无构建）
skills/                可选：本机 SKILL.md 技能
plugins/               可选：本机 plugin.json 插件
dsh-plugin/            DSH 插件 bundle（可选）：Harness 面板 + Agent 工具 + 控制台监管
tools/                 开发辅助：假模型服务、素材装配、发布快照与扫描闸门
tests/                 pytest（无网络依赖；上游 helper 契约测试真跑子进程）
vendor/                上游组件：MIT 目录随仓分发，受限素材以子模块引用
docs/                  docs/使用说明.md 是控制台「说明」页的数据源
requirements*.txt      核心 + weixin4 / moments / pet / dev 四组可选
run.bat                裸机一键：便携 Python + 建 venv + 分组安装 + 启动
data/                  运行数据（已 gitignore；config.json、agent.db、.env 都在这一侧）
```

---

## 9. Webhook 协议（多账号）

设置账号驱动为 `webhook`，填 `callback_url` 与 `token`。

**入站**（外部 → 本应用）

```http
POST /api/webhook/inbound
Content-Type: application/json

{
  "account": "acc_work",          // 二选一：直接指定账号 id
  "token": "该账号的 token",       // 二选一：用该账号自己的 token 匹配
  "chat_id": "wxid_xxx",
  "chat_name": "林晓",
  "sender_id": "wxid_xxx",
  "sender_name": "林晓",
  "content": "在吗",
  "is_group": false,
  "msg_type": "text"
}
```

路由顺序：`account` → 唯一匹配的 `token` → 唯一的 webhook 账号。都对不上会返回 400，避免串号。
指定 `account` 时，若该账号配了 `token`，请求必须带同一个 token，否则返回 403——`account` 只用于路由，不能替代凭据。

**出站**（本应用 → 外部）

```http
POST <callback_url>
{"action":"send_text","to":"<chat_id>","content":"...","token":"...","ts":1730000000}
```

---

## 10. API 一览

下表只列常用的一组；完整清单以运行中的 **`/docs`** 为准（页头带版本号，由 `app/__init__.py` 的 `__version__` 提供）。整族未列在表里的有：`/api/moments/*`（朋友圈）、`/api/personas/*`（人设）、`/api/mood/*`、`/api/pet/*`、`/api/proactive/*`、`/api/bili/*`、`/api/growth/*`、`/api/usage`、`/api/eula`、`/api/health`、`/api/events`、`/api/weixin4/accounts`、`/api/server/shutdown|restart`、`/api/agent/upload`、`/api/chats/batch-ai`、`/dsh-whale/*`，以及 `/api/emojis` 的 `import-qq / batch-delete / batch-lock / retag / consolidate-tags` 等。

三条贯穿性规则：**所有非只读请求只允许本机回环地址**（`config.LOCAL_HOSTS`）；**设了访问令牌后 `/api/*` 与 `/ws` 都要带令牌**；**未设令牌时 `/api/*` 只接受本机来源**（同机其他进程仍可读，跨站写请求还会按 `Origin` 拒绝），`/ws` 始终只允许本机。即使只在本机使用也建议设置访问令牌。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/status` | 当前账号的通道 / AI / 能力状态 |
| GET | `/api/accounts` | 账号列表与连接状态 |
| POST | `/api/accounts` | 新增账号 |
| PUT | `/api/accounts/{id}` | 修改账号（自动重启该账号通道） |
| DELETE | `/api/accounts/{id}` | 删除账号（至少保留一个） |
| POST | `/api/accounts/{id}/activate` | 切换当前账号 |
| GET | `/api/instances` | 微信窗口 / 版本 / Hook 可行性 |
| GET | `/api/stats` | 当前账号统计、7 日趋势、token 用量 |
| GET | `/api/chats` | 当前账号会话列表 |
| GET | `/api/chats/{uid}/messages` | 消息记录（uid = `账号id:会话id`） |
| POST | `/api/chats/{uid}/send` | 发消息（`source` 可选 `human` / `dsh`，默认 `human`；触发接管暂停） |
| POST | `/api/chats/{uid}/send-emoji` | 人工发动画表情（wcferry / weixin4 支持） |
| GET | `/api/emojis` | 当前账号收藏的表情 |
| POST | `/api/emojis` | 收藏表情 |
| POST | `/api/emojis/batch-zip` | 导出选中（`ids`）或全部（`all`）表情，zip 含 `manifest.json` 标签清单 |
| POST | `/api/emojis/import` | 批量导入 zip / 散图；zip 还原标签，散图用 `tags` 统一打标 |
| DELETE | `/api/emojis/{id}` | 移除收藏 |
| POST | `/api/emojis/{id}/lock` | 锁定/解锁单张表情（body `{"locked": true/false}`） |
| GET/POST | `/api/emoji/tag-locks` | 查询/切换标签锁（body `{"tag": "...", "locked": true/false}`） |
| GET | `/api/emoji/proxy` | 表情 CDN 代理（展示 / 下载，`download=1` 触发下载） |
| POST | `/api/chats/{uid}/ai` | 开关该会话 AI |
| POST | `/api/chats/{uid}/resume` | 解除接管暂停 |
| GET/PUT | `/api/settings` | 全局配置（模型/策略/令牌，Key 脱敏） |
| POST | `/api/settings/test-ai` | 连通性测试 |
| GET | `/api/accounts/{id}/memories` | 记忆列表（`keyword/layer/kind/chat_uid/pinned` 筛选，附 stats） |
| POST | `/api/accounts/{id}/memories` | 手动添加记忆（支持 `layer/subject/confidence/pinned`） |
| PUT/DELETE | `/api/accounts/{id}/memories/{mid}` | 修改 / 删除单条记忆 |
| POST | `/api/accounts/{id}/memories/batch-delete` | 批量删除（别人的 id 自动跳过） |
| POST | `/api/accounts/{id}/memories/consolidate` | 整理：合并重复、清过期、衰减、重算档案 |
| POST | `/api/accounts/{id}/memories/reindex` | 重建 embedding 索引（未配置返回 `ok=false`） |
| POST | `/api/accounts/{id}/memories/import` | 从 JSON 导入记忆（同内容自动合并） |
| GET | `/api/accounts/{id}/memory-profiles` | 人物档案列表 |
| POST | `/api/accounts/{id}/memory-profiles/{chat_uid}/rebuild` | 重算单个会话的人物档案 |
| GET | `/api/capabilities` | 技能 / 插件 / MCP 状态与已挂载工具 |
| POST | `/api/capabilities/reload` | 重新扫描技能/插件并重连 MCP |
| GET/PUT | `/api/capabilities/mcp-config` | 读取 / 保存 `data/mcp.json` 并重载 |
| PUT | `/api/capabilities/settings` | 开关能力总开关/控制台/自动回复/技能提示词 |
| GET | `/api/capabilities/skills/{name}` | 读取一个技能的完整正文 |
| POST | `/api/capabilities/mcp/{name}/test` | 查看单个 MCP 服务器连接与工具状态 |
| GET | `/api/diagnose` | 环境自检 |
| GET | `/api/deps/state` | 六个可选功能的依赖就绪状态与最近安装阶段 |
| POST | `/api/deps/install` | 一键安装某功能依赖（body `{"feature":"weixin4"}`；命令固定白名单） |
| GET | `/api/deps/install/log` | 增量读取安装日志（`feature`、`offset`） |
| GET | `/api/update/check` | 检查更新（`force=1` 跳过缓存），对比本地与 Gitee 远端 master |
| POST | `/api/driver/restart` | 重启当前账号通道 |
| GET | `/api/agent/models` | 可用模型与思考强度选项 |
| GET | `/api/agent/conversations` | 控制台历史会话列表 |
| GET | `/api/agent/conversations/{id}` | 单个会话与历史轮次 |
| DELETE | `/api/agent/conversations/{id}` | 删除控制台会话 |
| POST | `/api/agent/chat` | 智能体对话（SSE 流，支持会话 / 模型 / 思考强度） |
| WS | `/ws` | 实时事件（消息 / 状态 / 策略） |

所有 `/api/*` 可用 `X-Account-Id` 头指定账号；不传则用当前激活账号。

串子站额度：searchanythingfun.xyz 是公开额度接口，约 30 次/分、500 次/日；只有开启「串子站表情包」开关后才会产生请求，本机自限日上限 450 次（`app/ai/chuanzi.py` 的 `DAILY_LIMIT`）。每轮自动回复最多请求 1 次，命中 429、日额度用完或网络失败都不重试、不排队，本轮直接回落本地表情库。

---

## 11. 风险与合规（务必阅读）

> 以下是使用本项目的主要法律与安全风险提示，不构成法律意见。软件按「现状」提供，许可与责任边界以 [EULA.md](EULA.md) 为准。

### 11.1 平台规则与账号风险

- 个人微信自动化**不符合《微信个人账号使用规范》**，存在被限制登录或封号的风险。建议先用小号验证。
- 本地 Hook 属于注入式方案，微信升级会失效；请固定 3.9.10.27 并关闭自动更新。
- 多开时端口配错会**串号**（把 A 号的消息发给 B 号），上线前先用「文件传输助手」验证。
- 只操作您本人所有或已获明确授权的账号与设备；不得用本软件侵入他人设备、窃取聊天记录或绕过安全措施。
- 「主动搭话」「跨对话代发」会以你的账号主动向他人发送消息，仅限本人账号与已明确同意的对象；不得用于营销、骚扰或陌生人群发。
- 本项目与腾讯公司及微信团队**不存在隶属、合作或授权关系**；「微信」「腾讯」等名称仅用于说明兼容对象。
- 如权利人认为本项目侵犯其权利，可通过仓库 Issues 联系；运营方会配合下架、移除或修改相关内容。

### 11.2 个人信息与数据合规

- 聊天记录包含聊天对象的个人信息。您需要具备合法性基础（例如取得同意、履行合同所必需等），并遵守告知、最小必要、存储期限与删除要求（《个人信息保护法》《数据安全法》）。
- 建议在群公告、签名或首次自动回复中告知对方「本会话可能由 AI 自动回复」；不要把 AI 自动回复用在需要明确人工身份的场合。
- 身份证件、生物识别、金融账户、行踪轨迹、健康医疗、未成年人信息属于敏感个人信息；不要把这些内容发给模型，确需处理的应取得**单独同意**并开展个人信息保护影响评估。
- 不要用本软件非法采集、买卖或泄露个人信息。跨对话转发默认开启，请定期检查事件流并收紧 `cross.*` 配置；发现异常先到「设置 → AI」关掉「跨对话沟通」。
- **数据出境**：模型、embedding、联网搜图若使用境外服务，相关数据可能构成出境；请自行完成安全评估、标准合同或保护认证，或改用境内/本地服务。
- 备份与导出（记忆 JSON、表情 zip）同样含个人信息，请按同等标准保管。

### 11.3 AI 生成内容与服务边界

- 输出由第三方模型概率生成，可能不准确、不完整或不当；不构成法律、医疗、金融等专业建议。不要让 AI 自动处理金钱、合同、身份验证、隐私类对话。
- 《人工智能生成合成内容标识办法》要求对可能引起公众混淆的 AI 生成内容添加**显式标识**。本软件**不会默认**标注 AI 身份：请在「设置 → 自动回复策略」把 `auto.prefix` 设为 `[AI]` 等显著文字，并在编辑、转发时保留标识，不得删除或伪造。
- 本软件仅供个人自用。**不得**将其（含修改版）作为面向境内公众的生成式 AI 服务、深度合成服务或算法推荐服务对外运营；如对外提供，需自行履行备案、安全评估、算法备案与内容标识义务。
- 广告、营销、医疗、法律、证券等受监管场景，另需遵守《广告法》《互联网广告管理办法》等行业规定。
- 未成年人：不得诱导未成年人沉迷、与未成年人进行不当交往，或向其发送违法有害信息。

### 11.4 第三方内容、著作权与抓取

- 联网搜图、表情墙、串子站台词与截图的权利归原权利人；仅限个人合理使用，不得用于侵权、违法或未经许可的商业用途。
- 不得绕过来源站点的 `robots` 协议、访问控制、频率限制或其他技术措施进行批量抓取，也不得用抓取内容建立替代性数据库或镜像站。
- 第三方站点接口可能随时变更或在未告知的情况下收紧额度；本软件已内置自限（每轮最多查站 1 次、日上限 450 次），但仍以站点条款为准。
- 第三方组件与素材的许可边界见 [NOTICE](NOTICE)；发行包不内嵌受限素材，需从上游自取。

### 11.5 本机部署与信息安全

- 默认只监听 `127.0.0.1`；写操作只允许回环来源，`/ws` 始终只允许本机。
- **未设访问令牌时，`/api/*` 只接受本机来源，但同机其他进程仍可读取聊天与配置**；跨机请求会被 403 拒绝。即使只在本机使用，也建议在「设置 → 服务」设置访问令牌。
- 不要把控制台监听到 `0.0.0.0` 或暴露到公网/局域网；未设令牌时跨机读取已被拒绝，但暴露本身仍会扩大攻击面。如确需跨设备访问，请自行加反向代理、TLS、强令牌与防火墙规则。
- 写请求已做 `Origin` 同源校验，浏览器跨站请求会被 403 拒绝；访问令牌仍是阻止本机其他进程与凭据泄漏的主要闸门，请保持开启。
- Webhook 入站若指定 `account`，该账号配了 `token` 就必须带同一个 token；`account` 只用于路由，不能替代凭据。
- `data/` 含完整聊天记录、记忆、表情与下载文件；`.env` 含 API Key；`data/mcp.json` 可能含 MCP 令牌。建议开启磁盘加密（如 BitLocker）、限制目录权限、不要把 `data/` 同步到公有云盘。
- 密钥只写 `.env`（已 gitignore），配置回显只保留后 4 位；不要把 Key 贴进 Issue、日志或截图。
- 联网抓取会逐跳校验并拒绝内网地址、下载文件名做净化、表情代理只允许公网 http/https；已知**未防 DNS rebinding**（校验与实际连接是两次解析），不要把不可信 URL 交给 AI。
- 技能 `SKILL.md` 本身不执行代码，但会作为指令进入模型上下文；恶意技能可以诱导 AI 使用已有工具。安装前先读正文。
- Python 插件（`trusted: true`）与 MCP 服务器以本应用权限运行，**没有沙箱**；只装可信来源。默认只在控制台可用，自动回复需显式打开 `capabilities.auto_reply_enabled` 与条目的 `auto_reply: true`。
- 启动时的更新检查只读取 Gitee/GitHub 的版本号，不自动下载或执行更新；升级请自行 `git pull` 并审阅 diff。
- `run.bat` 的便携 Python 带 SHA256 校验，桌宠素材校验 npm 公布的 sha1；但 pip 依赖仍来自公网，建议使用可信镜像并固定版本。
- 发现安全漏洞请通过仓库 Issues（标注 `security`）或发布页公示的私下渠道报告，修复前请勿公开细节。

### 11.6 许可与再分发

- 本项目采用 **PolyForm Noncommercial License 1.0.0**：源码开放，仅限**非商业目的**的学习、研究、实验、教学与个人使用。
- 允许为非商业目的修改、创建衍生作品并再分发；再分发须保留 [LICENSE](LICENSE)、[NOTICE](NOTICE)、`Required Notice` 与版权声明，并对接收方适用同一许可。
- **禁止**商业使用、销售、转售、按次收费、托管服务、代运营、账号矩阵与营销群发。
- 该许可**不是** OSI 认可的开源许可；[NOTICE](NOTICE) 列明的第三方组件仍按各自许可证（如 MIT）授权，根目录 [LICENSE](LICENSE) 不改变其许可。

---

## 12. 开发调试

```bash
# 本地假模型（无需 API Key，验证流式 + 工具调用）
python tools/fake_llm.py          # 127.0.0.1:8899
# 设置里把 Base URL 改成 http://127.0.0.1:8899，Key 随便填

# 全量测试（1438 例，含心情 / 主动搭话 / 记忆 v2 / 能力系统 / 发送闸门）
# 测试依赖不在核心依赖里，先装一次：
.venv\Scripts\python.exe -m pip --isolated install -r requirements-dev.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe -m pytest -q
```

Windows 下用 `.venv\Scripts\python.exe`；其他平台用 `.venv/bin/python`。

文档约定：用户可见的改动要同时更新 `docs/使用说明.md` 与 `CHANGELOG.md`；控制台「说明」页直接读这份使用说明，改完刷新即见。新增控制台页面时，使用说明的页面表必须补一行，否则 `tests/test_usage_doc.py` 的覆盖检查会失败。

贡献与审查规则见 [AGENTS.md](AGENTS.md)：所有改动走 Issue + PR，必须人工审查、脱敏、测试后合并；主干禁止直接推送与强制推送。

---

## 13. 最终用户许可协议（EULA）

本项目采用 **PolyForm Noncommercial License 1.0.0**（源码开放、仅限非商业目的），许可正文见 [LICENSE](LICENSE)；使用条款见 [EULA.md](EULA.md)（[English](EULA.en.md)），当前为 **1.2 版（2026-11-01 生效）**。中文版本为最终版本，英文译本仅供参考。该许可不是 OSI 开源许可，禁止商业使用；`NOTICE` 列明的第三方组件仍按各自许可证授权，`LICENSE` 不改变其许可。

控制台侧栏「协议」与「设置 → 使用边界」都可以打开协议页；也可以直接访问 `/eula`（如 <http://127.0.0.1:8765/eula>，加 `?lang=en` 查看英文）。

每个 Release 的说明顶部会自动附带 [RELEASE_DISCLAIMER.md](RELEASE_DISCLAIMER.md)：非官方、非商业、按现状、无担保、责任限制、赔偿与下架配合。

---

## 14. 第三方组件

各组件的许可范围、pin 版本，以及发行包里**不**包含哪些内容，集中写在 [NOTICE](NOTICE)。要点：两个上游子模块的**美术素材不在 MIT 覆盖范围内**（whale 的 `assets/**` 与 `icon.png`、pet 的 `legacy/`），因此本仓库与 Release 附件都不内嵌它们，需 `git clone --recursive` 或 `run.bat` 从上游自取。

- `vendor/bilibili-mcp/`：B 站只读 MCP，来自 [dsh-bili-subagent](https://github.com/Yuuyuko-uu/dsh-bili-subagent) 的 vendor，上游 [bilibili-mcp](https://github.com/adoresever/bilibili-mcp)，MIT。DeepFeiyu 只通过 `BILI_MCP_TOOLS` 注册只读工具，发布/互动工具默认不开放。
- `dsh-feiyu-mood`（Yuuyuko-uu，MIT）：心情判断规则与挂饰素材；本项目按 MIT 许可原生移植为 `app/mood/` 与 `app/static/mood-widget.js`，素材溯源见 `vendor/dsh-feiyu-mood/PROVENANCE.md`。
- `dsh-dafeiyu`（QCYTSN，MIT）：桌宠的原生 Helper（`runtime/*.py`）与角色帧素材。DeepFeiyu 以子进程方式复用，**不改上游一行代码**；帧为 MIT（上游 PC2005-cloud/dsh-pet），光标为 BSD-3（Chromium），提示音 MIT。来源、校验值与许可构成见 `vendor/dsh-feiyu-pet-PROVENANCE.md`；老「大肥鱼」帧不受 MIT 覆盖、未引入。
- `vendor/dsh-whale-widget/`：小鲸鱼余额挂件，MIT，素材授权见其 `PROVENANCE.md`。

