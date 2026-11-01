import re
from pathlib import Path

INDEX = Path("app/static/index.html").read_text("utf-8")
APPJS = Path("app/static/app.js").read_text("utf-8")


def _pet_page() -> str:
    return INDEX.split('id="page-pet"', 1)[1].split("</section>", 1)[0]


def test_pet_nav_and_page_present():
    assert 'data-page="pet"' in INDEX
    assert 'id="page-pet"' in INDEX
    assert "桌面宠物" in _pet_page()


def test_pet_js_wired():
    for token in ("loadPetPage", "savePetConfig", "petPreview",
                  "/api/pet/state", "/api/pet/config", "/api/pet/test"):
        assert token in APPJS, token


def test_pet_page_ids_match_what_js_looks_up():
    """JS 里引用的每个 pet* 元素 id 都得在页面上存在，否则设置项会静默不生效。

    两种写法都要抓：项目里既用 $("#id") 也有 getElementById。
    """
    referenced = set(re.findall(r'getElementById\(["\']#?(pet[A-Za-z]+)["\']\)', APPJS))
    referenced |= set(re.findall(r'\$\(["\']#(pet[A-Za-z]+)["\']\)', APPJS))
    assert referenced, "app.js 里应当有 pet* 元素引用"
    declared = set(re.findall(r'id="(pet[A-Za-z]+)"', INDEX))
    missing = sorted(referenced - declared)
    assert not missing, f"页面缺少元素：{missing}"


def test_switch_page_dispatches_to_pet():
    """新页面必须挂进既有的页面切换，否则点导航不加载数据、切账号也不刷新。

    项目的分发是 if 链（mood/proactive 同写法），不是映射表——断言跟着实际形态写，
    别把测试的假设当成代码的要求。
    """
    assert 'if (page === "pet") loadPetPage();' in APPJS
    assert 'if (state.page === "pet") loadPetPage();' in APPJS


def test_pet_page_has_no_secret_fields():
    section = _pet_page()
    for banned in ("api_key", "access_token", "凭据", "余额", "sk-"):
        assert banned not in section, banned


def test_pet_preview_endpoints_referenced_from_page():
    section = _pet_page()
    assert "petPreview(" in section and "savePetConfig(" in section


def test_pet_fix_button_and_terminal_modal_present():
    assert 'id="petFixBtn"' in _pet_page()
    assert 'id="petInstallMask"' in INDEX
    assert 'id="petInstallTerm"' in INDEX
    assert 'id="petInstallStatus"' in INDEX
    assert "bili-log-card" in INDEX            # 复用 B 站终端样式，不新造一套视觉


def test_pet_install_js_polls_incrementally():
    for token in ("petInstall(", "pollPetInstallLog", "/api/pet/install",
                  "/api/pet/install/log?offset=", "closePetInstallTerminal"):
        assert token in APPJS, token
    assert "setInterval(pollPetInstallLog, 800)" in APPJS


def test_api_errors_carry_status_and_404_explains_restart():
    """404 通常意味着控制台进程还是旧版本：把状态码带给调用方，提示才不会只剩 Not Found。"""
    assert "err.status = res.status" in APPJS
    assert "e.status === 404" in APPJS
    assert "重启后按钮才可用" in APPJS
