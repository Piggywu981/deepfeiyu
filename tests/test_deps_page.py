"""设置页「可选功能依赖」卡片与安装日志弹窗的静态一致性。

后端 /api/deps 由独立任务并行实现，这里只锁定前端接线：元素 id 存在且在
#page-settings 段内、渲染与轮询走服务端下发的数据、弹窗里不放凭据字段。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
APPJS = (ROOT / "app" / "static" / "app.js").read_text("utf-8")


def _settings_page() -> str:
    """截取 #page-settings 一段：别的页面也有 card / 终端弹窗，全局搜 id 不算数。"""
    return INDEX.split('id="page-settings"', 1)[1].split("</section>", 1)[0]


def _deps_card() -> str:
    return _settings_page().split('id="depsCard"', 1)[1].split('id="updateTag"', 1)[0]


def _deps_modal() -> str:
    return INDEX.split('id="depsInstallMask"', 1)[1].split('id="shutdownMask"', 1)[0]


def _deps_js() -> str:
    """依赖卡片这段 JS：从段首注释到文件末尾，避免误判既有 B 站/桌宠代码。"""
    return APPJS.split("可选功能依赖（设置页）", 1)[1]


def _go_settings_branch() -> str:
    return APPJS.split('if (page === "settings") {', 1)[1].split("}", 1)[0]


def test_deps_card_lives_in_settings_page():
    page = _settings_page()
    for token in ('id="depsCard"', 'id="depsList"', "可选功能依赖", "重新检测"):
        assert token in page, token
    # 位置按需求卡在「账号与通道」之后、「软件更新」之前。
    assert page.index('id="accTabs"') < page.index('id="depsCard"') < page.index('id="updateTag"')


def test_deps_install_modal_present():
    for token in ('id="depsInstallMask"', 'id="depsInstallTerm"', 'id="depsInstallStatus"'):
        assert token in INDEX, token
    modal = _deps_modal()
    # 复用 B 站终端弹窗的既有视觉，不新造一套。
    assert "bili-log-card" in modal
    assert 'onclick="closeDepsInstallTerminal()"' in modal
    assert 'onclick="copyDepsInstallLog()"' in modal
    assert "data/deps/install.log" in modal
    # 不能和桌宠弹窗抢 id，否则两边状态互相覆盖；位置按需求在桌宠之后、关机遮罩之前。
    assert "petInstallMask" not in modal
    assert INDEX.index('id="petInstallMask"') < INDEX.index('id="depsInstallMask"') < INDEX.index('id="shutdownMask"')


def test_deps_js_wired():
    for token in ("loadDepsCard", "/api/deps/state", "/api/deps/install",
                  "installDep(", "pollDepsInstallLog", "closeDepsInstallTerminal",
                  "copyDepsInstallLog", "/api/deps/install/log?offset=",
                  "setInterval(pollDepsInstallLog, 800)"):
        assert token in APPJS, token
    # POST body 的字段名必须和后端契约一致。
    assert "body: JSON.stringify({ feature: featureId })" in APPJS


def test_settings_dispatch_loads_deps_card():
    assert "loadDepsCard();" in APPJS
    assert "loadDepsCard();" in _go_settings_branch()


def test_deps_features_are_server_driven():
    """六个功能（core/weixin4/moments/pet/wcferry/bili）由 /api/deps/state 下发。

    前端不写死中文名与安装命令：后端注册表改名或换安装方式时，页面不用跟着改。
    """
    js = _deps_js()
    assert "Array.isArray(depsCache.features)" in js
    assert "features.map(" in js
    for field in ("f.label", "f.desc", "f.supported", "f.missing", "f.note", "f.phase"):
        assert field in js, field
    assert "一键安装" in js and "重新安装" in js


def test_deps_card_has_no_arbitrary_command_input():
    """卡片只提供固定功能的一键安装，不开放「填任意包名/命令」的输入框。"""
    card = _deps_card()
    assert "<input" not in card
    assert "pip install" not in card
    assert "任意" not in card


def test_deps_dynamic_text_is_escaped_and_click_delegated():
    js = _deps_js()
    for token in ("esc(f.label", "esc(f.desc", "esc(f.note",
                  'data-dep-install="${attr(id)}"', 'data-dep-log="${attr(id)}"',
                  'closest("[data-dep-install]")', 'closest("[data-dep-log]")'):
        assert token in js, token
    # 服务端字段不进 onclick 字符串。
    assert "onclick=\"installDep(" not in js
    assert "onclick=\"openDepsLog(" not in js


def test_deps_phase_labels_are_chinese():
    for token in ('idle: "空闲"', 'installing: "安装中"', 'ok: "完成"', 'error: "失败"'):
        assert token in APPJS, token


def test_deps_poll_endpoint_carries_feature_and_offset():
    js = _deps_js()
    assert "encodeURIComponent(depsInstallFeature)" in js
    assert "Number(r.offset ?? depsInstallOffset)" in js
    assert "if (r.reset)" in js
    assert "r.done" in js
    # 查看日志走 offset=0 读最近日志。
    assert "/api/deps/install/log?offset=0&feature=" in js


def test_deps_modal_has_no_secret_fields():
    modal = _deps_modal()
    for banned in ("api_key", "access_token", "凭据", "sk-"):
        assert banned not in modal, banned
