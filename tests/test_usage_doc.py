"""使用说明：Markdown 渲染（含表格）、控制台说明页与文档覆盖率。"""
from __future__ import annotations

import re
from pathlib import Path

from app import helpdoc
from app.markdown_lite import render_markdown

ROOT = Path(__file__).resolve().parent.parent


def test_render_markdown_tables():
    html = render_markdown(
        "| 页面 | 作用 |\n"
        "|---|---|\n"
        "| 概览 | **状态** |\n"
        "| 会话 | `AI` 开关 |\n"
    )
    assert "<table>" in html
    assert "<thead><tr><th>页面</th><th>作用</th></tr></thead>" in html
    assert "<td><strong>状态</strong></td>" in html
    assert "<td><code>AI</code> 开关</td>" in html
    assert "|---|" not in html


def test_render_markdown_escapes_table_cells():
    html = render_markdown("| 危险 |\n|---|\n| <script>alert(1)</script> |\n")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_markdown_relative_links_become_text():
    html = render_markdown("看 [README](../README.md) 和 [站外](https://example.com/a)")
    assert "README" in html
    assert "[README]" not in html
    assert 'href="https://example.com/a"' in html


def test_render_markdown_nested_lists_stay_in_one_ordered_list():
    html = render_markdown(
        "1. 第一步\n"
        "   - 子项 A\n"
        "   - 子项 B\n"
        "2. 第二步\n"
    )
    assert html.count("<ol>") == 1
    assert html.count("<ul>") == 1
    assert "<li>第一步<ul><li>子项 A</li><li>子项 B</li></ul></li>" in html
    assert html.index("第一步") < html.index("第二步")


def test_render_markdown_cjk_soft_wrap_has_no_extra_space():
    assert render_markdown("中文\n换行") == "<p>中文换行</p>"
    assert render_markdown("a\nb") == "<p>a b</p>"


def test_render_markdown_drops_dangerous_links_completely():
    assert render_markdown("[点我](javascript:alert(1))") == "<p>点我</p>"


def test_render_markdown_keeps_eula_syntax_with_tables():
    html = render_markdown(
        "# 标题\n\n> 引用\n\n- **粗** 与 `码`\n\n| A |\n|---|\n| 1 |\n\n---\n"
    )
    assert "<h1>标题</h1>" in html
    assert "<blockquote>引用</blockquote>" in html
    assert "<li><strong>粗</strong> 与 <code>码</code></li>" in html
    assert "<table>" in html
    assert "<hr>" in html


def test_real_usage_doc_renders_tables_and_code():
    data = helpdoc.payload()
    assert data["ok"] is True
    assert data["chars"] > 1000
    assert "<h1>" in data["html"]
    assert "<table>" in data["html"]
    assert "<code>" in data["html"]


def test_usage_payload_rereads_file_every_time(tmp_path, monkeypatch):
    path = tmp_path / "usage.md"
    path.write_text("# 第一版\n\n正文", encoding="utf-8")
    monkeypatch.setattr(helpdoc, "DOC_PATH", path)
    assert "第一版" in helpdoc.payload()["html"]
    path.write_text("# 第二版\n\n新的正文", encoding="utf-8")
    assert "第二版" in helpdoc.payload()["html"]


def test_usage_api_is_uncached_and_live(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    path = tmp_path / "usage.md"
    path.write_text("# 使用说明\n\n正文", encoding="utf-8")
    monkeypatch.setattr(helpdoc, "DOC_PATH", path)
    client = TestClient(main.app)
    res = client.get("/api/usage")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-store"
    assert "<h1>使用说明</h1>" in res.json()["html"]
    assert res.json()["updated_at"] > 0
    path.write_text("# 更新后的说明", encoding="utf-8")
    assert "更新后的说明" in client.get("/api/usage").json()["html"]


def test_usage_api_missing_file_returns_404(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(helpdoc, "DOC_PATH", tmp_path / "missing.md")
    assert TestClient(main.app).get("/api/usage").status_code == 404


def test_console_usage_page_is_wired():
    html = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
    js = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
    assert 'data-page="usage"' in html
    assert 'id="page-usage"' in html
    assert 'id="usageDoc"' in html
    assert 'usage: ["使用说明"' in js
    assert 'page === "usage"' in js
    assert "/api/usage" in js


def test_every_console_page_is_documented():
    html = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
    doc = (ROOT / "docs" / "使用说明.md").read_text("utf-8")
    blocks = re.findall(
        r'<button[^>]*data-page="([a-z0-9-]+)"[^>]*>(.*?)</button>',
        html,
        re.S,
    )
    assert blocks, "没有从 index.html 解析到任何导航页"

    section = doc.split("## 2. 左侧页面一览", 1)[1].split("## 3.", 1)[0]
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|\s*[^|]+?\s*\|\s*$", section, re.M)
    documented = {row.strip() for row in rows} - {"页面", "---"}
    assert documented, "使用说明的页面表没有解析到任何行"

    missing = []
    for key, block in blocks:
        label = re.search(r"<span>(.*?)</span>", block, re.S)
        name = (label.group(1) if label else key).strip()
        if name not in documented:
            missing.append(f"{key}（{name}）")
    assert not missing, "使用说明的页面表缺少这些控制台页面：" + "、".join(missing)


def test_eula_renderer_still_available():
    from app import eula

    assert "<h1>T</h1>" in eula.render_markdown("# T")
