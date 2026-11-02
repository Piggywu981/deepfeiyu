# Deep肥鱼 × DeepSeek Harness 插件

在 DeepSeek Harness 里运行与操作 **Deep肥鱼微信智能体**（本仓库的 Python 控制台）：

- Harness 左侧栏新增「Deep肥鱼」图标，中央面板提供控制台状态、启停、账号切换、会话列表、消息记录与发送；
- Harness「设置 → Deep肥鱼」新增依赖环境页：查看控制台各可选功能的 Python 依赖状态并一键安装，安装过程显示实时日志；
- 同一设置页还有「关联 DSH 插件」：可一键安装上游的桌宠（`dsh-dafeiyu`）与心情（`dsh-feiyu-mood`）DSH 插件（npm 最新版，白名单 + 二次确认；它们由 Harness 会话驱动，与控制台里的同名功能是两套）；
- 「打开完整控制台」会在 Harness 右侧栏的官方浏览器标签页里打开完整控制台（`http://127.0.0.1:8765`）；
- 给 Harness 智能体 8 个 `deepfeiyu_*` 工具，可在服务端身份闸门、禁发名单与节流约束下读取会话、发送消息。

## 组成

| 文件 | 作用 |
|---|---|
| `index.js` | Host 入口：配置规范化、控制台监督、资源生命周期 |
| `lib/ops.js` | 控制台 HTTP 操作层（面板路由与工具共用） |
| `lib/supervisor.js` | 控制台进程监督（拉起/健康轮询/停止） |
| `lib/approval.js` | Agent 发送审批（跟随 Harness 权限模式） |
| `lib/tools.js` | 8 个 `deepfeiyu_*` 工具 |
| `lib/route.js` | 面板数据路由：`POST /deepfeiyu/<endpoint>`（Connection 鉴权），含 `deps.*` 与 `dshPlugins.*` |
| `lib/dsh-plugins.js` | 「关联 DSH 插件」白名单安装器：只调 Host 的 `pluginManager.installBundle`，包名与选项写死 |
| `client.js` | Client 半：`sidebar.panellist` 图标 + `main` 中央面板 + `settings.section` 设置页 |

无构建步骤、无 npm 依赖；仅使用 DSH 提供的 Cordis 服务与 Node 内建模块。

## 安装

1. 先在项目根运行一次 `run.bat`（或等价命令）完成 `.venv` 与依赖安装；插件不会替你下载依赖。
2. 在 DSH 里安装本地 bundle（绝对路径指向本目录）：
   - 插件管理页选择「安装本地目录」，或
   - 让智能体调用 `plugin_manager`：`action=install_bundle`，`target=<仓库根>/dsh-plugin`。
3. 安装结果以 `application` 字段为准；首次安装后刷新页面以加载 Client 模块。
4. 替换已安装的插件包后需要一次插件重载（在插件页把「Deep肥鱼」关一次再打开，或重启 Harness）才会加载新的 Host JS；只刷新页面只更新 Client 半。

## 配置

配置写在 profile 的 `cordis.patch.yml` 里本插件的行上（`id: deepfeiyu`），例如：

```yaml
- id: deepfeiyu
  name: '@local/deepfeiyu-dsh'
  config:
    mode: auto
    autostart: true
    host: 127.0.0.1
    port: 8765
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `projectDir` | 插件目录的上一级 | Deep肥鱼仓库根；用于定位 `.venv`、`data/config.json` |
| `pythonPath` | `<projectDir>/.venv/Scripts/python.exe`（非 Windows 为 `.venv/bin/python`） | 控制台解释器；不存在时面板/工具给出可读错误 |
| `mode` | `auto` | `auto` 自动拉起并监管；`attach` 只探测已运行实例，不拉起 |
| `autostart` | `true` | 仅 `auto`：插件激活时若控制台未运行则拉起 |
| `host` / `port` | `127.0.0.1` / `8765` | 控制台监听地址 |
| `accessToken` | 空 | 控制台访问令牌；空则读环境变量 `DEEPFEIYU_ACCESS_TOKEN`，再读 `data/config.json` 的 `server.access_token` |
| `requestTimeoutMs` | `5000` | 普通请求超时 |
| `sendTimeoutMs` | `60000` | 发送超时（服务端节流可能排队） |
| `panelTitle` | `Deep肥鱼` | 面板标题 |

## 设置页：依赖环境

在 Harness「设置 → Deep肥鱼」中列出控制台六个可选功能（core / weixin4 / moments / pet / wcferry / bili）的 Python 依赖状态：

- 每行显示状态徽标（已安装 / 未安装 / 缺少 N 项 / 不支持当前系统 / 安装中）、说明与缺失项；
- 点「一键安装」（已装则「重新安装」）后，页面内的终端窗口每 800ms 拉取增量日志，完成或失败会显示结论；已安装或失败过的功能可「查看日志」；
- 控制台未运行时页面直接显示原因与「启动控制台」按钮，不静默失败。

Host 侧仅新增三个同源代理端点：`POST /deepfeiyu/deps.state`、`deps.install`、`deps.log`，转发到控制台的 `/api/deps/*`。安装仍由控制台执行：本机来源限制、访问令牌校验与固定的依赖白名单命令都不变，插件不新增任何 shell 执行能力。安装完成后按页面提示重启控制台。

## 设置页：关联 DSH 插件

桌宠、心情与余额小鲸鱼的**上游**本来就是 DSH 插件，设置页可以把它们装进当前 Harness profile：

| 入口 | npm 包 | 说明 |
|---|---|---|
| 桌宠（Harness 会话版） | `dsh-dafeiyu` | 由 Harness 会话事件驱动，自带原生 helper 与素材（解包约 242MB）；与控制台里由微信状态驱动的桌宠是两套 |
| 心情（Harness 会话版） | `dsh-feiyu-mood` | 每轮 Harness 会话后判断心情，挂饰与设置都在 DSH 侧；不读微信，也不注入控制台心情 |
| 余额小鲸鱼（Harness 版） | `dsh-whale-widget` | Harness 右下角的 DeepSeek 余额挂件（今日用量、峰谷、气泡与音效，约 7MB）；与控制台里的余额挂件是两套。代码 MIT，素材/图标按上游「as-is 仅用于运行本插件」分发 |

- 安装的是 npm **最新版**（Host 传包名给 `pluginManager.installBundle`，`saveExact` 把实际版本写回 profile）；已有版本时不自动"卸载再装"，更新请到 DSH 自己的插件页；
- 前端只能传白名单 `id`，包名与安装选项写在 `lib/dsh-plugins.js`；点按钮前会弹一次确认，说明会执行第三方代码；
- 安装是 Host 进程里的后台任务，设置页轮询进度并显示 `application` 结论（`applied` 刷新页面生效，`restart-required` 需重启 Harness）；
- 只依赖 Host 的 `pluginManager` 服务：服务缺失时该区块显示"没有插件管理服务"，面板其余功能不受影响。

## 融合：共享心情与桌宠

融合让 Harness 与微信共享**同一份心情、同一只桌宠**（状态源在控制台），而不是再开第二个独立插件：

- 设置页新增「心情与桌宠（融合）」：绑定一个微信账号、三个开关、当前心情与双源时间线；
- 右下角心情挂饰（`shell.overlay`）只读共享状态，20s 轮询，点开看最近 5 条双源事件；
- Host 监听 `session/event`（`user/message`、`assistant/message`、`tool/call`、`tool/result`、`turn/end`）：
  - 回合结束后用 DSH 默认模型判断心情，结果写回控制台 `POST /api/dsh/mood-event`；限流：同账号 ≥90s、每日最多 40 次；
  - 工具/回合事件经 3s 防抖后写 `POST /api/dsh/pet-event`，驱动控制台那只桌宠；
  - 通过 `systemPrompt.context`（`deepfeiyu.mood`，60s 刷新）把共享心情注入 Harness 提示词。
- 三个开关：DSH 判断心情 / 宠物反映 Harness / 允许 Harness 来历进微信（默认开）。
- 隐私：Harness 原文**不出 Host**，只传判断结果与 ≤60 字理由；微信来源的理由不进 Harness；群聊里的 Harness 来历也降级为"只说心情"。
- 控制台停机时判断与宠物事件直接丢弃、记日志，不阻塞 Harness 对话；插件卸载会清理监听、定时器与提示词上下文。
- 注意：「设置页 → 关联 DSH 插件」里的 `dsh-dafeiyu` / `dsh-feiyu-mood` 是**独立版**，不与控制台共享状态；融合模式下不建议同时开启，否则会出现两套心情/两只宠物。

## 安全

- **不削弱任何服务端闸门**：所有发送仍走 Python 控制台的既有 `send_text`（含驱动在线、绑定身份确认、`send.forbidden_wxids` 禁发名单、写操作本机限制、访问令牌校验）。
- **Agent 发送跟随 Harness 权限模式**：会话审批策略为 `ask`（逐步审批 / Auto 自动审批）时，每次发送先走 `ctx.approval.request`，仅 `allowed-once` 才发送；策略为 `never`（完全批准 / headless）时不弹审批直接发送。无法判定时 fail closed 为 `ask`。
- **agent 发送走节流队列**：工具调用以 `source="dsh"` 走控制台既有的发送节流（全局最小间隔 + 会话冷却），比人工发送更保守；面板发送等价人工点击（`source="human"`）。
- **令牌不外泄**：令牌只在 Host 进程内用于调用 `127.0.0.1`，不写日志、不返回给 Client、不进入工具结果。
- **面板数据路由有鉴权**：`/deepfeiyu/*` 先调用 `ctx.connection.requestRejection` 复用 DSH 的浏览器 Cookie 与 Host/Origin 校验，未通过直接 401/403。
- **第三方插件安装是显式用户动作**：`dshPlugins.*` 只操作本机 Harness profile，白名单固定为 `dsh-dafeiyu` / `dsh-feiyu-mood`，不接受任意 spec；Client 点击前二次确认。它等价于用户在 DSH 插件页手动安装，不改变 DSH 自身的版本兼容与构建脚本审批逻辑；Agent 工具仍只能走 `plugin_manager`（需要 danger-full-access 或审批）。
- **融合不搬运原文**：`fusion.*` 只把已判断好的心情结果与 ≤60 字理由写回控制台；Harness 对话原文只留在 Host 内存，用于一次判断后即丢弃；控制台注入 Harness 的只有共享心情数值与来源为 DSH 的短理由。
- 聊天内容会作为工具结果进入模型上下文（这是本插件被要求的用途）；插件自身不把聊天内容写入日志或文件。

## 工具清单

| 工具 | 读/写 | 说明 |
|---|---|---|
| `deepfeiyu_status` | 读 | 控制台阶段/端口/版本/可达性 |
| `deepfeiyu_accounts` | 读 + 受控写 | 列账号；`action=activate` 切换当前账号 |
| `deepfeiyu_chats` | 读 | 会话列表（可搜索、限条数） |
| `deepfeiyu_messages` | 读 | 某会话最近消息 |
| `deepfeiyu_send` | 写 | 发送文本（受审批与全部服务端闸门） |
| `deepfeiyu_stats` | 读 | 今日统计与 token 用量 |
| `deepfeiyu_capabilities` | 读 | 技能/插件/MCP 状态 |
| `deepfeiyu_console` | 写 | `start` / `stop` / `restart` / `status` |

## 故障排查

| 现象 | 处理 |
|---|---|
| 面板显示「未找到 Python」 | 先运行 `run.bat` 初始化 `.venv`；或用配置 `pythonPath` 指定解释器 |
| 面板显示连接失败 | 端口是否被占用、`host`/`port` 是否与控制台一致；`deepfeiyu_console action=status` 查看 |
| 工具报「控制台未运行」 | `deepfeiyu_console action=start`，或打开面板点「启动」 |
| 工具报 401/403 | 控制台设置了访问令牌，但插件没读到；配置 `accessToken` 或设置 `DEEPFEIYU_ACCESS_TOKEN` |
| 设置页安装按钮报「控制台未运行」 | 先在设置页点「启动控制台」（或面板/工具的启动入口），就绪后状态徽标会自动刷新 |
| 关联 DSH 插件显示「没有插件管理服务」 | 当前 profile 没有启用 `plugin-manager`；在 DSH 插件页启用，或改用 DSH 插件页手动安装 |
| 关联 DSH 插件安装失败 / 提示已安装 | 失败看结论里的诊断（体积大时可能下载超时，可重试）；已安装时更新请到 DSH 插件页操作，这里不自动卸载重装 |
| 发送被拒 | 看错误：无通道/身份未确认/命中禁发名单/未获审批/节流超时，分别对应控制台与服务端策略 |

## 卸载与回滚

- DSH 插件页关闭并移除此 bundle，或让智能体调用 `plugin_manager` 移除 `@local/deepfeiyu-dsh`；
- 刷新页面；插件卸载时会终止**它自己拉起**的控制台进程（`attach` 模式下不会动外部实例）；
- 仓库侧回滚：丢弃对应分支即可，本插件不在 `main` 上、不改动既有 Python 行为（仅新增 `source` 参数，默认值保持 `human`）。
