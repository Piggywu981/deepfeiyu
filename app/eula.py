"""EULA 文档：读取根目录 Markdown，渲染独立协议页与控制台协议接口。"""
from __future__ import annotations

import html
from typing import Any

from . import config
from .markdown_lite import render_markdown

FILES = {"zh": "EULA.md", "en": "EULA.en.md"}
TITLES = {"zh": "最终用户许可协议", "en": "End User License Agreement"}
SWITCH_LABEL = {"zh": "English", "en": "中文"}
BACK_LABEL = {"zh": "返回控制台", "en": "Back to console"}


def normalize_lang(lang: str | None) -> str:
    """只认 zh / en，其余一律回落中文（中文版本为准）。"""
    return "en" if lang == "en" else "zh"


def load(lang: str) -> str:
    path = config.ROOT / FILES[normalize_lang(lang)]
    return path.read_text(encoding="utf-8")


def payload(lang: str | None = "zh") -> dict[str, Any]:
    code = normalize_lang(lang)
    return {
        "lang": code,
        "title": TITLES[code],
        "html": render_markdown(load(code)),
    }


PAGE_CSS = """
:root {
  --bg: #f4f6f8;
  --panel: #ffffff;
  --line: #e7ecf2;
  --ink: #0f172a;
  --ink-2: #44506a;
  --muted: #8a94a6;
  --green: #07c160;
  --green-d: #05a552;
  --green-soft: #f2fbf6;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.85;
  -webkit-font-smoothing: antialiased;
}
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  max-width: 900px;
  margin: 0 auto;
  padding: 22px 20px 0;
}
.brand {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  color: var(--ink);
  text-decoration: none;
  font-weight: 700;
  font-size: 15px;
}
.brand span { color: var(--muted); font-size: 11px; font-weight: 500; letter-spacing: 0.6px; text-transform: uppercase; }
.top-nav { display: flex; align-items: center; gap: 14px; font-size: 12px; }
.top-nav a { color: var(--green-d); text-decoration: none; }
.top-nav a:hover { text-decoration: underline; }
.top-nav .switch {
  padding: 5px 12px;
  border: 1px solid #cdeedb;
  border-radius: 20px;
  background: var(--green-soft);
}
.wrap { max-width: 900px; margin: 0 auto; padding: 18px 20px 60px; }
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 34px 42px 38px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 14px 34px -22px rgba(16, 24, 40, 0.35);
}
.doc h1 { margin: 0 0 6px; font-size: 24px; line-height: 1.35; letter-spacing: -0.3px; }
.doc h2 { margin: 30px 0 10px; font-size: 16px; letter-spacing: -0.1px; }
.doc h3 { margin: 22px 0 8px; font-size: 14.5px; }
.doc p { margin: 9px 0; color: var(--ink-2); }
.doc ul { margin: 8px 0; padding-left: 22px; color: var(--ink-2); }
.doc li { margin: 4px 0; }
.doc blockquote {
  margin: 14px 0;
  padding: 10px 14px;
  border-left: 3px solid var(--green);
  border-radius: 8px;
  background: var(--green-soft);
  color: var(--ink-2);
}
.doc hr { border: 0; border-top: 1px solid var(--line); margin: 24px 0; }
.doc a { color: var(--green-d); text-decoration: none; }
.doc a:hover { text-decoration: underline; }
.doc strong { color: var(--ink); }
.doc code {
  padding: 1px 5px;
  border-radius: 4px;
  background: #f1f4f8;
  font-size: 12px;
}
@media (max-width: 640px) {
  .top { padding: 16px 14px 0; }
  .wrap { padding: 14px 12px 40px; }
  .card { padding: 22px 18px 26px; border-radius: 14px; }
  .doc h1 { font-size: 20px; }
  .doc h2 { font-size: 15px; }
}
"""


def missing_page(lang: str | None = "zh") -> str:
    """EULA.md 缺失时的 404 页，避免协议入口直接抛 500。"""
    code = normalize_lang(lang)
    title = "协议文件缺失" if code == "zh" else "License document missing"
    desc = (
        "未找到 EULA.md，请从项目仓库恢复后刷新页面。"
        if code == "zh"
        else "EULA.md was not found. Restore it from the project repository and reload."
    )
    back = BACK_LABEL[code]
    html_lang = "zh-CN" if code == "zh" else "en"
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title} · Deep肥鱼</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<main class="wrap">
  <article class="card doc"><h1>{title}</h1><p>{desc}</p><p><a href="/">{back}</a></p></article>
</main>
</body>
</html>
"""


def page(lang: str | None = "zh") -> str:
    code = normalize_lang(lang)
    other = "en" if code == "zh" else "zh"
    body = render_markdown(load(code))
    title = html.escape(TITLES[code])
    html_lang = "zh-CN" if code == "zh" else "en"
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title} · Deep肥鱼</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<header class="top">
  <a class="brand" href="/">Deep肥鱼 <span>DeepFeiyu</span></a>
  <nav class="top-nav">
    <a class="switch" href="/eula?lang={other}">{SWITCH_LABEL[code]}</a>
    <a href="/">{BACK_LABEL[code]}</a>
  </nav>
</header>
<main class="wrap">
  <article class="card doc">{body}</article>
</main>
</body>
</html>
"""
