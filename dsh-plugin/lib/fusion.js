// DSH 侧融合：把 Harness 会话事件变成共享心情判断与桌宠事件。
//
// 硬约束：
// - 只把「判断结果 + 短理由」发给控制台，Harness 原文不出 Host；
// - 控制台不可达时丢弃本轮，不阻塞 Harness；
// - 所有监听/定时器必须在 dispose() 里清理，HMR 重载不能重复注册。
const TURN_TEXT_LIMIT = 2000
const SESSION_CACHE_LIMIT = 32
const PET_DEBOUNCE_MS = 3000
const JUDGE_MIN_GAP_MS = 90_000
const JUDGE_DAILY_MAX = 40
const JUDGE_MODEL_MAX_TOKENS = 300
const JUDGE_PROMPT_CACHE_MS = 5 * 60_000
const MOOD_REFRESH_MS = 60_000
const DIM_KEYS = ['happy', 'calm', 'sweet', 'heartache', 'worry', 'sad', 'jealous']

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
  // 心情判断与提示词注入的运行时状态：只在内存里，不写任何文件。
  let judgePrompt = null
  let promptAt = 0
  let moodText = ''
  let moodAt = 0
  let judgeAt = 0
  let judgeDay = ''
  let judgeCount = 0
  let judgeBusy = false

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
      void refreshMood()
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
      if (reason.kind === 'completed') void judgeSession(session)
      void refreshMood()
    }
  })

  listen('agent/error', () => {
    postPet('error')
  })

  function todayKey() {
    return new Date().toISOString().slice(0, 10)
  }

  async function loadJudgePrompt() {
    if (judgePrompt && Date.now() - promptAt < JUDGE_PROMPT_CACHE_MS) return judgePrompt
    judgePrompt = await api.dshJudgePrompt()
    promptAt = Date.now()
    return judgePrompt
  }

  function parseJudge(text) {
    const raw = String(text || '')
    const start = raw.indexOf('{')
    const end = raw.lastIndexOf('}')
    if (start < 0 || end <= start) return null
    try {
      const data = JSON.parse(raw.slice(start, end + 1))
      return data && typeof data === 'object' ? data : null
    } catch {
      return null
    }
  }

  async function streamText(options) {
    let text = ''
    for await (const chunk of ctx.get('llm').stream(options)) {
      if (chunk && chunk.type === 'text-delta' && typeof chunk.text === 'string') {
        text += chunk.text
      }
      if (chunk && chunk.type === 'finish' && chunk.reason && chunk.reason.kind === 'error') {
        return ''
      }
    }
    return text
  }

  async function judgeSession(session) {
    if (judgeBusy || disposed) return
    const id = session && session.id ? String(session.id) : ''
    const item = sessions.get(id)
    if (!item || !item.user || !item.reply) return
    const day = todayKey()
    if (day !== judgeDay) {
      judgeDay = day
      judgeCount = 0
    }
    if (judgeCount >= JUDGE_DAILY_MAX) return
    if (judgeAt && Date.now() - judgeAt < JUDGE_MIN_GAP_MS) return
    const llm = ctx.get('llm')
    const defaultModel = ctx.get('agentDefaultModel')
    if (!llm || typeof llm.stream !== 'function' || !defaultModel) return
    const selection = defaultModel.currentSelection()
    if (!selection || !selection.provider || !selection.model) return
    judgeBusy = true
    judgeAt = Date.now()
    judgeCount += 1
    try {
      const prompt = await loadJudgePrompt()
      const userText = '对方这轮说：\n' + item.user
        + '\n\n你这轮回复：\n' + item.reply
        + '\n\n请判断你这一轮的心情变化，只输出 JSON。'
      const raw = await streamText({
        provider: String(selection.provider),
        model: String(selection.model),
        system: String(prompt && prompt.system ? prompt.system : ''),
        messages: [{ role: 'user', content: [{ type: 'text', text: userText }] }],
        maxTokens: JUDGE_MODEL_MAX_TOKENS,
        temperature: 0,
      })
      const parsed = parseJudge(raw)
      if (!parsed) return
      const dims = {}
      for (const key of DIM_KEYS) {
        const value = Number(parsed.dims && parsed.dims[key])
        dims[key] = Number.isFinite(value) ? Math.max(-10, Math.min(10, value)) : 0
      }
      const moodDelta = Number(parsed.moodDelta)
      const confidence = Number(parsed.confidence)
      if (!Number.isFinite(moodDelta) || !Number.isFinite(confidence)) return
      await api.dshMoodEvent({
        moodDelta: Math.max(-10, Math.min(10, moodDelta)),
        dims,
        confidence: Math.max(0, Math.min(100, Math.round(confidence))),
        reason: typeof parsed.reason === 'string' ? parsed.reason.slice(0, 60) : '',
        session: { id, title: item.title || '' },
      })
    } catch (error) {
      ctx.logger.warn('deepfeiyu: 融合心情判断失败 %s', error?.message || error)
    } finally {
      judgeBusy = false
    }
  }

  async function refreshMood() {
    if (disposed || (moodAt && Date.now() - moodAt < MOOD_REFRESH_MS)) return
    try {
      const state = await api.dshState()
      moodAt = Date.now()
      const mood = state && state.mood ? state.mood : null
      // 控制台没开心情时不注入：与 inject_block 的 enabled 判断保持一致。
      if (!mood || typeof mood.mood !== 'number' || mood.enabled === false) {
        moodText = ''
        return
      }
      const last = state.timeline && state.timeline[0] ? state.timeline[0] : null
      const reason = last && last.source === 'dsh' && last.reason
        ? '；刚才：' + String(last.reason).slice(0, 60)
        : ''
      moodText = '（你此刻的心情：' + Math.round(mood.mood) + '/100' + reason + '）'
    } catch {
      moodText = ''
    }
  }

  // 共享心情注入 Harness 提示词：60s 刷新一次；控制台不可达时返回空串，不阻塞对话。
  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt && typeof systemPrompt.context === 'function') {
    const off = systemPrompt.context({
      name: 'deepfeiyu.mood',
      order: 50,
      text: () => moodText,
    })
    if (typeof off === 'function') disposers.push(off)
  }

  const timer = ctx.get('timer')
  if (timer && typeof timer.interval === 'function') {
    const off = timer.interval(() => { void refreshMood() }, MOOD_REFRESH_MS)
    if (typeof off === 'function') disposers.push(off)
  }

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
