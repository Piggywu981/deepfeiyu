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
    // DSH 融合：绑定/共享心情/宠物事件全部走控制台；判断提示词也由控制台渲染。
    dshState: () => call('/api/dsh/state'),
    dshConfig: (patch) => call('/api/dsh/config', { method: 'POST', body: patch, timeoutMs: 15000 }),
    dshTimeline: (limit = 20) => call(`/api/dsh/timeline?limit=${Math.max(1, Math.min(100, Math.trunc(Number(limit) || 20)))}`),
    dshJudgePrompt: () => call('/api/dsh/judge-prompt', { timeoutMs: 15000 }),
    dshMoodEvent: (payload) => call('/api/dsh/mood-event', { method: 'POST', body: payload, timeoutMs: 20000 }),
    dshPetEvent: (payload) => call('/api/dsh/pet-event', { method: 'POST', body: payload, timeoutMs: 5000 }),
    // 依赖管理：控制台按功能 id 返回状态、异步启动安装、按偏移增量读日志。
    // 安装是后台任务，POST 只负责启动，所以超时放宽到 15s；日志接口可能因缓冲重置返回 reset。
    depsState: () => call('/api/deps/state'),
    depsInstall: (feature) => call('/api/deps/install', { method: 'POST', body: { feature }, timeoutMs: 15000 }),
    depsLog: (feature, offset = 0) => call(
      `/api/deps/install/log?feature=${encodeURIComponent(feature)}&offset=${Math.max(0, Math.trunc(Number(offset) || 0))}`,
    ),
    send: (uid, text, source) => call(`/api/chats/${encodeURIComponent(uid)}/send`, {
      method: 'POST',
      body: { text, source },
      timeoutMs: getConfig().sendTimeoutMs,
    }),
    // 会话控制：转发控制台现成的 AI 开关 / 恢复接管 / 批量操作，只发白名单字段。
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
    // 设置页 P1：模型/人格/更新转发控制台既有 API；写入走窄补丁，响应由控制台校验。
    settingsGet: () => call('/api/settings'),
    settingsPut: (patch) => call('/api/settings', { method: 'PUT', body: patch, timeoutMs: 15000 }),
    settingsTestAi: (payload) => call('/api/settings/test-ai', { method: 'POST', body: payload, timeoutMs: 30000 }),
    personasList: () => call('/api/personas'),
    personasAdd: (payload) => call('/api/personas', { method: 'POST', body: payload, timeoutMs: 15000 }),
    personasDelete: (name) => call(`/api/personas/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    personasSyncWiki: () => call('/api/personas/sync_wiki', { method: 'POST', body: {}, timeoutMs: 60000 }),
    updateCheck: () => call('/api/update/check?force=1', { timeoutMs: 30000 }),
  }
}

/** 把内部错误翻译成面向用户/模型的中文消息。 */
export function describeError(err) {
  if (err instanceof ConsoleDownError) {
    return `${err.message}；请先启动控制台（工具 deepfeiyu_console，或面板上的「启动」按钮）。`
  }
  return String(err?.message || err)
}
