# DSH 面板会话控制（期1）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 DSH 面板的会话界面具备控制台同款会话控制——会话 AI 开关、人工接管一键恢复、多选批量设为人工/AI、状态副标题。

**架构：** 面板（Client 半）只新增渲染与状态，数据全部来自既有 `chats.list`；写操作经 Host 新增的三个白名单转发端点打到控制台现成 REST API（`/api/chats/{uid}/ai`、`/resume`、`/api/chats/batch-ai`）。Python 侧零改动、零新依赖、不引入实时通道（实时属期2）。

**技术栈：** 无构建 Cordis bundle（原生 React `h()` + 主题 token CSS）；Node 内建模块；Python 侧仅 `pytest` 静态断言 + `node --check` + 一个用 `node -e` 驱动的路由行为测试。

**规格：** `docs/superpowers/specs/2026-11-05-dsh-panel-chat-control-design.md`（已获批，commit `24ba350`）
**基线：** `e36f9ef` 之后的 `main`；`tests/test_dsh_plugin.py` 基线 **30 passed**（实测 0.58s）
**分支：** 任务 1 第一步创建 `feat/dsh-panel-chat-control`，全部改动提交在该分支，不推送。

---

## 文件结构（本计划涉及）

| 文件 | 职责 | 任务 |
|---|---|---|
| `dsh-plugin/lib/ops.js` | 控制台 HTTP 转发（+3 个函数，复用 `call()` 的令牌与超时） | 1 |
| `dsh-plugin/lib/route.js` | 面板端点注册与入参校验（+3 个 routes 表项） | 1 |
| `dsh-plugin/client.js` | 面板状态、操作函数、渲染与 CSS（线程头 / 列表徽标 / 多选） | 2-4 |
| `tests/test_dsh_plugin.py` | 静态断言 + 路由行为测试（+6 个用例） | 1-5 |
| `dsh-plugin/README.md` | 面板组成、端点表、安全节 | 5 |
| `docs/使用说明.md` | §16.1「面板能做什么」 | 5 |
| `CHANGELOG.md` | `[未发布]` 下新条目（含真实验证状态） | 5-6 |

**不改：** `app/**/*.py`、`dsh-plugin/index.js`、`lib/tools.js`、`lib/approval.js`、`lib/fusion.js`、`package.json`、许可与法律文档、根 `README.md`。

---

## 任务 1：Host 转发层（ops + route + 行为测试）

**文件：**
- 修改：`dsh-plugin/lib/ops.js`（`createConsoleApi` 返回对象，`send` 之后）
- 修改：`dsh-plugin/lib/route.js:49-151`（`routes` 表）
- 测试：`tests/test_dsh_plugin.py`（文件末尾追加）

- [ ] **步骤 1：创建功能分支**

运行（PowerShell，仓库根）：

```powershell
git checkout -b feat/dsh-panel-chat-control
```

预期：`Switched to a new branch 'feat/dsh-panel-chat-control'`；`git status --short` 干净。

- [ ] **步骤 2：编写失败的测试（静态断言 + 路由行为）**

在 `tests/test_dsh_plugin.py` 文件末尾追加两个测试与一个 harness 常量。harness 放在 `TOOL_NAMES` 定义之后也可以，但**必须在使用它的测试函数之前**（模块级常量无顺序依赖，追加到文件末尾的测试函数上方即可）：

```python
# 期1 会话控制：用假 ctx 驱动真实 registerRoute handler，覆盖转发、校验与错误分类。
ROUTE_HARNESS = r"""
import { pathToFileURL } from 'node:url'
const base = pathToFileURL(process.cwd() + '/').href
const { registerRoute } = await import(base + 'lib/route.js')
const { ConsoleDownError } = await import(base + 'lib/ops.js')

const calls = []
let failNext = false
let handler = null
const ctx = {
  connection: { requestRejection: () => 0 },
  webServer: {
    register: (route) => {
      handler = route.handler
      return () => {}
    },
  },
  effect: () => {},
  logger: { debug: () => {} },
  get: () => undefined,
}
const api = {
  chatAi: async (uid, enabled) => {
    if (failNext) {
      failNext = false
      throw new ConsoleDownError('无法连接 Deep肥鱼控制台 http://127.0.0.1:8765（ECONNREFUSED）')
    }
    calls.push(['chatAi', uid, enabled])
    return { ok: true, ai_enabled: enabled }
  },
  chatResume: async (uid) => {
    calls.push(['chatResume', uid])
    return { ok: true }
  },
  chatsBatchAi: async (uids, enabled) => {
    calls.push(['chatsBatchAi', uids, enabled])
    return { ok: true, changed: uids.length, ai_enabled: enabled }
  },
}
registerRoute({
  ctx,
  config: {},
  api,
  state: () => ({}),
  snapshot: () => ({}),
  supervisor: { start() {}, stop() {}, restart() {} },
  sendGate: undefined,
})

async function post(endpoint, payload) {
  const chunks = [Buffer.from(JSON.stringify(payload))]
  const req = {
    method: 'POST',
    url: '/deepfeiyu/' + endpoint,
    headers: {},
    async *[Symbol.asyncIterator]() {
      for (const chunk of chunks) yield chunk
    },
  }
  let status = 0
  let body = ''
  const res = {
    writeHead(code) { status = code },
    end(text) { body = text },
  }
  await handler(req, res)
  return { status, body: body ? JSON.parse(body) : null }
}

const out = []
async function step(name, fn) {
  const before = calls.length
  try {
    const extra = (await fn()) || {}
    out.push({ name, status: 'ok', calls: calls.slice(before), ...extra })
  } catch (error) {
    out.push({ name, status: 'threw', error: String((error && error.stack) || error) })
  }
}

await step('chat.ai-ok', async () => {
  const r = await post('chat.ai', { uid: '  acc_default:wxid_a  ', enabled: true })
  return { http: r.status, value: r.body && r.body.value, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.ai-missing-uid', async () => {
  const r = await post('chat.ai', { uid: '   ', enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.ai-bad-enabled', async () => {
  const r = await post('chat.ai', { uid: 'acc_default:wxid_a', enabled: 'yes' })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.ai-console-down', async () => {
  failNext = true
  const r = await post('chat.ai', { uid: 'acc_default:wxid_a', enabled: false })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.resume-ok', async () => {
  const r = await post('chat.resume', { uid: 'acc_default:wxid_b' })
  return { http: r.status, value: r.body && r.body.value }
})
await step('chat.resume-missing-uid', async () => {
  const r = await post('chat.resume', {})
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-ok-dedupe', async () => {
  const r = await post('chats.batchAi', { uids: ['a', ' a ', '', 'b', 'a'], enabled: false })
  return { http: r.status, value: r.body && r.body.value }
})
await step('batch-not-array', async () => {
  const r = await post('chats.batchAi', { uids: 'a', enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-over-limit', async () => {
  const uids = Array.from({ length: 201 }, (_, i) => 'u' + i)
  const r = await post('chats.batchAi', { uids, enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-empty-uids', async () => {
  const r = await post('chats.batchAi', { uids: ['', '  '], enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-bad-enabled', async () => {
  const r = await post('chats.batchAi', { uids: ['a'], enabled: 'true' })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})

console.log(JSON.stringify(out))
"""


def test_chat_control_routes_registered():
    """期1：三个会话控制端点必须在 Host 路由与 ops 层都登记，且入参有白名单校验。"""
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for endpoint in ("chat.ai", "chat.resume", "chats.batchAi"):
        assert f"'{endpoint}'" in route, f"lib/route.js 缺少路由 {endpoint}"
    for method in ("chatAi", "chatResume", "chatsBatchAi"):
        assert method in ops, f"lib/ops.js 缺少 {method}"
    for fragment in ("encodeURIComponent(uid)}/ai", "encodeURIComponent(uid)}/resume",
                     "'/api/chats/batch-ai'", "method: 'POST'",
                     "body: { enabled }", "body: { uids, enabled }"):
        assert fragment in ops, f"lib/ops.js 缺少片段 {fragment}"
    for message in ("缺少 uid", "enabled 必须是布尔值", "uids 必须是数组",
                    "uids 超过 200 上限", "uids 不能为空"):
        assert message in route, f"route.js 缺少入参校验文案 {message}"


def test_chat_control_route_behavior():
    """路由行为：白名单转发、参数校验、去重截断与控制台离线错误分类。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，无法跑路由行为测试（已在 PR 中如实说明）")
    proc = subprocess.run(
        [node, "--input-type=module", "-e", ROUTE_HARNESS],
        cwd=str(PKG),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, (proc.stderr or proc.stdout)
    results = {item["name"]: item for item in json.loads(proc.stdout)}
    for name, item in results.items():
        assert item["status"] == "ok", f"{name} 抛异常：{item.get('error')}"

    ok = results["chat.ai-ok"]
    assert ok["http"] == 200, ok
    assert ok["calls"] == [["chatAi", "acc_default:wxid_a", True]], ok
    assert ok["value"] == {"uid": "acc_default:wxid_a", "ai_enabled": True}, ok

    missing = results["chat.ai-missing-uid"]
    assert missing["http"] == 400 and missing["code"] == "INVALID_ARGS" and missing["calls"] == [], missing
    bad_enabled = results["chat.ai-bad-enabled"]
    assert bad_enabled["http"] == 400 and bad_enabled["code"] == "INVALID_ARGS" and bad_enabled["calls"] == [], bad_enabled

    down = results["chat.ai-console-down"]
    assert down["http"] == 200 and down["code"] == "CONSOLE_DOWN", down

    resume = results["chat.resume-ok"]
    assert resume["http"] == 200 and resume["calls"] == [["chatResume", "acc_default:wxid_b"]], resume
    assert resume["value"] == {"uid": "acc_default:wxid_b", "paused": False}, resume
    no_uid = results["chat.resume-missing-uid"]
    assert no_uid["http"] == 400 and no_uid["code"] == "INVALID_ARGS", no_uid

    batch = results["batch-ok-dedupe"]
    assert batch["http"] == 200, batch
    assert batch["calls"] == [["chatsBatchAi", ["a", "b"], False]], batch
    assert batch["value"] == {"ok": True, "changed": 2, "ai_enabled": False}, batch
    for name in ("batch-not-array", "batch-over-limit", "batch-empty-uids", "batch-bad-enabled"):
        assert results[name]["http"] == 400, name
        assert results[name]["code"] == "INVALID_ARGS", name
```

- [ ] **步骤 3：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```

预期：**2 failed, 30 passed**；失败原因是 `lib/route.js 缺少路由 chat.ai`（静态断言）与行为测试中 `chat.ai` 返回 404 `UNKNOWN_ENDPOINT` 后的断言失败。若失败原因不是「端点尚不存在」，先停下检查 harness 本身。

- [ ] **步骤 4：ops.js 新增三个转发函数**

在 `dsh-plugin/lib/ops.js` 的 `send:` 条目（约 `:107-111`）之后插入：

```js
    chatAi: (uid, enabled) => call(`/api/chats/${encodeURIComponent(uid)}/ai`, {
      method: 'POST',
      body: { enabled },
    }),
    chatResume: (uid) => call(`/api/chats/${encodeURIComponent(uid)}/resume`, {
      method: 'POST',
      body: {},
    }),
    chatsBatchAi: (uids, enabled) => call('/api/chats/batch-ai', {
      method: 'POST',
      body: { uids, enabled },
    }),
```

- [ ] **步骤 5：route.js 新增三个端点**

在 `dsh-plugin/lib/route.js` 的 `stats:` 条目（`:77`）之前插入（即 `send.text` 之后、`stats` 之前）：

```js
    // 会话控制：转发控制台现成的 AI 开关 / 恢复接管 / 批量操作，只发白名单字段。
    'chat.ai': async (payload) => {
      const uid = typeof payload.uid === 'string' ? payload.uid.trim() : ''
      const enabled = payload.enabled
      if (!uid) throw Object.assign(new Error('缺少 uid'), { code: 'INVALID_ARGS' })
      if (typeof enabled !== 'boolean') throw Object.assign(new Error('enabled 必须是布尔值'), { code: 'INVALID_ARGS' })
      await api.chatAi(uid, enabled)
      return { uid, ai_enabled: enabled }
    },
    'chat.resume': async (payload) => {
      const uid = typeof payload.uid === 'string' ? payload.uid.trim() : ''
      if (!uid) throw Object.assign(new Error('缺少 uid'), { code: 'INVALID_ARGS' })
      await api.chatResume(uid)
      return { uid, paused: false }
    },
    'chats.batchAi': async (payload) => {
      const enabled = payload.enabled
      if (typeof enabled !== 'boolean') throw Object.assign(new Error('enabled 必须是布尔值'), { code: 'INVALID_ARGS' })
      if (!Array.isArray(payload.uids)) throw Object.assign(new Error('uids 必须是数组'), { code: 'INVALID_ARGS' })
      if (payload.uids.length > 200) throw Object.assign(new Error('uids 超过 200 上限'), { code: 'INVALID_ARGS' })
      const uids = [...new Set(payload.uids.map((value) => (typeof value === 'string' ? value.trim() : '')).filter(Boolean))]
      if (!uids.length) throw Object.assign(new Error('uids 不能为空'), { code: 'INVALID_ARGS' })
      return await api.chatsBatchAi(uids, enabled)
    },
```

- [ ] **步骤 6：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/lib/ops.js
node --check dsh-plugin/lib/route.js
```

预期：**32 passed**（0 failed；若本机无 node 则 31 passed + 1 skipped）；两条 `node --check` 无输出（退出码 0）。

- [ ] **步骤 7：Commit**

```powershell
git add dsh-plugin/lib/ops.js dsh-plugin/lib/route.js tests/test_dsh_plugin.py
git commit -m "feat: 面板 Host 新增会话控制三端点转发"
```

预期：1 file... 实际 3 files changed；`git status --short` 干净。

---

## 任务 2：线程头（AI 开关 / 接管徽标 / 恢复按钮 / 状态副标题）

**文件：**
- 修改：`dsh-plugin/client.js`（状态区 `:388-392`、`setErrorOnce` 后 `:401-404`、卸载效果 `:481-493`、`selectChat` `:545-554`、`activateAccount` `:573-589`、`handleDraftKeyDown` 后 `:618`、`renderThread` `:884-888`、`STYLES` `:135` 之后）
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/test_dsh_plugin.py` 末尾：

```python
def test_client_thread_head_chat_control():
    """线程头要能开关会话 AI、显示状态副标题，并在接管中给出恢复入口。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("toggleChatAi", "resumeChat", "setChatFeedbackOnce",
                  "chat.ai", "chat.resume", "chatBusy", "chatFeedback",
                  "AI 自动回复", "人工接管中", "AI 已关闭",
                  "恢复 AI", "thread-actions", "thread-sub", "aria-checked"):
        assert token in source, f"client.js 缺少 {token}"
    # 副标题必须由 ai_enabled / paused 推出，而不是写死文案
    assert "selectedChat.paused" in source, "线程头未读取 paused 状态"
    assert "selectedChat.ai_enabled" in source, "线程头未读取 ai_enabled 状态"
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_client_thread_head_chat_control -q
```

预期：**1 failed**，报 `client.js 缺少 toggleChatAi`。

- [ ] **步骤 3：新增状态与行内反馈**

`client.js` 状态区，在 `const [error, setError] = React.useState('');`（`:392`）之后插入：

```js
          // 会话控制：AI 开关 / 恢复接管 / 批量操作共用一把互斥锁与一条行内反馈。
          const [chatBusy, setChatBusy] = React.useState(false);
          const [chatFeedback, setChatFeedback] = React.useState(null);
          const feedbackTimer = React.useRef(null);
```

在 `setErrorOnce` 函数（`:401-404`）之后插入：

```js
          // 行内反馈：错误留到下一次操作才清，成功 4s 后自动消失。
          function setChatFeedbackOnce(next) {
            setChatFeedback(next);
            if (feedbackTimer.current) window.clearTimeout(feedbackTimer.current);
            feedbackTimer.current = null;
            if (next && next.tone === 'ok') {
              feedbackTimer.current = window.setTimeout(() => {
                feedbackTimer.current = null;
                setChatFeedback((previous) => (previous && previous.text === next.text ? null : previous));
              }, 4000);
            }
          }
```

- [ ] **步骤 4：卸载时清理反馈定时器**

`React.useEffect` 状态轮询的清理函数（`:489-492`）改为：

```js
            return () => {
              mounted.current = false;
              window.clearInterval(timer);
              if (feedbackTimer.current) {
                window.clearTimeout(feedbackTimer.current);
                feedbackTimer.current = null;
              }
            };
```

- [ ] **步骤 5：切换会话 / 切换账号 / 控制台停止时清空反馈**

1. `selectChat` 函数体（`:545-554`）里 `setSendError('');` 之后加一行：

```js
            setChatFeedback(null);
```

2. `activateAccount`（`:573-589`）里 `setMessages([]);` 之后加：

```js
            setChatFeedback(null);
```

3. 控制台停止清数据的效果（`:500-509`）里 `setSendError('');` 之后加：

```js
              setChatFeedback(null);
```

- [ ] **步骤 6：新增两个操作函数**

在 `handleDraftKeyDown` 函数（`:612-618`）之后插入：

```js
          // ------------------------------------------------------------ 会话控制
          async function toggleChatAi(enabled) {
            if (!selectedUid || chatBusy || !running) return;
            setChatBusy(true);
            setChatFeedback(null);
            try {
              await call('chat.ai', { uid: selectedUid, enabled });
              if (!mounted.current) return;
              // 线程头读的是 messages.list 返回的 chat，与列表是两份数据，必须同步合并。
              setChat((previous) => (previous && previous.uid === selectedUid ? { ...previous, ai_enabled: enabled } : previous));
              setChatFeedbackOnce({ scope: 'thread', tone: 'ok', text: enabled ? '已开启 AI 自动回复' : '已关闭 AI 自动回复' });
              await loadChats(keyword);
            } catch (error0) {
              // 规格 §5.5：行内反馈 + 顶部错误条双通道，不能只在一处显示。
              if (!mounted.current) return;
              setError(errorMessage(error0));
              setChatFeedbackOnce({ scope: 'thread', tone: 'error', text: errorMessage(error0) });
            } finally {
              if (mounted.current) setChatBusy(false);
            }
          }

          async function resumeChat(uid) {
            const target = typeof uid === 'string' && uid ? uid : selectedUid;
            if (!target || chatBusy || !running) return;
            const scope = target === selectedUid ? 'thread' : 'list';
            setChatBusy(true);
            setChatFeedback(null);
            try {
              await call('chat.resume', { uid: target });
              if (!mounted.current) return;
              if (target === selectedUid) {
                setChat((previous) => (previous && previous.uid === target ? { ...previous, paused: false } : previous));
              }
              setChatFeedbackOnce({ scope, tone: 'ok', text: '已恢复 AI 自动回复' });
              await loadChats(keyword);
            } catch (error0) {
              if (!mounted.current) return;
              setError(errorMessage(error0));
              setChatFeedbackOnce({ scope, tone: 'error', text: errorMessage(error0) });
            } finally {
              if (mounted.current) setChatBusy(false);
            }
          }
```

- [ ] **步骤 7：改造 renderThread 线程头**

把 `renderThread` 里从 `const rows = messages.map(renderMessage);` 到 thread-head 结束的片段（`:883-888`）：

```js
            const rows = messages.map(renderMessage);
            return h('section', { className: 'dsh-deepfeiyu-thread' },
              h('div', { className: 'dsh-deepfeiyu-thread-head' },
                h('span', { className: 'dsh-deepfeiyu-thread-name' }, selectedName || String(selectedUid)),
                selectedChat && selectedChat.is_group ? h('span', { className: 'dsh-deepfeiyu-tag' }, '群聊') : null,
                h('span', { className: 'dsh-deepfeiyu-hint' }, messages.length ? '最近 ' + messages.length + ' 条' : '')),
```

整体替换为：

```js
            const rows = messages.map(renderMessage);
            const aiState = !selectedChat
              ? ''
              : !selectedChat.ai_enabled
                ? 'AI 已关闭'
                : selectedChat.paused
                  ? '人工接管中'
                  : 'AI 自动回复';
            const kindLabel = selectedChat ? (selectedChat.is_group ? '群聊' : '私聊') : '';
            return h('section', { className: 'dsh-deepfeiyu-thread' },
              h('div', { className: 'dsh-deepfeiyu-thread-head' },
                h('div', { className: 'dsh-deepfeiyu-thread-main' },
                  h('div', { className: 'dsh-deepfeiyu-thread-title' },
                    h('span', { className: 'dsh-deepfeiyu-thread-name' }, selectedName || String(selectedUid)),
                    kindLabel ? h('span', { className: 'dsh-deepfeiyu-tag' }, kindLabel) : null,
                    selectedChat && selectedChat.paused
                      ? h('span', { className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-warn' }, '人工接管中')
                      : null),
                  h('div', { className: 'dsh-deepfeiyu-thread-sub' },
                    aiState ? h('span', { className: 'dsh-deepfeiyu-hint' }, aiState) : null,
                    h('span', { className: 'dsh-deepfeiyu-hint' }, messages.length ? '最近 ' + messages.length + ' 条' : ''),
                    chatFeedback && chatFeedback.scope === 'thread'
                      ? h('span', {
                          className: 'dsh-deepfeiyu-feedback dsh-deepfeiyu-is-' + (chatFeedback.tone === 'ok' ? 'ok' : 'error'),
                          role: chatFeedback.tone === 'ok' ? 'status' : 'alert',
                        }, chatFeedback.text)
                      : null)),
                h('div', { className: 'dsh-deepfeiyu-thread-actions' },
                  selectedChat && selectedChat.paused
                    ? actionButton('恢复 AI', () => { void resumeChat(selectedUid); }, {
                        disabled: !running || chatBusy,
                        title: '恢复该会话的 AI 自动回复',
                      })
                    : null,
                  h('button', {
                    type: 'button',
                    role: 'switch',
                    'aria-checked': Boolean(selectedChat && selectedChat.ai_enabled),
                    className: 'dsh-deepfeiyu-btn' + (selectedChat && selectedChat.ai_enabled ? ' dsh-deepfeiyu-btn-on' : ''),
                    disabled: !running || chatBusy || !selectedChat,
                    title: '该会话是否由 AI 自动回复',
                    onClick: () => { void toggleChatAi(!(selectedChat && selectedChat.ai_enabled)); },
                  }, selectedChat && selectedChat.ai_enabled ? 'AI 开' : 'AI 关'))),
```

其后的 `h('div', { className: 'dsh-deepfeiyu-messages', ...` 起保持原样。

- [ ] **步骤 8：STYLES 新增 CSS**

在 `STYLES` 字符串里 `.dsh-deepfeiyu-thread-head { ... }`（`:135`）规则之后插入：

```css
.dsh-deepfeiyu-thread-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.dsh-deepfeiyu-thread-title { display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }
.dsh-deepfeiyu-thread-sub { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dsh-deepfeiyu-thread-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.dsh-deepfeiyu-feedback { font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-feedback.dsh-deepfeiyu-is-error { color: var(--dsw-alias-state-error-primary, #ec1313); }
.dsh-deepfeiyu-feedback.dsh-deepfeiyu-is-ok { color: var(--dsw-alias-state-success-primary, #22c55e); }
```

- [ ] **步骤 9：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/client.js
```

预期：**33 passed**（node 缺失时 32 passed + 1 skipped）；`node --check` 退出码 0。

- [ ] **步骤 10：Commit**

```powershell
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat: 面板线程头 AI 开关、状态副标题与恢复按钮"
```

---

## 任务 3：会话列表「接管中」徽标可点击恢复

**文件：**
- 修改：`dsh-plugin/client.js`（`renderChatRow` `:778`、`STYLES` 标签规则后）
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的测试**

追加：

```python
def test_client_paused_tag_clickable():
    """列表里的「人工」徽标要能点击就地恢复 AI，且不打开会话（stopPropagation）。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    assert "人工接管中，点击恢复 AI 自动回复" in source, "缺少接管徽标的 title 提示"
    assert "dsh-deepfeiyu-is-clickable" in source, "接管徽标未加可点击样式类"
    assert "event.stopPropagation()" in source, "徽标点击未阻止冒泡，会误触发行点击"
    # 点击必须走 resumeChat，而不是打开会话
    assert re.search(r"is-clickable[\s\S]{0,400}?resumeChat\(uid\)", source), (
        "接管徽标没有接到 resumeChat(uid)"
    )
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_client_paused_tag_clickable -q
```

预期：**1 failed**，报 `缺少接管徽标的 title 提示`。

- [ ] **步骤 3：改造 renderChatRow 的 paused 标签**

`renderChatRow` 内这一行（`:778`）：

```js
            if (item.paused) tags.push(h('span', { className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-warn', key: 'paused' }, '人工'));
```

替换为：

```js
            if (item.paused) tags.push(h('span', {
              key: 'paused',
              className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-warn dsh-deepfeiyu-is-clickable',
              role: 'button',
              tabIndex: 0,
              title: '人工接管中，点击恢复 AI 自动回复',
              onClick: (event) => {
                event.stopPropagation();
                void resumeChat(uid);
              },
              onKeyDown: (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                event.stopPropagation();
                void resumeChat(uid);
              },
            }, '人工'));
```

- [ ] **步骤 4：STYLES 新增可点击标签样式**

在 `.dsh-deepfeiyu-tag { ... }`（`:128`）规则之后插入：

```css
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-clickable { cursor: pointer; }
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-clickable:hover { border-color: var(--dsw-alias-state-business-primary, #4176e6); color: var(--dsw-alias-state-business-primary, #4176e6); }
```

- [ ] **步骤 5：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/client.js
```

预期：**34 passed**（node 缺失时 33 passed + 1 skipped）；`node --check` 退出码 0。

- [ ] **步骤 6：Commit**

```powershell
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat: 面板会话列表接管徽标点击即恢复 AI"
```

---

## 任务 4：会话多选批量（设为人工 / 设为 AI）

**文件：**
- 修改：`dsh-plugin/client.js`（状态区、`loadChats` `:433-447`、`selectChat`、`activateAccount`、停止效果、操作函数区、`renderChatRow` 行按钮 `:780-786`、`renderChatList` `:797-821`、`STYLES` `:114` 之后）
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的测试**

追加：

```python
def test_client_multi_select_bulk():
    """多选批量：模式开关、全选作用于当前列表、批量端点与退出清空选中集。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("selectMode", "selectedChats", "toggleSelectMode", "toggleSelectAll",
                  "handleChatRowClick", "bulkSetAi", "chats.batchAi",
                  "退出多选", "设为人工", "设为 AI", "已选 ",
                  "dsh-deepfeiyu-is-checked", "dsh-deepfeiyu-chats-tools"):
        assert token in source, f"client.js 缺少 {token}"
    # 批量成功要退出多选并清空选中集（对齐控制台行为）
    assert re.search(r"bulkSetAi[\s\S]{0,900}?setSelectMode\(false\)", source), (
        "bulkSetAi 成功后未退出多选模式"
    )
    # 搜索结果是子集，不得拿子集修剪选中集：只在无关键词时修剪
    assert "if (!keywordValue)" in source, "loadChats 未按是否有关键词区分选中集修剪"
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_client_multi_select_bulk -q
```

预期：**1 failed**，报 `client.js 缺少 selectMode`。

- [ ] **步骤 3：新增多选状态**

状态区（任务 2 新增的三行之后）插入：

```js
          // 多选批量：selectMode 下点行只切换选中，不打开会话。
          const [selectMode, setSelectMode] = React.useState(false);
          const [selectedChats, setSelectedChats] = React.useState(() => new Set());
```

- [ ] **步骤 4：loadChats 按需修剪选中集**

`loadChats` 里 `setChats(Array.isArray(value && value.chats) ? value.chats : []);`（`:439`）替换为：

```js
              const list = Array.isArray(value && value.chats) ? value.chats : [];
              setChats(list);
              // 只有不带关键词的全量拉取才修剪：搜索结果是子集，
              // 拿子集修剪会把「当前搜索看不见但仍然有效」的选中项误删。
              if (!keywordValue) {
                setSelectedChats((previous) => {
                  if (!previous.size) return previous;
                  const alive = new Set(list.map((item) => item && item.uid).filter(Boolean));
                  const next = new Set([...previous].filter((uid) => alive.has(uid)));
                  return next.size === previous.size ? previous : next;
                });
              }
```

- [ ] **步骤 5：退出多选的清理点**

1. `selectChat` 里任务 2 加的 `setChatFeedback(null);` 之后再加：

```js
            setSelectMode(false);
            setSelectedChats(new Set());
```

2. `activateAccount` 里同样加这两行（切账号必须清空选中集，防止误伤旧账号 uid）。

3. 控制台停止清数据的效果里同样加这两行。

- [ ] **步骤 6：新增多选操作函数**

任务 2 的「会话控制」函数区（`resumeChat` 之后）插入：

```js
          function toggleSelectMode() {
            if (!running) return;
            if (selectMode) setSelectedChats(new Set());
            setSelectMode(!selectMode);
            setChatFeedback(null);
          }

          function handleChatRowClick(uid) {
            if (selectMode) {
              setSelectedChats((previous) => {
                const next = new Set(previous);
                if (next.has(uid)) next.delete(uid);
                else next.add(uid);
                return next;
              });
              return;
            }
            selectChat(uid);
          }

          function toggleSelectAll() {
            setSelectedChats((previous) => {
              const alive = chats.filter((item) => item && item.uid).map((item) => item.uid);
              if (!alive.length) return previous;
              const all = alive.every((uid) => previous.has(uid));
              const next = new Set(previous);
              for (const uid of alive) {
                if (all) next.delete(uid);
                else next.add(uid);
              }
              return next;
            });
          }

          async function bulkSetAi(enabled) {
            const uids = [...selectedChats];
            if (!uids.length || chatBusy || !running) return;
            setChatBusy(true);
            setChatFeedback(null);
            try {
              // 200 个会话逐个改库可能慢于默认 10s，放宽到 30s。
              const value = await call('chats.batchAi', { uids, enabled }, 30000);
              if (!mounted.current) return;
              const changed = value && typeof value.changed === 'number' ? value.changed : 0;
              setSelectMode(false);
              setSelectedChats(new Set());
              setChatFeedbackOnce({
                scope: 'list',
                tone: 'ok',
                text: '已把 ' + changed + ' 个会话设为' + (enabled ? ' AI 自动回复' : '人工接管'),
              });
              await loadChats(keyword);
            } catch (error0) {
              if (!mounted.current) return;
              setError(errorMessage(error0));
              setChatFeedbackOnce({ scope: 'list', tone: 'error', text: errorMessage(error0) });
            } finally {
              if (mounted.current) setChatBusy(false);
            }
          }
```

- [ ] **步骤 7：renderChatList 新增多选工具条**

`renderChatList` 的 `return h('aside', ...)` 里，在 `chats-head` 那一行（`:805-818`）之后、`chat-list` 之前插入：

```js
              h('div', { className: 'dsh-deepfeiyu-chats-tools' },
                actionButton(selectMode ? '退出多选' : '多选', toggleSelectMode, {
                  disabled: !running,
                  title: '批量设置会话的 AI 开关',
                }),
                selectMode
                  ? h(React.Fragment, null,
                      actionButton(
                        chats.length > 0 && chats.every((item) => item && item.uid && selectedChats.has(item.uid))
                          ? '取消全选'
                          : '全选',
                        toggleSelectAll,
                        { disabled: !chats.length, title: '全选当前列表里的会话' },
                      ),
                      h('span', { className: 'dsh-deepfeiyu-hint' }, '已选 ' + selectedChats.size),
                      actionButton('设为人工', () => { void bulkSetAi(false); }, {
                        disabled: !running || chatBusy || !selectedChats.size,
                        title: '把选中会话的 AI 关闭',
                      }),
                      actionButton('设为 AI', () => { void bulkSetAi(true); }, {
                        disabled: !running || chatBusy || !selectedChats.size,
                        title: '把选中会话交给 AI 自动回复',
                      }))
                  : null,
                chatFeedback && chatFeedback.scope === 'list'
                  ? h('span', {
                      className: 'dsh-deepfeiyu-feedback dsh-deepfeiyu-is-' + (chatFeedback.tone === 'ok' ? 'ok' : 'error'),
                      role: chatFeedback.tone === 'ok' ? 'status' : 'alert',
                    }, chatFeedback.text)
                  : null),
```

- [ ] **步骤 8：renderChatRow 行按钮接入多选**

`renderChatRow` 的行按钮（`:780-786`）：

```js
            return h('button', {
              key: uid,
              type: 'button',
              role: 'listitem',
              className: 'dsh-deepfeiyu-chat' + (uid === selectedUid ? ' dsh-deepfeiyu-is-active' : ''),
              title: item.name ? String(item.name) : uid,
              onClick: () => selectChat(uid),
            },
```

替换为：

```js
            return h('button', {
              key: uid,
              type: 'button',
              role: 'listitem',
              className: 'dsh-deepfeiyu-chat'
                + (uid === selectedUid ? ' dsh-deepfeiyu-is-active' : '')
                + (selectMode && selectedChats.has(uid) ? ' dsh-deepfeiyu-is-checked' : ''),
              title: item.name ? String(item.name) : uid,
              onClick: () => handleChatRowClick(uid),
            },
```

- [ ] **步骤 9：STYLES 新增多选样式**

在 `.dsh-deepfeiyu-chats-head { ... }`（`:114`）规则之后插入：

```css
.dsh-deepfeiyu-chats-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dsh-deepfeiyu-chat.dsh-deepfeiyu-is-checked { background: var(--dsw-alias-interactive-bg-hover, #2631480f); box-shadow: inset 0 0 0 1px var(--dsw-alias-state-business-primary, #4176e6); }
```

- [ ] **步骤 10：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/client.js
```

预期：**35 passed**（node 缺失时 34 passed + 1 skipped）；`node --check` 退出码 0。

- [ ] **步骤 11：Commit**

```powershell
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat: 面板会话多选批量设为人工/AI"
```

---

## 任务 5：文档同步 + 全量验证

**文件：**
- 修改：`dsh-plugin/README.md`（顶部组成列表、组成表 `route.js` 行、安全节）
- 修改：`docs/使用说明.md`（§16.1 会话条目）
- 修改：`CHANGELOG.md`（`## [未发布]` 下新增条目）
- 测试：`tests/test_dsh_plugin.py`（文档断言）

- [ ] **步骤 1：编写失败的文档断言测试**

追加：

```python
def test_readme_documents_chat_control():
    """期1 会话控制必须在插件 README 与使用说明里可见，并写明不走审批的定位。"""
    readme = (PKG / "README.md").read_text(encoding="utf-8")
    for token in ("多选", "恢复 AI", "AI 开关", "chat.ai", "不走 Harness 审批"):
        assert token in readme, f"dsh-plugin/README.md 缺少 {token}"
    usage = (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8")
    for token in ("多选", "恢复 AI", "AI 开关"):
        assert token in usage, f"docs/使用说明.md 缺少 {token}"
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_readme_documents_chat_control -q
```

预期：**1 failed**，报 `dsh-plugin/README.md 缺少 多选`。

- [ ] **步骤 3：改 dsh-plugin/README.md**

1. 顶部组成列表第一条（`:5`）：

   - 原：`- Harness 左侧栏新增「Deep肥鱼」图标，中央面板提供控制台状态、启停、账号切换、会话列表、消息记录与发送；`
   - 改：`- Harness 左侧栏新增「Deep肥鱼」图标，中央面板提供控制台状态、启停、账号切换、会话列表（「接管中」徽标点击即恢复 AI、多选批量设为人工/AI）、消息记录与发送、线程头会话 AI 开关与状态副标题；`

2. 组成表 `lib/route.js` 行（`:20`）：

   - 原：`| \`lib/route.js\` | 面板数据路由：\`POST /deepfeiyu/<endpoint>\`（Connection 鉴权），含 \`deps.*\` 与 \`dshPlugins.*\` |`
   - 改：`| \`lib/route.js\` | 面板数据路由：\`POST /deepfeiyu/<endpoint>\`（Connection 鉴权），含 \`chat.ai\` / \`chat.resume\` / \`chats.batchAi\`、\`deps.*\` 与 \`dshPlugins.*\` |`

3. 「## 安全」节里 `- **Agent 发送跟随 Harness 权限模式**：...` 条目之后新增一条：

```markdown
- **面板会话操作不走 Harness 审批**：会话 AI 开关、恢复接管与多选批量等价于人在控制台里点一下（与面板发送 `source=human` 同一定位），只受控制台访问令牌校验与本机来源限制约束；破坏性低于发消息，随时可改回。
```

- [ ] **步骤 4：改 docs/使用说明.md §16.1**

`:416` 这一条：

- 原：`- 会话：搜索框 + 会话列表（最后一条消息摘要、时间、未读、AI / 人工状态）；选中后显示该会话最近消息。`
- 改：

```markdown
- 会话：搜索框 + 会话列表（最后一条消息摘要、时间、未读、AI / 人工状态；「人工」徽标点击即恢复 AI 且不打开会话；「多选」可全选当前列表并批量设为人工/AI）；选中后显示该会话最近消息，线程头带状态副标题（群聊/私聊 · AI 自动回复/人工接管中/AI 已关闭）、会话 AI 开关与「恢复 AI」按钮。
```

- [ ] **步骤 5：运行文档测试与插件测试**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py tests/test_usage_doc.py tests/test_readme_claims.py -q
```

预期：全部通过（`test_dsh_plugin.py` **36 passed**；另两个文件的既有用例通过）。若 `test_usage_doc` 报页面表缺失，检查是否误动了 §2 的页面表——本任务不该碰它。

- [ ] **步骤 6：跑全量测试并记录真实输出**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

预期：0 failed；基线为 2026-11-04 的 **1603 passed / 1 skipped**，本计划新增 6 个用例，理论值 **1609 passed / 1 skipped**。**以实际输出为准**：若出现失败或环境性 skipped，如实记录原因，不得改测试凑绿。记下准确数字供下一步写 CHANGELOG。

- [ ] **步骤 7：写 CHANGELOG 条目**

在 `CHANGELOG.md` 的 `## [未发布]`（`:7`）与 `### 2026-11-04 · ...`（`:9`）之间插入：

```markdown
### 2026-11-05 · DSH 面板会话控制（期1：AI 开关 / 恢复接管 / 多选批量）

- 面板线程头新增会话 AI 开关、状态副标题（群聊/私聊 · AI 自动回复/人工接管中/AI 已关闭）、「人工接管中」徽标与「恢复 AI」按钮；会话列表的「人工」徽标改为可点击，点击即恢复该会话的 AI 自动回复且不打开会话。
- 会话列表新增「多选」：多选/退出多选、全选（作用于当前搜索结果）、已选计数、批量「设为人工 / 设为 AI」；退出多选、切换会话或切换账号都会清空选中集，只在不带关键词的全量拉取时修剪已消失的会话。
- Host 侧新增三个同源端点 `chat.ai` / `chat.resume` / `chats.batchAi`（`POST /deepfeiyu/*`，Connection 鉴权不变），转发到控制台现成的 `POST /api/chats/{uid}/ai`、`POST /api/chats/{uid}/resume`、`POST /api/chats/batch-ai`；入参白名单校验（uid 非空、`enabled` 必须布尔、`uids` ≤200 且去重）。Python 侧零改动、零新依赖；面板操作不走 Harness 审批，与既有面板发送（`source=human`）同一定位，已写入 `dsh-plugin/README.md` 安全节。
- 验证状态：`tests/test_dsh_plugin.py` **36 passed**；全量 `pytest -q` **<以步骤 6 实际输出填写：passed/skipped 数与耗时>**；`node --check`（经 `test_js_syntax`）通过；`python tools/release_snapshot.py --scan-tracked .` 扫描通过、`git diff --check` 干净。实机安装与面板操作验收见下一条「实机验证」记录。
```

> `<以步骤 6 实际输出填写...>` 是给执行者的**填写指令**，不是可提交内容：写入前必须替换成步骤 6 的真实数字，禁止保留尖括号占位。

- [ ] **步骤 8：脱敏扫描与 diff 检查**

```powershell
.venv\Scripts\python.exe tools/release_snapshot.py --scan-tracked .
git diff --check
```

预期：扫描通过、`git diff --check` 无输出（退出码 0）。人工过一遍 `git diff` 确认无密钥、无真实聊天、无本机绝对路径。

- [ ] **步骤 9：Commit**

```powershell
git add dsh-plugin/README.md docs/使用说明.md CHANGELOG.md tests/test_dsh_plugin.py
git commit -m "docs: 面板会话控制使用说明与更新日志"
```

---

## 任务 6：安装实机验证与验收

**文件：**
- 修改：`CHANGELOG.md`（验证状态补实机结论）

- [ ] **步骤 1：重装 bundle**

调用 `plugin_manager`：`action=install_bundle`，`target=D:\Gitee\deepfeiyu\dsh-plugin`。

预期：读返回的 `application`——`applied` 即生效；`restart-required` 则按 `nextStep` 提示重启 Harness；出现 `warnings` 逐条记录，不忽略。

- [ ] **步骤 2：重载插件让 Host 半加载新路由**

Host JS 变了，只刷新页面不够（`dsh-plugin/README.md`「安装之后」第 5 条）：在插件页把「Deep肥鱼」关一次再打开，或 `plugin_manager` 对 `include:deepfeiyu` 先 `enabled: false` 再 `true`。预期：控制台随重载重启并恢复 `running`（历史 CHANGELOG 已记录该行为）；若控制台没起来，用面板「启动」按钮或 `deepfeiyu_console action=start`。

- [ ] **步骤 3：请用户刷新页面并执行验收清单**

请用户在 `http://127.0.0.1:8040` 刷新后逐项确认（对应规格 §9）：

1. 线程头出现 AI 开关，切换后**控制台会话页**同会话状态同步变化；
2. 会话处于人工接管时，列表「人工」徽标可点击，点后恢复 AI 且**不打开该会话**；
3. 多选 → 全选 → 设为人工/AI，控制台对应会话标签批量变化，面板提示「已把 N 个会话设为…」；
4. 副标题显示 `群聊|私聊 · AI 自动回复|人工接管中|AI 已关闭`（无「AI 判断」属预期，期2 补）；
5. 控制台停机时新按钮禁用并有可读提示；
6. 若点击报「未知 endpoint: chat.ai」→ Host 半未重载，回到步骤 2 再做一次。

- [ ] **步骤 4：把实机结论补进 CHANGELOG 并提交**

在任务 5 的 CHANGELOG 条目末尾追加一条实机记录（只写真实发生的结果，未验证项如实标注）：

```markdown
- 实机验证（2026-11-05）：<安装 application 结论、重载方式、用户验收 1-5 的实际结果；未通过项写明现象与后续处理>
```

```powershell
git add CHANGELOG.md
git commit -m "docs: 面板会话控制实机验证结论"
```

- [ ] **步骤 5：收尾报告**

向用户汇报：改了哪些文件、每条命令的真实输出、验收结果、未验证项与剩余风险（如视觉细节、真实微信通道下的批量操作）。不推送、不开 PR，等用户指示。

---

## 回滚

- 任一任务失败且无法修复：`git revert` 对应提交即可，无数据与配置迁移；
- 已安装的 bundle 回滚：用旧提交的 `dsh-plugin/` 重新 `install_bundle`，或在插件页停用；
- 测试失败不得通过删改断言「变绿」——回到实现修。

## 已知边界（写给执行者）

- 期1 **没有**「AI 判断：<拦截原因>」副标题：`policy` 事件只经控制台 `/ws` 下发，属期2；
- 面板搜索是服务端过滤（`chats.list` 带 keyword），全选作用于**当前搜索结果**，与控制台本地过滤语义一致；
- 批量上限 200 与 `chats.list` 的 limit 对齐，超出直接 400，不截断；
- 真实微信通道下的批量操作本机可能无可用通道验证，属已知未验证项，如实报告。
