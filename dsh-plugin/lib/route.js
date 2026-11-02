// 面板数据路由：同源 POST /deepfeiyu/<endpoint>。
// 自定义 webServer 路由默认无鉴权，必须显式调用 Connection 的 requestRejection 复用浏览器 Cookie / Host-Origin 校验。
import { describeError, ConsoleDownError, ConsoleApiError } from './ops.js'
import { createDshPluginsApi } from './dsh-plugins.js'

export const ROUTE_PREFIX = '/deepfeiyu'
const MAX_BODY_BYTES = 256 * 1024

function json(res, status, payload) {
  const text = JSON.stringify(payload)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(text),
  })
  res.end(text)
}

function ok(res, value) {
  json(res, 200, { ok: true, value })
}

function fail(res, status, code, message) {
  json(res, status, { ok: false, error: { code, message, details: {} } })
}

async function readJsonBody(req) {
  const chunks = []
  let size = 0
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error('请求体过大'), { code: 'BODY_TOO_LARGE' })
    chunks.push(chunk)
  }
  if (chunks.length === 0) return {}
  const text = Buffer.concat(chunks).toString('utf8').trim()
  if (!text) return {}
  const parsed = JSON.parse(text)
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw Object.assign(new Error('请求体必须是 JSON 对象'), { code: 'INVALID_BODY' })
  }
  return parsed
}

export function registerRoute({ ctx, config, api, state, snapshot, supervisor, sendGate }) {
  // 「关联 DSH 插件」只操作 Host 的 pluginManager 服务，和控制台 HTTP 无关；
  // 服务缺失时 state() 返回 available:false，面板照常可用。
  const dshPlugins = createDshPluginsApi({ ctx })
  const routes = {
    state: async () => snapshot(),
    'console.start': async () => supervisor.start(),
    'console.stop': async () => supervisor.stop(),
    'console.restart': async () => supervisor.restart(),
    'accounts.list': async () => ({ accounts: await api.accounts() }),
    'accounts.activate': async (payload) => {
      if (!payload.id) throw Object.assign(new Error('缺少 id'), { code: 'INVALID_ARGS' })
      await api.activateAccount(String(payload.id))
      return { active: String(payload.id), accounts: await api.accounts() }
    },
    'chats.list': async (payload) => ({
      chats: await api.chats(payload.keyword ? String(payload.keyword) : '', payload.limit),
    }),
    'messages.list': async (payload) => {
      if (!payload.uid) throw Object.assign(new Error('缺少 uid'), { code: 'INVALID_ARGS' })
      return await api.messages(String(payload.uid), payload.limit)
    },
    'send.text': async (payload) => {
      const uid = typeof payload.uid === 'string' ? payload.uid.trim() : ''
      const text = typeof payload.text === 'string' ? payload.text.trim() : ''
      if (!uid) throw Object.assign(new Error('缺少 uid'), { code: 'INVALID_ARGS' })
      if (!text) throw Object.assign(new Error('text 不能为空'), { code: 'INVALID_ARGS' })
      if (text.length > 2000) throw Object.assign(new Error('text 超过 2000 字上限'), { code: 'INVALID_ARGS' })
      // 面板发送等价于控制台里人工点发送：source=human，不排队；仍受服务端身份闸门与禁发名单约束。
      const message = await api.send(uid, text, 'human')
      return { message }
    },
    stats: async () => ({ stats: await api.stats() }),
    capabilities: async () => ({ capabilities: await api.capabilities() }),
    // 设置页依赖管理：直接透传控制台 body，client 的 call() 会解包 {ok:true,value}。
    'deps.state': async () => api.depsState(),
    'deps.install': async (payload) => {
      if (!payload.feature || typeof payload.feature !== 'string') {
        throw Object.assign(new Error('缺少 feature'), { code: 'INVALID_ARGS' })
      }
      return await api.depsInstall(String(payload.feature))
    },
    'deps.log': async (payload) => {
      if (!payload.feature || typeof payload.feature !== 'string') {
        throw Object.assign(new Error('缺少 feature'), { code: 'INVALID_ARGS' })
      }
      return await api.depsLog(String(payload.feature), payload.offset)
    },
    // 关联 DSH 插件：只转发白名单 id，包名与安装参数在 lib/dsh-plugins.js 里写死。
    // 安装是后台任务，这里只负责启动；进度与结果由 state 轮询读取。
    'dshPlugins.state': async () => dshPlugins.state(),
    'dshPlugins.install': async (payload) => {
      const id = typeof payload.id === 'string' ? payload.id.trim() : ''
      if (!id) throw Object.assign(new Error('缺少 id'), { code: 'INVALID_ARGS' })
      return await dshPlugins.install(id)
    },
    // DSH 融合：代理控制台 /api/dsh/*；config 只接受白名单字段。
    'fusion.state': async () => api.dshState(),
    'fusion.config': async (payload) => {
      const patch = {}
      if (typeof payload.account_id === 'string') patch.account_id = payload.account_id
      for (const key of ('judge_enabled', 'pet_events_enabled', 'cross_reason_enabled')) {
        if (typeof payload[key] === 'boolean') patch[key] = payload[key]
      }
      if (!Object.keys(patch).length) {
        throw Object.assign(new Error('没有可更新的字段'), { code: 'INVALID_ARGS' })
      }
      return await api.dshConfig(patch)
    },
    'fusion.timeline': async (payload) => await api.dshTimeline(payload.limit),
  }

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
    const url = new URL(req.url || '/', 'http://127.0.0.1')
    const endpoint = url.pathname.slice(ROUTE_PREFIX.length).replace(/^\/+/, '')
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

  const dispose = ctx.webServer.register({ kind: 'prefix', path: ROUTE_PREFIX, handler })
  ctx.effect(() => dispose, 'deepfeiyu.route')
}
