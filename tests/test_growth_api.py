"""成长 API：状态、特质、日记、目标、提案、时间线、导出、审批、回滚。"""
import time

import pytest

from app import config, db
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


@pytest.fixture()
def api(temp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.growth import api as growth_api

    monkeypatch.setattr(growth_api, "_aid", lambda request: "acc_a")
    monkeypatch.setattr(
        config, "account", lambda aid: {"id": aid} if aid == "acc_a" else None
    )
    monkeypatch.setattr(config, "growth_enabled", lambda aid: aid == "acc_a")
    monkeypatch.setattr(
        config,
        "growth_cfg",
        lambda: {
            "enabled": True,
            "accounts": {"acc_a": {"enabled": True}},
            "prompt_chars": 1200,
            "summary_chars": 800,
            "manual_max_per_day": 3,
            "min_new_messages": 20,
        },
    )
    monkeypatch.setattr(config, "save", lambda patch: patch)
    return TestClient(main.app)


def test_status_and_settings(api, monkeypatch):
    status = api.get("/api/growth/status").json()
    assert status["account_id"] == "acc_a"
    assert status["enabled"] is True
    assert status["pending"] == 0

    saved = {}
    monkeypatch.setattr(config, "save", lambda patch: saved.update(patch) or patch)
    resp = api.patch(
        "/api/growth/settings",
        json={"account_enabled": True, "prompt_chars": 1500},
    )
    assert resp.status_code == 200
    assert saved["growth"]["prompt_chars"] == 1500
    assert saved["growth"]["accounts"]["acc_a"]["enabled"] is True


def test_trait_crud(api):
    created = api.post(
        "/api/growth/traits",
        json={"kind": "voice", "content": "我说话先停半拍", "tags": ["口癖"]},
    )
    assert created.status_code == 200
    trait = created.json()["trait"]
    assert trait["source"] == "owner"
    assert trait["status"] == "active"

    listed = api.get("/api/growth/traits?q=停半拍").json()["items"]
    assert listed[0]["id"] == trait["id"]

    patched = api.patch(
        f"/api/growth/traits/{trait['id']}",
        json={"content": "我说话开始变短", "strength": 3},
    )
    assert patched.status_code == 200
    assert patched.json()["trait"]["strength"] == 3

    retired = api.post(f"/api/growth/traits/{trait['id']}/retire")
    assert retired.json()["trait"]["status"] == "retired"
    activated = api.post(f"/api/growth/traits/{trait['id']}/activate")
    assert activated.json()["trait"]["status"] == "active"


def test_journal_goals_and_summary(api):
    journal = store.upsert_journal("acc_a", "2026-10-27", "今天学会了停顿")
    assert api.get("/api/growth/journal").json()["items"][0]["id"] == journal["journal"]["id"]
    assert api.delete(f"/api/growth/journal/{journal['journal']['id']}").status_code == 200

    goal = api.post("/api/growth/goals", json={"content": "接住老王的梗"}).json()["goal"]
    patched = api.patch(
        f"/api/growth/goals/{goal['id']}", json={"status": "done", "progress": "接住两个"}
    )
    assert patched.json()["goal"]["status"] == "done"

    summary = api.patch("/api/growth/summary", json={"self_summary": "我现在更爱接梗了"})
    assert summary.status_code == 200
    assert api.get("/api/growth/summary").json()["self_summary"] == "我现在更爱接梗了"


def test_proposal_approve_and_reject(api):
    db.upsert_chat("acc_a", "c1", "群一", True, "可以", time.time() - 5)
    db.add_message(
        {
            "id": "m1",
            "account_id": "acc_a",
            "chat_uid": "acc_a:c1",
            "chat_id": "c1",
            "chat_name": "群一",
            "sender_name": "老王",
            "content": "我觉得可以",
            "msg_type": "text",
            "is_group": True,
            "ts": time.time(),
        }
    )
    existing = store.upsert_trait(
        "acc_a",
        {
            "kind": "value",
            "content": "我觉得钓鱼很无聊",
            "confidence": 1.0,
            "evidence": [{"type": "message", "quote": "无聊", "msg_ids": ["m1"]}],
        },
    )["trait"]
    proposal = store.create_proposal(
        "acc_a",
        "trait",
        {
            "op": "update",
            "id": existing["id"],
            "kind": "value",
            "content": "我觉得钓鱼特别有意思",
            "confidence": 0.95,
            "evidence": [{"type": "message", "quote": "可以", "msg_ids": ["m1"]}],
        },
        before=existing,
        reason="跳变",
    )
    approved = api.post(
        f"/api/growth/proposals/{proposal['id']}/approve", json={}
    )
    assert approved.status_code == 200
    assert store.get_trait("acc_a", existing["id"])["content"] == "我觉得钓鱼特别有意思"
    assert store.get_proposal("acc_a", proposal["id"])["status"] == "applied"

    pending = store.create_proposal("acc_a", "trait", {"op": "add", "content": "x"}, reason="测试")
    rejected = api.post(
        f"/api/growth/proposals/{pending['id']}/reject", json={"reason": "不像我"}
    )
    assert rejected.status_code == 200
    assert store.get_proposal("acc_a", pending["id"])["status"] == "rejected"


def test_events_and_rollback(api):
    run = store.start_run("acc_a", "manual", "2026-10-27")
    store.apply_reflection(
        "acc_a",
        run["id"],
        {
            "traits": [
                {
                    "decision": "auto",
                    "payload": {
                        "op": "add", "kind": "voice", "content": "我说话先停半拍",
                        "evidence": [{"type": "message", "quote": "嗯", "msg_ids": ["m1"]}],
                    },
                }
            ]
        },
    )
    event = next(e for e in store.list_events("acc_a") if e["kind"] == "trait.add")
    resp = api.post(f"/api/growth/events/{event['id']}/rollback", json={})
    assert resp.status_code == 200
    assert store.get_trait("acc_a", int(event["entity_id"]))["status"] == "retired"


def test_reflect_requires_enabled_and_returns_run(api, monkeypatch):
    from app.growth import api as growth_api

    async def fake_run(llm, account_id, trigger, run=None, now=None):
        return {"status": "ok", "run_id": run["id"] if run else 0}

    monkeypatch.setattr(growth_api.reflect, "run_reflection", fake_run)
    monkeypatch.setattr(growth_api, "_llm", lambda request: object())
    resp = api.post("/api/growth/reflect")
    assert resp.status_code == 200
    assert resp.json()["run_id"] > 0


def test_export(api):
    store.set_summary("acc_a", "我现在更爱接梗了")
    md = api.get("/api/growth/export?format=md")
    assert md.status_code == 200
    assert "# 自我成长导出" in md.text
    js = api.get("/api/growth/export?format=json")
    assert js.status_code == 200
    assert js.json()["state"]["self_summary"] == "我现在更爱接梗了"
