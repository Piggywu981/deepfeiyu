"""每轮话费归因：LLM 成本、Agent 汇总、控制台/自动回复落库。"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient

from app import config, db, main, usage as usage_mod
from app.ai.agent import Agent
from app.ai.llm import LLM
from app.wechat.base import Message
from app.wechat.manager import AccountSession


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(
        config,
        "load",
        lambda: {
            "server": {"access_token": ""},
            "ai": {
                "api_key": "sk-test",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "vision_enabled": False,
                "memory_enabled": False,
                "web_enabled": False,
            },
            "auto": {},
        },
    )
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


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    fresh = usage_mod.UsageLedger(tmp_path / "whale-usage.json")
    monkeypatch.setattr(usage_mod, "LEDGER", fresh)
    return fresh


def _llm() -> LLM:
    cfg = {
        "ai": {
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "price_input_hit": 0.2,
            "price_input_miss": 2.0,
            "price_output": 3.0,
        }
    }
    return LLM(lambda: cfg)


class FakeLLM:
    """按脚本回放 usage 事件；用于验证 Agent 的跨轮汇总。"""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self.rounds = rounds

    async def stream(self, messages, tools=None, **kwargs):
        for evt in self.rounds.pop(0):
            yield evt


def _usage(cost: float, prompt: int = 10, completion: int = 5) -> dict:
    return {
        "type": "usage",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost": cost,
    }


def test_record_usage_returns_cost_and_records_ledger(ledger):
    cost = _llm()._record_usage(
        "deepseek-chat",
        {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "prompt_cache_hit_tokens": 400,
            "prompt_cache_miss_tokens": 600,
        },
    )
    assert cost == pytest.approx(0.00278)
    last = ledger.last_turn()
    assert last["amount"] == pytest.approx(0.00278)
    assert last["tokens"] == 1500


def test_agent_sums_cost_across_tool_rounds():
    async def noop(args):
        return {"ok": True}

    from app.ai.agent import Tool

    llm = FakeLLM(
        [
            [
                {"type": "tool_delta", "index": 0, "id": "call_1", "name": "noop", "args": "{}"},
                _usage(0.001),
                {"type": "finish", "reason": "tool_calls"},
            ],
            [
                {"type": "text", "text": "好了"},
                _usage(0.002, prompt=20, completion=8),
                {"type": "finish", "reason": "stop"},
            ],
        ]
    )
    events: list[dict] = []

    async def on_event(evt):
        events.append(evt)

    result = asyncio.run(
        Agent(llm, [Tool("noop", "", {}, noop)], max_rounds=2).run(
            [{"role": "user", "content": "开始"}], on_event=on_event
        )
    )
    assert result["usage"]["measured"] is True
    assert result["usage"]["cost"] == pytest.approx(0.003)
    assert result["usage"]["prompt_tokens"] == 30
    assert result["usage"]["completion_tokens"] == 13
    done = next(evt for evt in events if evt["type"] == "done")
    assert done["usage"]["cost"] == pytest.approx(0.003)


def test_agent_without_usage_is_not_measured():
    class SilentLLM:
        async def stream(self, messages, tools=None, **kwargs):
            yield {"type": "text", "text": "好"}
            yield {"type": "finish", "reason": "stop"}

    result = asyncio.run(Agent(SilentLLM(), [], max_rounds=1).run([{"role": "user", "content": "hi"}]))
    assert result["usage"]["measured"] is False
    assert result["usage"]["cost"] is None


def test_turn_usage_meta_only_for_measured_usage():
    assert usage_mod.turn_usage_meta({}) == {}
    assert usage_mod.turn_usage_meta({"measured": False, "cost": 1, "prompt_tokens": 1}) == {}
    assert usage_mod.turn_usage_meta(
        {"measured": True, "cost": 0.00278, "prompt_tokens": 1000, "completion_tokens": 500}
    ) == {"cost": 0.00278, "tokens": {"input": 1000, "output": 500}}
    assert usage_mod.turn_usage_meta(
        {"measured": True, "cost": None, "prompt_tokens": 10, "completion_tokens": 5}
    ) == {"tokens": {"input": 10, "output": 5}}


class _FakeSession:
    account_id = "acc_cost"
    account = {"id": "acc_cost", "name": "测试号"}
    name = "测试号"
    nickname = "小鲸"
    driver = None


class _FakeAgent:
    usage = {
        "measured": True,
        "cost": 0.00278,
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, messages, on_event=None, **kwargs):
        for evt in (
            {"type": "delta", "text": "收到"},
            {"type": "done", "text": "收到", "usage": dict(self.usage)},
        ):
            if on_event is not None:
                res = on_event(evt)
                if inspect.isawaitable(res):
                    await res
        return {"text": "收到", "usage": dict(self.usage)}


def test_agent_console_persists_turn_cost(temp_db, configured_ai, monkeypatch):
    captured: dict = {}

    def fake_build_tools(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "session_of", lambda request: _FakeSession())
    monkeypatch.setattr(main, "_console_prompt", lambda session: "系统提示")
    monkeypatch.setattr(main, "build_tools", fake_build_tools)
    monkeypatch.setattr(main, "Agent", _FakeAgent)
    main.SHUTDOWN["requested"] = False

    client = TestClient(main.app)
    res = client.post("/api/agent/chat", json={"text": "你好"})
    assert res.status_code == 200
    assert '"cost": 0.00278' in res.text

    convs = db.list_agent_conversations("acc_cost")
    assert len(convs) == 1
    assert captured["billing_tools"] is True
    assert captured["conversation_id"] == convs[0]["id"]
    turns = db.list_agent_turns(convs[0]["id"])
    assistant = next(t for t in turns if t["role"] == "assistant")
    assert assistant["meta"]["cost"] == pytest.approx(0.00278)
    assert assistant["meta"]["tokens"] == {"input": 1000, "output": 500}
    assert assistant["meta"]["stopped"] is False
    assert assistant["meta"]["error"] == ""


class _TruncatedAgent:
    """模拟「思考把额度吃光、一个字正文都没有」的那一轮。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, messages, on_event=None, **kwargs):
        for evt in (
            {"type": "reasoning", "text": "想了很久"},
            {"type": "note", "text": "被截断了", "usage": {"measured": False}},
            {"type": "done", "text": "", "usage": {"measured": False}, "truncated": True},
        ):
            if on_event is not None:
                res = on_event(evt)
                if inspect.isawaitable(res):
                    await res
        return {"text": "", "usage": {"measured": False}, "truncated": True}


def test_agent_console_persists_truncation(temp_db, configured_ai, monkeypatch):
    monkeypatch.setattr(main, "session_of", lambda request: _FakeSession())
    monkeypatch.setattr(main, "_console_prompt", lambda session: "系统提示")
    monkeypatch.setattr(main, "build_tools", lambda *a, **k: [])
    monkeypatch.setattr(main, "Agent", _TruncatedAgent)
    main.SHUTDOWN["requested"] = False

    client = TestClient(main.app)
    res = client.post("/api/agent/chat", json={"text": "继续回复鲸鲸"})
    assert res.status_code == 200
    assert '"type": "note"' in res.text, "被截断要让前端收到提示"
    assert '"truncated": true' in res.text

    convs = db.list_agent_conversations("acc_cost")
    turns = db.list_agent_turns(convs[0]["id"])
    assistant = next(t for t in turns if t["role"] == "assistant")
    assert assistant["meta"]["truncated"] is True
    assert assistant["meta"]["note"] == "被截断了"


class _StubDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self) -> None:
        self.sent_text: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent_text.append((chat_id, text))


def _auto_cfg() -> dict:
    return {
        "auto": {"history_context": 12},
        "ai": {
            "temperature": 0.7,
            "max_tokens": 800,
            "vision_enabled": False,
            "memory_enabled": False,
            "emoji_with_reply": False,
            "system_prompt": "人设",
        },
    }


def test_auto_reply_persists_cost_meta(temp_db):
    session = AccountSession({"id": "acc_auto", "name": "测试号"}, _auto_cfg, FakeLLM([]))
    session.driver = _StubDriver()
    uid = db.upsert_chat("acc_auto", "c1", "会话一", False, "在吗", 1000.0)
    session.llm = FakeLLM(
        [
            [
                {"type": "text", "text": "在的呀"},
                _usage(0.00278, prompt=1000, completion=500),
                {"type": "finish", "reason": "stop"},
            ]
        ]
    )
    msg = Message(
        chat_id="c1",
        chat_name="会话一",
        sender_name="对方",
        content="在吗",
        msg_type="text",
        msg_id="m1",
        ts=1001.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    texts = [
        row
        for row in db.list_messages(uid, limit=50)
        if row["is_self"] and row["msg_type"] == "text" and row["source"] == "agent"
    ]
    assert texts and texts[-1]["meta"]["cost"] == pytest.approx(0.00278)
    assert texts[-1]["meta"]["tokens"] == {"input": 1000, "output": 500}
