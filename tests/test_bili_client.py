"""B 站 MCP 发现、工具调用与登录解析。"""
import json
from pathlib import Path

import pytest

from app.capabilities.registry import CapabilityRegistry


def test_registry_includes_bili_config_when_present(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    bili = tmp_path / "bili"
    bili.mkdir()
    (bili / "mcp.json").write_text("{}", "utf-8")
    user = tmp_path / "mcp.json"
    user.write_text("{}", "utf-8")
    cfg = {"capabilities": {"mcp_config": str(user)}}
    reg = CapabilityRegistry(lambda: cfg)
    paths = [str(p) for p in reg._mcp_paths()]
    assert str(user) in paths
    assert str(bili / "mcp.json") in paths


class FakeMcpClient:
    connected = True
    tools = [{"name": "bili_search"}, {"name": "bili_reply"}]

    async def call(self, name, args, max_text=4000):
        assert name == "bili_check_credential"
        return {"ok": True, "content": json.dumps({"logged_in": True, "username": "测试号"})}


class FakeMcp:
    def __init__(self):
        self.clients = {"bilibili": FakeMcpClient()}

    def status(self):
        return {"count": 1, "items": [{"name": "bilibili", "connected": True, "tools": [{"name": "bili_search"}]}]}


class FakeRegistry:
    def __init__(self):
        self.mcp = FakeMcp()


def test_tool_json_parses_content():
    from app.bili import client as bili_client

    assert bili_client._tool_json({"ok": True, "content": '{"a": 1}'}) == {"a": 1}
    assert bili_client._tool_json({"ok": True, "content": "plain"}) == {"ok": True, "text": "plain"}
    assert bili_client._tool_json({"error": "boom"}) == {"ok": False, "error": "boom"}


def test_login_start_keeps_long_qrcode(monkeypatch):
    import asyncio

    from app.bili import client as bili_client

    seen = {}

    class LongQrClient:
        connected = True

        async def call(self, name, args, max_text=4000):
            seen["max_text"] = max_text
            payload = {
                "status": "qrcode_ready",
                "message": "请扫码",
                "qrcode_image": "data:image/png;base64," + "A" * 30_000,
                "qrcode_terminal": "B" * 30_000,
            }
            return {"ok": True, "content": json.dumps(payload)}

    class Registry:
        mcp = type("Mcp", (), {"clients": {"bilibili": LongQrClient()}})()

    monkeypatch.setattr(bili_client, "capability_registry", Registry)
    out = asyncio.run(bili_client.login_start())
    assert seen["max_text"] >= 47_000
    assert out["status"] == "qrcode_ready"
    assert out["qrcode_image"].startswith("data:image/png;base64,")
    assert "qrcode_terminal" not in out


def test_forbidden_tool_is_rejected(monkeypatch):
    import asyncio

    from app.bili import client as bili_client

    monkeypatch.setattr(bili_client, "capability_registry", FakeRegistry())
    out = asyncio.run(bili_client.call_tool("bili_reply", {}))
    assert out["ok"] is False
    assert "未开放" in out["error"]


def test_status_includes_credential(monkeypatch):
    import asyncio

    from app.bili import client as bili_client
    from app.bili import setup as bili_setup

    monkeypatch.setattr(bili_client, "capability_registry", FakeRegistry())
    monkeypatch.setattr(
        bili_setup,
        "credential_state",
        lambda: {"exists": True, "has_sessdata": False, "has_dedeuserid": False},
    )
    out = asyncio.run(bili_client.status())
    assert out["mcp"]["name"] == "bilibili"
    assert out["credential"]["username"] == "测试号"
    assert out["credential_state"]["has_sessdata"] is False
