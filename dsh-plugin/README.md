# Deep肥鱼 × DeepSeek Harness 插件

在 DeepSeek Harness 里运行与操作 **Deep肥鱼微信智能体**（本仓库的 Python 控制台）：

- Harness 左侧栏新增「Deep肥鱼」图标，中央面板提供控制台状态、启停、账号切换、会话列表、消息记录与发送；
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
| `lib/route.js` | 面板数据路由：`POST /deepfeiyu/<endpoint>`（Connection 鉴权） |
| `client.js` | Client 半：`sidebar.panellist` 图标 + `main` 中央面板 |

无构建步骤、无 npm 依赖；仅使用 DSH 提供的 Cordis 服务与 Node 内建模块。

## 安装

1. 先在项目根运行一次 `run.bat`（或等价命令）完成 `.venv` 与依赖安装；插件不会替你下载依赖。
2. 在 DSH 里安装本地 bundle（绝对路径指向本目录）：
   - 插件管理页选择「安装本地目录」，或
   - 让智能体调用 `plugin_manager`：`action=install_bundle`，`target=<仓库根>/dsh-plugin`。
3. 安装结果以 `application` 字段为准；首次安装后刷新页面以加载 Client 模块。
4. 替换已安装的插件包后需要重启 Harness 才会加载新的 Host JS（本地 link 包的文件改动可被 HMR 感知，但确定性行为以重启为准）。

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

## 安全

- **不削弱任何服务端闸门**：所有发送仍走 Python 控制台的既有 `send_text`（含驱动在线、绑定身份确认、`send.forbidden_wxids` 禁发名单、写操作本机限制、访问令牌校验）。
- **Agent 发送跟随 Harness 权限模式**：会话审批策略为 `ask`（逐步审批 / Auto 自动审批）时，每次发送先走 `ctx.approval.request`，仅 `allowed-once` 才发送；策略为 `never`（完全批准 / headless）时不弹审批直接发送。无法判定时 fail closed 为 `ask`。
- **agent 发送走节流队列**：工具调用以 `source="dsh"` 走控制台既有的发送节流（全局最小间隔 + 会话冷却），比人工发送更保守；面板发送等价人工点击（`source="human"`）。
- **令牌不外泄**：令牌只在 Host 进程内用于调用 `127.0.0.1`，不写日志、不返回给 Client、不进入工具结果。
- **面板数据路由有鉴权**：`/deepfeiyu/*` 先调用 `ctx.connection.requestRejection` 复用 DSH 的浏览器 Cookie 与 Host/Origin 校验，未通过直接 401/403。
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
| 发送被拒 | 看错误：无通道/身份未确认/命中禁发名单/未获审批/节流超时，分别对应控制台与服务端策略 |

## 卸载与回滚

- DSH 插件页关闭并移除此 bundle，或让智能体调用 `plugin_manager` 移除 `@local/deepfeiyu-dsh`；
- 刷新页面；插件卸载时会终止**它自己拉起**的控制台进程（`attach` 模式下不会动外部实例）；
- 仓库侧回滚：丢弃对应分支即可，本插件不在 `main` 上、不改动既有 Python 行为（仅新增 `source` 参数，默认值保持 `human`）。
