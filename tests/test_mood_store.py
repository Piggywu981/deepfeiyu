"""心情存储与账号配置：默认槽、状态回环、时间线、会话绑定、槽位解析。"""
from __future__ import annotations

import pytest

from app import config, db
from app.mood import constants, settings, store


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


def _mood_cfg(enabled: bool = True, account: dict | None = None) -> dict:
    base = config.DEFAULTS["mood"]
    return {
        "mood": {
            "enabled": enabled,
            "accounts": {"acc_a": account or {}},
        }
    }


def test_default_slot_is_created_with_base_mood(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    state = store.load_state("acc_a", store.DEFAULT_SLOT)
    assert state["mood"] == 70.0
    assert state["label"] == ""
    assert state["dims"] == {k: 0.0 for k in constants.DIM_KEYS}
    assert state["said_at"] == []
    assert state["kind"] == "judge"


def test_init_adds_last_chat_uid_to_old_slots_table(temp_db):
    """老库没有 last_chat_uid：再次 init 必须幂等补列，不能报 no such column。"""
    assert "last_chat_uid" in db._columns(db._conn(), "mood_slots")
    with db._write_lock:
        conn = db._conn()
        conn.execute("ALTER TABLE mood_slots DROP COLUMN last_chat_uid")
        conn.commit()
    assert "last_chat_uid" not in db._columns(db._conn(), "mood_slots")

    db.init()
    assert "last_chat_uid" in db._columns(db._conn(), "mood_slots")
    state = store.load_state("acc_a", store.DEFAULT_SLOT)
    assert state["last_chat_uid"] == ""


def test_state_roundtrip_keeps_dims_and_said_at(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    state = store.load_state("acc_a", store.DEFAULT_SLOT)
    state.update(
        mood=83.5,
        dims={"happy": 21.0, "calm": 5.0, "sweet": 0.0, "heartache": 0.0,
              "worry": 0.0, "sad": 0.0, "jealous": 0.0},
        last_reason="号主说没吃饭，我心里咯噔一下",
        last_confidence=88,
        last_noted_mood=83.5,
        injected_for=0.0,
        said_at=[111.0, 222.0],
        minor=0,
        kind="judge",
        updated_at=333.0,
    )
    store.save_state("acc_a", store.DEFAULT_SLOT, state)
    again = store.load_state("acc_a", store.DEFAULT_SLOT)
    assert again["mood"] == 83.5
    assert again["dims"]["happy"] == 21.0
    assert again["last_reason"] == "号主说没吃饭，我心里咯噔一下"
    assert again["said_at"] == [111.0, 222.0]
    assert again["updated_at"] == 333.0


def test_corrupt_said_at_items_are_skipped(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    store.load_state("acc_a", store.DEFAULT_SLOT)
    with db._write_lock:
        conn = db._conn()
        conn.execute(
            "UPDATE mood_slots SET said_at=? WHERE account_id=? AND slot_id=?",
            ('[1,null,"oops",2]', "acc_a", store.DEFAULT_SLOT),
        )
        conn.commit()
    state = store.load_state("acc_a", store.DEFAULT_SLOT)
    assert state["said_at"] == [1.0, 2.0]


def test_events_are_listed_newest_first_with_paging(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    for i in range(5):
        store.add_event(
            "acc_a", "default", at=float(100 + i), mood=70.0 + i, delta=i,
            dims={}, dims_delta={}, reason=f"第{i}条", confidence=80,
            kind="judge", source="user", minor=0,
        )
    rows = store.list_events("acc_a", "default", limit=2)
    assert [r["reason"] for r in rows] == ["第4条", "第3条"]
    older = store.list_events("acc_a", "default", limit=2, before=103.0)
    assert [r["reason"] for r in older] == ["第2条", "第1条"]


def test_bindings_are_unique_and_resolved(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    slot = store.create_slot("acc_a", "s1", label="小号")
    assert slot["slot_id"] == "s1"
    store.bind_chat("acc_a", "s1", "acc_a:wxid_bob")
    store.bind_chat("acc_a", "s1", "acc_a:wxid_bob")
    assert store.slot_of_chat("acc_a", "acc_a:wxid_bob") == "s1"
    assert store.list_bindings("acc_a") == [{"slot_id": "s1", "chat_uid": "acc_a:wxid_bob"}]
    store.unbind_chat("acc_a", "acc_a:wxid_bob")
    assert store.slot_of_chat("acc_a", "acc_a:wxid_bob") is None


def test_delete_slot_removes_bindings(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    store.create_slot("acc_a", "s1")
    store.bind_chat("acc_a", "s1", "u1")
    store.delete_slot("acc_a", "s1")
    assert store.list_slots("acc_a") == []
    assert store.list_bindings("acc_a") == []
    with pytest.raises(ValueError):
        store.delete_slot("acc_a", store.DEFAULT_SLOT)


def test_settings_merge_and_enable_switch(temp_db, monkeypatch):
    monkeypatch.setattr(
        config, "load",
        lambda: _mood_cfg(account={"base": 55, "inject_per_day": 2, "unknown": 1}),
    )
    st = settings.settings("acc_a")
    assert st["base"] == 55
    assert st["inject_per_day"] == 2
    assert st["decay_half_hours"] == 12
    assert st["inject_mode"] == "event"
    assert "unknown" not in st
    assert settings.enabled("acc_a") is True
    monkeypatch.setattr(config, "load", lambda: _mood_cfg(enabled=False))
    assert settings.enabled("acc_a") is False


def test_resolve_slot_account_vs_chat(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    assert settings.resolve_slot("acc_a", "any") == "default"
    store.create_slot("acc_a", "s1")
    store.bind_chat("acc_a", "s1", "u1")
    monkeypatch.setattr(
        config, "load",
        lambda: _mood_cfg(account={"mode": "chat"}),
    )
    assert settings.resolve_slot("acc_a", "u1") == "s1"
    assert settings.resolve_slot("acc_a", "u2") == "default"
    assert settings.resolve_slot("acc_a", "") == "default"


def test_names_fall_back_to_persona(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _mood_cfg())
    monkeypatch.setattr(config, "persona_of", lambda account: ("提示", "大肥鱼"))
    monkeypatch.setattr(config, "account", lambda aid: {"id": aid, "nickname": "号主"})
    self_name, owner, aliases = settings.names("acc_a", "default", chat_name="小明")
    assert self_name == "大肥鱼"
    assert owner == "小明"
    assert aliases == ""
