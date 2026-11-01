// 控制台 HTTP 操作层：面板路由与 Agent 工具共用同一实现，避免两套逻辑漂移。
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

/** 控制台未运行 / 不可达（可以提示用户先启动）。 */
export class ConsoleDownError extends Error {
  constructor(message) {
    super(message)
    this.name = 'ConsoleDownError'
    this.code = 'CONSOLE_DOWN'
  }
}

/** 控制台返回了非 2xx 或业务失败。 */
export class ConsoleApiError extends Error {
  constructor(status, message) {
    super(message)
    this.name = 'ConsoleApiError'
    this.code = 'CONSOLE_HTTP'
    this.status = status
  }
}

/**
 * 访问令牌优先级：插件配置 → 环境变量 → 项目 data/config.json（只读）。
 * 令牌永远不写日志、不回显给 Client、不进入工具结果。
 */
export async function resolveToken(config) {
  if (config.accessToken) return String(config.accessToken)
  if (process.env.DEEPFEIYU_ACCESS_TOKEN) return process.env.DEEPFEIYU_ACCESS_TOKEN
  try {
    const raw = await readFile(join(config.projectDir, 'data', 'config.json'), 'utf8')
    const value = JSON.parse(raw)?.server?.access_token
    return typeof value === 'string' ? value : ''
  } catch {
    return ''
  }
}

export function createConsoleApi({ getConfig, getToken }) {
  function baseUrl() {
    const c = getConfig()
    return `http://${c.host}:${c.port}`
  }

  async function call(path, { method = 'GET', body, timeoutMs = 5000 } = {}) {
    const c = getConfig()
    const headers = { accept: 'application/json' }
    const token = await getToken()
    if (token) headers['x-access-token'] = token
    if (body !== undefined) headers['content-type'] = 'application/json'
    let response
    try {
      response = await fetch(`${baseUrl()}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(Math.max(1000, timeoutMs)),
      })
    } catch (err) {
      const code = err?.cause?.code || err?.code || err?.name || 'UNKNOWN'
      throw new ConsoleDownError(`无法连接 Deep肥鱼控制台 ${baseUrl()}（${code}）`)
    }
    const text = await response.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    if (!response.ok) {
      const detail = typeof data?.detail === 'string'
        ? data.detail
        : typeof data === 'string'
          ? data.slice(0, 300)
          : JSON.stringify(data ?? '').slice(0, 300)
      throw new ConsoleApiError(response.status, `控制台返回 ${response.status}：${detail || '请求失败'}`)
    }
    return data
  }

  return {
    health: () => call('/api/health', { timeoutMs: 3000 }),
    status: () => call('/api/status'),
    accounts: () => call('/api/accounts'),
    activateAccount: (id) => call(`/api/accounts/${encodeURIComponent(id)}/activate`, { method: 'POST', body: {} }),
    chats: (keyword = '', limit = 200) => {
      const safeLimit = Math.max(1, Math.min(200, Math.trunc(Number(limit) || 200)))
      return call(`/api/chats?keyword=${encodeURIComponent(keyword)}&limit=${safeLimit}`)
    },
    messages: (uid, limit = 80) => {
      const safeLimit = Math.max(1, Math.min(100, Math.trunc(Number(limit) || 80)))
      return call(`/api/chats/${encodeURIComponent(uid)}/messages?limit=${safeLimit}`)
    },
    stats: () => call('/api/stats'),
    capabilities: () => call('/api/capabilities'),
    send: (uid, text, source) => call(`/api/chats/${encodeURIComponent(uid)}/send`, {
      method: 'POST',
      body: { text, source },
      timeoutMs: getConfig().sendTimeoutMs,
    }),
  }
}

/** 把内部错误翻译成面向用户/模型的中文消息。 */
export function describeError(err) {
  if (err instanceof ConsoleDownError) {
    return `${err.message}；请先启动控制台（工具 deepfeiyu_console，或面板上的「启动」按钮）。`
  }
  return String(err?.message || err)
}
