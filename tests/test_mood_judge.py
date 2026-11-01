"""心情判断：提示词、解析、串行队列、失败降级、账号隔离。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app import config, db
from app.mood import judge as judge_mod, prompt, service, store
from app.mood.constants import DIM_KEYS


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
    service.reset_runtime_for_tests()


def _cfg(enabled=True, **account):
    # 真实 config.account() 直接读根级 accounts；补空列表避免 KeyError。
    return {
        "accounts": [],
        "mood": {
            "enabled": enabled,
            "accounts": {"acc_a": account},
        },
    }


class FakeLLM:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.configured = True

    async def stream(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        payload = self.payloads.pop(0) if self.payloads else "{}"
        if isinstance(payload, Exception):
            raise payload
        yield {"type": "text", "text": payload}
        yield {"type": "finish", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class SlowFakeLLM(FakeLLM):
    """在流式调用里停留 delay 秒，并记录进入/离开时间，用于验证串行。"""

    def __init__(self, payloads, delay=0.01):
        super().__init__(payloads)
        self.delay = delay
        self.spans = []

    async def stream(self, messages, tools=None, **kwargs):
        start = time.perf_counter()
        await asyncio.sleep(self.delay)
        self.calls.append({"messages": messages, "kwargs": kwargs})
        self.spans.append((start, time.perf_counter()))
        payload = self.payloads.pop(0) if self.payloads else "{}"
        if isinstance(payload, Exception):
            raise payload
        yield {"type": "text", "text": payload}
        yield {"type": "finish", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _delta(mood=5, reason="号主夸我，我心里甜甜的", conf=88, **dims):
    base = {k: 0 for k in DIM_KEYS}
    base.update(dims)
    return json.dumps({"moodDelta": mood, "dims": base, "reason": reason, "confidence": conf},
                      ensure_ascii=False)


def _run_observe(*args, **kwargs):
    """仓库未装 pytest-asyncio，沿用 asyncio.run 驱动 observe + wait_idle。"""
    async def scenario():
        status = service.observe(*args, **kwargs)
        await service.wait_idle()
        return status

    return asyncio.run(scenario())


def test_prompt_contains_names_aliases_and_rules():
    sys_text = prompt.system("大肥鱼", "号主", "宝宝,亲爱的")
    assert "你是「大肥鱼」，是号主的伴侣" in sys_text
    assert "宝宝,亲爱的" in sys_text
    assert "moodDelta" in sys_text
    assert "代词" in sys_text
    assert "confidence" in sys_text
    assert "过去式" in sys_text


def test_mode_supplements():
    assert "定时任务" in prompt.mode_supplement("work", "号主")
    assert "主动去找" in prompt.mode_supplement("talk", "号主")
    assert prompt.mode_supplement("", "号主") == ""


def test_user_message_order_and_source_note():
    text = prompt.user_message("", "我没吃饭", "谁让你不吃饭", "号主", "console")
    assert "谁让你不吃饭" in text
    assert "号主这轮说" in text
    assert "控制台" in text
    work = prompt.user_message("work", "去巡逻", "刷了一圈", "号主", "bili-patrol")
    assert "定时任务" in work
    assert "去巡逻" in work


def test_judge_returns_parsed_delta():
    llm = FakeLLM([_delta()])
    d = asyncio.run(judge_mod.judge(
        llm, {"self": "大肥鱼", "owner": "号主", "aliases": "", "judge_model": ""},
        "号主夸我", "嘿嘿", ""))
    assert d["moodDelta"] == 5
    assert d["reason"] == "号主夸我，我心里甜甜的"
    assert llm.calls[0]["kwargs"]["temperature"] == 0.2
    assert llm.calls[0]["kwargs"]["max_tokens"] == 1000


def test_judge_invalid_json_returns_none():
    llm = FakeLLM(["今天心情不错"])
    d = asyncio.run(judge_mod.judge(
        llm, {"self": "A", "owner": "B", "aliases": "", "judge_model": ""},
        "x", "y", ""))
    assert d is None


def test_judge_model_override():
    llm = FakeLLM([_delta()])
    asyncio.run(judge_mod.judge(
        llm, {"self": "A", "owner": "B", "aliases": "", "judge_model": "mimo-v2.6-flash"},
        "x", "y", ""))
    assert llm.calls[0]["kwargs"]["model"] == "mimo-v2.6-flash"


def test_observe_updates_state_and_event(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta(mood=9, heartache=6)])
    status = _run_observe("acc_a", "u1", "我没吃饭", "怎么又不吃", llm=llm)
    assert status == "queued"
    state = store.load_state("acc_a", "default")
    assert state["mood"] == 73.0          # round(9/3)=3
    assert state["dims"]["heartache"] == 6.0
    assert state["minor"] == 0           # heartache 6 >= sig_dim 5 → 显著
    events = store.list_events("acc_a", "default")
    assert len(events) == 1
    assert events[0]["source"] == "user"
    assert events[0]["injected"] == 0


def test_observe_minor_change_is_recorded_but_flagged(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta(mood=2, reason="随口应了一声", conf=90)])
    _run_observe("acc_a", "u1", "哦", "嗯", llm=llm)
    events = store.list_events("acc_a", "default")
    assert events and events[0]["minor"] == 1


def test_observe_no_change_writes_nothing(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta(mood=0, reason="", conf=0)])
    _run_observe("acc_a", "u1", "在吗", "在", llm=llm)
    assert store.list_events("acc_a", "default") == []
    assert store.load_state("acc_a", "default")["updated_at"] == 0.0


def test_observe_disabled_skips_model(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled=False))
    llm = FakeLLM([_delta()])
    assert _run_observe("acc_a", "u1", "嗨", "嗯", llm=llm) == "skipped"
    assert llm.calls == []


def test_observe_queue_cap_three(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta() for _ in range(4)])

    async def scenario():
        results = [service.observe("acc_a", "u1", f"第{i}句", "嗯", llm=llm) for i in range(4)]
        await service.wait_idle()
        return results

    results = asyncio.run(scenario())
    assert results[:3] == ["queued", "queued", "queued"]
    assert results[3] == "dropped"
    assert len(llm.calls) == 3


def test_observe_model_failure_does_not_change_state(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([RuntimeError("boom")])
    _run_observe("acc_a", "u1", "嗨", "嗯", llm=llm)
    assert store.list_events("acc_a", "default") == []
    assert store.load_state("acc_a", "default")["mood"] == 70.0


def test_observe_chat_mode_binds_slot(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg(mode="chat"))
    store.create_slot("acc_a", "s1")
    store.bind_chat("acc_a", "s1", "u1")
    llm = FakeLLM([_delta()])
    _run_observe("acc_a", "u1", "嗨", "嗯", llm=llm)
    assert store.list_events("acc_a", "s1")
    assert store.list_events("acc_a", "default") == []


def test_observe_skips_non_conversation_sources(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta() for _ in range(8)])
    for source in ("system", "runtime-context", "time-context", "system-reminder",
                   "time-sampled", "tool", "subagent", "schedule"):
        assert _run_observe("acc_a", "u1", "嗨", "嗯", source=source, llm=llm) == "skipped"
    assert llm.calls == []
    assert store.list_events("acc_a", "default") == []


def test_observe_unknown_source_is_judged_as_user(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta()])
    assert _run_observe("acc_a", "u1", "嗨", "嗯", source="mystery", llm=llm) == "queued"
    events = store.list_events("acc_a", "default")
    assert len(events) == 1
    assert events[0]["source"] == "mystery"


def test_save_state_with_event_writes_both_and_returns_id(temp_db):
    now = time.time()
    state = store.load_state("acc_a", "default")
    state["mood"] = 66.0
    state["updated_at"] = now
    eid = store.save_state_with_event(
        "acc_a", "default", state,
        event={
            "at": now, "mood": 66.0, "delta": -3, "dims": state["dims"],
            "dims_delta": {"sad": 2}, "reason": "号主没吃饭，我有点担心",
            "confidence": 80, "kind": "judge", "source": "user", "minor": 0,
        },
    )
    assert eid > 0
    assert store.load_state("acc_a", "default")["mood"] == 66.0
    events = store.list_events("acc_a", "default")
    assert len(events) == 1
    assert events[0]["id"] == eid
    assert events[0]["dims_delta"] == {"sad": 2}


def test_observe_atomic_write_failure_releases_queue(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    real = store.save_state_with_event

    def boom(*args, **kwargs):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(store, "save_state_with_event", boom)
    llm = FakeLLM([_delta(mood=9, heartache=6)])
    assert _run_observe("acc_a", "u1", "我没吃饭", "怎么又不吃", llm=llm) == "queued"
    assert store.list_events("acc_a", "default") == []
    assert store.load_state("acc_a", "default")["mood"] == 70.0
    assert service._pending.get(("acc_a", "default"), 0) == 0

    monkeypatch.setattr(store, "save_state_with_event", real)
    llm2 = FakeLLM([_delta(mood=5)])
    assert _run_observe("acc_a", "u1", "夸夸你", "嘿嘿", llm=llm2) == "queued"
    assert len(store.list_events("acc_a", "default")) == 1


def test_observe_same_slot_is_serialized(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = SlowFakeLLM([_delta(mood=5), _delta(mood=3)], delay=0.01)

    async def scenario():
        assert service.observe("acc_a", "u1", "第一句", "嗯", llm=llm) == "queued"
        assert service.observe("acc_a", "u1", "第二句", "嗯", llm=llm) == "queued"
        await service.wait_idle()

    asyncio.run(scenario())
    assert len(llm.spans) == 2
    (start1, end1), (start2, end2) = llm.spans
    assert end1 <= start2 or end2 <= start1


def test_observe_accounts_are_isolated(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    llm = FakeLLM([_delta(mood=9), _delta(mood=-9)])

    async def scenario():
        assert service.observe("acc_a", "u1", "夸我", "嘿嘿", llm=llm) == "queued"
        assert service.observe("acc_b", "u2", "骂我", "呜呜", llm=llm) == "queued"
        await service.wait_idle()

    asyncio.run(scenario())
    events_a = store.list_events("acc_a", "default")
    events_b = store.list_events("acc_b", "default")
    assert len(events_a) == 1 and len(events_b) == 1
    assert all(e["account_id"] == "acc_a" for e in events_a)
    assert all(e["account_id"] == "acc_b" for e in events_b)
    assert store.load_state("acc_a", "default")["mood"] == 73.0
    assert store.load_state("acc_b", "default")["mood"] == 67.0
