"""主动搭话调度：开关、在线状态、白名单顺序、异常隔离与生命周期接线。"""
from __future__ import annotations

import asyncio

import pytest

from app import config
from app.proactive import scheduler as scheduler_mod


class FakeSession:
    def __init__(self, driver: object | None = None) -> None:
        self.driver = driver


class FakeHub:
    def __init__(self, sessions: dict[str, FakeSession | None] | None = None) -> None:
        self.sessions = sessions or {}

    def get(self, account_id: str | None = None) -> FakeSession | None:
        return self.sessions.get(account_id or "")


class FakeService:
    """记录每次掷骰；可按 chat_uid 注入异常，便于验证单条失败不影响其余。"""

    def __init__(self, errors: dict[str, BaseException] | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.errors = dict(errors or {})

    async def maybe_roll(self, account_id: str, entry: dict, trigger: str = "auto") -> dict:
        uid = entry["chat_uid"]
        self.calls.append((account_id, uid, trigger))
        exc = self.errors.get(uid)
        if exc is not None:
            raise exc
        return {"status": "miss"}


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    accounts: list[dict],
    whitelists: dict[str, list[dict]],
    *,
    enabled: bool = True,
) -> None:
    """只替换调度器读取的三处外部状态，不碰真实配置与数据库。"""
    monkeypatch.setattr(scheduler_mod.settings, "enabled", lambda: enabled)
    monkeypatch.setattr(
        scheduler_mod.settings,
        "settings",
        lambda aid: {"whitelist": whitelists.get(aid, [])},
    )
    monkeypatch.setattr(config, "accounts", lambda: accounts)


def _entry(aid: str, uid: str) -> dict:
    return {"chat_uid": f"{aid}:{uid}", "name": uid, "is_group": False}


def test_tick_globally_disabled_makes_no_calls(monkeypatch):
    service = FakeService()
    hub = FakeHub({"acc_a": FakeSession(object())})
    _patch(
        monkeypatch,
        [{"id": "acc_a"}],
        {"acc_a": [_entry("acc_a", "u1")]},
        enabled=False,
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    asyncio.run(sched.tick())
    assert service.calls == []


def test_tick_skips_disabled_account(monkeypatch):
    service = FakeService()
    hub = FakeHub({"acc_a": FakeSession(object())})
    _patch(
        monkeypatch,
        [{"id": "acc_a", "enabled": False}],
        {"acc_a": [_entry("acc_a", "u1")]},
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    asyncio.run(sched.tick())
    assert service.calls == []


@pytest.mark.parametrize("session", [None, FakeSession(None)])
def test_tick_skips_offline_session(monkeypatch, session):
    service = FakeService()
    hub = FakeHub({"acc_a": session})
    _patch(
        monkeypatch,
        [{"id": "acc_a"}],
        {"acc_a": [_entry("acc_a", "u1")]},
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    asyncio.run(sched.tick())
    assert service.calls == []


def test_tick_skips_empty_whitelist(monkeypatch):
    service = FakeService()
    hub = FakeHub({"acc_a": FakeSession(object())})
    _patch(monkeypatch, [{"id": "acc_a"}], {"acc_a": []})
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    asyncio.run(sched.tick())
    assert service.calls == []


def test_tick_rolls_each_whitelist_entry_in_order(monkeypatch):
    service = FakeService()
    hub = FakeHub(
        {"acc_a": FakeSession(object()), "acc_b": FakeSession(object())}
    )
    _patch(
        monkeypatch,
        [{"id": "acc_a"}, {"id": "acc_b"}],
        {
            "acc_a": [_entry("acc_a", "u1")],
            "acc_b": [_entry("acc_b", "u2")],
        },
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    asyncio.run(sched.tick())
    assert service.calls == [
        ("acc_a", "acc_a:u1", "auto"),
        ("acc_b", "acc_b:u2", "auto"),
    ]


def test_tick_continues_after_single_entry_error(monkeypatch):
    service = FakeService({"acc_a:u1": RuntimeError("boom")})
    hub = FakeHub({"acc_a": FakeSession(object())})
    _patch(
        monkeypatch,
        [{"id": "acc_a"}],
        {"acc_a": [_entry("acc_a", "u1"), _entry("acc_a", "u2")]},
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    asyncio.run(sched.tick())
    # 第一条异常被吞掉，第二条仍照常执行
    assert service.calls == [
        ("acc_a", "acc_a:u1", "auto"),
        ("acc_a", "acc_a:u2", "auto"),
    ]


def test_tick_reraises_cancelled_error(monkeypatch):
    service = FakeService({"acc_a:u1": asyncio.CancelledError()})
    hub = FakeHub({"acc_a": FakeSession(object())})
    _patch(
        monkeypatch,
        [{"id": "acc_a"}],
        {"acc_a": [_entry("acc_a", "u1"), _entry("acc_a", "u2")]},
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)

    async def run() -> None:
        with pytest.raises(asyncio.CancelledError):
            await sched.tick()

    asyncio.run(run())
    # 取消必须在第一条就中断整轮，不能继续扫后面的会话
    assert service.calls == [("acc_a", "acc_a:u1", "auto")]


def test_start_and_stop_are_idempotent(monkeypatch):
    service = FakeService()
    _patch(monkeypatch, [], {}, enabled=False)
    sched = scheduler_mod.ProactiveScheduler(FakeHub(), service=service)

    async def run() -> None:
        await sched.start()
        first = sched._task
        assert first is not None and not first.done()
        await sched.start()
        assert sched._task is first  # 重复 start 不新建任务
        await sched.stop()
        assert sched._task is None
        assert first.done()
        await sched.stop()  # 未运行/已停止时再 stop 不抛

    asyncio.run(run())


def test_loop_ticks_repeatedly(monkeypatch):
    service = FakeService()
    hub = FakeHub({"acc_a": FakeSession(object())})
    _patch(
        monkeypatch,
        [{"id": "acc_a"}],
        {"acc_a": [_entry("acc_a", "u1")]},
    )
    sched = scheduler_mod.ProactiveScheduler(hub, service=service)
    sched.interval = 0.01  # 绕过构造时的 1 秒下限，专测循环反复触发

    async def run() -> None:
        await sched.start()
        for _ in range(50):
            if len(service.calls) >= 2:
                break
            await asyncio.sleep(0.01)
        await sched.stop()

    asyncio.run(run())
    assert len(service.calls) >= 2
    assert all(call == ("acc_a", "acc_a:u1", "auto") for call in service.calls)


def test_lifespan_wires_scheduler_around_bili(monkeypatch):
    from app import main as main_mod

    events: list[str] = []

    def record(name: str):
        async def _call(*args, **kwargs) -> None:
            events.append(name)

        return _call

    monkeypatch.setattr(main_mod.db, "init", lambda: None)
    monkeypatch.setattr(
        main_mod.config,
        "load",
        lambda: {"server": {"host": "127.0.0.1", "port": 0}},
    )
    monkeypatch.setattr(main_mod.hub, "start", record("hub.start"))
    monkeypatch.setattr(main_mod.hub, "stop_all", record("hub.stop"))
    monkeypatch.setattr(
        main_mod.capability_registry, "start", record("capability.start")
    )
    monkeypatch.setattr(
        main_mod.capability_registry, "stop", record("capability.stop")
    )
    monkeypatch.setattr(
        main_mod.growth_scheduler, "start", record("growth.start")
    )
    monkeypatch.setattr(
        main_mod.growth_scheduler, "stop", record("growth.stop")
    )
    monkeypatch.setattr(
        main_mod.bili_patrol_scheduler, "start", record("bili.start")
    )
    monkeypatch.setattr(
        main_mod.bili_patrol_scheduler, "stop", record("bili.stop")
    )
    monkeypatch.setattr(
        main_mod.proactive_scheduler, "start", record("proactive.start")
    )
    monkeypatch.setattr(
        main_mod.proactive_scheduler, "stop", record("proactive.stop")
    )

    async def run() -> None:
        async with main_mod.lifespan(main_mod.app):
            pass

    asyncio.run(run())
    assert events == [
        "hub.start",
        "capability.start",
        "growth.start",
        "bili.start",
        "proactive.start",
        "proactive.stop",
        "bili.stop",
        "growth.stop",
        "capability.stop",
        "hub.stop",
    ]
    assert main_mod.app.state.proactive_scheduler is main_mod.proactive_scheduler
