"""回合钩子：自动回复成功后按 user 入队；控制台回合结束后按 console 入队。"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient

from app import config, db, main
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


class FakeLLM:
    configured = True

    def __init__(self, text: str = "我在呢") -> None:
        self.text = text

    async def stream(self, messages, tools=None, **kwargs):
        yield {"type": "text", "text": self.text}
        yield {"type": "finish", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class FakeDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True


def _patch_auto_config(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {
        "ai": {"memory_enabled": False, "vision_enabled": False, "emoji_with_reply": False},
        "auto": {"history_context": 4, "split_reply": False, "max_reply_chars": 300},
        "send": {"enabled": False},
        "emoji": {},
    })
    monkeypatch.setattr(config, "persona_of", lambda acc: ("人设", "大肥鱼"))


def _auto_session(llm=None):
    session = AccountSession(
        {"id": "acc_a", "name": "号", "persona": {}},
        lambda: config.load(),
        llm or FakeLLM(),
    )
    session.driver = FakeDriver()
    session.account["enabled"] = True
    return session


def _auto_message():
    db.upsert_chat("acc_a", "c1", "号主", False, "在吗")
    return Message(chat_id="c1", chat_name="号主", msg_id="m1", msg_type="text",
                   content="在吗", is_group=False, is_self=False, sender_name="号主")


def test_auto_reply_queues_mood_after_send(temp_db, monkeypatch):
    from app.mood import service as mood_service

    calls = []
    monkeypatch.setattr(
        mood_service, "observe",
        lambda *a, **kw: calls.append((a, kw)) or "queued",
    )
    _patch_auto_config(monkeypatch)
    session = _auto_session()
    msg = _auto_message()
    asyncio.run(session._auto_reply_run(db.uid_of("acc_a", "c1"), msg))
    assert calls
    args, kw = calls[0]
    assert args[0] == "acc_a"
    assert args[1] == "acc_a:c1"
    assert args[2] == "在吗"
    assert args[3] == "我在呢"
    assert kw.get("source") == "user"
    assert kw["llm"] is session.llm


def test_auto_reply_skip_does_not_queue_mood(temp_db, monkeypatch):
    from app.mood import service as mood_service

    calls = []
    monkeypatch.setattr(
        mood_service, "observe",
        lambda *a, **kw: calls.append((a, kw)) or "queued",
    )
    _patch_auto_config(monkeypatch)
    session = _auto_session(FakeLLM(" [SKIP] "))
    msg = _auto_message()
    asyncio.run(session._auto_reply_run(db.uid_of("acc_a", "c1"), msg))
    assert calls == []
    assert session.driver.sent == []


class _FakeSession:
    account_id = "acc_a"
    account = {"id": "acc_a", "name": "测试号"}
    name = "测试号"
    nickname = "小鲸"
    driver = None


class _FakeAgent:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, messages, on_event=None, **kwargs):
        for evt in ({"type": "delta", "text": "收到"}, {"type": "done", "text": "收到"}):
            if on_event is not None:
                res = on_event(evt)
                if inspect.isawaitable(res):
                    await res
        return {"text": "收到", "usage": {}}


class _ErrorAgent:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, messages, on_event=None, **kwargs):
        raise RuntimeError("模型炸了")


def _console_client(monkeypatch, agent_cls):
    monkeypatch.setattr(main, "session_of", lambda request: _FakeSession())
    monkeypatch.setattr(main, "_console_prompt", lambda session: "系统提示")
    monkeypatch.setattr(main, "build_tools", lambda *a, **kw: [])
    monkeypatch.setattr(main, "Agent", agent_cls)
    monkeypatch.setattr(config, "load", lambda: {
        "server": {"access_token": ""},
        "ai": {"memory_enabled": False, "vision_enabled": False, "api_key": "sk-test",
               "base_url": "https://api.deepseek.com", "model": "m"},
        "auto": {},
        "mood": {"enabled": True, "accounts": {}},
    })
    main.SHUTDOWN["requested"] = False
    return TestClient(main.app)


def test_console_turn_queues_mood(temp_db, configured_ai, monkeypatch):
    from app.mood import service as mood_service

    calls = []
    monkeypatch.setattr(
        mood_service, "observe",
        lambda *a, **kw: calls.append((a, kw)) or "queued",
    )
    client = _console_client(monkeypatch, _FakeAgent)
    res = client.post("/api/agent/chat", json={"text": "你好"})
    assert res.status_code == 200
    assert calls
    args, kw = calls[0]
    assert args[0] == "acc_a"
    assert args[1] == ""
    assert args[2] == "你好"
    assert args[3] == "收到"
    assert kw.get("source") == "console"
    assert kw["llm"] is main.llm


def test_console_error_turn_does_not_queue_mood(temp_db, configured_ai, monkeypatch):
    from app.mood import service as mood_service

    calls = []
    monkeypatch.setattr(
        mood_service, "observe",
        lambda *a, **kw: calls.append((a, kw)) or "queued",
    )
    client = _console_client(monkeypatch, _ErrorAgent)
    res = client.post("/api/agent/chat", json={"text": "你好"})
    assert res.status_code == 200
    assert calls == []


def test_observe_swallows_settings_failure(monkeypatch, caplog):
    from app.mood import service as mood_service

    def boom(account_id):
        raise RuntimeError("配置坏了")

    monkeypatch.setattr(mood_service.settings, "settings", boom)
    assert mood_service.observe("acc_a", "u1", "你好", "在呢") == "skipped"
    assert "心情判断入队失败" in caplog.text


def test_observe_without_event_loop_is_plain_skip(monkeypatch, caplog):
    from app.mood import service as mood_service

    monkeypatch.setattr(mood_service.settings, "settings",
                        lambda account_id: {"auto_judge": True})
    monkeypatch.setattr(mood_service.settings, "enabled", lambda account_id=None: True)
    monkeypatch.setattr(mood_service.settings, "resolve_slot",
                        lambda account_id, chat_uid="": "default")
    assert mood_service.observe("acc_a", "u1", "你好", "在呢") == "skipped"
    assert "心情判断入队失败" not in caplog.text
