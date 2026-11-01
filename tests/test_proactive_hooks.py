"""主动搭话：专用提示词、受限工具的一轮生成与表情透传。"""
from __future__ import annotations

import asyncio

import pytest

from app.ai.agent import Tool
from app.wechat import manager
from app.wechat.manager import AccountSession


class FakeLLM:
    """只需要 configured；真实调用一律由假 Agent 顶替。"""

    def __init__(self, configured: bool = True) -> None:
        self.configured = configured


def _cfg(memory_enabled: bool = True) -> dict:
    return {
        "ai": {"memory_enabled": memory_enabled, "memory_max": 8, "persona_name": "小鲸"},
        "memory": {"max_items": 6},
        "auto": {},
        "send": {"enabled": False},
    }


def _session(llm=None) -> AccountSession:
    return AccountSession(
        {"id": "acc_a", "name": "测试号", "nickname": "小鲸", "persona": {}},
        _cfg,
        llm or FakeLLM(),
    )


def _chat(is_group: bool = False) -> dict:
    return {
        "uid": "acc_a:g1" if is_group else "acc_a:c1",
        "chat_id": "g1" if is_group else "c1",
        "name": "摸鱼群" if is_group else "阿明",
        "is_group": is_group,
    }


def _row(content: str, *, is_self: bool = False, is_group: bool = False,
         sender_name: str = "阿明", sender_id: str = "wxid_ming01",
         msg_type: str = "text", revoked: bool = False) -> dict:
    return {
        "content": content,
        "msg_type": msg_type,
        "is_group": is_group,
        "is_self": is_self,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "is_revoked": revoked,
        "meta": {},
    }


@pytest.fixture(autouse=True)
def _quiet_blocks(monkeypatch):
    """默认把所有可选注入块关成空，测试按需单独打开，不依赖真实配置/数据。"""
    monkeypatch.setattr(manager.config, "persona_of", lambda acc: ("你是小鲸，一条爱聊天的鱼。", "小鲸"))
    monkeypatch.setattr(manager.chuanzi, "enabled", lambda cfg=None: False)
    monkeypatch.setattr(manager.growth_prompt, "block", lambda *a, **kw: "")
    monkeypatch.setattr(manager.mood_service, "inject_block", lambda *a, **kw: "")
    monkeypatch.setattr(manager.moe_wiki, "self_block", lambda *a, **kw: "")
    monkeypatch.setattr(manager.memory, "inject_block", lambda *a, **kw: "")

    async def _retrieve(*a, **kw):
        return ""

    monkeypatch.setattr(manager.memory, "retrieve_context", _retrieve)


# —— 提示词 ——

def test_proactive_prompt_has_self_start_framing():
    session = _session()
    messages = session._build_proactive_prompt(
        _cfg(), _chat(), [], "三天没聊了", max_chars=120,
    )
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "你是小鲸，一条爱聊天的鱼。" in system
    assert "测试号" in system and "小鲸" in system
    assert "微信私聊" in system and "阿明" in system
    assert "三天没聊了" in system
    assert "120" in system
    assert "[SKIP]" in system
    assert "主动开口" in system
    assert "这句动机只是提醒" in system
    # 不能出现回复型指令（“不是对方发来消息”是主动开口的说明，不算回复模板）
    assert "对方发来一条" not in system
    assert "回复要求" not in system
    assert "群聊未 @" not in system
    # 最后一条是主动开口的 user 提示，不是对方消息
    last = messages[-1]
    assert last["role"] == "user"
    assert "现在没有新消息" in last["content"]
    assert "「阿明」" in last["content"]


def test_proactive_prompt_private_history_roles_and_revoke():
    session = _session()
    history = [
        _row("在吗"),
        _row("system 内部事件", msg_type="system"),
        _row("在的", is_self=True, sender_name="我", sender_id="self"),
        _row("刚才那条当我没说", revoked=True),
    ]
    messages = session._build_proactive_prompt(
        _cfg(), _chat(), history, "想起来问问",
    )
    # system + 三条非 system 历史（撤回那条也算）+ 主动提示
    assert len(messages) == 5
    assert messages[1] == {"role": "user", "content": "在吗"}
    assert messages[2] == {"role": "assistant", "content": "在的"}
    assert messages[3]["role"] == "user"
    assert messages[3]["content"].startswith("[阿明撤回了这条消息，原文如下] ")
    assert "现在没有新消息" in messages[4]["content"]


def test_proactive_prompt_group_prefix_and_restraint():
    session = _session()
    history = [
        _row("有人打游戏吗", is_group=True, sender_name="阿强", sender_id="wxid_qiang01"),
        _row("我在", is_self=True, is_group=True, sender_name="我", sender_id="self"),
        _row("我也来", is_group=True, sender_name="阿强", sender_id="wxid_qiang01",
             revoked=True),
    ]
    messages = session._build_proactive_prompt(
        _cfg(), _chat(is_group=True), history, "群里冷清了",
    )
    system = messages[0]["content"]
    assert "微信群聊" in system
    assert "群里开口更克制" in system
    assert "@ 所有人" in system
    assert messages[1]["content"].startswith("阿强(wxid_qiang01)：")
    assert messages[2] == {"role": "assistant", "content": "我在"}
    assert messages[3]["content"].startswith(
        "[阿强(wxid_qiang01)撤回了这条消息，原文如下] "
    )


def test_proactive_prompt_private_has_no_group_restraint():
    session = _session()
    system = session._build_proactive_prompt(
        _cfg(), _chat(), [], "三天没聊了",
    )[0]["content"]
    assert "群里开口更克制" not in system
    assert "@ 所有人" not in system


def test_proactive_prompt_uses_given_memory_block(monkeypatch):
    called = []
    monkeypatch.setattr(
        manager.memory, "inject_block",
        lambda *a, **kw: called.append((a, kw)) or "不该被调用",
    )
    session = _session()
    system = session._build_proactive_prompt(
        _cfg(), _chat(), [], "想起她说过的话", memory_block="【记忆】她喜欢橘子",
    )[0]["content"]
    assert "【记忆】她喜欢橘子" in system
    assert "不该被调用" not in system
    assert called == []


def test_proactive_prompt_memory_fallback(monkeypatch):
    called = []

    def fake_inject(account_id, chat_uid="", limit=12, query=""):
        called.append((account_id, chat_uid, limit, query))
        return "【长期记忆】她上次说想去海边"

    monkeypatch.setattr(manager.memory, "inject_block", fake_inject)
    session = _session()
    system = session._build_proactive_prompt(
        _cfg(), _chat(), [], "想起她说过的话", memory_block=None,
    )[0]["content"]
    assert "【长期记忆】她上次说想去海边" in system
    assert called == [("acc_a", "acc_a:c1", 6, "想起她说过的话")]


def test_proactive_prompt_appends_growth_mood_wiki(monkeypatch):
    monkeypatch.setattr(manager.growth_prompt, "block", lambda *a, **kw: "【成长】最近在学吉他")
    monkeypatch.setattr(manager.mood_service, "inject_block", lambda *a, **kw: "【心情】有点困")
    monkeypatch.setattr(manager.moe_wiki, "self_block", lambda *a, **kw: "【图鉴】大肥鱼")
    session = _session()
    system = session._build_proactive_prompt(
        _cfg(), _chat(), [], "随便聊聊",
    )[0]["content"]
    assert system.index("【成长】") < system.index("【心情】") < system.index("【图鉴】")


def test_proactive_prompt_preview_skips_mood_injection(monkeypatch):
    """试运行不能消耗「一次变化只说一次」的心情注入额度。"""
    calls = []
    monkeypatch.setattr(
        manager.mood_service, "inject_block",
        lambda *a, **kw: calls.append(a) or "【心情】有点困",
    )
    session = _session()
    real = session._build_proactive_prompt(_cfg(), _chat(), [], "随便聊聊")[0]["content"]
    preview = session._build_proactive_prompt(
        _cfg(), _chat(), [], "随便聊聊", preview=True
    )[0]["content"]
    assert "【心情】" in real
    assert "【心情】" not in preview
    assert len(calls) == 1


def test_proactive_prompt_optional_blocks_failure_skipped(monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("可选块挂了")

    monkeypatch.setattr(manager.growth_prompt, "block", broken)
    monkeypatch.setattr(manager.moe_wiki, "self_block", broken)
    monkeypatch.setattr(manager.memory, "inject_block", broken)
    session = _session()
    messages = session._build_proactive_prompt(
        _cfg(), _chat(), [], "随便聊聊", memory_block=None,
    )
    system = messages[0]["content"]
    assert "你是小鲸，一条爱聊天的鱼。" in system
    assert "【这次是你主动开口】" in system
    assert messages[-1]["role"] == "user"
    assert "现在没有新消息" in messages[-1]["content"]


def test_proactive_prompt_filters_system_before_last_12():
    session = _session()
    # 20 条对话 + 4 条系统事件 = 24 条；过滤 system 后应只映射最后 12 条对话
    history = []
    for i in range(20):
        history.append(_row(f"对话{i}"))
        if i in (0, 5, 10, 15):
            history.append(_row(f"系统事件{i}", msg_type="system"))
    assert len(history) == 24
    messages = session._build_proactive_prompt(
        _cfg(), _chat(), history, "随便聊聊",
    )
    contents = [m["content"] for m in messages[1:-1]]
    assert contents == [f"对话{i}" for i in range(8, 20)]
    assert len(messages) == 1 + 12 + 1


def test_proactive_prompt_group_pat_has_no_sender_prefix():
    session = _session()
    pat = _row(
        "「阿强」拍了拍「我」", is_group=True, msg_type="pat",
        sender_name="阿强", sender_id="wxid_qiang01",
    )
    messages = session._build_proactive_prompt(
        _cfg(), _chat(is_group=True), [pat], "群里冷清了",
    )
    assert messages[1] == {"role": "user", "content": "「阿强」拍了拍「我」"}


def test_proactive_prompt_chuanzi_override(monkeypatch):
    monkeypatch.setattr(manager.chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(
        manager.chuanzi, "persona_override",
        lambda original, name="": f"【串子】{name}",
    )
    session = _session()
    system = session._build_proactive_prompt(
        _cfg(), _chat(), [], "随便聊聊",
    )[0]["content"]
    assert "【串子】小鲸" in system
    assert "你是小鲸，一条爱聊天的鱼。" not in system


# —— 一轮生成 ——

ALLOWED_TOOLS = {
    "search_emojis",
    "send_wechat_emoji",
    "recall_memory",
    "get_current_time",
}
# get_chat_history 也会 resolve 会话、可能写会话元数据；主动搭话的历史已在提示词里，
# 不给工具，避免任何跨会话写入路径。
EXTRA_TOOLS = [
    "send_wechat_message",
    "fetch_page",
    "remember",
    "list_wechat_chats",
    "get_chat_history",
]


def _install_agent(monkeypatch, result=None, exc: BaseException | None = None):
    captured: dict = {}

    class FakeAgent:
        def __init__(self, llm, tools, max_rounds=5):
            captured["llm"] = llm
            captured["tools"] = tools
            captured["max_rounds"] = max_rounds

        async def run(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            if exc is not None:
                raise exc
            return result if result is not None else {"text": "", "usage": {}}

    monkeypatch.setattr(manager, "Agent", FakeAgent)
    return captured


async def _ok_handler(args):
    return {"ok": True}


def _install_tools(monkeypatch, deferred_item: dict | None = None, emoji_handler=None):
    captured: dict = {}

    def fake_build_tools(session, **kwargs):
        captured["session"] = session
        captured["kwargs"] = kwargs
        if deferred_item is not None:
            kwargs["defer_emoji"].append(deferred_item)
        names = list(ALLOWED_TOOLS) + EXTRA_TOOLS
        # 用真实 Tool：run_proactive_turn 会给 send_wechat_emoji 包一层会话锁
        return [
            Tool(
                name=n,
                description="",
                parameters={},
                handler=(
                    (emoji_handler or _ok_handler)
                    if n == "send_wechat_emoji"
                    else _ok_handler
                ),
            )
            for n in names
        ]

    monkeypatch.setattr(manager, "build_tools", fake_build_tools)
    return captured


def _install_db(monkeypatch, chat=None, history=None):
    usage_calls: list[tuple] = []
    monkeypatch.setattr(manager.db, "get_chat", lambda uid: chat)
    monkeypatch.setattr(manager.db, "list_messages", lambda uid, limit=50: list(history or []))
    monkeypatch.setattr(manager.db, "add_usage", lambda *a: usage_calls.append(a))
    return usage_calls


EMOJI_ITEM = {"uid": "acc_a:c1", "emoji": {"md5": "m1", "url": "http://x/1.gif"}, "to": "阿明"}


def test_run_proactive_turn_normal(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    tools_cap = _install_tools(monkeypatch, EMOJI_ITEM)
    agent_cap = _install_agent(
        monkeypatch,
        {"text": "“今天路过那家店，想起你说过想吃”", "usage": {"prompt_tokens": 30, "completion_tokens": 12}},
    )
    session = _session()
    result = asyncio.run(session.run_proactive_turn("acc_a:c1", "三天没聊了", max_chars=50))
    assert result["ok"] is True
    assert result["skip"] is False
    assert result["error"] == ""
    assert result["text"] == "今天路过那家店，想起你说过想吃"
    assert result["usage"] == {"prompt_tokens": 30, "completion_tokens": 12}
    assert result["emoji"] == [EMOJI_ITEM]
    # 只有允许的工具进入这一轮
    names = {t.name for t in agent_cap["tools"]}
    assert names == ALLOWED_TOOLS
    assert agent_cap["max_rounds"] == 2
    assert agent_cap["kwargs"] == {"temperature": 0.85, "max_tokens": 300, "effort": "off"}
    assert tools_cap["kwargs"]["allow_send"] is False
    assert tools_cap["kwargs"]["allow_web"] is False
    assert tools_cap["kwargs"]["chat_uid"] == "acc_a:c1"
    assert tools_cap["kwargs"]["emoji_tools"] is True
    assert tools_cap["kwargs"]["tool_context"] == "proactive"
    assert isinstance(tools_cap["kwargs"]["defer_emoji"], list)
    assert agent_cap["messages"][0]["role"] == "system"
    assert "现在没有新消息" in agent_cap["messages"][-1]["content"]


def test_run_proactive_turn_usage_persisted(monkeypatch):
    usage_calls = _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(
        monkeypatch,
        {"text": "在忙吗", "usage": {"prompt_tokens": 100, "completion_tokens": 7}},
    )
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert usage_calls == [("acc_a", 100, 7)]


def test_run_proactive_turn_usage_missing_tokens_defaults_zero(monkeypatch):
    usage_calls = _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": "在忙吗", "usage": {"total_tokens": 9}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert usage_calls == [("acc_a", 0, 0)]


def test_run_proactive_turn_skip_keyword(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch, EMOJI_ITEM)
    _install_agent(monkeypatch, {"text": "跳过", "usage": {}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert result["skip"] is True
    assert result["text"] == ""
    assert result["emoji"] == []


def test_run_proactive_turn_skip_drops_emoji(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch, EMOJI_ITEM)
    _install_agent(monkeypatch, {"text": " [SKIP] ", "usage": {}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert result["skip"] is True
    assert result["text"] == ""
    assert result["emoji"] == []


def test_run_proactive_turn_empty_text_is_skip(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": " \n ", "usage": {}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert result["skip"] is True
    assert result["text"] == ""


def test_run_proactive_turn_agent_error(monkeypatch):
    usage_calls = _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": "", "error": "模型超时", "usage": {}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is False
    assert result["error"] == "模型超时"
    assert usage_calls == []


def test_run_proactive_turn_agent_exception_is_swallowed(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, exc=RuntimeError("网络炸了"))
    session = _session()
    result = asyncio.run(session.run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is False
    assert result["error"] == "网络炸了"
    assert result["emoji"] == []


def test_run_proactive_turn_cancelled_is_reraised(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, exc=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))


def test_run_proactive_turn_optional_blocks_failure_does_not_block(monkeypatch):
    _install_db(monkeypatch, _chat(), [_row("在吗")])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": "突然想起你", "usage": {}})

    def broken(*a, **kw):
        raise RuntimeError("可选块挂了")

    async def no_memory(*a, **kw):
        return None

    monkeypatch.setattr(manager.memory, "retrieve_context", no_memory)
    monkeypatch.setattr(manager.memory, "inject_block", broken)
    monkeypatch.setattr(manager.growth_prompt, "block", broken)
    monkeypatch.setattr(manager.moe_wiki, "self_block", broken)
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert result["skip"] is False
    assert result["text"] == "突然想起你"


def test_run_proactive_turn_emoji_tool_scoped_to_current_chat(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    calls: list[dict] = []

    async def fake_send(args):
        # 传到原 handler 的 to 必须已被改写成空，由 chat_uid 解析当前会话
        assert args["to"] == ""
        calls.append(dict(args))
        tools_cap["kwargs"]["defer_emoji"].append(EMOJI_ITEM)
        return {"ok": True, "to": "阿明", "emoji_id": args.get("emoji_id")}

    tools_cap = _install_tools(monkeypatch, emoji_handler=fake_send)
    captured: dict = {}

    class FakeAgent:
        def __init__(self, llm, tools, max_rounds=5):
            captured["tools"] = {t.name: t for t in tools}

        async def run(self, messages, **kwargs):
            emoji_tool = captured["tools"]["send_wechat_emoji"]
            captured["rejected"] = await emoji_tool.handler(
                {"to": "别人", "emoji_id": 1}
            )
            captured["deferred_after_rejected"] = list(
                tools_cap["kwargs"]["defer_emoji"]
            )
            captured["accepted"] = await emoji_tool.handler(
                {"to": "", "emoji_id": 1}
            )
            return {"text": "在忙吗", "usage": {}}

    monkeypatch.setattr(manager, "Agent", FakeAgent)
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert "error" in captured["rejected"]
    assert captured["deferred_after_rejected"] == []
    assert captured["accepted"]["ok"] is True
    assert calls == [{"to": "", "emoji_id": 1}]
    assert result["ok"] is True
    assert result["emoji"] == [EMOJI_ITEM]


def test_run_proactive_turn_unconfigured_llm(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    session = _session(FakeLLM(configured=False))
    result = asyncio.run(session.run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is False
    assert result["error"] == "模型未配置"


def test_run_proactive_turn_missing_chat(monkeypatch):
    _install_db(monkeypatch, None, [])
    _install_tools(monkeypatch)
    agent_cap = _install_agent(monkeypatch, {"text": "不该走到这", "usage": {}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:nope", "随便聊聊"))
    assert result["ok"] is False
    assert result["error"] == "会话不存在"
    assert agent_cap == {}  # 没进模型调用


def test_run_proactive_turn_truncates_long_text(monkeypatch):
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": "啊" * 300, "usage": {}})
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊", max_chars=20))
    assert result["ok"] is True
    assert result["text"].endswith("…")
    assert len(result["text"]) == 21


def test_run_proactive_turn_memory_query_from_recent_history(monkeypatch):
    history = [
        _row("一"),
        _row("二"),
        _row("三"),
        _row("四"),
        _row("五"),
    ]
    _install_db(monkeypatch, _chat(), history)
    _install_tools(monkeypatch)
    agent_cap = _install_agent(monkeypatch, {"text": "在干嘛", "usage": {}})
    calls = []

    async def fake_retrieve(account_id, uid, query, limit=None):
        calls.append((account_id, uid, query))
        return "【记忆】她周末去爬山"

    monkeypatch.setattr(manager.memory, "retrieve_context", fake_retrieve)
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    # 只取最近 4 条拼检索词，且检索到的记忆块真的进了系统提示
    assert calls == [("acc_a", "acc_a:c1", "二 三 四 五")]
    assert "【记忆】她周末去爬山" in agent_cap["messages"][0]["content"]


def test_run_proactive_turn_memory_failure_does_not_block(monkeypatch):
    _install_db(monkeypatch, _chat(), [_row("在吗")])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": "突然想起你", "usage": {}})

    async def broken(*a, **kw):
        raise RuntimeError("向量库挂了")

    monkeypatch.setattr(manager.memory, "retrieve_context", broken)
    result = asyncio.run(_session().run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert result["text"] == "突然想起你"


def test_run_proactive_turn_memory_disabled_skips_retrieve(monkeypatch):
    _install_db(monkeypatch, _chat(), [_row("在吗")])
    _install_tools(monkeypatch)
    _install_agent(monkeypatch, {"text": "在忙吗", "usage": {}})
    calls = []

    async def fake_retrieve(*a, **kw):
        calls.append((a, kw))
        return "不该检索"

    monkeypatch.setattr(manager.memory, "retrieve_context", fake_retrieve)
    session = AccountSession(
        {"id": "acc_a", "name": "测试号", "nickname": "小鲸", "persona": {}},
        lambda: _cfg(memory_enabled=False),
        FakeLLM(),
    )
    result = asyncio.run(session.run_proactive_turn("acc_a:c1", "随便聊聊"))
    assert result["ok"] is True
    assert calls == []


# —— 表情发送包装 ——

def _emoji_tool(handler) -> Tool:
    return Tool(
        name="send_wechat_emoji",
        description="",
        parameters={},
        handler=handler,
    )


def test_scope_proactive_emoji_tool_rejects_other_chat():
    calls = []

    async def handler(args):
        calls.append(args)
        return {"ok": True}

    tool = _emoji_tool(handler)
    scoped = manager._scope_proactive_emoji_tool(tool, "acc_a:c1")
    out = asyncio.run(scoped.handler({"to": "别人", "emoji_id": 1}))
    assert "error" in out
    assert calls == []
    # 原 Tool 不被就地改写
    assert tool.handler is handler


@pytest.mark.parametrize("to", ["", "acc_a:c1"])
def test_scope_proactive_emoji_tool_forces_current_chat(to):
    calls = []

    async def handler(args):
        calls.append(dict(args))
        return {"ok": True}

    scoped = manager._scope_proactive_emoji_tool(_emoji_tool(handler), "acc_a:c1")
    out = asyncio.run(scoped.handler({"to": to, "emoji_id": 7}))
    assert out == {"ok": True}
    assert calls == [{"to": "", "emoji_id": 7}]


@pytest.mark.parametrize("to", ["c1", "阿明", "阿明二"])
def test_scope_proactive_emoji_tool_rejects_chat_id_and_name(to):
    """同名/子串会话会被 resolve 抢先命中，chat_id 与名称都不认，否则立刻发给别人。"""
    calls = []

    async def handler(args):
        calls.append(dict(args))
        return {"ok": True}

    scoped = manager._scope_proactive_emoji_tool(_emoji_tool(handler), "acc_a:c1")
    out = asyncio.run(scoped.handler({"to": to, "emoji_id": 7}))
    assert "error" in out
    assert calls == []


def test_scope_proactive_memory_tool_requires_keyword():
    """空 query 会退回全账号最近记忆，可能带出别的会话；必须拦住。"""
    calls = []

    async def handler(args):
        calls.append(dict(args))
        return {"memories": []}

    tool = Tool(name="recall_memory", description="", parameters={}, handler=handler)
    scoped = manager._scope_proactive_memory_tool(tool)
    for bad in ({}, {"query": ""}, {"query": "   "}):
        out = asyncio.run(scoped.handler(bad))
        assert "error" in out
    assert "error" in asyncio.run(scoped.handler(None))
    assert calls == []
    assert tool.handler is handler


def test_scope_proactive_memory_tool_passes_keyword():
    calls = []

    async def handler(args):
        calls.append(dict(args))
        return {"memories": []}

    scoped = manager._scope_proactive_memory_tool(
        Tool(name="recall_memory", description="", parameters={}, handler=handler)
    )
    out = asyncio.run(scoped.handler({"query": "  生日  ", "limit": 3}))
    assert out == {"memories": []}
    assert calls == [{"query": "生日", "limit": 3}]


def test_flush_proactive_emoji_empty_does_not_call(monkeypatch):
    session = _session()
    calls = []

    async def fake_flush(items):
        calls.append(items)
        return len(items)

    monkeypatch.setattr(session, "_flush_deferred_emoji", fake_flush)
    assert asyncio.run(session.flush_proactive_emoji([])) == 0
    assert calls == []


def test_flush_proactive_emoji_delegates(monkeypatch):
    session = _session()
    calls = []

    async def fake_flush(items):
        calls.append(items)
        return len(items)

    monkeypatch.setattr(session, "_flush_deferred_emoji", fake_flush)
    items = [EMOJI_ITEM, dict(EMOJI_ITEM, uid="acc_a:c2")]
    assert asyncio.run(session.flush_proactive_emoji(items)) == 2
    assert calls == [items]


def test_run_proactive_turn_recall_memory_handler_is_scoped(monkeypatch):
    """接线测试：Agent 拿到的 recall_memory 必须已经是「空 query 拒绝」的版本。"""
    _install_db(monkeypatch, _chat(), [])
    _install_tools(monkeypatch)
    agent_cap = _install_agent(monkeypatch, {"text": "在忙吗", "usage": {}})
    session = _session()
    asyncio.run(session.run_proactive_turn("acc_a:c1", "随便聊聊"))
    tool = next(t for t in agent_cap["tools"] if t.name == "recall_memory")
    out = asyncio.run(tool.handler({"query": "   "}))
    assert "error" in out
