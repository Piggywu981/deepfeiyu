# 完整设置缝进插件设置 · P1 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 DSH「设置 → Deep肥鱼」里做出分标签的完整设置（模型与人格 / 自动回复 / 依赖与融合 / 更新与边界四个标签），数据与控制台同一份、保存只发窄补丁。

**架构：** 插件 Client 页内标签按需懒加载 → 同源 `POST /deepfeiyu/<endpoint>` → Host 显式路由表 → 控制台既有 `/api/settings|personas|update` API。控制台是唯一状态源；本计划只改 `dsh-plugin/`（Host 路由 + Client 页面）与测试/文档，**不改控制台 Python 代码**。

**技术栈：** Cordis bundle（无构建原生 JS，React 由宿主注入）、FastAPI 控制台既有接口、pytest（静态断言 + 契约测试）、node --check。

**规格：** `docs/superpowers/specs/2026-11-02-full-settings-in-plugin-design.md`（本计划只覆盖其 P1；P2「账号与通道」另写一份计划）

---

## 文件结构

| 文件 | 职责 | 变更 |
|---|---|---|
| `dsh-plugin/lib/ops.js` | 控制台 HTTP 调用层（与面板/工具共用） | 修改：新增 8 个设置页方法 |
| `dsh-plugin/lib/route.js` | 同源路由表（Connection 鉴权后逐个分发） | 修改：新增 8 个 endpoint，`settings.put` 校验 patch 形状 |
| `dsh-plugin/client.js` | Client 半：面板 + 设置页 | 修改：标签壳、模型与人格标签、自动回复标签、更新与边界标签、依赖标签迁移、设置类控件与 CSS |
| `tests/test_dsh_settings_contract.py` | 控制台契约测试（端点存在、密钥不回传、掩码回写） | 新建 |
| `tests/test_dsh_plugin.py` | bundle 静态一致性 | 修改：补 5 个静态断言（路由/标签） |
| `README.md`、`dsh-plugin/README.md`、`docs/使用说明.md`、`CHANGELOG.md` | 文档 | 修改：设置页分标签说明 |

`client.js` 是无构建单文件模块（`factory(require)` 里 `require` 只解析宿主模块，不能 import 本地文件），所以控件组件与四个标签页都写在 `client.js` 的 `apply(ctx)` 闭包内，按节组织。

---

## 任务 1：契约测试 + Host 设置路由

**文件：**
- 创建：`tests/test_dsh_settings_contract.py`
- 修改：`dsh-plugin/lib/ops.js`（`dshPetEvent` 行之后）
- 修改：`dsh-plugin/lib/route.js`（`'fusion.timeline'` 条目之后、路由表闭合 `}` 之前）
- 修改：`tests/test_dsh_plugin.py`（`def test_js_syntax():` 之前插入）

- [ ] **步骤 1：编写失败的契约测试**

创建 `tests/test_dsh_settings_contract.py`：

```python
"""插件设置页依赖的控制台契约：端点存在、密钥不回传、掩码回写不覆盖真值。

不跑网络：只断言路由登记与 config.public/_unmask_secrets 的纯函数行为。
"""
from __future__ import annotations

import copy
from pathlib import Path

import app.main as main
from app import config

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dsh-plugin"

# 规格 §6 的 P1 端点：ops.js 必须登记、控制台必须真实注册
REQUIRED_API_PATHS = (
    "/api/settings",
    "/api/settings/test-ai",
    "/api/personas",
    "/api/personas/sync_wiki",
    "/api/update/check",
)


def test_ops_js_registers_required_console_paths():
    source = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for path in REQUIRED_API_PATHS:
        assert path in source, f"ops.js 缺少 {path}"
    # 删除人格是带 id 的路径，ops.js 里以 encodeURIComponent 前缀写法登记
    assert "/api/personas/" in source, "ops.js 缺少人格删除的带 id 路径"


def test_required_paths_exist_in_console_routes():
    registered = {getattr(route, "path", "") for route in main.app.routes}
    for path in REQUIRED_API_PATHS:
        assert path in registered, f"控制台未注册 {path}"
    assert "/api/personas/{name}" in registered, "控制台未注册人格删除路径"


def test_public_never_returns_real_api_key():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["ai"]["api_key"] = "sk-1234567890abcd"
    out = config.public(cfg)
    api_key = out["ai"]["api_key"]
    assert api_key != "sk-1234567890abcd"
    assert api_key.startswith("•") and api_key.endswith("abcd")
    assert out["ai"]["configured"] is True


def test_masked_secret_roundtrip_restores_true_value():
    """前端把未修改的掩码串原样回传，控制台必须还原真值而不是写入掩码。"""
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["server"]["access_token"] = "secret-token-1234"
    masked = config.public(cfg)["server"]["access_token"]
    assert masked.startswith("•")
    patch = {"server": {"access_token": masked}}
    config._unmask_secrets(patch, cfg)
    assert patch["server"]["access_token"] == "secret-token-1234"
```

- [ ] **步骤 2：运行契约测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_settings_contract.py -q`
预期：FAIL——`test_ops_js_registers_required_console_paths` 报 `ops.js 缺少 /api/settings`（另外 3 个先过：路径与掩码契约控制台本来就满足）

- [ ] **步骤 3：编写失败的静态测试**

在 `tests/test_dsh_plugin.py` 的 `def test_js_syntax():` 之前插入：

```python
def test_settings_routes_registered():
    """设置页 P1 的 8 个 endpoint 必须在 Host 路由与 ops 层都登记。"""
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for endpoint in ("settings.get", "settings.put", "settings.testAi",
                     "personas.list", "personas.add", "personas.delete",
                     "personas.syncWiki", "update.check"):
        assert f"'{endpoint}'" in route, f"route.js 缺少 {endpoint}"
    for method in ("settingsGet", "settingsPut", "settingsTestAi",
                   "personasList", "personasAdd", "personasDelete",
                   "personasSyncWiki", "updateCheck"):
        assert method in ops, f"ops.js 缺少 {method}"
    # settings.put 必须校验 patch 形状，不能把任意值透传给配置接口
    assert "patch 必须是对象" in route
```

- [ ] **步骤 4：运行静态测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_settings_routes_registered -q`
预期：FAIL，报 `route.js 缺少 settings.get`

- [ ] **步骤 5：实现 ops.js 方法**

在 `dsh-plugin/lib/ops.js` 的 `dshPetEvent` 行之后插入：

```js
    // 设置页 P1：模型/人格/更新转发控制台既有 API；写入走窄补丁，响应由控制台校验。
    settingsGet: () => call('/api/settings'),
    settingsPut: (patch) => call('/api/settings', { method: 'PUT', body: patch, timeoutMs: 15000 }),
    settingsTestAi: (payload) => call('/api/settings/test-ai', { method: 'POST', body: payload, timeoutMs: 30000 }),
    personasList: () => call('/api/personas'),
    personasAdd: (payload) => call('/api/personas', { method: 'POST', body: payload, timeoutMs: 15000 }),
    personasDelete: (name) => call(`/api/personas/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    personasSyncWiki: () => call('/api/personas/sync_wiki', { method: 'POST', body: {}, timeoutMs: 60000 }),
    updateCheck: () => call('/api/update/check?force=1', { timeoutMs: 30000 }),
```

（说明：控制台 `PUT /api/settings` 的请求体就是 patch 本身，所以 `body: patch` 直接发；`settings.put` 路由负责从 Client 的 `{patch}` 包装里取出窄补丁。）

- [ ] **步骤 6：实现 route.js 端点**

在 `dsh-plugin/lib/route.js` 的 `'fusion.timeline'` 条目之后、路由表闭合 `}` 之前插入：

```js
    // 设置页 P1：只透传；settings.put 的窄补丁由 Client 组装，这里只查形状。
    'settings.get': async () => api.settingsGet(),
    'settings.put': async (payload) => {
      const patch = payload && typeof payload.patch === 'object' && payload.patch !== null
        && !Array.isArray(payload.patch)
        ? payload.patch
        : null
      if (!patch) throw Object.assign(new Error('patch 必须是对象'), { code: 'INVALID_ARGS' })
      return await api.settingsPut(patch)
    },
    'settings.testAi': async (payload) => {
      const body = {}
      for (const key of ['base_url', 'api_key', 'model']) {
        if (typeof payload[key] === 'string') body[key] = payload[key]
      }
      return await api.settingsTestAi(body)
    },
    'personas.list': async () => api.personasList(),
    'personas.add': async (payload) => {
      if (!payload.name || typeof payload.name !== 'string') {
        throw Object.assign(new Error('缺少 name'), { code: 'INVALID_ARGS' })
      }
      return await api.personasAdd({
        name: payload.name,
        persona_name: typeof payload.persona_name === 'string' ? payload.persona_name : '',
        system_prompt: typeof payload.system_prompt === 'string' ? payload.system_prompt : '',
      })
    },
    'personas.delete': async (payload) => {
      if (!payload.name || typeof payload.name !== 'string') {
        throw Object.assign(new Error('缺少 name'), { code: 'INVALID_ARGS' })
      }
      return await api.personasDelete(payload.name)
    },
    'personas.syncWiki': async () => api.personasSyncWiki(),
    'update.check': async () => api.updateCheck(),
```

- [ ] **步骤 7：运行两个测试验证通过**

运行：
```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_settings_contract.py tests/test_dsh_plugin.py -q
```
预期：全部 PASS（契约 4 个 + bundle 静态 26 个）

- [ ] **步骤 8：假 ctx 冒烟（不入库）**

把下面脚本写到 `%TEMP%\settings-routes-smoke.mjs`（临时文件，不提交），`node` 运行：

```js
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
const ROOT = 'E:/Gitee/deepfeiyu/dsh-plugin'
const { registerRoute } = await import(pathToFileURL(resolve(ROOT, 'lib/route.js')).href)
function makeRes() { return { status: 0, body: '', writeHead(s) { this.status = s }, end(t) { this.body = t } } }
function makeReq(endpoint, payload) {
  return { method: 'POST', headers: {}, url: '/deepfeiyu/' + endpoint,
    async *[Symbol.asyncIterator]() { if (payload !== undefined) yield Buffer.from(JSON.stringify(payload)) } }
}
const calls = []
const api = {
  settingsGet: async () => ({ ai: { model: 'm' } }),
  settingsPut: async (patch) => { calls.push(['put', patch]); return { ok: true, settings: patch } },
  settingsTestAi: async (p) => ({ ok: true, latency_ms: 12, ...p }),
  personasList: async () => ({ ok: true, personas: [] }),
  personasAdd: async (p) => { calls.push(['add', p]); return { ok: true, personas: [] } },
  personasDelete: async (name) => ({ ok: true, name }),
  personasSyncWiki: async () => ({ ok: true, created: 1, updated: 0, skipped: [] }),
  updateCheck: async () => ({ ok: true, git_repo: true, has_update: false, version: '1.0.1' }),
}
let handler = null
const ctx = {
  connection: { requestRejection: () => undefined },
  webServer: { register: (r) => { handler = r.handler; return () => {} } },
  effect: (fn) => fn(),
  logger: { debug() {} },
  get: () => undefined,
}
registerRoute({ ctx, config: {}, api, state: {}, snapshot: () => ({}), supervisor: {}, sendGate: {} })
async function call(endpoint, payload) {
  const res = makeRes()
  await handler(makeReq(endpoint, payload), res)
  return { status: res.status, body: JSON.parse(res.body) }
}
function assert(ok, msg) { if (!ok) throw new Error('ASSERT: ' + msg); console.log('ok -', msg) }

let r = await call('settings.put', { patch: { ai: { model: 'x' } } })
assert(r.status === 200 && calls[0][1].ai.model === 'x', 'settings.put 透传窄补丁')
r = await call('settings.put', { ai: { model: 'x' } })
assert(r.status === 400 && r.body.error.code === 'INVALID_ARGS', '缺 patch 被拒绝')
r = await call('personas.add', { name: '预设A', system_prompt: '你好' })
assert(calls[1][1].name === '预设A' && calls[1][1].persona_name === '', 'personas.add 补默认字段')
r = await call('personas.add', {})
assert(r.status === 400, 'personas.add 缺 name 被拒绝')
r = await call('personas.delete', { name: '预设A' })
assert(r.body.ok === true, 'personas.delete 转发')
r = await call('update.check', {})
assert(r.body.value.has_update === false, 'update.check 透传')
r = await call('settings.testAi', { model: 'm' })
assert(r.body.value.latency_ms === 12, 'test-ai 透传')
console.log('\nALL PASS')
```

运行：`node %TEMP%\settings-routes-smoke.mjs`
预期：输出 8 条 `ok - …` 与 `ALL PASS`

- [ ] **步骤 9：Commit**

```bash
git add tests/test_dsh_settings_contract.py tests/test_dsh_plugin.py dsh-plugin/lib/ops.js dsh-plugin/lib/route.js
git commit -m "feat(dsh-plugin): 设置页 P1 控制台路由与契约测试（任务 1/6）"
```

---

## 任务 2：页内标签壳 + 依赖与融合迁移

**文件：**
- 修改：`dsh-plugin/client.js`（STYLES 尾部、状态区、轮询 effect、渲染区、return 块）
- 修改：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试**

在 `tests/test_dsh_plugin.py` 的 `def test_js_syntax():` 之前插入：

```python
def test_settings_page_tab_shell():
    """设置页改为页内标签壳：默认依赖与融合，原三块迁入 deps 标签。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("SETTINGS_TABS", "function TabBar", "role: 'tab'", "aria-selected",
                  "renderDepsTab", "dsh-tabpanel-deps", "'dsh-deepfeiyu-tabs'",
                  "'Deep肥鱼 · 设置'", "React.useState('deps')"):
        assert token in source, f"client.js 缺少 {token}"
    # 原三块必须仍由 deps 标签渲染（不回归）
    for token in ("renderDshPluginsSection()", "renderFusionSection()", "renderLog()"):
        assert token in source, f"依赖标签丢了 {token}"
    # 轮询跟随标签：只有 deps 分支拉依赖数据
    assert "if (tab === 'deps')" in source
    # 旧标题必须改名，避免与标签语义冲突
    assert "'Deep肥鱼 · 依赖环境'" not in source
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_settings_page_tab_shell -q`
预期：FAIL，报 `client.js 缺少 SETTINGS_TABS`

- [ ] **步骤 3：STYLES 补标签与表单控件 CSS，声明标签列表**

在 `dsh-plugin/client.js` 的 STYLES 里、`@media (max-width: 900px) {` 之前插入：

```css
.dsh-deepfeiyu-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 10px; }
.dsh-deepfeiyu-tab { appearance: none; margin: 0; padding: 5px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 8px; background: var(--dsw-alias-button-elevated-fill, #fff); color: var(--dsw-alias-label-secondary, #61666b); font: inherit; font-size: 13px; line-height: 20px; cursor: pointer; }
.dsh-deepfeiyu-tab:hover:not([aria-selected="true"]) { background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-tab[aria-selected="true"] { border-color: transparent; background: var(--dsw-alias-button-primary-fill, #0f1115); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-tab:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #4176e6); outline-offset: 1px; }
.dsh-deepfeiyu-tabpanel { display: flex; flex-direction: column; gap: 12px; }
.dsh-deepfeiyu-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 14px; }
.dsh-deepfeiyu-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.dsh-deepfeiyu-field > span { font-size: 12.5px; color: var(--dsw-alias-label-secondary, #61666b); }
.dsh-deepfeiyu-field input, .dsh-deepfeiyu-field textarea, .dsh-deepfeiyu-field select { box-sizing: border-box; width: 100%; padding: 5px 9px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 6px; background: var(--dsw-alias-bg-base, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; font-size: 13px; line-height: 20px; }
.dsh-deepfeiyu-field > em { font-size: 11.5px; line-height: 16px; color: var(--dsw-alias-label-tertiary, #81858c); font-style: normal; }
.dsh-deepfeiyu-toggle-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--dsw-alias-border-l1, #0000000a); }
.dsh-deepfeiyu-toggle-row:last-child { border-bottom: 0; }
.dsh-deepfeiyu-toggle-text { min-width: 0; }
.dsh-deepfeiyu-toggle-text strong { display: block; font-size: 13px; font-weight: 500; color: var(--dsw-alias-label-primary, #0f1115); }
.dsh-deepfeiyu-toggle-text em { display: block; font-size: 11.5px; font-style: normal; color: var(--dsw-alias-label-tertiary, #81858c); overflow-wrap: anywhere; }
.dsh-deepfeiyu-form-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.dsh-deepfeiyu-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.dsh-deepfeiyu-chip { appearance: none; margin: 0; padding: 3px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 999px; background: var(--dsw-alias-button-elevated-fill, #fff); color: var(--dsw-alias-label-secondary, #61666b); font: inherit; font-size: 12px; line-height: 18px; cursor: pointer; }
.dsh-deepfeiyu-chip.on { border-color: transparent; background: var(--dsw-alias-button-primary-fill, #0f1115); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-chip.warn { border-color: var(--dsw-alias-state-warn-primary, #f59e0b); }
.dsh-deepfeiyu-btn-on { border-color: transparent; background: var(--dsw-alias-state-business-primary, #4176e6); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-risklist { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; line-height: 19px; color: var(--dsw-alias-label-secondary, #61666b); }
.dsh-deepfeiyu-risklist strong { color: var(--dsw-alias-label-primary, #0f1115); }
```

在 STYLES 的结束反引号（`};` 前那个 `` ` ``）之后、`function errorMessage` 所在行之前的位置——即 STYLES 定义闭合处——追加标签列表（与 `STYLES` 同层，factory 作用域）：

```js
        // 设置页标签：P1 四个，P2 会插入「账号与通道」（规格 §5）。
        const SETTINGS_TABS = [['deps', '依赖与融合']];
```

- [ ] **步骤 4：组件内加 tab 状态与 TabBar**

在 `DeepFeiyuSettingsPage` 的 `const [fusionPending, setFusionPending] = React.useState(false);` 之后插入：

```js
          // 页内标签：P1 四个，随各任务加入；默认依赖与融合保持原习惯（规格 §7）。
          const [tab, setTab] = React.useState('deps');
```

在 `function renderDepsError() {` 之前插入：

```js
          function TabBar() {
            return h('div', {
              className: 'dsh-deepfeiyu-tabs',
              role: 'tablist',
              'aria-label': 'Deep肥鱼设置分组',
            },
            SETTINGS_TABS.map(([id, label]) => h('button', {
              key: id,
              type: 'button',
              role: 'tab',
              id: 'dsh-tab-' + id,
              'aria-controls': 'dsh-tabpanel-' + id,
              'aria-selected': tab === id,
              className: 'dsh-deepfeiyu-tab',
              onClick: () => setTab(id),
            }, label)));
          }
```

- [ ] **步骤 5：拆分生命周期 effect 与标签轮询 effect**

把现有这段（`// 依赖状态轮询：挂载立即拉一次…` 到 `}, []);`）整体替换为：

```js
          // 组件生命周期与日志定时器只在挂载/卸载时处理：
          // 拆出来是为了让标签切换的轮询 effect 可以重跑，而安装日志轮询不被打断。
          React.useEffect(() => {
            mounted.current = true;
            return () => {
              mounted.current = false;
              clearLogTimer();
            };
          }, []);

          // 数据轮询跟随当前标签：打开标签立即拉一次，之后每 4s；页面在后台时跳过。
          React.useEffect(() => {
            const tick = () => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              if (tab === 'deps') {
                void loadDeps();
                void loadDshPlugins();
                void loadFusion();
              }
            };
            tick();
            const timer = window.setInterval(tick, POLL_MS);
            return () => window.clearInterval(timer);
          }, [tab]);
```

- [ ] **步骤 6：依赖内容包成 deps 标签，改 return 与标题**

在 `return h('div', { className: 'dsh-deepfeiyu-settings' },` 之前插入：

```js
          function renderDepsTab() {
            return h('div', {
              role: 'tabpanel',
              id: 'dsh-tabpanel-deps',
              'aria-labelledby': 'dsh-tab-deps',
              className: 'dsh-deepfeiyu-tabpanel',
            },
            renderDepsError(),
            deps
              ? h('div', { className: 'dsh-deepfeiyu-dep-list' },
                features.length
                  ? features.map(renderDepRow)
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '控制台未报告任何依赖功能。'))
              : (depsError ? null : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取依赖状态…')),
            renderLog(),
            renderDshPluginsSection(),
            renderFusionSection());
          }
```

把 return 块整体替换为：

```js
          return h('div', { className: 'dsh-deepfeiyu-settings' },
            h('style', null, STYLES),
            h('div', { className: 'dsh-deepfeiyu-heading' },
              h('h2', { className: 'dsh-deepfeiyu-settings-title' }, 'Deep肥鱼 · 设置'),
              h('p', { className: 'dsh-deepfeiyu-meta' }, '控制台的设置、依赖与融合；状态以控制台为准，保存后就地刷新。')),
            h(TabBar),
            tab === 'deps' ? renderDepsTab() : null);
```

- [ ] **步骤 7：运行测试验证通过**

运行：
```powershell
node --check dsh-plugin/client.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；静态测试全部 PASS

- [ ] **步骤 8：Commit**

```bash
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): 设置页标签壳与依赖融合迁移（任务 2/6）"
```

---

## 任务 3：「模型与人格」标签

**文件：**
- 修改：`dsh-plugin/client.js`
- 修改：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试**

在 `def test_js_syntax():` 之前插入：

```python
def test_model_tab_present():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("'模型与人格'", "renderModelTab", "loadSettingsTab", "saveSettingsTab",
                  "switchProfile", "settingsPatchFromDraft", "draftFromSettings",
                  "testAiConnection", "applyPreset", "savePresetAs", "deletePreset",
                  "syncWikiPresets", "aria-pressed", "EMBEDDING_API_KEY",
                  "function Field", "function ToggleRow"):
        assert token in source, f"client.js 缺少 {token}"
    # 保存必须走窄补丁（patch 包装），不得整体回传 settings.get 的结果
    assert "call('settings.put', { patch: settingsPatchFromDraft(draft)" in source
    # 草稿只在为空时初始化，4s 轮询不得覆盖用户正在编辑的输入
    assert "prev || draftFromSettings" in source
    # API Key 草稿初值为空：掩码只做占位，不回填
    assert "api_key: ''," in source
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_model_tab_present -q`
预期：FAIL，报 `client.js 缺少 '模型与人格'`

- [ ] **步骤 3：注册标签与加状态**

`SETTINGS_TABS` 改为：

```js
        const SETTINGS_TABS = [['model', '模型与人格'], ['deps', '依赖与融合']];
```

在 `const [tab, setTab] = React.useState('deps');` 之后插入：

```js
          // 模型与人格标签：settings 是只读快照，draft 是表单草稿；
          // 草稿只在首次加载或保存回执时重建，4s 轮询不会覆盖正在编辑的内容。
          const [settings, setSettings] = React.useState(null);
          const [settingsError, setSettingsError] = React.useState('');
          const [settingsPending, setSettingsPending] = React.useState(false);
          const [settingsFlash, setSettingsFlash] = React.useState('');
          const [draft, setDraft] = React.useState(null);
          const [testResult, setTestResult] = React.useState('');
          const [testPending, setTestPending] = React.useState(false);
          const [presets, setPresets] = React.useState([]);
          const [presetName, setPresetName] = React.useState('');
          const [presetPending, setPresetPending] = React.useState(false);
```

- [ ] **步骤 4：加表单控件组件**

在 `function DeepFeiyuSettingsPage() {` 之前插入（`apply(ctx)` 闭包内、与设置页同层）：

```js
        // 设置表单控件：只做展示，状态一律留在各标签的 draft 里。
        function Field({ label, hint, children }) {
          return h('label', { className: 'dsh-deepfeiyu-field' },
            h('span', null, label),
            children,
            hint ? h('em', null, hint) : null);
        }

        function TextInput({ value, onChange, placeholder, type, min, max }) {
          return h('input', {
            type: type || 'text',
            value,
            placeholder,
            min,
            max,
            onChange: (event) => onChange(event.target.value),
          });
        }

        function TextArea({ value, onChange, rows, placeholder }) {
          return h('textarea', {
            rows: rows || 3,
            value,
            placeholder,
            onChange: (event) => onChange(event.target.value),
          });
        }

        function ToggleRow({ label, hint, checked, onChange }) {
          return h('div', { className: 'dsh-deepfeiyu-toggle-row' },
            h('div', { className: 'dsh-deepfeiyu-toggle-text' },
              h('strong', null, label),
              hint ? h('em', null, hint) : null),
            h('button', {
              type: 'button',
              role: 'switch',
              'aria-checked': Boolean(checked),
              className: 'dsh-deepfeiyu-btn' + (checked ? ' dsh-deepfeiyu-btn-on' : ''),
              onClick: () => onChange(!checked),
            }, checked ? '开' : '关'));
        }
```

- [ ] **步骤 5：实现加载/保存/切档/测试/预设函数**

在 `async function installFeature(feature) {` 之前插入：

```js
          // ------------------------------------------------ 模型与人格
          function draftFromSettings(s) {
            const ai = s.ai || {};
            const mem = s.memory || {};
            const emb = mem.embedding || {};
            return {
              base_url: ai.base_url || '',
              api_key: '',   // 只写：掩码不回填到输入框，占位符展示 ai.api_key
              model: ai.model || '',
              temperature: String(ai.temperature ?? 0.7),
              vision_enabled: !!ai.vision_enabled,
              web_enabled: ai.web_enabled !== false,
              memory_enabled: ai.memory_enabled !== false,
              system_prompt: ai.system_prompt || '',
              persona_name: ai.persona_name || '',
              memory_profile_enabled: mem.profile_enabled !== false,
              memory_context_chars: String(mem.context_chars ?? 1200),
              memory_max_items: String(mem.max_items ?? 24),
              memory_compress_after: String(mem.compress_after_messages ?? 24),
              embedding_enabled: !!emb.enabled,
              embedding_base_url: emb.base_url || '',
              embedding_model: emb.model || '',
              embedding_key_env: emb.key_env || 'EMBEDDING_API_KEY',
            };
          }

          function settingsPatchFromDraft(d) {
            return {
              ai: {
                base_url: d.base_url.trim(),
                api_key: d.api_key.trim(),   // 空串 = 控制台不覆盖真值
                model: d.model.trim(),
                temperature: parseFloat(d.temperature) || 0.7,
                vision_enabled: d.vision_enabled,
                web_enabled: d.web_enabled,
                memory_enabled: d.memory_enabled,
                system_prompt: d.system_prompt.trim(),
                persona_name: d.persona_name.trim(),
              },
              memory: {
                profile_enabled: d.memory_profile_enabled,
                context_chars: parseInt(d.memory_context_chars, 10) || 1200,
                max_items: parseInt(d.memory_max_items, 10) || 24,
                compress_after_messages: parseInt(d.memory_compress_after, 10) || 24,
                embedding: {
                  enabled: d.embedding_enabled,
                  base_url: d.embedding_base_url.trim(),
                  model: d.embedding_model.trim(),
                  key_env: d.embedding_key_env.trim() || 'EMBEDDING_API_KEY',
                },
              },
            };
          }

          async function loadSettingsTab() {
            try {
              const value = await call('settings.get', {}, 15000);
              if (!mounted.current) return;
              setSettings(value);
              // 只在草稿为空（首次进入/保存回执后已由保存函数重建）时初始化，轮询不覆盖编辑
              setDraft((prev) => prev || draftFromSettings(value));
              setSettingsError('');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            }
          }

          async function saveSettingsTab() {
            if (!draft || settingsPending) return;
            setSettingsPending(true);
            setSettingsError('');
            try {
              const value = await call('settings.put', { patch: settingsPatchFromDraft(draft) }, 15000);
              if (!mounted.current) return;
              const saved = value && typeof value === 'object' ? value.settings : null;
              if (saved) {
                setSettings(saved);
                setDraft(draftFromSettings(saved));
              }
              setSettingsFlash('已保存 ' + new Date().toLocaleTimeString());
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setSettingsPending(false);
            }
          }

          async function switchProfile(id) {
            if (!draft || !settings || settingsPending) return;
            const ai = settings.ai || {};
            if (!id || id === (ai.active_profile || '')) return;
            const base = draft.base_url.trim();
            const model = draft.model.trim();
            setSettingsPending(true);
            setSettingsError('');
            try {
              // 与控制台一致：未保存的地址/模型先落回当前档，再切档，避免手改内容被吞
              if ((base && base !== (ai.base_url || '')) || (model && model !== (ai.model || ''))) {
                const flush = {};
                if (base) flush.base_url = base;
                if (model) flush.model = model;
                await call('settings.put', { patch: { ai: flush } }, 15000);
              }
              const value = await call('settings.put', { patch: { ai: { active_profile: id } } }, 15000);
              if (!mounted.current) return;
              const saved = value && typeof value === 'object' ? value.settings : null;
              if (saved) {
                setSettings(saved);
                setDraft(draftFromSettings(saved));
              }
              setSettingsFlash('已切到「' + id + '」');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setSettingsPending(false);
            }
          }

          async function testAiConnection() {
            if (!draft || testPending) return;
            setTestPending(true);
            setTestResult('正在测试…');
            try {
              const value = await call('settings.testAi', {
                base_url: draft.base_url.trim(),
                api_key: draft.api_key.trim() || undefined,
                model: draft.model.trim(),
              }, 30000);
              if (!mounted.current) return;
              const latency = value && Number.isFinite(Number(value.latency_ms))
                ? Number(value.latency_ms) : 0;
              const reply = value && value.reply ? value.reply : 'ok';
              setTestResult('连接正常 · ' + latency + 'ms · ' + reply);
              // 控制台 test-ai 会先落盘再测试：刷新快照以更新「已配置」等标记
              await loadSettingsTab();
            } catch (error0) {
              if (mounted.current) setTestResult(errorMessage(error0));
            } finally {
              if (mounted.current) setTestPending(false);
            }
          }

          async function loadPresets() {
            try {
              const value = await call('personas.list', {}, 15000);
              if (!mounted.current) return;
              setPresets(value && Array.isArray(value.personas) ? value.personas : []);
            } catch {
              // 预设读不到只置空：该区显示 0 份，不影响其它字段编辑
              if (mounted.current) setPresets([]);
            }
          }

          function applyPreset(name) {
            const found = presets.find((item) => item.name === name);
            if (!found || !draft) return;
            setPresetName(name);
            setDraft((prev) => (prev
              ? { ...prev, system_prompt: found.system_prompt || '', persona_name: found.persona_name || '' }
              : prev));
            setSettingsFlash('已应用预设「' + name + '」，点「保存设置」生效');
          }

          async function savePresetAs() {
            const name = presetName.trim();
            if (!draft || presetPending) return;
            if (!name) {
              setSettingsError('先在预设名称里写一个名字');
              return;
            }
            const exists = presets.some((item) => item.name === name);
            if (exists && !window.confirm('覆盖已有预设「' + name + '」？')) return;
            setPresetPending(true);
            setSettingsError('');
            try {
              await call('personas.add', {
                name,
                persona_name: draft.persona_name.trim(),
                system_prompt: draft.system_prompt.trim(),
              }, 15000);
              await loadPresets();
              setSettingsFlash('已保存预设「' + name + '」');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setPresetPending(false);
            }
          }

          async function deletePreset(name) {
            if (!name || presetPending) return;
            if (!window.confirm('删除人格预设「' + name + '」？')) return;
            setPresetPending(true);
            setSettingsError('');
            try {
              await call('personas.delete', { name }, 15000);
              if (presetName === name) setPresetName('');
              await loadPresets();
              setSettingsFlash('已删除预设「' + name + '」');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setPresetPending(false);
            }
          }

          async function syncWikiPresets() {
            if (presetPending) return;
            setPresetPending(true);
            setSettingsError('');
            try {
              const value = await call('personas.syncWiki', {}, 60000);
              await loadPresets();
              const created = value && Number.isFinite(Number(value.created)) ? Number(value.created) : 0;
              const updated = value && Number.isFinite(Number(value.updated)) ? Number(value.updated) : 0;
              setSettingsFlash('图集预设同步：新建 ' + created + ' 份、更新 ' + updated + ' 份');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setPresetPending(false);
            }
          }
```

- [ ] **步骤 6：轮询分支与标签渲染**

轮询 tick 的 deps 分支之后加：

```js
              if (tab === 'model') {
                void loadSettingsTab();
                void loadPresets();
              }
```

在 `function renderDepsTab() {` 之前插入：

```js
          function renderModelTab() {
            const panel = { role: 'tabpanel', id: 'dsh-tabpanel-model', 'aria-labelledby': 'dsh-tab-model', className: 'dsh-deepfeiyu-tabpanel' };
            const ai = settings ? settings.ai || {} : null;
            const profiles = ai && Array.isArray(ai.profiles) ? ai.profiles : [];
            const activeId = ai ? ai.active_profile || '' : '';
            if (!draft) {
              return h('div', panel,
                settingsError
                  ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                    h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                    settingsButton('重试', () => { void loadSettingsTab(); }, {}))
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取设置…'));
            }
            const set = (key, value) => setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
            const active = profiles.find((item) => item.id === activeId) || null;
            const keyEnv = (active && active.key_env) || 'DEEPSEEK_API_KEY';
            return h('div', panel,
              settingsError
                ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                  h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                  settingsButton('关闭', () => setSettingsError(''), { title: '收起错误提示' }))
                : null,
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '模型档'),
                  h('span', { className: 'dsh-deepfeiyu-badge' }, ai && ai.configured ? '已配置' : '未配置')),
                h('div', { className: 'dsh-deepfeiyu-chips' },
                  profiles.map((item) => h('button', {
                    key: item.id,
                    type: 'button',
                    className: 'dsh-deepfeiyu-chip'
                      + (item.id === activeId ? ' on' : '')
                      + (item.configured ? '' : ' warn'),
                    'aria-pressed': item.id === activeId,
                    disabled: settingsPending,
                    title: [item.description, item.base_url, item.model,
                      '密钥：.env 的 ' + (item.key_env || 'DEEPSEEK_API_KEY'),
                    ].filter(Boolean).join(' · '),
                    onClick: () => { void switchProfile(item.id); },
                  }, (item.name || item.id) + (item.configured ? '' : ' · 未填 Key')))),
                active
                  ? h('p', { className: 'dsh-deepfeiyu-hint' },
                    (active.name || active.id) + '：' + (active.description || '')
                    + '（密钥读 .env 的 ' + keyEnv + '）')
                  : null,
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: 'Base URL' },
                    h(TextInput, { value: draft.base_url, onChange: (v) => set('base_url', v), placeholder: 'https://api.deepseek.com' })),
                  h(Field, { label: 'API Key', hint: '只写入项目根 .env 的 ' + keyEnv + '，不进 data/config.json；留空不改' },
                    h(TextInput, { value: draft.api_key, onChange: (v) => set('api_key', v), placeholder: ai && ai.api_key ? ai.api_key : 'sk-…' })),
                  h(Field, { label: '模型名', hint: '这一档实际发给服务商的模型名，改了只覆盖这一档' },
                    h(TextInput, { value: draft.model, onChange: (v) => set('model', v), placeholder: 'deepseek-chat' })),
                  h(Field, { label: '温度' },
                    h(TextInput, { type: 'number', value: draft.temperature, onChange: (v) => set('temperature', v), min: 0, max: 2 }))),
                h('div', { className: 'dsh-deepfeiyu-form-actions' },
                  h('span', { className: 'dsh-deepfeiyu-hint' }, testResult),
                  settingsButton(testPending ? '测试中…' : '测试连接', () => { void testAiConnection(); }, {
                    disabled: testPending,
                    title: '用当前表单值先落盘，再发一次两字小请求测连通与延迟',
                  }))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '能力与记忆')),
                h(ToggleRow, { label: '启用视觉理解', hint: '多模态识图', checked: draft.vision_enabled, onChange: (v) => set('vision_enabled', v) }),
                h(ToggleRow, { label: '启用联网搜索', hint: 'AI 可搜索网页并抓取正文', checked: draft.web_enabled, onChange: (v) => set('web_enabled', v) }),
                h(ToggleRow, { label: '启用长期记忆', checked: draft.memory_enabled, onChange: (v) => set('memory_enabled', v) }),
                h(ToggleRow, { label: '注入人物档案', hint: '按重要度/相关度把档案带进提示词', checked: draft.memory_profile_enabled, onChange: (v) => set('memory_profile_enabled', v) }),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '记忆上下文长度（字符）' },
                    h(TextInput, { type: 'number', value: draft.memory_context_chars, onChange: (v) => set('memory_context_chars', v), min: 200 })),
                  h(Field, { label: '每层最大条数' },
                    h(TextInput, { type: 'number', value: draft.memory_max_items, onChange: (v) => set('memory_max_items', v), min: 1 })),
                  h(Field, { label: '压缩阈值（条）' },
                    h(TextInput, { type: 'number', value: draft.memory_compress_after, onChange: (v) => set('memory_compress_after', v), min: 1 })))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '语义检索（Embedding）')),
                h(ToggleRow, { label: '启用语义检索', hint: '需要一个 OpenAI 兼容的 /embeddings 接口', checked: draft.embedding_enabled, onChange: (v) => set('embedding_enabled', v) }),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: 'Embedding 接口地址' },
                    h(TextInput, { value: draft.embedding_base_url, onChange: (v) => set('embedding_base_url', v), placeholder: 'https://api.deepseek.com' })),
                  h(Field, { label: 'Embedding 模型' },
                    h(TextInput, { value: draft.embedding_model, onChange: (v) => set('embedding_model', v), placeholder: 'text-embedding-v3' })),
                  h(Field, { label: '密钥环境变量', hint: '只读环境变量，不写进 config.json' },
                    h(TextInput, { value: draft.embedding_key_env, onChange: (v) => set('embedding_key_env', v), placeholder: 'EMBEDDING_API_KEY' })))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '人设与人格预设')),
                h(Field, { label: '系统提示词（全局默认）', hint: '各账号可在账号表单里单独覆盖（P2 标签）' },
                  h(TextArea, { value: draft.system_prompt, onChange: (v) => set('system_prompt', v), rows: 4, placeholder: '你是一个…' })),
                h(Field, { label: '人格名', hint: 'AI 的人格称呼，随人设一起注入提示词' },
                  h(TextInput, { value: draft.persona_name, onChange: (v) => set('persona_name', v), placeholder: 'Deep肥鱼' })),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '选择已保存的人格' },
                    h('select', {
                      value: presetName,
                      onChange: (event) => setPresetName(event.target.value),
                    },
                    h('option', { value: '' }, '— 选择已保存的人格 —'),
                    presets.map((item) => h('option', { key: item.name, value: item.name },
                      (item.persona_name && item.persona_name !== item.name
                        ? item.name + '（' + item.persona_name + '）' : item.name)
                      + (item.source === 'ai-moe-wiki' ? '·图集' : ''))))),
                  h(Field, { label: '预设名称', hint: '选择或新建时用这个名字' },
                    h(TextInput, { value: presetName, onChange: setPresetName, placeholder: '例如：工作号人格' }))),
                h('div', { className: 'dsh-deepfeiyu-form-actions' },
                  h('span', { className: 'dsh-deepfeiyu-badge' }, presets.length + ' 份'),
                  settingsButton('应用所选', () => applyPreset(presetName.trim()), {
                    disabled: !presetName.trim() || presetPending,
                    title: '把预设填入上方提示词与人格名（需再点保存设置）',
                  }),
                  settingsButton(presetPending ? '处理中…' : '保存为预设', () => { void savePresetAs(); }, {
                    disabled: presetPending,
                    title: '把当前提示词与人格名存成预设；同名先确认覆盖',
                  }),
                  settingsButton('删除', () => { void deletePreset(presetName.trim()); }, {
                    disabled: presetPending || !presetName.trim(),
                  }),
                  settingsButton('从图集同步', () => { void syncWikiPresets(); }, {
                    disabled: presetPending,
                    title: '从 ai-moe-wiki 图集生成/更新预设，跳过用户自建同名',
                  }))),
              h('div', { className: 'dsh-deepfeiyu-form-actions' },
                h('span', { className: 'dsh-deepfeiyu-hint' }, settingsFlash),
                settingsButton(settingsPending ? '保存中…' : '保存设置', () => { void saveSettingsTab(); }, {
                  primary: true,
                  disabled: settingsPending || !draft,
                  title: '只提交本页字段的窄补丁，其它配置不受影响',
                })));
          }
```

在 return 块里 `tab === 'deps' ? renderDepsTab() : null);` 改为：

```js
            tab === 'model' ? renderModelTab() : null,
            tab === 'deps' ? renderDepsTab() : null);
```

- [ ] **步骤 7：运行测试验证通过**

运行：
```powershell
node --check dsh-plugin/client.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；全部静态测试 PASS

- [ ] **步骤 8：Commit**

```bash
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): 设置页模型与人格标签（任务 3/6）"
```

---

## 任务 4：「自动回复」标签

**文件：**
- 修改：`dsh-plugin/client.js`
- 修改：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试**

在 `def test_js_syntax():` 之前插入：

```python
def test_auto_tab_present():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("'自动回复'", "renderAutoTab", "autoFromSettings", "autoPatchFromDraft",
                  "saveAutoTab", "splitLines", "visit_gap_sec", "split_reply",
                  "chuanzi_site_emoji", "only_whitelist", "cross_daily_limit"):
        assert token in source, f"client.js 缺少 {token}"
    # 自动回复保存也必须是窄补丁，且与模型草稿分开
    assert "call('settings.put', { patch: autoPatchFromDraft(autoDraft)" in source
    # 加载时同时播种两个草稿，互不覆盖
    assert "prev || autoFromSettings" in source
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_auto_tab_present -q`
预期：FAIL，报 `client.js 缺少 '自动回复'`

- [ ] **步骤 3：注册标签与加状态**

`SETTINGS_TABS` 改为：

```js
        const SETTINGS_TABS = [['model', '模型与人格'], ['auto', '自动回复'], ['deps', '依赖与融合']];
```

在 `const [presetPending, setPresetPending] = React.useState(false);` 之后插入：

```js
          // 自动回复标签：独立草稿（与模型草稿互不覆盖），错误/保存中状态与模型标签共用
          // ——两者读写同一个 GET/PUT /api/settings，同一个错误态在两个标签里各显各的。
          const [autoDraft, setAutoDraft] = React.useState(null);
          const [autoPending, setAutoPending] = React.useState(false);
```

- [ ] **步骤 4：实现自动回复草稿/保存/渲染**

在 `async function syncWikiPresets() {` … 结束之后（即 `// --------------------------------- 模型与人格` 区块的最后一个函数之后）、`async function installFeature(feature) {` 之前插入：

```js
          // ------------------------------------------------ 自动回复
          function splitLines(value) {
            return String(value || '')
              .split('\n')
              .map((item) => item.trim())
              .filter(Boolean);
          }

          function autoFromSettings(s) {
            const a = s.auto || {};
            const ai = s.ai || {};
            const c = s.cross || {};
            return {
              enabled: !!a.enabled,
              default_ai_on: !!a.default_ai_on,
              group_need_at: !!a.group_need_at,
              emoji_reply: a.emoji_reply !== false,
              pat_reply: a.pat_reply !== false,
              instant_reply: !!a.instant_reply,
              only_whitelist: !!a.only_whitelist,
              whitelist: (a.whitelist || []).join('\n'),
              blacklist: (a.blacklist || []).join('\n'),
              min_interval_sec: String(a.min_interval_sec ?? 3),
              daily_limit: String(a.daily_limit ?? 300),
              takeover_pause_min: String(a.takeover_pause_min ?? 10),
              prefix: a.prefix || '',
              reply_timeout_sec: String(a.reply_timeout_sec ?? 60),
              timeout_fallback: a.timeout_fallback || '',
              split_reply: !!a.split_reply,
              emoji_with_reply: ai.emoji_with_reply !== false,
              emoji_auto_collect: ai.emoji_auto_collect !== false,
              wiki_emoji_only: !!ai.wiki_emoji_only,
              chuanzi_mode: !!ai.chuanzi_mode,
              chuanzi_site_emoji: !!ai.chuanzi_site_emoji,
              cross_enabled: c.enabled !== false,
              cross_relay: c.relay !== false,
              cross_visit: c.visit !== false,
              cross_daily_limit: String(c.daily_limit ?? 30),
              cross_visit_gap_min: String(Math.round((c.visit_gap_sec ?? 600) / 60)),
            };
          }

          function autoPatchFromDraft(d) {
            return {
              auto: {
                enabled: d.enabled,
                default_ai_on: d.default_ai_on,
                group_need_at: d.group_need_at,
                emoji_reply: d.emoji_reply,
                pat_reply: d.pat_reply,
                instant_reply: d.instant_reply,
                only_whitelist: d.only_whitelist,
                whitelist: splitLines(d.whitelist),
                blacklist: splitLines(d.blacklist),
                min_interval_sec: parseInt(d.min_interval_sec, 10) || 0,
                daily_limit: parseInt(d.daily_limit, 10) || 0,
                takeover_pause_min: parseInt(d.takeover_pause_min, 10) || 0,
                prefix: d.prefix,
                reply_timeout_sec: parseFloat(d.reply_timeout_sec) || 0,
                timeout_fallback: d.timeout_fallback.trim(),
                split_reply: d.split_reply,
              },
              ai: {
                emoji_with_reply: d.emoji_with_reply,
                emoji_auto_collect: d.emoji_auto_collect,
                wiki_emoji_only: d.wiki_emoji_only,
                chuanzi_mode: d.chuanzi_mode,
                chuanzi_site_emoji: d.chuanzi_site_emoji,
              },
              cross: {
                enabled: d.cross_enabled,
                relay: d.cross_relay,
                visit: d.cross_visit,
                daily_limit: parseInt(d.cross_daily_limit, 10) || 0,
                visit_gap_sec: (parseInt(d.cross_visit_gap_min, 10) || 0) * 60,
              },
            };
          }

          async function saveAutoTab() {
            if (!autoDraft || autoPending) return;
            setAutoPending(true);
            setSettingsError('');
            try {
              const value = await call('settings.put', { patch: autoPatchFromDraft(autoDraft) }, 15000);
              if (!mounted.current) return;
              const saved = value && typeof value === 'object' ? value.settings : null;
              if (saved) {
                setSettings(saved);
                setAutoDraft(autoFromSettings(saved));
              }
              setSettingsFlash('已保存 ' + new Date().toLocaleTimeString());
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setAutoPending(false);
            }
          }
```

同时把 `loadSettingsTab` 里的播种改成两个草稿（该函数已在任务 3 写好，这里在其 `setDraft((prev) => prev || draftFromSettings(value));` 之后追加一行）：

```js
              setAutoDraft((prev) => prev || autoFromSettings(value));
```

- [ ] **步骤 5：轮询分支与标签渲染**

轮询 tick 的 model 分支之后加：

```js
              if (tab === 'auto') void loadSettingsTab();
```

在 `function renderModelTab() {` 之前插入（`renderAutoTab` 完整实现）：

```js
          function renderAutoTab() {
            const panel = { role: 'tabpanel', id: 'dsh-tabpanel-auto', 'aria-labelledby': 'dsh-tab-auto', className: 'dsh-deepfeiyu-tabpanel' };
            if (!autoDraft) {
              return h('div', panel,
                settingsError
                  ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                    h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                    settingsButton('重试', () => { void loadSettingsTab(); }, {}))
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取设置…'));
            }
            const set = (key, value) => setAutoDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
            return h('div', panel,
              settingsError
                ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                  h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                  settingsButton('关闭', () => setSettingsError(''), { title: '收起错误提示' }))
                : null,
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '自动回复策略')),
                h(ToggleRow, { label: '自动回复总开关', hint: '关着不调模型、不注入、不判断心情', checked: autoDraft.enabled, onChange: (v) => set('enabled', v) }),
                h(ToggleRow, { label: '新会话默认开 AI', checked: autoDraft.default_ai_on, onChange: (v) => set('default_ai_on', v) }),
                h(ToggleRow, { label: '群聊需 @ 才回', hint: '按账号配置的昵称检测', checked: autoDraft.group_need_at, onChange: (v) => set('group_need_at', v) }),
                h(ToggleRow, { label: '表情回复', checked: autoDraft.emoji_reply, onChange: (v) => set('emoji_reply', v) }),
                h(ToggleRow, { label: '拍一拍也回', checked: autoDraft.pat_reply, onChange: (v) => set('pat_reply', v) }),
                h(ToggleRow, { label: '仅白名单回复', checked: autoDraft.only_whitelist, onChange: (v) => set('only_whitelist', v) }),
                h(Field, { label: '白名单（每行一个）' },
                  h(TextArea, { value: autoDraft.whitelist, onChange: (v) => set('whitelist', v), rows: 3, placeholder: '张三\n工作群' })),
                h(Field, { label: '黑名单（每行一个）' },
                  h(TextArea, { value: autoDraft.blacklist, onChange: (v) => set('blacklist', v), rows: 3, placeholder: '永远不回的会话' })),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '最短回复间隔（秒）' },
                    h(TextInput, { type: 'number', value: autoDraft.min_interval_sec, onChange: (v) => set('min_interval_sec', v), min: 0 })),
                  h(Field, { label: '每日回复上限' },
                    h(TextInput, { type: 'number', value: autoDraft.daily_limit, onChange: (v) => set('daily_limit', v), min: 0 })),
                  h(Field, { label: '人工接管暂停（分钟）', hint: '你亲自回一句后 AI 退让的时长' },
                    h(TextInput, { type: 'number', value: autoDraft.takeover_pause_min, onChange: (v) => set('takeover_pause_min', v), min: 0 })),
                  h(Field, { label: '回复前缀（可空）' },
                    h(TextInput, { value: autoDraft.prefix, onChange: (v) => set('prefix', v), placeholder: '例如：[AI]' })),
                  h(Field, { label: '回复超时（秒，0 = 不限制）' },
                    h(TextInput, { type: 'number', value: autoDraft.reply_timeout_sec, onChange: (v) => set('reply_timeout_sec', v), min: 0 })),
                  h(Field, { label: '超时兜底话（可空）' },
                    h(TextInput, { value: autoDraft.timeout_fallback, onChange: (v) => set('timeout_fallback', v), placeholder: '稍等，我想想怎么回你' }))),
                h(ToggleRow, { label: '一条回复拆成多条发送', hint: 'AI 可用 --- 分隔拆成最多 3 条短消息', checked: autoDraft.split_reply, onChange: (v) => set('split_reply', v) })),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '表情包与串子')),
                h(ToggleRow, { label: '回复必带表情包', hint: '按场景从表情库自选', checked: autoDraft.emoji_with_reply, onChange: (v) => set('emoji_with_reply', v) }),
                h(ToggleRow, { label: '自动收集聊天表情', checked: autoDraft.emoji_auto_collect, onChange: (v) => set('emoji_auto_collect', v) }),
                h(ToggleRow, { label: '仅 AI 娘表情', hint: '只允许 tags 带图鉴角色名的表情', checked: autoDraft.wiki_emoji_only, onChange: (v) => set('wiki_emoji_only', v) }),
                h(ToggleRow, { label: '串子模式', hint: '回复像串子；只对准观点与梗', checked: autoDraft.chuanzi_mode, onChange: (v) => set('chuanzi_mode', v) }),
                h(ToggleRow, { label: '串子站表情', hint: '联网查台词库并入库截图，默认关', checked: autoDraft.chuanzi_site_emoji, onChange: (v) => set('chuanzi_site_emoji', v) })),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '跨对话沟通')),
                h(ToggleRow, { label: '跨对话总开关', hint: '转告/代发与主动串门', checked: autoDraft.cross_enabled, onChange: (v) => set('cross_enabled', v) }),
                h(ToggleRow, { label: '允许转告 / 代发', checked: autoDraft.cross_relay, onChange: (v) => set('cross_relay', v) }),
                h(ToggleRow, { label: '允许主动串门', checked: autoDraft.cross_visit, onChange: (v) => set('cross_visit', v) }),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '每日跨对话上限（0 = 不限）' },
                    h(TextInput, { type: 'number', value: autoDraft.cross_daily_limit, onChange: (v) => set('cross_daily_limit', v), min: 0 })),
                  h(Field, { label: '串门冷却（分钟）' },
                    h(TextInput, { type: 'number', value: autoDraft.cross_visit_gap_min, onChange: (v) => set('cross_visit_gap_min', v), min: 0 })))),
              h('div', { className: 'dsh-deepfeiyu-form-actions' },
                h('span', { className: 'dsh-deepfeiyu-hint' }, settingsFlash),
                settingsButton(autoPending ? '保存中…' : '保存设置', () => { void saveAutoTab(); }, {
                  primary: true,
                  disabled: autoPending || !autoDraft,
                  title: '只提交本页字段的窄补丁，模型与账号设置不受影响',
                })));
          }
```

在 return 块里加标签分支：

```js
            tab === 'model' ? renderModelTab() : null,
            tab === 'auto' ? renderAutoTab() : null,
            tab === 'deps' ? renderDepsTab() : null);
```

- [ ] **步骤 6：运行测试验证通过**

运行：
```powershell
node --check dsh-plugin/client.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；全部静态测试 PASS

- [ ] **步骤 7：Commit**

```bash
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): 设置页自动回复标签（任务 4/6）"
```

---

## 任务 5：「更新与边界」标签

**文件：**
- 修改：`dsh-plugin/client.js`
- 修改：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试**

在 `def test_js_syntax():` 之前插入：

```python
def test_update_tab_present():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("'更新与边界'", "renderUpdateTab", "checkUpdateTab", "update.check",
                  "has_update", "git_repo", "使用边界", "《最终用户许可协议》",
                  "updateFetched", "openExternal"):
        assert token in source, f"client.js 缺少 {token}"
    # 更新检查只在首次打开时自动跑一次，之后只手动（不每 4s 打 Gitee）
    assert "tab === 'update' && !updateFetched.current" in source
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_update_tab_present -q`
预期：FAIL，报 `client.js 缺少 '更新与边界'`

- [ ] **步骤 3：注册标签与加状态**

`SETTINGS_TABS` 改为（最终态）：

```js
        const SETTINGS_TABS = [
          ['model', '模型与人格'],
          ['auto', '自动回复'],
          ['deps', '依赖与融合'],
          ['update', '更新与边界'],
        ];
```

在 `const [autoPending, setAutoPending] = React.useState(false);` 之后插入：

```js
          // 更新标签：首次打开自动查一次，之后只手动（避免每 4s 打 Gitee）。
          const [update, setUpdate] = React.useState(null);
          const [updateError, setUpdateError] = React.useState('');
          const [updatePending, setUpdatePending] = React.useState(false);
          const [consoleBase, setConsoleBase] = React.useState('http://127.0.0.1:8765');
          const updateFetched = React.useRef(false);
```

- [ ] **步骤 4：实现检查/渲染函数**

在 `async function saveAutoTab() {` … 结束之后、`async function installFeature(feature) {` 之前插入：

```js
          // ------------------------------------------------ 更新与边界
          async function checkUpdateTab() {
            if (updatePending) return;
            setUpdatePending(true);
            setUpdateError('');
            try {
              const value = await call('update.check', {}, 30000);
              if (!mounted.current) return;
              setUpdate(value && typeof value === 'object' ? value : null);
            } catch (error0) {
              if (mounted.current) setUpdateError(errorMessage(error0));
            } finally {
              if (mounted.current) setUpdatePending(false);
            }
          }

          async function readConsoleBase() {
            try {
              const value = await call('state', {}, 5000);
              if (mounted.current && value && value.host) {
                setConsoleBase('http://' + value.host + ':' + (value.port || 8765));
              }
            } catch {
              // 面板快照读不到时保留默认 8765，协议入口仍可用
            }
          }

          function openExternal(url) {
            const sidebarRight = ctx.get('sidebarRight');
            if (sidebarRight && typeof sidebarRight.openTab === 'function') {
              sidebarRight.openTab('browser', { params: { url } });
              return;
            }
            window.open(url, '_blank', 'noopener');
          }
```

- [ ] **步骤 5：轮询分支与标签渲染**

轮询 tick 的 auto 分支之后加：

```js
              if (tab === 'update' && !updateFetched.current) {
                updateFetched.current = true;
                void checkUpdateTab();
                void readConsoleBase();
              }
```

在 `function renderAutoTab() {` 之前插入：

```js
          function renderUpdateTab() {
            const panel = { role: 'tabpanel', id: 'dsh-tabpanel-update', 'aria-labelledby': 'dsh-tab-update', className: 'dsh-deepfeiyu-tabpanel' };
            const r = update;
            let versionLine;
            if (updateError) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' }, updateError);
            } else if (!r) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-hint' }, '正在检查更新…');
            } else if (!r.git_repo) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-hint' },
                '当前 v' + (r.version || '—') + '（解压安装的副本不做在线比对）');
            } else if (r.error) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' },
                '检查更新失败：' + r.error);
            } else if (r.has_update) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '发现新版本 ' + (r.remote_short || '')
                + '（当前 v' + (r.version || '未知') + ' · ' + (r.local_short || '')
                + '）：在项目目录运行 git pull 后重启服务。');
            } else {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '当前 v' + (r.version || '—') + (r.local_short ? ' · ' + r.local_short : '')
                + '，已是最新版本');
            }
            return h('div', panel,
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '软件更新')),
                versionLine,
                h('div', { className: 'dsh-deepfeiyu-form-actions' },
                  r && r.repo_url
                    ? settingsButton('打开仓库', () => openExternal(String(r.repo_url)), {
                      title: '在 DSH 右侧栏打开仓库页面',
                    })
                    : null,
                  settingsButton(updatePending ? '检查中…' : '检查更新', () => { void checkUpdateTab(); }, {
                    disabled: updatePending,
                    title: '与 Gitee 仓库的 master 分支比对',
                  }))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '使用边界')),
                h('ul', { className: 'dsh-deepfeiyu-risklist' },
                  h('li', null, h('strong', null, '账号风险：'),
                    '个人微信自动化不符合《微信个人账号使用规范》，存在被限制或封禁的可能，建议用小号先验证。'),
                  h('li', null, h('strong', null, '版本限制：'),
                    '本地 Hook 仅支持微信 PC 3.9.x；4.x 收消息走数据库解密，发送走前台窗口自动化（会短暂占用前台）。'),
                  h('li', null, h('strong', null, '能力边界：'),
                    '朋友圈、支付、小程序、语音视频通话不在 Hook 能力范围内。'),
                  h('li', null, h('strong', null, '数据：'),
                    '所有消息与配置只存在本机 data/ 目录，不同账号的数据按账号隔离。'),
                  h('li', null, h('strong', null, '协议：'),
                    '使用本软件即表示同意 ',
                    settingsButton('《最终用户许可协议》', () => openExternal(consoleBase + '/eula'), {
                      title: '在 DSH 右侧栏打开控制台协议页',
                    }),
                    '；英文译本仅供参考，以中文版为准。'))));
          }
```

在 return 块里加标签分支（最终四个分支）：

```js
            tab === 'model' ? renderModelTab() : null,
            tab === 'auto' ? renderAutoTab() : null,
            tab === 'deps' ? renderDepsTab() : null,
            tab === 'update' ? renderUpdateTab() : null);
```

- [ ] **步骤 6：运行测试验证通过**

运行：
```powershell
node --check dsh-plugin/client.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；全部静态测试 PASS

- [ ] **步骤 7：Commit**

```bash
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): 设置页更新与边界标签（任务 5/6）"
```

---

## 任务 6：文档、全量测试与验收

**文件：**
- 修改：`dsh-plugin/README.md`、`README.md`、`docs/使用说明.md`、`CHANGELOG.md`

- [ ] **步骤 1：更新 dsh-plugin README**

把 `dsh-plugin/README.md` 的 `## 设置页：依赖环境` 一节整节替换为：

```markdown
## 设置页：分标签的完整设置

在 Harness「设置 → Deep肥鱼」里用页内标签管理（P1 四个，P2 将加入「账号与通道」）：

| 标签 | 内容 | 数据来源 |
|---|---|---|
| 模型与人格 | 模型档切换、Base URL/模型/Key、温度、视觉/联网/记忆开关、记忆 v2 参数、Embedding、系统提示词、人格名、人格预设（应用/保存/删除/图集同步）、测试连接 | `GET/PUT /api/settings`、`/api/settings/test-ai`、`/api/personas*` |
| 自动回复 | 自动回复策略、表情包与串子、跨对话 | `GET/PUT /api/settings`（窄补丁） |
| 依赖与融合 | 六个可选功能依赖一键安装、关联 DSH 插件、心情与桌宠融合 | 既有 `deps.*` / `dshPlugins.*` / `fusion.*` |
| 更新与边界 | 检查更新、使用边界说明 | `GET /api/update/check` |

要点：

- 标签懒加载：打开哪个标签才拉哪个标签的数据，之后每 4s 轮询当前标签；切标签不打断安装日志轮询。
- **只发窄补丁**：保存只提交该标签表单涉及的字段，绝不把 `GET /api/settings` 的返回原样 PUT 回去（其中含 `configured`、档位投影与掩码串）。
- 密钥沿用控制台契约：API Key 只回显掩码与占位符，空值不覆盖；`••••` 掩码串原样回传由控制台还原真值。
- 控制台是唯一状态源：保存成功后用服务端返回的配置重建草稿；单标签加载/保存失败只在该标签内报错。
- 控制台未运行时标签内显示原因与「启动控制台」按钮，不静默失败。

Host 侧新增 8 个同源代理端点：`settings.get / settings.put / settings.testAi / personas.list / personas.add / personas.delete / personas.syncWiki / update.check`；`settings.put` 只接受对象形态的 `patch`。控制台侧本机来源限制与令牌校验不变。
```

（原「可选功能依赖」「关联 DSH 插件」「融合」三节保留不动，它们并入「依赖与融合」标签后仍然描述同一功能。）

- [ ] **步骤 2：更新根 README、使用说明与 CHANGELOG**

`README.md` 第 7.5 节介绍段里，把：

```markdown
`设置 → Deep肥鱼` 里还有一页依赖环境（六个功能的依赖状态与一键安装）、「关联 DSH 插件」与「心情与桌宠（融合）」（与微信共享同一份心情/一只桌宠；DSH 回合结束用默认模型判断并写回控制台，右下角挂饰与 Harness 提示词都读这份共享状态），Harness 里的智能体拿到 8 个 `deepfeiyu_*` 工具，插件还能按需拉起/停止本仓库的控制台进程。
```

替换为：

```markdown
`设置 → Deep肥鱼` 是分标签的完整设置页（模型与人格 / 自动回复 / 依赖与融合 / 更新与边界，P2 再加账号与通道）：模型档、人格预设、自动回复策略与控制台同一份数据，保存走窄补丁、密钥只回显掩码；「依赖与融合」标签含六个功能依赖一键安装、「关联 DSH 插件」与「心情与桌宠（融合）」（与微信共享同一份心情/一只桌宠；DSH 回合结束用默认模型判断并写回控制台，右下角挂饰与 Harness 提示词都读这份共享状态）。Harness 里的智能体拿到 8 个 `deepfeiyu_*` 工具，插件还能按需拉起/停止本仓库的控制台进程。
```

`docs/使用说明.md` 16.1 的这一行：

```markdown
- 设置页「Deep肥鱼」：列出核心依赖、微信 4.x、朋友圈、桌宠、WCFerry、B 站六项的依赖状态，可一键安装并在页内看安装日志；控制台未运行时页内直接给出「启动控制台」按钮。
```

替换为：

```markdown
- 设置页「Deep肥鱼」：页内分「模型与人格 / 自动回复 / 依赖与融合 / 更新与边界」四个标签（P2 再加「账号与通道」）。模型档、人格预设、自动回复策略与控制台是同一份数据，保存走窄补丁、密钥只回显掩码；依赖标签列出核心依赖、微信 4.x、朋友圈、桌宠、WCFerry、B 站六项并可一键安装与看日志；控制台未运行时页内直接给出「启动控制台」按钮。
```

`CHANGELOG.md` 在 `## [未发布]` 内、`### 2026-11-02 · 桌宠与心情双端融合` 一节之前插入：

```markdown
### 2026-11-02 · 完整设置缝进插件设置（P1）

- DSH「设置 → Deep肥鱼」改为页内标签：**模型与人格 / 自动回复 / 依赖与融合 / 更新与边界**（P2 将加入账号与通道）。依赖与融合三块（可选功能依赖、关联 DSH 插件、心情与桌宠融合）原样迁入「依赖与融合」标签，懒加载改为"打开标签才拉、之后只轮询当前标签"，切标签不再打断安装日志轮询。
- Host 新增 8 个同源代理端点：`settings.get / settings.put / settings.testAi / personas.list / personas.add / personas.delete / personas.syncWiki / update.check`；`settings.put` 只接受对象形态的窄补丁，拒绝把任意值透传给配置接口。
- 模型与人格标签：模型档切换（切档前先落回未保存的地址/模型，与控制台一致）、Base URL/模型/Key/温度、视觉/联网/记忆开关、记忆 v2 参数、Embedding、系统提示词、人格名、人格预设（应用/保存/删除/图集同步）、测试连接。
- 自动回复标签：总开关、群聊 @、拍一拍、白/黑名单、间隔/日上限、接管暂停、前缀/超时/兜底、拆分回复、表情包与串子、跨对话；保存只发本页字段的窄补丁，与模型草稿互不覆盖。
- 更新与边界标签：首次打开自动检查一次后仅手动（不每 4s 打 Gitee）、仓库入口走宿主侧栏浏览器、使用边界静态文案与协议入口。
- 密钥契约不变：API Key 只回显掩码与占位符、空值不覆盖；掩码串原样回传由控制台 `_unmask_secrets` 还原——新增 `tests/test_dsh_settings_contract.py` 把这三条与端点登记固化成测试。
- `README.md`、`dsh-plugin/README.md`、`docs/使用说明.md` 同步。
```

- [ ] **步骤 3：跑全量测试并记录数字**

运行：
```powershell
.venv\Scripts\python.exe -m pytest -q
node --check dsh-plugin/client.js
node --check dsh-plugin/lib/route.js
node --check dsh-plugin/lib/ops.js
git diff --check
```
预期：定向新增测试（`test_dsh_settings_contract.py` 4 个 + `test_dsh_plugin.py` 30 个）全 PASS；全量 pytest 与基线一致——6 个已知环境性失败（`test_pet_supervisor` 缺 `vendor/dsh-feiyu-pet/runtime/helper.py`、`test_whale_widget` 缺 `vendor/dsh-whale-widget` 子模块），0 新增失败；node 全 0。

- [ ] **步骤 4：CHANGELOG 回填验证状态**

在上面 CHANGELOG 条目末尾追加一行（把上一步的**真实输出数字**写进去，格式如下，不要留占位）：

```markdown
- 验证状态：`tests/test_dsh_settings_contract.py` 4 passed、`tests/test_dsh_plugin.py` 30 passed；全量 `pytest` 为 <真实 passed> passed / 6 failed / <真实 skipped> skipped，6 个失败均为基线已知环境失败（缺 `vendor/dsh-feiyu-pet`、`vendor/dsh-whale-widget` 子模块）；`node --check` 全过；假 ctx 冒烟 8 项断言全过。**真实安装/切档/保存未在浏览器里人工点击验收**。
```

- [ ] **步骤 5：实机重载（Host 路由生效）**

用 `plugin_manager` 的 `set_plugin` 把 `include:deepfeiyu` 关一次再开一次（此前 `deps.*`/`dshPlugins.*` 的 Host 路由就是这么加载的），然后：

```powershell
try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/settings' -UseBasicParsing -TimeoutSec 5; Write-Host "console=$($r.StatusCode)" } catch { Write-Host "console error: $($_.Exception.Message)" }
```
预期：控制台 `/api/settings` 200（设置页数据源可达）；`deepfeiyu_status` 显示 `running`。

- [ ] **步骤 6：人工验收清单（记录结果，不通过则回到对应任务修）**

1. 打开 设置 → Deep肥鱼：标题是「Deep肥鱼 · 设置」，四个标签可切换，默认落在「依赖与融合」，原三块（依赖/关联插件/融合）与改造前一致。
2. 「模型与人格」：档位 chips 显示、切档后字段刷新、改地址不点保存切档不丢（先落回再切）、留空 Key 保存后掩码不变、测试连接返回延迟、预设应用/保存/删除/图集同步各一次。
3. 「自动回复」：改一个开关保存，控制台网页打开同一设置页数值一致；模型标签未保存的草稿不被覆盖。
4. 「更新与边界」：自动查一次、手动再查、无 git 时显示"解压安装"文案、协议入口能打开；随后停掉控制台 → 任一标签显示「控制台未运行」+ 启动按钮 → 启动后恢复。
5. 依赖安装日志开着时切标签再切回来，日志仍在轮询（生命周期 effect 拆分的回归检查）。

- [ ] **步骤 7：Commit**

```bash
git add README.md dsh-plugin/README.md docs/使用说明.md CHANGELOG.md
git commit -m "docs: 记录设置页分标签与窄补丁契约（任务 6/6）"
```

---

## 自检记录

- **规格覆盖**：§5.1 模型与人格 → 任务 3；§5.2 自动回复 → 任务 4；§5.3 依赖与融合迁移 → 任务 2；§5.5 更新与边界 → 任务 5；§6 路由契约（16 个中的 P1 八个）→ 任务 1；§7 Client 结构（标签壳/控件/窄补丁/账号提示归 P2）→ 任务 2–5；§8 安全（窄补丁、掩码、显式路由）→ 任务 1/3/4；§9 失败降级 → 各标签错误态与任务 6 验收 4；§10 测试 → 任务 1/6；§11 P1 验收 → 任务 6；§12 非目标 → 无任务（未做其它页面/iframe/离线）。
- **占位符扫描**：全计划无 TODO/待定/类似任务 N；CHANGELOG 验证状态行要求回填真实数字（步骤 4 明确"用真实输出数字"，非占位）。
- **类型/签名一致性**：`call(endpoint, payload, timeoutMs=10000)`、`settingsButton(label, onClick, {primary,disabled,title})`、`errorMessage(error)`、`SETTINGS_TABS`、`draftFromSettings/autoFromSettings`、`loadSettingsTab` 在任务 3/4 间的引用一致；`settings.put` 的 `{patch}` 包装在 Client 与 Host 两侧一致；控制台响应字段（`settings`/`latency_ms`/`personas`/`created,updated`/`git_repo,has_update,remote_short,local_short,repo_url,error`）均已对照源码核实。
- **已知风险**：`window.confirm` 用于预设覆盖/删除（插件既有安装确认同款用法）；`ctx.get('sidebarRight')` 在部分宿主形态可能缺失（`openExternal` 已回退 `window.open`）；切档的两步 PUT 在极慢网络下可能乱序（控制台原页面同样行为，接受）。
