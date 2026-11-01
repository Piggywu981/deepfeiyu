"""主动搭话服务：掷骰闸门、试运行、生成发送、额度与并发。

全部使用假 Hub/假 session 与临时库，不接真实 Agent、LLM 或微信驱动；
时间与随机数由 ProactiveService 注入，测试结果可重复。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app import config, db
from app.proactive import engine, service as service_mod, store
from app.proactive.service import ProactiveService

AID = "acc_a"
UID = "acc_a:wxid_bob"
OTHER_UID = "acc_a:wxid_carol"
# 本地时间中午 12:00：避开默认安静时段 1–8，也避开峰时 14–18，结果不依赖运行机器时区
NOW = datetime(2026, 10, 29, 12, 0, 0).timestamp()
TODAY = engine.day_key(NOW)
TRIGGER = "好久没人跟我说话了，我该主动去打个招呼"


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


def _cfg(enabled: bool = True, account: dict | None = None,
         auto: dict | None = None) -> dict:
    return {
        "proactive": {"enabled": enabled, "accounts": {AID: dict(account or {})}},
        "auto": {
            "blacklist": [],
            "whitelist": [],
            "only_whitelist": False,
            **(auto or {}),
        },
    }


def _account(**over) -> dict:
    base = {"whitelist": [{"chat_uid": UID, "name": "Bob", "is_group": False}]}
    base.update(over)
    return base


def _add_chat(uid: str = UID, *, ai_enabled: bool = True, pause: float = 0.0,
              last_ts: float = NOW - 7200) -> None:
    account_id, _, chat_id = uid.partition(":")
    db.upsert_chat(account_id, chat_id, "Bob", False, ts=last_ts)
    db.set_chat_ai(uid, ai_enabled)
    if pause:
        db.set_chat_pause(uid, pause)


class FakeHub:
    def __init__(self) -> None:
        self.sessions: dict[str, object] = {}

    def get(self, account_id: str):
        return self.sessions.get(account_id)


class FakeSession:
    """只覆盖服务会用到的三个入口；所有调用都被记录下来供断言。"""

    def __init__(self) -> None:
        self.driver = object()
        self.llm = object()
        self.replies: list[object] = []
        self.turns: list[tuple[str, str, int]] = []
        self.previews: list[bool] = []
        self.sent: list[dict] = []
        self.flushed: list[list] = []
        self.send_error_at: int | None = None
        self.flush_error: Exception | None = None

    async def run_proactive_turn(self, uid: str, trigger_text: str,
                                 max_chars: int = 200, preview: bool = False) -> dict:
        self.turns.append((uid, trigger_text, max_chars))
        self.previews.append(preview)
        if not self.replies:
            return {"ok": True, "skip": False, "text": "在吗", "error": "", "emoji": []}
        item = self.replies.pop(0)
        if isinstance(item, BaseException):  # 含 CancelledError（BaseException 子类）
            raise item
        return item

    async def send_text(self, uid: str, text: str, source: str = "human",
                        meta: dict | None = None) -> dict:
        if self.send_error_at is not None and len(self.sent) + 1 == self.send_error_at:
            raise RuntimeError("发送失败")
        self.sent.append(
            {"uid": uid, "text": text, "source": source, "meta": dict(meta or {})}
        )
        return {"ok": True}

    async def flush_proactive_emoji(self, items: list) -> int:
        if self.flush_error:
            raise self.flush_error
        self.flushed.append(list(items))
        return len(items)


class Env:
    def __init__(self) -> None:
        self.cfg = _cfg(account=_account())
        self.account = {"id": AID, "name": "测试号"}
        self.hub = FakeHub()
        self.session = FakeSession()
        self.mood_calls: list[tuple[tuple, dict]] = []
        self.service = ProactiveService(self.hub, now=lambda: NOW, rng=lambda: 0.99)


@pytest.fixture()
def env(temp_db, monkeypatch):
    e = Env()
    monkeypatch.setattr(config, "load", lambda: e.cfg)
    monkeypatch.setattr(config, "account", lambda aid: e.account if aid == AID else None)

    def _observe(*args, **kwargs):
        e.mood_calls.append((args, kwargs))

    # 心情判断是同步旁路；默认替换成记录器，避免测试碰到真实模型/队列
    monkeypatch.setattr(service_mod.mood_service, "observe", _observe)
    return e


def _setup(env: Env, *, account: dict | None = None, with_session: bool = True,
           create_chat: bool = True, **chat_kw) -> None:
    env.cfg = _cfg(account=account or _account())
    if with_session:
        env.hub.sessions[AID] = env.session
    if create_chat:
        _add_chat(**chat_kw)


def _ready(env: Env, *, account: dict | None = None, auto: dict | None = None,
           **chat_kw) -> None:
    env.cfg = _cfg(account=account or _account(), auto=auto)
    env.hub.sessions[AID] = env.session
    _add_chat(**chat_kw)


def _roll(env: Env, entry: dict | None = None) -> dict:
    return asyncio.run(env.service.maybe_roll(AID, entry or {"chat_uid": UID}))


# ---------------------------------------------------------------- maybe_roll 拦截


def test_maybe_roll_disabled_returns_and_writes_nothing(env):
    _setup(env)
    env.cfg = _cfg(enabled=False, account=_account())
    res = _roll(env)
    assert res["status"] == "disabled"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_invalid_entry(env):
    _setup(env)
    res = _roll(env, {"name": "缺少 chat_uid"})
    assert res["status"] == "invalid"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_offline_without_session_or_driver(env):
    _setup(env, with_session=False)
    assert _roll(env)["status"] == "offline"
    env.hub.sessions[AID] = env.session
    env.session.driver = None
    assert _roll(env)["status"] == "offline"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_missing_chat_or_foreign_account(env):
    # 先把 ghost/异账号放进白名单，确保拦截来自会话校验而不是白名单
    _setup(
        env,
        account=_account(
            whitelist=[
                {"chat_uid": UID},
                {"chat_uid": f"{AID}:ghost"},
                {"chat_uid": "acc_b:wxid_x"},
            ]
        ),
    )
    assert _roll(env, {"chat_uid": f"{AID}:ghost"})["status"] == "missing"

    db.upsert_chat("acc_b", "wxid_x", "X", False, ts=NOW - 7200)
    assert _roll(env, {"chat_uid": "acc_b:wxid_x"})["status"] == "missing"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_quiet_hours(env):
    _setup(env, account=_account(quiet_start=12, quiet_end=13))
    assert _roll(env)["status"] == "quiet"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_cooldown(env):
    _setup(env, account=_account(cooldown_min=20))
    store.record_sent(AID, UID, NOW - 60, TODAY)
    assert _roll(env)["status"] == "cooldown"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_interval_not_due(env):
    _setup(env, account=_account(interval_min=30))
    store.record_roll(AID, UID, NOW - 60, False)
    assert _roll(env)["status"] == "not_due"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_account_daily_limit(env):
    _setup(env, account=_account(daily_limit=1, per_chat_daily=0))
    # 另一会话已发过一条：账号总额度用完，但当前会话本身没有冷却
    store.record_sent(AID, OTHER_UID, NOW - 7200, TODAY)
    assert _roll(env)["status"] == "quota"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_per_chat_daily_limit(env):
    _setup(env, account=_account(daily_limit=0, per_chat_daily=1))
    store.record_sent(AID, UID, NOW - 7200, TODAY)
    assert _roll(env)["status"] == "quota"
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_maybe_roll_miss_records_streak_and_event(env):
    _setup(env, account=_account(hit_peak=0, hit_offpeak=0))
    env.service._rng = lambda: 0.99
    res = _roll(env)
    assert res["status"] == "miss"
    assert res["hit_line"] == 0.0
    assert res["roll"] == pytest.approx(99.0)

    state = store.get_state(AID, UID)
    assert state["misses"] == 1
    assert state["last_roll_at"] == NOW
    assert env.session.turns == [] and env.session.sent == []

    events = store.list_events(AID)
    assert len(events) == 1
    assert events[0]["status"] == "miss"
    assert events[0]["hit"] == 0
    assert events[0]["hit_line"] == 0.0
    assert events[0]["roll"] == pytest.approx(99.0)
    assert events[0]["trigger"] == "auto"
    assert events[0]["meta"] == {"hit_line": 0.0, "roll": pytest.approx(99.0)}


def test_maybe_roll_entry_not_whitelisted(env, monkeypatch):
    _setup(env)
    rolls: list[tuple] = []

    def spy(*args, **kwargs):
        rolls.append((args, kwargs))

    monkeypatch.setattr(service_mod.store, "record_roll", spy)
    res = _roll(env, {"chat_uid": OTHER_UID})
    assert res["status"] == "not_whitelisted"
    # 不掷骰、不写事件、不动状态
    assert rolls == []
    assert store.list_events(AID) == []
    assert store.get_state(AID, OTHER_UID)["last_roll_at"] == 0


def test_maybe_roll_hit_calls_send_one_once_with_preset(env):
    _setup(env, account=_account(hit_peak=0, hit_offpeak=100))
    env.service._rng = lambda: 0.0
    calls: list[dict] = []

    async def fake_send_one(account_id, chat_uid, trigger="manual",
                            ignore_quota=False, dry_run=False, preset=None):
        calls.append(
            {
                "account_id": account_id,
                "chat_uid": chat_uid,
                "trigger": trigger,
                "preset": preset,
            }
        )
        return {"ok": True, "status": "sent", "text": "嗨", "error": ""}

    env.service.send_one = fake_send_one
    res = _roll(env)
    assert res["status"] == "sent"
    assert len(calls) == 1
    assert calls[0]["account_id"] == AID
    assert calls[0]["chat_uid"] == UID
    assert calls[0]["trigger"] == "auto"
    assert calls[0]["preset"] == {"hit_line": 100.0, "roll": 0.0}

    state = store.get_state(AID, UID)
    assert state["last_roll_at"] == NOW
    assert state["misses"] == 0


# ---------------------------------------------------------------- send_one 闸门


def test_send_one_disabled_writes_skipped_event(env):
    _ready(env)
    env.cfg = _cfg(enabled=False, account=_account())
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert res["error"] == "主动搭话未开启"
    assert env.session.turns == [] and env.session.sent == []
    events = store.list_events(AID)
    assert len(events) == 1
    assert events[0]["status"] == "skipped"
    assert events[0]["error"] == "主动搭话未开启"


def test_send_one_uid_not_in_whitelist(env):
    _ready(env, account=_account(whitelist=[{"chat_uid": OTHER_UID}]))
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert "白名单" in res["error"]
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID)[0]["status"] == "skipped"


@pytest.mark.parametrize(
    "auto, chat_kw, reason",
    [
        (None, {"ai_enabled": False}, "该会话已关闭 AI"),
        (None, {"pause": NOW + 100}, "人工接管中，AI 暂停"),
        ({"blacklist": ["Bob"]}, {}, "命中黑名单"),
    ],
)
def test_send_one_real_rules_gates(env, auto, chat_kw, reason):
    _ready(env, auto=auto, **chat_kw)
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert res["error"] == reason
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID)[0]["status"] == "skipped"


def test_send_one_offline_channel(env):
    _ready(env)
    env.hub.sessions.clear()
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert res["error"] == "微信通道未启动"

    env.hub.sessions[AID] = env.session
    env.session.driver = None
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert res["error"] == "微信通道未启动"
    assert env.session.turns == [] and env.session.sent == []


def test_send_one_uid_not_owned_by_account(env):
    _ready(env)
    db.upsert_chat("acc_b", "wxid_x", "X", False, ts=NOW - 7200)
    # 白名单里也放上，确认拦截来自归属校验而不是白名单
    env.cfg = _cfg(account=_account(whitelist=[{"chat_uid": "acc_b:wxid_x"}]))
    res = asyncio.run(env.service.send_one(AID, "acc_b:wxid_x"))
    assert res["status"] == "skipped"
    assert env.session.turns == [] and env.session.sent == []


def test_send_one_quota_block_and_ignore_quota(env):
    _ready(env, account=_account(daily_limit=1, per_chat_daily=0))
    store.record_sent(AID, OTHER_UID, NOW - 7200, TODAY)
    blocked = asyncio.run(env.service.send_one(AID, UID))
    assert blocked["status"] == "skipped"
    assert blocked["error"] == "已达今日主动搭话上限"

    env.session.replies = [
        {"ok": True, "skip": False, "text": "测试不受额度", "error": "", "emoji": []}
    ]
    res = asyncio.run(
        env.service.send_one(AID, UID, trigger="test", ignore_quota=True)
    )
    assert res["status"] == "sent"
    assert env.session.sent[0]["text"] == "测试不受额度"


def test_send_one_manual_ignore_quota_still_blocked(env):
    """ignore_quota 只对 test 生效，manual 不能借此绕过日上限。"""
    _ready(env, account=_account(daily_limit=1, per_chat_daily=0))
    store.record_sent(AID, OTHER_UID, NOW - 7200, TODAY)
    res = asyncio.run(
        env.service.send_one(AID, UID, trigger="manual", ignore_quota=True)
    )
    assert res["status"] == "skipped"
    assert res["error"] == "已达今日主动搭话上限"
    assert env.session.turns == [] and env.session.sent == []


# ---------------------------------------------------------------- 试运行与生成


def test_preview_generates_without_sending(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "今晚月亮不错", "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.preview(AID, UID))
    assert res == {
        "ok": True,
        "status": "preview",
        "text": "今晚月亮不错",
        "error": "",
    }
    assert env.session.turns == [(UID, TRIGGER, 200)]
    assert env.session.sent == [] and env.session.flushed == []
    assert env.mood_calls == []

    state = store.get_state(AID, UID)
    assert state["sent_today"] == 0
    assert state["last_self_at"] == 0
    assert state["last_roll_at"] == 0

    events = store.list_events(AID)
    assert len(events) == 1
    assert events[0]["status"] == "preview"
    assert events[0]["message"] == "今晚月亮不错"
    assert events[0]["trigger"] == "test"


def test_send_one_skip_result_does_not_send(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": True, "text": "[SKIP]", "error": "", "emoji": [{"id": 1}]}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert env.session.sent == [] and env.session.flushed == []
    assert store.get_state(AID, UID)["sent_today"] == 0
    assert store.list_events(AID)[0]["status"] == "skipped"


def test_send_one_empty_text_is_skipped(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "   ", "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "skipped"
    assert env.session.sent == []


def test_send_one_generation_error(env):
    _ready(env)
    env.session.replies = [
        {"ok": False, "skip": False, "text": "", "error": "模型未配置", "emoji": []}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "error"
    assert res["error"] == "模型未配置"
    assert env.session.sent == [] and env.mood_calls == []
    state = store.get_state(AID, UID)
    assert state["sent_today"] == 0 and state["last_self_at"] == 0
    assert store.list_events(AID)[0]["status"] == "error"


def test_send_one_gate_rechecked_after_generation(env, monkeypatch):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "想问问你", "error": "", "emoji": []}
    ]
    checks: list[float] = []

    def fake_check(cfg, account, chat, now=None):
        checks.append(now)
        if len(checks) == 1:
            return True, "允许主动搭话"
        return False, "人工接管中，AI 暂停"

    monkeypatch.setattr(service_mod.rules, "check_proactive", fake_check)
    res = asyncio.run(env.service.send_one(AID, UID))
    assert len(checks) == 2
    assert res["status"] == "skipped"
    assert res["error"] == "人工接管中，AI 暂停"
    assert env.session.turns == [(UID, TRIGGER, 200)]  # 生成过一次
    assert env.session.sent == []  # 但发送前被拦下
    assert store.list_events(AID)[0]["status"] == "skipped"


# ---------------------------------------------------------------- 发送与记账


def test_send_one_success_records_everything(env):
    _ready(env)
    env.session.replies = [
        {
            "ok": True,
            "skip": False,
            "text": "嗨，在忙吗",
            "error": "",
            "emoji": [{"emoji_id": 1}],
        }
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res == {"ok": True, "status": "sent", "text": "嗨，在忙吗", "error": ""}
    assert env.session.sent == [
        {
            "uid": UID,
            "text": "嗨，在忙吗",
            "source": "agent",
            "meta": {"proactive": True, "trigger": "manual"},
        }
    ]
    assert env.session.flushed == [[{"emoji_id": 1}]]
    assert (AID, UID) not in env.service._running

    state = store.get_state(AID, UID)
    assert state["sent_today"] == 1
    assert state["last_self_at"] == NOW
    assert state["day"] == TODAY

    events = store.list_events(AID)
    assert len(events) == 1
    assert events[0]["status"] == "sent"
    assert events[0]["trigger"] == "manual"
    assert events[0]["message"] == "嗨，在忙吗"
    assert events[0]["error"] == ""
    assert events[0]["hit"] == 0

    assert len(env.mood_calls) == 1
    args, kwargs = env.mood_calls[0]
    assert args == (AID, UID, TRIGGER, "嗨，在忙吗")
    assert kwargs == {"source": "proactive", "llm": env.session.llm}


def test_send_one_partial_failure_records_partial_send(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "第一条\n第二条", "error": "", "emoji": []}
    ]
    env.session.send_error_at = 2
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "error"
    assert res["text"] == "第一条"
    assert res["error"] == "发送失败"
    assert [s["text"] for s in env.session.sent] == ["第一条"]
    assert env.mood_calls == [] and env.session.flushed == []

    # 已发出的 1 条必须记账：否则下一轮可能超发、冷却也会失效
    state = store.get_state(AID, UID)
    assert state["sent_today"] == 1
    assert state["last_self_at"] == NOW
    assert state["day"] == TODAY
    assert store.account_sent_today(AID, TODAY) == 1

    events = store.list_events(AID)
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert events[0]["message"] == "第一条"
    assert events[0]["error"] == "发送失败"
    assert events[0]["meta"] == {"sent_parts": 1}


def test_send_one_partial_failure_test_trigger_counts_no_quota(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "第一条\n第二条", "error": "", "emoji": []}
    ]
    env.session.send_error_at = 2
    res = asyncio.run(env.service.send_one(AID, UID, trigger="test"))
    assert res["status"] == "error"
    assert store.account_sent_today(AID, TODAY) == 0

    # 不占自动额度，但开口时间仍要更新，冷却照样生效
    state = store.get_state(AID, UID)
    assert state["sent_today"] == 0
    assert state["last_self_at"] == NOW
    event = store.list_events(AID)[0]
    assert event["status"] == "error"
    assert event["meta"] == {"sent_parts": 1}


def test_send_one_record_sent_failure_keeps_sent_result(env, monkeypatch):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "已经发出去了", "error": "", "emoji": [{"id": 3}]}
    ]

    def boom(*args, **kwargs):
        raise RuntimeError("state 表坏了")

    monkeypatch.setattr(service_mod.store, "record_sent", boom)
    res = asyncio.run(env.service.send_one(AID, UID))
    # 发送事实已发生，记账失败不能把结果改写成 error
    assert res["status"] == "sent"
    assert res["text"] == "已经发出去了"
    assert len(env.mood_calls) == 1
    assert env.session.flushed == [[{"id": 3}]]

    events = store.list_events(AID)
    assert events[0]["status"] == "sent"
    assert any(e["status"] == "warn" for e in events)


def test_send_one_mood_and_emoji_failures_do_not_change_sent(env, monkeypatch):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "在呢", "error": "", "emoji": [{"id": 2}]}
    ]
    env.session.flush_error = RuntimeError("表情发送失败")

    def boom(*args, **kwargs):
        raise RuntimeError("心情队列崩了")

    monkeypatch.setattr(service_mod.mood_service, "observe", boom)
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "sent"
    assert store.get_state(AID, UID)["sent_today"] == 1
    assert store.list_events(AID)[0]["status"] == "sent"


def test_send_one_multiline_splits_into_at_most_three_messages(env):
    _ready(env)
    text = "第一行\n第二行\n第三行\n第四行"
    env.session.replies = [
        {"ok": True, "skip": False, "text": text, "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "sent"
    parts = [s["text"] for s in env.session.sent]
    assert parts == ["第一行", "第二行", "第三行 第四行"]
    assert store.list_events(AID)[0]["message"] == "\n".join(parts)


def test_send_one_test_trigger_counts_no_auto_quota(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "测试一句", "error": "", "emoji": []}
    ]
    res = asyncio.run(
        env.service.send_one(AID, UID, trigger="test", ignore_quota=True)
    )
    assert res["status"] == "sent"
    state = store.get_state(AID, UID)
    assert state["sent_today"] == 0
    assert state["last_self_at"] == NOW
    assert state["day"] == TODAY

    events = store.list_events(AID)
    assert events[0]["status"] == "sent"
    assert events[0]["trigger"] == "test"


def test_send_one_manual_counts_quota(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "手动一条", "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "sent"
    assert store.get_state(AID, UID)["sent_today"] == 1
    assert store.account_sent_today(AID, TODAY) == 1


def test_send_one_busy_when_same_chat_already_running(env):
    _ready(env)
    env.service._running.add((AID, UID))
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res == {"ok": False, "status": "busy", "text": "", "error": ""}
    assert env.session.turns == [] and env.session.sent == []
    assert store.list_events(AID) == []


def test_send_one_cancelled_error_rethrows_and_releases_lock(env):
    _ready(env)
    env.session.replies = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(env.service.send_one(AID, UID))
    assert (AID, UID) not in env.service._running
    assert env.session.sent == []
    assert store.list_events(AID) == []

    # 锁已清理：同一会话再次调用不应 busy
    env.session.replies = [
        {"ok": True, "skip": False, "text": "取消后重试", "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "sent"


def test_send_one_generation_exception_releases_lock(env):
    _ready(env)
    env.session.replies = [RuntimeError("模型崩了")]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "error"
    assert res["error"] == "模型崩了"
    assert (AID, UID) not in env.service._running
    assert store.list_events(AID)[0]["status"] == "error"

    # 锁已释放：恢复后同一会话可以继续发送
    env.session.replies = [
        {"ok": True, "skip": False, "text": "恢复后发送", "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "sent"


def test_send_one_event_carries_preset_and_hit_flag(env):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "中奖了", "error": "", "emoji": []}
    ]
    res = asyncio.run(
        env.service.send_one(AID, UID, preset={"hit_line": 50.0, "roll": 10.0})
    )
    assert res["status"] == "sent"
    event = store.list_events(AID)[0]
    assert event["hit"] == 1
    assert event["hit_line"] == 50.0
    assert event["roll"] == 10.0
    assert event["meta"] == {"hit_line": 50.0, "roll": 10.0}


def test_event_write_failure_does_not_break_sending(env, monkeypatch):
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "库坏了也要发", "error": "", "emoji": []}
    ]

    def boom(*args, **kwargs):
        raise RuntimeError("events 表坏了")

    monkeypatch.setattr(service_mod.store, "add_event", boom)
    res = asyncio.run(env.service.send_one(AID, UID))
    assert res["status"] == "sent"
    assert env.session.sent[0]["text"] == "库坏了也要发"
    assert store.get_state(AID, UID)["sent_today"] == 1


def test_event_write_failure_does_not_break_miss(env, monkeypatch):
    _setup(env, account=_account(hit_peak=0, hit_offpeak=0))
    env.service._rng = lambda: 0.5

    def boom(*args, **kwargs):
        raise RuntimeError("events 表坏了")

    monkeypatch.setattr(service_mod.store, "add_event", boom)
    res = _roll(env)
    assert res["status"] == "miss"
    assert store.get_state(AID, UID)["misses"] == 1


# ------------------------------------------------- 终审补：生成期间翻脸的三种情况


def test_send_one_stops_when_switch_flipped_during_generation(env):
    """生成那十几秒里用户关掉总开关：一个字的开场都不该发出去。"""
    _ready(env)
    session = env.session

    async def flip_then_reply(*args, **kwargs):
        env.cfg["proactive"]["enabled"] = False
        return {"ok": True, "skip": False, "text": "在吗", "error": "", "emoji": []}

    session.run_proactive_turn = flip_then_reply
    res = asyncio.run(env.service.send_one(AID, UID, trigger="manual"))
    assert res["status"] == "skipped"
    assert res["error"] == "主动搭话未开启"
    assert session.sent == []
    assert store.get_state(AID, UID)["sent_today"] == 0
    assert store.list_events(AID)[0]["status"] == "skipped"


def test_send_one_stops_when_chat_leaves_whitelist_during_generation(env):
    """生成期间把它移出白名单：界面承诺「移出后不会再主动搭这个会话」，必须兑现。"""
    _ready(env)
    session = env.session

    async def unlist_then_reply(*args, **kwargs):
        env.cfg["proactive"]["accounts"][AID] = {"whitelist": []}
        return {"ok": True, "skip": False, "text": "在吗", "error": "", "emoji": []}

    session.run_proactive_turn = unlist_then_reply
    res = asyncio.run(env.service.send_one(AID, UID, trigger="manual"))
    assert res["status"] == "skipped"
    assert res["error"] == "不在主动搭话白名单"
    assert session.sent == []
    assert store.get_state(AID, UID)["sent_today"] == 0


def test_concurrent_send_one_respects_daily_limit(env):
    """只剩 1 条额度时并发两轮：生成不在锁里，但重查+发送+记账在，只能发出去一条。"""
    _add_chat()  # Bob：默认那条白名单会话
    db.upsert_chat(AID, "wxid_carol", "Carol", False, ts=NOW - 7200)
    db.set_chat_ai(OTHER_UID, True)
    env.cfg = _cfg(
        account=_account(
            daily_limit=1,
            whitelist=[
                {"chat_uid": UID, "name": "Bob", "is_group": False},
                {"chat_uid": OTHER_UID, "name": "Carol", "is_group": False},
            ],
        )
    )
    env.hub.sessions[AID] = env.session
    release = asyncio.Event()

    async def turn(uid, trigger_text, max_chars=200, preview=False):
        # 两轮都先过了「生成前」的额度检查，然后在同一刻被放行
        releasing.append(uid)
        await release.wait()
        return {"ok": True, "skip": False, "text": f"{uid} 的开场", "error": "", "emoji": []}

    releasing: list[str] = []
    env.session.run_proactive_turn = turn

    original_send = env.session.send_text

    async def yielding_send(*args, **kwargs):
        # 真发是 to_thread + 节流，会让出事件循环；不让出就测不出账号锁的意义
        await asyncio.sleep(0)
        return await original_send(*args, **kwargs)

    env.session.send_text = yielding_send

    async def both():
        first = asyncio.create_task(env.service.send_one(AID, UID, trigger="manual"))
        for _ in range(20):
            await asyncio.sleep(0)
        second = asyncio.create_task(env.service.send_one(AID, OTHER_UID, trigger="manual"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(releasing) == 2, "两轮都应停在生成阶段，额度检查都通过"
        release.set()
        return await first, await second

    r1, r2 = asyncio.run(both())
    sent = sorted([r1["status"], r2["status"]])
    assert sent.count("sent") == 1
    assert "skipped" in sent or "quota" in sent
    assert len(env.session.sent) == 1
    assert store.account_sent_today(AID, TODAY) == 1


def test_preview_marks_generation_as_preview(env):
    """试运行必须把 preview 传到生成层，否则会白吃掉一次心情注入。"""
    _ready(env)
    env.session.replies = [
        {"ok": True, "skip": False, "text": "在吗", "error": "", "emoji": []}
    ]
    res = asyncio.run(env.service.preview(AID, UID))
    assert res["status"] == "preview"
    assert env.session.previews == [True]
    assert env.session.turns[0] == (UID, TRIGGER, 200)
