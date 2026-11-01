"""成长调度：到点、补跑、每日一次、活跃阈值、启停。"""
import asyncio
import time

import pytest

from app import config, db
from app.growth import scheduler as scheduler_mod
from app.growth import store


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


def _now(hour=10, minute=0, day=27):
    return time.mktime((2026, 10, day, hour, minute, 0, 0, 0, -1))


def _cfg(**over):
    base = {
        "enabled": True,
        "accounts": {"acc_a": {"enabled": True}},
        "reflection_hour": 3,
        "reflection_minute": 30,
        "catch_up": True,
        "catch_up_delay_sec": 120,
        "min_new_messages": 20,
    }
    base.update(over)
    return base


def _enable(monkeypatch, cfg=None):
    cfg = cfg or _cfg()
    monkeypatch.setattr(config, "growth_cfg", lambda: cfg)
    monkeypatch.setattr(config, "accounts", lambda: [{"id": "acc_a"}])
    monkeypatch.setattr(config, "growth_enabled", lambda aid: aid == "acc_a")


def _seed_messages(n=25, ts=None):
    now = ts or time.time()
    db.upsert_chat("acc_a", "c1", "群一", True, "", now - 1)
    for i in range(n):
        db.add_message(
            {
                "id": f"m{i}",
                "account_id": "acc_a",
                "chat_uid": "acc_a:c1",
                "chat_id": "c1",
                "chat_name": "群一",
                "sender_name": "老王",
                "content": f"消息{i}",
                "msg_type": "text",
                "is_group": True,
                "ts": now - i,
            }
        )


def test_due_auto_after_scheduled_with_activity(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(10, 0)
    _seed_messages(25, now - 100)
    sched = scheduler_mod.GrowthScheduler(None, now=lambda: now)
    assert sched.due_trigger("acc_a", now) == "auto"


def test_due_none_before_scheduled_first_run(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(1, 0)
    _seed_messages(25, now - 100)
    sched = scheduler_mod.GrowthScheduler(None, now=lambda: now)
    assert sched.due_trigger("acc_a", now) is None


def test_due_catchup_after_startup(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(1, 0)
    _seed_messages(25, now - 100)
    last = store.start_run("acc_a", "auto", "2026-10-26")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE growth_runs SET status='ok', finished_at=? WHERE id=?",
            (now - 20 * 3600, last["id"]),
        )
    sched = scheduler_mod.GrowthScheduler(None, now=lambda: now)
    sched._started_at = now - 200
    assert sched.due_trigger("acc_a", now) == "catchup"


def test_due_blocked_by_today_run(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(10, 0)
    _seed_messages(25, now - 100)
    run = store.start_run("acc_a", "manual", "2026-10-27")
    store.finish_run(run["id"], "ok")
    sched = scheduler_mod.GrowthScheduler(None, now=lambda: now)
    assert sched.due_trigger("acc_a", now) is None


def test_due_requires_activity(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(10, 0)
    _seed_messages(5, now - 100)
    sched = scheduler_mod.GrowthScheduler(None, now=lambda: now)
    assert sched.due_trigger("acc_a", now) is None
    _seed_messages(20, now - 100)
    assert sched.due_trigger("acc_a", now) == "auto"


def test_tick_calls_runner(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(10, 0)
    called = []

    async def runner(llm, account_id, trigger):
        called.append((account_id, trigger))

    sched = scheduler_mod.GrowthScheduler(None, now=lambda: now, runner=runner)
    monkeypatch.setattr(sched, "due_trigger", lambda aid, now=None: "auto")
    asyncio.run(sched.tick())
    assert called == [("acc_a", "auto")]


def test_start_and_stop(temp_db, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(config, "accounts", lambda: [])

    async def runner(llm, account_id, trigger):
        return None

    async def run():
        sched = scheduler_mod.GrowthScheduler(None, interval=0.01, runner=runner)
        await sched.start()
        assert sched._task is not None
        await asyncio.sleep(0.03)
        await sched.stop()
        assert sched._task is None or sched._task.done()

    asyncio.run(run())


def test_next_run_at(temp_db, monkeypatch):
    _enable(monkeypatch)
    now = _now(10, 0)
    assert scheduler_mod.next_run_at("acc_a", now) == now
    run = store.start_run("acc_a", "auto", "2026-10-27")
    store.finish_run(run["id"], "ok")
    nxt = scheduler_mod.next_run_at("acc_a", now)
    assert nxt > now
