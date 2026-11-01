"""心情 API：状态/时间线/配置/槽位/重置/试判断/素材。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.mood import service, store


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
def api(temp_db, monkeypatch):
    from app import main
    from app.mood import api as mood_api

    monkeypatch.setattr(config, "load", lambda: {
        "mood": {"enabled": True, "accounts": {"acc_a": {}}},
        "server": {"access_token": ""},
    })
    monkeypatch.setattr(config, "account", lambda aid: {"id": aid} if aid == "acc_a" else None)
    monkeypatch.setattr(config, "active_id", lambda: "acc_a")
    monkeypatch.setattr(config, "persona_of", lambda account: ("p", "大肥鱼"))
    saved = {}
    monkeypatch.setattr(config, "save", lambda patch: saved.update(patch) or patch)
    client = TestClient(main.app)
    client.saved = saved
    return client


def test_state_shape(api):
    data = api.get("/api/mood/state").json()
    assert data["ok"] is True
    assert data["account_id"] == "acc_a"
    assert data["slot_id"] == "default"
    assert data["mood"] == 70
    assert [d["key"] for d in data["dims"]] == [
        "happy", "calm", "sweet", "heartache", "worry", "sad", "jealous"
    ]
    assert data["injectMode"] == "event"
    assert data["timeline"] == []


def test_state_payload_decays_on_copy_without_writing(api, monkeypatch):
    """挂饰轮询只读：mood 在副本上衰减，库里的状态与时间线都不能动。"""
    state = store.load_state("acc_a", "default")
    decayed_at = time.time() - 12 * 3600
    state.update(mood=100.0, last_decay_at=decayed_at, updated_at=1000.0)
    store.save_state("acc_a", "default", state)

    def boom(*args, **kwargs):
        raise RuntimeError("state_payload 不应写库")

    monkeypatch.setattr(store, "save_state", boom)
    data = service.state_payload("acc_a")
    assert data["moodValue"] == pytest.approx(85.0, abs=0.2)

    fresh = store.load_state("acc_a", "default")
    assert fresh["mood"] == 100.0
    assert fresh["last_decay_at"] == decayed_at
    assert store.list_events("acc_a", "default") == []


def test_config_writes_enabled(api):
    res = api.post("/api/mood/config", json={"enabled": True, "account": {"base": 60}})
    assert res.status_code == 200
    assert api.saved["mood"]["enabled"] is True
    assert api.saved["mood"]["accounts"]["acc_a"]["base"] == 60


def test_slot_lifecycle(api):
    res = api.post("/api/mood/slots", json={"action": "create", "slot_id": "s1", "label": "小号"})
    assert res.json()["slot"]["slot_id"] == "s1"
    res = api.post("/api/mood/slots", json={"action": "rename", "slot_id": "s1", "label": "大号"})
    assert res.json()["slot"]["label"] == "大号"
    res = api.post("/api/mood/slots", json={"action": "bind", "slot_id": "s1", "chat_uid": "u1"})
    assert res.json()["bindings"] == [{"slot_id": "s1", "chat_uid": "u1"}]
    res = api.post("/api/mood/slots", json={"action": "unbind", "chat_uid": "u1"})
    assert res.json()["bindings"] == []
    res = api.post("/api/mood/slots", json={"action": "delete", "slot_id": "s1"})
    assert res.status_code == 200
    assert api.post("/api/mood/slots", json={"action": "delete", "slot_id": "default"}).status_code == 400


def test_reset_writes_manual_event(api):
    state = store.load_state("acc_a", "default")
    state.update(mood=90.0, last_reason="之前的事", updated_at=10.0)
    store.save_state("acc_a", "default", state)
    res = api.post("/api/mood/reset", json={"mood": 70})
    assert res.status_code == 200
    assert res.json()["mood"] == 70
    events = store.list_events("acc_a", "default")
    assert events[0]["kind"] == "manual"
    assert events[0]["source"] == "console"


def test_timeline_paging(api):
    for i in range(3):
        store.add_event("acc_a", "default", at=float(10 + i), mood=70, delta=1,
                        dims={}, dims_delta={}, reason=f"r{i}", confidence=80,
                        kind="judge", source="user", minor=0)
    data = api.get("/api/mood/timeline?limit=2").json()
    assert [x["reason"] for x in data["events"]] == ["r2", "r1"]
    older = api.get("/api/mood/timeline?limit=2&before=11").json()
    assert [x["reason"] for x in older["events"]] == ["r0"]


def test_judge_test_preview_does_not_write(api, monkeypatch):
    async def fake_judge(*args, **kwargs):
        return {"moodDelta": 3, "dims": {k: 0 for k in
                ("happy", "calm", "sweet", "heartache", "worry", "sad", "jealous")},
                "reason": "号主夸我", "confidence": 88}

    from app.mood import judge as judge_mod
    monkeypatch.setattr(judge_mod, "judge", fake_judge)
    res = api.post("/api/mood/judge-test", json={"text": "你真棒", "reply": "嘿嘿"})
    assert res.status_code == 200
    assert res.json()["delta"]["moodDelta"] == 3
    assert store.list_events("acc_a", "default") == []
    assert store.load_state("acc_a", "default")["updated_at"] == 0.0


def test_assets_and_widget(api):
    assert api.get("/api/mood/asset/face-good.png").status_code == 200
    assert api.get("/api/mood/asset/../../config.json").status_code == 404
    widget = api.get("/api/mood/widget.js")
    assert widget.status_code == 200
    assert "/api/mood" in widget.text


def test_index_loads_widget_from_static():
    from app import main

    html = (main.STATIC / "index.html").read_text("utf-8")
    assert '<script src="/static/mood-widget.js"' in html
    assert '<script src="/api/mood/widget.js"' not in html


def test_widget_uses_public_asset_paths():
    from app import main

    script = (main.STATIC / "mood-widget.js").read_text("utf-8")
    assert "/mood-assets" in script
    assert "/api/mood/state" in script
    assert "B + '/asset/" not in script


def test_mood_assets_public_even_with_token(api, monkeypatch):
    # 静态素材不走 /api，令牌中间件不该拦；同时 API 仍必须带令牌（没有放宽鉴权）
    from app import main

    ok = api.get("/mood-assets/face-good.png")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/png")

    monkeypatch.setattr(config, "load", lambda: {
        "mood": {"enabled": True, "accounts": {"acc_a": {}}},
        "server": {"access_token": "secret"},
    })
    client = TestClient(main.app)
    img = client.get("/mood-assets/face-good.png")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/png")
    assert client.get("/api/mood/state").status_code == 401


def test_writes_require_local(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.mood.api import _require_local

    ok = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    _require_local(ok)
    bad = SimpleNamespace(client=SimpleNamespace(host="8.8.8.8"))
    with pytest.raises(HTTPException) as exc:
        _require_local(bad)
    assert exc.value.status_code == 403
