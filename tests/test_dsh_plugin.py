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
    assert data["name"] == "@local/deepfeiyu-dsh"
    assert data["private"] is True
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
    assert row["name"] == "@local/deepfeiyu-dsh"
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
