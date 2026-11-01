"""控制台「搭话」页：入口、卡片字段、接口对接、事件委托与文档覆盖。

只读源码断言，保证页面真的接到了 Phase 3 的 API 上，并且使用说明有对应章节。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
CSS = (ROOT / "app" / "static" / "style.css").read_text("utf-8")
DOC = (ROOT / "docs" / "使用说明.md").read_text("utf-8")


def test_nav_entry_is_called_dahua():
    blocks = re.findall(
        r'<button[^>]*data-page="([a-z0-9-]+)"[^>]*>(.*?)</button>', HTML, re.S
    )
    labels = {key: re.search(r"<span>(.*?)</span>", b, re.S).group(1).strip()
              for key, b in blocks if re.search(r"<span>(.*?)</span>", b, re.S)}
    assert labels.get("proactive") == "搭话"
    # 导航位置：紧跟在「心情」之后
    order = [key for key, _ in blocks]
    assert order.index("proactive") == order.index("mood") + 1


def test_proactive_page_and_card_ids():
    assert 'id="page-proactive"' in HTML
    for anchor in (
        "proactiveEnabled",
        "proactiveEnabledTag",
        "proactiveSchedulerTag",
        "proactiveWarn",
        "proactiveStates",
        "proactiveChatPick",
        "proactiveGroupAck",
        "proactiveWhitelist",
        "proactiveTestPick",
        "proactiveTestOut",
        "proactiveEvents",
    ):
        assert f'id="{anchor}"' in HTML, anchor


def test_proactive_param_inputs_cover_account_settings():
    for field in (
        "proactiveIntervalMin",
        "proactiveCooldownMin",
        "proactiveQuietStart",
        "proactiveQuietEnd",
        "proactiveDailyLimit",
        "proactivePerChatDaily",
        "proactiveHitPeak",
        "proactiveHitOffpeak",
        "proactiveRecentMin",
        "proactiveRecentFactor",
        "proactiveColdMin",
        "proactiveColdFactor",
        "proactiveMissBonus",
        "proactiveBonusCap",
        "proactiveMaxChars",
        "proactiveTriggerText",
    ):
        assert f'id="{field}"' in HTML, field
    page = HTML.split('id="page-proactive"', 1)[1].split("</section>", 1)[0]
    assert 'type="checkbox" id="proactiveEnabled"' in page
    assert 'onchange="saveProactiveEnabled(this.checked)"' in page
    assert 'onclick="saveProactiveForm()"' in page
    assert 'onclick="addProactiveChat()"' in page
    assert 'onclick="testProactive(true)"' in page
    assert 'onclick="testProactive(false)"' in page
    assert 'onclick="runProactive()"' in page
    assert 'onclick="loadProactiveEvents()"' in page


def test_warn_text_states_it_really_sends():
    warn = re.search(r'id="proactiveWarn">(.*?)</p>', HTML, re.S).group(1)
    assert "默认关闭" in warn
    assert "真的发出微信消息" in warn
    assert "没有白名单不能启用" in warn


def test_app_js_wires_proactive_page():
    assert 'proactive: ["主动搭话"' in JS          # PAGE_META
    assert 'if (page === "proactive") loadProactivePage();' in JS
    assert 'if (state.page === "proactive") loadProactivePage();' in JS
    for path in (
        "/api/proactive/status",
        "/api/proactive/chats",
        "/api/proactive/config",
        "/api/proactive/whitelist",
        "/api/proactive/test",
        "/api/proactive/run",
        "/api/proactive/events",
    ):
        assert path in JS, path


def test_app_js_proactive_functions_exist():
    for fn in (
        "loadProactivePage",
        "renderProactiveStates",
        "renderProactiveWhitelist",
        "renderProactivePick",
        "saveProactiveEnabled",
        "fillProactiveForm",
        "saveProactiveForm",
        "addProactiveChat",
        "removeProactiveChat",
        "testProactive",
        "runProactive",
        "loadProactiveEvents",
    ):
        assert re.search(rf"(async )?function {fn}\(", JS), fn


def test_proactive_whitelist_rows_use_delegation():
    assert "data-proactive-remove=" in JS
    assert 'closest("[data-proactive-remove]")' in JS
    assert 'onclick="removeProactiveChat' not in JS
    # 动态内容一律 esc()
    assert "${esc(w.chat_uid)}" in JS
    assert "${esc(s.chat_name || s.chat_uid)}" in JS


def test_proactive_enable_and_send_confirm():
    assert 'confirm("开启后 AI 会按概率主动给白名单会话发微信消息，确定开启？")' in JS
    assert 'confirm("这条会真的发到微信，确定？")' in JS
    assert 'confirm("这会占用今日额度并真的发送，确定？")' in JS
    assert "白名单已空，主动搭话已自动关闭" in JS


def test_proactive_status_labels_cover_backend_statuses():
    for status in ("sent", "skipped", "error", "miss", "preview", "busy", "warn"):
        assert re.search(rf'{status}: "[^"]+"', JS), status


def test_proactive_styles_present():
    assert ".proactive-warn" in CSS
    assert ".proactive-wl-row" in CSS
    assert ".proactive-state" in CSS
    assert ".proactive-st-sent" in CSS
    assert "#page-proactive" in CSS


def test_usage_doc_page_row_and_section():
    section = DOC.split("## 2. 左侧页面一览", 1)[1].split("## 3.", 1)[0]
    assert re.search(r"^\|\s*搭话\s*\|", section, re.M), "页面表缺少「搭话」行"
    assert "## 6. 主动搭话" in DOC


def test_usage_doc_section_numbers_are_continuous():
    numbers = [int(n) for n in re.findall(r"^## (\d+)\.", DOC, re.M)]
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    assert len(numbers) >= 15


def test_app_js_syntax():
    subprocess.run(["node", "--check", str(ROOT / "app" / "static" / "app.js")], check=True)
