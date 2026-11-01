"""B 站 MCP 客户端外壳：只读工具、登录、状态。"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..capabilities.registry import registry as capability_registry
from . import setup as bili_setup

log = logging.getLogger("bili")

BILI_SERVER = "bilibili"
FORBIDDEN_TOOLS = frozenset(
    {
        "bili_reply",
        "bili_send_message",
        "bili_send_dynamic",
        "bili_upload_video",
        "bili_upload_video_multi",
        "bili_send_opus",
        "bili_triple",
        "bili_dm_recent",
        "bili_dm_sessions",
    }
)


def _client():
    mcp = capability_registry.mcp
    return mcp.clients.get(BILI_SERVER) if mcp else None


def _tool_json(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])}
    content = result.get("content")
    if isinstance(content, str):
        try:
            return json.loads(content)
        except Exception:  # noqa: BLE001
            return {"ok": True, "text": content}
    return result


async def call_tool(name: str, args: dict[str, Any] | None = None, *, max_text: int = 4000) -> Any:
    if name in FORBIDDEN_TOOLS:
        return {"ok": False, "error": f"B 站工具未开放：{name}"}
    client = _client()
    if not client or not getattr(client, "connected", False):
        return {"ok": False, "error": "B 站 MCP 未连接，请先安装并登录"}
    return _tool_json(await client.call(name, args or {}, max_text=max_text))


async def status() -> dict[str, Any]:
    mcp = capability_registry.mcp
    item = None
    if mcp:
        item = next((i for i in mcp.status().get("items", []) if i.get("name") == BILI_SERVER), None)
    credential = None
    if item and item.get("connected"):
        try:
            credential = await call_tool("bili_check_credential")
        except Exception as exc:  # noqa: BLE001
            credential = {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "setup": bili_setup.status(),
        "credential_state": bili_setup.credential_state(),
        "mcp": item,
        "credential": credential,
        "tools": [t.get("name") for t in (item or {}).get("tools", []) if isinstance(t, dict)],
    }


async def login_start() -> Any:
    # 二维码 base64 + 终端二维码约 47KB，默认 4000 字截断会把 JSON 拆坏；
    # 终端版对网页无用，解析后丢掉，只回传 message / qrcode_image 等字段。
    data = await call_tool("bili_login", max_text=200_000)
    if isinstance(data, dict) and data.get("qrcode_terminal"):
        data = {k: v for k, v in data.items() if k != "qrcode_terminal"}
    state = data.get("status") if isinstance(data, dict) else None
    log.info("B 站登录：生成二维码 → %s", state or "unknown")
    if isinstance(data, dict) and data.get("error"):
        log.warning("B 站登录失败：%s", data["error"])
    return data


async def login_poll() -> Any:
    data = await call_tool("bili_login_check")
    state = data.get("status") if isinstance(data, dict) else None
    if state == "error":
        log.warning("B 站登录轮询失败：%s", data.get("message") or data.get("error"))
    elif state:
        log.info("B 站登录轮询：%s", state)
    return data
