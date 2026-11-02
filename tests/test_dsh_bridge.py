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
