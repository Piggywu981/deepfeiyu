// Deep肥鱼 DSH 插件 Host 半：配置、控制台监督、Agent 工具与面板数据路由。
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createConsoleApi, resolveToken } from './lib/ops.js'
import { createSupervisor } from './lib/supervisor.js'
import { createSendGate } from './lib/approval.js'
import { registerTools } from './lib/tools.js'
import { registerRoute } from './lib/route.js'

export const inject = ['tools', 'subprocess', 'connection', 'webServer']

const DEFAULTS = {
  mode: 'auto',
  autostart: true,
  host: '127.0.0.1',
  port: 8765,
  accessToken: '',
  requestTimeoutMs: 5000,
  sendTimeoutMs: 60000,
  panelTitle: 'Deep肥鱼',
  pythonPath: '',
  projectDir: '',
}

function positiveInt(value, fallback, field) {
  if (value === undefined || value === null || value === '') return fallback
  const num = Number(value)
  if (!Number.isInteger(num) || num <= 0) throw new Error(`deepfeiyu: ${field} 必须是正整数`)
  return num
}

function normalizeConfig(rawConfig, pkgRoot) {
  const raw = rawConfig && typeof rawConfig === 'object' ? rawConfig : {}
  const projectDir = resolve(String(raw.projectDir || join(pkgRoot, '..')))
  const isWindows = process.platform === 'win32'
  const pythonPath = resolve(String(raw.pythonPath || join(projectDir, '.venv', isWindows ? 'Scripts/python.exe' : 'bin/python')))
  const mode = raw.mode === 'attach' ? 'attach' : 'auto'
  const port = positiveInt(raw.port, DEFAULTS.port, 'port')
  if (port > 65535) throw new Error('deepfeiyu: port 超出范围')
  const autostart = raw.autostart === undefined ? DEFAULTS.autostart : Boolean(raw.autostart)
  return {
    ...DEFAULTS,
    ...raw,
    projectDir,
    pythonPath,
    mode,
    port,
    autostart,
    host: String(raw.host || DEFAULTS.host),
    accessToken: raw.accessToken ? String(raw.accessToken) : '',
    requestTimeoutMs: positiveInt(raw.requestTimeoutMs, DEFAULTS.requestTimeoutMs, 'requestTimeoutMs'),
    sendTimeoutMs: positiveInt(raw.sendTimeoutMs, DEFAULTS.sendTimeoutMs, 'sendTimeoutMs'),
    panelTitle: String(raw.panelTitle || DEFAULTS.panelTitle),
  }
}

export function apply(ctx, rawConfig) {
  const pkgRoot = dirname(fileURLToPath(import.meta.url))
  const config = normalizeConfig(rawConfig, pkgRoot)

  const state = {
    phase: 'stopped',
    running: false,
    version: '',
    lastError: '',
    startedAt: 0,
    spawnedByPlugin: false,
  }

  let cachedToken
  const api = createConsoleApi({
    getConfig: () => config,
    getToken: async () => {
      if (cachedToken === undefined) cachedToken = await resolveToken(config)
      return cachedToken
    },
  })

  const snapshot = () => ({
    phase: state.phase,
    running: state.running,
    version: state.version,
    port: config.port,
    host: config.host,
    mode: config.mode,
    title: config.panelTitle,
    lastError: state.lastError,
    startedAt: state.startedAt,
    spawnedByPlugin: state.spawnedByPlugin,
  })

  const supervisor = createSupervisor({ ctx, config, state, api })
  const sendGate = createSendGate(ctx)

  registerTools({ ctx, config, api, state, snapshot, supervisor, sendGate })
  registerRoute({ ctx, config, api, state, snapshot, supervisor, sendGate })

  ctx.effect(() => () => supervisor.dispose(), 'deepfeiyu.supervisor')

  supervisor.boot().catch((err) => {
    state.phase = 'error'
    state.lastError = String(err?.message || err)
  })

  ctx.logger.info('deepfeiyu: active (mode=%s, port=%d, project=%s)', config.mode, config.port, config.projectDir)
}
