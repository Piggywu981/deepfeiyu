"""跨对话沟通（转告代发 / 主动串门 / 转发记录内目标）的单元与链路测试。

覆盖设计文档 docs/superpowers/specs/2026-10-26-cross-chat-communication-design.md：
rules.check_cross_visit、parse_chat_record_senders、build_tools(cross=...)、
resolve_recipient、cross_send、_flush_deferred_cross、_build_prompt、_auto_reply
链路与 _maybe_cross_visit / _cross_visit；以及「旧配置（无 cross 段）行为不变」。

全部离线：不联网、不连真实 LLM/微信；LLM 用 FakeLLM 按脚本回放 stream 事件，
驱动用 StubDriver 只记录发送。Windows 上 time.time() 精度粗，连发消息可能同 ts，
断言一律按内容/集合判断，不依赖消息时间排序（异步任务在 asyncio.run 的同一
async 函数里 gather，避免 asyncio.run 结束时把后台任务取消）。
"""
from __future__ import annotations

import asyncio
import copy
import time
import uuid

import pytest

from app import config, db, rules
from app.ai import tools as tools_mod
from app.wechat.base import Message, parse_chat_record_senders
from app.wechat.manager import AccountSession, CrossChatError


# ------------------------------------------------------------------ 夹具与工具


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """独立临时库 + 隔离本机 data/config.json（否则图集/default_ai_on 等设置会漏进用例）。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "load", lambda: {"ai": {}, "auto": {}, "cross": {}})
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


def _cross_conf(**over) -> dict:
    """DEFAULTS["cross"] 的完整副本，便于单测按需改开关/限额。"""
    conf = {
        "enabled": True,
        "relay": True,
        "visit": True,
        "daily_limit": 30,
        "visit_gap_sec": 600,
        "visit_max_per_hour": 6,
        "relay_max_chars": 500,
    }
    conf.update(over)
    return conf


def _cfg(*, cross: dict | None = None, ai: dict | None = None, auto: dict | None = None) -> dict:
    """最小可用配置；cross=None 表示旧配置（完全没有跨对话段）。"""
    out: dict = {
        "ai": {
            "temperature": 0.7,
            "max_tokens": 800,
            "vision_enabled": False,
            "memory_enabled": False,
            "emoji_with_reply": False,
            "system_prompt": "人设",
        },
        "auto": {
            "enabled": True,
            "history_context": 12,
            "max_reply_chars": 300,
            "prefix": "",
            "reply_timeout_sec": 0,
            "blacklist": [],
            "only_whitelist": False,
        },
        "send": {"enabled": False},
    }
    if cross is not None:
        out["cross"] = dict(cross)
    out["ai"].update(ai or {})
    out["auto"].update(auto or {})
    return out


class FakeLLM:
    """按预设脚本回放 stream 事件；configured=True 让 manager 侧串门前置判断放行。"""

    configured = True

    def __init__(self, rounds: list[list[dict]] | None = None) -> None:
        self.rounds = list(rounds or [])
        self.calls = 0

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        for evt in self.rounds.pop(0):
            yield evt


class RecordingLLM(FakeLLM):
    """额外记录每轮收到的 system 提示与工具名，用于验证提示词/工具注册。"""

    def __init__(self, rounds: list[list[dict]] | None = None) -> None:
        super().__init__(rounds)
        self.seen_tools: list[list[str]] = []
        self.seen_messages: list[list[dict]] = []

    async def stream(self, messages, tools=None, **kwargs):
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        self.seen_messages.append([dict(m) for m in messages])
        async for evt in super().stream(messages, tools=tools, **kwargs):
            yield evt


class StubDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    """只记录真发（chat_id, text），不碰微信。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class FailingDriver(StubDriver):
    """send_text 一律抛异常：验证发送失败后的额度回滚与留痕。"""

    def send_text(self, chat_id: str, text: str) -> None:
        raise RuntimeError("模拟发送失败")


def _session(cfg: dict | None = None, llm: FakeLLM | None = None) -> AccountSession:
    """带 StubDriver 的会话：跨对话发送需要驱动。"""
    session = AccountSession(
        {"id": "acc_x", "name": "A", "nickname": "B"},
        lambda: cfg if cfg is not None else _cfg(),
        llm or FakeLLM(),
    )
    session.driver = StubDriver()
    return session


def _llm_session(cfg: dict | None = None, llm: FakeLLM | None = None) -> AccountSession:
    """不带驱动的会话：resolve_recipient / 纯解析场景用。"""
    return AccountSession(
        {"id": "acc_x", "name": "A", "nickname": "B"},
        lambda: cfg if cfg is not None else _cfg(),
        llm or FakeLLM(),
    )


def _text_round(text: str) -> list[dict]:
    return [{"type": "text", "text": text}, {"type": "finish", "reason": "stop"}]


def _tool_round(name: str, args: str, call_id: str = "call_1") -> list[dict]:
    return [
        {"type": "tool_delta", "index": 0, "id": call_id, "name": name, "args": args},
        {"type": "finish", "reason": "tool_calls"},
    ]


def _msg(**over) -> Message:
    base = dict(
        chat_id="c1",
        chat_name="会话一",
        sender_name="对方",
        content="在吗",
        msg_type="text",
        msg_id="m1",
        ts=1000.0,
    )
    base.update(over)
    return Message(**base)


def _add_message(
    uid: str,
    content: str,
    *,
    is_self: bool = False,
    msg_type: str = "text",
    ts: float = 1000.0,
) -> None:
    """直接落一条消息行（模拟历史里已有的对话，不走 handle_message）。"""
    account_id, _, chat_id = uid.partition(":")
    db.add_message(
        {
            "id": uuid.uuid4().hex,
            "account_id": account_id,
            "chat_uid": uid,
            "chat_id": chat_id,
            "chat_name": "",
            "sender_id": "self" if is_self else "peer",
            "sender_name": "我" if is_self else "对方",
            "content": content,
            "msg_type": msg_type,
            "is_group": False,
            "is_self": is_self,
            "source": "wechat",
            "ts": ts,
            "meta": {},
        }
    )


async def _drain(session: AccountSession) -> None:
    """在同一个事件循环里等 session 的后台任务（转告 flush / 串门）跑完。"""
    for _ in range(10):
        pending = list(session._tasks)
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _run_auto_reply(session: AccountSession, uid: str, msg: Message) -> None:
    await session._auto_reply(uid, msg)
    await _drain(session)


async def _trigger_cross_visit(session: AccountSession, uid: str, msg: Message, **kwargs) -> None:
    session._maybe_cross_visit(uid, msg, **kwargs)
    await _drain(session)


def _deep_update(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _visit_chat(**over) -> dict:
    chat = {"uid": "acc_x:c1", "chat_id": "c1", "name": "会话一", "ai_enabled": True}
    chat.update(over)
    return chat


def _visit_rules_cfg(**over) -> dict:
    cfg = {
        "cross": {"enabled": True, "visit": True},
        "auto": {"enabled": True, "blacklist": []},
        "ai": {},
    }
    return _deep_update(cfg, over)


def _self_msg(**over) -> Message:
    base = dict(
        chat_id="c1",
        chat_name="会话一",
        sender_name="我",
        content="晚上一起吃饭",
        msg_type="text",
        msg_id="self-1",
        ts=1000.0,
        is_self=True,
    )
    base.update(over)
    return Message(**base)


# 结构取自真实 4.x 解密库：被转发的消息完整内嵌在 recorditem 的 CDATA 里；
# 第三位发言人「测试丙」带空格，用来验证 normalize 后与前面去重。
RECORD_SENDERS_XML = """<msg>
\t<appmsg>
\t\t<title>群聊的聊天记录</title>
\t\t<type>19</type>
\t\t<recorditem><![CDATA[<recordinfo>
\t<title>群聊的聊天记录</title>
\t<datalist count="3">
\t\t<dataitem datatype="1" dataid="a1">
\t\t\t<datadesc>铃声第一句</datadesc>
\t\t\t<sourcename>测试丙</sourcename>
\t\t</dataitem>
\t\t<dataitem datatype="1" dataid="a2">
\t\t\t<datadesc>那可太熟了</datadesc>
\t\t\t<sourcename>小林</sourcename>
\t\t</dataitem>
\t\t<dataitem datatype="1" dataid="a3">
\t\t\t<datadesc>那我呢</datadesc>
\t\t\t<sourcename> 测试丙 </sourcename>
\t\t</dataitem>
\t</datalist>
</recordinfo>]]></recorditem>
\t</appmsg>
</msg>"""


# ================================================================== A. rules.check_cross_visit


def test_check_cross_visit_allows_owner_text_in_private_chat():
    allowed, reason = rules.check_cross_visit(
        _visit_rules_cfg(), {"id": "acc_x", "name": "A", "nickname": "B"}, _visit_chat(), _self_msg()
    )
    assert allowed is True
    assert "串门" in reason


@pytest.mark.parametrize(
    "case,cfg_patch,msg_patch,chat_patch,keyword",
    [
        ("visit_off", {"cross": {"visit": False}}, {}, {}, "串门"),
        ("not_self", {}, {"is_self": False}, {}, "号主"),
        ("group", {}, {"is_group": True}, {}, "群聊"),
        ("ai_disabled", {}, {}, {"ai_enabled": False}, "AI"),
        ("blacklist", {"auto": {"blacklist": ["会话一"]}}, {}, {}, "黑名单"),
        ("non_text", {}, {"msg_type": "image"}, {}, "文本"),
        ("filehelper", {}, {"chat_id": "filehelper", "chat_name": "文件传输助手"}, {}, "文件传输"),
    ],
)
def test_check_cross_visit_rejects_ineligible(case, cfg_patch, msg_patch, chat_patch, keyword):
    cfg = _deep_update(_visit_rules_cfg(), cfg_patch)
    chat = _visit_chat(**chat_patch)
    msg = _self_msg(**msg_patch)
    allowed, reason = rules.check_cross_visit(
        cfg, {"id": "acc_x", "name": "A", "nickname": "B"}, chat, msg
    )
    assert allowed is False, case
    assert keyword in reason, f"{case}: 拦截原因缺少「{keyword}」→ {reason!r}"


# ================================================================== B. parse_chat_record_senders


def test_parse_chat_record_senders_dedups_in_order():
    senders = parse_chat_record_senders(RECORD_SENDERS_XML)
    assert senders == ["测试丙", "小林"]


def test_parse_chat_record_senders_plain_text_returns_empty():
    assert parse_chat_record_senders("普通文本，没有合并转发的记录结构") == []
    assert parse_chat_record_senders("") == []


# ================================================================== C. build_tools(cross=...)


def _tool_names(tools) -> list[str]:
    return [t.name for t in tools]


def test_build_tools_auto_mode_registers_cross_sender_only_with_cross_relay():
    session = _llm_session()
    with_cross = tools_mod.build_tools(
        session, allow_send=False, chat_uid="acc_x:c1", cross={"relay": True, "defer": []}
    )
    assert "send_wechat_message" in _tool_names(with_cross)

    # 旧配置 / 不传 cross：自动回复链路依旧不注册跨会话发送工具
    without_cross = tools_mod.build_tools(session, allow_send=False, chat_uid="acc_x:c1")
    assert "send_wechat_message" not in _tool_names(without_cross)


def test_build_tools_console_mode_registers_cross_sender_once():
    session = _llm_session()
    tools = tools_mod.build_tools(
        session, allow_send=True, cross={"relay": True, "defer": []}
    )
    assert _tool_names(tools).count("send_wechat_message") == 1


def _relay_handler(session: AccountSession, monkeypatch, target, ambiguous, cross):
    async def fake_resolve(name):
        return target, ambiguous

    monkeypatch.setattr(session, "resolve_recipient", fake_resolve)
    tools = tools_mod.build_tools(
        session, allow_send=False, chat_uid="acc_x:c1", cross=cross
    )
    return {t.name: t for t in tools}["send_wechat_message"].handler


def test_relay_handler_defers_message_to_target(temp_db, monkeypatch):
    session = _llm_session()
    db.upsert_chat("acc_x", "c1", "当前会话", False, "", 1000.0)
    target = {"uid": "acc_x:c2", "chat_id": "c2", "name": "目标"}
    defer: list[dict] = []
    handler = _relay_handler(
        session, monkeypatch, target, [], {"relay": True, "defer": defer}
    )

    res = asyncio.run(handler({"to": "目标", "content": "明天见"}))

    assert res["ok"] is True and res["deferred"] is True
    assert res["to"] == "目标"
    assert len(defer) == 1
    assert defer[0]["uid"] == "acc_x:c2"
    assert defer[0]["content"] == "明天见"


def test_relay_handler_rejects_current_chat(temp_db, monkeypatch):
    session = _llm_session()
    db.upsert_chat("acc_x", "c1", "当前会话", False, "", 1000.0)
    target = {"uid": "acc_x:c1", "chat_id": "c1", "name": "当前会话"}
    handler = _relay_handler(session, monkeypatch, target, [], {"relay": True, "defer": []})

    res = asyncio.run(handler({"to": "当前会话", "content": "给自己发"}))

    assert "error" in res
    assert "当前会话" in res["error"]


def test_relay_handler_reports_ambiguous_recipient(temp_db, monkeypatch):
    session = _llm_session()
    db.upsert_chat("acc_x", "c1", "当前会话", False, "", 1000.0)
    handler = _relay_handler(
        session, monkeypatch, None, ["甲", "甲2"], {"relay": True, "defer": []}
    )

    res = asyncio.run(handler({"to": "甲", "content": "明天见"}))

    assert "error" in res
    assert "多个会话" in res["error"]


def test_relay_handler_enforces_round_limit(temp_db, monkeypatch):
    session = _llm_session()
    db.upsert_chat("acc_x", "c1", "当前会话", False, "", 1000.0)
    target = {"uid": "acc_x:c9", "chat_id": "c9", "name": "新目标"}
    defer = [{"uid": f"acc_x:c{i}", "name": f"目标{i}", "content": "旧消息"} for i in range(3)]
    handler = _relay_handler(session, monkeypatch, target, [], {"relay": True, "defer": defer})

    res = asyncio.run(handler({"to": "新目标", "content": "第四条"}))

    assert "error" in res
    assert "最多" in res["error"]
    assert len(defer) == 3


def test_relay_handler_dedups_same_target_and_content(temp_db, monkeypatch):
    session = _llm_session()
    db.upsert_chat("acc_x", "c1", "当前会话", False, "", 1000.0)
    target = {"uid": "acc_x:c2", "chat_id": "c2", "name": "目标"}
    defer: list[dict] = []
    handler = _relay_handler(session, monkeypatch, target, [], {"relay": True, "defer": defer})

    first = asyncio.run(handler({"to": "目标", "content": "明天见"}))
    second = asyncio.run(handler({"to": "目标", "content": "明天见"}))

    assert first["ok"] is True and second["ok"] is True
    assert len(defer) == 1  # 重复登记不增长


# ================================================================== D. resolve_recipient


def _chats_session(chats: list[dict], monkeypatch) -> AccountSession:
    session = _llm_session()

    def fake_visible_chats(keyword="", limit=200):
        return chats

    monkeypatch.setattr(session, "visible_chats", fake_visible_chats)
    return session


def test_resolve_recipient_exact_name(monkeypatch):
    chats = [
        {"uid": "acc_x:c1", "chat_id": "c1", "name": "甲"},
        {"uid": "acc_x:c2", "chat_id": "c2", "name": "甲的朋友"},
    ]
    session = _chats_session(chats, monkeypatch)

    target, ambiguous = asyncio.run(session.resolve_recipient("甲"))

    assert target is not None and target["uid"] == "acc_x:c1"
    assert ambiguous == []


def test_resolve_recipient_unique_substring(monkeypatch):
    chats = [
        {"uid": "acc_x:c1", "chat_id": "c1", "name": "小甲"},
        {"uid": "acc_x:c2", "chat_id": "c2", "name": "乙"},
    ]
    session = _chats_session(chats, monkeypatch)

    target, ambiguous = asyncio.run(session.resolve_recipient("甲"))

    assert target is not None and target["uid"] == "acc_x:c1"
    assert ambiguous == []


def test_resolve_recipient_multiple_substrings_returns_candidates(monkeypatch):
    chats = [
        {"uid": "acc_x:c1", "chat_id": "c1", "name": "小甲"},
        {"uid": "acc_x:c2", "chat_id": "c2", "name": "甲鱼"},
    ]
    session = _chats_session(chats, monkeypatch)

    target, ambiguous = asyncio.run(session.resolve_recipient("甲"))

    assert target is None
    assert set(ambiguous) == {"小甲", "甲鱼"}


def test_resolve_recipient_not_found_without_driver(monkeypatch):
    session = _chats_session([{"uid": "acc_x:c1", "chat_id": "c1", "name": "乙"}], monkeypatch)
    assert session.driver is None

    target, ambiguous = asyncio.run(session.resolve_recipient("查无此人"))

    assert target is None
    assert ambiguous == []


# ================================================================== E. cross_send


def _cross_session(**over) -> AccountSession:
    return _session(cfg=_cfg(cross=_cross_conf(**over)))


def test_cross_send_actually_sends_and_records(temp_db):
    session = _cross_session()
    target_uid = db.upsert_chat("acc_x", "c2", "目标会话", False, "", 1000.0)

    asyncio.run(session.cross_send(target_uid, "明天见", kind="relay", requestor="当前会话"))

    # 真发到目标会话
    assert session.driver.sent == [("c2", "明天见")]
    rows = [r for r in db.list_messages(target_uid, limit=10) if r["is_self"]]
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "agent"
    assert row["meta"]["cross"]["kind"] == "relay"
    assert row["meta"]["cross"]["requestor"] == "当前会话"
    # 事件留痕 + kv 计数
    events = db.list_events("acc_x")
    assert any(e["kind"] == "cross" and "跨对话" in e["title"] for e in events)
    day = time.strftime("%Y-%m-%d")
    assert db.kv_get(f"cross:acc_x:{day}") == {"relay": 1, "visit": 0}


def test_cross_send_rejected_when_disabled(temp_db):
    session = _cross_session(enabled=False)
    target_uid = db.upsert_chat("acc_x", "c2", "目标会话", False, "", 1000.0)

    with pytest.raises(CrossChatError) as excinfo:
        asyncio.run(session.cross_send(target_uid, "明天见"))

    assert "关闭" in str(excinfo.value)
    assert session.driver.sent == []


def test_cross_send_rejected_when_content_too_long(temp_db):
    session = _cross_session(relay_max_chars=4)
    target_uid = db.upsert_chat("acc_x", "c2", "目标会话", False, "", 1000.0)

    with pytest.raises(CrossChatError) as excinfo:
        asyncio.run(session.cross_send(target_uid, "一二三四五六"))

    assert "最多" in str(excinfo.value)
    assert session.driver.sent == []


def test_cross_send_rejected_by_daily_limit(temp_db):
    session = _cross_session(daily_limit=5)
    target_uid = db.upsert_chat("acc_x", "c2", "目标会话", False, "", 1000.0)
    day = time.strftime("%Y-%m-%d")
    db.kv_set(f"cross:acc_x:{day}", {"relay": 5, "visit": 0})

    with pytest.raises(CrossChatError) as excinfo:
        asyncio.run(session.cross_send(target_uid, "明天见"))

    assert "上限" in str(excinfo.value)
    assert session.driver.sent == []


def test_cross_send_rejected_when_target_missing(temp_db):
    session = _cross_session()

    with pytest.raises(CrossChatError) as excinfo:
        asyncio.run(session.cross_send("acc_x:查无此会话", "明天见"))

    assert "不存在" in str(excinfo.value)
    assert session.driver.sent == []


def test_cross_send_refunds_daily_quota_when_send_fails(temp_db):
    """回归：先预占额度再真发，发送失败要把当日额度还回去、不写「跨对话·」成功事件。"""
    session = _cross_session()
    session.driver = FailingDriver()
    target_uid = db.upsert_chat("acc_x", "c2", "目标会话", False, "", 1000.0)

    with pytest.raises(RuntimeError):
        asyncio.run(session.cross_send(target_uid, "明天见"))

    # 预占的额度已回滚：内存计数与 kv 都是 0
    assert session._cross_today() == {"relay": 0, "visit": 0}
    day = time.strftime("%Y-%m-%d")
    assert db.kv_get(f"cross:acc_x:{day}") == {"relay": 0, "visit": 0}
    # 没发成功就不该有「跨对话·」成功事件
    assert not any("跨对话·" in e["title"] for e in db.list_events("acc_x"))


# ================================================================== F. _flush_deferred_cross


def test_flush_deferred_cross_sends_in_order(temp_db):
    session = _cross_session()
    uid_a = db.upsert_chat("acc_x", "c2", "目标一", False, "", 1000.0)
    uid_b = db.upsert_chat("acc_x", "c3", "目标二", False, "", 999.0)
    items = [
        {"uid": uid_a, "name": "目标一", "content": "第一条", "requestor": ""},
        {"uid": uid_b, "name": "目标二", "content": "第二条", "requestor": ""},
    ]

    asyncio.run(session._flush_deferred_cross(items))

    assert session.driver.sent == [("c2", "第一条"), ("c3", "第二条")]


def test_flush_deferred_cross_continues_after_failure(temp_db):
    session = _cross_session()
    uid_ok = db.upsert_chat("acc_x", "c3", "目标二", False, "", 1000.0)
    items = [
        {"uid": "acc_x:不存在", "name": "不存在", "content": "第一条", "requestor": ""},
        {"uid": uid_ok, "name": "目标二", "content": "第二条", "requestor": ""},
    ]

    asyncio.run(session._flush_deferred_cross(items))

    # 单条失败只留痕，不影响后续
    assert session.driver.sent == [("c3", "第二条")]
    warns = [e for e in db.list_events("acc_x") if e["level"] == "warn"]
    assert any("跨对话代发失败" in e["title"] for e in warns)


# ================================================================== G. _build_prompt


def _prompt_system(session: AccountSession, cfg: dict, msg: Message, chat: dict | None = None) -> str:
    chat = chat or {"uid": "acc_x:c1", "name": "会话一"}
    return session._build_prompt(cfg, chat, [], msg)[0]["content"]


def test_build_prompt_includes_cross_block_when_relay_on(temp_db):
    session = _session()
    system = _prompt_system(
        session, _cfg(cross={"enabled": True, "relay": True}), _msg(content="帮我给张三带句话")
    )
    assert "【跨对话沟通】" in system
    assert "send_wechat_message" in system


def test_build_prompt_without_cross_block_when_off(temp_db):
    session = _session()
    msg = _msg(content="帮我给张三带句话")
    # 旧配置（整段缺失）/ 总开关关闭 / 转告关闭 都不出现跨对话提示块
    for cfg in (
        _cfg(),
        _cfg(cross={"enabled": False, "relay": True}),
        _cfg(cross={"enabled": True, "relay": False}),
    ):
        assert "【跨对话沟通】" not in _prompt_system(session, cfg, msg)


def test_build_prompt_lists_record_senders_when_relay_on(temp_db):
    session = _session()
    msg = _msg(content=RECORD_SENDERS_XML, msg_type="link", msg_id="m-record", ts=1001.0)
    system = _prompt_system(session, _cfg(cross={"enabled": True, "relay": True}), msg)
    assert "记录里出现的发言人" in system
    assert "测试丙" in system


def test_build_prompt_record_warns_not_to_shout_when_off(temp_db):
    session = _session()
    msg = _msg(content=RECORD_SENDERS_XML, msg_type="link", msg_id="m-record", ts=1001.0)
    for cfg in (_cfg(), _cfg(cross={"enabled": False, "relay": True})):
        system = _prompt_system(session, cfg, msg)
        assert "别向他们喊话" in system
        assert "记录里出现的发言人" not in system


# ================================================================== H. _auto_reply 链路


def test_auto_reply_relays_message_after_reply(temp_db, monkeypatch):
    llm = FakeLLM(
        [
            _tool_round(
                "send_wechat_message", '{"to": "目标", "content": "明天见"}'
            ),
            _text_round("好，已转达"),
        ]
    )
    session = _session(cfg=_cfg(cross=_cross_conf()), llm=llm)
    uid = db.upsert_chat("acc_x", "c1", "当前会话", False, "在吗", 1000.0)
    target_uid = db.upsert_chat("acc_x", "c2", "目标", False, "", 999.0)

    async def fake_resolve(name):
        if name == "目标":
            return db.get_chat(target_uid), []
        return None, []

    monkeypatch.setattr(session, "resolve_recipient", fake_resolve)
    msg = _msg(content="帮我跟目标说明天见", msg_id="m-cross", ts=1002.0)

    asyncio.run(_run_auto_reply(session, uid, msg))

    # 当前会话先收到确认回复，目标会话随后真发转告
    assert [chat_id for chat_id, _ in session.driver.sent][:2] == ["c1", "c2"]
    assert session.driver.sent[0] == ("c1", "好，已转达")
    assert session.driver.sent[-1] == ("c2", "明天见")

    current_rows = db.list_messages(uid, limit=20)
    assert any(r["is_self"] and r["content"] == "好，已转达" for r in current_rows)

    target_rows = [r for r in db.list_messages(target_uid, limit=10) if r["is_self"]]
    assert target_rows and target_rows[0]["content"] == "明天见"
    assert target_rows[0]["source"] == "agent"
    assert target_rows[0]["meta"]["cross"]["kind"] == "relay"
    assert any(e["kind"] == "cross" and "跨对话" in e["title"] for e in db.list_events("acc_x"))


def test_auto_reply_flushes_relay_even_when_reply_skipped(temp_db, monkeypatch):
    """设计 7.3：[SKIP] / 无正文分支同样把已登记的转告发出去。"""
    llm = FakeLLM(
        [
            _tool_round("send_wechat_message", '{"to": "目标", "content": "明天见"}'),
            _text_round("[SKIP]"),
        ]
    )
    session = _session(cfg=_cfg(cross=_cross_conf()), llm=llm)
    uid = db.upsert_chat("acc_x", "c1", "当前会话", False, "在吗", 1000.0)
    target_uid = db.upsert_chat("acc_x", "c2", "目标", False, "", 999.0)

    async def fake_resolve(name):
        if name == "目标":
            return db.get_chat(target_uid), []
        return None, []

    monkeypatch.setattr(session, "resolve_recipient", fake_resolve)
    msg = _msg(content="帮我跟目标说明天见", msg_id="m-skip", ts=1002.0)

    asyncio.run(_run_auto_reply(session, uid, msg))

    # 本轮不接话，但已登记的转告照常发出；当前会话不产生回复
    assert session.driver.sent == [("c2", "明天见")]
    assert not any(r["is_self"] for r in db.list_messages(uid, limit=10))
    target_rows = [r for r in db.list_messages(target_uid, limit=10) if r["is_self"]]
    assert target_rows and target_rows[0]["meta"]["cross"]["kind"] == "relay"


def test_auto_reply_marks_undelivered_relay_when_reply_send_fails(temp_db, monkeypatch):
    """回归：本轮正文没发出去就不替人转告，但要留一条 warn 事件，不能静默消失。"""
    llm = FakeLLM(
        [
            _tool_round("send_wechat_message", '{"to": "目标", "content": "明天见"}'),
            _text_round("好，已转达"),
        ]
    )
    session = _session(cfg=_cfg(cross=_cross_conf()), llm=llm)
    session.driver = FailingDriver()
    uid = db.upsert_chat("acc_x", "c1", "当前会话", False, "在吗", 1000.0)
    target_uid = db.upsert_chat("acc_x", "c2", "目标", False, "", 999.0)

    async def fake_resolve(name):
        if name == "目标":
            return db.get_chat(target_uid), []
        return None, []

    monkeypatch.setattr(session, "resolve_recipient", fake_resolve)
    msg = _msg(content="帮我跟目标说明天见", msg_id="m-fail", ts=1002.0)

    asyncio.run(_run_auto_reply(session, uid, msg))

    # 当前会话正文发送失败：目标会话不能收到任何新消息，也没有成功发送记录
    assert session.driver.sent == []
    assert not any(r["is_self"] for r in db.list_messages(target_uid, limit=10))
    events = db.list_events("acc_x")
    assert not any("跨对话·" in e["title"] for e in events)
    # 但必须有「未发出」的 warn 留痕（审计线索）
    assert any(
        e["kind"] == "cross" and e["level"] == "warn" and "跨对话转告未发出" in e["title"]
        for e in events
    )


# ================================================================== I. 主动串门


def _visit_session(
    rounds: list[list[dict]] | None = None, **cross_over
) -> tuple[AccountSession, FakeLLM]:
    llm = FakeLLM(rounds)
    session = _session(cfg=_cfg(cross=_cross_conf(**cross_over)), llm=llm)
    return session, llm


def _prepare_visit_chat(
    session: AccountSession, chat_id: str = "c1", name: str = "会话一"
) -> tuple[str, Message]:
    """会话里已有对端聊天（串门要求至少一条对端消息），号主本人在这个会话刚发了一条。"""
    uid = db.upsert_chat("acc_x", chat_id, name, False, "对方先说了一句", 1000.0)
    db.set_chat_ai(uid, True)
    _add_message(uid, "对方先说了一句", is_self=False, ts=1000.0)
    return uid, _self_msg(
        chat_id=chat_id,
        chat_name=name,
        content="晚上一起吃饭",
        msg_id=f"self-{chat_id}",
        ts=1001.0,
    )


def test_maybe_cross_visit_sends_when_model_answers(temp_db):
    session, llm = _visit_session([_text_round("哈哈这个我懂")])
    uid, msg = _prepare_visit_chat(session)

    asyncio.run(_trigger_cross_visit(session, uid, msg))

    assert llm.calls == 1
    assert session.driver.sent == [("c1", "哈哈这个我懂")]
    agent_rows = [
        r for r in db.list_messages(uid, limit=20) if r["is_self"] and r["source"] == "agent"
    ]
    assert agent_rows and agent_rows[0]["content"] == "哈哈这个我懂"
    assert agent_rows[0]["meta"]["cross"]["kind"] == "visit"
    events = db.list_events("acc_x")
    assert any(e["kind"] == "cross" and "串门" in e["title"] for e in events)
    assert session._cross_visit_running == set()


def test_cross_visit_sends_nothing_on_skip(temp_db):
    session, llm = _visit_session([_text_round("[SKIP]")])
    uid, msg = _prepare_visit_chat(session)

    asyncio.run(_trigger_cross_visit(session, uid, msg))

    assert llm.calls == 1  # 调过一次模型（这次尝试也计入冷却）
    assert session.driver.sent == []
    assert not any(e["kind"] == "cross" for e in db.list_events("acc_x"))
    assert session._cross_visit_running == set()


def test_maybe_cross_visit_does_nothing_when_was_paused(temp_db):
    session, llm = _visit_session([_text_round("不该发")])
    uid, msg = _prepare_visit_chat(session)

    async def runner():
        session._maybe_cross_visit(uid, msg, was_paused=True)
        assert session._tasks == set()  # 人工接管期间不创建串门任务
        await _drain(session)

    asyncio.run(runner())

    assert llm.calls == 0
    assert session.driver.sent == []


def test_maybe_cross_visit_cooldown_blocks_second_trigger(temp_db):
    session, llm = _visit_session([_text_round("哈哈这个我懂")])
    uid, msg = _prepare_visit_chat(session)

    async def runner():
        session._maybe_cross_visit(uid, msg)
        await _drain(session)
        first_calls = llm.calls
        # 冷却期内（同会话 600s）再触发：不新建任务、不再烧模型
        session._cross_visit_at[uid] = time.time()
        session._maybe_cross_visit(uid, msg)
        assert session._tasks == set()
        return first_calls

    first_calls = asyncio.run(runner())

    assert first_calls == 1
    assert len(session.driver.sent) == 1


def test_maybe_cross_visit_hourly_limit_blocks_more_attempts(temp_db):
    """设计 7.4：全账号每小时串门尝试上限（这里是 1 次/小时，跨会话共享）。"""
    session, llm = _visit_session([_text_round("哈哈这个我懂")], visit_max_per_hour=1)
    uid_a, msg_a = _prepare_visit_chat(session)
    uid_b, msg_b = _prepare_visit_chat(session, chat_id="c2", name="会话二")

    async def runner():
        session._maybe_cross_visit(uid_a, msg_a)
        await _drain(session)
        session._maybe_cross_visit(uid_b, msg_b)
        assert session._tasks == set()  # 本小时额度用完，第二个会话不再尝试

    asyncio.run(runner())

    assert llm.calls == 1
    assert session.driver.sent == [("c1", "哈哈这个我懂")]


@pytest.mark.parametrize("case", ["ai_disabled", "group", "not_self"])
def test_maybe_cross_visit_skips_ineligible(temp_db, case):
    session, llm = _visit_session([_text_round("不该发")])
    uid, msg = _prepare_visit_chat(session)
    if case == "ai_disabled":
        db.set_chat_ai(uid, False)
    elif case == "group":
        msg.is_group = True
    elif case == "not_self":
        msg.is_self = False

    async def runner():
        session._maybe_cross_visit(uid, msg)
        assert session._tasks == set()
        await _drain(session)

    asyncio.run(runner())

    assert llm.calls == 0
    assert session.driver.sent == []


def test_cross_visit_skips_without_peer_messages(temp_db):
    session, llm = _visit_session([_text_round("不该发")])
    uid = db.upsert_chat("acc_x", "c1", "会话一", False, "只有我自己说过话", 1000.0)
    db.set_chat_ai(uid, True)
    _add_message(uid, "只有我自己说过话", is_self=True, ts=1000.0)
    msg = _self_msg(content="再补一句", msg_id="self-3", ts=1001.0)

    asyncio.run(session._cross_visit(uid, msg))

    assert llm.calls == 0  # 会话还没真正聊起来，不调模型
    assert session.driver.sent == []
    assert session._cross_visit_running == set()


def test_maybe_cross_visit_skips_stale_self_message(temp_db):
    """回归：重启后水位补推的旧自发消息（ts 早于 started_at - 5）不触发串门。"""
    session, llm = _visit_session([_text_round("过时的串门")])
    uid, _ = _prepare_visit_chat(session)
    session.started_at = time.time()

    async def runner():
        old_msg = _self_msg(
            content="一小时前我自己发的",
            msg_id="stale-1",
            ts=time.time() - 3600,
        )
        session._maybe_cross_visit(uid, old_msg, was_paused=False)
        await asyncio.gather(*session._tasks)
        assert session._tasks == set()

    asyncio.run(runner())

    assert llm.calls == 0
    assert session.driver.sent == []
    assert not any(e["kind"] == "cross" for e in db.list_events("acc_x"))


def test_maybe_cross_visit_allows_fresh_message_after_restart(temp_db):
    """对照：started_at 已设置时，刚发出的新消息（ts≈now）照常串门。"""
    session, llm = _visit_session([_text_round("哈哈这个我懂")])
    uid, _ = _prepare_visit_chat(session)
    session.started_at = time.time()

    async def runner():
        fresh_msg = _self_msg(content="刚发的新消息", msg_id="fresh-1", ts=time.time())
        session._maybe_cross_visit(uid, fresh_msg, was_paused=False)
        await asyncio.gather(*session._tasks)

    asyncio.run(runner())

    assert llm.calls == 1
    assert session.driver.sent == [("c1", "哈哈这个我懂")]


# ================================================================== J. 旧配置回归


def test_auto_reply_unchanged_without_cross_section(temp_db):
    llm = RecordingLLM([_text_round("在的")])
    session = _session(cfg=_cfg(), llm=llm)  # 旧配置：完全没有 cross 段
    uid = db.upsert_chat("acc_x", "c1", "会话一", False, "在吗", 1000.0)
    msg = _msg(content="在吗", msg_id="m-old", ts=1001.0)

    asyncio.run(_run_auto_reply(session, uid, msg))

    assert session.driver.sent[0] == ("c1", "在的")
    rows = [r for r in db.list_messages(uid, limit=10) if r["is_self"] and r["source"] == "agent"]
    assert rows and rows[0]["content"] == "在的"
    # 新功能默认前置显式判断：旧配置不注入提示块、不注册跨会话发送工具
    system = llm.seen_messages[0][0]["content"]
    assert "【跨对话沟通】" not in system
    assert "send_wechat_message" not in llm.seen_tools[0]
    assert not any(e["kind"] == "cross" for e in db.list_events("acc_x"))
