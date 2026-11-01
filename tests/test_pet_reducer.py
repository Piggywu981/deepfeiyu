from __future__ import annotations

from app.pet import reducer as rd


class Clock:
    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now


def _red():
    clock = Clock()
    return rd.PetReducer("acc_a", now_ms=clock), clock


def _auto(event_type: str, dtype: str, **extra):
    return {
        "type": event_type,
        "data": {
            "type": dtype, "scope": "auto",
            "account_id": "acc_a", "chat_uid": "u1", **extra,
        },
    }


def test_turn_start_becomes_thinking():
    red, _ = _red()
    assert red.on_event(_auto("agent_turn", "start")) == [{"kind": "state", "state": "THINKING"}]
    assert red.current()["state"] == "THINKING"


def test_tool_call_becomes_working_with_activity():
    red, _ = _red()
    red.on_event(_auto("agent_turn", "start"))
    out = red.on_event(_auto("agent_tool", "tool_start", name="web_search"))
    assert out == [{"kind": "state", "state": "WORKING", "activity": "searching"}]


def test_turn_done_pulses_success_and_resumes_idle():
    red, _ = _red()
    red.on_event(_auto("agent_turn", "start"))
    out = red.on_event(_auto("agent_tool", "done"))
    assert out[0]["kind"] == "pulse" and out[0]["state"] == "SUCCESS"
    assert out[0]["resumeState"] == "IDLE"
    assert out[0]["ttlMs"] <= rd.PULSE_TTL_MAX


def test_error_is_latched_until_next_turn():
    red, _ = _red()
    red.on_event({"type": "log", "data": {"level": "error", "title": "发送失败：timeout"}})
    assert red.current()["state"] == "ERROR"
    red.on_event(_auto("agent_tool", "done"))          # 同轮收尾不得把 ERROR 洗成 SUCCESS
    assert red.current()["state"] == "ERROR"
    red.on_event(_auto("agent_turn", "start"))         # 下一轮开始才解开
    assert red.current()["state"] == "THINKING"


def test_idle_fallback_after_quiet_window():
    red, clock = _red()
    red.on_event(_auto("agent_turn", "start"))
    clock.now = rd.IDLE_FALLBACK_MS - 1
    assert red.tick() == []
    clock.now = rd.IDLE_FALLBACK_MS + 1
    assert red.tick() == [{"kind": "state", "state": "IDLE"}]


def test_duplicate_state_is_not_resent():
    red, _ = _red()
    assert red.on_event(_auto("agent_turn", "start"))
    assert red.on_event(_auto("agent_turn", "start")) == []


def test_events_from_other_accounts_ignored():
    red, _ = _red()
    assert red.on_event({"type": "agent_turn",
                         "data": {"type": "start", "scope": "auto", "account_id": "acc_b"}}) == []


def test_offline_status_becomes_disconnected_and_back():
    # 字段名取自 AccountSession.status() 的真实键 connected，不是 online
    red, _ = _red()
    out = red.on_event({"type": "status", "data": {"account_id": "acc_a", "connected": False}})
    assert out == [{"kind": "state", "state": "DISCONNECTED"}]
    assert red.on_event({"type": "status", "data": {"account_id": "acc_a", "connected": True}}) == [
        {"kind": "state", "state": "IDLE"}
    ]


def test_waiting_is_never_emitted():
    red, _ = _red()
    events = (
        _auto("agent_turn", "start"),
        {"type": "log", "data": {"level": "warn", "title": "x"}},
        _auto("agent_tool", "tool_end", name="send_emoji", ok=True),
        _auto("agent_tool", "done"),
        {"type": "status", "data": {"account_id": "acc_a", "connected": False}},
    )
    for event in events:
        assert all(m.get("state") != "WAITING" for m in red.on_event(event))


def test_reducer_remembers_last_chat_uid():
    red, _ = _red()
    red.on_event(_auto("agent_turn", "start"))
    assert red.last_chat_uid == "u1"
    red.on_event({"type": "log", "data": {"level": "error", "title": "x"}})   # 无 chat_uid 不覆盖
    assert red.last_chat_uid == "u1"
    red.on_event({"type": "status", "data": {"account_id": "acc_a", "connected": False}})
    assert red.last_chat_uid == "u1"


def test_activity_for_tool_by_substring():
    assert rd.activity_for_tool("web_fetch") == "searching"
    assert rd.activity_for_tool("run_python") == "commanding"
    assert rd.activity_for_tool("whatever") == "editing"
    # 语义提示优先于来源前缀；看不出语义的 MCP/插件工具才走 using-tool
    assert rd.activity_for_tool("mcp__bili__search_video") == "searching"
    assert rd.activity_for_tool("mcp__bili__get_hot") == "using-tool"
    assert rd.activity_for_tool("plugin__bili__patrol_now") == "using-tool"
