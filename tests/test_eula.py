"""EULA：中英文版本、Markdown 渲染与协议路由。"""
from __future__ import annotations

import re
from pathlib import Path

from app import eula

ROOT = Path(__file__).resolve().parent.parent
SECTION_RE = re.compile(r"^## (\d+)\. ", re.M)
CLAUSE_RE = re.compile(r"^(\d+\.\d+) ", re.M)


def test_bilingual_versions_cover_the_same_numbered_sections():
    zh = (ROOT / "EULA.md").read_text(encoding="utf-8")
    en_path = ROOT / "EULA.en.md"
    assert en_path.is_file(), "缺少英文译本 EULA.en.md"
    en = en_path.read_text(encoding="utf-8")

    expected = [str(i) for i in range(1, 17)]
    assert SECTION_RE.findall(zh) == expected
    assert SECTION_RE.findall(en) == expected

    zh_clauses = CLAUSE_RE.findall(zh)
    assert zh_clauses == CLAUSE_RE.findall(en)
    assert len(zh_clauses) == 62

    assert "版本 1.0" in zh
    assert "2026 年 10 月 28 日" in zh
    assert "Version 1.0" in en
    assert "October 28, 2026" in en


def test_render_markdown_handles_eula_syntax():
    html = eula.render_markdown(
        "# Title\n\n> quote\n\n## 1. Section\n\n"
        "- **bold** and `code`\n"
        "- [link](https://example.com/a)\n\n"
        "---\n\n<https://example.org>"
    )
    assert "<h1>Title</h1>" in html
    assert "<blockquote>quote</blockquote>" in html
    assert "<h2>1. Section</h2>" in html
    assert "<ul><li><strong>bold</strong> and <code>code</code></li>" in html
    assert (
        '<a href="https://example.com/a" target="_blank" rel="noopener">link</a>'
        in html
    )
    assert "<hr>" in html
    assert (
        '<a href="https://example.org" target="_blank" rel="noopener">'
        "https://example.org</a>" in html
    )


def test_render_markdown_joins_soft_wrapped_paragraph_lines():
    assert eula.render_markdown("a\nb") == "<p>a b</p>"


def test_render_markdown_escapes_raw_html():
    html = eula.render_markdown('<script>alert("x")</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_real_documents_render_without_leftover_markdown():
    for lang in ("zh", "en"):
        body = eula.payload(lang)["html"]
        assert "**" not in body
        assert "](" not in body
        assert "&lt;" not in body
        assert "<script" not in body.lower()
        assert "<h2>1." in body
        assert "<h2>16." in body


def test_payload_and_page_follow_language():
    zh = eula.payload("zh")
    assert zh["lang"] == "zh"
    assert "最终用户许可协议" in zh["html"]
    en = eula.payload("en")
    assert en["lang"] == "en"
    assert "End User License Agreement" in en["html"]
    assert eula.payload("nope")["lang"] == "zh"

    page = eula.page("en")
    assert page.startswith("<!DOCTYPE html>")
    assert 'lang="en"' in page
    assert "/eula?lang=zh" in page
    assert "End User License Agreement" in page


def test_routes_serve_page_and_payload(monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, main

    monkeypatch.setattr(config, "load", lambda: {"server": {"access_token": ""}})
    client = TestClient(main.app)

    payload = client.get("/api/eula?lang=en")
    assert payload.status_code == 200
    assert payload.json()["lang"] == "en"

    page = client.get("/eula")
    assert page.status_code == 200
    assert "最终用户许可协议" in page.text

    bogus = client.get("/api/eula?lang=bogus")
    assert bogus.status_code == 200
    assert bogus.json()["lang"] == "zh"
    assert "最终用户许可协议" in client.get("/eula?lang=bogus").text


def test_missing_eula_file_returns_404_instead_of_500(monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, main

    monkeypatch.setattr(config, "load", lambda: {"server": {"access_token": ""}})
    monkeypatch.setattr(eula, "load", lambda lang: (_ for _ in ()).throw(OSError("missing")))
    client = TestClient(main.app)

    page = client.get("/eula")
    assert page.status_code == 404
    assert "协议文件缺失" in page.text

    payload = client.get("/api/eula")
    assert payload.status_code == 404
    assert "协议文件缺失" in payload.json()["detail"]
