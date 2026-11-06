// 「关联 DSH 插件」：在「设置 → Deep肥鱼」里一键安装本仓库引用过的上游 DSH 插件
// （桌宠 / 心情 / 余额小鲸鱼）。
//
// 为什么单独放一个文件：安装 DSH bundle 是"下载第三方代码并在 Host 进程全权限运行"，
// 和面板里的普通数据请求不同级别。这里只认白名单里的三个 npm 包，前端只能传 id，
// 包名、安装选项都在本文件里写死；真正下载前 Client 还会弹一次确认。
//
// 安装的是 npm 上的最新版（spec 就是包名，pnpm 会解析 latest，saveExact 把实际版本
// 写死回 profile）。已有旧版时不在这里做"卸载再装"：DSH 的 HMR 会拒绝覆盖已安装包，
// 自动卸载+重装一旦失败会把包留在卸载状态，更新入口留给 DSH 自己的插件页。
export const DSH_PLUGIN_DEFS = Object.freeze([
  Object.freeze({
    id: 'dafeiyu',
    name: 'dsh-dafeiyu',
    label: '桌宠（Harness 会话版）',
    description: '上游 dsh-dafeiyu：由 Harness 会话事件驱动的桌面宠物，自带原生 helper 与全部素材；与控制台里由微信状态驱动的桌宠是两套，会同时存在。',
    sizeHint: '约 242MB',
    note: '装完可在 DSH 插件页调整或停用；不影响控制台桌宠。',
  }),
  Object.freeze({
    id: 'feiyu-mood',
    name: 'dsh-feiyu-mood',
    label: '心情（Harness 会话版）',
    description: '上游 dsh-feiyu-mood：每轮 Harness 会话结束后判断心情，右下角挂饰与设置项都在 DSH 侧；不读微信，也不注入控制台的心情，与「心情与桌宠（融合）」互不同步——同时启用会出现两套心情数值。',
    sizeHint: '约 7MB',
    note: '装完可在 DSH 插件页调整阈值与注入方式；要与控制台同步请只用融合，别同时启用本插件。',
  }),
  Object.freeze({
    id: 'whale-widget',
    name: 'dsh-whale-widget',
    label: '余额小鲸鱼（Harness 版）',
    description: '上游 dsh-whale-widget：Harness 页面右下角的 DeepSeek 余额小鲸鱼，带今日用量、峰谷定价、自定义气泡与音效；与控制台里的余额挂件是两套，读的是 Harness 侧的用量。',
    sizeHint: '约 7MB',
    note: '代码 MIT；素材/图标随上游「as-is 仅用于运行本插件」分发，不授予再许可。',
  }),
])

const APPLICATION_TEXT = {
  applied: '已启用，刷新页面后生效',
  'restart-required': '已安装，重启 Harness 后生效',
  overridden: '已安装，但被更高优先级的配置覆盖',
  failed: '安装失败',
  cancelled: '安装已取消',
}

function describeManagementError(error) {
  if (!error || typeof error !== 'object') return ''
  const code = typeof error.code === 'string' ? error.code : ''
  const diagnostic = typeof error.diagnostic === 'string' ? error.diagnostic.trim() : ''
  if (diagnostic) return diagnostic.slice(0, 500)
  if (Array.isArray(error.incompatible) && error.incompatible.length) {
    return '版本不兼容：' + error.incompatible
      .map((item) => (item && typeof item === 'object' ? `${item.name}@${item.version}` : String(item)))
      .join('、')
  }
  return code
}

function summarizeResult(id, result) {
  const data = result && typeof result === 'object' ? result : {}
  const application = typeof data.application === 'string' ? data.application : 'failed'
  const ok = application === 'applied' || application === 'restart-required'
  const failure = describeManagementError(data.error)
  const warnings = Array.isArray(data.warnings)
    ? data.warnings.filter((item) => typeof item === 'string' && item.trim()).slice(0, 3)
    : []
  return {
    id,
    at: Date.now(),
    ok,
    application,
    version: typeof data.version === 'string' ? data.version : '',
    bundle: typeof data.bundle === 'string' ? data.bundle : '',
    message: ok ? String(APPLICATION_TEXT[application] || application) : failure || APPLICATION_TEXT[application] || '安装失败',
    warnings,
  }
}

/**
 * 依赖 Host 的 pluginManager 服务（base profile 提供）。服务不存在时只报告不可用：
 * 这是可选能力，不该让整个插件或面板因为没装 plugin-manager 而起不来。
 */
export function createDshPluginsApi({ ctx }) {
  // 一次只跑一个安装任务；job 在内存里，插件热重载后最多丢掉进度显示，不会重复装。
  let job = null
  // 最近一次安装结果，供设置页在安装结束后显示结论（不写文件、不落日志）。
  let last = null

  function manager() {
    return ctx.get('pluginManager')
  }

  function baseState(overrides) {
    return {
      available: true,
      error: '',
      installing: job ? job.id : '',
      startedAt: job ? job.startedAt : 0,
      last: last || null,
      plugins: DSH_PLUGIN_DEFS.map((def) => ({
        id: def.id,
        name: def.name,
        label: def.label,
        description: def.description,
        sizeHint: def.sizeHint,
        note: def.note,
        installed: false,
        enabled: false,
        version: '',
        availability: 'missing',
        error: '',
      })),
      ...overrides,
    }
  }

  async function state() {
    const pm = manager()
    if (!pm || typeof pm.listBundles !== 'function') {
      return baseState({
        available: false,
        error: '当前 Harness 没有插件管理服务（plugin-manager 未启用），无法安装 DSH 插件',
      })
    }
    let bundles
    try {
      bundles = await pm.listBundles()
    } catch (error) {
      return baseState({ available: false, error: '读取 DSH 插件列表失败：' + String(error?.message || error) })
    }
    const byName = new Map()
    for (const bundle of Array.isArray(bundles) ? bundles : []) {
      if (bundle && typeof bundle.name === 'string' && bundle.name) byName.set(bundle.name, bundle)
    }
    return baseState({
      plugins: DSH_PLUGIN_DEFS.map((def) => {
        const bundle = byName.get(def.name)
        return {
          id: def.id,
          name: def.name,
          label: def.label,
          description: def.description,
          sizeHint: def.sizeHint,
          note: def.note,
          installed: Boolean(bundle && bundle.installed),
          enabled: Boolean(bundle && bundle.enabled),
          version: bundle && typeof bundle.version === 'string' ? bundle.version : '',
          availability: bundle && typeof bundle.availability === 'string' ? bundle.availability : 'missing',
          error: bundle && bundle.error && typeof bundle.error.code === 'string' ? bundle.error.code : '',
        }
      }),
    })
  }

  async function install(id) {
    const def = DSH_PLUGIN_DEFS.find((item) => item.id === id)
    if (!def) {
      throw Object.assign(new Error('未知的 DSH 插件'), { code: 'INVALID_ARGS' })
    }
    const pm = manager()
    if (!pm || typeof pm.installBundle !== 'function') {
      throw Object.assign(new Error('当前 Harness 没有插件管理服务（plugin-manager 未启用），无法安装'), {
        code: 'INVALID_ARGS',
      })
    }
    if (job) {
      return {
        ok: false,
        started: false,
        id,
        error: job.id === id ? '这个插件正在安装，请稍候' : '已有另一个 DSH 插件正在安装，请稍候',
      }
    }
    const current = (await state()).plugins.find((item) => item.id === id)
    if (current && current.installed) {
      return {
        ok: false,
        started: false,
        id,
        error: `已安装${current.version ? ' v' + current.version : ''}；更新请到 DSH 的插件页操作`,
      }
    }
    job = { id, startedAt: Date.now() }
    last = null
    // 后台跑：242MB 的宠物包可能要几分钟，不能让设置页的 HTTP 请求一直挂着。
    Promise.resolve()
      .then(() => pm.installBundle(def.name, { enabled: true, saveExact: true }))
      .then((result) => {
        last = summarizeResult(id, result)
      })
      .catch((error) => {
        last = {
          id,
          at: Date.now(),
          ok: false,
          application: 'failed',
          version: '',
          bundle: def.name,
          message: String(error?.message || error).slice(0, 500),
          warnings: [],
        }
      })
      .finally(() => {
        job = null
      })
    return { ok: true, started: true, id }
  }

  return { state, install }
}
