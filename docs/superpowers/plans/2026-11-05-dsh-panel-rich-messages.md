# DSH 面板消息富展示（期3）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 消息流里图片、表情、引用按控制台同款富渲染，文件/聊天记录/系统消息显示真实文本（不再被去重逻辑吞成占位标签）。

**架构：** Host 在既有 `/deepfeiyu` 前缀下新增 **GET 字节代理**（`image`、`emoji`，同源 `requestRejection` 鉴权、令牌不出 Host）与两个收藏端点（`emojis.list`、`emoji.fav`）；Client 把 `renderMessage` 改成控制台同款分支顺序，媒体加载失败用**状态记账回退占位**（不做 DOM 手术，避免轮询重渲染回环）。

**技术栈：** 无构建 Cordis bundle（原生 React `h()` + token CSS）；Node 内建 `fetch`/`Buffer`；测试为 Python 静态断言 + `node -e` 驱动真实路由的行为测试。

**规格：** `docs/superpowers/specs/2026-11-05-dsh-panel-rich-messages-design.md`（已获批，commit `d94cfc8`）
**基线：** 分支 `feat/dsh-panel-chat-control` @ `d94cfc8`；`tests/test_dsh_plugin.py` 基线 **36 passed**（期1 终值，实测）
**分支：** 续用 `feat/dsh-panel-chat-control`（与期1 同分支；如何拆 PR 由仓库所有者决定）。**提交一律路径限定**（`git commit -- <本任务文件>`），因为同仓库另一会话可能往暂存区放文件——期1 已发生过一次误夹带。

---

## 文件结构（本计划涉及）

| 文件 | 职责 | 任务 |
|---|---|---|
| `dsh-plugin/lib/ops.js` | `fetchBytes` 字节传输 + 4 个薄封装 | 1 |
| `dsh-plugin/lib/route.js` | handler GET 分流、`getRoutes` 两条、POST 表两条 | 1 |
| `dsh-plugin/client.js` | 媒体状态与辅助函数、`renderMessage` 分支、CSS | 2-3 |
| `tests/test_dsh_plugin.py` | 静态断言 + 行为测试扩展（+5 用例） | 1-4 |
| `dsh-plugin/README.md`、`docs/使用说明.md`、`CHANGELOG.md` | 文档与验证状态 | 4-5 |

**不改：** `app/**/*.py`、`index.js`、`lib/tools.js`、`lib/approval.js`、`lib/fusion.js`、期1 的线程头/多选/接管逻辑、许可与法律文档。

---

## 任务 1：Host 字节代理与收藏端点

**文件：**
- 修改：`dsh-plugin/lib/ops.js`（模块顶部 `ConsoleApiError` 之后加常量；`createConsoleApi` 内 `return` 之前加 `fetchBytes`；返回对象加 4 个封装）
- 修改：`dsh-plugin/lib/route.js`（`routes` 表加 2 项；`registerRoute` 内加 `getRoutes`；`handler` 改 GET 分流）
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的测试（静态断言）**

追加到 `tests/test_dsh_plugin.py` 末尾：

```python
def test_rich_media_routes_registered():
    """期3：Host 要有图片/表情字节 GET 代理与收藏转发，GET 也必须过同源鉴权。"""
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for token in ("getRoutes", "image: async", "emoji: async", "'emoji.fav'", "'emojis.list'",
                  "METHOD_NOT_ALLOWED", "content-disposition", "cache-control"):
        assert token in route, f"lib/route.js 缺少 {token}"
    for token in ("fetchBytes", "messageImage", "emojiProxy", "emojisList", "emojiFav",
                  "arrayBuffer", "x-access-token", "MAX_MEDIA_BYTES"):
        assert token in ops, f"lib/ops.js 缺少 {token}"
    # GET 分流必须在 requestRejection 之后：不能出现「先放行 GET、后鉴权」
    auth_pos = route.index("ctx.connection.requestRejection")
    get_pos = route.index("req.method === 'GET'")
    assert auth_pos < get_pos, "GET 分流必须在同源鉴权之后"
    for message in ("缺少 uid", "缺少 msg", "缺少 url", "md5 必须是字符串",
                    "width/height 必须是数值"):
        assert message in route, f"route.js 缺少校验文案 {message}"
```

- [ ] **步骤 2：扩展 ROUTE_HARNESS（先扩测试载体，仍应失败）**

对 `tests/test_dsh_plugin.py` 里已有的 `ROUTE_HARNESS` 做四处插入（保持既有步骤不动）：

1. `const api = {` 块内、`chatAi` 条目之前插入：

```js
  messageImage: async (uid, msg) => {
    calls.push(['messageImage', uid, msg])
    return { status: 200, contentType: 'image/jpeg', body: Buffer.from('fakejpeg'), disposition: '' }
  },
  emojiProxy: async (url, md5, download) => {
    calls.push(['emojiProxy', url, md5, download])
    if (url.includes('missing')) {
      return { status: 404, contentType: 'application/json', body: Buffer.from('{"detail":"本机没有这张图片的缓存"}'), disposition: '' }
    }
    return { status: 200, contentType: 'image/gif', body: Buffer.from('fakegif'), disposition: download ? 'attachment; filename="emoji.gif"' : '' }
  },
  emojisList: async (limit) => {
    calls.push(['emojisList', limit])
    return [{ eid: 1, url: 'https://e.example/x.gif' }]
  },
  emojiFav: async (payload) => {
    calls.push(['emojiFav', payload])
    return { ok: true, emoji: { eid: 2, url: payload.url } }
  },
```

2. 第一个 `registerRoute({...})` 调用之后插入第二个「鉴权被拒」实例：

```js
let rejectedHandler = null
registerRoute({
  ctx: {
    ...ctx,
    connection: { requestRejection: () => 403 },
    webServer: { register: (route) => { rejectedHandler = route.handler; return () => {} } },
  },
  config: {},
  api,
  state: () => ({}),
  snapshot: () => ({}),
  supervisor: { start() {}, stop() {}, restart() {} },
  sendGate: undefined,
})
```

3. `post` 函数之后插入 GET 驱动器：

```js
async function get(path) {
  const req = {
    method: 'GET',
    url: '/deepfeiyu' + path,
    headers: {},
    async *[Symbol.asyncIterator]() {},
  }
  let status = 0
  let headers = {}
  let body = Buffer.alloc(0)
  const res = {
    writeHead(code, nextHeaders) { status = code; headers = nextHeaders || {} },
    end(text) { body = Buffer.isBuffer(text) ? text : Buffer.from(String(text ?? '')) },
  }
  await handler(req, res)
  return { status, headers, body }
}
```

4. `console.log(JSON.stringify(out))` 之前插入新步骤：

```js
await step('get-image-ok', async () => {
  const r = await get('/image?uid=' + encodeURIComponent('  acc_default:wxid_a  ') + '&msg=9006')
  return { http: r.status, type: r.headers['content-type'], cache: r.headers['cache-control'], text: r.body.toString() }
})
await step('get-image-missing-uid', async () => {
  const r = await get('/image?msg=9006')
  return { http: r.status, code: JSON.parse(r.body.toString()).error.code }
})
await step('get-unknown', async () => {
  const r = await get('/nope?uid=a&msg=b')
  return { http: r.status, code: JSON.parse(r.body.toString()).error.code }
})
await step('get-emoji-ok', async () => {
  const r = await get('/emoji?url=' + encodeURIComponent('https://e.example/x.gif') + '&md5=abc')
  return { http: r.status, type: r.headers['content-type'], text: r.body.toString() }
})
await step('get-emoji-download', async () => {
  const r = await get('/emoji?url=' + encodeURIComponent('https://e.example/x.gif') + '&download=1')
  return { http: r.status, disposition: r.headers['content-disposition'] }
})
await step('get-emoji-missing-url', async () => {
  const r = await get('/emoji?md5=abc')
  return { http: r.status, code: JSON.parse(r.body.toString()).error.code }
})
await step('get-emoji-upstream-404', async () => {
  const r = await get('/emoji?url=' + encodeURIComponent('https://e.example/missing.gif'))
  return { http: r.status, detail: JSON.parse(r.body.toString()).detail }
})
await step('get-to-post-route', async () => {
  const r = await get('/state')
  return { http: r.status, code: JSON.parse(r.body.toString()).error.code }
})
await step('get-auth-rejected', async () => {
  const req = { method: 'GET', url: '/deepfeiyu/image?uid=a&msg=b', headers: {}, async *[Symbol.asyncIterator]() {} }
  let status = 0
  let body = ''
  const res = { writeHead(code) { status = code }, end(text) { body = text } }
  await rejectedHandler(req, res)
  return { http: status, code: JSON.parse(body).error.code }
})
await step('emojis-list-clamp', async () => {
  const r = await post('emojis.list', { limit: 9999 })
  return { http: r.status, value: r.body && r.body.value }
})
await step('emojis-list-default', async () => {
  const r = await post('emojis.list', {})
  return { http: r.status, value: r.body && r.body.value }
})
await step('emoji-fav-ok', async () => {
  const r = await post('emoji.fav', { url: 'https://e.example/x.gif', md5: 'abc', width: 100, height: 80, evil: true })
  return { http: r.status, value: r.body && r.body.value }
})
await step('emoji-fav-missing-url', async () => {
  const r = await post('emoji.fav', { md5: 'abc' })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('emoji-fav-bad-width', async () => {
  const r = await post('emoji.fav', { url: 'https://e.example/x.gif', width: 'wide' })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
```

5. 追加行为测试函数：

```python
def test_rich_media_route_behavior():
    """GET 字节代理与收藏端点：透传、鉴权、参数校验、白名单与错误分类。"""
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

    img = results["get-image-ok"]
    assert img["http"] == 200 and img["type"] == "image/jpeg", img
    assert "max-age=86400" in img["cache"] and img["text"] == "fakejpeg", img
    assert img["calls"] == [["messageImage", "acc_default:wxid_a", "9006"]], img
    assert results["get-image-missing-uid"]["http"] == 400
    assert results["get-image-missing-uid"]["code"] == "INVALID_ARGS"
    assert results["get-unknown"]["http"] == 404
    assert results["get-unknown"]["code"] == "UNKNOWN_ENDPOINT"

    emoji = results["get-emoji-ok"]
    assert emoji["http"] == 200 and emoji["type"] == "image/gif" and emoji["text"] == "fakegif", emoji
    assert emoji["calls"] == [["emojiProxy", "https://e.example/x.gif", "abc", 0]], emoji
    dl = results["get-emoji-download"]
    assert dl["http"] == 200 and dl["disposition"] == 'attachment; filename="emoji.gif"', dl
    assert results["get-emoji-missing-url"]["code"] == "INVALID_ARGS"
    up = results["get-emoji-upstream-404"]
    assert up["http"] == 404 and "缓存" in up["detail"], up
    assert results["get-to-post-route"]["http"] == 405
    assert results["get-to-post-route"]["code"] == "METHOD_NOT_ALLOWED"
    assert results["get-auth-rejected"]["http"] == 403
    assert results["get-auth-rejected"]["code"] == "UNAUTHORIZED"

    clamp = results["emojis-list-clamp"]
    assert clamp["calls"] == [["emojisList", 500]] and len(clamp["value"]) == 1, clamp
    assert results["emojis-list-default"]["calls"] == [["emojisList", 500]]
    fav = results["emoji-fav-ok"]
    assert fav["http"] == 200 and fav["value"]["ok"] is True, fav
    assert set(fav["calls"][0][1]) == {"url", "md5", "width", "height"}, "白名单外字段被透传"
    assert results["emoji-fav-missing-url"]["code"] == "INVALID_ARGS"
    assert results["emoji-fav-bad-width"]["code"] == "INVALID_ARGS"
```

- [ ] **步骤 3：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```

预期：**2 failed, 36 passed**；失败原因分别是 `lib/route.js 缺少 getRoutes`（静态）与行为测试里 `getRoutes` 未定义（`req.method === 'GET'` 分支不存在 → GET 落到 405/404 断言失败）。若失败原因不是「实现尚不存在」，先查 harness。

- [ ] **步骤 4：ops.js 新增 `fetchBytes` 与 4 个封装**

1. 模块顶部 `ConsoleApiError` 类之后插入：

```js
/** 单张媒体字节上限：图片/表情远小于此值，超限按上游异常处理，防止拖垮面板。 */
const MAX_MEDIA_BYTES = 25 * 1024 * 1024
```

2. `createConsoleApi` 内、`return {` 之前插入：

```js
  // 字节流传输：图片/表情不能走 call()（它按 JSON 解析），令牌同样只在 Host 内用。
  async function fetchBytes(path, { timeoutMs = 10000 } = {}) {
    const headers = {}
    const token = await getToken()
    if (token) headers['x-access-token'] = token
    let response
    try {
      response = await fetch(`${baseUrl()}${path}`, {
        method: 'GET',
        headers,
        signal: AbortSignal.timeout(Math.max(1000, timeoutMs)),
      })
    } catch (err) {
      const code = err?.cause?.code || err?.code || err?.name || 'UNKNOWN'
      throw new ConsoleDownError(`无法连接 Deep肥鱼控制台 ${baseUrl()}（${code}）`)
    }
    const buffer = Buffer.from(await response.arrayBuffer())
    if (buffer.length > MAX_MEDIA_BYTES) {
      throw new ConsoleApiError(502, `媒体文件超过 ${Math.round(MAX_MEDIA_BYTES / 1024 / 1024)}MB 上限`)
    }
    return {
      status: response.status,
      contentType: response.headers.get('content-type') || '',
      disposition: response.headers.get('content-disposition') || '',
      body: buffer,
    }
  }
```

3. 返回对象里 `updateCheck` 之前插入：

```js
    messageImage: (uid, msg) => fetchBytes(
      `/api/chats/${encodeURIComponent(uid)}/messages/${encodeURIComponent(msg)}/image`,
    ),
    emojiProxy: (url, md5, download) => fetchBytes(
      `/api/emoji/proxy?url=${encodeURIComponent(url)}${md5 ? `&md5=${encodeURIComponent(md5)}` : ''}${download ? '&download=1' : ''}`,
    ),
    emojisList: (limit = 500) => call(`/api/emojis?limit=${Math.max(1, Math.min(500, Math.trunc(Number(limit) || 500)))}`),
    emojiFav: (payload) => call('/api/emojis', { method: 'POST', body: payload, timeoutMs: 15000 }),
```

- [ ] **步骤 5：route.js 新增 2 个 POST 端点与 GET 路由表**

1. `routes` 表里 `'chats.batchAi'` 条目之后插入：

```js
    // 表情收藏：白名单字段构造新对象，前端传来的多余字段一律丢弃。
    'emoji.fav': async (payload) => {
      const url = typeof payload.url === 'string' ? payload.url.trim() : ''
      if (!url) throw Object.assign(new Error('缺少 url'), { code: 'INVALID_ARGS' })
      const md5 = payload.md5 === undefined ? '' : payload.md5
      if (typeof md5 !== 'string') throw Object.assign(new Error('md5 必须是字符串'), { code: 'INVALID_ARGS' })
      const width = payload.width === undefined ? 0 : payload.width
      const height = payload.height === undefined ? 0 : payload.height
      if (!Number.isFinite(width) || !Number.isFinite(height)) {
        throw Object.assign(new Error('width/height 必须是数值'), { code: 'INVALID_ARGS' })
      }
      return await api.emojiFav({ url, md5, width, height })
    },
    'emojis.list': async (payload) => {
      const raw = payload && payload.limit !== undefined ? Number(payload.limit) : 500
      const limit = Number.isFinite(raw) ? Math.max(1, Math.min(500, Math.trunc(raw))) : 500
      return await api.emojisList(limit)
    },
```

2. `const routes = {` 之前（`dshPlugins` 创建之后）插入 GET 表与字节写回：

```js
  // GET 只服务媒体字节：参数全在 query、无 body，响应原样透传（含上游错误状态）。
  async function sendBytes(res, result) {
    const headers = {
      'content-type': result.contentType || 'application/octet-stream',
      'cache-control': 'private, max-age=86400',
      'content-length': String(result.body.length),
    }
    if (result.disposition) headers['content-disposition'] = result.disposition
    res.writeHead(result.status || 200, headers)
    res.end(result.body)
  }

  const getRoutes = {
    image: async (params, res) => {
      const uid = String(params.get('uid') || '').trim()
      const msg = String(params.get('msg') || '').trim()
      if (!uid) throw Object.assign(new Error('缺少 uid'), { code: 'INVALID_ARGS' })
      if (!msg) throw Object.assign(new Error('缺少 msg'), { code: 'INVALID_ARGS' })
      await sendBytes(res, await api.messageImage(uid, msg))
    },
    emoji: async (params, res) => {
      const url = String(params.get('url') || '').trim()
      if (!url) throw Object.assign(new Error('缺少 url'), { code: 'INVALID_ARGS' })
      const md5 = String(params.get('md5') || '').trim()
      const download = params.get('download') === '1' ? 1 : 0
      await sendBytes(res, await api.emojiProxy(url, md5, download))
    },
  }
```

- [ ] **步骤 6：handler 改 GET 分流**

把整个 `async function handler(req, res) { ... }`（自 `let rejection` 起至收尾 `}` 止，位于 `const dispose =` 之前）替换为：

```js
  async function handler(req, res) {
    let rejection
    try {
      rejection = ctx.connection.requestRejection({ headers: req.headers })
    } catch {
      rejection = 401
    }
    if (rejection) {
      fail(res, rejection === 403 ? 403 : 401, 'UNAUTHORIZED', '请求未通过 Harness 连接鉴权')
      return
    }
    const url = new URL(req.url || '/', 'http://127.0.0.1')
    const endpoint = url.pathname.slice(ROUTE_PREFIX.length).replace(/^\/+/, '')
    // GET 分流在鉴权之后：媒体字节与 JSON 错误体共用同一套同源校验。
    if (req.method === 'GET') {
      const route = Object.hasOwn(getRoutes, endpoint) ? getRoutes[endpoint] : undefined
      if (!route) {
        if (Object.hasOwn(routes, endpoint)) fail(res, 405, 'METHOD_NOT_ALLOWED', '该端点只接受 POST')
        else fail(res, 404, 'UNKNOWN_ENDPOINT', `未知 endpoint: ${endpoint}`)
        return
      }
      try {
        await route(url.searchParams, res)
      } catch (err) {
        ctx.logger.debug('deepfeiyu get %s failed: %s', endpoint, err?.message || err)
        if (err instanceof ConsoleDownError) fail(res, 200, 'CONSOLE_DOWN', describeError(err))
        else if (err?.code === 'INVALID_ARGS') fail(res, 400, 'INVALID_ARGS', err.message)
        else fail(res, 200, 'INTERNAL', describeError(err))
      }
      return
    }
    if (req.method !== 'POST') {
      fail(res, 405, 'METHOD_NOT_ALLOWED', '只接受 POST')
      return
    }
    let payload
    try {
      payload = await readJsonBody(req)
    } catch (err) {
      const tooLarge = err?.code === 'BODY_TOO_LARGE'
      fail(res, tooLarge ? 413 : 400, tooLarge ? 'BODY_TOO_LARGE' : 'INVALID_BODY', err?.message || '请求体无效')
      return
    }
    const route = Object.hasOwn(routes, endpoint) ? routes[endpoint] : undefined
    if (!route) {
      fail(res, 404, 'UNKNOWN_ENDPOINT', `未知 endpoint: ${endpoint}`)
      return
    }
    try {
      ok(res, await route(payload))
    } catch (err) {
      ctx.logger.debug('deepfeiyu route %s failed: %s', endpoint, err?.message || err)
      if (err instanceof ConsoleDownError) fail(res, 200, 'CONSOLE_DOWN', describeError(err))
      else if (err?.code === 'INVALID_ARGS') fail(res, 400, 'INVALID_ARGS', err.message)
      else if (err instanceof ConsoleApiError) fail(res, 200, 'CONSOLE_HTTP', describeError(err))
      else fail(res, 200, 'INTERNAL', describeError(err))
    }
  }
```

注意：原 handler 里 `const url = new URL(...)` 与 `const endpoint = ...` 两行已上移到 method 分支之前，**不要留重复声明**（否则 `node --check` 报语法错）。

- [ ] **步骤 7：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/lib/ops.js
node --check dsh-plugin/lib/route.js
```

预期：**38 passed**（0 failed）；两条 `node --check` 退出码 0。

- [ ] **步骤 8：Commit（路径限定）**

```powershell
git commit -m "feat: 面板 Host 图片/表情字节代理与收藏端点（期3 任务 1/5）" -- dsh-plugin/lib/ops.js dsh-plugin/lib/route.js tests/test_dsh_plugin.py
```

---

## 任务 2：Client 图片与表情渲染

**文件：**
- 修改：`dsh-plugin/client.js`（状态区、`consoleUrl` 后的模块级辅助函数、running 效果、`selectChat`、`bulkSetAi` 后的函数区、`renderMessage`、`STYLES`）
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的测试**

追加：

```python
def test_client_image_and_emoji_rendering():
    """图片与表情走同源字节代理渲染，失败回退占位，表情带收藏/下载。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("dsh-deepfeiyu-msg-image", "dsh-deepfeiyu-msg-image-link",
                  "/deepfeiyu/image?", "/deepfeiyu/emoji?",
                  "meta.emoji", "emojiFavs", "markMediaFailed", "favEmoji",
                  "emojis.list", "emoji.fav", "dsh-deepfeiyu-emoji-actions",
                  "loading: 'lazy'", "dsh-deepfeiyu-emoji-error"):
        assert token in source, f"client.js 缺少 {token}"
    # 图片必须新标签打开原图（对齐控制台）
    assert "target: '_blank'" in source, "图片链接未用新标签打开"
    # 媒体失败回退不能用 DOM 手术：轮询重渲染会来回打架
    assert "replaceWith" not in source, "禁止 DOM 手术式回退，用状态记账"
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_client_image_and_emoji_rendering -q
```

预期：**1 failed**，报 `client.js 缺少 dsh-deepfeiyu-msg-image`。

- [ ] **步骤 3：新增媒体状态**

状态区（`const [selectedChats, setSelectedChats] = ...` 之后）插入：

```js
          // 富媒体回退：加载失败的图片/表情按 key 记账，轮询重渲染不再重复打请求。
          const [mediaFailed, setMediaFailed] = React.useState(() => new Set());
          // 表情收藏态：控制台运行时拉一次表情墙，收藏成功后本地追加。
          const [emojiFavs, setEmojiFavs] = React.useState(() => new Set());
          const [emojiFavError, setEmojiFavError] = React.useState('');
```

- [ ] **步骤 4：模块级 URL 辅助函数**

`consoleUrl` 函数（`:308-311` 附近）之后插入：

```js
    /** 图片字节代理地址：同源 GET，令牌只在 Host 内使用。 */
    function imageUrl(uid, msgId) {
      return '/deepfeiyu/image?uid=' + encodeURIComponent(uid) + '&msg=' + encodeURIComponent(msgId);
    }

    /** 表情字节代理地址；download=1 时由控制台加 Content-Disposition。 */
    function emojiSrc(info) {
      return '/deepfeiyu/emoji?url=' + encodeURIComponent(info.url)
        + (info.md5 ? '&md5=' + encodeURIComponent(info.md5) : '');
    }
```

- [ ] **步骤 5：三个操作函数**

会话控制函数区（`bulkSetAi` 之后、`toggleSelectMode` 之前的位置任选，紧挨着放）插入：

```js
          function markMediaFailed(key) {
            if (!key) return;
            setMediaFailed((previous) => (previous.has(key) ? previous : new Set(previous).add(key)));
          }

          async function loadEmojiFavs() {
            try {
              const value = await call('emojis.list', { limit: 500 });
              if (!mounted.current || !Array.isArray(value)) return;
              setEmojiFavs(new Set(value.map((item) => item && item.url).filter(Boolean)));
            } catch {
              // 表情墙拉不到只影响「已收藏」标记：静默降级，不打扰消息流。
            }
          }

          async function favEmoji(info) {
            setEmojiFavError('');
            try {
              await call('emoji.fav', {
                url: info.url,
                md5: info.md5 || '',
                width: Number(info.width) || 0,
                height: Number(info.height) || 0,
              }, 15000);
              if (!mounted.current) return;
              setEmojiFavs((previous) => new Set(previous).add(info.url));
            } catch (error0) {
              if (mounted.current) setEmojiFavError(errorMessage(error0));
            }
          }
```

- [ ] **步骤 6：控制台运行时拉一次收藏列表、切会话清回退记账**

1. 「控制台开始运行时拉账号/统计」效果里（`void loadAccounts(); void loadStats();` 之后）加一行：

```js
              void loadEmojiFavs();
```

2. `selectChat` 里 `setSelectMode(false); setSelectedChats(new Set());` 之后加：

```js
            setMediaFailed(new Set());
            setEmojiFavError('');
```

- [ ] **步骤 7：renderMessage 加图片与表情分支**

1. `const content = rawContent && typeText && ... ? '' : rawContent;`（`:1037`，**本任务保留不动**，任务 3 才删）之后插入：

```js
            const imageId = message.msg_type === 'image' && message.id ? String(message.id) : '';
            const emojiInfo = message.meta && message.meta.emoji && message.meta.emoji.url ? message.meta.emoji : null;
            const imageKey = imageId ? 'image:' + imageId : '';
            const emojiKey = emojiInfo ? 'emoji:' + emojiInfo.url : '';
```

2. 气泡体（现为 `content || typeText ? ... : '（空消息）'` 三行）整体替换为：

```js
              imageId
                ? (mediaFailed.has(imageKey)
                    ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, '[图片]')
                    : h('a', {
                        className: 'dsh-deepfeiyu-msg-image-link',
                        href: imageUrl(selectedUid, imageId),
                        target: '_blank',
                        rel: 'noopener',
                        title: '点开查看原图',
                      },
                      h('img', {
                        className: 'dsh-deepfeiyu-msg-image',
                        src: imageUrl(selectedUid, imageId),
                        alt: '图片',
                        loading: 'lazy',
                        onError: () => markMediaFailed(imageKey),
                      })))
                : emojiInfo
                  ? (mediaFailed.has(emojiKey)
                      ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, '[表情]')
                      : h('span', { className: 'dsh-deepfeiyu-msg-emoji-wrap' },
                          h('img', {
                            className: 'dsh-deepfeiyu-msg-emoji',
                            src: emojiSrc(emojiInfo),
                            alt: '表情',
                            loading: 'lazy',
                            onError: () => markMediaFailed(emojiKey),
                          }),
                          h('span', { className: 'dsh-deepfeiyu-emoji-actions' },
                            h('button', {
                              type: 'button',
                              className: 'dsh-deepfeiyu-btn' + (emojiFavs.has(emojiInfo.url) ? ' dsh-deepfeiyu-btn-on' : ''),
                              title: emojiFavs.has(emojiInfo.url) ? '已收藏' : '收藏到表情面板',
                              onClick: () => { void favEmoji(emojiInfo); },
                            }, emojiFavs.has(emojiInfo.url) ? '★已收藏' : '★收藏'),
                            h('a', {
                              className: 'dsh-deepfeiyu-emoji-download',
                              href: emojiSrc(emojiInfo) + '&download=1',
                              download: 'emoji.gif',
                              title: '下载',
                            }, '⬇')),
                          emojiFavError
                            ? h('span', { className: 'dsh-deepfeiyu-emoji-error', role: 'alert' }, emojiFavError)
                            : null))
                  : (content || typeText
                      ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, content)
                      : h('span', { className: 'dsh-deepfeiyu-msg-text' }, '（空消息）'))));
```

（最后的 fallback 与原三行语义一致，只是缩进进了一层；`typeText` 标签行保持在气泡首行不动。）

- [ ] **步骤 8：STYLES 新增媒体样式**

在 `.dsh-deepfeiyu-msg-text {...}`（`:157`）规则之后插入：

```css
.dsh-deepfeiyu-msg-image-link { display: inline-block; max-width: 100%; }
.dsh-deepfeiyu-msg-image { display: block; max-width: 260px; max-height: 260px; border-radius: 8px; object-fit: cover; background: var(--dsw-alias-bg-layer-2, #f5f6f7); }
.dsh-deepfeiyu-msg-emoji-wrap { display: inline-flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.dsh-deepfeiyu-msg-emoji { max-width: 160px; max-height: 120px; object-fit: contain; }
.dsh-deepfeiyu-emoji-actions { display: inline-flex; gap: 6px; opacity: 0; transition: opacity .15s; }
.dsh-deepfeiyu-msg-emoji-wrap:hover .dsh-deepfeiyu-emoji-actions, .dsh-deepfeiyu-emoji-actions:focus-within { opacity: 1; }
.dsh-deepfeiyu-emoji-download { font-size: 12px; line-height: 20px; color: var(--dsw-alias-label-secondary, #61666b); text-decoration: none; }
.dsh-deepfeiyu-emoji-error { color: var(--dsw-alias-state-error-primary, #ec1313); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
```

- [ ] **步骤 9：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/client.js
```

预期：**39 passed**；`node --check` 退出码 0。

- [ ] **步骤 10：Commit（路径限定）**

```powershell
git commit -m "feat: 面板图片与表情富渲染含收藏下载（期3 任务 2/5）" -- dsh-plugin/client.js tests/test_dsh_plugin.py
```

---

## 任务 3：引用块与其余类型对齐（删除占位去重）

**文件：**
- 修改：`dsh-plugin/client.js`（`renderMessage`、`STYLES`）
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的测试**

追加：

```python
def test_client_quote_and_fallback_alignment():
    """引用渲染成引用块并剥正文尾巴；其余类型按控制台同款显示真实文本。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("meta.refer", "dsh-deepfeiyu-msg-quote", "dsh-deepfeiyu-msg-quote-name",
                  "dsh-deepfeiyu-msg-quote-body", "dsh-deepfeiyu-msg-quote-img",
                  "refer.msg_id", "refer.emoji", "String(refer.type)"):
        assert token in source, f"client.js 缺少 {token}"
    # 去重吞内容的逻辑必须删除——这正是「只剩占位符」的根因
    assert "rawContent === '[' + typeText + ']'" not in source, "去重逻辑仍在，会把 [系统消息] 等真实文本吞掉"
    # 正文尾部的 [引用…] 要剥掉，由引用块单独渲染
    assert r"\[引用[^\n]*\]\s*$" in source, "未剥离正文尾部的引用摘要"
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_client_quote_and_fallback_alignment -q
```

预期：**1 failed**，报 `client.js 缺少 meta.refer`。

- [ ] **步骤 3：renderMessage 加引用分支并删除去重**

1. 删除这两行（任务 2 曾原样保留的注释 + 去重）：

```js
            // 图片/语音等摘要本身就是「[图片]」这类占位，类型标签已经说明，不再重复一遍。
            const content = rawContent && typeText && rawContent === '[' + typeText + ']' ? '' : rawContent;
```

2. 在 `const imageKey/emojiKey` 两行之后插入：

```js
            const refer = message.meta && message.meta.refer ? message.meta.refer : null;
            const quoteCapable = message.msg_type === 'text' || message.msg_type === 'link';
```

3. 气泡体里 `: emojiInfo ? (...)` 分支的收尾 `)` 之后、fallback 之前插入引用分支（把 `: (content || typeText ...` 的 `:` 前面接上），完整气泡体结构为「图片 → 表情 → 引用 → fallback」：

```js
                  : refer && quoteCapable
                    ? h('span', { className: 'dsh-deepfeiyu-msg-text' },
                        rawContent.replace(/\s*\[引用[^\n]*\]\s*$/, '').trim(),
                        h('span', { className: 'dsh-deepfeiyu-msg-quote' },
                          h('span', { className: 'dsh-deepfeiyu-msg-quote-name' }, refer.displayname || '引用消息'),
                          String(refer.type) === '3' && (refer.msg_id || message.id)
                            ? (mediaFailed.has('image:' + String(refer.msg_id || message.id))
                                ? h('span', { className: 'dsh-deepfeiyu-msg-quote-body' }, '[图片]')
                                : h('img', {
                                    className: 'dsh-deepfeiyu-msg-quote-img',
                                    src: imageUrl(selectedUid, String(refer.msg_id || message.id)),
                                    alt: '引用图片',
                                    loading: 'lazy',
                                    onError: () => markMediaFailed('image:' + String(refer.msg_id || message.id)),
                                  }))
                            : refer.emoji && refer.emoji.url
                              ? (mediaFailed.has('emoji:' + refer.emoji.url)
                                  ? h('span', { className: 'dsh-deepfeiyu-msg-quote-body' }, '[表情]')
                                  : h('img', {
                                      className: 'dsh-deepfeiyu-msg-quote-img',
                                      src: emojiSrc(refer.emoji),
                                      alt: '引用表情',
                                      loading: 'lazy',
                                      onError: () => markMediaFailed('emoji:' + refer.emoji.url),
                                    }))
                              : h('span', { className: 'dsh-deepfeiyu-msg-quote-body' }, refer.summary || '')))
                    : (rawContent || typeText
                        ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, rawContent)
                        : h('span', { className: 'dsh-deepfeiyu-msg-text' }, '（空消息）'))));
```

> **一致性检查（步骤 1 删掉 `const content` 后，`content` 在 renderMessage 里必须一个不剩）**：整个函数体里若还残留 `content` 标识符（引用分支用 `rawContent`、fallback 也用 `rawContent`），就是漏改——`node --check` **查不出**未定义标识符，只有浏览器运行时才炸。改完用 `grep -n "content" 定位 renderMessage 函数体人工确认（`rawContent`、`typeText`、`msg_type` 等含 content 子串的除外）。

（若步骤 3 的整块与现有缩进对不上，以「图片 → 表情 → 引用 → fallback」四段嵌套为准手工对齐；`node --check` 会兜底。）

- [ ] **步骤 4：STYLES 新增引用样式**

`.dsh-deepfeiyu-emoji-error` 规则（任务 2 加的）之后插入：

```css
.dsh-deepfeiyu-msg-quote { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; padding: 4px 8px; border-left: 3px solid var(--dsw-alias-border-l2, #0000001a); background: var(--dsw-alias-bg-layer-2, #f5f6f7); border-radius: 0 6px 6px 0; font-size: 12px; color: var(--dsw-alias-label-secondary, #61666b); }
.dsh-deepfeiyu-msg-quote-name { font-weight: 500; }
.dsh-deepfeiyu-msg-quote-body { overflow-wrap: anywhere; }
.dsh-deepfeiyu-msg-quote-img { max-width: 200px; max-height: 160px; border-radius: 6px; object-fit: cover; }
```

> 多行还原无需改：`.dsh-deepfeiyu-msg-text` 已有 `white-space: pre-wrap`（`client.js:157`），聊天记录转录本来就会换行——被吞的是去重逻辑，不是样式。

- [ ] **步骤 5：运行测试验证通过 + JS 语法**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
node --check dsh-plugin/client.js
```

预期：**40 passed**；`node --check` 退出码 0。

- [ ] **步骤 6：Commit（路径限定）**

```powershell
git commit -m "feat: 面板引用块渲染并对齐控制台文本回退（期3 任务 3/5）" -- dsh-plugin/client.js tests/test_dsh_plugin.py
```

---

## 任务 4：文档同步 + 全量验证

**文件：**
- 修改：`dsh-plugin/README.md`、`docs/使用说明.md`、`CHANGELOG.md`
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的文档断言**

追加：

```python
def test_readme_documents_rich_messages():
    """期3 富显示必须在插件 README 与使用说明里可见。"""
    readme = (PKG / "README.md").read_text(encoding="utf-8")
    for token in ("图片", "表情", "引用", "fetchBytes", "/deepfeiyu/image"):
        assert token in readme, f"dsh-plugin/README.md 缺少 {token}"
    usage = (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8")
    for token in ("图片", "表情", "引用"):
        assert token in usage, f"docs/使用说明.md 缺少 {token}"
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py::test_readme_documents_rich_messages -q
```

预期：**1 failed**，报 `dsh-plugin/README.md 缺少 fetchBytes`。

- [ ] **步骤 3：改 dsh-plugin/README.md**

1. 顶部组成列表第一条末尾（句号前）追加：`；消息流按控制台同款显示图片缩略图（点开原图）、表情动图（★收藏/⬇下载）与引用块，文件/聊天记录/系统消息显示真实文本`（把原句号改为分号衔接，保持一条列表项）。
2. 组成表 `lib/route.js` 行：在 `chat.ai` / `chat.resume` / `chats.batchAi` 之后补 `、GET /deepfeiyu/image · /deepfeiyu/emoji 字节代理、emojis.list / emoji.fav`。
3. 「## 安全」节 `- **令牌不落盘**` 条目之后新增：

```markdown
- **媒体字节也走同源路由**：图片与表情由 Host 带令牌向控制台代取，经 `/deepfeiyu/image`、`/deepfeiyu/emoji` 同源 GET 回给页面（同样过 `requestRejection` 鉴权，单张上限 25MB）；令牌不进 URL、不回显给页面。
```

- [ ] **步骤 4：改 docs/使用说明.md §16.1**

「会话」那一条（期1 改过的 `- 会话：…` 整条）末尾追加一句：

```markdown
 消息流与控制台同款显示：图片缩略图（点开原图）、表情动图（可收藏/下载）、引用消息的引用块（含被引图片/表情），文件、聊天记录与入群/被踢等系统消息显示真实文本。
```

- [ ] **步骤 5：跑文档测试与插件测试**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py tests/test_usage_doc.py tests/test_readme_claims.py -q
```

预期：全部通过（`test_dsh_plugin.py` **41 passed**；另两个文件既有用例通过）。

- [ ] **步骤 6：全量测试并记录真实输出**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

预期：0 failed；期1 终值 1609 passed / 1 skipped + 本计划 5 个用例 → 理论 **1614 passed / 1 skipped**。以实际输出为准，失败不得改测试凑绿，如实记录。

- [ ] **步骤 7：写 CHANGELOG 条目**

`CHANGELOG.md` 的 `## [未发布]` 之后、期1 条目（`### 2026-11-05 · DSH 面板会话控制`）之前插入：

```markdown
### 2026-11-05 · DSH 面板消息富展示（期3：图片 / 表情 / 引用对齐控制台）

- 消息流按控制台同款分支渲染：图片内联缩略图（点击新标签打开原图，失败回退 `[图片]`）、表情动图（悬停 ★收藏 / ⬇下载，已收藏状态来自表情墙）、引用消息渲染为引用块（昵称 + 摘要，被引图片/表情直接显示原图，正文尾部不再出现 `[引用…]` 文本）。
- 删除面板「内容等于 `[类型]` 就吞成标签」的去重逻辑：文件 `[文件] 文件名`、聊天记录多行转录、入群/被踢等系统消息的真实文本照常显示（多行由既有 `white-space: pre-wrap` 还原）。
- Host 侧新增同源 GET 字节代理 `GET /deepfeiyu/image`、`GET /deepfeiyu/emoji`（过 `requestRejection` 同源鉴权、令牌只在 Host 内用、单张 25MB 上限、`private, max-age=86400` 缓存、上游错误状态原样透传给 `onerror`）与 `emojis.list` / `emoji.fav` 两个收藏端点（白名单字段构造，多余字段丢弃）。Python 侧零改动、零新依赖。
- 验证状态：`tests/test_dsh_plugin.py` **<步骤 6 后填写实际数字>**；全量 `pytest -q` **<步骤 6 实际输出：passed/skipped 与耗时>**；`node --check`（经 `test_js_syntax`）通过；`python tools/release_snapshot.py --scan-tracked .` 扫描通过、`git diff --check` 干净。实机对照控制台逐类核对见下一条「实机验证」记录。
```

> 两处尖括号是**填写指令**：写入前必须替换成步骤 5/6 的真实数字，禁止保留占位。

- [ ] **步骤 8：脱敏扫描与 diff 检查**

```powershell
.venv\Scripts\python.exe tools/release_snapshot.py --scan-tracked .
git diff --check
```

预期：扫描通过、退出码 0；人工过 `git diff` 确认无密钥、无真实聊天、无本机绝对路径、**无其他会话的文件**。

- [ ] **步骤 9：Commit（路径限定）**

```powershell
git commit -m "docs: 面板消息富展示使用说明与更新日志（期3 任务 4/5）" -- dsh-plugin/README.md docs/使用说明.md CHANGELOG.md tests/test_dsh_plugin.py
```

---

## 任务 5：重载实机验收与收尾

- [ ] **步骤 1：重载插件加载新 Host 半**

`plugin_manager`：`set_plugin` `include:deepfeiyu` → `enabled: false`，再 → `enabled: true`（两次都应 `application: applied`）。已装包是 Junction 指向工作区，**无需重装**。重载后控制台会重启，用 `deepfeiyu_status` 确认 `running` + `reachable`。

- [ ] **步骤 2：请用户刷新页面并执行验收清单**

对应规格 §9：

1. 图片消息显示缩略图，点开新标签是原图；不存在的图回退 `[图片]` 不报错；
2. 表情显示动图；★收藏后**控制台表情墙**出现该表情、按钮变「★已收藏」；⬇触发下载；
3. 引用显示昵称 + 摘要的引用块，被引图片/表情显示原图，正文没有 `[引用…]` 尾巴；
4. 文件、聊天记录（多行完整）、入群/被踢系统消息显示真实文本，不再是标签；
5. 控制台停机时：文本照常显示，图片/表情回退占位，不弹全局错误；
6. 若图片 404/报「未知 endpoint」→ Host 半没加载新代码，回到步骤 1。

- [ ] **步骤 3：把实机结论补进 CHANGELOG 并提交**

在期3 CHANGELOG 条目末尾追加（只写真实结果，未验证项如实标注）：

```markdown
- 实机验证（2026-11-05）：<重载方式、验收 1-6 实际结果；期1 验收回执一并补记或注明仍未回>
```

```powershell
git commit -m "docs: 面板消息富展示实机验证结论（期3 任务 5/5）" -- CHANGELOG.md
```

- [ ] **步骤 4：收尾报告**

汇报：改动文件清单、每条命令真实输出、验收结果、未验证项（真实微信通道下的图片/表情取图、期1 验收欠账）、剩余风险（期2 实时、期4 表情发送仍在队列）。不推送、不开 PR，等用户指示。

---

## 回滚与已知边界

- 任一任务失败且修不动：`git revert` 对应提交；无数据与配置迁移；测试失败不得删断言凑绿。
- 已知边界（如实报告）：表情 CDN 取图依赖控制台本地缓存与网络；`emojis.list` 只拉前 500 条，超出的表情墙旧条目会显示为「未收藏」（控制台自己也是分页加载）；引用的被引图片依赖驱动 `get_image*` 可用性（与控制台同一限制）。
- 队列未动：期2 实时桥、期4 表情发送、期1 实机验收欠账。
