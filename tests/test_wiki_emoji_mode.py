"""仅AI娘表情包模式（ai.wiki_emoji_only）：search_emojis 过滤、send_wechat_emoji 拦截。"""
from __future__ import annotations

import asyncio

import pytest

from app import config, db
from app.ai import moe_wiki
from app.ai.tools import build_tools


ROWS = [
    {"id": 1, "tags": "滑稽", "url": "https://e/1", "description": "滑稽"},
    {"id": 2, "tags": "大肥鱼,摸鱼", "url": "https://e/2", "description": "肥鱼"},
    {"id": 3, "tags": "Claude 娘", "url": "https://e/3", "description": ""},
    {"id": 4, "tags": "", "url": "https://e/4", "description": "无标签"},
]


class FakeSession:
    account_id = "acc"
    llm = type("L", (), {"configured": False})()

    async def resolve_chat(self, name: str):
        return {"uid": "u1", "name": name or "会话"}

    async def send_emoji(self, uid: str, row: dict, source: str = "agent") -> None:
        self.sent = getattr(self, "sent", [])
        self.sent.append((uid, row))


@pytest.fixture()
def wiki_names(monkeypatch):
    monkeypatch.setattr(moe_wiki, "character_names", lambda root=None: ["大肥鱼", "Claude娘"])


def _tool(name: str):
    tools = build_tools(FakeSession(), allow_send=True, chat_uid="u1")
    return next(t for t in tools if t.name == name)


def _fake_db(monkeypatch, rows):
    monkeypatch.setattr(db, "search_emojis", lambda *a, **k: [dict(r) for r in rows])
    monkeypatch.setattr(db, "get_emoji", lambda account_id, eid: next((r for r in rows if r["id"] == eid), None))


def test_search_filters_to_roster_tags(monkeypatch, wiki_names):
    _fake_db(monkeypatch, ROWS)
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    out = asyncio.run(_tool("search_emojis").handler({"query": ""}))
    assert [r["id"] for r in out] == [2, 3]


def test_search_passthrough_when_mode_off(monkeypatch, wiki_names):
    _fake_db(monkeypatch, ROWS)
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": False}})
    out = asyncio.run(_tool("search_emojis").handler({"query": ""}))
    assert [r["id"] for r in out] == [1, 2, 3, 4]


def test_send_blocked_without_roster_tag(monkeypatch, wiki_names):
    _fake_db(monkeypatch, ROWS)
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    res = asyncio.run(_tool("send_wechat_emoji").handler({"emoji_id": 1, "to": "会话"}))
    assert "error" in res
    assert "仅AI娘" in res["error"]


def test_send_allowed_with_roster_tag(monkeypatch, wiki_names):
    _fake_db(monkeypatch, ROWS)
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    res = asyncio.run(_tool("send_wechat_emoji").handler({"emoji_id": 2, "to": "会话"}))
    assert res.get("ok") is True


def test_search_empty_result_hints_roster(monkeypatch, wiki_names):
    _fake_db(monkeypatch, [])
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    out = asyncio.run(_tool("search_emojis").handler({"query": ""}))
    assert "图鉴" in out["hint"]
