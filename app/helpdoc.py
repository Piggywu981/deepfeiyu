"""使用说明：读取 docs/使用说明.md，供控制台「说明」页调用。

不缓存正文：每次请求都重新读文件，改完文档刷新控制台就能看到。
（模块名避开 app/usage.py —— 那是 LLM 用量账本。）
"""
from __future__ import annotations

from typing import Any

from . import config
from .markdown_lite import render_markdown

DOC_PATH = config.ROOT / "docs" / "使用说明.md"


def load() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def payload() -> dict[str, Any]:
    text = load()
    return {
        "ok": True,
        "title": "使用说明",
        "chars": len(text),
        "updated_at": DOC_PATH.stat().st_mtime,
        "html": render_markdown(text),
    }
