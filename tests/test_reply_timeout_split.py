"""自动回复超时兜底与多条拆分：到点必须至少回一条；--- 拆分逐条发送。"""
from __future__ import annotations

import asyncio

import pytest

from app import db
from app.wechat.base import Message
from app.wechat import manager as manager_mod
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


def _cfg(**auto_extra) -> dict:
    auto = {"history_context": 12, "max_reply_chars": 300, "prefix": ""}
    auto.update(auto_extra)
    return {
        "auto": auto,
        "send": {"trailing_emoji_gap_sec": [0, 0], "trailing_emoji_cooldown_sec": 0},
        "ai": {
            "temperature": 0.7,
            "max_tokens": 800,
            "vision_enabled": False,
            "memory_enabled": False,
            "system_prompt": "人设",
        },
    }


class SlowLLM:
    """先回放预置事件，然后一直卡住不结束——模拟模型/工具循环拖死。"""

    def __init__(self, pre_events: list[dict] | None = None) -> None:
        self.pre_events = pre_events or []

    async def stream(self, messages, tools=None, **kwargs):
        for evt in self.pre_events:
            yield evt
        await asyncio.sleep(30)


class ScriptLLM:
    """按脚本回放一轮事件。"""

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


def _msg(content: str, msg_id: str) -> Message:
    return Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content=content, msg_type="text", msg_id=msg_id, ts=1001.0,
    )


def _session(cfg, llm) -> AccountSession:
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: cfg, llm)
    session.driver = StubDriver()
    return session


def _agent_reply(uid: str) -> dict:
    rows = db.list_messages(uid, limit=10)
    return next(r for r in rows if r["is_self"] and r["source"] == "agent")


def _uid() -> str:
    return db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)


def test_timeout_sends_fallback_when_nothing_streamed(temp_db, monkeypatch):
    cfg = _cfg(reply_timeout_sec=0.2)
    session = _session(cfg, SlowLLM())
    uid = _uid()
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    asyncio.run(session._auto_reply(uid, _msg("在吗", "m1")))

    assert len(session.driver.sent) == 1
    assert session.driver.sent[0][1] == "稍等，我想想怎么回你"
    reply = _agent_reply(uid)
    assert reply["meta"]["timeout"] is True
    events = db.list_events("acc_a", 10)
    assert any("超时" in e["title"] and e["level"] == "warn" for e in events)


def test_timeout_sends_partial_text_when_streamed(temp_db, monkeypatch):
    cfg = _cfg(reply_timeout_sec=0.2)
    llm = SlowLLM([{"type": "text", "text": "抱歉来晚了"}])
    session = _session(cfg, llm)
    uid = _uid()
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    asyncio.run(session._auto_reply(uid, _msg("在吗", "m2")))

    assert session.driver.sent[0][1].endswith("抱歉来晚了")


def test_no_timeout_when_disabled(temp_db):
    cfg = _cfg(reply_timeout_sec=0)
    session = _session(cfg, SlowLLM())
    uid = _uid()

    async def run() -> None:
        task = asyncio.create_task(session._auto_reply(uid, _msg("在吗", "m3")))
        await asyncio.sleep(0.3)
        assert not session.driver.sent, "未配置超时前不应发送任何消息"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_split_reply_sends_multiple_messages(temp_db, monkeypatch):
    cfg = _cfg(split_reply=True)
    llm = ScriptLLM([[{"type": "text", "text": "我看了下\n---\n明天下午有空\n---\n要不三点见？"}]])
    session = _session(cfg, llm)
    uid = _uid()
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    asyncio.run(session._auto_reply(uid, _msg("明天有空吗", "m4")))

    sent = [t for _, t in session.driver.sent]
    assert sent == ["我看了下", "明天下午有空", "要不三点见？"]
    # tools/reasoning 挂在最后一条的 meta 上
    rows = db.list_messages(uid, limit=10)
    agent_rows = [r for r in rows if r["is_self"] and r["source"] == "agent"]
    assert len(agent_rows) == 3
    assert agent_rows[-1]["meta"]["tools"] == []
    assert agent_rows[0]["meta"] == {}
    events = db.list_events("acc_a", 10)
    assert any("共 3 条" in (e["detail"] or "") for e in events)


def test_newline_reply_sends_separate_messages(temp_db, monkeypatch):
    """用户要求：分开发送，不是换行发送——没写 --- 只用换行的回复也要拆成多条。"""
    cfg = _cfg(split_reply=True)
    llm = ScriptLLM([[{"type": "text", "text": "我那是饿的，不是贪吃啊\n\n小鱼干哪够塞牙缝呢"}]])
    session = _session(cfg, llm)
    uid = _uid()
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    asyncio.run(session._auto_reply(uid, _msg("你饿了吧", "m7")))

    sent = [t for _, t in session.driver.sent]
    assert sent == ["我那是饿的，不是贪吃啊", "小鱼干哪够塞牙缝呢"]
    assert all("\n" not in t for t in sent)


def test_split_off_keeps_dash_as_literal_text(temp_db, monkeypatch):
    cfg = _cfg(split_reply=False)
    llm = ScriptLLM([[{"type": "text", "text": "我看了下\n---\n明天下午有空"}]])
    session = _session(cfg, llm)
    uid = _uid()
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    asyncio.run(session._auto_reply(uid, _msg("明天有空吗", "m5")))

    assert len(session.driver.sent) == 1
    assert "---" in session.driver.sent[0][1]


def test_split_prompt_hint_only_when_enabled(temp_db):
    session = _session(_cfg(split_reply=True), ScriptLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    msg = _msg("在吗", "m6")
    prompt_on = session._build_prompt(session.get_cfg(), db.get_chat(uid), [], msg)
    assert "---" in prompt_on[0]["content"]

    session_off = _session(_cfg(split_reply=False), ScriptLLM([]))
    prompt_off = session_off._build_prompt(session_off.get_cfg(), db.get_chat(uid), [], msg)
    assert "---" not in prompt_off[0]["content"]
