// 控制台进程监督：auto 模式自动拉起 .venv Python，attach 模式只探测；插件卸载时终止自己拉起的进程。
import { access } from 'node:fs/promises'
import { join } from 'node:path'

const POLL_RUNNING_MS = 5000
const POLL_IDLE_MS = 15000
const START_DEADLINE_MS = 60000

export function createSupervisor({ ctx, config, state, api }) {
  let handle = null
  let timer = null
  let disposed = false

  async function fileExists(path) {
    try { await access(path); return true } catch { return false }
  }

  function schedule(ms = POLL_RUNNING_MS) {
    if (disposed) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(async () => {
      timer = null
      await probe()
      schedule(state.running ? POLL_RUNNING_MS : POLL_IDLE_MS)
    }, ms)
    timer.unref?.()
  }

  async function probe() {
    if (disposed) return false
    try {
      const health = await api.health()
      if (typeof health?.version === 'string') state.version = health.version
      state.running = true
      state.lastError = ''
      if (handle) state.phase = 'running'
      else if (state.phase !== 'attached' && state.phase !== 'starting') state.phase = 'attached'
      return true
    } catch {
      state.running = false
      if (!handle && (state.phase === 'running' || state.phase === 'attached')) state.phase = 'stopped'
      return false
    }
  }

  function stderrTail() {
    if (!handle) return ''
    try {
      const read = handle.collected?.stderr?.readFrom(0)
      if (!read?.text) return ''
      const lines = read.text.trim().split(/\r?\n/).filter(Boolean)
      return lines.slice(-3).join(' ').slice(0, 500)
    } catch {
      return ''
    }
  }

  function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)) }

  async function start() {
    if (disposed) return state
    if (await probe()) {
      state.phase = 'attached'
      state.spawnedByPlugin = false
      schedule()
      return state
    }
    if (config.mode === 'attach') {
      state.phase = 'stopped'
      schedule(POLL_IDLE_MS)
      return state
    }
    if (handle) return state
    // registry 安装时包在 profile 的 node_modules 里，上一级不是仓库根：
    // 先确认 projectDir 真是仓库根再谈解释器，否则「先运行 run.bat」会把人指到错误目录。
    if (!(await fileExists(join(config.projectDir, 'app', 'main.py')))) {
      state.phase = 'error'
      state.lastError = `projectDir 不是 Deep肥鱼仓库根（${config.projectDir}）：`
        + '按包名安装时包位于 profile 的 node_modules，上一级不是仓库根，'
        + '请在插件配置的 projectDir 里填写 Deep肥鱼仓库根目录。'
      schedule(POLL_IDLE_MS)
      return state
    }
    if (!(await fileExists(config.pythonPath))) {
      state.phase = 'error'
      state.lastError = `未找到 Python：${config.pythonPath}。请先在仓库根运行 run.bat 完成首次安装。`
      schedule(POLL_IDLE_MS)
      return state
    }
    state.phase = 'starting'
    state.lastError = ''
    try {
      handle = ctx.subprocess.spawn({
        argv: [config.pythonPath, '-m', 'app.main'],
        cwd: config.projectDir,
        env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
        stdio: {
          stdin: 'ignore',
          stdout: { maxBytes: 65536 },
          stderr: { maxBytes: 65536 },
        },
        graceMs: 5000,
      })
    } catch (err) {
      state.phase = 'error'
      state.lastError = `拉起控制台失败：${err?.message || err}`
      schedule(POLL_IDLE_MS)
      return state
    }
    state.spawnedByPlugin = true
    state.startedAt = Date.now()
    handle.done.then((outcome) => {
      if (disposed) return
      handle = null
      state.running = false
      state.spawnedByPlugin = false
      if (outcome && outcome.exitCode !== 0) {
        state.phase = 'error'
        state.lastError = stderrTail() || `控制台进程退出（code=${outcome.exitCode}）`
      } else {
        state.phase = 'stopped'
      }
      schedule(POLL_IDLE_MS)
    }).catch(() => {})
    const deadline = Date.now() + START_DEADLINE_MS
    while (Date.now() < deadline && !disposed) {
      await sleep(500)
      if (await probe()) {
        state.phase = 'running'
        schedule()
        return state
      }
      if (!handle) return state
    }
    if (!state.running) {
      state.phase = 'error'
      state.lastError ||= '控制台在 60 秒内没有就绪'
      schedule(POLL_IDLE_MS)
    }
    return state
  }

  async function stop() {
    if (!handle) {
      const healthy = await probe()
      state.phase = healthy ? 'attached' : 'stopped'
      if (!healthy) state.lastError = ''
      return state
    }
    state.phase = 'stopping'
    try { handle.terminate() } catch { /* 已退出 */ }
    try { await handle.waitForExit() } catch { /* 已退出 */ }
    handle = null
    state.running = false
    state.spawnedByPlugin = false
    state.phase = 'stopped'
    state.lastError = ''
    schedule(POLL_IDLE_MS)
    return state
  }

  async function restart() {
    await stop()
    await sleep(300)
    return await start()
  }

  async function boot() {
    if (disposed) return
    if (config.mode === 'auto' && config.autostart) {
      await start()
      return
    }
    const healthy = await probe()
    state.phase = healthy ? 'attached' : 'stopped'
    schedule(healthy ? POLL_RUNNING_MS : POLL_IDLE_MS)
  }

  function dispose() {
    disposed = true
    if (timer) { clearTimeout(timer); timer = null }
    if (handle) {
      try { handle.terminate() } catch { /* 已退出 */ }
      handle = null
    }
    state.running = false
    state.spawnedByPlugin = false
    state.phase = 'stopped'
  }

  return { boot, start, stop, restart, probe, dispose, get spawned() { return Boolean(handle) } }
}
