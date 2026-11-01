"""回复必带表情包：挑选输出解析、已发判定、漏发时的自动补发与开关。"""
from __future__ import annotations

import asyncio
import re

import pytest

from app import config, db, emoji
from app.ai import moe_wiki
from app.wechat import manager as manager_mod
from app.wechat.base import Message
from app.wechat.manager import (
    AccountSession,
    _emoji_sent_in,
    _incoming_brief,
    _parse_emoji_pick,
)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    # 隔离真实配置：开发机的 data/config.json 若开着 wiki_emoji_only，
    # 工具层会拦下测试用的普通表情，与本文件要测的时序无关
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


def _cfg(ai_extra: dict | None = None, auto_extra: dict | None = None):
    ai = {
        "temperature": 0.7,
        "max_tokens": 800,
        "vision_enabled": False,
        "memory_enabled": False,
        "system_prompt": "人设",
    }
    ai.update(ai_extra or {})
    auto = {"history_context": 12}
    auto.update(auto_extra or {})
    return lambda: {
        "auto": auto,
        "ai": ai,
        # 结尾表情节奏在测试里关掉：安静期 0 = 正文发完立即跟图（旧行为），便于断言
        "send": {"trailing_emoji_gap_sec": [0, 0], "trailing_emoji_cooldown_sec": 0},
    }


class FakeLLM:
    """按预设脚本回放 stream 事件；configured 可控，供补发表情的选择器走真路径。"""

    def __init__(self, rounds: list[list[dict]], configured: bool = True) -> None:
        self.rounds = rounds
        self.configured = configured

    async def stream(self, messages, tools=None, **kwargs):
        for evt in self.rounds.pop(0):
            yield evt


class RecordingLLM(FakeLLM):
    """在回放之外记录每轮 stream 收到的工具名与消息，验证挂了哪些工具、喂了哪些附图。"""

    def __init__(self, rounds: list[list[dict]], configured: bool = True) -> None:
        super().__init__(rounds, configured)
        self.seen_tools: list[list[str]] = []
        self.seen_messages: list[list[dict]] = []

    async def stream(self, messages, tools=None, **kwargs):
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        self.seen_messages.append(messages)
        async for evt in super().stream(messages, tools=tools, **kwargs):
            yield evt


class PromptCaptureLLM(FakeLLM):
    """回放脚本轮次，同时记下每次 stream 收到的消息全文，用于断言兜底候选清单的排布。"""

    def __init__(self, rounds: list[list[dict]], configured: bool = True) -> None:
        super().__init__(rounds, configured)
        self.prompts: list[str] = []

    async def stream(self, messages, tools=None, **kwargs):
        self.prompts.append("\n".join(str(m.get("content") or "") for m in messages))
        async for evt in super().stream(messages, tools=tools, **kwargs):
            yield evt


def _candidate_lines(prompt: str) -> list[str]:
    """挑出兜底提示里的「#id：…」候选行，顺序即模型看到的顺序。"""
    return [ln for ln in prompt.splitlines() if re.match(r"^#\d+：", ln)]


class StubDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self) -> None:
        self.sent_text: list[tuple[str, str]] = []
        self.sent_emoji: list[tuple[str, str]] = []
        self.calls: list[str] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent_text.append((chat_id, text))
        self.calls.append("text")

    def send_emoji(self, chat_id: str, path: str) -> None:
        self.sent_emoji.append((chat_id, str(path)))
        self.calls.append("emoji")


def _text_round(text: str) -> list[dict]:
    return [
        {"type": "text", "text": text},
        {"type": "finish", "reason": "stop"},
    ]


def _tool_round(name: str, args: str) -> list[dict]:
    return [
        {"type": "tool_delta", "index": 0, "id": "call_1", "name": name, "args": args},
        {"type": "finish", "reason": "tool_calls"},
    ]


def _self_emoji_rows(uid: str) -> list[dict]:
    return [
        r
        for r in db.list_messages(uid, limit=50)
        if r["is_self"] and r["msg_type"] == "emoji"
    ]


def test_parse_emoji_pick():
    assert _parse_emoji_pick("#12") == 12
    assert _parse_emoji_pick("第 3 个最贴切") == 3
    assert _parse_emoji_pick("挑不出合适的，输出 0") == 0
    assert _parse_emoji_pick("没有贴切的") == 0
    assert _parse_emoji_pick("") == 0


def test_emoji_sent_in():
    assert _emoji_sent_in([{"name": "send_wechat_emoji", "ok": True}])
    assert not _emoji_sent_in([{"name": "send_wechat_emoji", "ok": False}])
    assert not _emoji_sent_in([{"name": "search_emojis", "ok": True}])
    assert not _emoji_sent_in([])


def test_incoming_brief():
    nick = "小鲸"
    assert _incoming_brief(Message(
        chat_id="c", chat_name="x", sender_name="对方",
        content="", msg_type="emoji", msg_id="m", ts=0.0,
    ), nick) == "（对方发来一个表情包）"
    assert _incoming_brief(Message(
        chat_id="c", chat_name="x", sender_name="对方",
        content="@小鲸 在吗", msg_type="text", msg_id="m", ts=0.0,
    ), nick) == "在吗"


def test_auto_reply_fallback_sends_emoji(temp_db, tmp_path, monkeypatch):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "a" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "无语,熊猫头", "熊猫头无语脸")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    # 第 1 轮只回了文字没发表情 → 兜底选择器（第 2 轮）挑中库里的表情
    session.llm = FakeLLM([_text_round("在的呀"), _text_round(str(row["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m1", ts=1001.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    sent = session.driver.sent_emoji
    assert len(sent) == 1 and sent[0][0] == "c1"
    rows = _self_emoji_rows(uid)
    assert len(rows) == 1 and rows[0]["source"] == "agent"


def test_auto_reply_tool_emoji_after_text(temp_db, tmp_path, monkeypatch):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "哈哈", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "b" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "大笑", "狂笑熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    # 模型先调 send_wechat_emoji 再输出文字：表情只登记不真发，
    # 等正文发出后才补上——对方永远先收到文字；也不再触发兜底
    session.llm = FakeLLM(
        [
            _tool_round("send_wechat_emoji", f'{{"emoji_id": {row["id"]}}}'),
            _text_round("哈哈哈"),
        ]
    )
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="讲个笑话", msg_type="text", msg_id="m2", ts=1002.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert len(session.driver.sent_emoji) == 1
    assert session.driver.calls == ["text", "emoji"]
    assert len(_self_emoji_rows(uid)) == 1
    texts = [r for r in db.list_messages(uid, limit=50) if r["is_self"] and r["msg_type"] == "text"]
    assert texts and texts[0]["content"].endswith("哈哈哈")


def test_auto_reply_skip_with_tool_emoji_still_sends(temp_db, tmp_path, monkeypatch):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "[表情]", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "c" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "哈哈", "接住对方梗的熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    # 对方只发来一个表情包：模型选定表情后输出 [SKIP]（不回文字），表情照样发出
    session.llm = FakeLLM(
        [
            _tool_round("send_wechat_emoji", f'{{"emoji_id": {row["id"]}}}'),
            _text_round("[SKIP]"),
        ]
    )
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="", msg_type="emoji", msg_id="m3", ts=1003.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.sent_text == []
    assert len(session.driver.sent_emoji) == 1 and session.driver.sent_emoji[0][0] == "c1"
    assert len(_self_emoji_rows(uid)) == 1


def test_auto_reply_no_fallback_when_disabled(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg({"emoji_with_reply": False}), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)

    # 开关关闭：只回文字，不触发补发（脚本里也没给选择器轮次，若误触发会弹错并留下表情记录）
    session.llm = FakeLLM([_text_round("在的呀")], configured=True)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m3", ts=1003.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.sent_text and session.driver.sent_text[0][1].endswith("在的呀")
    assert _self_emoji_rows(uid) == []


def test_build_prompt_emoji_hint(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m4", ts=1004.0,
    )

    cfg = _cfg()()
    prompt = "\n".join(m["content"] for m in session._build_prompt(cfg, chat, [], msg))
    assert "每次回复都必须至少配一个表情包" in prompt

    cfg_off = _cfg({"emoji_with_reply": False})()
    prompt_off = "\n".join(m["content"] for m in session._build_prompt(cfg_off, chat, [], msg))
    assert "不想发就不用" in prompt_off
    assert "每次回复都必须至少配一个表情包" not in prompt_off


def test_instant_reply_text_first_emoji_via_fallback(temp_db, tmp_path, monkeypatch):
    """秒回+文字消息：主循环不给表情工具，一轮直接出文字零表情轮次；
    正文发出后由兜底轻量调用挑表情补上——对方永远先收到文字。"""
    session = AccountSession(
        {"id": "acc_a", "name": "A"}, _cfg(auto_extra={"instant_reply": True}), FakeLLM([])
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "d" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "在吗,招手", "招手熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    # 第 1 轮主 Agent 直接出文字（没有表情工具可调），第 2 轮兜底选择器挑库里的表情
    llm = RecordingLLM([_text_round("在的呀"), _text_round(str(row["id"]))])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m5", ts=1005.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.calls == ["text", "emoji"]
    main_tools = llm.seen_tools[0]
    assert "search_emojis" not in main_tools
    assert "send_wechat_emoji" not in main_tools
    assert "find_emoji_online" not in main_tools
    assert llm.seen_tools[1] == []  # 兜底选择器不带工具
    assert len(_self_emoji_rows(uid)) == 1


def test_instant_reply_emoji_msg_uses_parallel_pick(temp_db, tmp_path, monkeypatch):
    """秒回+对方只发表情包：主循环不再挂表情工具（省掉 search_emojis 的来回），
    模型一整轮只说话，表情由并行挑选补上；[表情] 表示只要表情不要文字。"""
    session = AccountSession(
        {"id": "acc_a", "name": "A"}, _cfg(auto_extra={"instant_reply": True}), FakeLLM([])
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "[表情]", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "e" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "哈哈", "接住对方梗的熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = RecordingLLM([_text_round("[表情]"), _text_round(str(row["id"]))])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="", msg_type="emoji", msg_id="m6", ts=1006.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert "send_wechat_emoji" not in llm.seen_tools[0]
    assert "search_emojis" not in llm.seen_tools[0]
    assert session.driver.sent_text == []  # 只要表情，不发消息文字
    assert session.driver.calls == ["emoji"]
    assert llm.seen_tools[1] == []  # 挑选器不带工具
    events = db.list_events("acc_a", limit=10)
    assert any("仅表情" in (e.get("title") or "") for e in events)


def test_instant_reply_chuanzi_emoji_token_sends_site_image_when_not_required(
    temp_db, tmp_path, monkeypatch
):
    """秒回+串子+关闭「回复必带表情包」：模型用 [表情] 要图时必须真的发一张站内图。

    旧实现里这个组合既没有表情工具、又不会走自动补图，提示词却写着
    「系统会自动补图」——模型以为有图，实际整轮只有文字。
    """
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(
            {"chuanzi_mode": True, "chuanzi_site_emoji": True, "emoji_with_reply": False},
            auto_extra={"instant_reply": True},
        ),
        FakeLLM([]),
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "发个串子表情包", 1000.0)
    local = db.add_emoji("acc_a", "local://local", "d" * 32, 100, 100)
    db.set_emoji_tags("acc_a", local["id"], "无语", "本地无语")
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    # 第 1 轮模型按提示只输出 [表情]；第 2 轮挑选器选中站内图
    llm = PromptCaptureLLM([_text_round("[表情]"), _text_round(str(site["id"]))])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="发个串子表情包", msg_type="text", msg_id="m7", ts=1007.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert calls and "发个串子表情包" in calls[0]
    assert len(calls) == 1
    assert session.driver.sent_text == []
    assert session.driver.calls == ["emoji"]
    assert len(_self_emoji_rows(uid)) == 1
    # 挑选器看到的站内候选带 [串子站] 与台词，优先选它
    assert "[串子站]" in llm.prompts[1]
    assert "台词：这是什么" in llm.prompts[1]


def test_instant_reply_emoji_msg_skip_stays_silent(temp_db, tmp_path, monkeypatch):
    """[SKIP] 仍是「这轮不接话」：并行挑选要作废，不能对方发个表情就硬跟一张。"""
    session = AccountSession(
        {"id": "acc_a", "name": "A"}, _cfg(auto_extra={"instant_reply": True}), FakeLLM([])
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "[表情]", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "e" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "哈哈", "接住对方梗的熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = RecordingLLM([_text_round("[SKIP]")])  # 只准备一轮：挑选被取消，不该再调模型
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="", msg_type="emoji", msg_id="m6b", ts=1006.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.calls == []
    assert len(llm.seen_tools) == 1


def test_instant_reply_emoji_msg_picks_from_illustration(temp_db, tmp_path, monkeypatch):
    """挑选器要看得见对方那张表情包，替掉工具轮次后才由它接手"看着图挑"。"""
    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg({"vision_enabled": True}, auto_extra={"instant_reply": True}),
        FakeLLM([]),
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "[表情]", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "e" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "哈哈", "接住对方梗的熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return str(path)

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = RecordingLLM([_text_round("哈哈接住"), _text_round(str(row["id"]))])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content='<msg><emoji cdnurl="https://cdn/x" md5="' + "e" * 32 + '"/></msg>',
        msg_type="emoji", msg_id="m6c", ts=1006.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    pick_messages = llm.seen_messages[1]
    user = pick_messages[-1]["content"]
    assert isinstance(user, list), "挑选轮的正文应带附图"
    assert any(p.get("type") == "image_url" for p in user)
    assert session.driver.calls == ["text", "emoji"]


def test_non_instant_reply_keeps_emoji_tools(temp_db, tmp_path, monkeypatch):
    """聚拢窗口模式：主循环照旧带表情工具（时序由 defer_emoji 登记机制保证）。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "f" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "在吗,招手", "招手熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = RecordingLLM([_text_round("在的呀"), _text_round(str(row["id"]))])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m7", ts=1007.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    main_tools = llm.seen_tools[0]
    assert "search_emojis" in main_tools
    assert "send_wechat_emoji" in main_tools
    assert "find_emoji_online" in main_tools


class ParallelPickLLM:
    """正文分片流式产出（每片之间让出事件循环）；挑选轮次会在正文产出途中被并行调度到。"""

    configured = True

    def __init__(self, emoji_id: int, driver: StubDriver) -> None:
        self.emoji_id = emoji_id
        self.driver = driver
        self.calls = 0
        self.chunks = ("在的呀", "，刚睡醒", "一会儿就去看")

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            for chunk in self.chunks:
                yield {"type": "text", "text": chunk}
                await asyncio.sleep(0)
            yield {"type": "finish", "reason": "stop"}
        else:
            self.driver.calls.append("pick")
            yield {"type": "text", "text": str(self.emoji_id)}
            yield {"type": "finish", "reason": "stop"}


def test_fast_text_picks_emoji_while_streaming(temp_db, tmp_path, monkeypatch):
    """秒回+文字：正文够长就在流式过程中并行挑表情，文字发完即发，挑选不占发送后的等待。"""
    session = AccountSession(
        {"id": "acc_a", "name": "A"}, _cfg(auto_extra={"instant_reply": True}), FakeLLM([])
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "a1" * 16, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "在吗,刚睡醒", "刚睡醒熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = ParallelPickLLM(row["id"], session.driver)
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="醒了没", msg_type="text", msg_id="m10", ts=1010.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    # 挑选在正文发出之前就完成了（"pick" 排在 "text" 之前），表情紧跟文字
    assert session.driver.calls == ["pick", "text", "emoji"]
    assert llm.calls == 2  # 正文一轮 + 挑选一次，没有重复挑


class ToolThenLongTextLLM:
    """第 1 轮调工具选表情，第 2 轮流式输出较长正文（每片之间让出事件循环）。"""

    configured = True

    def __init__(self, emoji_id: int) -> None:
        self.emoji_id = emoji_id
        self.calls = 0
        self.chunks = ("哈哈哈", "这个太好笑了", "我笑到打鸣")

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            assert tools and any(t["function"]["name"] == "send_wechat_emoji" for t in tools)
            yield {
                "type": "tool_delta", "index": 0, "id": "call_1",
                "name": "send_wechat_emoji", "args": f'{{"emoji_id": {self.emoji_id}}}',
            }
            yield {"type": "finish", "reason": "tool_calls"}
            return
        for chunk in self.chunks:
            yield {"type": "text", "text": chunk}
            await asyncio.sleep(0)
        yield {"type": "finish", "reason": "stop"}


def test_burst_mode_no_prefetch_when_model_picked(temp_db, tmp_path, monkeypatch):
    """聚拢窗口模式：模型自己用工具选好表情（正文再长也一样），不再并行预挑。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "哈哈", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "a2" * 16, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "大笑", "狂笑熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = ToolThenLongTextLLM(row["id"])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="讲个笑话", msg_type="text", msg_id="m11", ts=1011.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    # 只有工具轮 + 正文轮，没有挑选轮；表情仍排在文字之后
    assert llm.calls == 2
    assert session.driver.calls == ["text", "emoji"]
    assert len(_self_emoji_rows(uid)) == 1


class GatedPickLLM:
    """第 1 轮先吐够长的前言再调工具；挑选轮次挂起等闸门，用来验证 tool_start 时被取消。"""

    configured = True

    def __init__(self, emoji_id: int) -> None:
        self.emoji_id = emoji_id
        self.calls = 0
        self.pick_started = 0
        self.pick_cancelled = 0
        self.gate = asyncio.Event()  # 永不 set：挑选只能靠取消结束

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "text", "text": "让我先看看聊天记录再回你"}
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # 让预挑任务跑起来并挂到闸门上
            yield {
                "type": "tool_delta", "index": 0, "id": "call_1",
                "name": "send_wechat_emoji", "args": f'{{"emoji_id": {self.emoji_id}}}',
            }
            yield {"type": "finish", "reason": "tool_calls"}
            return
        if self.calls == 2:
            self.pick_started += 1
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.pick_cancelled += 1
                raise
        for chunk in ("哈哈哈", "笑死我了哈哈", "太逗了"):
            yield {"type": "text", "text": chunk}
            await asyncio.sleep(0)
        yield {"type": "finish", "reason": "stop"}


def test_tool_round_cancels_preamble_prefetch(temp_db, tmp_path, monkeypatch):
    """工具轮的前言文字只是中间话：预挑被取消，不给回复抢跑挑表情、也不白占并发。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "哈哈", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "a3" * 16, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "大笑", "狂笑熊猫头")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = GatedPickLLM(row["id"])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="讲个笑话", msg_type="text", msg_id="m12", ts=1012.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert llm.pick_started == 1 and llm.pick_cancelled == 1
    assert llm.calls == 3  # 工具轮 + 被取消的挑选 + 正文轮，没有多出来的挑选
    assert session.driver.calls == ["text", "emoji"]
    assert len(_self_emoji_rows(uid)) == 1


def test_build_prompt_instant_fast_text(temp_db):
    """提示词：秒回时文字与表情消息都走「直接说话，表情由系统配」；非秒回保持工具指引。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    text_msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m8", ts=1008.0,
    )
    emoji_msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="", msg_type="emoji", msg_id="m9", ts=1009.0,
    )

    def _prompt(cfg, msg):
        return "\n".join(m["content"] for m in session._build_prompt(cfg, chat, [], msg))

    # 秒回+文字：直接写文字，不引导先找表情
    instant_cfg = _cfg(auto_extra={"instant_reply": True})()
    prompt = _prompt(instant_cfg, text_msg)
    assert "直接输出文字回复" in prompt
    assert "每次回复都必须至少配一个表情包" not in prompt

    # 秒回+表情消息：不再叫模型用工具，改成 [表情]/[SKIP] 约定
    emoji_prompt = _prompt(instant_cfg, emoji_msg)
    assert "每次回复都必须至少配一个表情包" not in emoji_prompt
    assert "search_emojis" not in emoji_prompt
    assert "整条只输出 [表情]" in emoji_prompt

    # 非秒回：指引不变
    assert "每次回复都必须至少配一个表情包" in _prompt(_cfg()(), text_msg)
    assert "search_emojis" in _prompt(_cfg()(), emoji_msg)

    # 秒回+不强制表情：提示词不再提表情工具
    off_cfg = _cfg({"emoji_with_reply": False}, auto_extra={"instant_reply": True})()
    prompt_off = _prompt(off_cfg, text_msg)
    assert "search_emojis" not in prompt_off
    assert "直接输出文字回复" not in prompt_off


def test_build_prompt_chuanzi_and_fast_branch(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m20", ts=1020.0,
    )

    cfg = _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True})()
    prompt = "\n".join(m["content"] for m in session._build_prompt(cfg, chat, [], msg))
    assert "【串子模式】" in prompt
    assert "每轮最多查一次" in prompt
    assert "串子站入库的表情优先" in prompt
    # 串子模式不是"软人设后面贴补丁"：persona 要被整段替换成串子嘴，
    # 同时 ≤30 字的硬长度上限要生效。
    assert "串子嘴" in prompt
    assert "≤30 字" in prompt
    assert "嘴碎、缺德、爱抬杠" in prompt

    off = _cfg(ai_extra={"chuanzi_mode": False})()
    off_prompt = "\n".join(m["content"] for m in session._build_prompt(off, chat, [], msg))
    # 关掉就回到原软人设：串子人格词和硬长度上限都不出现
    assert "串子嘴" not in off_prompt
    assert "≤30 字" not in off_prompt

    fast = _cfg(
        ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}, auto_extra={"instant_reply": True}
    )()
    fast_prompt = "\n".join(
        m["content"] for m in session._build_prompt(fast, chat, [], msg)
    )
    assert "直接输出文字回复" in fast_prompt
    assert "系统会在文字发出后从串子站补一张图" in fast_prompt


def test_build_prompt_chuanzi_site_switch_off_uses_local_wording(temp_db):
    """串子站表情包默认关闭：提示词不许再承诺查站，改用本地库口径。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m21", ts=1021.0,
    )

    cfg = _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": False})()
    prompt = "\n".join(m["content"] for m in session._build_prompt(cfg, chat, [], msg))
    assert "【串子模式】" in prompt
    assert "这一轮不能联网查台词库" in prompt
    assert "每轮最多查一次" not in prompt
    assert "本轮不联网查站" in prompt

    fast = _cfg(
        ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": False},
        auto_extra={"instant_reply": True},
    )()
    fast_prompt = "\n".join(
        m["content"] for m in session._build_prompt(fast, chat, [], msg)
    )
    assert "系统会在文字发出后从本地表情库补一张图" in fast_prompt
    assert "从串子站补" not in fast_prompt


def test_chuanzi_site_switch_off_fallback_picks_imported_site_emoji_without_hunting(
    temp_db, tmp_path, monkeypatch
):
    """开关关闭时兜底选图不查站，但已入库的站内图仍可作为候选（不影响已入库）。"""
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": False}),
        FakeLLM([]),
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {"ok": True, "emoji_id": site["id"], "source": "searchanythingfun"}

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)

    session.llm = PromptCaptureLLM([_text_round(str(site["id"]))])
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m22", ts=1022.0,
    )
    row = asyncio.run(
        session._pick_reply_emoji(
            chat, [], msg, "在的呀", chuanzi_state={"used": False, "hint": "在吗"}
        )
    )

    assert calls == []
    assert row and row["id"] == site["id"]
    assert "[串子站]" in session.llm.prompts[0]


def test_build_prompt_chuanzi_serious_adds_hard_gate(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "我不想活了", 1000.0)
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="我不想活了", msg_type="text", msg_id="m27", ts=1027.0,
    )
    cfg = _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True})()

    prompt = "\n".join(
        m["content"] for m in session._build_prompt(cfg, chat, [], msg)
    )

    assert "【本轮硬闸】" in prompt
    assert "安慰" in prompt
    assert "串子图" in prompt


def test_chuanzi_fallback_queries_site_once_and_prefers_site(
    temp_db, tmp_path, monkeypatch
):
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}),
        FakeLLM([]),
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    local = db.add_emoji("acc_a", "local://local", "d" * 32, 100, 100)
    db.set_emoji_tags("acc_a", local["id"], "在吗,招手", "招手熊猫头")
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    session.llm = PromptCaptureLLM([_text_round("在的呀"), _text_round(str(site["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m21", ts=1021.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert calls and "在吗" in calls[0]
    assert len(calls) == 1
    assert len(session.driver.sent_emoji) == 1
    assert session.driver.sent_emoji[0][0] == "c1"
    # 真发出去的就是站内那张图（按落盘文件名的 md5 认领），不是本地表情
    assert session.driver.sent_emoji[0][1] == str(tmp_path / f"{site['md5']}.gif")
    assert local["md5"] not in session.driver.sent_emoji[0][1]
    # 站内图被顶到候选第一位并带 [串子站] 标注与台词
    lines = _candidate_lines(session.llm.prompts[-1])
    assert lines[0] == (
        f"#{site['id']}：[串子站] {site['tags']}｜{site['description']}｜台词：这是什么"
    )
    assert any(ln.startswith(f"#{local['id']}：") for ln in lines)


def test_chuanzi_fallback_reuses_tool_recorded_site_row(temp_db, monkeypatch):
    """模型本轮已查过站并记下 emoji_id：兜底直接复用那张图，一次站点请求都不再发。"""
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}),
        FakeLLM([]),
    )
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    local = db.add_emoji("acc_a", "local://local", "d" * 32, 100, 100)
    db.set_emoji_tags("acc_a", local["id"], "在吗,招手", "招手熊猫头")
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": local["id"],
            "quote": "第二次偷来的",
            "source": "searchanythingfun",
            "imported": True,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)

    state = {"used": True, "hint": "在吗", "emoji_id": site["id"], "quote": "这是什么"}
    session.llm = PromptCaptureLLM([_text_round(str(site["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m23", ts=1023.0,
    )
    row = asyncio.run(
        session._pick_reply_emoji(chat, [], msg, "在的呀", chuanzi_state=state)
    )

    assert calls == []
    assert row and row["id"] == site["id"]
    lines = _candidate_lines(session.llm.prompts[0])
    assert lines[0].startswith(f"#{site['id']}：[串子站] ")
    assert "台词：这是什么" in lines[0]
    assert "第二次偷来的" not in session.llm.prompts[0]
    assert state["emoji_id"] == site["id"]


def test_chuanzi_fallback_serious_skips_site_and_asks_comfort(temp_db, monkeypatch):
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}),
        FakeLLM([]),
    )
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "我不想活了", 1000.0)
    chat = db.get_chat(uid)
    comfort = db.add_emoji("acc_a", "local://comfort", "c" * 32, 100, 100)
    db.set_emoji_tags("acc_a", comfort["id"], "安慰,抱抱", "抱抱")
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)

    state = {"used": False, "hint": "我不想活了", "serious": True}
    session.llm = PromptCaptureLLM([_text_round(str(comfort["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="我不想活了", msg_type="text", msg_id="m28", ts=1028.0,
    )
    row = asyncio.run(
        session._pick_reply_emoji(
            chat, [], msg, "先别一个人扛，我在。", chuanzi_state=state
        )
    )

    assert calls == []
    assert row and row["id"] == comfort["id"]
    prompt = session.llm.prompts[0]
    assert "[串子站]" not in prompt
    assert "本轮硬闸" in prompt
    assert "安慰" in prompt


def test_instant_chuanzi_sends_text_then_site_image(temp_db, tmp_path, monkeypatch):
    """秒回+串子：正文先走，站内图紧跟其后补上，站点台词绝不混进文字里。"""
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(
            ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}, auto_extra={"instant_reply": True}
        ),
        FakeLLM([]),
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    llm = RecordingLLM([_text_round("在的呀"), _text_round(str(site["id"]))])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m24", ts=1024.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.calls == ["text", "emoji"]
    assert len(calls) == 1
    assert session.driver.sent_emoji[0][1] == str(tmp_path / f"{site['md5']}.gif")
    # 主轮没挂表情工具
    assert "search_emojis" not in llm.seen_tools[0]
    # 台词一个字都没进正文
    assert session.driver.sent_text and all(
        "这是什么" not in t for _, t in session.driver.sent_text
    )
    self_texts = [
        str(r["content"])
        for r in db.list_messages(uid, limit=50)
        if r["is_self"] and r["msg_type"] == "text"
    ]
    assert self_texts and all("这是什么" not in t for t in self_texts)


def test_reply_emoji_fallback_gates_site_privileges_on_live_flag(
    temp_db, monkeypatch
):
    """串子关闭时兜底选图也不给站内图特权：不豁免角色名、不置顶、不打 [串子站]。"""
    from app.ai import chuanzi, moe_wiki

    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    local = db.add_emoji("acc_a", "local://local", "d" * 32, 100, 100)
    db.set_emoji_tags("acc_a", local["id"], "在吗,招手", "招手熊猫头")
    site, _ = db.import_site_emoji(
        "acc_a", "e" * 32, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: False)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    monkeypatch.setattr(moe_wiki, "character_names", lambda *a, **k: ["大肥鱼"])
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m25", ts=1025.0,
    )

    # 仅AI娘开着：站内图不再豁免，两张都不合格 → 不挑、不发、不查站
    llm = PromptCaptureLLM([_text_round(str(site["id"]))])
    session.llm = llm
    assert (
        asyncio.run(
            session._pick_reply_emoji(chat, [], msg, "在的呀", chuanzi_state=None)
        )
        is None
    )
    assert llm.prompts == []
    assert calls == []

    # 仅AI娘关掉：站内图仍在候选里，但既不置顶也不标注
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
    llm2 = PromptCaptureLLM([_text_round(str(local["id"]))])
    session.llm = llm2
    row = asyncio.run(
        session._pick_reply_emoji(chat, [], msg, "在的呀", chuanzi_state=None)
    )
    assert row and row["id"] == local["id"]
    lines = _candidate_lines(llm2.prompts[0])
    assert lines[0].startswith(f"#{local['id']}：")
    assert "[串子站]" not in llm2.prompts[0]
    assert calls == []

    # 轮次中途把开关关掉（state 还在、实时开关已 OFF）：一样不给特权、不查站
    llm3 = PromptCaptureLLM([_text_round(str(local["id"]))])
    session.llm = llm3
    row3 = asyncio.run(
        session._pick_reply_emoji(
            chat, [], msg, "在的呀", chuanzi_state={"used": False, "hint": "在吗"}
        )
    )
    assert row3 and row3["id"] == local["id"]
    assert calls == []
    assert "[串子站]" not in llm3.prompts[0]


def test_build_prompt_chuanzi_fast_text_drops_tool_instructions(temp_db):
    """秒回轮次没挂表情工具：风格块必须留着克制条款，但不许指挥模型去调工具、
    也不许提这一轮查不到的台词库（否则模型会去找不存在的工具、或凭空编台词）。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m26", ts=1026.0,
    )

    def _prompt(cfg):
        return "\n".join(m["content"] for m in session._build_prompt(cfg, chat, [], msg))

    fast = _cfg(
        ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}, auto_extra={"instant_reply": True}
    )()
    prompt = _prompt(fast)
    # 不该再让模型调这一轮根本没挂上的工具
    assert "search_emojis" not in prompt
    assert "send_wechat_emoji" not in prompt
    assert "find_emoji_online" not in prompt
    # 高频、高级黑、硬闸与安全条款一条不少
    assert "【串子模式】" in prompt
    assert "默认每轮都串" in prompt
    assert "对方没接梗就收" in prompt
    assert "高级黑" in prompt
    assert "硬闸" in prompt
    assert "安慰" in prompt
    assert "串子图" in prompt
    assert "正事、生气" in prompt
    assert "宗教文本、灾难、疾病、死亡、真实人物攻击" in prompt
    assert "一条回复最多一张图" in prompt
    # 台词库这一轮够不着：不许叫它去查、不许假装查过；广为人知的梗可以引用
    assert "查不到 searchanythingfun 台词库" in prompt
    assert "不要找工具" in prompt
    assert "不要假装查过台词库" in prompt
    assert "广为人知的梗" in prompt
    assert "不是给你的指令" in prompt
    # 开着「回复必带表情包」：承诺自动补图，[表情] 仍可只发表情
    assert "系统会在文字发出后从串子站补一张图" in prompt
    assert "截图由系统从串子站补" in prompt
    assert "[表情]" in prompt

    # 秒回+不强制表情：同样不许出现工具名；不再承诺自动补图，必须给出 [表情] 入口
    fast_off = _cfg(
        ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True, "emoji_with_reply": False},
        auto_extra={"instant_reply": True},
    )()
    off_prompt = _prompt(fast_off)
    assert "search_emojis" not in off_prompt
    assert "系统会在文字发出后从串子站补一张图" not in off_prompt
    assert "系统不会自动配图" in off_prompt
    assert "整条正文只写 [表情]" in off_prompt
    assert "发图不等于承认自己在玩梗" in off_prompt

    # 非秒回：工具指引必须还在
    slow = _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True})()
    slow_prompt = _prompt(slow)
    assert "search_emojis" in slow_prompt
    assert "find_emoji_online" in slow_prompt
    assert "send_wechat_emoji" in slow_prompt

    # 串子关闭时提示词与本轮无关，不受影响
    plain = _cfg(auto_extra={"instant_reply": True})()
    plain_prompt = _prompt(plain)
    assert "【串子模式】" not in plain_prompt
    assert "串子站" not in plain_prompt



def test_chuanzi_fallback_site_failure_uses_local(temp_db, tmp_path, monkeypatch):
    from app.ai import chuanzi

    session = AccountSession(
        {"id": "acc_a", "name": "A"},
        _cfg(ai_extra={"chuanzi_mode": True, "chuanzi_site_emoji": True}),
        FakeLLM([]),
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    local = db.add_emoji("acc_a", "local://local", "f" * 32, 100, 100)
    db.set_emoji_tags("acc_a", local["id"], "在吗,招手", "招手熊猫头")
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {"ok": False, "error": "429", "message": "额度不足"}

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)

    session.llm = FakeLLM([_text_round("在的呀"), _text_round(str(local["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m22", ts=1022.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert len(calls) == 1
    assert len(session.driver.sent_emoji) == 1


# ------------------------------------------------- 自动打标「非表情包」剔除与锁定
# 锁定行不能被这条链路删掉；reject_emoji 返回 None（并发被锁/行已没）时也不能碰缓存文件。

def _not_sticker_tagger(monkeypatch):
    from app.ai import tagger

    async def fake_tag(llm, image_path, context, vocab=None):
        return {"not_sticker": True}

    monkeypatch.setattr(tagger, "tag_emoji", fake_tag)


def _seed_emoji_cache(tmp_path, monkeypatch, md5: str):
    """把表情缓存目录指到临时目录并写一张缓存图，返回缓存路径。"""
    monkeypatch.setattr(emoji, "EMOJI_DIR", tmp_path)
    path = tmp_path / f"{md5}.gif"
    path.write_bytes(b"GIF89a" + b"\x00" * 10)
    return path


def test_auto_tag_not_sticker_leaves_locked_row_and_cache_alone(
    temp_db, tmp_path, monkeypatch
):
    """锁定表情不能被后台打标的「非表情包」判定删行、入拒收名单或清缓存。"""
    md5 = "d" * 32
    row = db.add_emoji("acc_a", "local://locked", md5, 100, 100)
    db.set_emoji_locked("acc_a", row["id"], True)
    cache = _seed_emoji_cache(tmp_path, monkeypatch, md5)
    _not_sticker_tagger(monkeypatch)

    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    asyncio.run(session._tag_emoji_row(db.get_emoji("acc_a", row["id"]), "", "", cache))

    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh is not None and fresh["locked"] == 1
    assert cache.is_file()
    assert db.list_reject_md5s("acc_a") == set()


def test_auto_tag_not_sticker_keeps_cache_when_reject_returns_none(
    temp_db, tmp_path, monkeypatch
):
    """reject_emoji 对锁定行返回 None：必须原样退出，不能按「本账号之外没有引用」清掉缓存。"""
    md5 = "e" * 32
    row = db.add_emoji("acc_a", "local://racing", md5, 100, 100)
    cache = _seed_emoji_cache(tmp_path, monkeypatch, md5)
    _not_sticker_tagger(monkeypatch)
    # 模拟打标期间这张图被别的入口锁上：数据层拒绝删除
    monkeypatch.setattr(db, "reject_emoji", lambda *args, **kwargs: None)

    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    asyncio.run(session._tag_emoji_row(db.get_emoji("acc_a", row["id"]), "", "", cache))

    assert db.get_emoji("acc_a", row["id"]) is not None
    assert cache.is_file()
    assert db.list_reject_md5s("acc_a") == set()


def test_auto_tag_not_sticker_still_removes_unlocked_row(temp_db, tmp_path, monkeypatch):
    """回归护栏：未锁定的坏图仍然要删行 + 入名单 + 清缓存。"""
    md5 = "f" * 32
    row = db.add_emoji("acc_a", "local://junk", md5, 100, 100)
    cache = _seed_emoji_cache(tmp_path, monkeypatch, md5)
    _not_sticker_tagger(monkeypatch)

    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    asyncio.run(session._tag_emoji_row(db.get_emoji("acc_a", row["id"]), "", "", cache))

    assert db.get_emoji("acc_a", row["id"]) is None
    assert not cache.exists()
    assert db.list_reject_md5s("acc_a") == {md5}


# ---------------------------------------------------------------- 分条发送 + 结尾表情
def test_wiki_emoji_only_without_roster_still_sends_trailing_emoji(
    temp_db, tmp_path, monkeypatch
):
    """回归：「仅AI娘表情包」开着但图鉴不可用时，候选不能被清空——

    否则「回复必带表情包」会永远挑不到图（实测开关打开 + moe_wiki_path 指向的
    图集目录不存在时，每轮回复都没有结尾表情）。
    """
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "d" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "可爱,卖萌", "卖萌表情")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    monkeypatch.setattr(moe_wiki, "character_names", lambda root=None: [])
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    # 第 1 轮只回文字，第 2 轮（兜底选择器）挑中库里的表情
    session.llm = FakeLLM([_text_round("在的呀"), _text_round(str(row["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m10", ts=1010.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    assert session.driver.calls == ["text", "emoji"]
    assert len(_self_emoji_rows(uid)) == 1


def test_reply_splits_newlines_then_sends_trailing_emoji(temp_db, tmp_path, monkeypatch):
    """用户要求：多句分条发送（不换行），一轮说完最后跟一个表情包。"""
    session = AccountSession(
        {"id": "acc_a", "name": "A"}, _cfg(auto_extra={"split_reply": True}), FakeLLM([])
    )
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://test", "e" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "开心,卖萌", "开心表情")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    session.llm = FakeLLM(
        [_text_round("我看了下\n\n明天下午有空"), _text_round(str(row["id"]))]
    )
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="明天有空吗", msg_type="text", msg_id="m11", ts=1011.0,
    )
    asyncio.run(session._auto_reply(uid, msg))

    texts = [t for _, t in session.driver.sent_text]
    assert texts == ["我看了下", "明天下午有空"]
    assert all("\n" not in t for t in texts)
    assert session.driver.calls == ["text", "text", "emoji"]  # 表情永远跟在最后


# ---------------------------------------------------------------- 候选池全库覆盖 + 兜底
def test_rank_emoji_candidates_covers_whole_library():
    rows = [
        {"id": i, "md5": f"{i:032x}", "tags": f"标签{i}", "description": f"描述{i}"}
        for i in range(1, 61)
    ]
    recent = {rows[0]["md5"]}
    out = manager_mod._rank_emoji_candidates(rows, "我们聊聊标签25吧", recent, limit=40)
    ids = [r["id"] for r in out]
    assert len(out) == 40
    assert 25 in ids          # 上下文相关的老表情能进候选
    assert 1 not in ids       # 最近发过的沉底，不在前 40
    assert len(set(ids)) == 40


def test_pick_reply_candidates_include_old_emojis(temp_db):
    """回归：库超过 20 张时，候选必须来自全库而不是只取最新 20 张。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    chat = db.get_chat(uid)
    ids = []
    for i in range(30):
        row = db.add_emoji("acc_a", f"local://x{i}", f"{i + 1:032x}", 100, 100)
        db.set_emoji_tags("acc_a", row["id"], f"测试标签{i}", "")
        ids.append(row["id"])

    llm = PromptCaptureLLM([_text_round(f"#{ids[0]}")])
    session.llm = llm
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m30", ts=1030.0,
    )
    picked = asyncio.run(session._pick_reply_emoji(chat, [], msg, "在的呀"))
    assert picked and picked["id"] == ids[0]  # 最旧的一张也能被挑中
    fed = {
        int(m.group(1))
        for m in (re.match(r"#(\d+)", ln) for ln in _candidate_lines(llm.prompts[0]))
        if m
    }
    assert ids[0] in fed and ids[-1] in fed and len(fed) == 30


def test_send_prefetch_emoji_falls_back_when_pick_none(temp_db, tmp_path, monkeypatch):
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://fallback", "9" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "可爱", "兜底表情")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    async def pick_none(*args, **kwargs):
        return None

    monkeypatch.setattr(session, "_pick_reply_emoji", pick_none)
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m31", ts=1031.0,
    )
    ok = asyncio.run(
        session._send_prefetch_emoji(
            None, chat, [], msg, "在的呀", None, uid, False, fallback=True
        )
    )
    assert ok and len(session.driver.sent_emoji) == 1

    # fallback=False（硬闸场景）时不许乱补图
    session.driver.sent_emoji.clear()
    ok2 = asyncio.run(
        session._send_prefetch_emoji(
            None, chat, [], msg, "在的呀", None, uid, False, fallback=False
        )
    )
    assert ok2 is False and session.driver.sent_emoji == []


def test_auto_reply_picker_declines_still_sends_fallback(temp_db, tmp_path, monkeypatch):
    """挑选模型回 0 时也要有结尾表情（用户要求每轮都有图）。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://fallback2", "8" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "可爱", "兜底表情")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)
    session.llm = FakeLLM([_text_round("在的呀"), _text_round("0")])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m32", ts=1032.0,
    )
    asyncio.run(session._auto_reply(uid, msg))
    assert len(_self_emoji_rows(uid)) == 1


def test_send_prefetch_emoji_retries_another_on_send_failure(temp_db, tmp_path, monkeypatch):
    class FlakyDriver(StubDriver):
        def __init__(self):
            super().__init__()
            self.fail_first = True

        def send_emoji(self, chat_id, path):
            if self.fail_first:
                self.fail_first = False
                raise RuntimeError("模拟发送失败")
            super().send_emoji(chat_id, path)

    session = AccountSession({"id": "acc_a", "name": "A"}, _cfg(), FakeLLM([]))
    session.driver = FlakyDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row1 = db.add_emoji("acc_a", "local://r1", "7" * 32, 100, 100)
    row2 = db.add_emoji("acc_a", "local://r2", "6" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row1["id"], "开心", "")
    db.set_emoji_tags("acc_a", row2["id"], "开心", "")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    async def pick_row(*args, **kwargs):
        return dict(row1)

    monkeypatch.setattr(session, "_pick_reply_emoji", pick_row)
    monkeypatch.setattr(manager_mod, "_fallback_reply_emoji", lambda aid, u: dict(row2))
    chat = db.get_chat(uid)
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m33", ts=1033.0,
    )
    ok = asyncio.run(
        session._send_prefetch_emoji(
            None, chat, [], msg, "在的呀", None, uid, False, fallback=True
        )
    )
    assert ok is True
    assert len(session.driver.sent_emoji) == 1
    assert row1["md5"] not in session.driver.sent_emoji[0][1]  # 换了一张


# ---------------------------------------------------------------- 结尾表情节奏
async def _run_round(session, uid, msg):
    await session._auto_reply(uid, msg)


def _emoji_cfg(**send) -> dict:
    cfg = _cfg()()
    cfg["send"] = {
        "trailing_emoji_gap_sec": [0, 0],
        "trailing_emoji_cooldown_sec": 0,
    }
    cfg["send"].update(send)
    return cfg


def test_trailing_emoji_waits_for_quiet_gap(temp_db, tmp_path, monkeypatch):
    """正文发完后不立刻跟图：先等安静期，安静期到了才发。"""
    cfg = _emoji_cfg(trailing_emoji_gap_sec=[0.08, 0.08])
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: cfg, FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://gap", "5" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "开心", "")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0.08)
    session.llm = FakeLLM([_text_round("在的呀"), _text_round(str(row["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m40", ts=1040.0,
    )

    async def scenario():
        await session._auto_reply(uid, msg)
        immediate = list(session.driver.sent_emoji)
        await asyncio.sleep(0.6)
        return immediate, list(session.driver.sent_emoji)

    immediate, after = asyncio.run(scenario())
    assert immediate == []          # 安静期内先不发
    assert len(after) == 1          # 安静期结束补上


def test_trailing_emoji_cancelled_when_conversation_continues(temp_db, tmp_path, monkeypatch):
    """安静期里对方又接话（新一轮开始）→ 这次结尾表情作废，不跟图。"""
    cfg = _emoji_cfg(trailing_emoji_gap_sec=[0.15, 0.15])
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: cfg, FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://gap2", "4" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "开心", "")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0.15)
    session.llm = FakeLLM([_text_round("在的呀"), _text_round(str(row["id"]))])
    msg = Message(
        chat_id="c1", chat_name="会话一", sender_name="对方",
        content="在吗", msg_type="text", msg_id="m41", ts=1041.0,
    )

    async def scenario():
        await session._auto_reply(uid, msg)
        # 模拟对方紧接着又发一条 → 新一轮回复开始，序列号 +1
        session._emoji_seq[uid] = session._emoji_seq.get(uid, 0) + 1
        await asyncio.sleep(0.4)

    asyncio.run(scenario())
    assert session.driver.sent_emoji == []


def test_trailing_emoji_cooldown_suppresses_repeat(temp_db, tmp_path, monkeypatch):
    """同一会话冷却期内第二轮不再跟图（一句一个太多）。"""
    cfg = _emoji_cfg(trailing_emoji_cooldown_sec=300)
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: cfg, FakeLLM([]))
    session.driver = StubDriver()
    uid = db.upsert_chat("acc_a", "c1", "会话一", False, "在吗", 1000.0)
    row = db.add_emoji("acc_a", "local://cool", "3" * 32, 100, 100)
    db.set_emoji_tags("acc_a", row["id"], "开心", "")

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / f"{md5 or 'x'}.gif"
        path.write_bytes(b"GIF89a" + b"\x00" * 10)
        return path

    monkeypatch.setattr(emoji, "fetch_emoji", fake_fetch)
    monkeypatch.setattr(manager_mod.random, "uniform", lambda a, b: 0)

    def _round(mid, text):
        session.llm = FakeLLM([_text_round(text), _text_round(str(row["id"]))])
        return Message(
            chat_id="c1", chat_name="会话一", sender_name="对方",
            content=text, msg_type="text", msg_id=mid, ts=1042.0,
        )

    asyncio.run(session._auto_reply(uid, _round("m42", "在的呀")))
    assert len(session.driver.sent_emoji) == 1
    asyncio.run(session._auto_reply(uid, _round("m43", "好的呀")))
    assert len(session.driver.sent_emoji) == 1  # 冷却期内不再跟图
