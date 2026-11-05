"""DSH 插件 bundle 的静态一致性：清单、patch、locale、icon、文件清单、工具名与 JS 语法。

只校验仓库里的静态文件，不安装插件、不连运行中的 Harness；面板视觉与实机审批
效果不在本文件覆盖范围内。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dsh-plugin"

# 规格 §5.2：Agent 工具名固定为这 8 个，Host 侧工具源码里必须一字不差地出现
TOOL_NAMES = (
    "deepfeiyu_status",
    "deepfeiyu_accounts",
    "deepfeiyu_chats",
    "deepfeiyu_messages",
    "deepfeiyu_send",
    "deepfeiyu_stats",
    "deepfeiyu_capabilities",
    "deepfeiyu_console",
)


def _manifest() -> dict:
    return json.loads((PKG / "package.json").read_text(encoding="utf-8"))


def _js_files() -> list[Path]:
    return [PKG / "index.js", PKG / "client.js", *sorted((PKG / "lib").glob("*.js"))]


def test_manifest_required_fields():
    data = _manifest()
    # 分发给其他人时用公开包名；private 必须去掉，否则无法发布到 registry。
    assert data["name"] == "deepfeiyu-dsh"
    assert "private" not in data
    assert data["license"] == "PolyForm-Noncommercial-1.0.0"
    assert data["type"] == "module"
    assert data["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert data["dsh"]["client"]["platform"] == "web"
    for key in (".", "./client", "./icon", "./locale/*.json"):
        assert key in data["exports"], f"package.json exports 缺少 {key}"
    assert data["exports"]["."] == "./index.js"
    assert data["exports"]["./client"] == "./client.js"


def test_patch_inserts_bundle_row():
    patch = yaml.safe_load((PKG / "cordis.patch.yml").read_text(encoding="utf-8"))
    rows = patch[0]["insert"]
    row = next(
        (r for r in rows if r.get("id") == "deepfeiyu"),
        None,
    )
    assert row is not None, "cordis.patch.yml 没有 id=deepfeiyu 的 insert 行"
    # patch 行名必须与 package.json 名一致，否则 profile 解析不到模块
    assert row["name"] == "deepfeiyu-dsh"
    assert row["name"] == _manifest()["name"]
    # 安装后必须有默认 config，否则 Host 侧 apply 拿到 undefined
    assert isinstance(row.get("config"), dict)
    assert row["config"].get("mode") in ("auto", "attach")
    assert row["config"].get("port") == 8765


@pytest.mark.parametrize("loc", ["en", "zh"])
def test_locale_meta(loc):
    meta = json.loads((PKG / "locale" / f"{loc}.json").read_text(encoding="utf-8"))["meta"]
    assert isinstance(meta["title"], str) and meta["title"].strip()
    assert isinstance(meta["description"], str) and meta["description"].strip()


def test_icon_exists_and_is_small_svg():
    icon = PKG / "icon.svg"
    assert icon.is_file(), "缺少 dsh-plugin/icon.svg"
    assert 0 < icon.stat().st_size <= 256 * 1024, "icon.svg 必须存在且不超过 256KiB"
    assert icon.read_text(encoding="utf-8").lstrip().startswith("<svg"), "icon.svg 不是 SVG"


def test_files_list_matches_disk():
    data = _manifest()
    assert data["files"], "package.json files 不能为空，否则安装包缺文件"
    missing: list[str] = []
    for rel in data["files"]:
        target = PKG / rel.rstrip("/")
        if rel.endswith("/"):
            if not target.is_dir():
                missing.append(rel)
        elif "*" in rel:
            if not list(PKG.glob(rel)):
                missing.append(rel)
        elif not target.is_file():
            missing.append(rel)
    assert not missing, "package.json files 声明了磁盘上不存在的条目：" + "、".join(missing)
    # 规格 §5.2 要求的 Host/Client 入口无论 files 怎么写都必须在
    for name in ("index.js", "client.js", "cordis.patch.yml"):
        assert (PKG / name).is_file(), f"缺少 bundle 必需文件 dsh-plugin/{name}"


def test_tool_names_match_host_source():
    tools = PKG / "lib" / "tools.js"
    if not tools.is_file():
        pytest.fail(
            "dsh-plugin/lib/tools.js 尚未生成（Host 侧并行开发中），无法校验 8 个工具名；"
            "等 Host 文件落地后重跑本测试"
        )
    source = tools.read_text(encoding="utf-8")
    for name in TOOL_NAMES:
        assert f"'{name}'" in source or f'"{name}"' in source, f"lib/tools.js 缺少工具名 {name}"


def test_settings_section_registered_in_client():
    """设置页分区必须注册在 settings.section，label 为「Deep肥鱼」，并走 deps.* 代理端点。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    assert "DeepFeiyuSettingsPage" in source, "client.js 缺少设置页组件 DeepFeiyuSettingsPage"
    for endpoint in ("deps.state", "deps.install", "deps.log"):
        assert f"'{endpoint}'" in source or f'"{endpoint}"' in source, f"client.js 未调用 {endpoint}"
    # 只匹配设置页那一次注册：name、id 与 label 必须成组出现，避免把侧栏图标的同名 label 当成通过。
    assert re.search(
        r"name:\s*'settings\.section',[\s\S]*?label:\s*\(\)\s*=>\s*'Deep肥鱼'",
        source,
    ), "client.js 没有以「Deep肥鱼」为 label 注册 settings.section"


def test_route_proxies_deps_endpoints():
    """Host 路由要把设置页的依赖请求代理到控制台，且 feature 参数必须校验。"""
    source = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    for endpoint in ("deps.state", "deps.install", "deps.log"):
        assert f"'{endpoint}'" in source, f"lib/route.js 缺少路由 {endpoint}"
    assert "api.depsState" in source, "deps.state 未代理到 api.depsState"
    assert "api.depsInstall" in source, "deps.install 未代理到 api.depsInstall"
    assert "api.depsLog" in source, "deps.log 未代理到 api.depsLog"
    assert "INVALID_ARGS" in source, "缺少 feature 时应返回 INVALID_ARGS"


def test_ops_targets_console_deps_api():
    """控制台 HTTP 层要指向固定的 /api/deps/* 契约路径。"""
    source = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for path in ("/api/deps/state", "/api/deps/install", "/api/deps/install/log"):
        assert path in source, f"lib/ops.js 缺少控制台路径 {path}"


def test_readme_documents_settings_page():
    """用户可见改动必须在 README 说明设置页与依赖安装，以及控制台未运行时的处理。"""
    text = (PKG / "README.md").read_text(encoding="utf-8")
    assert "设置" in text, "README 未提到 DSH 设置页"
    assert "一键安装" in text and "依赖" in text, "README 未说明依赖状态与一键安装"
    assert "/api/deps" in text, "README 未说明依赖接口来源"
    assert "控制台未运行" in text, "README 故障排查未覆盖控制台未运行"


def test_dsh_plugin_installer_whitelist():
    """「关联 DSH 插件」只允许固定白名单：前端只能传 id，包名与安装选项写死。"""
    source = (PKG / "lib" / "dsh-plugins.js").read_text(encoding="utf-8")
    for name in ("dsh-dafeiyu", "dsh-feiyu-mood", "dsh-whale-widget"):
        assert f"name: '{name}'" in source, f"白名单缺少 {name}"
    # spec 只能来自定义表，不能来自请求体
    assert "installBundle(def.name" in source, "installBundle 的 spec 必须取白名单定义"
    assert "saveExact: true" in source, "安装最新版后应把实际版本写死回 profile"
    assert "未知的 DSH 插件" in source, "未知 id 必须被拒绝"
    assert "payload.spec" not in source and "payload.name" not in source, "不能接受前端传入的包名/spec"


def test_route_proxies_dsh_plugin_routes():
    """Host 路由要把设置页的 DSH 插件安装请求交给白名单安装器，只传 id。"""
    source = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    assert "createDshPluginsApi" in source, "route.js 未创建 DSH 插件安装器"
    for endpoint in ("dshPlugins.state", "dshPlugins.install"):
        assert f"'{endpoint}'" in source, f"lib/route.js 缺少路由 {endpoint}"
    assert "dshPlugins.install(id)" in source, "install 路由应只转发 id"
    assert "缺少 id" in source, "缺少 id 时应返回 INVALID_ARGS"


def test_client_dsh_plugin_section():
    """设置页要能看到并安装这两个上游 DSH 插件，且点击前必须二次确认。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("关联 DSH 插件", "dshPlugins.state", "dshPlugins.install",
                  "一键安装最新版", "window.confirm"):
        assert token in source, f"client.js 缺少 {token}"
    # 确认文案必须说明会执行第三方代码
    assert "第三方代码" in source, "安装确认必须说明会执行第三方代码"


def test_readme_documents_dsh_plugin_install():
    """DSH 插件安装功能与安全边界必须在 README 说明。"""
    text = (PKG / "README.md").read_text(encoding="utf-8")
    for token in ("关联 DSH 插件", "dsh-dafeiyu", "dsh-feiyu-mood",
                  "dsh-whale-widget", "第三方代码", "最新版"):
        assert token in text, f"README 缺少 {token}"


def test_fusion_module_collects_events_and_posts_pet_events():
    source = (PKG / "lib" / "fusion.js").read_text(encoding="utf-8")
    for token in ("session/event", "user/message", "assistant/message",
                  "tool/call", "tool/result", "turn/end",
                  "dshPetEvent", "turn_start", "tool_start", "postPet", "dispose"):
        assert token in source, f"lib/fusion.js 缺少 {token}"


def test_fusion_routes_registered():
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for endpoint in ("fusion.state", "fusion.config", "fusion.timeline"):
        assert f"'{endpoint}'" in route, f"route.js 缺少 {endpoint}"
    for path in ("/api/dsh/state", "/api/dsh/config", "/api/dsh/timeline",
                 "/api/dsh/judge-prompt", "/api/dsh/mood-event", "/api/dsh/pet-event"):
        assert path in ops, f"ops.js 缺少 {path}"


def test_index_wires_fusion_with_disposer():
    source = (PKG / "index.js").read_text(encoding="utf-8")
    assert "createFusion" in source and "fusion.dispose" in source


def test_fusion_judges_mood_and_injects_prompt_context():
    source = (PKG / "lib" / "fusion.js").read_text(encoding="utf-8")
    for token in ("dshJudgePrompt", "dshMoodEvent", "agentDefaultModel",
                  "streamText(", "systemPrompt", "context(", "deepfeiyu.mood",
                  "JUDGE_MIN_GAP_MS", "JUDGE_DAILY_MAX", "parseJudge",
                  "mood.enabled === false"):
        assert token in source, f"lib/fusion.js 缺少 {token}"


def test_settings_page_has_fusion_section():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("心情与桌宠（融合）", "fusion.state", "fusion.config",
                  "fusion.timeline", "绑定账号", "允许 Harness 来历进微信"):
        assert token in source, f"client.js 缺少 {token}"


def test_client_registers_mood_overlay():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("DeepFeiyuMoodWidget", "shell.overlay", "deepfeiyu-mood",
                  "fusion.state", "20_000"):
        assert token in source, f"client.js 缺少 {token}"


def test_settings_routes_registered():
    """设置页 P1 的 8 个 endpoint 必须在 Host 路由与 ops 层都登记。"""
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for endpoint in ("settings.get", "settings.put", "settings.testAi",
                     "personas.list", "personas.add", "personas.delete",
                     "personas.syncWiki", "update.check"):
        assert f"'{endpoint}'" in route, f"route.js 缺少 {endpoint}"
    for method in ("settingsGet", "settingsPut", "settingsTestAi",
                   "personasList", "personasAdd", "personasDelete",
                   "personasSyncWiki", "updateCheck"):
        assert method in ops, f"ops.js 缺少 {method}"
    # settings.put 必须校验 patch 形状，不能把任意值透传给配置接口
    assert "patch 必须是对象" in route


def test_settings_page_tab_shell():
    """设置页改为页内标签壳：默认依赖与融合，原三块迁入 deps 标签。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("SETTINGS_TABS", "function TabBar", "role: 'tab'", "aria-selected",
                  "renderDepsTab", "dsh-tabpanel-deps", "dsh-deepfeiyu-tabs",
                  "Deep肥鱼 · 设置", "React.useState('deps')"):
        assert token in source, f"client.js 缺少 {token}"
    # 原三块必须仍由 deps 标签渲染（不回归）
    for token in ("renderDshPluginsSection()", "renderFusionSection()", "renderLog()"):
        assert token in source, f"依赖标签丢了 {token}"
    # 轮询跟随标签：只有 deps 分支拉依赖数据
    assert "if (tab === 'deps')" in source
    # 旧标题必须改名，避免与标签语义冲突
    assert "Deep肥鱼 · 依赖环境" not in source


def test_model_tab_present():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("'模型与人格'", "renderModelTab", "loadSettingsTab", "saveSettingsTab",
                  "switchProfile", "settingsPatchFromDraft", "draftFromSettings",
                  "testAiConnection", "applyPreset", "savePresetAs", "deletePreset",
                  "syncWikiPresets", "aria-pressed", "EMBEDDING_API_KEY",
                  "function Field", "function ToggleRow"):
        assert token in source, f"client.js 缺少 {token}"
    # 保存必须走窄补丁（patch 包装），不得整体回传 settings.get 的结果
    assert "call('settings.put', { patch: settingsPatchFromDraft(draft)" in source
    # 草稿只在为空时初始化，4s 轮询不得覆盖用户正在编辑的输入
    assert "prev || draftFromSettings" in source
    # API Key 草稿初值为空：掩码只做占位，不回填
    assert "api_key: ''," in source


def test_auto_tab_present():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("'自动回复'", "renderAutoTab", "autoFromSettings", "autoPatchFromDraft",
                  "saveAutoTab", "splitLines", "visit_gap_sec", "split_reply",
                  "chuanzi_site_emoji", "only_whitelist", "cross_daily_limit"):
        assert token in source, f"client.js 缺少 {token}"
    # 自动回复保存也必须是窄补丁，且与模型草稿分开
    assert "call('settings.put', { patch: autoPatchFromDraft(autoDraft)" in source
    # 加载时同时播种两个草稿，互不覆盖
    assert "prev || autoFromSettings" in source


def test_update_tab_present():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("'更新与边界'", "renderUpdateTab", "checkUpdateTab", "update.check",
                  "has_update", "git_repo", "使用边界", "《最终用户许可协议》",
                  "updateFetched", "openExternal"):
        assert token in source, f"client.js 缺少 {token}"
    # 更新检查只在首次打开时自动跑一次，之后只手动（不每 4s 打 Gitee）
    assert "tab === 'update' && !updateFetched.current" in source


def test_supervisor_guards_project_dir():
    """registry 安装时包在 profile 的 node_modules 里，上一级不是仓库根。

    拉起控制台前必须校验 projectDir，并给出「配置 projectDir」的引导；
    只有仓库根本身缺 .venv 才提示 run.bat，两种错误不能混用。
    """
    source = (PKG / "lib" / "supervisor.js").read_text(encoding="utf-8")
    assert "join(config.projectDir, 'app', 'main.py')" in source, (
        "supervisor 拉起前未校验 projectDir 是否为仓库根"
    )
    assert "仓库根" in source, "缺少 projectDir 配置引导文案"
    # 误导性文案必须只在「确实是仓库根但缺解释器」时出现
    assert "run.bat" in source


def test_manifest_publish_metadata():
    """发布到 registry 前必须补齐元数据与包内许可证文本（npm 只打包包目录内的文件）。"""
    data = _manifest()
    repo = data.get("repository")
    assert isinstance(repo, dict) and "gitee.com" in str(repo.get("url", "")), "缺 repository"
    assert isinstance(data.get("homepage"), str) and data["homepage"], "缺 homepage"
    assert isinstance(data.get("bugs"), dict) and data["bugs"].get("url"), "缺 bugs"
    assert "LICENSE" in data["files"], "files 必须包含 LICENSE，否则发布包无许可证文本"
    assert (PKG / "LICENSE").is_file(), "dsh-plugin/LICENSE 缺失"
    # 有意不声明 engines.dsh：只在 0.2.1-rc.5 实测，声明会拦住其他版本安装（README 已说明）
    assert "dsh" not in data.get("engines", {}), "不要声明 engines.dsh"


def test_readme_documents_registry_install():
    """README 必须说明按包名安装与由此必需的 projectDir，且不能再留「尚未发布」表述。"""
    text = (PKG / "README.md").read_text(encoding="utf-8")
    assert "deepfeiyu-dsh" in text
    assert "npm" in text
    assert "node_modules" in text, "registry 安装后包不在仓库旁边，必须解释原因"
    assert "尚未发布到 npm" not in text, "发行文档定稿前必须移除「尚未发布」表述"


def test_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，无法做 JS 语法检查（已在 PR 中如实说明）")
    files = _js_files()
    assert files, "bundle 没有任何 JS 文件"
    for f in files:
        assert f.is_file(), f"缺少 JS 文件 {f.relative_to(ROOT)}"
        proc = subprocess.run(
            [node, "--check", str(f)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.returncode == 0, (
            f"{f.relative_to(ROOT)} 语法检查失败：{(proc.stderr or proc.stdout).strip()}"
        )


# 期1 会话控制：用假 ctx 驱动真实的 registerRoute handler，覆盖转发、校验与错误分类。
ROUTE_HARNESS = r"""
import { pathToFileURL } from 'node:url'
const base = pathToFileURL(process.cwd() + '/').href
const { registerRoute } = await import(base + 'lib/route.js')
const { ConsoleDownError } = await import(base + 'lib/ops.js')

const calls = []
let failNext = false
let handler = null
const ctx = {
  connection: { requestRejection: () => 0 },
  webServer: {
    register: (route) => {
      handler = route.handler
      return () => {}
    },
  },
  effect: () => {},
  logger: { debug: () => {} },
  get: () => undefined,
}
const api = {
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
  chatAi: async (uid, enabled) => {
    if (failNext) {
      failNext = false
      throw new ConsoleDownError('无法连接 Deep肥鱼控制台 http://127.0.0.1:8765（ECONNREFUSED）')
    }
    calls.push(['chatAi', uid, enabled])
    return { ok: true, ai_enabled: enabled }
  },
  chatResume: async (uid) => {
    calls.push(['chatResume', uid])
    return { ok: true }
  },
  chatsBatchAi: async (uids, enabled) => {
    calls.push(['chatsBatchAi', uids, enabled])
    return { ok: true, changed: uids.length, ai_enabled: enabled }
  },
}
registerRoute({
  ctx,
  config: {},
  api,
  state: () => ({}),
  snapshot: () => ({}),
  supervisor: { start() {}, stop() {}, restart() {} },
  sendGate: undefined,
})
// 第二个实例：鉴权被拒时 GET 也必须吃 401/403，不能因为是图片就放行。
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

async function post(endpoint, payload) {
  const chunks = [Buffer.from(JSON.stringify(payload))]
  const req = {
    method: 'POST',
    url: '/deepfeiyu/' + endpoint,
    headers: {},
    async *[Symbol.asyncIterator]() {
      for (const chunk of chunks) yield chunk
    },
  }
  let status = 0
  let body = ''
  const res = {
    writeHead(code) { status = code },
    end(text) { body = text },
  }
  await handler(req, res)
  return { status, body: body ? JSON.parse(body) : null }
}

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

const out = []
async function step(name, fn) {
  const before = calls.length
  try {
    const extra = (await fn()) || {}
    out.push({ name, status: 'ok', calls: calls.slice(before), ...extra })
  } catch (error) {
    out.push({ name, status: 'threw', error: String((error && error.stack) || error) })
  }
}

await step('chat.ai-ok', async () => {
  const r = await post('chat.ai', { uid: '  acc_default:wxid_a  ', enabled: true })
  return { http: r.status, value: r.body && r.body.value, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.ai-missing-uid', async () => {
  const r = await post('chat.ai', { uid: '   ', enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.ai-bad-enabled', async () => {
  const r = await post('chat.ai', { uid: 'acc_default:wxid_a', enabled: 'yes' })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.ai-console-down', async () => {
  failNext = true
  const r = await post('chat.ai', { uid: 'acc_default:wxid_a', enabled: false })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('chat.resume-ok', async () => {
  const r = await post('chat.resume', { uid: 'acc_default:wxid_b' })
  return { http: r.status, value: r.body && r.body.value }
})
await step('chat.resume-missing-uid', async () => {
  const r = await post('chat.resume', {})
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-ok-dedupe', async () => {
  const r = await post('chats.batchAi', { uids: ['a', ' a ', '', 'b', 'a'], enabled: false })
  return { http: r.status, value: r.body && r.body.value }
})
await step('batch-not-array', async () => {
  const r = await post('chats.batchAi', { uids: 'a', enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-over-limit', async () => {
  const uids = Array.from({ length: 201 }, (_, i) => 'u' + i)
  const r = await post('chats.batchAi', { uids, enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-empty-uids', async () => {
  const r = await post('chats.batchAi', { uids: ['', '  '], enabled: true })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})
await step('batch-bad-enabled', async () => {
  const r = await post('chats.batchAi', { uids: ['a'], enabled: 'true' })
  return { http: r.status, code: r.body && r.body.error && r.body.error.code }
})

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

console.log(JSON.stringify(out))
"""


def test_chat_control_routes_registered():
    """期1：三个会话控制端点必须在 Host 路由与 ops 层都登记，且入参有白名单校验。"""
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for endpoint in ("chat.ai", "chat.resume", "chats.batchAi"):
        assert f"'{endpoint}'" in route, f"lib/route.js 缺少路由 {endpoint}"
    for method in ("chatAi", "chatResume", "chatsBatchAi"):
        assert method in ops, f"lib/ops.js 缺少 {method}"
    for fragment in ("encodeURIComponent(uid)}/ai", "encodeURIComponent(uid)}/resume",
                     "'/api/chats/batch-ai'", "method: 'POST'",
                     "body: { enabled }", "body: { uids, enabled }"):
        assert fragment in ops, f"lib/ops.js 缺少片段 {fragment}"
    for message in ("缺少 uid", "enabled 必须是布尔值", "uids 必须是数组",
                    "uids 超过 200 上限", "uids 不能为空"):
        assert message in route, f"route.js 缺少入参校验文案 {message}"


def test_chat_control_route_behavior():
    """路由行为：白名单转发、参数校验、去重截断与控制台离线错误分类。"""
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

    ok = results["chat.ai-ok"]
    assert ok["http"] == 200, ok
    assert ok["calls"] == [["chatAi", "acc_default:wxid_a", True]], ok
    assert ok["value"] == {"uid": "acc_default:wxid_a", "ai_enabled": True}, ok

    missing = results["chat.ai-missing-uid"]
    assert missing["http"] == 400 and missing["code"] == "INVALID_ARGS" and missing["calls"] == [], missing
    bad_enabled = results["chat.ai-bad-enabled"]
    assert bad_enabled["http"] == 400 and bad_enabled["code"] == "INVALID_ARGS" and bad_enabled["calls"] == [], bad_enabled

    down = results["chat.ai-console-down"]
    assert down["http"] == 200 and down["code"] == "CONSOLE_DOWN", down

    resume = results["chat.resume-ok"]
    assert resume["http"] == 200 and resume["calls"] == [["chatResume", "acc_default:wxid_b"]], resume
    assert resume["value"] == {"uid": "acc_default:wxid_b", "paused": False}, resume
    no_uid = results["chat.resume-missing-uid"]
    assert no_uid["http"] == 400 and no_uid["code"] == "INVALID_ARGS", no_uid

    batch = results["batch-ok-dedupe"]
    assert batch["http"] == 200, batch
    assert batch["calls"] == [["chatsBatchAi", ["a", "b"], False]], batch
    assert batch["value"] == {"ok": True, "changed": 2, "ai_enabled": False}, batch
    for name in ("batch-not-array", "batch-over-limit", "batch-empty-uids", "batch-bad-enabled"):
        assert results[name]["http"] == 400, name
        assert results[name]["code"] == "INVALID_ARGS", name


def test_client_thread_head_chat_control():
    """线程头要能开关会话 AI、显示状态副标题，并在接管中给出恢复入口。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("toggleChatAi", "resumeChat", "setChatFeedbackOnce",
                  "chat.ai", "chat.resume", "chatBusy", "chatFeedback",
                  "AI 自动回复", "人工接管中", "AI 已关闭",
                  "恢复 AI", "thread-actions", "thread-sub", "aria-checked"):
        assert token in source, f"client.js 缺少 {token}"
    # 副标题必须由 ai_enabled / paused 推出，而不是写死文案
    assert "selectedChat.paused" in source, "线程头未读取 paused 状态"
    assert "selectedChat.ai_enabled" in source, "线程头未读取 ai_enabled 状态"


def test_client_paused_tag_clickable():
    """列表里的「人工」徽标要能点击就地恢复 AI，且不打开会话（stopPropagation）。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    assert "人工接管中，点击恢复 AI 自动回复" in source, "缺少接管徽标的 title 提示"
    assert "dsh-deepfeiyu-is-clickable" in source, "接管徽标未加可点击样式类"
    assert "event.stopPropagation()" in source, "徽标点击未阻止冒泡，会误触发行点击"
    # 点击必须走 resumeChat，而不是打开会话
    assert re.search(r"is-clickable[\s\S]{0,400}?resumeChat\(uid\)", source), (
        "接管徽标没有接到 resumeChat(uid)"
    )


def test_client_multi_select_bulk():
    """多选批量：模式开关、全选作用于当前列表、批量端点与退出清空选中集。"""
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("selectMode", "selectedChats", "toggleSelectMode", "toggleSelectAll",
                  "handleChatRowClick", "bulkSetAi", "chats.batchAi",
                  "退出多选", "设为人工", "设为 AI", "已选 ",
                  "dsh-deepfeiyu-is-checked", "dsh-deepfeiyu-chats-tools"):
        assert token in source, f"client.js 缺少 {token}"
    # 批量成功要退出多选并清空选中集（对齐控制台行为）
    assert re.search(r"bulkSetAi[\s\S]{0,900}?setSelectMode\(false\)", source), (
        "bulkSetAi 成功后未退出多选模式"
    )
    # 搜索结果是子集，不得拿子集修剪选中集：只在无关键词时修剪
    assert "if (!keywordValue)" in source, "loadChats 未按是否有关键词区分选中集修剪"


def test_readme_documents_chat_control():
    """期1 会话控制必须在插件 README 与使用说明里可见，并写明不走审批的定位。"""
    readme = (PKG / "README.md").read_text(encoding="utf-8")
    for token in ("多选", "恢复 AI", "AI 开关", "chat.ai", "不走 Harness 审批"):
        assert token in readme, f"dsh-plugin/README.md 缺少 {token}"
    usage = (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8")
    for token in ("多选", "恢复 AI", "AI 开关"):
        assert token in usage, f"docs/使用说明.md 缺少 {token}"


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
