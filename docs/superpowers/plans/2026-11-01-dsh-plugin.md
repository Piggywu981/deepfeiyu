# Deep肥鱼 DSH 插件实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 Deep肥鱼做成可安装到 DSH `desktop` profile 的 Cordis bundle：Harness 内有「Deep肥鱼」原生面板，Agent 有 8 个 `deepfeiyu_*` 工具，插件负责拉起/停止 Python 控制台，发送走 Harness 权限模式审批。

**架构：** `dsh-plugin/` 无构建 bundle；Host 半用 `ctx.connection.rpc.handle('/deepfeiyu', …)` 暴露操作、用 `ctx.subprocess` 监管 `.venv` Python 控制台、用 `ctx.tools.register` 注册工具；Client 半用 `sidebar.panellist` + `main[deepfeiyu]` 注册原生 React 面板，通过 `ctx.connection.rpc.call` 取数；「完整控制台」用 `ctx.sidebarRight.openTab('browser', …)` 在右侧栏打开。Python 侧只加 `SendBody.source` 字段。

**技术栈：** Cordis（DSH 内置）、Connection RPC、ctx.subprocess、React（浏览器模块表提供）、FastAPI（既有）、pytest、PyYAML（已作为 uvicorn 依赖存在）。

**规格：** `docs/superpowers/specs/2026-11-01-dsh-plugin-design.md`（已批准）

**工作区：** `.agents/worktrees/feat/dsh-plugin`（分支 `feat/dsh-plugin`，基线 `14597a7`）

---

## 文件结构（先锁定边界）

```
dsh-plugin/
  package.json          # bundle 清单：exports/files/dsh.bundle/dsh.client
  cordis.patch.yml      # 插入 deepfeiyu 行 + 默认 config
  index.js              # Host 入口：配置规范化、wiring、资源生命周期
  lib/ops.js            # 控制台操作层（HTTP 调用 + 错误映射），面板与工具共用
  lib/supervisor.js     # 控制台进程监督（spawn/健康轮询/停止/状态）
  lib/approval.js       # 发送审批映射（permissionPresets → ask/never）
  lib/tools.js          # 8 个 deepfeiyu_* ToolDefinition
  lib/rpc.js            # Connection RPC endpoints（'/deepfeiyu'）
  client.js             # Client 半：图标 + 中央面板 + 完整控制台入口
  locale/en.json        # Plugin Manager 展示元数据
  locale/zh.json
  icon.svg              # 自绘图标
  README.md             # 安装/配置/安全/卸载
app/main.py             # SendBody.source（最小改动，约 322-535 行区域）
tests/test_send_source.py     # source 语义
tests/test_dsh_plugin.py      # bundle 静态一致性
docs/使用说明.md / CHANGELOG.md / README.md   # 用户可见文档
```

**写入范围（并行时不得重叠）：**
- Lead：`dsh-plugin/package.json`、`cordis.patch.yml`、`index.js`、`lib/*`、`icon.svg`、`locale/*`、`dsh-plugin/README.md`
- 工作者 A（python-docs）：`app/main.py`、`tests/test_send_source.py`、`tests/test_dsh_plugin.py`、`docs/使用说明.md`、`CHANGELOG.md`、`README.md`
- 工作者 B（client-panel）：`dsh-plugin/client.js`

---

## 任务 0：P0 假设探针（Lead，先做）

**文件：**
- 创建：`dsh-plugin/package.json`、`cordis.patch.yml`、`index.js`（临时最小版）、`client.js`（临时最小版）、`locale/en.json`、`locale/zh.json`、`icon.svg`
- 测试：`cordis_inspect_query` 实机查询（无仓库测试文件）

- [ ] **步骤 1：写临时 bundle 骨架**

`package.json`（最终版也用它）：

```json
{
  "name": "@local/deepfeiyu-dsh",
  "version": "0.1.0",
  "private": true,
  "description": "在 DeepSeek Harness 中使用 Deep肥鱼微信智能体控制台",
  "type": "module",
  "exports": {
    ".": "./index.js",
    "./client": "./client.js",
    "./locale/*.json": "./locale/*.json",
    "./icon": "./icon.svg"
  },
  "files": ["index.js", "client.js", "lib/", "cordis.patch.yml", "locale/*.json", "icon.svg", "README.md"],
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "client": { "platform": "web", "immediately": true, "inject": ["@deepseek-ai/dsh-client-ui-conversation"] }
  }
}
```

`cordis.patch.yml`：

```yaml
- insert:
    - id: deepfeiyu
      name: '@local/deepfeiyu-dsh'
      config:
        mode: auto
        autostart: true
        host: 127.0.0.1
        port: 8765
```

`index.js`（临时：验证 Connection RPC + 工具注册）：

```js
export const inject = ['tools', 'connection']

const runs = []

export function apply(ctx, rawConfig) {
  const config = { title: 'Deep肥鱼', ...rawConfig }
  runs.push(ctx.effect(() => ctx.tools.register({
    name: 'deepfeiyu_probe',
    description: 'Deep肥鱼插件探针：确认插件已激活并返回最近一次客户端 RPC 时间。',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: { type: 'object', properties: { ok: { type: 'boolean' }, clientProbeAt: { type: 'number' } }, additionalProperties: false },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute() {
      return { ok: true, clientProbeAt: lastClientProbe }
    },
  }), 'deepfeiyu.probe-tool'))

  let lastClientProbe = 0
  ctx.effect(() => ctx.connection.rpc.handle('/deepfeiyu', async (endpoint, payload) => {
    if (endpoint === 'probe') {
      lastClientProbe = Date.now()
      return { ok: true, value: { pong: true } }
    }
    return { ok: false, error: { code: 'UNKNOWN_ENDPOINT', message: `未知 endpoint: ${endpoint}`, details: {} } }
  }), 'deepfeiyu.rpc')

  ctx.logger.info('deepfeiyu: probe active (title=%s)', config.title)
  void runs
}
```

`client.js`（临时：验证 slot 注册 + client RPC）：

```js
window.__ModuleLoader__.load({
  id: '@local/deepfeiyu-dsh',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    function Icon({ size = 18 }) {
      return h('svg', { viewBox: '0 0 24 24', width: size, height: size, 'aria-hidden': true },
        h('circle', { cx: 12, cy: 12, r: 10, fill: 'none', stroke: 'currentColor', strokeWidth: 2 }))
    }
    function Panel() {
      return h('div', { style: { padding: 20, color: 'var(--dsw-alias-text-primary)' } }, 'Deep肥鱼 P0 探针面板')
    }

    return {
      inject: ['slots', 'connection'],
      apply(ctx) {
        ctx.effect(() => ctx.slots.register({ name: 'sidebar.panellist', id: 'deepfeiyu', order: 20, label: () => 'Deep肥鱼' }, Icon), 'deepfeiyu.icon')
        ctx.effect(() => ctx.slots.register({ name: 'main', key: 'deepfeiyu' }, Panel), 'deepfeiyu.panel')
        void ctx.connection.rpc.call('/deepfeiyu', 'probe', {}).catch(() => {})
      },
    }
  },
})
```

`locale/en.json`：`{"meta":{"title":"DeepFeiyu WeChat Agent","description":"Run and control the DeepFeiyu WeChat agent console from the Harness UI."}}`
`locale/zh.json`：`{"meta":{"title":"Deep肥鱼微信智能体","description":"在 Harness 界面里运行并操作 Deep肥鱼微信智能体控制台。"}}`
`icon.svg`：自绘 24×24 圆环鱼形，`fill="currentColor"`，≤2KB。

- [ ] **步骤 2：安装探针并读安装结果**

运行（绝对路径）：

```powershell
# 用 plugin_manager 工具（不是 shell）：
# action=install_bundle, target=E:\Gitee\deepfeiyu\.agents\worktrees\feat\dsh-plugin\dsh-plugin
```

预期：`application: applied`（或 `restart-required`）；`warnings` 不阻断。

- [ ] **步骤 3：确认 Host 行与工具**

`cordis_inspect_query` host `Config.listConfigs {name:'@local/deepfeiyu-dsh'}` → 有 `deepfeiyu` entry；
`cordis_inspect_query` host `Tool.listTools` → 含 `deepfeiyu_probe`。

- [ ] **步骤 4：确认 Client 注册（需页面已加载新 bundle）**

`cordis_inspect_query` client `Slots.listSubTree {root:'sidebar.panellist'}` → occupants 含 `deepfeiyu`；
`{root:'main'}` → occupants 含 `deepfeiyu`。
若未出现：请用户刷新页面后重查；仍不出现则记录失败并改用备选方案（`webServer` exact route + `requestRejection`）。

- [ ] **步骤 5：确认 Client→Host RPC 与权限预设**

刷新后等 5s，再调用 `deepfeiyu_probe` 工具（Host 返回 `clientProbeAt>0` 即证明 Client RPC 打通）。
`cordis_inspect_query` host `Service.listService {service:'permissionPresets'}` 记录可用；本机 profile 有该服务。

- [ ] **步骤 6：Commit 探针**

```powershell
git add dsh-plugin
git commit -m "feat(dsh-plugin): 探针 bundle 验证 Connection RPC 与 slot 注册"
```

**P0 失败分支：** 若 Connection RPC 不可用，Host 改为 `ctx.webServer.register({kind:'prefix', path:'/deepfeiyu', handler})` + `ctx.connection.requestRejection({headers:req.headers})`，Client 改为同源 `fetch('/deepfeiyu/...')`；其余任务不变。若 `sidebarRight` 不可用，面板按钮降级 `window.open`。

---

## 任务 1：配置与操作层（Lead）

**文件：**
- 创建：`dsh-plugin/lib/ops.js`、`dsh-plugin/lib/tools.js`（先只含 status 工具）、`dsh-plugin/lib/rpc.js`（先只含 state endpoint）
- 修改：`dsh-plugin/index.js`
- 测试：实机（`cordis_inspect_query` + `deepfeiyu_status` 工具）

- [ ] **步骤 1：实现 `lib/ops.js` 的控制台 HTTP 客户端**

```js
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

export class ConsoleDownError extends Error {}
export class ConsoleApiError extends Error {
  constructor(status, message) { super(message); this.status = status; this.code = 'CONSOLE_HTTP' }
}

export async function resolveToken(config, projectDir) {
  if (config.accessToken) return String(config.accessToken)
  if (process.env.DEEPFEIYU_ACCESS_TOKEN) return process.env.DEEPFEIYU_ACCESS_TOKEN
  try {
    const raw = await readFile(join(projectDir, 'data', 'config.json'), 'utf8')
    const token = JSON.parse(raw)?.server?.access_token
    return typeof token === 'string' ? token : ''
  } catch { return '' }
}

export function createConsoleApi({ getConfig, getToken }) {
  async function call(path, { method = 'GET', body, timeoutMs = 5000 } = {}) {
    const c = getConfig()
    const url = `http://${c.host}:${c.port}${path}`
    const headers = { accept: 'application/json' }
    const token = await getToken()
    if (token) headers['x-access-token'] = token
    if (body !== undefined) headers['content-type'] = 'application/json'
    const signal = AbortSignal.timeout(timeoutMs)
    let res
    try {
      res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal })
    } catch (err) {
      throw new ConsoleDownError(`控制台未运行或不可达（${url}）：${err?.message || err}`)
    }
    const text = await res.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    if (!res.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : text.slice(0, 300)
      throw new ConsoleApiError(res.status, `控制台返回 ${res.status}：${detail}`)
    }
    return data
  }
  return {
    health: () => call('/api/health', { timeoutMs: 3000 }),
    status: () => call('/api/status'),
    accounts: () => call('/api/accounts'),
    activate: (id) => call(`/api/accounts/${encodeURIComponent(id)}/activate`, { method: 'POST', body: {} }),
    chats: (keyword = '', limit = 200) => call(`/api/chats?keyword=${encodeURIComponent(keyword)}&limit=${Math.trunc(limit)}`),
    messages: (uid, limit = 80) => call(`/api/chats/${encodeURIComponent(uid)}/messages?limit=${Math.trunc(limit)}`),
    stats: () => call('/api/stats'),
    capabilities: () => call('/api/capabilities'),
    send: (uid, text, source) => call(`/api/chats/${encodeURIComponent(uid)}/send`, { method: 'POST', body: { text, source }, timeoutMs: getConfig().sendTimeoutMs }),
  }
}
```

- [ ] **步骤 2：在 `index.js` 做配置规范化与 wiring（本任务先不接 supervisor）**

```js
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createConsoleApi, resolveToken } from './lib/ops.js'
import { registerTools } from './lib/tools.js'
import { registerRpc } from './lib/rpc.js'

export const inject = ['tools', 'subprocess', 'connection']

const DEFAULTS = {
  mode: 'auto', autostart: true, host: '127.0.0.1', port: 8765,
  accessToken: '', requestTimeoutMs: 5000, sendTimeoutMs: 60000,
  panelTitle: 'Deep肥鱼', pythonPath: '',
}

export function apply(ctx, rawConfig) {
  const pkgRoot = dirname(fileURLToPath(import.meta.url))
  const projectDir = resolve(rawConfig?.projectDir || join(pkgRoot, '..'))
  const isWin = process.platform === 'win32'
  const pythonPath = resolve(rawConfig?.pythonPath || join(projectDir, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python'))
  const config = {
    ...DEFAULTS, ...(rawConfig || {}),
    projectDir, pythonPath,
    mode: rawConfig?.mode === 'attach' ? 'attach' : 'auto',
    port: Number(rawConfig?.port || 8765),
  }
  if (!['auto', 'attach'].includes(String(config.mode))) throw new Error('deepfeiyu: mode 只能是 auto 或 attach')
  if (!Number.isInteger(config.port) || config.port < 1 || config.port > 65535) throw new Error('deepfeiyu: port 非法')

  const state = { phase: 'stopped', running: false, lastError: '', startedAt: 0, spawnedByPlugin: false, version: '' }
  const api = createConsoleApi({ getConfig: () => config, getToken: () => resolveToken(config, projectDir) })

  // 所有消费方（工具与 RPC）共用的状态快照，字段见任务 3 的 deepfeiyu_status 行
  const snapshot = () => ({
    phase: state.phase, running: state.running, version: state.version,
    port: config.port, mode: config.mode, lastError: state.lastError,
    startedAt: state.startedAt, spawnedByPlugin: state.spawnedByPlugin,
    title: config.panelTitle,
  })

  registerTools({ ctx, config, api, state, snapshot })
  registerRpc({ ctx, config, api, state, snapshot })
}
```

- [ ] **步骤 3：实现 `state` RPC 与 `deepfeiyu_status` 工具（本任务的最小可用版）**

`lib/rpc.js`：

```js
export function registerRpc({ ctx, config, api, state, snapshot }) {
  ctx.effect(() => ctx.connection.rpc.handle('/deepfeiyu', async (endpoint, payload) => {
    try {
      switch (endpoint) {
        case 'state':
          return { ok: true, value: snapshot() }
        default:
          return { ok: false, error: { code: 'UNKNOWN_ENDPOINT', message: `未知 endpoint: ${endpoint}`, details: {} } }
      }
    } catch (err) {
      return { ok: false, error: { code: err?.code || 'INTERNAL', message: String(err?.message || err), details: {} } }
    }
  }), 'deepfeiyu.rpc')
}
```

`lib/tools.js`：

```js
export function registerTools({ ctx, config, api, state, snapshot }) {
  ctx.effect(() => ctx.tools.register({
    name: 'deepfeiyu_status',
    description: '查看 Deep肥鱼微信智能体控制台的运行状态：阶段、端口、版本、是否运行、最近错误。',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: { type: 'object', properties: { phase: { type: 'string' }, running: { type: 'boolean' }, port: { type: 'number' } }, additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 1) }],
    },
    async execute() {
      try {
        const health = await api.health()
        state.running = true; state.version = health.version
        if (state.phase === 'stopped') state.phase = 'attached'
      } catch { state.running = false }
      return snapshot()
    },
  }), 'deepfeiyu.tools.status')
}
```

- [ ] **步骤 4：安装并验证**

`plugin_manager` 重装（替换已装包后**需重启 Harness** 才加载新 JS；本地 link 包文件原地修改可被 HMR 感知，但为确定性仍按提示重启/刷新）→ 调用 `deepfeiyu_status`，预期返回 `{phase, running, port, …}` 且不抛错。

- [ ] **步骤 5：Commit**

```powershell
git add dsh-plugin && git commit -m "feat(dsh-plugin): 配置规范化与控制台 HTTP 操作层"
```

---

## 任务 2：控制台监督器（Lead）

**文件：**
- 创建：`dsh-plugin/lib/supervisor.js`
- 修改：`dsh-plugin/index.js`（import supervisor、`supervisor.boot()`、dispose）、`dsh-plugin/lib/tools.js`（加 `deepfeiyu_console`）、`dsh-plugin/lib/rpc.js`（加 `console.*`）
- 测试：实机

- [ ] **步骤 1：实现监督器**

```js
import { access } from 'node:fs/promises'

export function createSupervisor({ ctx, config, state, api }) {
  let handle = null
  let timer = null
  let disposed = false

  async function fileExists(p) { try { await access(p); return true } catch { return false } }

  async function probe() {
    try { const h = await api.health(); state.running = true; state.phase = 'running'; state.version = h.version; state.lastError = ''; state.startedAt ||= Date.now(); return true }
    catch { state.running = false; return false }
  }

  function schedule(ms = 5000) {
    if (disposed) return
    clearTimeout(timer)
    timer = setTimeout(async () => { await probe(); schedule() }, ms)
    timer.unref?.()
  }

  async function start({ allowSpawn = true } = {}) {
    if (await probe()) { state.phase = 'attached'; state.spawnedByPlugin = false; schedule(); return state }
    if (!allowSpawn || config.mode === 'attach') { state.phase = 'stopped'; schedule(15000); return state }
    if (!(await fileExists(config.pythonPath))) {
      state.phase = 'error'; state.lastError = `未找到 Python：${config.pythonPath}。请先在项目目录运行 run.bat 完成首次安装。`
      schedule(15000); return state
    }
    state.phase = 'starting'; state.lastError = ''
    handle = ctx.subprocess.spawn({
      argv: [config.pythonPath, '-m', 'app.main'],
      cwd: config.projectDir,
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      stdio: { stdin: 'ignore', stdout: { maxBytes: 65536 }, stderr: { maxBytes: 65536 } },
      graceMs: 5000,
    })
    state.spawnedByPlugin = true
    state.startedAt = Date.now()
    handle.done.then((outcome) => {
      if (disposed) return
      state.running = false; state.spawnedByPlugin = false
      if (outcome.exitCode !== 0) {
        state.phase = 'error'
        state.lastError = tailError(handle) || `控制台进程退出（code=${outcome.exitCode}）`
      } else { state.phase = 'stopped' }
      schedule(15000)
    }).catch(() => {})
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 500))
      if (await probe()) { state.phase = 'running'; schedule(); return state }
      if (state.phase === 'error') return state
    }
    state.phase = 'error'; state.lastError ||= '控制台 60s 内未就绪'; state.running = false
    schedule(15000); return state
  }

  function tailError(h) {
    try {
      const read = h.collected?.stderr?.readFrom(0)
      if (!read?.text) return ''
      return read.text.trim().split('\n').slice(-3).join(' ').slice(0, 500)
    } catch { return '' }
  }

  async function stop() {
    if (!handle) { if (await probe()) { state.phase = 'attached'; return state } state.phase = 'stopped'; return state }
    state.phase = 'stopping'
    handle.terminate()
    await handle.waitForExit().catch(() => {})
    handle = null; state.running = false; state.spawnedByPlugin = false; state.phase = 'stopped'; return state
  }

  async function boot() {
    if (config.autostart) await start({ allowSpawn: true })
    else { await probe(); state.phase = state.running ? 'attached' : 'stopped'; schedule() }
  }

  function dispose() {
    disposed = true; clearTimeout(timer)
    if (handle) { handle.terminate(); handle = null }
    state.running = false; state.phase = 'stopped'; state.spawnedByPlugin = false
  }

  return { boot, start, stop, probe, dispose, get handle() { return handle } }
}
```

- [ ] **步骤 2：把 supervisor 接进 `index.js` 与工具/RPC**

`index.js` 追加（`apply` 内、`snapshot` 定义之后）：

```js
const supervisor = createSupervisor({ ctx, config, state, api })
registerTools({ ctx, config, api, state, snapshot, supervisor })
registerRpc({ ctx, config, api, state, snapshot, supervisor })
ctx.effect(() => () => supervisor.dispose(), 'deepfeiyu.supervisor')
supervisor.boot().catch((err) => { state.phase = 'error'; state.lastError = String(err?.message || err) })
```

（替换任务 1 中 `registerTools/registerRpc` 的两行调用，并新增 supervisor 的 import。）

工具定义（完整）：name `deepfeiyu_console`；`parameters` JSON Schema：

```json
{
  "type": "object",
  "properties": { "action": { "type": "string", "enum": ["start", "stop", "restart", "status"], "description": "要执行的控制台动作" } },
  "required": ["action"]
}
```

`execute`：`start`→`supervisor.start()`、`stop`→`supervisor.stop()`、`restart`→`stop()` 后 `start()`、`status`→`supervisor.probe()`；返回 `snapshot()`；错误 throw 中文消息。RPC 增加 `console.start` / `console.stop` / `console.restart`，返回同一 `snapshot()`。

- [ ] **步骤 3：实机验证**

`.venv` 已存在（本 worktree 已建）→ 调用 `deepfeiyu_console {action:'start'}`，预期 60s 内 `phase:'running'`；再 `status`；`stop` 后 `phase:'stopped'` 且进程消失（`Get-NetTCPConnection -LocalPort 8765` 为空）。

- [ ] **步骤 4：Commit**

```powershell
git add dsh-plugin && git commit -m "feat(dsh-plugin): 控制台进程监督与健康轮询"
```

---

## 任务 3：只读操作与工具（Lead）

**文件：**
- 修改：`dsh-plugin/lib/tools.js`、`dsh-plugin/lib/rpc.js`
- 测试：实机

- [ ] **步骤 1：实现工具公共封装**

```js
function define({ name, description, parameters, schema, run }) {
  return {
    name, description, parameters,
    output: { schema, render: (_a, v) => [{ type: 'text', text: JSON.stringify(v, null, 1) }] },
    async execute(args, exec) { return await run(args || {}, exec) },
  }
}
```

- [ ] **步骤 2：6 个只读工具**（参数 schema 与返回结构逐条如下；`deepfeiyu_status` 与 `deepfeiyu_console` 已在任务 1/2 存在，本任务补齐其余 6 个）

| 工具 | parameters | 返回 value |
|---|---|---|
| `deepfeiyu_status` | `{type:'object', properties:{}, additionalProperties:false}` | `{phase,running,version?,port,mode,lastError,startedAt,spawnedByPlugin,console:{configured:boolean,reachable:boolean,version?}}` |
| `deepfeiyu_accounts` | `{action?:'list'\|'activate', account_id?:string}` | `{accounts:[{id,name,nickname,driver,enabled,active,connected,running_driver,display_name,detail,error}], active}` |
| `deepfeiyu_chats` | `{keyword?:string, limit?:integer(1-200)}` | `{chats:[…原样 /api/chats 字段…]}` |
| `deepfeiyu_messages` | `{uid:string, limit?:integer(1-100)}` | `{chat, messages:[…原样字段，text/msg_type/is_self/ts/sender_name…]}` |
| `deepfeiyu_stats` | `{}` | `{stats: 原样 /api/stats}` |
| `deepfeiyu_capabilities` | `{}` | `{capabilities: 原样 /api/capabilities}` |

错误映射：`ConsoleDownError` → `throw new Error('Deep肥鱼控制台未运行：' + err.message)`；`ConsoleApiError` 原样消息。

- [ ] **步骤 3：RPC endpoints**：`state`、`accounts.list`、`accounts.activate`、`chats.list`、`messages.list`、`stats`、`capabilities`、`console.start|stop|restart`。统一 try/catch → `{ok:false,error:{code,message,details:{}}}`；`ConsoleDownError` → `code:'CONSOLE_DOWN'`。

- [ ] **步骤 4：实机验证**：控制台 running 时依次调用 6 个工具；`deepfeiyu_accounts {action:'list'}` 返回数组；停掉控制台后 `deepfeiyu_status` 返回 `reachable:false` 而不抛异常（工具自身仍成功）。

- [ ] **步骤 5：Commit**

```powershell
git add dsh-plugin && git commit -m "feat(dsh-plugin): 只读操作、Agent 工具与 Connection RPC"
```

---

## 任务 4：发送链路（工作者 A + Lead 联调）

**文件：**
- 修改：`app/main.py:518-535`（`SendBody` 与 `send`）
- 创建：`tests/test_send_source.py`
- 修改：`dsh-plugin/lib/ops.js`（已含 send）、`dsh-plugin/lib/approval.js`、`dsh-plugin/lib/tools.js`（`deepfeiyu_send`）、`lib/rpc.js`（`send.text`）

- [ ] **步骤 1：写失败的 Python 测试**

```python
"""DSH 插件发送来源：默认 human；dsh 来源透传并走既有节流分支。"""
import asyncio

import pytest
from fastapi import HTTPException

from app import main as m


class _Body:
    def __init__(self, text: str, source: str | None = "human") -> None:
        self.text = text
        if source is not None:
            self.source = source


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_text(self, uid: str, text: str, source: str = "human", **kwargs) -> dict:
        self.calls.append((uid, text, source))
        return {"uid": uid, "text": text, "source": source}


class _Request:
    pass


def _run(session, body):
    m.hub.get = lambda _aid: session  # type: ignore[assignment]

    async def _go():
        return await m.send(_Request(), "uid1", body)

    return asyncio.run(_go())


def test_default_source_is_human(monkeypatch):
    session = _Session()
    res = _run(session, _Body("你好"))
    assert res["ok"] is True
    assert session.calls == [("uid1", "你好", "human")]


def test_dsh_source_is_forwarded(monkeypatch):
    session = _Session()
    _run(session, _Body("你好", "dsh"))
    assert session.calls == [("uid1", "你好", "dsh")]


def test_blank_text_is_rejected():
    session = _Session()
    with pytest.raises(HTTPException) as exc:
        _run(session, _Body("   "))
    assert exc.value.status_code == 400
```

运行：`.venv\Scripts\python.exe -m pytest tests/test_send_source.py -q`
预期：FAIL（`SendBody` 无 `source` 或 `send` 不接受 `_Body`）。

- [ ] **步骤 2：最小实现**

`app/main.py`：

```python
from typing import Any, Literal

class SendBody(BaseModel):
    text: str
    # human = 控制台人工发送（不排队）；dsh = Harness 智能体发送（走既有节流队列）
    source: Literal["human", "dsh"] = "human"
```

`send()` 内：`record = await session.send_text(uid, text, source=body.source)`。

- [ ] **步骤 3：运行测试验证通过**

`.venv\Scripts\python.exe -m pytest tests/test_send_source.py -q` → PASS；再跑 `tests/test_send_gate.py tests/test_send_throttle.py tests/test_local_guard.py`（回归）。

- [ ] **步骤 4：Host 侧审批映射 `lib/approval.js`**

```js
export function createSendGate(ctx) {
  const sessions = ctx.get('sessions')
  const presets = ctx.get('permissionPresets')
  const approval = ctx.get('approval')

  function approvalMode(agent) {
    try {
      const session = agent && sessions?.get(agent.id)
      if (!session || !presets) return 'ask'
      const name = presets.current(session)
      const spec = presets.resolve(name)
      return spec?.approval === 'never' ? 'never' : 'ask'
    } catch { return 'ask' }
  }

  return {
    approvalMode,
    async assertAllowedSend(exec, reason) {
      if (approvalMode(exec?.agent) === 'never') return
      if (!approval) throw new Error('当前环境没有审批服务，已拒绝发送（fail closed）')
      const outcome = await approval.request({
        agent: exec.agent, toolName: 'deepfeiyu_send', callId: exec.callId,
        reason, displayReason: { en: reason, zh: reason }, signal: exec.signal,
      })
      if (outcome !== 'allowed-once') {
        throw new Error(`发送未获批准（${outcome}）；如要直接发送，请把会话权限切换到“完全批准”。`)
      }
    },
  }
}
```

`deepfeiyu_send` 工具：parameters `{type:'object', properties:{uid:{type:'string',description:'会话 uid'},text:{type:'string',description:'要发送的文本，最多 2000 字'}}, required:['uid','text']}`；`execute`：校验去空白非空、长度 ≤2000 → `assertAllowedSend(exec, '通过 Deep肥鱼发送一条微信消息')` → `api.send(uid, text, 'dsh')`。

`index.js` 在任务 1 的 `apply` 内追加（import + wiring）：

```js
import { createSendGate } from './lib/approval.js'
// apply 内，api 之后：
const sendGate = createSendGate(ctx)
registerTools({ ctx, config, api, state, snapshot, supervisor, sendGate })
registerRpc({ ctx, config, api, state, snapshot, supervisor, sendGate })
```

RPC `send.text`（面板）：调 `api.send(uid, text, 'human')`（用户点击发送，等价人工）。

- [ ] **步骤 5：实机验证**：控制台 running 且无真实微信通道时，调用 `deepfeiyu_send` → 预期在 ask 模式先弹审批（用户拒绝则工具报“发送未获批准”）；批准后报 Python 侧可读错误（“微信通道未启动”），证明身份闸门生效。真实发送需有通道账号，由用户在验收时自测。

- [ ] **步骤 6：Commit**

```powershell
git add app/main.py tests/test_send_source.py dsh-plugin
git commit -m "feat(dsh-plugin): 发送链路接入既有闸门与 Harness 审批"
```

---

## 任务 5：Client 面板（工作者 B）

**文件：**
- 修改/创建：`dsh-plugin/client.js`（唯一写入文件）

- [ ] **步骤 1：注册与样式骨架**

```js
window.__ModuleLoader__.load({
  id: '@local/deepfeiyu-dsh',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    // ... Icon / Panel / usePoll 见下
    return { inject: ['slots', 'connection'], apply(ctx) { /* 注册 icon + panel */ } }
  },
})
```

Icon：接收 `{size, active}`，24×24 SVG，`currentColor`，自绘鱼形。
Panel：`ctx.connection.rpc.call('/deepfeiyu', 'state', {})` 等；4s 轮询 `state`（挂载时立即拉一次），选中会话时 4s 轮询 `messages.list`；卸载清理 interval。
「打开完整控制台」：优先 `ctx.get('sidebarRight')?.openTab?.('browser', { params: { url } })`，否则 `window.open(url, '_blank', 'noopener')`。

- [ ] **步骤 2：面板组成（无嵌套 iframe）**

- 头部：状态圆点（运行绿/停止灰/错误红）+ `panelTitle` + 版本/端口 + 启动/停止/重启按钮 + 打开完整控制台按钮。
- 账号条：chips，点击 `accounts.activate` 后刷新。
- 左列：搜索框 + 会话列表（名称、last_message 摘要、时间、未读、paused/ai 标记）。
- 右列：消息记录（`is_self` 左右对齐；`msg_type!=='text'` 显示类型标签）+ 发送框（`send.text`）。
- 底部：今日统计（inbound/outbound/ai/chats）。
- 错误：顶部可关闭错误条，显示 RPC error.message。

- [ ] **步骤 3：样式约束**

类名前缀 `dsh-deepfeiyu-`；颜色只用 `var(--dsw-alias-*)`（文本、边框、背景、危险色）；无 `require` Harness 包；无 `document.body` 操作；组件内用 `<style>` React 元素渲染 CSS（卸载自动移除）。

- [ ] **步骤 4：语法自查**

`node --check dsh-plugin/client.js`（Node 22 已安装）；`node --check dsh-plugin/index.js dsh-plugin/lib/*.js`。

- [ ] **步骤 5：实机验证**

刷新页面 → `Slots.listSubTree {root:'main'}` 有 `deepfeiyu` occupant 且无 `slot entry crashed` 报错（console 不可读时，至少确认 registration 存在）；面板数据由用户在页面确认。

- [ ] **步骤 6：Commit**

```powershell
git add dsh-plugin/client.js
git commit -m "feat(dsh-plugin): Harness 原生「Deep肥鱼」面板"
```

---

## 任务 6：静态测试与文档（工作者 A）

**文件：**
- 创建：`tests/test_dsh_plugin.py`
- 修改：`README.md`、`docs/使用说明.md`、`CHANGELOG.md`、`dsh-plugin/README.md`（README 由 Lead 起草、工作者 A 校对）

- [ ] **步骤 1：写 bundle 静态测试**

```python
"""DSH 插件 bundle 的静态一致性：清单、patch、locale、icon、文件清单、工具名。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dsh-plugin"


def test_manifest_required_fields():
    data = json.loads((PKG / "package.json").read_text(encoding="utf-8"))
    assert data["private"] is True
    assert data["type"] == "module"
    assert data["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert data["dsh"]["client"]["platform"] == "web"
    for key in (".", "./client", "./icon"):
        assert key in data["exports"]
    assert "./locale/*.json" in data["exports"]


def test_patch_inserts_bundle_row():
    patch = yaml.safe_load((PKG / "cordis.patch.yml").read_text(encoding="utf-8"))
    rows = patch[0]["insert"]
    assert any(r["id"] == "deepfeiyu" and r["name"] == "@local/deepfeiyu-dsh" for r in rows)


def test_files_list_matches_disk():
    data = json.loads((PKG / "package.json").read_text(encoding="utf-8"))
    assert (PKG / "index.js").is_file()
    assert (PKG / "client.js").is_file()
    assert (PKG / "cordis.patch.yml").is_file()
    for loc in ("en", "zh"):
        meta = json.loads((PKG / "locale" / f"{loc}.json").read_text(encoding="utf-8"))["meta"]
        assert meta["title"] and meta["description"]
    icon = PKG / "icon.svg"
    assert icon.is_file() and icon.stat().st_size <= 256 * 1024
    for rel in data["files"]:
        if rel.endswith("/"):
            assert (PKG / rel).is_dir()
        elif "*" not in rel:
            assert (PKG / rel).is_file(), rel


def test_tool_names_match_host_source():
    source = (PKG / "lib" / "tools.js").read_text(encoding="utf-8")
    for name in (
        "deepfeiyu_status", "deepfeiyu_accounts", "deepfeiyu_chats",
        "deepfeiyu_messages", "deepfeiyu_send", "deepfeiyu_stats",
        "deepfeiyu_capabilities", "deepfeiyu_console",
    ):
        assert f"'{name}'" in source or f'"{name}"' in source


def test_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，无法做 JS 语法检查（已在 PR 中如实说明）")
    files = [PKG / "index.js", PKG / "client.js", *sorted((PKG / "lib").glob("*.js"))]
    for f in files:
        subprocess.run([node, "--check", str(f)], check=True, capture_output=True)
```

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q` → 预期 PASS。

- [ ] **步骤 2：文档**

- `README.md` 新增「DSH 插件」小节：一句定位 + 安装命令（DSH UI 或 `plugin_manager`）+ 配置表 + 安全说明 + 卸载方式；不声称未验证的能力。
- `docs/使用说明.md` 新增页面/功能说明（DSH 面板与工具、发送审批与权限模式、完整控制台入口）。
- `CHANGELOG.md` 顶部新增条目（日期 2026-11-01，`feat`）。
- `dsh-plugin/README.md` 完整化（配置表、故障排查：`.venv` 缺失/端口占用/令牌、卸载与回滚）。

- [ ] **步骤 3：全量回归**

`.venv\Scripts\python.exe -m pytest -q` → 预期与基线一致（9 failed / 1392+ passed / 1 skipped，失败为环境性），新增测试全绿；记录真实数字。

- [ ] **步骤 4：脱敏检查**

```powershell
.venv\Scripts\python.exe tools\release_snapshot.py --scan-tracked .
git diff --check
```

- [ ] **步骤 5：Commit**

```powershell
git add tests/test_dsh_plugin.py README.md docs/使用说明.md CHANGELOG.md dsh-plugin/README.md
git commit -m "test(dsh-plugin): bundle 静态校验、文档与 CHANGELOG"
```

---

## 任务 7：安装与实机验收（Lead）

**文件：** 无新增；修复回环到对应任务文件

- [ ] **步骤 1：安装**：`plugin_manager install_bundle(target=<abs>/dsh-plugin)`；记录 `application` / `warnings`。
- [ ] **步骤 2：确认生效**：`cordis_inspect_query` host `Config.listConfigs {name:'@local/deepfeiyu-dsh'}`；`Tool.listTools` 含 8 个工具。
- [ ] **步骤 3：刷新页面后确认 Client**：`Slots.listSubTree {root:'main'}` / `{root:'sidebar.panellist'}` 含 `deepfeiyu`；用户肉眼确认图标/面板/完整控制台入口。
- [ ] **步骤 4：功能冒烟**：`deepfeiyu_console start` → `deepfeiyu_status`（running）→ `deepfeiyu_accounts` → `deepfeiyu_chats` → `deepfeiyu_messages`（取第一个会话 uid）→ `deepfeiyu_stats` → `deepfeiyu_capabilities` → `deepfeiyu_send`（ask 模式审批拒绝路径 + 批准路径）→ `deepfeiyu_console stop`。
- [ ] **步骤 5：验收清单**：对照规格 §9.1 逐项勾选；失败项回到对应任务修复。
- [ ] **步骤 6：最终 commit**（如有修复）。

```powershell
git add -A && git commit -m "fix(dsh-plugin): 实机验收问题修复"
```

---

## 风险与回滚

- Connection RPC 不可用 → 任务 0 步骤 6 的备选（webServer route + requestRejection + fetch）。
- 替换已装包需重启 Harness；首次安装后需刷新页面（HMR 边界，原文依据见规格 §3）。
- Python 发送节流可能排队：`sendTimeoutMs` 60s；超时返回明确错误，不自动重试。
- 回滚：`plugin_manager remove_bundle` → 刷新 → 丢弃分支（不 push、不打 tag）。

## 完成标准

规格 §9.1 全部勾选；全量 pytest 与基线一致；`tools/release_snapshot.py --scan-tracked .` 与 `git diff --check` 通过；提交信息符合 Conventional Commits；未 push/未开 PR。
