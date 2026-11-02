"""DSH 融合桥接：绑定、状态、共享心情写入、宠物事件与隐私规则。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app import config, db
from app.dsh_bridge import api as bridge_api
from app.dsh_bridge import service as bridge_service

ACCOUNT = {"id": "acc_a", "name": "工作号", "driver": "webhook", "enabled": True}


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


@pytest.fixture()
def cfg(monkeypatch):
    """真实 config 模块的 load/save/accounts 全部换成内存字典。"""
    mood_account = {
        "inject_mode": "event", "inject_confidence": 60, "inject_per_day": 6,
        "base": 70, "floor": 0, "decay_half_hours": 12,
        "sig_mood": 3, "sig_dim": 5, "judge_model": "",
    }
    state = {
        "mood": {"enabled": True, "accounts": {"acc_a": mood_account}},
        "dsh": {"account_id": "", "judge_enabled": True,
                "pet_events_enabled": True, "cross_reason_enabled": True},
    }

    def load():
        return {"active_account": "acc_a", "accounts": [dict(ACCOUNT)], **state}

    def save(patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key] = {**state[key], **value}
            else:
                state[key] = value
        return load()

    monkeypatch.setattr(config, "load", load)
    monkeypatch.setattr(config, "save", save)
    monkeypatch.setattr(config, "accounts", lambda: [dict(ACCOUNT)])
    monkeypatch.setattr(config, "account",
                        lambda aid: dict(ACCOUNT) if aid == "acc_a" else None)
    monkeypatch.setattr(config, "active_id", lambda: "acc_a")
    monkeypatch.setattr(config, "persona_of", lambda account: ("p", "大肥鱼"))
    return state


def _client() -> TestClient:
    return TestClient(main.app)


def test_state_unbound_falls_back_to_active(cfg):
    cfg["dsh"]["account_id"] = "gone"
    body = _client().get("/api/dsh/state").json()
    assert body["ok"] is True
    assert body["accountId"] == "acc_a"
    assert body["boundFallback"] is True
    assert body["config"]["judge_enabled"] is True
    assert [a["id"] for a in body["accounts"]] == ["acc_a"]


def test_config_updates_binding_and_switches(cfg):
    res = _client().post("/api/dsh/config", json={
        "account_id": "acc_a", "cross_reason_enabled": False,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["accountId"] == "acc_a" and body["boundFallback"] is False
    assert body["config"]["cross_reason_enabled"] is False
    assert cfg["dsh"]["cross_reason_enabled"] is False


def test_config_rejects_unknown_account(cfg):
    res = _client().post("/api/dsh/config", json={"account_id": "nope"})
    assert res.status_code == 400
    assert "账号" in res.json()["detail"]


def test_remote_host_rejected():
    class _Remote:
        client = type("C", (), {"host": "8.8.8.8"})()

    with pytest.raises(HTTPException) as caught:
        bridge_api._require_local(_Remote())
    assert caught.value.status_code == 403


def _seed_mood(account_id="acc_a", slot_id="default"):
    from app.mood import store
    state = store.load_state(account_id, slot_id)
    state.update({"mood": 70.0, "last_confidence": 80, "updated_at": 1000.0})
    store.save_state(account_id, slot_id, state)


def test_judge_prompt_is_rendered_for_bound_account(cfg):
    body = _client().get("/api/dsh/judge-prompt").json()
    assert body["version"] and len(body["version"]) == 12
    assert "只输出一个 JSON 对象" in body["system"]
    assert "moodDelta" in body["schema"]


def test_mood_event_applies_and_records_dsh_source(temp_db, cfg):
    _seed_mood()
    res = _client().post("/api/dsh/mood-event", json={
        "moodDelta": 6,
        "dims": {"happy": 8, "calm": 0, "sweet": 2,
                 "heartache": 0, "worry": 0, "sad": 0, "jealous": 0},
        "confidence": 85,
        "reason": "用户在 Harness 里把一个麻烦的 bug 交给我，我认真查完了",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True and body["accountId"] == "acc_a"
    from app.mood import store
    events = store.list_events("acc_a", "default", limit=5)
    assert events and events[0]["source"] == "dsh"
    assert len(events[0]["reason"]) <= 60


def test_mood_event_rejects_out_of_range(cfg):
    res = _client().post("/api/dsh/mood-event", json={
        "moodDelta": 99, "dims": {}, "confidence": 80, "reason": "x"})
    assert res.status_code == 400


def test_mood_event_skipped_when_judge_disabled(cfg):
    cfg["dsh"]["judge_enabled"] = False
    body = _client().post("/api/dsh/mood-event", json={
        "moodDelta": 3, "dims": {}, "confidence": 80, "reason": "x"}).json()
    assert body["applied"] is False and body["skipped"] == "judge-disabled"


def test_apply_judged_delta_shared_by_wechat_and_dsh(temp_db, cfg):
    from app.mood import service as mood_service
    from app.mood import store
    _seed_mood()
    result = mood_service.apply_judged_delta(
        "acc_a", "default",
        {"moodDelta": 6,
         "dims": {"happy": 6, "calm": 0, "sweet": 0,
                  "heartache": 0, "worry": 0, "sad": 0, "jealous": 0},
         "confidence": 90, "reason": "r" * 80},
        source="dsh",
    )
    assert result["applied"] is True and result["significant"] is True
    events = store.list_events("acc_a", "default", limit=1)
    assert events[0]["source"] == "dsh"
    assert events[0]["reason"] == "r" * 60
    assert events[0]["kind"] == "judge" and events[0]["minor"] == 0
