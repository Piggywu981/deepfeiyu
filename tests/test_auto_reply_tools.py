"""自动回复：思考链与工具调用随 AI 回复持久化（meta.reasoning / meta.tools），供控制台展示。"""
from __future__ import annotations

import asyncio

import pytest

from app import db
from app.wechat.base import Message
from app.wechat.manager import AccountSession


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    db.init()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


def _cfg() -> dict:
    return {
        "send": {"trailing_emoji_gap_sec": [0, 0], "trailing_emoji_cooldown_sec": 0},
        "auto": {"history_context": 12, "max_reply_chars": 300, "prefix": ""},
        "ai": {
            "temperature": 0.7,
            "max_tokens": 800,
            "vision_enabled": False,
            "memory_enabled": False,
            "system_prompt": "人设",
        },
    }


class FakeLLM:
    """按预设脚本回放 stream 事件：第 N 轮对应一段事件列表，依次弹出。"""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self.rounds = rounds

    async def stream(self, messages, tools=None, **kwargs):
        for evt in self.rounds.pop(0):
            yield evt


class StubDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _tool_round(tool_name: str = "get_current_time") -> list[dict]:
    return [
        {"type": "reasoning", "text": "对方问时间，我先查一下当前时间"},
        {"type": "tool_delta", "index": 0, "id": "call_1", "name": tool_name, "args": "{}"},
        {"type": "finish", "reason": "tool_calls"},
    ]


def _text_round(text: str) -> list[dict]:
    return [
        {"type": "text", "text": text},
        {"type": "finish", "reason": "stop"},
    ]


def _reply_row(uid: str) -> dict:
    rows = db.list_messages(uid, limit=10)
    return next(r for r in rows if r["is_self"] and r["source"] == "agent")


def test_auto_reply_persists_reasoning_and_tools(temp_db):
    llm = FakeLLM([_tool_round(), _text_round("现在 10 点")])
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg, llm)
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="现在几点", msg_type="text", msg_id="m1", ts=1001.0,
    )

    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.sent and session.driver.sent[0][0] == "c1"
    reply = _reply_row(uid)
    assert reply["content"].endswith("现在 10 点")
    assert reply["meta"]["reasoning"] == "对方问时间，我先查一下当前时间"
    tools = reply["meta"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "get_current_time"
    assert tools[0]["ok"] is True
    assert "time" in tools[0]["result"]


def test_auto_reply_without_tools_keeps_empty_tools_log(temp_db):
    llm = FakeLLM([_text_round("在的")])
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg, llm)
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m2", ts=1002.0,
    )

    asyncio.run(session._auto_reply(uid, msg))

    reply = _reply_row(uid)
    assert reply["content"].endswith("在的")
    assert reply["meta"]["tools"] == []
    assert reply["meta"]["reasoning"] == ""


def test_auto_reply_publishes_live_reasoning_and_done_events(temp_db, monkeypatch):
    from app.wechat import manager as manager_mod

    events: list[dict] = []
    monkeypatch.setattr(manager_mod.bus, "publish", lambda evt: events.append(evt))
    llm = FakeLLM([_tool_round(), _text_round("现在 10 点")])
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg, llm)
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="现在几点", msg_type="text", msg_id="m3", ts=1003.0,
    )

    asyncio.run(session._auto_reply(uid, msg))

    reasoning = [e for e in events if e["type"] == "agent_reasoning"]
    assert reasoning, "思考增量应实时发布到总线"
    assert "".join(e["data"]["text"] for e in reasoning) == "对方问时间，我先查一下当前时间"
    assert all(
        e["data"]["scope"] == "auto" and e["data"]["account_id"] == "acc_a" and e["data"]["chat_uid"] == uid
        for e in reasoning
    )
    tools = [e for e in events if e["type"] == "agent_tool" and e["data"].get("type") == "tool_start"]
    assert tools and tools[0]["data"]["name"] == "get_current_time"
    done = [e for e in events if e["type"] == "agent_tool" and e["data"].get("type") == "done"]
    assert done and done[0]["data"]["chat_uid"] == uid
