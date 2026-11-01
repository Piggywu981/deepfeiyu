"""拟人化自动回复：聚拢窗口、习惯镜像、文字先发表情垫后。"""
import asyncio
import time
import uuid

import pytest

from app import config
from app import db
from app import emoji
from app.config import DEFAULTS
from app.wechat.base import DriverError, Message
from app.wechat.manager import AccountSession

EMOJI_MD5 = "b" * 32
EMOJI_XML = f'<msg><emoji cdnurl="https://cdn/x" md5="{EMOJI_MD5}"/></msg>'


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


def _human_cfg() -> dict:
    """测试用完整配置：节流间隔缩短到可 sleep 的量级，其余功能关闭隔离变量。"""
    return {
        "send": {
            "enabled": True,
            "min_gap_sec": [0, 0],
            "chat_gap_sec": [30, 30],
            "burst_gap_sec": [0.03, 0.05],
            "trailing_emoji_gap_sec": [0, 0],
            "trailing_emoji_cooldown_sec": 0,
        },
        "ai": {
            "vision_enabled": False,
            "emoji_auto_collect": False,
            "emoji_auto_tag": False,
            "memory_enabled": False,
            "persona_name": "鱼",
        },
        "auto": {
            "enabled": True,
            "group_need_at": True,
            "emoji_reply": True,
            "only_whitelist": False,
            "whitelist": [],
            "blacklist": [],
            "min_interval_sec": 0,
            "daily_limit": 0,
            "working_hours": {"enabled": False},
            "history_context": 12,
            "max_reply_chars": 300,
            "reply_delay_sec": [0.05, 0.08],
            "max_burst_wait_sec": 0.5,
        },
    }


class _LLM:
    configured = True


class FakeDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.fail = fail

    def send_text(self, chat_id: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("text", text, time.monotonic()))

    def send_emoji(self, chat_id: str, path: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("emoji", path, time.monotonic()))


def _session(cfg: dict, llm=None) -> AccountSession:
    return AccountSession({"id": "acc_x", "name": "A", "nickname": "B"}, lambda: cfg, llm)


def _db_msg(
    uid: str,
    chat_id: str,
    sender_id: str,
    sender_name: str,
    content: str,
    ts: float,
    msg_type: str = "text",
    is_group: bool = False,
    meta: dict | None = None,
) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "account_id": "acc_x",
        "chat_uid": uid,
        "chat_id": chat_id,
        "chat_name": "会话",
        "sender_id": sender_id,
        "sender_name": sender_name,
        "content": content,
        "msg_type": msg_type,
        "is_group": is_group,
        "is_self": False,
        "source": "wechat",
        "ts": ts,
        "meta": meta or {},
    }


def _text_msg(msg_id: str = "t1") -> Message:
    return Message(
        chat_id="wxid_friend",
        chat_name="好友",
        sender_id="wxid_friend",
        sender_name="好友",
        content="帮我看看",
        msg_id=msg_id,
    )


class FakeAgent:
    """替身 Agent：可选登记一个表情再产出文本，驱动 _auto_reply 的各分支。"""

    behavior: dict = {}

    def __init__(self, llm, tools, max_rounds: int = 5) -> None:
        self.tools = {t.name: t for t in tools}

    async def run(self, messages, on_event=None, **kw):
        b = FakeAgent.behavior
        hook = b.get("during")  # 生成过程中注入事件（如再来一条消息），此时会话锁正被占着
        if hook:
            await hook()
        # 合并后「回复必带表情包」兜底会用空工具列表再跑一次 Agent，没有表情工具就跳过登记
        if b.get("emoji_id") and "send_wechat_emoji" in self.tools:
            await self.tools["send_wechat_emoji"].handler({"emoji_id": b["emoji_id"]})
        return {"text": b.get("text", ""), "usage": {}}


def test_default_config_has_humanization_keys():
    assert DEFAULTS["auto"]["reply_delay_sec"] == [2, 4]
    assert DEFAULTS["auto"]["max_burst_wait_sec"] == 10
    assert DEFAULTS["auto"]["instant_reply"] is False
    assert DEFAULTS["send"]["burst_gap_sec"] == [1, 3]


def test_find_emoji_by_md5(temp_db):
    db.add_emoji("acc_x", "https://cdn/1.gif", "a" * 32)
    assert db.find_emoji_by_md5("acc_x", "a" * 32)["url"] == "https://cdn/1.gif"
    assert db.find_emoji_by_md5("acc_x", "") is None  # 空 md5 直接 None，不查全表
    assert db.find_emoji_by_md5("other_acc", "a" * 32) is None  # 账号隔离


def test_history_content_renders_tags(temp_db):
    row = db.add_emoji("acc_x", "https://cdn/2.gif", EMOJI_MD5)
    db.update_emoji_tags("acc_x", row["id"], "猫,无语,加班,困,饿")
    msg_row = {"msg_type": "emoji", "content": "[表情]", "meta": {"emoji": {"md5": EMOJI_MD5}}}
    assert emoji.history_content("acc_x", msg_row) == "[表情包:猫、无语、加班、困]"


def test_history_content_fallbacks(temp_db):
    acc = "acc_x"
    assert emoji.history_content(acc, {"msg_type": "text", "content": "在吗", "meta": {}}) == "在吗"
    no_md5 = {"msg_type": "emoji", "content": "[表情]", "meta": {"emoji": {}}}
    assert emoji.history_content(acc, no_md5) == "[表情]"
    no_row = {"msg_type": "emoji", "content": "[表情]", "meta": {"emoji": {"md5": "c" * 32}}}
    assert emoji.history_content(acc, no_row) == "[表情]"
    untagged = {"msg_type": "emoji", "content": "[表情]", "meta": {"emoji": {"md5": "d" * 32}}}
    db.add_emoji(acc, "https://cdn/3.gif", "d" * 32)  # 在库但没打过标
    assert emoji.history_content(acc, untagged) == "[表情]"


def test_get_chat_history_renders_tags(temp_db):
    uid = uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    row = db.add_emoji("acc_x", "https://cdn/2.gif", EMOJI_MD5)
    db.update_emoji_tags("acc_x", row["id"], "猫,无语")
    db.add_message(
        _db_msg(uid, "wxid_friend", "wxid_friend", "好友", "[表情]", time.time(),
                msg_type="emoji", meta={"emoji": {"md5": EMOJI_MD5}})
    )

    class Sess:
        account_id = "acc_x"

        async def resolve_chat(self, chat):
            return {"uid": uid, "name": "好友", "chat_id": "wxid_friend"}

    from app.ai.tools import build_tools

    tool = {t.name: t for t in build_tools(Sess(), allow_send=False, chat_uid=uid)}["get_chat_history"]
    out = asyncio.run(tool.handler({"chat": "好友"}))
    assert out["messages"][0]["text"] == "[表情包:猫、无语]"


def test_sender_emoji_habit_counts_and_trailing(temp_db):
    uid = db.upsert_chat("acc_x", "room1", "群", True)
    now = time.time()
    for i in range(6):  # 小王的闲聊文字
        db.add_message(_db_msg(uid, "room1", "wxid_wang", "小王", f"闲聊{i}", now - 300 + i * 40))
    for k in range(3):  # 文字后 5 秒跟一张表情
        base = now - 100 + k * 10
        db.add_message(_db_msg(uid, "room1", "wxid_wang", "小王", f"说事{k}", base))
        db.add_message(_db_msg(uid, "room1", "wxid_wang", "小王", "[表情]", base + 5, msg_type="emoji"))
    for i in range(8):  # 小李纯文字
        db.add_message(_db_msg(uid, "room1", "wxid_li", "小李", f"正文{i}", now - 50 + i))

    wang = db.sender_emoji_habit(uid, sender_id="wxid_wang")
    assert wang["total"] == 12  # 6 条闲聊 + 3 组「文字+表情」
    assert wang["emoji_n"] == 3
    assert wang["trailing_n"] == 3
    assert abs(wang["emoji_share"] - 0.25) < 1e-9
    assert abs(wang["trailing_share"] - 1.0) < 1e-9
    assert abs(db.sender_emoji_habit(uid, sender_id="wxid_li")["emoji_share"]) < 1e-9
    assert db.sender_emoji_habit(uid, sender_id="wxid_nobody") is None  # 样本不足


def test_sender_emoji_habit_private_without_sender(temp_db):
    uid = db.upsert_chat("acc_x", "wxid_a", "朋友", False)
    now = time.time()
    for i in range(10):
        db.add_message(_db_msg(uid, "wxid_a", "wxid_a", "朋友", "hello", now - 100 + i))
    h = db.sender_emoji_habit(uid)  # 私聊不传 sender_id，统计对方全部消息
    assert h["total"] == 10 and h["emoji_n"] == 0


def test_sender_emoji_habit_trailing_requires_text_within_30s(temp_db):
    uid = db.upsert_chat("acc_x", "wxid_b", "朋友", False)
    now = time.time()
    for i in range(8):  # 凑够样本数（<8 条返回 None）
        db.add_message(_db_msg(uid, "wxid_b", "wxid_b", "朋友", f"正文{i}", now - 300 + i))
    db.add_message(_db_msg(uid, "wxid_b", "wxid_b", "朋友", "说事", now - 120))
    db.add_message(_db_msg(uid, "wxid_b", "wxid_b", "朋友", "[表情]", now - 60, msg_type="emoji"))  # 距离 60s，不算跟发
    h = db.sender_emoji_habit(uid)
    assert h["total"] == 10
    assert h["emoji_n"] == 1 and h["trailing_n"] == 0


def test_send_emoji_tool_defers_when_sink_given(temp_db):
    uid = uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    row = db.add_emoji("acc_x", "https://cdn/x", EMOJI_MD5)

    class Sess:
        account_id = "acc_x"
        sent: list[int] = []

        async def resolve_chat(self, chat):
            return {"uid": uid, "name": "好友", "chat_id": "wxid_friend"}

        async def send_emoji(self, uid_, emoji_row, source="human"):
            self.sent.append(emoji_row["id"])

    from app.ai.tools import build_tools

    sess = Sess()
    sink: list[dict] = []
    tool = {t.name: t for t in build_tools(sess, allow_send=False, chat_uid=uid, defer_emoji=sink)}[
        "send_wechat_emoji"
    ]
    res = asyncio.run(tool.handler({"emoji_id": row["id"]}))
    assert res["deferred"] is True
    assert sink == [{"uid": uid, "emoji": db.get_emoji("acc_x", row["id"]), "to": "好友"}]
    assert sess.sent == []  # 登记模式不直发

    tool2 = {t.name: t for t in build_tools(sess, allow_send=False, chat_uid=uid)}["send_wechat_emoji"]
    res2 = asyncio.run(tool2.handler({"emoji_id": row["id"]}))
    assert res2["ok"] is True and sess.sent == [row["id"]]  # 无 sink 时行为不变


def _incoming(content: str, msg_id: str, msg_type: str = "text") -> Message:
    return Message(
        chat_id="wxid_friend",
        chat_name="好友",
        sender_id="wxid_friend",
        sender_name="好友",
        content=content,
        msg_type=msg_type,
        msg_id=msg_id,
    )


def test_burst_merges_into_single_reply(temp_db, monkeypatch):
    session = _session(_human_cfg(), llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    calls: list[tuple[str, str, list[str]]] = []

    async def fake_reply(u, msg, burst_msgs=None):
        calls.append((u, msg.msg_id, [m.msg_id for m in (burst_msgs or [])]))

    monkeypatch.setattr(session, "_auto_reply", fake_reply)

    async def flow():
        await session.handle_message(_incoming("笑死", "m1"))
        await session.handle_message(_incoming(EMOJI_XML, "m2", msg_type="emoji"))
        await asyncio.sleep(0.4)

    asyncio.run(flow())
    assert len(calls) == 1  # 两连发只回一次
    assert calls[0][1] == "m2"  # 以最后一条为触发
    assert calls[0][2] == ["m1", "m2"]  # 整轮连发都在 burst 里


def test_fire_reply_rechecks_rules(temp_db, monkeypatch):
    session = _session(_human_cfg(), llm=_LLM())
    session.driver = FakeDriver()
    uid = uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    calls: list[str] = []

    async def fake_reply(u, msg, burst_msgs=None):
        calls.append(u)

    monkeypatch.setattr(session, "_auto_reply", fake_reply)

    async def flow():
        await session.handle_message(_incoming("在吗", "m1"))
        db.set_chat_pause(uid, time.time() + 60)  # 窗口期内人工接管
        await asyncio.sleep(0.4)

    asyncio.run(flow())
    assert calls == []  # 触发前重过规则检查，被拦截


def test_burst_wait_capped_by_max_burst_wait(temp_db, monkeypatch):
    cfg = _human_cfg()
    cfg["auto"]["reply_delay_sec"] = [5, 5]  # 窗口本身很长
    cfg["auto"]["max_burst_wait_sec"] = 0.3
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    calls: list[str] = []

    async def fake_reply(u, msg, burst_msgs=None):
        calls.append(msg.msg_id)

    monkeypatch.setattr(session, "_auto_reply", fake_reply)

    async def flow():
        await session.handle_message(_incoming("第一句", "m1"))
        await asyncio.sleep(0.1)
        await session.handle_message(_incoming("第二句", "m2"))  # 刷新窗口，若无上限要等 5s
        await asyncio.sleep(0.6)

    asyncio.run(flow())
    assert calls == ["m2"]  # 兜底触发，且取最新一帧


def test_blocked_message_does_not_refresh_window(temp_db, monkeypatch):
    cfg = _human_cfg()
    cfg["auto"]["emoji_reply"] = False  # 表情消息会被规则拦截
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    calls: list[tuple[str, list[str]]] = []

    async def fake_reply(u, msg, burst_msgs=None):
        calls.append((msg.msg_id, [m.msg_id for m in (burst_msgs or [])]))

    monkeypatch.setattr(session, "_auto_reply", fake_reply)

    async def flow():
        await session.handle_message(_incoming("在吗", "m1"))
        await session.handle_message(_incoming(EMOJI_XML, "m2", msg_type="emoji"))  # 规则拦截
        await asyncio.sleep(0.4)

    asyncio.run(flow())
    assert calls == [("m1", ["m1"])]  # 被拦截的消息既不刷新窗口也不进 burst


def test_message_during_running_turn_is_queued_not_dropped(temp_db, monkeypatch):
    """正文还在生成就来了新消息：不能一把扔了（表现成"查完不回"），本轮发完要补一轮。"""
    cfg = _human_cfg()
    cfg["auto"]["instant_reply"] = True
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)

    seen: list[list[str]] = []
    real_run = session._auto_reply_run

    async def spy(u, msg, burst_msgs=None):
        lock = session._locks.get(u)
        if lock is not None and lock.locked():
            await real_run(u, msg, burst_msgs)  # 被占锁：只记录进队列，不算一轮
            return
        seen.append([m.content for m in (burst_msgs or [msg])])
        await real_run(u, msg, burst_msgs)

    fired: list[int] = []

    async def during() -> None:
        if fired:
            return  # 只在第一轮生成过程中插入新消息，补跑轮不该再续上一条
        fired.append(1)
        await session._auto_reply(uid, _incoming("第二条", "mq1"))

    FakeAgent.behavior = {"text": "收到", "during": during}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)
    monkeypatch.setattr(session, "_auto_reply_run", spy)
    async def flow():
        await session._auto_reply(uid, _incoming("第一条", "mp1"))
        await asyncio.sleep(0.05)  # 等收尾补跑的那一轮

    asyncio.run(flow())

    assert [s[0] for s in seen] == ["第一条", "第二条"]  # 第二条被排队补跑
    assert session._queued.get(uid) in (None, [])  # 排空，不留到下一轮


def test_queued_message_rechecks_rules_before_replying(temp_db, monkeypatch):
    """补跑前要重过一遍规则：期间对方接管/关掉 AI 就不该再张嘴。"""
    cfg = _human_cfg()
    cfg["auto"]["instant_reply"] = True
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)

    seen: list[str] = []
    real_run = session._auto_reply_run

    async def spy(u, msg, burst_msgs=None):
        lock = session._locks.get(u)
        if lock is None or not lock.locked():
            seen.append(msg.content)  # 只算真正开跑的轮次
        await real_run(u, msg, burst_msgs)

    fired: list[int] = []

    async def during() -> None:
        if fired:
            return
        fired.append(1)
        db.set_chat_pause(uid, time.time() + 60)  # 生成期间人工接管
        await session._auto_reply(uid, _incoming("第二条", "mq2"))

    FakeAgent.behavior = {"text": "收到", "during": during}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)
    monkeypatch.setattr(session, "_auto_reply_run", spy)
    async def flow():
        await session._auto_reply(uid, _incoming("第一条", "mp2"))
        await asyncio.sleep(0.05)

    asyncio.run(flow())

    assert seen == ["第一条"]  # 攒下的那条压根不开轮：补跑前就被规则拦下
    assert len(session.driver.calls) == 1  # 接管中，不该再张嘴
    assert session._queued.get(uid) in (None, [])
    events = db.list_events("acc_x", limit=10)
    assert any("补跑前被拦下" in (e.get("title") or "") for e in events)


def test_stale_queued_message_is_dropped(temp_db, monkeypatch):
    """排太久的积压别在几分钟后突然冒出来：过期直接丢，只留还得回的。"""
    cfg = _human_cfg()
    cfg["auto"]["instant_reply"] = True
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)

    stale = _incoming("陈年旧消息", "mq3")
    stale.ts = time.time() - 4000
    seen: list[str] = []
    real_run = session._auto_reply_run

    async def spy(u, msg, burst_msgs=None):
        lock = session._locks.get(u)
        if lock is None or not lock.locked():
            seen.append(msg.content)  # 只算真正开跑的轮次
        await real_run(u, msg, burst_msgs)

    fired: list[int] = []

    async def during() -> None:
        if fired:
            return
        fired.append(1)
        session._queued.setdefault(uid, []).append(stale)  # 直接塞一条过期的

    FakeAgent.behavior = {"text": "收到", "during": during}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)
    monkeypatch.setattr(session, "_auto_reply_run", spy)
    async def flow():
        await session._auto_reply(uid, _incoming("第一条", "mp3"))
        await asyncio.sleep(0.05)

    asyncio.run(flow())

    assert seen == ["第一条"]
    assert len(session.driver.calls) == 1
    assert session._queued.get(uid) in (None, [])


def test_addressed_reply_waits_only_short_cooldown(temp_db):
    """私聊连发：短冷却生效，第二条不用等同会话 30s 长冷却。"""

    cfg = _human_cfg()
    cfg["send"]["min_gap_sec"] = [0, 0]
    cfg["send"]["chat_gap_sec"] = [30, 30]
    cfg["send"]["direct_gap_sec"] = [0.05, 0.06]
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)

    async def flow() -> float:
        await session.send_text(uid, "第一条", source="agent", direct=True)
        t0 = time.monotonic()
        await session.send_text(uid, "第二条", source="agent", direct=True)
        return time.monotonic() - t0

    assert asyncio.run(flow()) < 1.0
    assert [c[1] for c in session.driver.calls] == ["第一条", "第二条"]


def _ai_outbound_row(uid: str, chat_id: str, content: str, ts: float, is_group: bool = False) -> dict:
    """造一条「AI 自己发出去」的记录（_db_msg 默认是入站，这里改两个字段）。"""
    row = _db_msg(uid, chat_id, "self", "我", content, ts, is_group=is_group)
    row["is_self"] = True
    row["source"] = "agent"
    return row


def test_group_follow_up_uses_short_cooldown(temp_db):
    """群里没 @ 我，但 AI 半分钟前刚接过话：这轮算来回对话，走短冷却。"""
    cfg = _human_cfg()
    cfg["send"]["min_gap_sec"] = [0, 0]
    cfg["send"]["chat_gap_sec"] = [30, 30]
    cfg["send"]["direct_gap_sec"] = [0.05, 0.06]
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "room9@chatroom", "群", True)
    now = time.time()
    db.add_message(_ai_outbound_row(uid, "room9@chatroom", "上一条是我回的", now - 30, is_group=True))
    db.add_message(_db_msg(uid, "room9@chatroom", "wxid_w", "小王", "接着聊", now - 5, is_group=True))
    msg = Message(
        chat_id="room9@chatroom", chat_name="群", sender_id="wxid_w", sender_name="小王",
        content="再来一张", is_group=True, msg_id="mf1",
    )
    assert session._quick_reply_warranted(uid, msg) is True

    async def flow() -> float:
        await session.send_text(uid, "接住", source="agent", direct=True)
        t0 = time.monotonic()
        await session.send_text(uid, "再接", source="agent", direct=True)
        return time.monotonic() - t0

    assert asyncio.run(flow()) < 1.0  # 没被 30s 长冷却按住


def test_cold_group_thread_keeps_long_cooldown(temp_db):
    """AI 上一条已经是三分钟前：算重新插话，仍按长冷却预订槽位。"""
    cfg = _human_cfg()
    cfg["send"]["min_gap_sec"] = [0, 0]
    cfg["send"]["chat_gap_sec"] = [30, 30]
    cfg["send"]["direct_gap_sec"] = [0.05, 0.06]
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "room9@chatroom", "群", True)
    now = time.time()
    db.add_message(_ai_outbound_row(uid, "room9@chatroom", "早就说完收工了", now - 200, is_group=True))
    db.add_message(_db_msg(uid, "room9@chatroom", "wxid_w", "小王", "突然冒一句", now - 5, is_group=True))
    msg = Message(
        chat_id="room9@chatroom", chat_name="群", sender_id="wxid_w", sender_name="小王",
        content="在吗", is_group=True, msg_id="mf2",
    )
    assert session._quick_reply_warranted(uid, msg) is False


def test_group_chatter_still_books_long_cooldown(temp_db):
    """没 @ 我的群闲聊仍按长冷却预订槽位，短冷却不能变成全局加速。"""
    cfg = _human_cfg()
    cfg["send"]["min_gap_sec"] = [0, 0]
    cfg["send"]["chat_gap_sec"] = [30, 30]
    cfg["send"]["direct_gap_sec"] = [0.05, 0.06]
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "room1@chatroom", "群", True)

    asyncio.run(session.send_text(uid, "冒一句", source="agent", direct=False))
    reserved = session._throttle._next_chat["room1@chatroom"] - time.monotonic()
    assert reserved > 25  # 长冷却照旧，而不是被 direct_gap 拖短


def test_instant_reply_skips_window(temp_db, monkeypatch):
    cfg = _human_cfg()
    cfg["auto"]["instant_reply"] = True
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)  # upsert 默认关 AI，不开则入站规则直接拦截
    calls: list[tuple[str, list[str]]] = []

    async def fake_reply(u, msg, burst_msgs=None):
        calls.append((msg.msg_id, [m.msg_id for m in (burst_msgs or [])]))

    monkeypatch.setattr(session, "_auto_reply", fake_reply)

    async def flow():
        await session.handle_message(_incoming("笑死", "m1"))
        await session.handle_message(_incoming(EMOJI_XML, "m2", msg_type="emoji"))
        await asyncio.sleep(0.05)

    asyncio.run(flow())
    assert [c[0] for c in calls] == ["m1", "m2"]  # 收到即回，不再等聚拢窗口
    assert all(len(c[1]) == 1 for c in calls)  # 每次只回当条，后续消息进不了本轮 burst
    assert session._pending == {}  # 不留待回复窗口


def test_auto_reply_sends_text_then_emoji(temp_db, tmp_path, monkeypatch):
    session = _session(_human_cfg(), llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)
    row = db.add_emoji("acc_x", "https://cdn/x", EMOJI_MD5)
    FakeAgent.behavior = {"emoji_id": row["id"], "text": "哈哈行"}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)
    gif = tmp_path / "e.gif"
    gif.write_bytes(b"GIF89a")
    monkeypatch.setattr("app.emoji.fetch_emoji", lambda url, md5="", timeout=15.0: str(gif))

    asyncio.run(session._auto_reply(uid, _text_msg()))

    kinds = [c[0] for c in session.driver.calls]
    assert kinds == ["text", "emoji"]  # 文字在前，表情垫后
    gap = session.driver.calls[1][2] - session.driver.calls[0][2]
    assert 0.02 <= gap < 1.0  # burst 短间隔，而不是 30s 的同会话冷却


def test_skip_with_registered_emoji_sends_emoji_only(temp_db, tmp_path, monkeypatch):
    session = _session(_human_cfg(), llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)
    row = db.add_emoji("acc_x", "https://cdn/x", EMOJI_MD5)
    FakeAgent.behavior = {"emoji_id": row["id"], "text": "[SKIP]"}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)
    gif = tmp_path / "e.gif"
    gif.write_bytes(b"GIF89a")
    monkeypatch.setattr("app.emoji.fetch_emoji", lambda url, md5="", timeout=15.0: str(gif))

    emoji_msg = _incoming(EMOJI_XML, "m9", msg_type="emoji")
    asyncio.run(session._auto_reply(uid, emoji_msg))

    kinds = [c[0] for c in session.driver.calls]
    assert kinds == ["emoji"]  # 以表情回表情：不输出文字
    events = db.list_events("acc_x", limit=10)
    assert any("仅表情" in (e.get("title") or "") for e in events)  # 新代码的「仅表情」事件（旧实现记的是"无需回复"）


def test_flush_failure_does_not_lose_text(temp_db, monkeypatch):
    session = _session(_human_cfg(), llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)
    row = db.add_emoji("acc_x", "https://cdn/x", EMOJI_MD5)
    FakeAgent.behavior = {"emoji_id": row["id"], "text": "好的"}
    monkeypatch.setattr("app.wechat.manager.Agent", FakeAgent)

    async def broken_send_emoji(uid_, emoji_row, source="human", burst=False):
        raise DriverError("驱动不支持发表情")

    monkeypatch.setattr(session, "send_emoji", broken_send_emoji)

    asyncio.run(session._auto_reply(uid, _text_msg()))

    assert [c[0] for c in session.driver.calls] == ["text"]  # 文字已发出
    events = db.list_events("acc_x", limit=10)
    assert any("表情发送失败" in (e.get("title") or "") for e in events)  # 失败有事件可查


def test_prompt_habit_hint_tiers():
    cfg = _human_cfg()
    cfg["ai"]["emoji_with_reply"] = False  # 习惯分级提示只在「回复必带表情包」关闭时生效
    session = _session(cfg, llm=_LLM())
    high = {"total": 20, "emoji_n": 10, "trailing_n": 6, "emoji_share": 0.5, "trailing_share": 0.6}
    prompt = session._build_prompt(cfg, {"name": "小王"}, [], _text_msg(), [], habit=high)
    assert "很爱发表情包" in prompt[0]["content"]
    assert "跟一个表情包" in prompt[0]["content"]

    low = {"total": 20, "emoji_n": 1, "trailing_n": 0, "emoji_share": 0.05, "trailing_share": 0.0}
    prompt = session._build_prompt(cfg, {"name": "老陈"}, [], _text_msg(), [], habit=low)
    assert "几乎不发表情包" in prompt[0]["content"]

    mid = {"total": 20, "emoji_n": 4, "trailing_n": 1, "emoji_share": 0.2, "trailing_share": 0.25}
    prompt = session._build_prompt(cfg, {"name": "小李"}, [], _text_msg(), [], habit=mid)
    assert "偶尔发表情包" in prompt[0]["content"]


def test_prompt_habit_hint_neutral_without_data():
    cfg = _human_cfg()
    cfg["ai"]["emoji_with_reply"] = False
    session = _session(cfg, llm=_LLM())
    prompt = session._build_prompt(cfg, {"name": "朋友"}, [], _text_msg(), [], habit=None)
    assert "很爱发表情包" not in prompt[0]["content"]
    assert "几乎不发表情包" not in prompt[0]["content"]
    assert "search_emojis" in prompt[0]["content"]  # 表情包库能力介绍保留


def test_prompt_history_renders_emoji_tags(temp_db):
    session = _session(_human_cfg(), llm=_LLM())
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    row = db.add_emoji("acc_x", "https://cdn/2.gif", EMOJI_MD5)
    db.update_emoji_tags("acc_x", row["id"], "猫,无语")
    db.add_message(
        _db_msg(uid, "wxid_friend", "wxid_friend", "好友", "[表情]", time.time() - 10,
                msg_type="emoji", meta={"emoji": {"md5": EMOJI_MD5}})
    )
    history = db.list_messages(uid, limit=5)
    prompt = session._build_prompt(_human_cfg(), {"name": "好友", "uid": uid}, history, _text_msg(), [])
    assert any(
        "[表情包:猫、无语]" in m.get("content")
        for m in prompt
        if isinstance(m.get("content"), str)
    )


def test_prompt_placeholder_tags_without_vision(temp_db):
    session = _session(_human_cfg(), llm=_LLM())  # vision_enabled=False
    db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    row = db.add_emoji("acc_x", "https://cdn/2.gif", EMOJI_MD5)
    db.update_emoji_tags("acc_x", row["id"], "猫,无语")
    emoji_msg = _incoming(EMOJI_XML, "m8", msg_type="emoji")
    prompt = session._build_prompt(_human_cfg(), {"name": "好友"}, [], emoji_msg, [])
    assert "参考标签理解" in prompt[-1]["content"]
    assert "猫、无语" in prompt[-1]["content"]


def test_auto_reply_injects_habit_into_system(temp_db, monkeypatch):
    cfg = _human_cfg()
    cfg["ai"]["emoji_with_reply"] = False  # 习惯分级提示只在「回复必带表情包」关闭时生效
    session = _session(cfg, llm=_LLM())
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    db.set_chat_ai(uid, True)
    now = time.time()
    for i in range(8):
        db.add_message(
            _db_msg(uid, "wxid_friend", "wxid_friend", "好友", "[表情]", now - 100 + i, msg_type="emoji")
        )
    seen_system: list[str] = []

    class RecordingAgent(FakeAgent):
        async def run(self, messages, on_event=None, **kw):
            seen_system.append(messages[0]["content"])
            return {"text": "好", "usage": {}}

    monkeypatch.setattr("app.wechat.manager.Agent", RecordingAgent)
    asyncio.run(session._auto_reply(uid, _text_msg()))
    assert any("很爱发表情包" in s for s in seen_system)
