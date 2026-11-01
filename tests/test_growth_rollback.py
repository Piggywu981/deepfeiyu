"""回滚、冲突、巩固与衰减。"""
import time

import pytest

from app import db
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


def _trait(content="我说话先停半拍", source="auto"):
    return store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": content,
            "source": source,
            "confidence": 0.9,
            "evidence": [{"type": "message", "quote": content, "msg_ids": ["m1"]}],
        },
    )["trait"]


def test_rollback_trait_add_retires_it(temp_db):
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
    trait_id = int(event["entity_id"])
    rolled = store.rollback_event("acc_a", event["id"])
    assert rolled["restored"]["status"] == "retired"
    assert store.get_trait("acc_a", trait_id)["status"] == "retired"
    assert store.get_event("acc_a", event["id"])["reverted_by"] > 0


def test_rollback_conflict_and_force(temp_db):
    trait = _trait()
    run = store.start_run("acc_a", "manual", "2026-10-27")
    store.apply_reflection(
        "acc_a",
        run["id"],
        {
            "traits": [
                {
                    "decision": "auto",
                    "payload": {
                        "op": "update", "id": trait["id"], "kind": "voice",
                        "content": "我说话先停半拍",
                        "evidence": [{"type": "message", "quote": "嗯", "msg_ids": ["m1"]}],
                    },
                }
            ]
        },
    )
    event = next(e for e in store.list_events("acc_a") if e["kind"] == "trait.update")
    store.update_trait("acc_a", trait["id"], {"content": "我说话开始变长了"})
    with pytest.raises(RuntimeError):
        store.rollback_event("acc_a", event["id"])
    rolled = store.rollback_event("acc_a", event["id"], force=True)
    assert rolled["restored"]["content"] == "我说话先停半拍"


def test_rollback_journal_and_summary(temp_db):
    run = store.start_run("acc_a", "manual", "2026-10-27")
    store.apply_reflection(
        "acc_a",
        run["id"],
        {"journal": {"day": "2026-10-27", "content": "今天学会了停顿"}, "summary": "我现在更爱接梗了"},
    )
    journal_event = next(e for e in store.list_events("acc_a") if e["kind"] == "journal.add")
    summary_event = next(e for e in store.list_events("acc_a") if e["kind"] == "summary.update")
    store.rollback_event("acc_a", journal_event["id"])
    assert store.list_journal("acc_a") == []
    store.rollback_event("acc_a", summary_event["id"])
    assert store.get_state("acc_a")["self_summary"] == ""


def test_consolidate_and_decay(temp_db):
    _trait("我说话喜欢先停半拍")
    _trait("我说话喜欢先停顿半拍")
    merged = store.consolidate_traits("acc_a")
    assert merged == 1
    traits = store.list_traits("acc_a")
    active = [t for t in traits if t["status"] == "active"]
    retired = [t for t in traits if t["status"] == "retired"]
    assert len(active) == 1 and len(retired) == 1
    assert len(active[0]["evidence"]) == 2

    # 把强度压到 1、last_seen 拨回 31 天前，再衰减到休眠
    with db.transaction() as conn:
        conn.execute(
            "UPDATE growth_traits SET strength=1, last_seen_at=? WHERE account_id=? AND status='active'",
            (time.time() - 31 * 86400, "acc_a"),
        )
    changed = store.decay_traits("acc_a", days=30)
    assert changed == 1
    assert store.list_traits("acc_a", status="dormant")


def test_cleanup_journals(temp_db):
    store.upsert_journal("acc_a", "2020-01-01", "很旧的日记")
    store.upsert_journal("acc_a", "2026-10-27", "今天的日记")
    removed = store.cleanup_journals("acc_a", keep_days=30)
    assert removed == 1
    assert [j["day"] for j in store.list_journal("acc_a")] == ["2026-10-27"]
