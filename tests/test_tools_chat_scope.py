"""自动回复模式（绑定 chat_uid）下，会话类工具只能触达当前会话，防止注入套出其他会话内容。"""
import asyncio

from app.ai import tools as tools_mod
from app.wechat.manager import AccountSession


def _session() -> AccountSession:
    return AccountSession({"id": "acc_x", "name": "A", "nickname": "B"}, lambda: {"ai": {}}, None)


def test_list_chats_only_shows_current_chat(monkeypatch):
    session = _session()

    def fake_visible_chats(keyword="", limit=20):
        return [
            {"uid": "uid_here", "chat_id": "c1", "name": "本群", "is_group": True,
             "last_message": "hi", "last_ts": 0},
            {"uid": "uid_other", "chat_id": "c2", "name": "别家", "is_group": False,
             "last_message": "secret", "last_ts": 0},
        ]

    monkeypatch.setattr(session, "visible_chats", fake_visible_chats)
    tool = {
        t.name: t for t in tools_mod.build_tools(session, allow_send=False, chat_uid="uid_here")
    }["list_wechat_chats"]
    res = asyncio.run(tool.handler({}))
    assert [c["name"] for c in res] == ["本群"]


def test_get_history_refuses_other_chat(monkeypatch):
    session = _session()

    async def fake_resolve(name):
        return {"uid": "uid_other", "chat_id": "c2", "name": name}

    monkeypatch.setattr(session, "resolve_chat", fake_resolve)
    tool = {
        t.name: t for t in tools_mod.build_tools(session, allow_send=False, chat_uid="uid_here")
    }["get_chat_history"]
    res = asyncio.run(tool.handler({"chat": "别家"}))
    assert "error" in res and "当前会话" in res["error"]


def test_get_history_allows_current_chat(monkeypatch):
    session = _session()

    async def fake_resolve(name):
        return {"uid": "uid_here", "chat_id": "c1", "name": name}

    monkeypatch.setattr(session, "resolve_chat", fake_resolve)
    monkeypatch.setattr(
        tools_mod.db,
        "list_messages",
        lambda uid, limit=20: [
            {"ts": 0, "is_self": False, "sender_name": "群友", "chat_name": "本群", "content": "hello"}
        ],
    )
    tool = {
        t.name: t for t in tools_mod.build_tools(session, allow_send=False, chat_uid="uid_here")
    }["get_chat_history"]
    res = asyncio.run(tool.handler({"chat": "本群"}))
    assert res["chat_id"] == "c1"
    assert res["messages"][0]["text"] == "hello"


def test_search_messages_filtered_to_current_chat(monkeypatch):
    session = _session()

    def fake_search(account_id, keyword, limit=10):
        return [
            {"uid": "uid_here", "chat_name": "本群", "ts": 1, "content": "a"},
            {"uid": "uid_other", "chat_name": "别家", "ts": 2, "content": "b"},
        ]

    monkeypatch.setattr(tools_mod.db, "search_messages", fake_search)
    tool = {
        t.name: t for t in tools_mod.build_tools(session, allow_send=False, chat_uid="uid_here")
    }["search_messages"]
    res = asyncio.run(tool.handler({"keyword": "x"}))
    assert [r["chat"] for r in res] == ["本群"]


def test_console_mode_still_sees_all_chats(monkeypatch):
    session = _session()

    def fake_visible_chats(keyword="", limit=20):
        return [
            {"uid": "uid_a", "chat_id": "c1", "name": "甲", "is_group": False,
             "last_message": "", "last_ts": 0},
            {"uid": "uid_b", "chat_id": "c2", "name": "乙", "is_group": False,
             "last_message": "", "last_ts": 0},
        ]

    monkeypatch.setattr(session, "visible_chats", fake_visible_chats)
    tool = {t.name: t for t in tools_mod.build_tools(session, allow_send=False)}["list_wechat_chats"]
    res = asyncio.run(tool.handler({}))
    assert [c["name"] for c in res] == ["甲", "乙"]
