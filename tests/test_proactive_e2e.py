"""主动搭话端到端：调度器 → 服务 → 真实 AccountSession/Agent → 假驱动发送 → 库与事件。

这条链路跨了 settings/engine/store/rules/manager/service/scheduler 七个模块，单元测试各管一段，
这里只验「串起来真的能发出去、并且该拦的拦住」：
- 命中后消息真的进驱动、进 messages（带 proactive 元信息）、进 proactive_events，额度与 misses 落库；
- 同一 tick 之后间隔未到不再掷；未中只留 miss 记录；
- 黑名单在命中后仍被发前闸门拦下，一个字的开场都不会发出去；
- 安静时段连掷骰都不发生。

全程离线：LLM 用假 stream 回放，驱动只记录发送，库和配置都是临时的一份。
"""
from __future__ import annotations

import asyncio
import copy
import time

import pytest

from app import config, db
from app.mood import service as mood_service
from app.proactive import scheduler as scheduler_mod, service as service_mod, settings, store
from app.wechat import manager as manager
from app.wechat.manager import AccountSession

AID = "acc_x"
UID = "acc_x:c1"


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """临时库 + 内存配置：config.load 返回同一份可变 dict，save 就地合并。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    db.init()

    cfg = _base_cfg()
    monkeypatch.setattr(config, "load", lambda: cfg)
    # 能力系统在测试里没启动，_get_cfg 持有的是原始 config.load 引用，直接掐掉外部工具
    monkeypatch.setattr(manager.capability_registry, "tools_for", lambda context="console": [])

    observed: list[tuple] = []
    monkeypatch.setattr(
        mood_service,
        "observe",
        lambda account_id, chat_uid, user_text, reply, source="user", llm=None: observed.append(
            (account_id, chat_uid, user_text, reply, source)
        ) or "queued",
    )
    yield cfg, observed
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


def _base_cfg(**over) -> dict:
    """默认：开启搭话、白名单一个私聊、间隔 30 分钟、安静时段 1–8。"""
    account = {**settings.ACCOUNT_DEFAULTS, "whitelist": [{"chat_uid": UID, "name": "阿明", "is_group": False}]}
    cfg = {
        "active_account": AID,
        "accounts": [
            {
                "id": AID,
                "name": "测试号",
                "nickname": "小鲸",
                "enabled": True,
                "persona": {"system_prompt": "你是一条爱聊天的鱼。", "persona_name": "小鲸"},
            }
        ],
        "ai": {
            "persona_name": "小鲸",
            "system_prompt": "通用系统提示",
            "memory_enabled": False,
            "emoji_with_reply": False,
            "vision_enabled": False,
            "wiki_emoji_only": False,
            "chuanzi_mode": False,
            "moe_wiki_path": "",
        },
        "auto": {"blacklist": [], "only_whitelist": False, "whitelist": []},
        "mood": {"enabled": False, "accounts": {}},
        "growth": {"enabled": False, "accounts": {}},
        "proactive": {"enabled": True, "accounts": {AID: account}},
    }
    cfg.update(over)
    return cfg


class FakeLLM:
    """按脚本回放一轮纯文本；主动搭话只需要一轮就能出正文。"""

    configured = True

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        yield {"type": "text", "text": self.text}
        yield {"type": "finish", "reason": "stop"}


class StubDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeHub:
    def __init__(self, session: AccountSession | None) -> None:
        self.session = session

    def get(self, account_id: str | None = None) -> AccountSession | None:
        return self.session


def _seed_chat(ts: float) -> None:
    """建会话并留两条历史：让提示词有上下文，last_ts 落在 ts。"""
    db.upsert_chat(AID, "c1", "阿明", False, "上次聊到加班", ts=ts)
    # 新会话默认是人工接管，主动搭话的闸门会拦；这里显式交给 AI
    db.set_chat_ai(UID, True)
    for offset, content, is_self in ((-60, "最近在忙啥", False), (-30, "忙着改东西", True)):
        db.add_message(
            {
                "id": f"m{offset}",
                "account_id": AID,
                "chat_uid": UID,
                "chat_id": "c1",
                "chat_name": "阿明",
                "sender_id": "",
                "sender_name": "阿明" if not is_self else "我",
                "content": content,
                "msg_type": "text",
                "is_group": False,
                "is_self": is_self,
                "source": "wechat" if not is_self else "agent",
                "ts": ts + offset,
                "meta": {},
            }
        )


def _run(temp_env, *, now: float, rng_value: float, text: str = "在忙吗，刚看到个好笑的"):
    cfg, observed = temp_env
    _seed_chat(now - 3600)  # 一小时前最后说过话：既不算刚聊完，也没冷场
    session = AccountSession(
        {"id": AID, "name": "测试号", "nickname": "小鲸", "persona": {"system_prompt": "人设", "persona_name": "小鲸"}},
        lambda: cfg,
        FakeLLM(text),
    )
    session.driver = StubDriver()
    hub = FakeHub(session)
    service = service_mod.ProactiveService(hub, now=lambda: now, rng=lambda: rng_value)
    sched = scheduler_mod.ProactiveScheduler(hub, service=service, now=lambda: now)
    asyncio.run(sched.tick())
    return session, cfg, observed


def _at(hour: int, minute: int = 0, day: tuple[int, int, int] = (2026, 10, 29)) -> float:
    """本地时间构造时间戳：调度看的是本地小时，测试不能让时区牵着走。"""
    y, m, d = day
    return time.mktime((y, m, d, hour, minute, 0, 0, 0, -1))


def test_hit_sends_and_records(temp_env):
    """峰时命中线 20、掷骰 0：真实走一遍生成→发送→落库→事件→心情判断。"""
    session, _, observed = _run(temp_env, now=_at(15, 0), rng_value=0.0)

    assert session.driver.sent == [("c1", "在忙吗，刚看到个好笑的")]
    rows = db.list_messages(UID, limit=10)
    proactive_rows = [r for r in rows if (r.get("meta") or {}).get("proactive")]
    assert len(proactive_rows) == 1
    assert proactive_rows[0]["source"] == "agent"
    assert proactive_rows[0]["meta"]["trigger"] == "auto"

    events = store.list_events(AID, limit=10)
    assert [e["status"] for e in events] == ["sent"]
    assert events[0]["hit"] == 1
    assert events[0]["message"] == "在忙吗，刚看到个好笑的"

    state = store.get_state(AID, UID)
    assert state["sent_today"] == 1 and state["misses"] == 0
    assert state["last_self_at"] == pytest.approx(_at(15, 0))

    # 心情判断拿到的是「开口的动机 + 真的发出去的话」，来源标 proactive
    assert observed == [(AID, UID, settings.ACCOUNT_DEFAULTS["trigger_text"], "在忙吗，刚看到个好笑的", "proactive")]


def test_second_tick_within_interval_is_quiet(temp_env):
    """同一 tick 之后间隔未到：不再掷骰，也不该再发。"""
    cfg, _ = temp_env
    session, _, _ = _run(temp_env, now=_at(15, 0), rng_value=0.0)
    assert session.driver.sent

    # 换一个只前进 5 分钟的服务实例再跑一轮（配置里的白名单与库里状态都还在）
    later = _at(15, 5)
    service = service_mod.ProactiveService(
        FakeHub(session), now=lambda: later, rng=lambda: 0.0
    )
    sched = scheduler_mod.ProactiveScheduler(FakeHub(session), service=service, now=lambda: later)
    asyncio.run(sched.tick())
    assert session.driver.sent == [("c1", "在忙吗，刚看到个好笑的")]
    assert store.list_events(AID, limit=10) and len(store.list_events(AID, limit=10)) == 1


def test_miss_only_records_dice(temp_env):
    """没掷中：只有 miss 记录，不动额度、不调模型、不发东西。"""
    session, _, observed = _run(temp_env, now=_at(15, 0), rng_value=0.99)
    assert session.driver.sent == []
    assert observed == []
    assert session.llm.calls == 0
    events = store.list_events(AID, limit=10)
    assert [e["status"] for e in events] == ["miss"]
    assert events[0]["hit"] == 0
    assert store.get_state(AID, UID)["misses"] == 1
    assert store.get_state(AID, UID)["sent_today"] == 0


def test_blacklisted_chat_blocked_after_hit(temp_env):
    """命中了也必须在发前闸门停下：黑名单会话一个字都发不出去。"""
    cfg, _ = temp_env
    cfg["auto"]["blacklist"] = ["阿明"]
    session, _, observed = _run(temp_env, now=_at(15, 0), rng_value=0.0)
    assert session.driver.sent == []
    assert observed == []
    events = store.list_events(AID, limit=10)
    assert [e["status"] for e in events] == ["skipped"]
    assert events[0]["error"] == "命中黑名单"
    assert store.get_state(AID, UID)["sent_today"] == 0


def test_quiet_hours_roll_nothing(temp_env):
    """安静时段（默认 1–8）连掷骰都不做，不产生任何记录。"""
    session, _, observed = _run(temp_env, now=_at(3, 0), rng_value=0.0)
    assert session.driver.sent == []
    assert observed == []
    assert store.list_events(AID, limit=10) == []
    assert session.llm.calls == 0


def test_daily_limit_blocks_next_roll(temp_env):
    """账号日上限用满后，下一次到点也不再掷骰。"""
    now = _at(15, 0)
    session, cfg, _ = _run(temp_env, now=now, rng_value=0.0)
    account = cfg["proactive"]["accounts"][AID]
    account["daily_limit"] = 1
    account["interval_min"] = 1
    later = _at(15, 30)
    service = service_mod.ProactiveService(FakeHub(session), now=lambda: later, rng=lambda: 0.0)
    sched = scheduler_mod.ProactiveScheduler(FakeHub(session), service=service, now=lambda: later)
    asyncio.run(sched.tick())
    assert len(session.driver.sent) == 1
    assert len(store.list_events(AID, limit=10)) == 1


def test_switch_off_stops_everything(temp_env):
    """关掉总开关：不再掷骰、不再调模型、不再发送。"""
    cfg, _ = temp_env
    cfg["proactive"]["enabled"] = False
    session, _, observed = _run(temp_env, now=_at(15, 0), rng_value=0.0)
    assert session.driver.sent == []
    assert session.llm.calls == 0
    assert observed == []
    assert store.list_events(AID, limit=10) == []


def test_generated_text_reaches_driver_within_max_chars(temp_env):
    """正文按账号 max_chars 收口，超长由 sanitize 截断，绝不发原文那么长。"""
    cfg, _ = temp_env
    cfg["proactive"]["accounts"][AID]["max_chars"] = 20
    long_text = "鱼" * 60
    session, _, _ = _run(temp_env, now=_at(15, 0), rng_value=0.0, text=long_text)
    assert session.driver.sent
    sent = session.driver.sent[0][1]
    assert len(sent) <= 21  # 20 字 + 省略号
    assert sent != long_text


def test_settings_defaults_are_persisted_copy(temp_env):
    """配置读取不污染原始 dict，且默认值与计划一致（峰 20/谷 60/日上限 6）。"""
    cfg, _ = temp_env
    st = settings.settings(AID)
    assert st["hit_peak"] == 20 and st["hit_offpeak"] == 60 and st["daily_limit"] == 6
    assert cfg["proactive"]["accounts"][AID] == settings.ACCOUNT_DEFAULTS | cfg["proactive"]["accounts"][AID]
    mutated = copy.deepcopy(st)
    mutated["daily_limit"] = 99
    assert settings.settings(AID)["daily_limit"] == 6
