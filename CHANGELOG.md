# 更新日志

「Deep肥鱼」（DeepFeiyu WeChat Agent）的所有显著变更记录在本文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。`v1.0.0` 是本项目的第一个发行版，其下按开发日期分组记录了从 2026-10-21 起的全部显著变更——包括后来被移除或改写的实验，阅读时以「当前版本实际包含什么」为准，功能可用性以控制台与 `docs/使用说明.md` 为准。

## [1.0.0] - 2026-11-01（重新发行）

> 本版为 **v1.0.0 的重新发行**：在原 1.0.0 基础上补充法律合规、安全修复、非商业许可、协作规则与发行免责声明。原 tag 与 Release 已由本次快照替换。

**本次重新发行包含**

- 许可切换为 **PolyForm Noncommercial License 1.0.0**（源码开放、仅限非商业目的），EULA 升至 1.2。
- 安全修复：未设令牌时跨机读接口 403、Webhook `account` 绕过 token、`Origin` 跨站校验、归档解包路径穿越、令牌恒时比较。
- 新增 `AGENTS.md`（Issue + PR、人工审查、脱敏、测试后合并）与 `RELEASE_DISCLAIMER.md`（发行说明免责声明）。
- README 第 11 节重构为六类合规与安全提示；移除「养号」「风控降频」「全权限」等表述。

### 2026-11-01 · 发行免责声明

- 新增 `RELEASE_DISCLAIMER.md`：明确非官方、非商业、按现状、无担保、运营方不运营服务/不收账号/不收集聊天/不代发消息，用户自行承担责任并赔偿，责任上限与权利人下架配合。
- `.github/workflows/release.yml` 在创建 Release 前把该声明拼到 `notes.md` 顶部，保证每个发行说明都带免责声明。
- `README` 协议章节补充指向 `RELEASE_DISCLAIMER.md`；新增回归测试防止 workflow 漏掉该步骤。

### 2026-11-01 · 新增协作与审查规则

- 新增 `AGENTS.md`：明确拒绝许愿式需求；所有代码必须人工审查、脱敏、测试后合并；主干禁止直接推送与强制推送；仅 Owner 授权的维护者可合并与打 tag；所有问题走 Issue + PR；补充 AI 代理边界、安全红线、完成标准与违规处理。
- `README` 开发调试章节补充指向 `AGENTS.md` 的入口。

### 2026-11-01 · 许可切换为非商业源码许可

- `LICENSE` 改为 **PolyForm Noncommercial License 1.0.0**：源码开放，允许非商业目的的学习、研究、教学、个人使用与修改、再分发；禁止商业使用、销售、转售、托管与代运营。该许可**不是** OSI 开源许可，正文保留官方英文条款与 `Required Notice`。
- `EULA` 升至 **1.2**：许可授予改为 PolyForm NC；允许非商业再分发修改版；删除「禁止公开再分发修改版」，改为保护 `Required Notice` 与许可声明；新增权利人通知与下架配合条款。
- `README`、`NOTICE` 同步许可表述；补充「与腾讯无隶属、合作或授权关系」与权利人通知渠道。
- 移除 README 中「风控降频」「养号」「全权限」等易被理解为规避平台限制的表述。
- 按项目所有者决定，保留 `wcferry` / `weixin4` 驱动；相关法律风险仍由使用者与分发者自行承担。

### 2026-11-01 · 法律合规与信息安全文档修订

- `EULA` 升至 **1.1**（2026-11-01 生效）：补充生成式 AI 服务边界（不得面向公众运营、备案与安全评估义务）、AI 生成内容显式标识（`auto.prefix`）、个人信息保护与数据出境、未成年人保护、本机服务安全等条款；联系方式统一为 Gitee 仓库。
- `NOTICE` 补充 MIT 版权与许可全文，以及根许可不适用于第三方组件的边界说明。
- `README` 第 11 节重构为六类合规与安全提示（平台规则、个人信息、AI 标识与服务边界、第三方内容与抓取、本机信息安全、许可与再分发）；API 章节补充「未设令牌时接口只接受本机来源」。
- `tests/test_eula.py` 同步条款数与版本断言（66 条）。

### 2026-11-01 · 安全修复

- `/api/*` 在未设置访问令牌时只接受本机来源：此前把 `server.host` 改成 `0.0.0.0` 会让局域网读到聊天与配置，现在跨机读请求返回 403。
- 写请求增加 `Origin` 同源校验：浏览器跨站请求返回 403；Webhook 网关、脚本等不带 `Origin` 的客户端不受影响。
- Webhook 入站：指定 `account` 时，若该账号配了 `token` 必须带同一个 token，堵住「用 account 绕过 token」的注入口。
- 令牌比较改为恒时比较；查询参数里的令牌只允许 `GET/HEAD`（图片、下载链接用），写请求只认请求头。
- 桌宠素材解包增加路径穿越防护：zip 逐条校验绝对路径与 `..`，tar 使用 `filter="data"`。
- 新增 `tests/test_local_guard.py`、`tests/test_webhook_inbound.py`、`tests/test_pet_assets.py` 用例；`tests/test_readme_claims.py` 改用显式用例 id，避免 Windows 环境变量长度上限。

### 2026-10-31 · 首次发行

本版本是 Deep肥鱼的第一个正式发行版：一个有独立人格、掌管自己多个微信账号的 AI 智能体——消息实时接入，它自己判断该不该回，再调用工具发消息，附一个可操作的 Web 控制台。

**包含的能力**

- 三条微信通道：`wcferry`（PC 3.9.x 本地 Hook，全权限收发）、`weixin4`（PC 4.x，后台解密数据库收 + UIA 前台发送）、`webhook`（自建 HTTP 网关）。多账号之间数据与策略隔离。
- 记忆 v2（分层长期记忆、人物档案、可选语义检索、写入分级与低价值 TTL）、心情、主动搭话、朋友圈收发与互动、B 站只读巡逻、可选桌面宠物。
- 能力系统：本机 `SKILL.md` 技能、`plugin.json` 插件、通用 MCP 服务器都可挂到智能体上；默认只在控制台可用，不默认进入自动回复。
- 裸机一键运行：`run.bat` 自带便携 Python 3.12 与分组可选依赖，某一组装不上只关掉对应功能、不挡启动。

**这一版的安全姿态**

- 新功能默认关闭；没有可用通道时不启动驱动。
- 发送受身份闸门保护：通道绑定的微信身份确认不了、或命中 `send.forbidden_wxids`，一律只收不发；4.x 账号目录失配时报错停止，不自动改绑别的账号。
- 所有非只读请求只允许本机回环地址；设置访问令牌后 `/api/*` 与 `/ws` 都要带令牌；联网抓取逐跳拒绝内网地址；AI 发文件限定在本程序目录。
- 密钥只存 `.env`，配置回显只留后 4 位；聊天记录与配置都在 `data/`（不入库）。

**发行前已知未验收 / 未解决**

- 桌宠的 Windows 实机表现（透明置顶窗、点击穿透、多屏拖动）仍需人工验收；缺 PySide6 或缺素材时桌宠不启动，微信收发不受影响。
- 联网抓取的 `_assert_public` 未防 DNS rebinding（校验与实际连接是两次解析）。
- 本地 Hook 属注入式方案，微信升级会失效；4.x 发送依赖 UIA 与前台窗口，短时会抢前台。

许可为专有软件，条款见 `LICENSE` 与 `EULA.md`；第三方组件与「发行包不含哪些素材」见 `NOTICE`。

### 2026-10-31 · 许可与署名

- 新增 `LICENSE`：明确本项目为**专有软件**，条款以 `EULA.md`（中文正本）为准，不采用 MIT/Apache/GPL。
- 新增 `NOTICE`：逐项列出第三方组件的许可、子模块 pin 版本与 commit，并写明发行包**不含**上游受限素材（whale 的 `assets/**` 与 `icon.png` 由上游声明「不授予再许可」，pet 的 `legacy/` 不在 MIT 覆盖内），以及 `dsh-speak-up` 的 clean-room 立场与 `ai-moe-wiki`（CC BY-NC）不打包。
- `EULA` §8.1（中英双语）补上第三方边界：著作权声明不及于第三方组件，本协议不作超出各组件自身许可的再授权。
- README §13/§14 指向上述两份文件。

### 2026-10-30 · 控制台写操作只允许本机

- 所有非只读请求（改配置、发消息、重启、增删白名单等）校验来源必须是回环地址；此前只有心情/桌宠/搭话/B 站四个模块自带本机校验，`/api/settings`、`/api/agent/chat`、`/api/growth/*`、`/api/server/restart` 都没有。
- 本机来源白名单收到 `config.LOCAL_HOSTS` 一处定义，模块内校验与全局中间件共用，不再各写一份地址集合。
- 启动时提示两类风险：未设置访问令牌（`/api` 读接口对本机其他进程开放）、监听在非回环地址。
- 新增 `tests/test_local_guard.py`：远程 POST/DELETE 必须 403，本机与 TestClient 放行，远程 GET 与 OPTIONS 不拦。
- `/ws` 实时推送此前不受访问令牌约束（http 中间件只管 `/api/` 且覆盖不到 WebSocket），而未带令牌的连接一样能收到 `hello` 帧里的全部账号信息与后续聊天正文。现在连接在 `accept` 之前校验令牌与来源，控制台自动把令牌拼到 WebSocket 地址上。新增 `tests/test_ws_guard.py`。
- `/api/webhook/inbound` 删掉「本机只有一个 webhook 会话就接收」的兜底：那等于一个免凭据注入口，本机任何进程都能伪造入站消息驱动 AI 回复。现在必须带 `account` 或该账号的 `token`，否则 400。新增 `tests/test_webhook_inbound.py`（含带凭据仍能路由的正向用例）。
- 联网抓取的 SSRF 防护此前只校验第一个 URL：`follow_redirects=True` 让 httpx 在校验之后自己跟跳，一个 302 就能把请求指到 `127.0.0.1:8765` 把聊天与配置读回来喂给模型。现在关闭自动重定向、自己逐跳跟，每一跳重新解析并判内网，超过 5 跳中止；下载路径同样处理并显式关闭流连接。新增 `tests/test_web_ssrf_redirect.py`（拒绝跳回环、正常公网链仍跟到底、跳数封顶）。
- **仍未解决**：`_assert_public` 的 DNS 解析与实际连接是两次解析，理论上存在 rebinding 窗口；本机回环与内网段是主要威胁面，公网重定向已封。

### 2026-10-30 · 安装链路

- 依赖拆成核心 + 三组可选（`requirements.txt` / `requirements-weixin4.txt` / `requirements-moments.txt` / `requirements-pet.txt`），Windows-only 的包补上 `sys_platform == "win32"` 标记。此前注释写着「可选」的朋友圈与 4.x 依赖其实是必装行，任一装不上整个 `pip install` 就失败、程序根本起不来；现在可选组装不上只关掉对应功能并打印一行说明。
- `run.bat` 会执行 `git submodule update --init --recursive`：桌宠 Helper 与余额挂件素材来自上游仓库，公开包不内嵌这些素材，新克隆的机器必须自己拉；没有 Git 时跳过而不是卡死。
- 便携 Python 下载加 SHA256 校验（对照值取自实际从 `nuget.azure.cn` 取得的 3.12.8 包）。
- README 与使用说明的首次运行步骤改为逐条列出这四件事。
- 新增 `requirements-dev.txt`（`pytest` / `pytest-subtests`）。此前测试依赖只存在于作者手工装过的旧 `.venv` 里，任何一次重建都会失去跑测试的能力——拆分可选依赖时漏了这一组。
- 可选组之间的传递关系写进注释：装了 4.x 那组后，`numpy` / `opencv-python` / `pyautogui` 大多已随 `wechatauto-replica` 进来，朋友圈那组真正补的是 `pywin32` / `winsdk` / `psutil`。

### 2026-10-30 · 产品版本号

- 新增 `app/__init__.py` 的 `__version__ = "1.0.0"` 作为唯一来源；此前全仓没有版本号，「检查更新」只比对远端 commit 短哈希。
- 接入各展示位：FastAPI `/docs` 页、启动横幅、`GET /api/health` 的 `version`、余额挂件的 `versionLabel`（原来外显的是 `deepfeiyu` 这个占位串）、侧栏与「软件更新」页显示 `v1.0.0 · 短哈希`。
- 去掉「非 git 安装」的措辞：解压安装的副本本来就只显版本号并可继续用，那句话会让人以为装坏了。
- 新增 `tests/test_version.py` 守住各展示位确实读同一常量。
- 更新检查的比对对象不再写死成私有 Gitee 仓：改由本机 `origin` 推导，主机限定 Gitee/GitHub 白名单，其他地址一律拒绝。此前公开仓的用户从 GitHub 取代码，「检查更新」却去打一个私有仓的 API，永远查不到东西。
- 分支解析顺序改为「当前分支的上游 → 远端默认分支 → master」，不再用当前分支名兜底：本地分支或 checkout 出来的 tag 在远端不存在，会一直误报「远端没有 xxx 分支」。
- 侧栏「软件更新」的副标题不再向用户承诺具体平台；新增 `tests/test_update_source.py`。

### 2026-10-30 · AI 能力面的边界收紧

- `send_wechat_file` 此前接受任意本地绝对路径，只要文件存在就发：一句诱导就能把 `.env` 或 `data/agent.db` 发给任意联系人。现在收敛到本程序的下载/上传目录，越界直接回错误给模型；控制台人工选文件不受影响。新增 `tests/test_send_file_scope.py`。
- B 站巡逻的「只读」不再只靠生成的 `data/bili/mcp.json` 工具白名单：巡逻取工具时按 `mcp__bilibili__` 前缀全量拿，绕过了 `client.FORBIDDEN_TOOLS`，用户手改 mcp.json 或被诱导点重装就能拿到 `bili_reply`/`bili_upload_video`。现在工具集在代码侧再过一道禁止名单；`bili.allow_publish` 是无人读取的死配置，已删除。新增 `tests/test_bili_readonly_gate.py`。
- 收件人解析不再「子串首个命中即发」：`resolve_chat` 在本地会话里按子串匹配到多个候选时抛错并列出候选名，而不是默默返回第一条——重名联系人或同名群会被发错对象。全等名与 uid/chat_id 命中仍优先，跨对话转告原有的歧义保护不变。工具调度器已把异常统一回给模型（`app/ai/agent.py:212`），无需逐个调用点改。新增 `tests/test_resolve_chat_ambiguous.py`。
- 配置回显脱敏：`config.public()` 原先只藏模型 API Key，账号的 `webhook.token` 与 `server.access_token` 原样回给前端；`/api/capabilities/mcp-config` 还会把含 `Authorization` 的 mcp.json 整份回传。现在这三处都只回 `•` 加后 4 位，并在保存时把没改动过的脱敏串换回真值（`_unmask_secrets` / `_restore_mcp_secrets`），直接保存不会把 `••••abcd` 写成凭据。新增 `tests/test_config_masking.py`。
- 已核实为审查误报、未改生产代码：「插件工具可把模型参数当整条 URL 用」。`call_http_tool` 的 scheme 检查作用在模板原文上且早于渲染，整串 `{arg}` 模板在渲染前就被拒；参数插进路径时按 `quote(safe="")` 转义，改不了主机与 userinfo。新增 `tests/test_plugin_url_template.py` 把该行为钉成回归约束。

### 2026-10-30 · 发送身份闸门

- 新增 `send.forbidden_wxids`（默认空）：命中名单的账号在任何发送出口一律拒绝，只收不发。真实 wxid 只写本地 `data/config.json`，代码与仓库里不带账号身份。
- 三个发送出口（文本 / 文件 / 表情）统一先过身份闸门：通道没启动、或没能确认通道实际绑定的微信身份时拒绝发送，不再「不知道是谁就照发」。
- 微信 4.x 账号目录失配不再自动改绑：目录不存在时通道报错停止并提示重新选择，此前它会挑一个「有可见窗口的账号」继续跑，存在把消息发到另一个号上的风险。
- 新增 `tests/test_send_gate.py`：覆盖禁发名单命中、身份未知、配置缺 `send` 段、通道未启动四种负控。

### 2026-10-29 · 桌面宠物（上游 dsh-dafeiyu Helper）

- 新增 `app/pet/`：`assets`（素材装配与 manifest 重写校验）、`protocol`（stdin 信封盖章）、`reducer`（事件总线 → 七个 Helper 状态）、`bubble`（气泡文案与隐私清洗）、`moodlink`（心情 → 待机节奏与脉冲）、`settings`（配置与 `DSH_DAFEIYU_*` 映射）、`supervisor`（子进程监管与退避重启）、`api`（`/api/pet/*`）。
- 新增「桌宠」设置页与 `tools/pet_assets.py` 装配脚本；上游 Helper 代码零修改，逐字节复制到 `data/pet/`。
- 默认**关闭**。缺 PySide6、缺素材或素材来源变更时桌宠不启动，控制台显示具体原因，微信收发不受影响。
- 动作只由运行状态决定（在想 / 在忙 / 回好了 / 发送失败 / 微信没连上）；心情通过气泡措辞、三档待机节奏与瞬时脉冲影响表现，不新造动画语义。
- 隐私：气泡只显示状态词、会话名（默认开、可关）与短摘要，正文原文永不进气泡；摘要默认关且过凭证样式扫描。
- 素材来源：`vendor/dsh-feiyu-pet` 子模块 pin `v0.1.23`（默认源）；npm 官方包 `dsh-dafeiyu-0.1.23.tgz` 作弱网兜底，装配前比对 registry 公布的 sha1。
- 自动验证：`tests/test_pet_*` 覆盖装配、协议信封、状态归约、文案、心情换算、REST 与页面接线；契约测试真跑上游 `helper.py --headless`，含非法状态与缺版本号的负控。
- **未验证**：Windows 实机的透明置顶窗、点击穿透与多屏拖动需要装 PySide6 后人工验收，见设计文档第 7 节。


### 2026-10-29 · 主动搭话（Phase 3）

- 新增 `app/proactive/`：`settings`（账号默认值、范围夹取、白名单规范化）、`engine`（峰/谷时段、安静时段、时间系数、命中线）、`store`（每会话状态与事件）、`rules`（发送前闸门）、`service`（掷骰 → 闸门 → 生成 → 发送 → 记账）、`scheduler`（每 60 秒一轮）、`api`。
- 默认**关闭**；没有白名单不能启用，移除白名单最后一条会自动关掉开关。开启前控制台需二次确认——掷中后是**真的往微信发**。
- 掷骰不调模型：间隔、冷却、账号/单会话日上限、安静时段（含起不含止、可跨午夜、起==止即关闭）全部通过才掷一次；命中线 = 峰/谷基准 × 「刚聊过/冷场」时间系数 + 没掷中连败加成（有上限），没掷中也写记录并保留连败计数，重启不再清零。
- 生成走专用提示词与受限工具集（`run_proactive_turn`）：告诉 AI 这是「你自己想开口」而不是对方发了话，最多 2 轮；只留表情检索/发送与记忆检索，表情工具只认当前会话 uid（`to` 填会话名/chat_id 也可能被同名会话抢先命中，一律拒绝）、记忆检索必须带关键词（空查询会漏出别的会话的记忆），代发/文件/配置等副作用工具一律不给。`[SKIP]` 与空输出不发，超长只截断后发；试运行不消耗心情注入额度。
- 每次发送前重过闸门：通道在线、会话属于该账号、会话 AI 未关、非内部会话、不撞 `auto.blacklist`、`only_whitelist` 开着时名单不能为空且要在其中、不在人工接管暂停、命中搭话白名单；不过则记 `skipped`，不发。生成那十几秒里关掉总开关或把它移出白名单同样会被拦下；重查→发送→记账在同一把账号锁里完成，并发轮次不能挤爆日上限。
- 控制台新增「搭话」页（导航在「心情」之后）：开关与调度状态、每会话状态表、白名单增删（群聊需显式勾选确认）、全部掷骰参数、试一试三种按钮（试运行不发 / 真发一条不占额度 / 手动补一条占额度）、最近记录表。
- 新增 API：`GET /api/proactive/status`、`POST /api/proactive/config`、`GET /api/proactive/chats`、`POST /api/proactive/whitelist`、`POST /api/proactive/test`、`POST /api/proactive/run`、`GET /api/proactive/events`；写接口只允许本机控制台，`/config` 拒绝改白名单（必须走 `/whitelist`，防止绕过归属校验）。
- 与「主动串门」互不干扰：串门接号主正在聊的话，搭话是隔一段时间自己开口；配置、额度、记录各自独立。
- `docs/使用说明.md` 新增「主动搭话」一节（原 6–14 节顺延为 7–15），页面表补「搭话」行；新增 `tests/test_proactive_ui.py` 与既有文档覆盖测试一起守住「新增页面必须写文档」。
- Clean-room 说明：上游 `dsh-speak-up` 仓库**没有 LICENSE 文件**，本项目不复制其代码、提示词原文与素材，只按 README 描述的行为重写。
- 新增 9 个测试文件共 264 例（存储/引擎/闸门/生成/服务/调度/API/控制台/端到端），全量 `pytest` 1171 例通过；端到端串起调度→闸门→真实 Agent→驱动→库→事件→心情钩子。

### 2026-10-29 · 控制台新增「说明」页

- `docs/使用说明.md` 进入控制台左侧「说明」页：`GET /api/usage` 每次现读文件并渲染，改完文档刷新即见，不用重启或构建。
- Markdown 渲染器抽到 `app/markdown_lite.py`，补表格与有序列表；EULA 继续复用同一套渲染。
- 页面顶部显示最后更新时间，可手动刷新；说明正文沿用协议页排版，表格自适应窄屏。
- 新增文档覆盖测试：`index.html` 里每个导航页都必须在使用说明中出现，新增页面不写文档会让测试失败。
- 约定：用户可见改动同时更新 `docs/使用说明.md` 与 `CHANGELOG.md`。

### 2026-10-29 · 修复合并转发记录被提前截断

- 问题：合并转发的聊天记录只读到 3~4 条就出现「……（后略）」。`parse_chat_record` 里 `used` 累计的是字符数，却和条数上限 `max_items`（默认 80）比较，整段累计满 80 字就误判条数到顶——截图里 3 条共约 96 字，第 4 条起全被丢掉
- 条数与字数分开计：`count` 管条数上限，`used + len(line)` 管 1500 字预算；两条截断路径各有回归测试
- 已入库的旧记录（id 为「会话#分片#local_id」）下次启动回填时按同 id REPLACE 自动修复，无需手工清库；全量 `pytest` 893 例通过

### 2026-10-29 · 心情（Phase 2）

- 新增 `app/mood/`：七项情绪、每轮小模型判断、半衰回归、按可信度/显著性/日上限注入提示词。
- 自动回复与控制台回合结束后各触发一次判断；默认关闭，控制台「心情」页一键开启。
- 控制台右下角新增心情挂饰（基于 dsh-feiyu-mood 0.7.6，MIT），可拖动、记忆位置、收起成小圆。
- 新增四页签设置：心情 / 会话绑定 / 判断与注入 / 挂饰；多账号隔离，槽位可绑多个会话。
- 明确偏离上游：默认关；时间线不设 365 天硬上限；挂饰增加位置记忆。
- 判断采样按上游 0.7.6 实测：`temperature=0.2`、`max_tokens=1000`（400 会截断判断 JSON）。
- 全量 `pytest` 886 例通过；`node --check` 校验 `app.js` 与 `mood-widget.js` 通过。

### 2026-10-29 · 微信「点不动」排查：发送链路降扰 + 图片解密修复

- 现象：号主反馈「一开本项目微信就死机」，具体是主窗口单击全废、双击却能弹出独立会话窗口（说明微信 UI 线程没停，是单击被吞了）。本机实测：驱动每次发送都要先做一轮桌面根节点的 UIA 全树遍历——`ListControl(searchDepth=0xFFFFFFFF)` 8.8s 未命中 + 从 `GetRootControl()` 的 `max_depth=25` DFS 6.6s/4282 节点，被扫到的应用（含微信）都得在自己的 UI 线程上应答，而这条路径每条消息都要付一遍
- `weixin4` 驱动的 `_find_search_list` 改为只在「微信主窗口 + 同进程其它顶层窗口」子树里找搜索下拉；没命中就照实失败，不再退回桌面全扫（仅当库内部名字漂移、限定范围走不通时才回退上游实现，保证发得出去）
- 启动不再预热 UIA 发送通道：预热那一次会扫 `Weixin.dll` 强开无障碍 gate、物化整棵 mmui 控件树，正好落在「刚打开项目」那一刻。改为首次发送时再做，代价是第一条回复慢几秒；旧行为可用账号配置 `weixin4.warmup_on_start = true` 恢复
- 朋友圈轮询默认 120s → 600s：每轮都要解密一次 `sns.db`（外加 WAL 合并），实测还会顺带解密动态配图并去拉远端图片；本机内存只剩 3.5~4 GB（占用 87~89%，服务进程 909 MB 私有里 80% 被压进页文件），这个固定负载直接压到微信身上
- 修复入站图片全部读不出来：`diagnose._load_wx4_module` 按文件裸加载 `media.py` 时没带包上下文，而库里有**函数内**延迟相对导入（第 280 行 `from . import db as _dbmod` 等），载入阶段毫无异常，直到真去解一张图才抛「attempted relative import with no known parent package」——实测一天 104 次「读取图片失败」
- 新增 `tools/wx_click_watch.py`（只读取证工具）：全局低级钩子按 `LLMHF_INJECTED` 区分「真人硬件」与「机器人注入」，把每次按下/抬起、微信消息循环应答探测、内存水位放在同一条时间轴，用来验证「注入事件夹在按下与抬起之间 → mmui 把单击判成拖拽丢掉」这条假设；只记键码以外的信息，落盘在 `data/`（不记按键内容）
- 未定论的旁枝：本机微信窗口 `GetDpiForWindow=144` 而进程内 `GetDpiForSystem=96`，钩子坐标与 `SetCursorPos`/`GetWindowRect` 之间一度量出 ~1.5× 的偏移，两次复测互相矛盾；坐标换算是否还有第二个问题，留给看门狗的数据判

### 2026-10-29 · 移除自动回复「引用原消息」

- 自动回复不再以微信原生「引用」带上触发消息，所有回复按普通文本发出；此前逐条定位消息行、右键点「引用」的界面步骤整体删掉，发送链路只剩搜索会话 → 校验目标 → 输入 → 回车
- 一并移除设置页「回复时引用原消息」开关、`auto.quote_reply` 配置、驱动 `supports_quote` 能力与引用回声对账（`quote:` 留底键、80 字 `<title>` 截断处理）及相关测试；微信里他人发来的引用消息仍照常解析、展示并参与 AI 上下文
- 若旧配置里还留着 `quote_reply`，会被当成未知键忽略，不用手工清理

### 2026-10-28 · 结尾表情改为「等对话告一段落再发」

- 问题：秒回 + 分条后每轮自动回复都跟一张表情，连续接话时体感是「一句一个」，太密
- 新增 `send.trailing_emoji_gap_sec`（默认 `[3, 5]` 秒安静期）：正文发出后先等这段，期间对方又接话/新一轮回复开始（内部序列号变化）就作废，等真正告一段落的那轮再跟图
- 新增 `send.trailing_emoji_cooldown_sec`（默认 120 秒）：同一会话两次结尾表情的最小间隔，聊天节奏慢时也不会每轮都跟
- 安静期配成 0 时保持旧行为（正文发完立即跟图），测试/想立刻跟图可用
- 实测（真实模型离线回放）：两轮快速对话 `emoji=0`；安静期结束后 `emoji=1`；冷却期内再聊一轮仍 `emoji=1`
- 新增 3 个回归用例（安静期延迟、对话继续时作废、冷却抑制）；相关测试配置补 `send` 默认值，全量 672 通过（2 个仓库既有失败与本次无关）

### 2026-10-28 · 修复朋友圈读成号主个人微信、刷新不出来

- **读错微信号**：多开机器上历史同步可能落到号主个人号（`wxid_friend_02…`）的 `sns.db`，控制台里 125 位作者有 124 位只在号主个人号的好友列表里；当前驱动虽已正确解密机器人号（`wxid_friend_01…`），旧缓存没清、同步水位还是号主号的 tid（比机器人号最新 tid 还新），增量拉取永远判定「没新动态」——两件事叠加就是「读取的是我的微信 + 刷新不出来」
- 修复：朋友圈缓存按**当前解密账号**重建——新增 `moments:{account}:owner` 记录归属，与 `driver.db.account` 不一致时清空动态/互动、水位归零后重新同步；配置 `weixin4.account_dir` 与驱动账号不一致时拒绝同步并报错，避免把另一个微信号的动态写进来；同步水位自检（比当前账号最新 tid 还新即判定来自别的号并重置）
- **刷新改为深度刷新**：控制台「立即同步」先打开微信朋友圈页，让客户端拉一次最新时间线（纯读本地库只能看到上次打开时的缓存），等几秒写库后再读，完事切回聊天页；打开失败自动退回纯库同步并在提示里说明。接口 `POST /api/moments/sync` 支持 `{"deep": false}` 只读库
- 点赞/评论/深度刷新都显式**绑定驱动账号的窗口**：此前 `WeChatGUI()` 自动挑「最大的可见微信窗口」，多开时会点到号主个人微信的窗口上
- 控制台状态显示当前账号昵称；页面文案说明深度刷新会短暂操作微信窗口
- 实测：重启后自动清掉 313 条错号动态 / 105 条互动并按机器人号重建（114 条，作者是机器人号好友 iYa、海鸥别叫了等）；深刷 9.4s 返回 `ui_opened=true`，完成后窗口切回聊天页、发送链路正常；全量 669 通过（2 个仓库既有失败与本次无关）
- 新增 `tests/test_moments_account.py`：切号重建、同号不动、水位修复、深刷、错号拒绝、清缓存计数共 6 例

### 2026-10-28 · 修复表情包系统：只能发最近几张、偶发发不出

- **候选池只取最新 20 张**：`_pick_reply_emoji` 原来用 `db.search_emojis(account, "", 30)`，该查询按 `hit_count/last_seen/id` 排序且内部把上限夹到 20——库里 408 张表情时永远只在最新 20 张（实测只会发 id 537~556 那批企鹅）里挑，用户体感就是「只能发最近添加的几张」「鲸鱼娘表情包哪去了」。现在改为：全库（`db.list_emojis`）→ 硬闸/仅AI娘过滤 → 上下文相关优先 + 随机多样性 + 本会话最近发过的沉底 → 最多 40 张送挑；小库（≤20 张）保持旧排序不变
- **偶发发不出**：挑选模型回 0/编号不在候选/LLM 失败时，从全库随机兜底一张（硬闸场景与严格「仅AI娘」模式除外）；发送异常自动换一张重试一次；挑选提示词改为「除非对话严肃到明显不适合，否则必须挑一个」；模型自己选的表情实际发成功时不再重复补
- 新增日志：每轮记录「全库 N 张，本轮送挑 M 张」、挑选回退、发送失败换图重试，便于以后排查
- 实测（真实模型 + 真实表情库离线回放 3 轮）：每轮都是正文（含分条）→ 结尾表情，候选覆盖 id 157~513 的旧表情；随机抽 old id 144/241/267 经控制台 API 实发成功；全量测试 663 通过（2 个仓库既有失败与本次无关）

### 2026-10-28 · 自动回复改为「分条发送 + 每轮结尾一个表情包」

- **分条发送，不再换行塞进一条**：`rules.split_reply` 除了按单独一行的 `---` 拆，还会把回复里的换行按行拆开（`第一句\n\n第二句` → 两条独立消息），每条消息内部不留换行；超过 3 条时用空格并入最后一条，不丢内容。提示词同步改成「要说两句以上时每句话单独成一条消息，用 `---` 分隔，不要用换行代替分条」
- **修复结尾表情包发不出来**：`_pick_reply_emoji` 在「仅AI娘表情包」开关打开、但 `ai.moe_wiki_path` 指向的图鉴目录不存在时，`character_names()` 为空 → `roster_tagged` 把候选全部清空 → 「回复必带表情包」每轮都静默跳过了表情。现在图鉴不可用时 fail-open 退回普通候选（图鉴可用时仍严格按角色名过滤），并记一条 info 日志
- 测试：新增分条（纯换行/混合 `---`）、结尾表情顺序、「仅AI娘+图鉴缺失仍发结尾表情」等用例；全量 658 通过（2 个仓库既有失败与本次无关）
- 实测（真实模型 + 真实表情库，离线回放）：回复被拆成 3 条无换行短消息，最后紧跟 1 个表情包，顺序为 `text,text,text,emoji`

### 2026-10-28 · 修复微信重启后无法发送（UIA 控件树「gate 没激活」）

- 根因：上一次「发送走 UIA」改造里，`_NamedSearchUIA.__init__` 没有把 hwnd 传给库的 `WeChatUIA.__init__`（只在构造后给 `self.hwnd` 赋值），导致库里的 `_bound_pid` 为空、`_candidate_hwnds()` 恒返回空。微信重启/升级后无障碍 gate 字节归零、旧窗口绑定失效时，`ensure_window()` 会在「会话窗口候选为空」分支**静默返回 False**（没有任何热激活尝试日志），发送侧直接报「微信控件树不可用」。只要微信不重启（gate 一直是 1）就暴露不出来，所以之前一直正常
- 修复：构造引擎时把 hwnd 传进库的 `__init__`（旧版库不收该参数时退回手工补 `_bound_pid`），保持「只认这个账号窗口」的 `_find_main` 钉定逻辑
- 加固：`_ensure_uia` 失败时重建引擎并「显式补一轮热激活 + 等待树物化」，最多重试 3 次（微信刚重启/窗口未就绪时等几秒再找窗口）；每次失败记录 hwnd/pid/bound_pid 诊断日志；启动预热失败也改为隔 5s 重试 3 次
- 实测：先复位 WeChat 进程里的 gate 字节（模拟微信重启），再通过控制台 API 发送——自动热激活 gate 并发送成功（2.2s），日志出现「热激活 UIA：PID=… 0 -> 1 / UIA 树已物化」
- 新增 `tests/test_weixin4_send.py` 回归用例 2 例：hwnd 必须传进库（`_bound_pid` 被正确记录）、旧版库不收 hwnd 时自动补 `_bound_pid`

### 2026-10-28 · 发送后恢复前台窗口

- weixin4 的 RPA 发送任务开始前记下前台窗口，任务结束（成功或失败）把微信顶掉的前台还回去；号主本来就在微信里、或中途自己切了别的窗口则不抢
- 覆盖自动回复、跨对话、表情/文件、拍一拍等所有走 UIA 发送队列的操作；启动预热也不再让微信留在前台
- 新增 `tests/test_weixin4_send.py` 用例：成功/失败都恢复，原窗口是微信/已失效/最小化/前台已变时跳过

### 2026-10-28 · 最终用户许可协议与协议页

- 新增中文 `EULA.md` 与英文译本 `EULA.en.md`（条款 1–16 对齐，约定中文版本为准）；覆盖微信自动化账号风险、AI 自动回复与跨对话发送、第三方模型/联网服务的数据流向、插件与 MCP 代码执行、免责与责任限制、消费者权利保留
- 新增 `GET /eula?lang=zh|en` 独立协议页与 `GET /api/eula?lang=zh|en` 接口；`app/eula.py` 读取根目录 Markdown，转义后渲染标题、引用、列表与链接
- 控制台新增「协议」页（侧栏导航与「设置 → 使用边界」入口）：中文/English 切换、语言偏好持久化、加载失败提示、新窗口打开（跟随当前语言）
- README 增加协议章节；新增 `tests/test_eula.py`（中英文条款对齐、Markdown 渲染与转义、接口/页面）

### 2026-10-28 · 自动回复引用原消息

AI 回消息默认带上微信原生「引用」，让对方一眼知道在答哪条。

- 自动回复首条文字以「引用」发出：仍是最新一条时滚到最底部、核对底部行（正文前缀或消息类型）后再引用；历史里的目标按可见正文前缀查找，方向必须是对方、短句不是最新就不猜
- 引用卡挂上后粘贴**不清空**输入区（Ctrl+A/Delete 可能摘掉引用卡）；输入框已有草稿/残留、或挂引用这几秒里号主开始打字，都取消引用（前者交给普通发送清场，后者直接放弃本次回复，不清掉号主正在打的字）；落不进输入框也退回普通发送
- 定位、右键菜单、粘贴任一不成立都不阻塞回复；分条续发只有首条带引用，表情/文件与秒回纯表情回复不引用；拍一拍/系统通知不引用
- 引用回复落库是 appmsg type=57 XML：回声对账按 refermsg 的 `<title>`（实际发出的文字）匹配按前 80 字登记的出站留底；只有长度正好 80+1 的「…」才当微信截断符，正文自带的省略号原样保留，避免长回复/带省略号回复被误判成「人工接管」而暂停 AI。引用发出后注销普通正文回声键，避免 90 秒内把号主手打的同文消息当成回声吞掉
- 控制台出站记录带 `meta.refer`（引用者/摘要/被引用图片或表情），气泡按与入站引用同款样式渲染引用块；被引用图片按原消息 id 取图
- 设置 → 自动回复策略新增「回复时引用原消息」开关（`auto.quote_reply`，默认开）；wcferry / webhook 通道没有引用入口，开关只对 weixin4 生效

### 2026-10-28 · 自我进化·心脏：连续自我、夜间反思与可回滚成长

给 Deep肥鱼加一套默认关闭、可审计、可回滚的成长子系统；本期只做「心脏」，不写技能/代码、不主动外发。

- DB 升到 v6：新增 `growth_state/traits/journal/goals/proposals/events/runs` 七张表；特质无总量上限，支持 `scope/chat_uid` 会话作用域、强度/置信度/证据与退役/休眠
- 反思：默认 03:30 每账号每天一次，关机启动后补跑；输入取最近消息/控制台对话/新增记忆/自我内容，输出日记、自我总览、特质与目标；反思器不带工具，只产出 JSON
- 风险闸：低风险自动生效；立场跳变、身份变化、修改号主特质进待批；安全越界、假证据、命令句/链接/密钥直接拒绝；所有变化写时间线，可回滚，冲突需强制确认
- 注入：控制台与微信自动回复在基础人设后注入动态成长块；关系特质只在对应会话注入；关闭成长时提示词逐字不变
- 控制台：新增「成长」页（状态、自我总览、特质库、日记、目标、待批、时间线、导出）；控制台智能体新增只读 `recall_growth` / `read_journal`
- API：新增 `/api/growth/*` 状态/特质/日记/目标/提案/事件/总览/反思/设置/导出接口
- 测试：新增 9 个成长测试文件

### 2026-10-27 · 记忆 v2：三层、人物档案、混合检索、压缩与整理

把原来「一张扁平表 + 固定 12 条注入」的记忆重做成三层，并新增人物档案与整理/压缩链路。

- DB 升到 v5：`memories` 增量补 `layer/subject/confidence/pinned/embedding/last_access/access_count/expires_at/source/meta`；新增 `memory_profiles`。老库按 `kind=event` 归入 `episodic`，其余归 `core`，不重写内容
- 检索：`关键词 + 可选 embedding 余弦 + 重要度 + 新近度 + 置顶` 混合打分；`build_context` 按 `memory.context_chars` 字符预算拼「人物档案 + 长期事实 + 相关往事 + 最近会话」，并记 `last_access/access_count`；没配 embedding 自动退回关键词，不阻塞回复
- 写入：抽取 prompt 增加 `layer/subject/confidence`；完全相同的记忆自动合并；回复后把超阈值的较早对话压成 `session` 摘要（默认 7 天过期，只留最近两条）；新增「整理记忆」做清过期、合并重复、45 天衰减、重算档案
- API：记忆列表支持 `layer/kind/chat_uid/pinned` 筛选并返回 stats；新增人物档案列表/重算、整理、重建 embedding 索引、JSON 导入
- 控制台：「记忆」页改成「记忆流 / 人物档案」；支持层级/类型/置顶筛选、置顶、改层级、编辑、批删、JSON 导入导出；设置页可配注入预算、条数、压缩阈值和 embedding
- 工具：`remember` 增加 `layer/subject/confidence`；`recall_memory` 走 async 混合检索
- 新增 `tests/test_memory_v2.py`（21 例）；全量 `pytest` 648 例通过

### 2026-10-27 · 能力系统：SKILL.md、plugin.json、通用 MCP

新增能力注册表，让本机技能、插件和 MCP 服务器都能挂到同一个智能体上。

- 技能：递归扫描 `skills/`、`data/skills/` 的 `SKILL.md`，解析 YAML frontmatter；系统提示只列名字+描述，模型需要时用 `load_skill` 读正文，`list_skills` 列出已安装技能。技能只读，不自动执行脚本
- 插件：扫描 `plugins/`、`data/plugins/` 的 `plugin.json`；支持声明式 HTTP 工具（`{arg}` 占位、URL 编码、超时/截断）和显式 `trusted: true` 的 Python 插件（`register(add_tool)`）；工具名 `plugin__<插件>__<工具>`
- MCP：新增 stdio + Streamable HTTP 客户端，读取 `data/mcp.json`（兼容 `mcpServers`），实现 initialize / notifications/initialized / tools/list / tools/call；工具名 `mcp__<服务器>__<工具>`；启动失败、超时、进程退出都只记状态，不拖垮主应用
- 安全边界：技能只读；插件/MCP 默认只在控制台可用；自动回复要同时打开 `capabilities.auto_reply_enabled` 和条目自己的 `auto_reply: true`
- 控制台：新增「能力」页，展示技能/插件/MCP 状态与工具、查看技能正文、测试 MCP、编辑 `data/mcp.json` 并重载；开关能力总开关/控制台/自动回复/技能提示词
- API：`GET /api/capabilities`、`POST /api/capabilities/reload`、`GET/PUT /api/capabilities/mcp-config`、`PUT /api/capabilities/settings`、`GET /api/capabilities/skills/{name}`、`POST /api/capabilities/mcp/{name}/test`
- 新增 `tests/test_capabilities.py`（13 例，含假 MCP stdio 服务器、HTTP 插件、上下文过滤）；README 补记忆 v2 与能力系统章节

### 2026-10-27 · 控制台智能体可读朋友圈配图

真机现象（控制台对话「锐评一下 mios 最新朋友圈」）：智能体拿到了动态正文、图片张数和点赞评论，却明确说自己「读不到图、没有下载入口、也没有能看图的工具」，只能按「信息塞图、正文不解释」的习惯猜图里是什么。

- 新增控制台工具 `view_wechat_moment_images`：按 `browse_wechat_moments` 返回的 `feed_id`（或 `publisher` + `keyword`）读取一条动态的配图，交给识图模型输出画面与图中文字描述；不传 `index` 默认读前 3 张（最多 4 张），传 `index` 读指定第几张（从 1 开始），`question` 用来带上用户的具体问题
- 返回里带每张图的本地 `path`，需要转发原图时可直接交给 `send_wechat_file`；识图未开启时工具明确提示去「设置 → 模型」打开，不静默失败
- `MomentsService` 新增 `download_media_detail`：返回 `status/path/reason`，缓存过期、解密失败、CDN 失效、写入失败等原因会翻成中文交给模型，不再只有 `None`
- 图片下载后按 Pillow 识别的真实格式存成固定副本 `image_<index>.<ext>`：二次读图不再依赖会被轮换的微信 Sns 缓存；同时修正缓存把 png/webp 落成 `<md5>.mp4` 的后缀问题，`thumb_data_url` 也不再按扩展名预判，识图缩图统一按内容解码
- 自动回复不开放读图（提示词同步说明「看配图只有控制台能做」）：避免好友朋友圈的图片内容被带进外部会话，也避免识图耗时拖慢聊天回复
- 新增测试：工具只在控制台注册、按 feed_id/publisher 定位、默认/指定张数、识图关闭拦截、全失败原因、缓存副本与错误后缀归一化

### 2026-10-27 · 设置页新增「添加聊天中的表情包」开关

- 设置 → AI 行为新增开关，对应 `ai.emoji_auto_collect`（默认开启）：关闭后聊天里出现的表情包不再自动收进表情库（对方发的、自己发的、群聊/合并转发里的都算），只保留手动收藏与主动导入；开关状态实时生效，无需重启
- 控制台手动收藏（`POST /api/emojis` 等）走 `force=True`，不受该开关影响
- 新增 `tests/test_emoji_auto_collect.py`（5 例）：默认值、开关关闭时跳过自动收集、开启时正常收集、手动收集不受影响、设置页 HTML/JS 接线

### 2026-10-27 · 串子站表情包改为独立开关（默认关闭）

- 新增 `ai.chuanzi_site_emoji`（默认 `false`）：串子模式只管接话语气，是否联网查 searchanythingfun 并入库截图由这个开关单独控制
- 关闭时不查站、不新增站内图：`search_emojis` / `find_emoji_online` / `_pick_reply_emoji` 都只走本地库；自动回复里的 `find_emoji_online` 回到原来的 Bing/斗图啦/发表情联网找图，不再碰串子站
- 已入库的「串子站」表情不受影响：检索、发送、标签锁与导出照常；仅 AI 娘角色名豁免、站内图置顶这些串子特权仍跟串子模式走，与开关无关
- 提示词按开关分两套口径：关闭时不再承诺查站或引用台词库，秒回自动补图改成从本地表情库补；硬闸、安慰图等安全条款原样保留
- 设置页新增「串子站表情包」开关并更新串子模式说明；README、控制台状态块同步
- 新增测试：开关默认关闭时不查站但已入库站内图仍可检索/发送；原有查站测试显式打开开关

### 2026-10-27 · 串子模式改为高频/高级黑，硬闸可配安慰图

- 人设不再整段丢弃：`persona_override` 保留账号级人设（大肥鱼/小肥鱼等）的身份、外形、AI 坦白和「不编造」，只把「语气自然、像真人聊天」和「我确认一下再回你」替换成串子语气；大肥鱼的鲸尾/深蓝女仆装/鲸鱼标不再被写成通用小鲸鱼娘
- 提示词去掉「别每句都串」，改为默认每轮找角度、拿不准也串；新增捧杀/假中立/拉踩/阴阳怪气四种高级黑，明确只对准观点/方案/梗，不对准人；保留 ≤30 字、不解释、禁用开头、每轮查站一次、只拿一张
- 新增 `chuanzi.looks_serious()` 与单轮 `serious` 状态：自杀/自伤/重病/丧亲/借钱/转账/隐私/合同/明确喊停/明显崩溃时硬闸收梗，人格不退出，单句正经回；硬闸可配一张安慰/倾听/正经向的图，不配串子图
- 硬闸期间 `search_emojis` / `find_emoji_online` / `send_wechat_emoji` / `_pick_reply_emoji` 全部跳过串子站，并过滤历史入库的 `source=searchanythingfun` 行；秒回自动补图也只从本地挑安慰/正经向
- 设置页串子模式说明、README、设计文档 §1/§6/§7/§10/§13/§14 同步
- 新增/更新测试：人设外形保留、硬闸触发/误判、硬闸跳过站点与历史站内图、发送拦截、秒回提示词；全量 `pytest` 592 例通过

### 2026-10-26 · 控制台智能体感知「串子模式」

真机现象（21:18，控制台对话「我现在开了串子模式，你给鲸鲸发条试试」）：智能体把「串子模式」当成一个口头风格，自己现编了一段挑衅文案，表情从本地库随手挑了张嚣张小肥鱼；思考里完全没有「设置里有这个模式」的概念，也没有用库内 119 张「串子站」素材。

- 根因：`_console_prompt` 从不读 `ai.chuanzi_mode`；`build_tools` 在控制台传 `chuanzi_state=None`，而站内图的角色名豁免、站内优先排序、发送豁免全都绑在 `chuanzi_on`（有 state 才算）上。于是控制台既不知道模式存在，也拿不到站内图的任何特权
- 新增 `_CONSOLE_CHUANZI_BLOCK`：模式开启时注入「串子模式已开启」——说明自动回复的串子行为、库内 tags 带「串子站」的图可直接用 `search_emojis` + `send_wechat_emoji` 复用；同时写明控制台没有查串子站的工具，`find_emoji_online` 仍走 Bing/斗图啦/发表情，不许声称查过站、不许编台词或假图
- `build_tools` 把「模式开启」与「本轮自动查站」拆开：`chuanzi_mode`（模式开）负责站内图的角色名豁免、置顶与发送豁免；`chuanzi_on`（模式开且有 `chuanzi_state`）才触发自动查站。控制台因此能识别并优先使用库内站内图，又不会因为普通表情检索白耗站点额度
- 新增 `test_console_prompt_announces_chuanzi_mode`（开着有状态块、关掉没有）与 `test_console_site_emoji_privileges_without_state`（无 state 时站内图豁免仅AI娘、置顶，本地杂图仍拦）；全量 `pytest` 576 例通过

### 2026-10-26 · 修复秒回+串子模式「只发文字、不发串子图」

真机现象（20:35，测试会话）：对方说「是串子就发串子表情包」，AI 回「我这不是串子，我这是有原则的肥鱼。」，整轮没有表情；思考里一边写「串子模式下系统会自动补图」，一边在找 `searchanythingfun` 工具——「工具列表里没有……列出的工具没有台词库」。

- 根因一：`_build_prompt` 在 `instant_reply` 且 `emoji_with_reply=False` 时把 `emoji_line` 整段置空，`emoji_required` 也为 False，正文发完不会走 `_pick_reply_emoji`；但 `chuanzi.prompt_block(fast_text=True)` 仍写着「系统会自动从串子站补一张图」。提示词承诺了系统不会做的事，模型也就不再考虑用 `[表情]` 要图
- 根因二：秒回风格块还保留「素材优先来自 searchanythingfun 台词库」「每轮最多查一次」这类工具期指令，而这一轮根本没挂表情工具——模型只能空找一场，或者编一句假台词
- `chuanzi.prompt_block(fast_text=True, auto_emoji=...)` 分两套口径：开着「回复必带表情包」时说「截图由系统补」；关掉时明确「普通回复不自动配图，要图就整条只写 [表情]」，并写明「发图不等于承认自己在玩梗」
- 秒回风格块删掉台词库/查站次数等这一轮够不着的指令，改成「没有表情工具、查不到台词库：不要找工具、不要假装查过台词库；想引用广为人知的梗可以直接说」
- `_build_prompt` 的秒回+串子分支不再依赖 `emoji_with_reply` 把整段置空：关闭时照样给出 `[表情]` 入口；表情消息分支也按同一口径区分「文字会被系统配图」与「只有 [表情] 才配图」
- 新增 `test_instant_reply_chuanzi_emoji_token_sends_site_image_when_not_required`：秒回+串子+关闭必配，模型输出 `[表情]` 时只发一张站内图、不补文字，挑选器看到 `[串子站]` 与台词；`test_build_prompt_chuanzi_fast_text_drops_tool_instructions` 补断言：关闭必配时不得再承诺自动补图、必须给 `[表情]` 入口、不得提台词库工具
- 全量 `pytest` 574 例通过

### 2026-10-26 · 表情墙多选支持批量锁定/解锁

- 新增 `POST /api/emojis/batch-lock`（`ids` + `locked`）：一次给多张表情上锁/解锁，别人的与不存在的 id 自动跳过并回报命中数；`db.set_emojis_locked` 复用批删的 id 清洗规则
- 多选工具条新增「🔒 锁定」按钮：选中项全部已锁时按钮自动切成「🔓 解锁」，混选时统一执行锁定；锁定后标签修改、AI 重打标与删除仍按原合同返回 409，发送不受影响
- `tests/test_emoji_wall.py` 补 3 例：批量上锁/解锁（含锁定后改标签被拦）、跨账号与失效 id 跳过、空 ids 不动库；该文件 60 例通过

### 2026-10-26 · 修复 wxgf 转码交白帧，识图不再把图看成空白

真机现象：群里收到的图片，管理后台预览是一个白块，模型回「The image is blank white」，这一轮 ¥0.016 照扣。落盘文件 `data/media/<账号>/10000000001@chatroom_1365.jpg` 实测 370×850、4009 字节、全图只有 1 种颜色 `(255,255,255)`——模型没看错，是本机给的就是白图。

根因在第三方包：微信 4.x 把静图也用 HEVC 装进 `wxgf` 容器，而 `wechatauto/media.py:_wxgf_to_jpg` 的 ffmpeg 写死 `-frames:v 1`。实测这条 `.dat`（15054 字节明文）里有两帧——偏移 40 的 VPS/SPS/PPS + 94 字节 IDR，SPS 是 `hevc (Rext), gray`，一张纯色空白底；偏移 217 又一套参数集 + 14752 字节 IDR，SPS 是 `hevc (Main), yuvj420p`，真图在第二帧。上游永远只交第一帧。本机 152 张已解密图里 12 张中此雷（约 8%，全部落在 wxgf 分支，普通 JPEG/PNG 的 `.dat` 不受影响），vendor 自带的 `_image_complete` 也拦不住——它只看 JPEG 有没有 `FF D9`，一张完整的空白图照样过关。

- `app/wechat/diagnose.py` 新增 `_patch_wxgf_to_jpg`，沿用 `_patch_get_self_info` 的运行时打补丁路子，在 `wx4_media_module()` 加载后生效：解码至多 `WXGF_MAX_FRAMES=12` 帧，由 `pick_wxgf_frame` 取字节数最大的一帧（空白帧全图同色压不出体积），并列取靠前帧，动画表情仍交首帧；认不出帧照旧返回 None 让上层落 `.wxgf` 兜底，不拿空白冒充成果
- `app/ai/vision.py` 新增 `is_blank_image` / `split_blank_images` 作转码事故兜底：`manager` 在拼 prompt 前剔除整幅纯白/纯黑/128 灰的图并记一条 warn 事件，不再为坏图白跑一轮。判不出来（没装 Pillow、文件不是图）一律不算空白，避免把能看的图误杀成「本机没有图片内容」；全红这类**故意**发的纯色图当正常内容放行
- 新增 `tests/test_wxgf_frames.py`（6 例）：用 ffmpeg 合成「首帧纯白 + 次帧有内容」的 wxgf 样本，先负控断言首帧确实是纯色，再验补丁后交出的是有内容那帧；把选帧改回「取首帧」，2 例立刻失败。`tests/test_vision.py` 补 7 例覆盖空白判定与拆分
- 真机复跑 local_id 1365：4009 字节 1 色 → 50007 字节 16646 色，内容正是那张 LangMem 工具调用截图。全量 455 例通过
- 生效条件：补丁在模块加载时打，需重启服务；已落盘的 12 张空白 jpg 会在下次识图时被同名覆盖

### 2026-10-26 · 朋友圈互动与消息收发共用一把界面锁

真机现象：`comment_wechat_moment` 报「评论未成功：未能定位到目标朋友圈」，同参数的 `browse_wechat_moments` 却正常——本地库里定位是成功的，死在微信界面那一层。日志（`wechatauto_logs/app_20261026.log` 14:45）：14:45:01 已找到 `mmui::TimeLineListView`，14:45:04 发送线程开头的 `back_to_chat_tab()` 点了一下导航栏「微信」把页面切走，时间线控件的 rect 塌成 `(0,0,0,0)`，滚轮的落点校验（进程 71552 != 27060）拦下每一次滚动，`find_moment` 一屏都没翻就返回 None；随后 14:45:14 / :23 / :37、14:46:47 四次重试全撞在同一处。

- 新增 `app/wechat/ui_gate.py`：`UI_LOCK` 成为「谁在动微信界面」的唯一裁决者。`weixin4_driver._rpa_loop` 的每个操作都 `with ui_gate.hold()`，朋友圈 `like` / `like_cancel` / `comment` 改占同一把锁（等锁上限 `moments._UI_WAIT_SECONDS` 45 秒，等不到就回「界面正被消息收发占用，这次先不操作」，绝不硬抢）。原先两边各一把锁：驱动只串行在自己的 RPA 线程里，朋友圈只锁 `_uia_lock`，跨线程照样互相切页面
- 互动前 `_ensure_moments_page()` 先清 `Moment._list` / `_api` 缓存再验证时间线可见：库缓存的列表控件在页面被切走后 `Exists` 仍为真，`_ensure_list` 便不再 `SwitchToMoments`，于是每次重试都对着一个隐形控件空转；页面确实不可见时直接拒绝执行，不再空跑 300 屏滚动
- 失败原因按现场拆开：底层只回一句「未能定位到目标朋友圈」，现在按「回聊天页前记下的时间线还在不在」分成两种——中途被切走（等一会儿再试）与界面里真找不到（太旧刷不出来 / 昵称不一致，换 from_id 或换一条），智能体不再拿同一句话盲目重试
- 操作收尾新增 `_back_to_chat()`，主动把主窗带回聊天页，别把用户的微信一直停在朋友圈
- 新增 `tests/test_moments_ui_gate.py`（8 例）：界面被占时 RPA 线程不插队、等不到界面就不碰窗口、rect 全零拒绝执行、互动前清缓存、成功后回聊天页、两种失败原因分流；变异负控——去掉 `ui_gate.hold()`、不清缓存或不回聊天页，对应用例立刻失败。全量 442 例通过

### 2026-10-26 · MiMo 按官方牌价接入小鲸鱼记账

- 新增 `app/pricing.py`：MiMo v2.6 flash / pro / pro-ultraspeed 内置人民币牌价（元 / 百万 tokens）分别为 `0.02 / 1 / 2`、`0.025 / 3 / 6`、`0.25 / 30 / 60`；官方来源见 [MiMo-V2.6-Flash](https://mimo.mi.com/models/en-US/mimo-v2.6-flash)、[MiMo-V2.6-Pro](https://mimo.mi.com/models/en-US/mimo-v2.6-pro)、[MiMo-V2.6-Pro-UltraSpeed](https://mimo.mi.com/models/zh-CN/mimo-v2.6-pro-ultraspeed)
- `LLM._record_usage` 改按实际模型名取价，切到 MiMo 后不再拿 DeepSeek 的全局 `ai.price_*` 估算；已知 MiMo 模型可在对应 profile 补 `price_input_hit` / `price_input_miss` / `price_output` 覆盖，DeepSeek 与未知模型仍走全局兜底
- 真机账本里已有 `mimo-v2.6-flash` 的 usage 事件，说明 MiMo 无需额外参数也会回传真实 usage；保持只对 DeepSeek 发送 `stream_options.include_usage`，不凭未验证的第三方端点改变请求
- `/dsh-whale/balance.json`、页面账单和智能体 `get_whale_balance` 对 MiMo 返回 `NO_BALANCE_API`：小米官方只在网页控制台提供用量/余额，没有用 API Key 查询的公开接口；每轮与今日金额继续按 MiMo 牌价走本地账本，不伪造余额
- 新增 `tests/test_mimo_pricing.py`（7 例）：服务商识别、三档 MiMo 价、未知模型兜底、profile 覆盖、真实 usage 换算、MiMo 不额外发送 `stream_options`、MiMo 余额接口边界；全量 434 例通过

### 2026-10-26 · 自动回复延迟归因与提速

先按真机数据定位（`data/whale-usage.json` 里每次模型调用的时刻 × `messages` 的出站时刻，13:00–14:05 共 18 条文本回复）：没被冷却卡住的中位 11.6s，被卡住的中位 25.2s（其中纯等冷却 16.8s、最高 19.9s），走表情/查史工具轮次的中位 24.3s。据此修四处：

- 形象附图改缩略图：`vision.self_image_messages` 原来每轮把 `data/self/avatar.jpg` 原图（实测 3072×5504 / 5.9MB → base64 7.9MB）塞进请求，同机同档实测带原图首字 7.56s，纯文本只要 3.11s。现在统一走 `thumb_data_url`（512px、按 路径+mtime 缓存复用，实测 payload 36.9KB、带图首字 0.99s）；Pillow 不可用或图解码失败时这轮直接不带图，绝不回退成原图。控制台对话（`main.py` 同一函数）同源受益
- 发送冷却自适应：新增 `send.direct_gap_sec`（默认 `[8, 12]`），私聊、群里 `@昵称`、被拍一拍这类「点名在等回答」的回复走短冷却，群里的顺嘴闲聊照旧按 `chat_gap_sec` `[20, 45]`；全局 `min_gap_sec` 不放松，风控下限还在。判定收在 `_addressed_to_me`，`_send_gaps` / `_throttled` / `send_text` / `send_emoji` 透传 `direct`
- 表情轮次并行预选：开「秒回」后**所有**消息类型都不再给模型挂表情工具（原先对方只发表情包时保留 `search_emojis` → `send_wechat_emoji`，实测三个来回 10.1s + 6.2s + 6.0s）。`_pick_reply_emoji` 改为收对方那张表情附图，接手「看着图挑」的活，正文第一段增量一到就并行开挑。新增约定输出 `[表情]` = 只回表情包不带文字（`[SKIP]` 仍是彻底不接话，此时并行挑选任务作废，不会莫名甩一张表情过去）
- 补发与分条不再各等一遍冷却：正文后的补发表情按 `burst_gap_sec` 紧跟（原来走完整同会话冷却，成了「文字发完半分钟后蹦出一张表情」）；`split_reply` 的第 2/3 条改 `burst=True`，落在同一批里，不用再等 20–45s
- 修吞消息：秒回时该会话锁被占着，新到的消息以前直接 `return`（表现成「问完就没声了」）。现在攒进 `_queued`，本轮收尾补跑一轮；补跑前重过一遍 `rules.check`（期间被接管/关掉 AI 就不回，记 `补跑前被拦下` 事件），只认 120 秒内的积压、最多留 8 条
- 新增测试：`test_vision.py` 缩略图三条（体积、缓存复用、解码失败宁缺毋滥）、`test_send_throttle.py` 短冷却与点名判定五条、`test_reply_humanization.py` 冷却与积压四条、`test_reply_emoji.py` 表情轮三条（无表情工具 + 仅表情、`[SKIP]` 静默不跟表情、挑选器看得见附图），并改写原先断言「表情消息保留工具」的两条用例。全量 421 例通过

### 2026-10-26 · 发送排队再归因：模型说完到落地之间的等待

接着量 15:30–16:10 群里斗图那段的 23 条出站（每条用「最近一次模型调用时刻 → 消息落库时刻」算差）：14 条等待 <10s（中位 2.1s，就是 UIA 搜会话→校验→粘贴→回读→回车本身），9 条被按在 13.1–36.5s。这 9 条有个共同点——**`@我` 的个数 0/9**，全是群里没人点名的接话，走的是 `chat_gap_sec` 的 `[20, 45]`；短冷却那条路只认私聊/被 @/被拍，斗图场景一条都碰不上。还查出一层项目配置里看不见的叠加节流：weixin4 发送依赖的 wechatauto 自带「拟人节奏」，`natural` 档要求任意两次界面写入之间再隔 2.5–6s、120s 内写满 6 次直接睡 30–75s，状态跨进程落在 `~/.wechatauto/rhythm.json`（今天全天它真正生效过 1 次：15:10:18 `send 前等待 2.0s`，cooloff 0 次——因为自家 20–45s 冷却已经把间隔拉得比它宽；一旦自家冷却调短，这层立刻开始收费）。据此：

- `send.chat_gap_sec` 默认整体降到 `[12, 25]`（`data/config.json` 里存的 `[20, 45]` 也通过 `PUT /api/settings` 改到 `[12, 25]`，运行中的进程立刻按新值排队）
- 连续对话也算点名：新增 `send.conversation_window_sec`（默认 60）与 `_in_live_conversation`，窗口内 AI 自己回过、对方又接了话，群里没 `@` 的续答同样走 `direct_gap_sec` 短冷却；冷场后重新插话、以及只有人工出站（微信回显）的会话都不算，仍按长冷却。判定收在 `AccountSession._quick_reply_warranted`，`_auto_reply_run` 复用已读到的 history 不额外查库
- wechat4 驱动启动时把库内节奏压到 `fast`（写间隔 0.6–1.4s、20 次才 cooloff），只留鼠标轨迹与键击抖动；显式设了 `WECHATAUTO_RHYTHM` 就尊重它不覆盖，见 `weixin4_driver._apply_rhythm_profile`
- 新增测试：`test_send_throttle.py` 默认档位与连续对话判定四条、`test_reply_humanization.py` 群聊续答走短冷却 / 冷场仍走长冷却两条、`test_weixin4_send.py` 节奏档位钉死与环境变量优先两条。全量 463 例通过

### 2026-10-26 · 模型配置分档落地，MiMo v2.6 Flash 可一键切换

- 补上 README 早就写了、但代码里从来没有的「模型配置（profile）」：`ai.profiles` 三档默认值进 `DEFAULTS`（`deepseek-flash` / `mimo-v2.6-flash` / `custom`），每档自带 Base URL、模型名和 `key_env`；`ai.active_profile` 是当前生效档，`ai.base_url` / `ai.model` / `ai.api_key` 降级为它的运行时镜像，`config.load()` 每次按生效档重写，其余代码（`llm`、`whale`、`vision`、`tagger`、`manager`）一行没改就跟着换服务商
- 密钥按档独立：填 Key 只写该档 `key_env` 对应的 `.env` 行（`DEEPSEEK_API_KEY` / `MIMO_API_KEY` / `CUSTOM_API_KEY`），`config.json` 仍不含密钥；`save()` 把 `patch.ai` 里的 `base_url` / `model` / `api_key` 一律落到目标档（带 `active_profile` 就落那一档），改 mimo 的地址不会牵连 DeepSeek 档，脱敏串（`•••••`）回填也被拦住
- `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 环境变量改为只作用于 DeepSeek 那一档，切到 MiMo 时不再把小米的地址冲成代理；存量单模型配置升级时自动归位——原地址不含 deepseek 就整包落到 `custom` 档并选中它，`config.json` 里的明文 Key 迁进对应 `.env` 键
- 设置页「模型」卡顶部新增档位 chip 行（未填 Key 的档标红并写「未填 Key」），点一下即切档：先把表单里没保存的地址/模型名落回旧档再切，切完成本、模型下拉与状态栏一起刷新；卡头标签、API Key 与模型名的说明文字跟着当前档改口径，`/api/settings` 的 `ai.profiles[]` 只带 `configured` 布尔、不带任何密钥
- 提示与诊断按档说话：`llm.stream()` 报错从写死的「填写 DeepSeek API Key」改为「『MiMo v2.6 Flash』还没填 API Key」，`/api/diagnose` 的 AI 项显示「档名 · 模型名」，`manager.status` 的 `ai` 段补 `profile` / `profile_name`；`provider_kind` 认得出 `api.xiaomimimo.com`，思考强度自动切成 auto/off/on 三档、请求参数走 `thinking.type`
- 牌价 `ai.price_*` 保持全局（MiMo 成本仍按 DeepSeek 牌价估算，要准需在 `config.json` 改），余额查询仍旧只对 DeepSeek 上游开放、非 DeepSeek 地址返回 `NOT_DEEPSEEK`
- README：`ai.model_mode` 与 `select_model_profile` 那节描述的是没实现的东西，替换为「模型配置（profile）」表格与真实行为；快速开始与 `.env` 说明按界面上的档名重写
- 新增 14 例覆盖默认档、切档镜像、按档取密钥、改档不串档、空 Key 不改值、脱敏回写、明文 Key 迁移、无效档名回落、自定义档、环境变量只作用 DeepSeek 档、`llm` 随档切换；全量 406 例通过

### 2026-10-26 · 链接卡片不再被当成文件，网址进历史

- 修复 `summarize_message_content` 的 appmsg 判据：微信 4.x 公众号文章链接卡片自带一个只装缩略图的 `<appattach>`（`cdnthumburl/md5/aeskey`），旧判据「appattach 有子节点」把链接卡片全标成「[文件] 标题」，`<url>` 分支永远走不到，网址整个丢失——AI 只能看到标题，回一句「我读不了里面的内容」。现在按真文件才有的字段（`<fileext>`、`<attachid>@cdn_…`、非零 `<totallen>`）和 appmsg 子类型 6 判文件，链接卡片标「[链接]」并把网址原样拼在摘要末尾
- 网址不做参数裁剪：实测 `mp.weixin.qq.com/s` 去掉 `chksm` 只留 `__biz/mid/idx/sn` 的精简版返回 38 字符空壳页，带全参数才有 1285 字符正文，因此只还原 XML 里的 `&amp;`，其余保持原样；`weixin://` 这类打不开的小程序地址不进摘要，仍标「[卡片]」
- 修复引用消息内层副本的原始 XML 泄漏：4.x 库 `refermsg.content` 开头带 `wxid_xxx: ` 发送者前缀，`parse_refermsg` 只按 `startswith("<")` 探测，导致引用链接卡片/图片时整段 XML 当纯文本入库。抽出 `_XML_HEAD_RE` 与摘要共用，前缀 + XML 声明的写法一并识别
- 提示词跟进：链接卡片带可访问网址时，不再告诉模型「打不开也读不了、没有工具可以解析」，改为提示可用 `fetch_page` 打开原文，读不到再直说。自动回复策略不变——link 仍只有合并转发聊天记录放行，普通链接不主动接话，只进历史上下文供模型引用
- 历史自愈：`add_history_messages` 的 REPLACE 条件加上「[链接] 」开头的行，旧库误标的「[文件] 标题」在下次启动回填时按同 id 从解密库重读原文升级（旧行里没有网址，无法就地修）
- 新增 6 例：真实链接/文件/小程序卡片摘要（缩略图 appattach 不误判、自闭合 `<url />` 不当网址）、引用被截断的链接卡片不泄漏 XML、回填升级误标行、带网址链接的提示词措辞；全量 392 例通过

### 2026-10-26 · 拍一拍消息支持

- 新增拍一拍识别：3.x hook 的纯文本（`"张三" 拍了拍你` / `你 拍了拍"李四"`）与 4.x 本地库的 `<sysmsg type="pat">` XML 都归一成「谁 拍了拍谁」，保留自定义后缀；4.x 按 `fromusername` / `pattedusername` 回查备注/昵称，本账号统一显示为「我」（自己拍的）或「你」（被拍）
- 拍一拍不再被当成普通系统通知丢弃或压成「[系统消息]」：wcferry 放行 type=10000 的拍一拍，weixin4 实时与历史回填两条链路都分类为 `msg_type=pat`，webhook 入站同样支持（保留网关给的发送者名、剥掉正文发送者前缀）；消息 `meta.pat` 带 from/to 的 wxid、昵称、后缀与 self 标记，旧 system 行回填时原地升级为可读文案，不产生重复行
- 自动回复：新增 `auto.pat_reply`（默认开，设置页「被拍一拍时回复」）；私聊被拍可回一句，群聊只接「拍自己」的，拍别人不掺和，且拍自己时不再要求 @；提示词按轻互动处理，允许只输出 `[SKIP]`。撤回回溯与表情习惯统计排除 pat，避免拍一拍被误标成撤回原件或拉偏表情比例
- 控制台：拍一拍以居中浅色提示展示（不再占左右气泡），消息类型标签为「拍一拍」；`auto.pat_reply` 可在「设置 → 自动回复策略」开关
- 新增 `tests/test_pat.py`（32 例）：纯文本/XML 解析与后缀、wxid 解析、self/other 判定、三套驱动接入、提示词、群聊回复策略、关闭开关、撤回排除、旧 system 行回填升级与歧义保护；全量 386 例通过
### 2026-10-26 · 串子模式接入 searchanythingfun + 表情/标签锁定

- 串子模式（`ai.chuanzi_mode`）：自动回复按串子方式接话，首次表情检索先查 searchanythingfun 台词库；每轮最多 1 次请求、只下载/入库/使用 1 张，命中 429/配额/网络失败自动回落本地。秒回开启时正文先发，站点台词不参与正文
- 站内入库：`emojis.source='searchanythingfun'`，自动打「串子站」+ 作品名 + 字幕标签，描述保存台词与标题；默认 `locked=1`；「串子站」标签首次入库自动锁名，用户主动解锁后不再锁回
- 表情墙锁定：新增标签锁（防「整理标签」改名/合并）与表情锁（禁改标签/描述、AI 重打标、单删/批删；发送/导出不受影响）；批量删除/打标自动跳过锁定项并提示数量
- 接口：`POST /api/emojis/{id}/lock`、`GET/POST /api/emoji/tag-locks`；编辑/重打标/删除锁定表情返回 409；`batch-delete` 返回 `skipped_locked`；`consolidate-tags` 返回 `locked_tags_skipped`
- 与「仅 AI 娘表情包」同开时，串子站入库表情优先且不受角色名标签限制；本地其余表情仍过滤
- 站点请求只出现在自动回复链路：控制台智能体不传单轮状态，`find_emoji_online` 在控制台仍是原联网找图；关掉串子模式后工具文案、选图与过滤行为全部回到改动前
- 收尾修复：后台自动打标判定「非表情包」时，锁定行不再走 `reject_emoji`+清缓存（`db.reject_emoji` 对锁定行返回 None，而缓存清理只看别的账号，会把锁定行的图删掉只剩库里的空行）；`find_emoji_online` 关闭态描述里被隐式拼接吃掉的一个空格已补回；表情墙两处「已跳过 N 张锁定表情」提示统一为同一 toast 样式
- 测试：新增 `tests/test_emoji_locks.py`（5 例）、`tests/test_chuanzi.py`（8 例）、`tests/test_chuanzi_tools.py`（9 例，含关闭态文案逐字断言），扩展 `tests/test_reply_emoji.py`（25 例）、`tests/test_emoji_wall.py`（57 例）；本次收尾另加 6 例——锁定表情在「AI 判定非表情包」链路存活且缓存不被删 3 例、锁定表情仍可发送/导出/代取 3 例；全量 399 例通过

### 2026-10-26 · 跨对话沟通（转告代发 / 主动串门 / 转发记录内目标）

- 自动回复不再只能回当前会话：新增「跨对话沟通」能力（`cross` 配置段，默认开启，设置页「AI」卡片可分别关转告/串门或关总开关）。好友在聊天里明确要求「帮我给某某发条消息 / 转告某某 / 跟某某说一声」时，AI 用新增的跨会话 `send_wechat_message` 把内容真的发到对方指定的人或群，随后在当前会话回一句确认；收件人按昵称/备注/群名解析（精确 → 唯一子串 → 驱动通讯录兜底），找不到或多义时直接返回错误让 AI 反问，绝不猜人发错
- 转告延迟发送：工具只登记（一轮最多 3 条、同目标同内容去重），本轮正文与表情都发完后再由后台任务按发送节流顺序真发；单条失败只记 `cross` 事件与日志，不影响已发出的回复和其他转告。`[SKIP]` / 无正文的分支也会把已登记的转告发出去
- 主动串门：号主本人（非服务回声）在其他私聊里发消息时，AI 能看到该会话最近上下文，用一次轻量模型调用（`effort=off`、≤40 字）决定要不要补一句话，`[SKIP]`/空输出即静默。限额：同一会话冷却 `cross.visit_gap_sec`（默认 600s）、全账号每小时最多 `cross.visit_max_per_hour`（默认 6 次）、每日总量与转告共用 `cross.daily_limit`；群聊、文件传输助手/内部会话、关闭 AI 的会话、黑名单/白名单外、非工作时段，以及这条消息之前已处于「人工接管暂停」的会话都不串门
- 合并转发聊天记录里的人可直接转告：记录分支把 `<sourcename>` 发言人名单注入提示词，AI 可按名字发给被点名的那个人；没被要求转告时保持「别向他们喊话」的原约束
- 风控与留痕：跨对话消息照常以 `agent` 来源入库、带 `meta.cross = {kind, requestor}`，受全局发送节流；每次成功/失败写 `cross` 事件；提示词要求只按对方明确点名的收件人发、不转发链接/广告/拉群、钱/验证码/身份隐私一律拒绝、不透露其他会话存在与内容
- 配置与数据：`config.DEFAULTS` 新增 `cross` 段（`enabled` / `relay` / `visit` / `daily_limit` / `visit_gap_sec` / `visit_max_per_hour` / `relay_max_chars`）；每日计数存 kv `cross:<账号>:<日期>`，无新表、无迁移
- 修复两处既有测试抖动（本机环境相关）：`db.list_messages` 在同一时间戳（Windows 时钟精度下连发消息常见）时用 `rowid` 兜底排序，拆分回复等测试不再受 SQLite tie 排序影响；`test_default_ai_takeover` 隔离本机 `data/config.json` 的 `default_ai_on`
- 新增 `tests/test_cross_chat.py`（48 例）覆盖策略、收件人解析、代发/延迟队列、串门决策与限额、提示词、`_auto_reply` 集成与旧配置回归；设计文档 `docs/superpowers/specs/2026-10-26-cross-chat-communication-design.md`；全量 395 例通过（另 1 例 `test_whale_widget` 因 vendor submodule 未初始化、本机无法访问 GitHub 而失败，与本次改动无关）

### 2026-10-26 · 余额与每轮话费接入会话 / 智能体

- 每轮成本归因：LLM 的 usage 事件在记账之外回传本次调用成本（`app/ai/llm.py`），`Agent` 跨工具轮次汇总为这一轮的总成本（`app/ai/agent.py`）；`usage.turn_usage_meta()` 统一把成本与输入/输出 tokens 转成落库 `meta`，没收到真实 usage 时不伪造 0 成本
- 微信自动回复把本轮成本写进最后一条 AI 消息的 `meta`（`app/wechat/manager.py`）；控制台对话写进 assistant 回合的 `meta`（`app/main.py`），刷新页面、切换历史对话后仍能看到
- 会话页：线程头新增「余额 ¥x · 上轮 ¥y」，每条 AI 回复的时间旁显示本轮 `¥y`；智能体页：头部同样显示余额与本对话上轮成本，每条回复底部显示「本轮消耗 ¥y」。余额复用 `/dsh-whale/balance.json`（25 秒服务端缓存），进入这两页时拉取、之后 60 秒轮询，点余额可强制刷新；无 Key / 非 DeepSeek 显示「—」并悬停说明原因
- 控制台智能体新增两个可调用工具：`get_whale_balance`（余额、赠金/充值、今日已用）与 `get_last_turn_cost`（当前对话上一轮成本与输入/输出 tokens）。余额查询抽到 `app/whale.py`，挂件路由与工具共用同一份 25 秒缓存；工具只在控制台注册，微信自动回复不暴露余额
- 口径：只统计主回复 / 主对话的模型调用，不含后台记忆提取与补发表情时的独立挑选；成本按 `ai.price_*` 牌价估算，与挂件账本同源
- 新增 `tests/test_turn_cost.py`（6 例）：成本换算与账本落账、Agent 跨工具轮汇总、无 usage 时不标记、meta 形状、控制台回合落库、自动回复消息 `meta` 落库；`test_whale_widget.py` 补余额成功路径字段形状一例；`test_billing_tools.py`（6 例）覆盖工具注册范围、余额映射/错误、上轮成本只读当前对话、无上一轮与不拿更早一轮冒充；全量 354 例通过

### 2026-10-26 · 表情墙批量导入导出（带标签）

- 表情墙支持整库或选中表情导出为 zip：图片之外附带根目录 `manifest.json`，逐图记录 `file / md5 / tags / description / width / height`，换机或换账号导入后标签、描述一并还原。导出走临时文件 + `FileResponse`，全库数百 MB 也不会整包进内存；取不到缓存且无法现抓的图片跳过，不阻断其余导出
- 新增「导入表情包」：可一次选 zip 压缩包和一批散图。zip 按清单还原标签；没有清单的旧 zip 和散图按图片处理，散图导入前填一组统一标签（可留空）。按内容 md5 去重：新表情以 `local://<md5>` 入库并写进表情缓存，同 md5 已存在时标签取并集（原标签在前、最多 8 个），描述只在原来为空时补写，不覆盖现有内容
- 导入限额与安全：单文件 32MB、单次 5000 个文件 / 5000 个压缩包条目、解压总量 1GB（跨多个上传文件累计，请求体另有 1GB 上限）；只认图片魔数，损坏/非图片/超限条目跳过；文件名不参与落盘路径（一律按内容 md5 存缓存），压缩包不会解压到磁盘
- 控制台：工具栏新增「导入表情包」「导出全部」，多选栏「打包下载」改为「导出选中」；导入/导出按钮带 loading 态，完成后 toast 返回新增/更新/跳过数量
- 接口：`POST /api/emojis/batch-zip` 请求体扩展为 `{ids:[...]}` 或 `{all:true}`，响应头带 `X-Emoji-Packed / X-Emoji-Skipped`；新增 `POST /api/emojis/import`（multipart：`files` + 可选 `tags`）
- 新增 `tests/test_emoji_wall.py` 导入导出用例（23 例）：manifest 标签、整库/选中范围、导出→导入还原、同 md5 合并、散图统一标签、旧 zip 无清单、坏包/非图片/超限、上传数量与共享预算、荒谬尺寸、路径穿越、流式清理、跨账号隔离

### 2026-10-26 · weixin4 发送改为「搜索栏搜昵称 → 校验目标会话 → 发送」

- 发送链路重做：不再依赖按旧窗口比例写死的坐标点击（实测当前窗口下「点搜索框」实际会落到会话列表、把消息留在当前/错误会话里，例如文件传输助手）。现在走 wechatauto 的 UIA 引擎：在微信左上角搜索栏搜索对方昵称/备注 → 打开会话 → **校验当前会话确实等于目标** → 把正文粘贴进输入框 → 回车；校验不过直接报错、不发送
- 发送前回读输入框内容（库自带相似度校验），`Ctrl+V` 没落进输入框时不回空消息；目标会话打不开或打错都抛可读错误，自动回复会记录「回复发送失败」事件而不是静默发错人
- 所有 UIA 操作串行在驱动内单独线程（uiautomation COM 有线程亲和性；自动回复与控制台发送不再互相抢窗口），启动后后台预热 UIA 引擎（首次需扫描 Weixin.dll 打开无障碍树，数秒到数十秒，不阻塞启动）
- 表情/文件发送同样先搜索并校验目标会话再粘贴，避免附件发错会话

### 2026-10-26 · 同名好友/群友改按 username（wxid）识别

- 群聊上下文里的发送者标注从「昵称」改为「昵称(wxid)」：自动回复提示词（`_build_prompt` 历史/当前消息前缀、撤回标记）与 `get_chat_history` 工具输出统一走 `base.sender_label`，同名群友靠 wxid 区分；系统提示词同步说明 wxid 是唯一标识、回复里照常只称呼昵称。wcferry 从正文解析出的「id」与 weixin4 旧数据的数值行 id 起不到区分作用，这类不可信 id 不标注（`base.unique_sender_id` 判定），宁缺毋滥
- weixin4 驱动消息的 `sender_id` 统一存 wxid（`sender_username`），拿不到才退数值 id；实时与回填两条链路一致，群成员表情习惯统计（`sender_emoji_habit` 按 sender_id 精确匹配）从此不再受两套 id 并存影响（注意：改动前入库的旧消息仍是数值 id，同一人的存量消息与新消息会算作两个发送者）
- 朋友圈工具链以 username 为准：`browse_wechat_moments` 每条动态新增 `from_id`（发布者 username）；`like_wechat_moment` / `comment_wechat_moment` 的 `publisher` 优先按 username 匹配，只传昵称且存在多个同名好友时拒绝操作并提示用 from_id，防止点赞/评论到错误的人
- 测试隔离修复：`test_reply_emoji` / `test_reply_humanization` 此前未隔离 `config.load`，开发机真实配置开着 `wiki_emoji_only` 时工具层会拦下测试表情、7 个时序用例误挂；fixture 里 monkeypatch `config.load` 返回空配置
- 新增 `tests/test_sender_identity.py`（11 例）：唯一 id 判定、展示名格式、weixin4 sender_id 存 wxid、群聊提示词前缀、get_history 带 wxid、朋友圈 from_id 返回/按 username 定位/同名昵称拒绝

### 2026-10-26 · 打标附带图鉴角色参考立绘

- 打标视觉模型此前只拿到文字名册，没见过角色长相，认不出图鉴角色。现在图集可用且打标带图时，随请求附带 30 张角色主立绘缩略图（`moe_wiki.character_refs` + `vision.thumb_data_url`，320px JPEG 约 413KB 总量，按 mtime 缓存复用），文字清单「第 N 张参考图=角色名（设定文案）」与附图严格对齐，并提示按发型发色/服饰/标志性元素对号入座——参考图与参考文案一次到位
- 纯上下文打标（无图）不附参考立绘，清单也不注入，保持图文对齐；Pillow 不可用时自动降级为不附参考图（requirements 补充声明 `Pillow>=10.0`）
- 新增测试：`test_emoji_qq_cache.py` 附图与降级两例、`test_moe_wiki.py` 参考清单对齐一例；全量 297 例通过

### 2026-10-25 · 仅 AI 娘表情包模式 + 全体 AI 娘人设预设

- 新增「仅 AI 娘表情包」开关（`ai.wiki_emoji_only`，默认关，设置页 AI 卡片）：开启后自动回复链路里 `search_emojis` 只返回 tags 带图鉴角色名的表情、`send_wechat_emoji` 对无角色名标签的表情硬拦截、「回复必带表情包」的补发兜底同样只在合格候选里挑；词表来自 ai-moe-wiki 图集非梗图图名（`moe_wiki.character_names`），打标链路本就注入图鉴名册，收集到的表情天然可能带角色名标签，杂梗图在两种来源下都发不出去
- 新增「全体 AI 娘人设预设」：`moe_wiki.build_persona_presets` 扫描图集 01-立绘人设 三个子目录（模型娘立绘 13 + 平台工具拟人 14 + DeepSeek 系 3，共 30 角色），说明取同名条目正文、合集条目「注」里含该名的分句（名字出现最早者胜出）或兜底文案；控制台「人格预设」卡新增「从图集更新 AI 娘预设」按钮（`POST /api/personas/sync_wiki`），生成/更新带 `source: "ai-moe-wiki"` 标记的 json，用户自建同名预设不会被覆盖，下拉里图集来源显示「·图集」后缀
- 修复《逐图详细说明.md》解析：第三部分全库索引原来被并进最后一个条目（103）的正文，认图名册会混入「目录结构」等索引噪音，现在遇到「第三部分」标题即截断
- 新增测试：`test_moe_wiki.py` 角色名词表/标签过滤/人设生成（13 例）、`test_wiki_emoji_mode.py` 模式开关下的搜索过滤与发送拦截（5 例）

### 2026-10-25 · 多人格预设保存与切换

- 新增「人格预设」：调好的人设可存成独立人格文件（`data/personas/<名称>.json`，内含 `persona_name` + `system_prompt`，可直接编辑、备份、分享），控制台「设置」页新增「人格预设」卡片——下拉选择已保存的人格后一键「应用到全局」（立即写入全局人设与人格名）或「填入当前账号」（填进「账号与通道」表单，点「保存账号」生效），支持把当前全局/账号人设另存为预设（重名覆盖）与删除
- `config.py` 新增 `list_personas / read_persona / save_persona / delete_persona` 四个文件操作函数（文件名自动去 `.json` 后缀，非法字符 `\ / : * ? " < > |`、空名与超 80 字符直接拒绝）；`main.py` 暴露 `GET/POST /api/personas`、`DELETE /api/personas/{name}`
- 设置页补上此前缺失的全局「人格名」输入框（`ai.persona_name` 一直存在但界面改不了，应用预设时人格名与人设一并填入），保存设置的补丁同步携带该字段
- 新增 `tests/test_persona_presets.py`（4 例）：保存/读取与落盘 json 格式、`.json` 后缀归一与重名覆盖、非法文件名拒绝、读取与删除的缺失处理

### 2026-10-25 · 表情包时序修正：先保证消息发出来再发表情包

- 修复「回复必带表情包」的发送顺序：模型调 `send_wechat_emoji` 原本在 `agent.run()` 期间就立即真发，而文字要等整轮结束后才发，对方往往先收到表情、后收到正文。现在自动回复链路里该工具只负责「选定」（`build_tools` 新增 `defer_emoji` 参数，传列表则登记不发送，其他调用点默认 `None` 行为不变），正文全部发出后再由 `_flush_deferred_emoji` 统一真发（每条带 0.8–1.6s 人类节奏间隔）；表情工具照常返回成功，原有的「漏发兜底」不会被重复触发
- 「只发表情不回文字」的场景（对方只发表情包、AI 选好表情后输出 `[SKIP]`）以及正文被净化清空的分支，同样会把选好的表情补发出去；超时兜底的「稍等」文案仍不带表情（沿用原决策）
- 提示词同步改为「send_wechat_emoji 选定即可，系统保证先发文字后发表情」，不再要求模型自己控制时机；控制台「智能体」页链路不受影响
- 测试：`test_reply_emoji.py` 工具发表情用例改为断言 `calls == ["text", "emoji"]` 的时序，新增「`[SKIP]` 时选定的表情仍发出、且不发文字」用例

### 2026-10-25 · 跨会话消息互吞修复 + 引用表情原图显示

- 修复群友反馈的「吞消息」：weixin4 的 `msg_id` 原来是「分片#local_id」，但微信 4.x 是**每个会话一张独立的 `Msg_<hash>` 表、`local_id` 每表从 1 起**——同一个分片库文件里不同会话必然撞 id。撞上后 `_seen` 实时去重直接把后来会话的消息丢掉（实测：22:22甲群先占用了 `message__message_0.db#11/#14/#15`，22:31–22:33 乙群的三条消息——含两条表情包——全部被吞，触发群友引用质问），就算绕过去 `add_message` 的 `INSERT OR REPLACE` 也会跨会话覆盖历史。现在 `msg_id` 改为「会话#分片#local_id」（`Weixin4Driver._msg_id`），`get_image` 的 id 解析抽成 `_split_msg_id` 兼容新旧两种格式；`add_history_messages` 本就按「会话+时间±2s+内容+发送者」兜底去重，格式切换不会造成回填重复
- 引用消息里被引用的动画表情现在显示原图而不是「[表情]」占位：4.x 引用消息内嵌的原消息 XML 是 HTML 转义且冒号被混淆成 `*#*`（`http*#*//`、`media*#*0_0`）的副本，`parse_refermsg` 先还原混淆再从中提取表情的 `cdnurl/md5/尺寸`，存进 `meta.refer.emoji`；控制台引用块直接经 `/api/emoji/proxy` 渲染原图（`app.js`，取不到图回落「[表情]」文本）
- AI 同步看见被引用的表情包：自动回复收到「引用表情」消息且开着识图时，按内嵌 cdnurl 取图（表情库缓存同 md5 直接命中）作为附图给模型，提示词相应提示「被引用的表情包已经作为附图」；此前只有引用图片（type 3）走 svrid 反查原图，引用表情只能看到「[表情]」三个字
- 新增测试：`test_content_summary.py` 引用表情还原（真实样本含双层转义与 `*#*` 混淆）、`test_weixin4_send.py` msg_id 跨会话不碰撞与三种 id 格式解析；`test_history_backfill.py` 的复合 id 断言更新为新格式

### 2026-10-25 · 默认全 AI 接管选项

- 新增 `auto.default_ai_on` 开关（默认关，控制台「设置 → 自动回复策略」里叫「默认全 AI 接管」）：开启后新出现的会话初始即为 AI 自动回复，不再需要逐个手动打开。`db.upsert_chat` / `upsert_chat_meta` 两处 INSERT 的 `ai_enabled` 从硬编码 `0` 改为跟随该开关（`db._new_chat_ai_enabled()`），收消息、白名单同步、按名查找三条建会话路径统一生效
- 只影响新建会话：已有会话的 AI 开关（含 v3 迁移时统一改为人工接管的存量会话）不被覆盖，存量会话可用会话页多选批量设置一次性交给 AI；群聊仍受「群里必须 @ 我才回复」约束，黑/白名单照常生效
- 新增 `tests/test_default_ai_takeover.py`（5 例）：默认人工接管、开启后新会话 AI 接管、meta 路径同样生效、已有会话开关不被覆盖、默认值存在

### 2026-10-25 · 接入 ai-moe-wiki 形象图鉴

- 新增 `app/ai/moe_wiki.py`：接入本地的 AI 娘化图集仓库 [ai-moe-wiki](https://gitee.com/PiggyWu981/ai-moe-wiki)（独立 git 仓库，B 站 UP 主 ZipZipPipe 绘制、CC NC 协议；默认路径 `../ai-moe-wiki`，`config.json` 的 `ai.moe_wiki_path` 可改，留空停用）。启动后扫描 7 个分类目录建索引，解析《逐图详细说明.md》第一/二部分的逐条说明（`**N. 标题**` + 正文，兼容 `> 注` 与第二部分 `- 文件：/- 注：` 条目），按归一化标题对齐到图片文件，说明文本按 mtime 缓存
- 注册自我形象：图集里的大肥鱼（DeepSeek-Pro 娘，蓝发鲸尾深蓝女仆装，与默认人设描述一致）及女仆装三视图、电脑壁纸识别为「自己」的图（`moe_wiki.SELF_HINTS`，小肥鱼是 V4.1 Flash 另一个角色不算）；主立绘已复制为 `data/self/avatar.jpg`，开启识图后每轮通过形象注入让模型直接看见自己
- 认图认角色：自动回复收到图片/表情包，或文本提到图集角色名（梗图标题不算，避免「涨价了」这类日常词误触发）时，注入全库「形象图鉴」名册（图名 + 一句话说明，≤3600 字符），对方发 ZipZipPipe 的 AI 娘图能对号入座
- 发图能力：新增智能体工具 `search_moe_wiki_images`（按角色/关键词检索图集）与 `send_moe_wiki_image`（挑图真实发送，weixin4 通道按图片消息发出），被问长相/立绘时可以把自己的图发过去；控制台「智能体」提示词同样注入自我形象块
- 表情打标接入图鉴：`tagger.tag_emoji` 的提示词在梗百科之外追加打标版图鉴名册（`moe_wiki.tag_block`，图集不可用时省略）——自动收集打标、批量 AI 打标、重新打标、联网找表情入库四条链路共用，收到 ZipZipPipe 的 AI 娘梗图能直接用图鉴名字打标（梗图图名往往就是梗的原文）；提示词拼装抽成 `tagger.build_tag_prompt` 便于测试
- 新增 `tests/test_moe_wiki.py`（8 例）：文档解析与文件对齐、自我块（梗图不算形象图）、名册截断、认图触发条件、检索排序、路径解析与降级、打标块内容与打标提示词注入/降级

### 2026-10-25 · 微信 4.x 数据目录误判导致 weixin4 启动失败修复

- 修复 weixin4 通道报「以下数据库未能取得密钥」启动失败：微信 4.x 跟随系统「文档」已知文件夹保存 `xwechat_files`（客户端配置里写的是 `MyDocument:`），开启 OneDrive 文档备份后该文件夹被重定向到 `C:\Users\<name>\OneDrive\文档`，而 `%USERPROFILE%\Documents` 下还残留一份过期副本。wechatauto 的自动探测只认后者，选中过期副本后库 salt 已变、缓存密钥一个都验不过。新增 `resolve_wx4_db_dir()`：先用 `SHGetFolderPathW(CSIDL_PERSONAL)` 解析「文档」真实路径，再把所有候选目录按 `.db` 最新修改时间比较，选微信真正在写的目录；驱动启动与「设置 → 账号与通道」的账号下拉都改用它，密钥缺失报错会带上实际数据目录，便于下次直接看出探测错位
- 修复微信 4.x 装在自定义目录（如 `D:\ruanjian\Weixin`）时「检测不到微信客户端」、`auto` 永远解析不到 weixin4：客户端检测现在还会读注册表 `Software\Tencent\Weixin|WeChat` 的 `InstallPath`、运行中进程的 exe、以及可见微信窗口的 exe；窗口来源只认 `Weixin.exe`/`WeChat.exe`，避免把其它 Qt 程序（如远程工具）误判成微信
- 双开时自动绑定「有可见微信窗口」的账号：后台账号也在写库，但前台 RPA 发送只能用可见窗口，之前按最近活跃选账号可能绑到后台账号导致发不出消息；新增 `resolve_wx4_account()`，优先选可见窗口对应的 wxid，找不到再交给库按最近活跃自选
- 新增 `tests/test_wx4_db_dir.py`：过期副本 vs 活跃目录选择、用户指定账号优先（含同名账号两份副本取较新）、无账号目录兜底、路径规整、可见窗口账号选择

### 2026-10-25 · 引用消息显示修复与引用图片识图

- 修复引用消息（引用回复）摘要被压坏：微信引用消息 XML 外层永远带一个空的 `<appattach />`，旧摘要逻辑把所有引用消息误判成「[文件] 回复文本」（引用图片时内层 XML 又让整条只剩「[图片]」，回复文本整个丢失）。`summarize_message_content` 现在优先识别 `<refermsg>`，压成「回复文本 + `[引用 发送者：原消息摘要]`」两行，被引用的文本/图片/语音/视频/文件/聊天记录各自递归走对应摘要；空的 `<appattach />` 不再算文件
- 引用图片可被看见：`Message.to_dict` 把 refermsg 的 `svrid`/原发送者/类型/摘要存进 `meta.refer`；weixin4 驱动新增 `get_image_by_server_id`（在近期图片行里把 svrid 换算回 local_id+分片再解密，覆盖最近 300 张）。自动回复遇到「引用图片 + 提问」时把被引用原图作为附图给视觉模型，控制台图片端点同样支持按 svrid 出图——之前 AI 对「@Deep肥鱼 这是什么」这类引用提问只能看到「[文件]」占位符
- 控制台聊天页引用消息富渲染：气泡内显示回复文本 + 引用块（被引用者名字 + 原消息摘要），引用的是图片时直接渲染缩略图（点击看原图），微信客户端同款样式
- 历史回填升级：启动回填时引用消息按同 id REPLACE 升级旧坏摘要（与聊天记录卡片同策略），重启服务后最近的历史引用消息自动修复
- `tests/test_content_summary.py` 新增引用消息样本（4.x HTML 转义与 3.x CDATA 两种内层格式）覆盖：回复文本保留、被引用内容分类摘要、meta.refer 结构、回填升级坏行

### 2026-10-25 · 回复必带表情包

- 新增 `ai.emoji_with_reply` 开关（默认开，控制台「AI 设置」里叫「回复必带表情包」）：自动回复提示词从「想发再发」改为「每次回复都必须至少配一个表情包」——先用 `search_emojis` 按场景挑，紧接着用 `send_wechat_emoji` 发出，再输出文字；对方发表情包要接话时同样必须带表情（只发表情时发完文本输出 [SKIP] 的约定不变）
- 代码级兜底：一轮回复发出后检查工具日志，若 `send_wechat_emoji` 没有成功发过（模型忘了或发送失败），用一次轻量 LLM 调用（effort=off）按最近对话 + 回复内容从表情库挑最贴切的一张自动补发（source=agent）；库里为空或没挑到贴切的就只回文字不硬凑，超时兜底的「稍等」文案不带表情
- 开启时自动回复 agent 轮次 3→4（检索 + 发送 + 正文，留一次重试空间），关闭时维持 3 轮不变；挑选输出防御式解析（取首个数字，0 或无效编号视为放弃）
- 控制台「智能体」对话页有独立提示词，不受此开关影响
- 新增 `tests/test_reply_emoji.py`：挑选输出解析、已发判定、漏发自动补发（工具已发不重复补、开关关闭不补）、提示词随开关切换

### 2026-10-25 · dsh-whale 余额小鲸鱼挂件

- 控制台右下角新增「余额小鲸鱼」挂件：实时显示 DeepSeek 账户余额，余额变化时泡泡提示 + 数字滚动，支持拖拽吸附、贴边翻转、按压音效、缩放（0.6–2.5）与设置菜单；上游 [MeteorNOX/DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)（v0.4.2，MIT）以 git submodule 形式挂在 `vendor/dsh-whale-widget`，前端文件原样伺服不改动（升级：`git submodule update --remote vendor/dsh-whale-widget`）
- 新增挂件宿主接口（`app/main.py`）：`GET /dsh-whale/widget.js|image.png|rua.gif|sound/*.mp3` 伺服 submodule 素材，`GET /dsh-whale/balance.json` 用已配置的 DeepSeek API Key 代理官方 `GET /user/balance`（25 秒缓存，`?refresh=1` 强制刷新，非 DeepSeek 上游时返回 `NOT_DEEPSEEK`），`GET/PUT /dsh-whale/size.json` 把缩放/音效等设置持久化到 `data/whale-size.json`（缺字段沿用旧值不洗成默认）；其余 DSH 专属接口（自定义角色、泡泡编辑、多模型余额等）不实现，挂件自动优雅降级
- 每轮对话消耗与今日已用：LLM 流式响应开启 `stream_options.include_usage`（仅 DeepSeek 上游），每次调用把真实 usage 按牌价换算成估算金额记进本地账本（`app/usage.py`，落盘 `data/whale-usage.json`）；挂件在每轮结束后弹「上一轮对话消耗 ¥x」泡泡、余额面板显示「今日已用」，用量面板可看按模型明细 / 近 7 天 / 全部记录，预警与今日预算提醒照常生效。牌价默认按 DeepSeek 现价（元/百万 tokens：缓存命中 0.2 / 未命中 2 / 输出 3），可在 `config.json` 的 `ai.price_*` 覆盖；口径是「Deep肥鱼 本机调用量估算」，账户里其他程序用同一个 Key 的消耗不计入
- `index.html` 给 `<main>` 加 `id="root"` 作为挂件启动自检的锚点（上游约定：容器内找到 textarea 才挂载），并引入 `/dsh-whale/widget.js`
- 已知边界：挂件接口不经过 access_token 鉴权（与页面/静态资源一致，仅返回余额数字与用量统计，无敏感数据）；素材许可随上游 PROVENANCE.md（代码 MIT，美术/音效 as-is），自用无碍，若仓库开源需替换或注明来源；用量面板里「添加自定义模型 / 余额校正」依赖的多模型接口未实现，显示加载失败属预期
- 新增 `tests/test_whale_widget.py`：素材伺服、余额接口形状（无 Key / 非 DeepSeek）、size.json 读写合并语义、用量账本记账与今日汇总、last-turn / usage-records / usage-settings 契约、未实现接口 404 降级

### 2026-10-25 · 记忆页多选批处理

- 控制台「记忆」页新增「多选」模式：工具栏「多选」进入后每条记忆左侧出现勾选框，点条目即选中/取消，支持「全选/反选」（对当前列表，含搜索结果）与「清空」，Esc 或「退出多选」随时离开；多选状态下单条的改/删按钮隐藏，点条目不再误触
- 批量操作三件套：批量删除（确认后一次请求完成，返回实际删除数）、批量设为重要/取消重要（全部已重要时按钮自动变为取消，逐条走编辑接口并显示进度）、导出 txt（把选中记忆连同类型/作用域/重要度/时间打包成文本文件下载，纯前端不落库）
- 新增控制台 API：`POST /api/accounts/{id}/memories/batch-delete`（仅限当前账号，与单条增删改同一鉴权约束）；数据层新增 `remove_memories` 批量删除（账号隔离，越权/不存在的 id 自动跳过）
- 多选状态跨搜索保留（关键词搜索不丢勾选），全量刷新时自动剔除已不存在的记忆；切换账号自动退出多选，避免把上一个账号的选中项带过去

### 2026-10-25 · 微信 4.x 表情/文件发送

- weixin4 驱动新增 `send_emoji` / `send_file`：复用前台 RPA 流程（激活窗口 → 搜索会话 → 聚焦输入框），最后一步把文件以 `CF_HDROP` 放进剪贴板粘贴发送，微信按文件类型自动识别为动画表情（GIF）/ 图片消息（jpg、png）/ 文件卡片（其余）
- 之前该通道发文本、发文件、发表情都只有文本可用，AI 聊天里发表情包会报「不支持发送表情」；现在智能体工具 `send_wechat_emoji`、`send_wechat_file` 与控制台表情面板「发送」在 4.x 通道均可用
- `send_text` 与新发送共用「搜索会话 + 输入框聚焦」逻辑；能力面板「发送文件/表情」置为可用
- 已知边界：发送会短暂占用前台窗口（与文本发送一致）；粘贴用的剪贴板内容会被覆盖

### 2026-10-25 · 表情墙多选批处理

- 表情包页新增「多选」模式：每个表情左上角出现勾选框，点卡片即选中/取消（再点一次退出），支持按当前筛选结果「全选/反选」与「清空」，Esc 或「退出多选」随时离开
- 批量操作三件套：批量删除（确认后一次请求完成，返回实际删除数）、批量 AI 打标（逐个调用打标接口并显示进度，打标前先经代理预热图片缓存，避免因缺缓存失败）、打包下载（服务端把选中表情打成一个 zip 返回，抓不到的跳过并在 `X-Emoji-Skipped` 与提示里注明）
- 新增控制台 API：`POST /api/emojis/batch-delete`、`POST /api/emojis/batch-zip`；数据层新增 `remove_emojis` 批量删除（账号隔离，越权/不存在的 id 自动跳过）
- 多选状态跨筛选切换保留，表情被其他入口删除后自动从选中集里剔除；批量删除后聊天收藏面板同步刷新

### 2026-10-25 · 朋友圈收发互动

- 新增朋友圈接收：weixin4 通道启动后后台轮询微信本地 `sns.db`，增量同步好友动态与「谁赞/评了我」互动通知入库，控制台实时推送
- 控制台新增「朋友圈」侧栏页：动态流（正文/九宫格图片/点赞/评论）浏览与搜索、互动通知列表、手动「立即同步」、未读角标
- 新增点赞与评论能力：控制台动态卡片可直接点赞、发评论（也支持回复指定评论）；操作走微信 4.x 界面自动化（UIA 控件定位 + 模板匹配），执行时会短暂占用前台窗口
- 智能体新增朋友圈工具：`browse_wechat_moments`（浏览动态）、`like_wechat_moment` / `comment_wechat_moment`（仅控制台智能体可用，自动回复不开放写操作）
- 新增控制台 API：`GET /api/moments`、`GET /api/moments/interactions`、`POST /api/moments/interactions/seen`、`POST /api/moments/sync`、`POST /api/moments/{feed_id}/like`、`POST /api/moments/{feed_id}/comment`、`GET /api/moments/{feed_id}/media/{index}`
- 新增配置项：`moments.poll_interval`（轮询间隔，默认 120 秒）、`moments.enabled`、`ai.moments_ai_enabled`（AI 朋友圈工具开关）
- 数据层新增 `moments`、`moment_interactions` 表
- 修复四处朋友圈链路缺陷：诊断模块注入的 `wechatauto` 空壳 stub 会挡住完整导入（报 `cannot import name 'WeChat' (unknown location)`），现在真包可导入时不再注入，朋友圈侧再撞上也会自愈清模块；`WxResponse` 的成功状态字面量是中文「成功」而非英文 `success`，此前实际成功的点赞/评论会被误判为失败；后台服务进程抢不到前台（Windows 前台锁静默拒绝 `SetForegroundWindow`），互动前改用 `AttachThreadInput` 强抢并还原最小化窗口；UIA 跑在 asyncio 工作线程上缺 COM 初始化（`尚未调用 CoInitialize`，UIA 全部静默失败），点赞/评论入口补 `CoInitializeEx`
- 已知边界：发表朋友圈暂不支持（微信 4.x 发表页为自绘界面，无法可靠自动化）；朋友圈图片仅在本机缓存或 CDN 链接仍有效时可查看；点赞/评论需要桌面已解锁且微信已登录

### 2026-10-25 · QQ 表情缓存导入

- 表情包页新增「从QQ缓存导入」按钮：扫描本机 QQNT 缓存（`Documents/Tencent Files/<QQ号>/nt_qq/nt_data`）的 `Emoji/personal_emoji`、`Emoji/emoji-recv/<月份>`、`Emoji/marketface`、`Emoji/BaseEmojiSyastems`（内置小黄脸）与 `Pic/<月份>` 目录，按文件头识别类型、按内容 md5 去重后导入当前账号表情库
- Ori 原图优先，Thumb 补缺：Thumb 文件名带 `_0/_720` 尺寸后缀，去掉后缀与 Ori 同名的跳过，Ori 缺失的才收（多变体留体积最大的）；marketface 原图是腾讯私有格式，可用的只有 `_aio.png` 预览；内置小黄脸 apng 动图优先于静态 png，同 stem 的 lottie JSON 不会顶掉图片；OriTemp/ThumbTemp 临时目录不碰
- 导入的表情以 `local://<md5>` 占位链接入库，`fetch_emoji` 在 md5 命中本地缓存时先于 URL 校验直接返回缓存文件——表情墙展示、抽屉发送、AI 检索发送全链路照常复用
- 表情列表接口 `GET /api/emojis` 支持分页：默认每页 200（上限 5000），可带 `offset` 翻页；表情墙首屏只加载一页，底部「加载更多」按钮按页增量拉取，搜索/筛选在已加载范围内生效
- AI 打标新增「非表情包拒收」：打标提示词要求模型判断图片是否真是表情包，普通照片/截图/二维码等输出 `not_sticker`，此时删除该表情并按内容 md5 记入 `emoji_rejects` 拒收名单（无其他账号引用时同步清掉缓存文件）；QQ 缓存导入按文件名（QQNT 即 md5）跳过名单内的文件，不再重复导入和重复打标；单条/批量打标与自动打标链路均生效
- 批量 AI 打标支持并发与中止：6 路并发 worker 池，运行中按钮变「⏹ 停止」可随时中止（在途请求收尾后停）；新增「AI打标未打标」一键按钮，无需手动多选即可给全部未打标表情打标
- 标签收敛防发散：AI 打标注入库内已有标签词表（按频次取前 120），要求优先原样复用、不造同义变体；新增「整理标签」按钮（`POST /api/emojis/consolidate-tags`）让 LLM 把存量同义标签归并成规范写法并重写 tags 列，规范词限定词表内、专名不并进泛化词；标签 chips 只展示前 100 个，可一键展开全部
- 表情检索 `search_emojis` 支持多关键词：query 按空格/顿号/逗号拆词，任一关键词命中标签或描述即返回（修复整串匹配导致「鲸鱼、无语、晚安」这类查询 0 命中的问题），命中关键词越多排越前；检索为空时工具返回换词重试与联网补库（find_emoji_online）的引导

### 2026-10-24 · 自我形象认知

- 全局人设追加形象描述：蓝色小鲸鱼娘外观（参考社区「大肥鱼」设定与萌娘百科 DeepSeek娘 条目），被问到长相/头像时能自然回答
- 新增形象图注入：把头像图放到 `data/self/avatar.png` 后，开着识图时微信自动回复与控制台对话每轮都会把形象图附给模型，让它真正"看见"自己（`app/ai/vision.py` 的 `self_image_messages`）
- 形象图支持按账号区分：`data/self/<账号id>/avatar.png` 优先，共享 `data/self/avatar.png` 兜底，多账号各用各的形象
- 新增智能体工具 `look_at_self`：按需看图获取外观细节描述，人设与注入之外的第二条认知通道

### 2026-10-24 · 表情包管理与标签墙

- AI 新增联网找表情能力：智能体工具 `find_emoji_online` 联网搜图（Bing 图片搜索优先，斗图啦/发表情兜底）→ 下载校验（魔数 + 最小体积，按图床带 Referer 防盗链）→ 入库（内容 md5 去重）→ 结合聊天上下文自动打标，随后可用 `send_wechat_emoji` 发送
- 聊天面板新增「标签墙」tab，可按标签浏览表情包
- 新增表情详情抽屉：改标签、发送、重打标、删除
- 新增表情包预览墙独立页
- 新增 AI 重打标接口 `POST /api/emojis/{id}/retag`
- 新增手动编辑标签接口 `PUT /api/emojis/{id}`
- 新增标签规范化与覆盖写库函数
- 新增表情包自动收集、打标和更新检查功能
- 自动回复增加能力边界声明与密钥泄露/越权调用防护
- 新增拟人化表情回复、表情包预览墙的设计文档与实现计划

#### 缺陷修复

- 消除迁移代码中的 SQL 拼接，改为参数化查询与字面语句

### 2026-10-23 · 多账号能力补全与控制台重构

#### 新功能

- 为多账号支持长期记忆和人设管理功能
- 新增表情包自动回复功能
- 新增联网能力和服务关闭功能
- 新增动画表情收发与收藏功能
- AI 对话新增识图功能，支持图片发送与识别
- 重构智能体控制台，支持思考过程展示、历史会话与模型切换
- 新增会话多选批量设置 AI 模式功能
- 控制台窄屏适配
- 新增 weixin4 驱动，支持微信 4.x 客户端

#### 变更

- 移除模拟通道相关代码，统一会话可见性逻辑
- 移除 Rust 版源码、文档与验收脚本，只保留 Python 版

#### 其他

- 更新 `run.bat` 的安装提示和 pip 安装命令

### 2026-10-22 · 运行看板与品牌重塑

#### 新功能

- 完成运行看板端到端验收、消息去重与配套文档
- 微信 4.x 支持后台文本收发，修复消息解码问题

#### 变更

- 项目品牌名从「深飞鱼」改为「Deep肥鱼」，并重构模型配置系统

#### 缺陷修复

- 终审收口：校准句柄、轮询兜底、文档与去重收紧

#### 文档

- 新增运行看板 GUI 实现计划（三栏运维台）

### 2026-10-21 · Rust 重写与微信 4.x 直连

#### 新功能

- 完成 Rust 重写：多账号控制台 + 微信 4.x 直连驱动，实测解密 19 个数据库 / 19 个真实会话
- Rust 移植微信 4.x 密钥提取与数据库读链路，本机实测通过
- df-cli 新增坐标校准命令，修复 df-cli 分支并补齐自检工具

#### 缺陷修复

- 微信 4.x 发送链路开启 DPI 感知、前台校验与越界保护，真机发消息跑通

#### 文档

- Rust 重写与微信 4.x 直连适配设计文档
- 微信 4.x 逆向实测报告与 NOTICE 署名

[1.0.0]: https://github.com/PiggyWu981/deepfeiyu/releases/tag/v1.0.0
