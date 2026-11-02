// 8 个 Agent 工具，全部复用 lib/ops.js 与控制台监督器，和面板路由共享同一实现。
import { describeError } from './ops.js'

const OUTPUT_OBJECT = { type: 'object', additionalProperties: true }

function jsonRender(_args, value) {
  return [{ type: 'text', text: JSON.stringify(value, null, 1) }]
}

function defineTool(name, description, parameters, run) {
  return {
    name,
    description,
    parameters,
    output: { schema: OUTPUT_OBJECT, render: jsonRender },
    async execute(args, exec) {
      try {
        return await run(args || {}, exec)
      } catch (err) {
        throw new Error(describeError(err))
      }
    },
  }
}

function requireText(value, field) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!text) throw new Error(`${field} 不能为空`)
  return text
}

export function registerTools({ ctx, config, api, state, snapshot, supervisor, sendGate }) {
  function add(definition, label) {
    ctx.effect(() => ctx.tools.register(definition), `deepfeiyu.tools.${label}`)
  }

  add(defineTool(
    'deepfeiyu_status',
    '查看 Deep肥鱼微信智能体控制台的运行状态：阶段、端口、版本、是否运行、最近错误；同时探测控制台 HTTP 是否可达。',
    { type: 'object', properties: {}, additionalProperties: false },
    async () => {
      let reachable = false
      try {
        const health = await api.health()
        reachable = true
        state.running = true
        if (typeof health?.version === 'string') state.version = health.version
        if (state.phase === 'stopped') state.phase = 'attached'
      } catch {
        state.running = false
      }
      return { ...snapshot(), console: { reachable } }
    },
  ), 'status')

  add(defineTool(
    'deepfeiyu_accounts',
    '列出 Deep肥鱼控制台里的微信账号（名称、驱动、连接状态、当前账号）。action=activate 可切换当前账号（受控写，不发送消息）。',
    {
      type: 'object',
      properties: {
        action: { type: 'string', enum: ['list', 'activate'], description: 'list（默认）或 activate' },
        account_id: { type: 'string', description: 'action=activate 时必填：账号 id' },
      },
      additionalProperties: false,
    },
    async (args) => {
      const action = args.action === 'activate' ? 'activate' : 'list'
      if (action === 'activate') {
        const id = requireText(args.account_id, 'account_id')
        await api.activateAccount(id)
        return { activated: id, accounts: await api.accounts() }
      }
      return { accounts: await api.accounts() }
    },
  ), 'accounts')

  add(defineTool(
    'deepfeiyu_chats',
    '列出 Deep肥鱼控制台当前账号的会话（按最近消息排序），可按关键词过滤。返回内容可能包含用户隐私信息。',
    {
      type: 'object',
      properties: {
        keyword: { type: 'string', description: '可选：按会话名称过滤' },
        limit: { type: 'integer', description: '返回条数，1-200，默认 200' },
      },
      additionalProperties: false,
    },
    async (args) => ({ chats: await api.chats(args.keyword ? String(args.keyword) : '', args.limit) }),
  ), 'chats')

  add(defineTool(
    'deepfeiyu_messages',
    '读取 Deep肥鱼一个会话的最近消息（文本/图片等类型标签、发送者、时间）。返回内容可能包含用户隐私信息。',
    {
      type: 'object',
      properties: {
        uid: { type: 'string', description: '会话 uid（来自 deepfeiyu_chats）' },
        limit: { type: 'integer', description: '返回条数，1-100，默认 80' },
      },
      required: ['uid'],
      additionalProperties: false,
    },
    async (args) => {
      const uid = requireText(args.uid, 'uid')
      return await api.messages(uid, args.limit)
    },
  ), 'messages')

  add(defineTool(
    'deepfeiyu_send',
    '通过 Deep肥鱼控制台向一个微信会话发送文本消息。发送仍受服务端身份闸门、禁发名单与节流约束；是否弹审批跟随当前会话的 Harness 权限模式。',
    {
      type: 'object',
      properties: {
        uid: { type: 'string', description: '会话 uid（来自 deepfeiyu_chats）' },
        text: { type: 'string', description: '要发送的文本，最多 2000 字' },
      },
      required: ['uid', 'text'],
      additionalProperties: false,
    },
    async (args, exec) => {
      const uid = requireText(args.uid, 'uid')
      const text = requireText(args.text, 'text')
      if (text.length > 2000) throw new Error('text 超过 2000 字上限')
      await sendGate.assertAllowedSend(
        exec,
        '通过 Deep肥鱼向微信会话发送一条消息',
        'Send one WeChat message through DeepFeiyu',
      )
      const message = await api.send(uid, text, 'dsh')
      return { ok: true, uid, message }
    },
  ), 'send')

  add(defineTool(
    'deepfeiyu_stats',
    '读取 Deep肥鱼当前账号的今日统计与 token 用量（进出消息数、AI 回复数、会话数、用量明细）。',
    { type: 'object', properties: {}, additionalProperties: false },
    async () => ({ stats: await api.stats() }),
  ), 'stats')

  add(defineTool(
    'deepfeiyu_capabilities',
    '读取 Deep肥鱼能力系统状态：内置技能（SKILL.md）、插件（plugin.json）、MCP 服务器的启用与连接情况。',
    { type: 'object', properties: {}, additionalProperties: false },
    async () => ({ capabilities: await api.capabilities() }),
  ), 'capabilities')

  add(defineTool(
    'deepfeiyu_console',
    '管理 Deep肥鱼控制台进程：start 拉起、stop 停止、restart 重启、status 只探测。auto 模式下插件会自动管理它自己拉起的进程。',
    {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['start', 'stop', 'restart', 'status'],
          description: '要执行的控制台动作',
        },
      },
      required: ['action'],
      additionalProperties: false,
    },
    async (args) => {
      const action = String(args.action || 'status')
      if (action === 'start') return { ...(await supervisor.start()), action }
      if (action === 'stop') return { ...(await supervisor.stop()), action }
      if (action === 'restart') return { ...(await supervisor.restart()), action }
      if (action === 'status') { await supervisor.probe(); return { ...snapshot(), action } }
      throw new Error(`未知 action: ${action}`)
    },
  ), 'console')
}
