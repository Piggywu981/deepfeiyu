"""主动搭话数据层：表结构、状态回环、事件记录与账号设置。"""
from __future__ import annotations

import pytest

from app import config, db
from app.proactive import settings, store

AID = "acc_a"
UID = "acc_a:wxid_bob"


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


def _cfg(enabled: bool = False, account: dict | None = None) -> dict:
    return {
        "proactive": {
            "enabled": enabled,
            "accounts": {AID: account or {}},
        }
    }


def _reconnect() -> None:
    """模拟进程重启：丢掉线程连接，重新读取必须还能看到已落盘的状态。"""
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


def test_tables_and_index_exist(temp_db):
    names = {
        r[0]
        for r in db.read_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"proactive_state", "proactive_events"} <= names
    indexes = {
        r[0]
        for r in db.read_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_proactive_events" in indexes
    assert db.read_conn().execute("PRAGMA user_version").fetchone()[0] == 6


def _default(column) -> str | None:
    """PRAGMA dflt_value 保留 SQL 原文引号，这里只比较规范化后的值。"""
    raw = column["dflt_value"]
    if raw is None:
        return None
    return str(raw).strip().strip("'\"")


def test_proactive_table_columns_and_defaults(temp_db):
    state = {
        r["name"]: r
        for r in db.read_conn().execute("PRAGMA table_info(proactive_state)").fetchall()
    }
    assert {
        "account_id",
        "chat_uid",
        "last_roll_at",
        "last_self_at",
        "misses",
        "day",
        "sent_today",
    } <= set(state)
    assert state["account_id"]["pk"] == 1
    assert state["chat_uid"]["pk"] == 2
    assert state["account_id"]["notnull"] == 1
    assert state["chat_uid"]["notnull"] == 1
    assert _default(state["last_roll_at"]) == "0"
    assert _default(state["last_self_at"]) == "0"
    assert _default(state["misses"]) == "0"
    assert _default(state["day"]) == ""
    assert _default(state["sent_today"]) == "0"

    events = {
        r["name"]: r
        for r in db.read_conn().execute("PRAGMA table_info(proactive_events)").fetchall()
    }
    assert {
        "id",
        "account_id",
        "chat_uid",
        "at",
        "trigger",
        "hit_line",
        "roll",
        "hit",
        "status",
        "message",
        "error",
        "meta",
    } <= set(events)
    assert events["id"]["pk"] == 1
    assert events["id"]["type"] == "INTEGER"
    assert events["at"]["notnull"] == 1
    assert _default(events["trigger"]) == "auto"
    assert _default(events["hit_line"]) == "0"
    assert _default(events["roll"]) == "0"
    assert _default(events["hit"]) == "0"
    assert _default(events["status"]) == ""
    assert _default(events["message"]) == ""
    assert _default(events["error"]) == ""
    assert _default(events["meta"]) == "{}"


def test_get_state_defaults_are_read_only(temp_db):
    assert store.get_state(AID, UID) == {
        "account_id": AID,
        "chat_uid": UID,
        "last_roll_at": 0.0,
        "last_self_at": 0.0,
        "misses": 0,
        "day": "",
        "sent_today": 0,
    }
    assert store.list_states(AID) == []
    assert db.read_conn().execute("SELECT COUNT(*) FROM proactive_state").fetchone()[0] == 0


def test_record_roll_accumulates_then_hit_resets(temp_db):
    store.record_roll(AID, UID, 100.0, False)
    store.record_roll(AID, UID, 200.0, False)
    _reconnect()
    state = store.get_state(AID, UID)
    assert state["misses"] == 2
    assert state["last_roll_at"] == 200.0
    assert state["sent_today"] == 0
    assert state["day"] == ""
    assert [s["chat_uid"] for s in store.list_states(AID)] == [UID]

    hit = store.record_roll(AID, UID, 300.0, True)
    assert hit["misses"] == 0
    assert hit["last_roll_at"] == 300.0
    _reconnect()
    assert store.get_state(AID, UID)["misses"] == 0
    assert store.get_state("acc_b", UID)["misses"] == 0


def test_record_sent_counts_same_day_and_resets_across_day(temp_db):
    store.record_roll(AID, UID, 10.0, False)
    first = store.record_sent(AID, UID, 100.0, "2026-10-29")
    assert first["sent_today"] == 1
    assert first["day"] == "2026-10-29"
    assert first["last_self_at"] == 100.0
    assert first["misses"] == 1  # 开口记账不能冲掉连败次数

    second = store.record_sent(AID, UID, 200.0, "2026-10-29")
    assert second["sent_today"] == 2
    assert store.account_sent_today(AID, "2026-10-29") == 2

    reset = store.record_sent(AID, UID, 300.0, "2026-10-30", count=False)
    assert reset["day"] == "2026-10-30"
    assert reset["sent_today"] == 0
    assert reset["last_self_at"] == 300.0

    counted = store.record_sent(AID, UID, 400.0, "2026-10-30")
    assert counted["sent_today"] == 1
    assert counted["last_self_at"] == 400.0


def test_record_sent_count_false_keeps_same_day_counter(temp_db):
    store.record_sent(AID, UID, 100.0, "2026-10-29")
    state = store.record_sent(AID, UID, 200.0, "2026-10-29", count=False)
    assert state["sent_today"] == 1
    assert state["last_self_at"] == 200.0


def test_account_sent_today_sums_current_day_only(temp_db):
    other_chat = "acc_a:wxid_carol"
    store.record_sent(AID, UID, 1.0, "2026-10-29")
    store.record_sent(AID, UID, 2.0, "2026-10-29", count=False)
    store.record_sent(AID, other_chat, 3.0, "2026-10-29")
    store.record_sent(AID, other_chat, 4.0, "2026-10-29")
    store.record_sent(AID, "acc_a:wxid_erin", 5.0, "2026-10-28")
    store.record_sent("acc_b", "acc_b:wxid_dave", 6.0, "2026-10-29")

    assert store.account_sent_today(AID, "2026-10-29") == 3
    assert store.account_sent_today(AID, "2026-10-28") == 1
    assert store.account_sent_today(AID, "2026-10-30") == 0
    assert store.account_sent_today("acc_b", "2026-10-29") == 1


def test_events_order_limit_before_and_meta_fallback(temp_db):
    ids = []
    for i in range(5):
        ids.append(
            store.add_event(
                AID,
                UID,
                100.0 + i,
                "auto",
                20.0,
                float(i),
                False,
                "miss",
                message=f"第{i}条",
                meta={"i": i},
            )
        )
    same_at = store.add_event(
        AID, UID, 104.0, "manual", 0, 0, True, "sent", message="同刻后写"
    )

    rows = store.list_events(AID)
    assert [r["message"] for r in rows] == ["同刻后写", "第4条", "第3条", "第2条", "第1条", "第0条"]
    assert rows[0]["id"] == same_at
    assert rows[1]["meta"] == {"i": 4}

    assert [r["message"] for r in store.list_events(AID, limit=2)] == ["同刻后写", "第4条"]
    assert [r["message"] for r in store.list_events(AID, limit=2, before=103.0)] == ["第2条", "第1条"]
    assert len(store.list_events(AID, limit=0)) == 1
    assert len(store.list_events(AID, limit=500)) == 6
    assert store.list_events("acc_b") == []

    with db._write_lock:
        conn = db._conn()
        conn.execute("UPDATE proactive_events SET meta=? WHERE id=?", ("not-json", ids[0]))
        conn.execute("UPDATE proactive_events SET meta=? WHERE id=?", ("[1,2]", ids[1]))
        conn.commit()
    by_id = {r["id"]: r for r in store.list_events(AID, limit=200)}
    assert by_id[ids[0]]["meta"] == {}
    assert by_id[ids[1]]["meta"] == {}


def test_event_text_is_truncated_and_meta_defaults_empty(temp_db):
    eid = store.add_event(
        AID,
        UID,
        1.0,
        "t" * 30,
        0.0,
        0.0,
        False,
        "s" * 30,
        message="m" * 3000,
        error="e" * 1500,
    )
    row = store.list_events(AID)[0]
    assert row["id"] == eid
    assert len(row["trigger"]) == 20
    assert len(row["status"]) == 20
    assert len(row["message"]) == 2000
    assert len(row["error"]) == 1000
    assert row["meta"] == {}
    assert row["hit"] == 0


def test_settings_defaults(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg())
    st = settings.settings(AID)
    assert st["whitelist"] == []
    assert st["interval_min"] == 30
    assert st["cooldown_min"] == 20
    assert st["quiet_start"] == 1
    assert st["quiet_end"] == 8
    assert st["daily_limit"] == 6
    assert st["per_chat_daily"] == 2
    assert st["hit_peak"] == 20
    assert st["hit_offpeak"] == 60
    assert st["recent_min"] == 20
    assert st["recent_factor"] == 0.15
    assert st["cold_min"] == 360
    assert st["cold_factor"] == 1.5
    assert st["miss_bonus"] == 10
    assert st["bonus_cap"] == 40
    assert st["trigger_text"] == "好久没人跟我说话了，我该主动去打个招呼"
    assert st["max_chars"] == 200
    assert settings.settings("acc_unknown") == st
    assert settings.enabled() is False
    assert settings.root() == {"enabled": False, "accounts": {AID: {}}}


def test_settings_clamps_numbers_and_ignores_unknown(temp_db, monkeypatch):
    monkeypatch.setattr(
        config,
        "load",
        lambda: _cfg(
            account={
                "interval_min": 0,
                "cooldown_min": 9999,
                "quiet_start": -1,
                "quiet_end": 30,
                "daily_limit": 1000,
                "per_chat_daily": -5,
                "hit_peak": 250,
                "hit_offpeak": -3,
                "recent_min": 5000,
                "recent_factor": 99,
                "cold_min": -10,
                "cold_factor": 50,
                "miss_bonus": 500,
                "bonus_cap": 500,
                "max_chars": 5,
                "unknown": 1,
            }
        ),
    )
    st = settings.settings(AID)
    assert st["interval_min"] == 1
    assert st["cooldown_min"] == 1440
    assert st["quiet_start"] == 0
    assert st["quiet_end"] == 23
    assert st["daily_limit"] == 100
    assert st["per_chat_daily"] == 0
    assert st["hit_peak"] == 100
    assert st["hit_offpeak"] == 0
    assert st["recent_min"] == 1440
    assert st["recent_factor"] == 10
    assert st["cold_min"] == 0
    assert st["cold_factor"] == 10
    assert st["miss_bonus"] == 100
    assert st["bonus_cap"] == 100
    assert st["max_chars"] == 20
    assert "unknown" not in st


def test_settings_invalid_types_fall_back_to_defaults(temp_db, monkeypatch):
    monkeypatch.setattr(
        config,
        "load",
        lambda: _cfg(
            account={
                "interval_min": "abc",
                "cooldown_min": None,
                "quiet_start": [],
                "daily_limit": {},
                "hit_peak": "x",
                "recent_factor": None,
                "max_chars": "big",
                "trigger_text": "   ",
                "dry_run": "not-a-bool",
            }
        ),
    )
    st = settings.settings(AID)
    assert st["interval_min"] == 30
    assert st["cooldown_min"] == 20
    assert st["quiet_start"] == 1
    assert st["daily_limit"] == 6
    assert st["hit_peak"] == 20
    assert st["recent_factor"] == 0.15
    assert st["max_chars"] == 200
    assert st["trigger_text"] == "好久没人跟我说话了，我该主动去打个招呼"
    # 认不出的账号字段会被忽略：设置表里没有 dry_run 这一项了
    assert "dry_run" not in st


def test_settings_infinities_and_nan_fall_back_to_defaults(temp_db, monkeypatch):
    monkeypatch.setattr(
        config,
        "load",
        lambda: _cfg(
            account={
                "interval_min": float("inf"),
                "cooldown_min": float("-inf"),
                "quiet_start": float("nan"),
                "hit_peak": float("inf"),
                "hit_offpeak": float("-inf"),
                "recent_factor": float("nan"),
                "cold_factor": float("nan"),
                "max_chars": float("inf"),
            }
        ),
    )
    st = settings.settings(AID)  # 不能抛 OverflowError
    assert st["interval_min"] == 30
    assert st["cooldown_min"] == 20
    assert st["quiet_start"] == 1
    assert st["hit_peak"] == 20
    assert st["hit_offpeak"] == 60
    assert st["recent_factor"] == 0.15
    assert st["cold_factor"] == 1.5
    assert st["max_chars"] == 200


def test_settings_bool_parsing_for_enabled_switch(monkeypatch, temp_db):
    """JSON 里手写的 "false"/"0" 不能被 bool() 当成开启。"""
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled="false"))
    assert settings.enabled() is False
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled="true"))
    assert settings.enabled() is True
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled=" TRUE "))
    assert settings.enabled() is True
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled="on"))
    assert settings.enabled() is True
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled=0))
    assert settings.enabled() is False
    # 认不出来的值回退默认（关），不会误开真发
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled="maybe"))
    assert settings.enabled() is False
    monkeypatch.setattr(config, "load", lambda: _cfg(enabled=2))
    assert settings.enabled() is False


def test_root_returns_deep_copy(temp_db, monkeypatch):
    cfg = _cfg(account={"interval_min": 45, "whitelist": [{"chat_uid": "acc_a:bob"}]})
    monkeypatch.setattr(config, "load", lambda: cfg)

    first = settings.root()
    first["enabled"] = True
    first["accounts"][AID]["interval_min"] = 1
    first["accounts"][AID]["whitelist"].append({"chat_uid": "acc_a:eve"})

    assert settings.root() == cfg["proactive"]
    assert settings.enabled() is False
    assert settings.settings(AID)["interval_min"] == 45
    assert settings.whitelist(AID) == [
        {"chat_uid": "acc_a:bob", "name": "", "is_group": False}
    ]


def test_settings_trigger_text_is_trimmed_and_capped(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _cfg(account={"trigger_text": "  " + "啊" * 300 + "  "}))
    assert settings.settings(AID)["trigger_text"] == "啊" * 200


def test_settings_tolerates_broken_config_shapes(temp_db, monkeypatch):
    monkeypatch.setattr(
        config, "load", lambda: {"proactive": {"enabled": True, "accounts": "bad"}}
    )
    st = settings.settings(AID)
    assert st["interval_min"] == 30
    assert settings.enabled() is True

    monkeypatch.setattr(config, "load", lambda: {})
    assert settings.root() == {}
    assert settings.enabled() is False
    assert settings.settings(AID)["whitelist"] == []


def test_whitelist_normalizes_dedupes_and_caps(temp_db, monkeypatch):
    raw = [
        {"chat_uid": " acc_a:g1 ", "name": " 群一 ", "is_group": 1},
        {"chat_uid": "acc_a:g1", "name": "重复条目", "is_group": True},
        {"chat_uid": "acc_a:bob"},
        {"chat_uid": "   "},
        {"name": "缺 uid"},
        "oops",
        {"chat_uid": 123},
    ]
    monkeypatch.setattr(config, "load", lambda: _cfg(account={"whitelist": raw}))
    entries = settings.whitelist(AID)
    assert entries == [
        {"chat_uid": "acc_a:g1", "name": "群一", "is_group": True},
        {"chat_uid": "acc_a:bob", "name": "", "is_group": False},
    ]
    assert settings.valid_entry("oops") is None
    assert settings.valid_entry({"chat_uid": "u", "name": 5, "is_group": "yes"}) == {
        "chat_uid": "u",
        "name": "",
        "is_group": True,
    }
    # 手写字符串 "false" 不能被当成群聊；未知值回退私聊。
    assert settings.valid_entry({"chat_uid": "u", "is_group": "false"})["is_group"] is False
    assert settings.valid_entry({"chat_uid": "u", "is_group": "maybe"})["is_group"] is False

    entries.append({"chat_uid": "acc_a:extra", "name": "", "is_group": False})
    assert len(settings.whitelist(AID)) == 2  # 返回副本，改动不污染配置

    many = [{"chat_uid": f"acc_a:u{i}", "name": str(i)} for i in range(60)]
    monkeypatch.setattr(config, "load", lambda: _cfg(account={"whitelist": many}))
    capped = settings.whitelist(AID)
    assert len(capped) == 50
    assert capped[0]["chat_uid"] == "acc_a:u0"
    assert capped[-1]["chat_uid"] == "acc_a:u49"
