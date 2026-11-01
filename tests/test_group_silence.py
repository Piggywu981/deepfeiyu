"""群聊不 @ 的静默：提示词跟随 group_need_at，[SKIP] 不落成微信消息。"""
from __future__ import annotations

import asyncio

import pytest

from app import config, db
from app.wechat.base import Message
from app.wechat.manager import AccountSession


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
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


def _cfg(group_need_at: bool) -> dict:
    return {
        "send": {"enabled": False},
        "ai": {
            "vision_enabled": False,
            "memory_enabled": False,
            "emoji_with_reply": False,
            "system_prompt": "人设",
        },
        "auto": {
            "group_need_at": group_need_at,
            "history_context": 12,
            "max_reply_chars": 300,
        },
    }


class _LLM:
    configured = True


class FakeDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.calls.append(("text", text))

    def send_emoji(self, chat_id: str, path: str) -> None:
        self.calls.append(("emoji", path))


class FakeAgent:
    behavior: dict = {}

    def __init__(self, llm, tools, max_rounds: int = 5) -> None:
        self.tools = {t.name: t for t in tools}

    async def run(self, messages, on_event=None, **kw):
        return {"text": FakeAgent.behavior.get("text", ""), "usage": {}}


def _group_msg(content: str = "得老71才能踢", msg_id: str = "g1") -> Message:
    return Message(
        chat_id="room@chatroom",
        chat_name="吃瓜群",
        sender_id="wxid_a",
        sender_name="测试丙",
        content=content,
        msg_id=msg_id,
        is_group=True,
        ts=1001.0,
    )


def _session(cfg: dict) -> AccountSession:
    return AccountSession({"id": "acc_x", "name": "A", "nickname": "B"}, lambda: cfg, _LLM())


def test_group_prompt_follows_group_need_at(temp_db):
    """群聊必须 @ 时才提 @ 规则；不 @ 模式要显式给出 [SKIP] 静默出口。"""
    cfg_at = _cfg(group_need_at=True)
    cfg_free = _cfg(group_need_at=False)
    session = _session(cfg_free)
    uid = db.upsert_chat("acc_x", "room@chatroom", "吃瓜群", True, "在吗", 1000.0)
    chat = db.get_chat(uid)
    msg = _group_msg()

    prompt_at = "\n".join(
        m["content"] for m in session._build_prompt(cfg_at, chat, [], msg)
    )
    prompt_free = "\n".join(
        m["content"] for m in session._build_prompt(cfg_free, chat, [], msg)
    )

    assert "只回应 @ 你的内容" in prompt_at
    assert "[SKIP]" not in prompt_at
    assert "只回应 @ 你的内容" not in prompt_free
    assert "[SKIP]" in prompt_free
    # 两种模式都不该让模型把括号动作/内心独白当正文发出去
    assert "不要用括号" in prompt_free


def test_text_skip_sends_nothing(temp_db, monkeypatch):
    """模型判断这轮不用接话时，[SKIP] 只能留在服务端，不能发进群。"""
    session = _session(_cfg(group_need_at=False))
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "room@chatroom", "吃瓜群", True, "在吗", 1000.0)
    db.set_chat_ai(uid, True)
    FakeAgent.behavior = {"text": "[SKIP]"}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)

    asyncio.run(session._auto_reply(uid, _group_msg()))

    assert session.driver.calls == []
    self_texts = [m for m in db.list_messages(uid, limit=20) if m["is_self"]]
    assert self_texts == []
    events = db.list_events("acc_x", limit=10)
    assert any("不需要接话" in (e.get("title") or "") for e in events)
