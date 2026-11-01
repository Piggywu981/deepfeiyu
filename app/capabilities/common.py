"""能力系统的小工具：把 JSON 里常见的真假值写法统一成 bool。"""
from __future__ import annotations

from typing import Any

_TRUE = {"1", "true", "yes", "on", "y", "是"}
_FALSE = {"0", "false", "no", "off", "n", "否", ""}


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return default


def compose_tool_name(prefix: str, namespace: str, tool: str, max_len: int = 64) -> str:
    """拼 `prefix__namespace__tool`，超长时优先保留工具名尾部。"""
    def clean(value: str) -> str:
        import re

        text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
        return text or "tool"

    ns = clean(namespace)
    name = clean(tool)
    # 至少给工具名留 8 个字符，命名空间按剩余空间截断
    reserve = min(len(name), 8)
    max_ns = max(1, max_len - len(prefix) - 4 - reserve)
    if len(ns) > max_ns:
        import hashlib

        digest = hashlib.sha1(ns.encode("utf-8")).hexdigest()[:4]
        keep = max(1, max_ns - 5)
        ns = f"{ns[:keep]}_{digest}"[:max_ns]
    head = f"{prefix}__{ns}__"
    room = max(1, max_len - len(head))
    # 工具名太长时保留尾部：同名工具往往靠后缀区分（read_file / write_file）
    return (head + (name[-room:] if len(name) > room else name))[:max_len]
