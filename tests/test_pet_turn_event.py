from __future__ import annotations

import asyncio

import pytest

from app import bus
from app.wechat import manager as manager_mod


class _StubMsg:
    msg_id = "m1"
    content = "在吗"


@pytest.fixture()
def session_and_events(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(bus.bus, "publish", events.append)
    session = manager_mod.AccountSession(
        {"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None
    )
    return session, events


async def _noop_run(self, uid, msg, burst_msgs=None) -> None:
    return None


def test_auto_reply_publishes_turn_start(session_and_events, monkeypatch):
    session, events = session_and_events
    monkeypatch.setattr(manager_mod.AccountSession, "_auto_reply_run", _noop_run)
    asyncio.run(session._auto_reply("u1", _StubMsg()))

    starts = [e for e in events if e.get("type") == "agent_turn" and e["data"]["type"] == "start"]
    assert len(starts) == 1
    assert starts[0]["data"]["account_id"] == "acc_a"
    assert starts[0]["data"]["chat_uid"] == "u1"
    assert starts[0]["data"]["scope"] == "auto"


def test_existing_turn_end_event_not_renamed(session_and_events, monkeypatch):
    """收尾仍走既有的 agent_tool{type:"done"}：桌宠只加事件，不改旧事件的形状。"""
    session, events = session_and_events
    monkeypatch.setattr(manager_mod.AccountSession, "_auto_reply_run", _noop_run)
    asyncio.run(session._auto_reply("u1", _StubMsg()))

    ends = [e for e in events if e.get("type") == "agent_tool" and e["data"]["type"] == "done"]
    assert len(ends) == 1
    assert ends[0]["data"]["scope"] == "auto"
    assert not [e for e in events if e.get("type") == "agent_turn" and e["data"]["type"] == "done"]


def test_turn_start_published_even_when_reply_raises(session_and_events, monkeypatch):
    """出错时 start 仍要发出去：桌宠靠它把 ERROR 从闩锁里放出来。"""
    session, events = session_and_events

    async def boom(self, uid, msg, burst_msgs=None) -> None:
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(manager_mod.AccountSession, "_auto_reply_run", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(session._auto_reply("u1", _StubMsg()))

    assert [e for e in events if e.get("type") == "agent_turn"]
