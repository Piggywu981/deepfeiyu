// DSH 侧融合：把 Harness 会话事件变成共享心情判断与桌宠事件。
//
// 硬约束：
// - 只把「判断结果 + 短理由」发给控制台，Harness 原文不出 Host；
// - 控制台不可达时丢弃本轮，不阻塞 Harness；
// - 所有监听/定时器必须在 dispose() 里清理，HMR 重载不能重复注册。
const TURN_TEXT_LIMIT = 2000
const SESSION_CACHE_LIMIT = 32
const PET_DEBOUNCE_MS = 3000

function textOf(message) {
  const blocks = message && Array.isArray(message.content) ? message.content : []
  return blocks
    .filter((block) => block && block.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('\n')
    .trim()
    .slice(0, TURN_TEXT_LIMIT)
}

function sessionTitle(session) {
  const header = session && session.header && typeof session.header === 'object' ? session.header : {}
  const title = header.title || header.name || ''
  return String(title).slice(0, 40)
}

export function createFusion({ ctx, config, api }) {
  const sessions = new Map()
  const disposers = []
  let disposed = false
  let lastPet = ''
  let lastPetAt = 0

  function listen(event, handler) {
    const off = ctx.on(event, handler)
    if (typeof off === 'function') disposers.push(off)
  }

  function cache(session, patch) {
    const id = session && session.id ? String(session.id) : ''
    if (!id) return
    const current = sessions.get(id) || { user: '', reply: '', title: '' }
    Object.assign(current, patch)
    sessions.delete(id)
    sessions.set(id, current)
    while (sessions.size > SESSION_CACHE_LIMIT) {
      sessions.delete(sessions.keys().next().value)
    }
  }

  function postPet(kind, extra = {}) {
    if (disposed) return
    const now = Date.now()
    if (kind === lastPet && now - lastPetAt < PET_DEBOUNCE_MS) return
    lastPet = kind
    lastPetAt = now
    Promise.resolve()
      .then(() => api.dshPetEvent({ kind, ...extra }))
      .catch(() => {})
  }

  listen('session/event', (session, event) => {
    if (!event || typeof event.type !== 'string') return
    const sessionInfo = {
      id: session && session.id ? String(session.id) : '',
      title: sessionTitle(session),
    }
    if (event.type === 'user/message') {
      cache(session, { user: textOf(event.data), title: sessionInfo.title })
      postPet('turn_start', { session: sessionInfo })
      return
    }
    if (event.type === 'assistant/message') {
      const reply = textOf(event.data && event.data.message)
      if (reply) cache(session, { reply })
      return
    }
    if (event.type === 'tool/call') {
      postPet('tool_start', {
        tool: String((event.data && event.data.name) || ''),
        session: sessionInfo,
      })
      return
    }
    if (event.type === 'tool/result') {
      postPet('tool_done', {
        tool: String((event.data && event.data.name) || ''),
        session: sessionInfo,
      })
      return
    }
    if (event.type === 'turn/end') {
      const reason = event.data && event.data.reason ? event.data.reason : {}
      postPet(reason.kind === 'completed' ? 'turn_end' : 'error', { session: sessionInfo })
    }
  })

  listen('agent/error', () => {
    postPet('error')
  })

  function dispose() {
    disposed = true
    for (const off of disposers.splice(0)) {
      try {
        if (typeof off === 'function') off()
      } catch {
        // 卸载途中的清理失败不应阻止其余 disposer 执行。
      }
    }
  }

  return { dispose }
}
