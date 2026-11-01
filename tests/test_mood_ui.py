"""控制台心情页：入口、四页签、接口调用与脚本语法。"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_nav_and_section_present():
    html = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
    assert 'data-page="mood"' in html
    assert 'id="page-mood"' in html
    for tab in ("state", "slots", "judge", "widget"):
        assert f'data-mood-tab="{tab}"' in html
    assert "/static/mood-widget.js" in html
    assert "/api/mood/widget.js" not in html


def test_app_js_wires_mood_page():
    js = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
    assert "mood: [" in js                    # PAGE_META
    assert 'page === "mood"' in js
    assert "/api/mood/state" in js
    assert "/api/mood/config" in js
    assert "/api/mood/slots" in js
    assert "/api/mood/reset" in js
    assert "/api/mood/judge-test" in js


def test_app_js_syntax():
    subprocess.run(["node", "--check", str(ROOT / "app" / "static" / "app.js")], check=True)
    subprocess.run(["node", "--check", str(ROOT / "app" / "static" / "mood-widget.js")], check=True)


def test_mood_face_uses_public_assets():
    html = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
    js = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
    assert "/mood-assets/" in js
    assert "/api/mood/asset/" not in js
    assert 'src="/mood-assets/face-ok.png"' in html


def test_mood_slots_use_event_delegation():
    js = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
    assert "data-mood-act" in js
    assert 'onclick="renameMoodSlot' not in js
    assert 'onclick="deleteMoodSlot' not in js
    assert 'onclick="unbindMoodChat' not in js


def test_switch_account_reloads_mood_page():
    js = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
    assert 'if (state.page === "mood") loadMoodPage();' in js
