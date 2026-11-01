"""成长账本：表结构、事务、状态与特质 CRUD、账号/会话隔离。"""
import pytest

from app import db
from app.growth import sanitize, store


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


TABLES = {
    "growth_state",
    "growth_traits",
    "growth_journal",
    "growth_goals",
    "growth_proposals",
    "growth_events",
    "growth_runs",
}


def test_growth_tables_and_version(temp_db):
    names = {
        r[0]
        for r in db.read_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert TABLES <= names
    assert db.read_conn().execute("PRAGMA user_version").fetchone()[0] == 6


def test_transaction_rolls_back(temp_db):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO kv(k,v) VALUES('growth-x','1')")
            raise RuntimeError("boom")
    assert db.kv_get("growth-x") is None


def test_state_and_summary(temp_db):
    state = store.get_state("acc_a")
    assert state["self_summary"] == ""
    assert store.set_summary("acc_a", "我现在更爱接梗了", run_id=3) == {
        "before": "",
        "after": "我现在更爱接梗了",
    }
    assert store.get_state("acc_a")["self_summary"] == "我现在更爱接梗了"
    assert store.get_state("acc_b")["self_summary"] == ""


def test_upsert_trait_dedupes_and_strengthens(temp_db):
    first = store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话喜欢先停半拍",
            "tags": ["口癖"],
            "strength": 1,
            "confidence": 0.8,
            "evidence": [{"type": "message", "quote": "嗯……", "msg_ids": ["m1"]}],
        },
    )
    assert first["action"] == "added"
    assert first["trait"]["strength"] == 1

    second = store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": " 我说话喜欢先停半拍 ",
            "tags": ["口癖", "停顿"],
            "strength": 3,
            "confidence": 0.9,
            "evidence": [{"type": "message", "quote": "我想想", "msg_ids": ["m2"]}],
        },
    )
    assert second["action"] == "updated"
    assert second["trait"]["id"] == first["trait"]["id"]
    assert second["trait"]["strength"] == 3
    assert [e["quote"] for e in second["trait"]["evidence"]] == ["嗯……", "我想想"]
    assert second["trait"]["tags"] == "口癖,停顿"
    assert store.list_traits("acc_a")[0]["id"] == first["trait"]["id"]


def test_trait_scope_and_account_isolation(temp_db):
    a = store.upsert_trait(
        "acc_a",
        {
            "kind": "relationship",
            "content": "和老王聊天喜欢互损",
            "scope": "chat",
            "chat_uid": "acc_a:g1",
            "evidence": [{"type": "message", "quote": "你又说错了", "msg_ids": ["m1"]}],
        },
    )
    b = store.upsert_trait(
        "acc_a",
        {
            "kind": "relationship",
            "content": "和老王聊天喜欢互损",
            "scope": "chat",
            "chat_uid": "acc_a:g2",
            "evidence": [{"type": "message", "quote": "哈哈", "msg_ids": ["m2"]}],
        },
    )
    assert a["trait"]["id"] != b["trait"]["id"]
    assert len(store.list_traits("acc_a")) == 2
    assert store.list_traits("acc_b") == []
    assert [t["chat_uid"] for t in store.list_active_traits("acc_a", "acc_a:g1")] == [
        "acc_a:g1"
    ]
    assert store.list_active_traits("acc_a", "") == []


def test_owner_trait_not_modified_by_auto(temp_db):
    owner = store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话简短",
            "source": "owner",
            "strength": 2,
            "confidence": 1.0,
            "evidence": [{"type": "console", "quote": "以后简短点", "turn_ids": [1]}],
        },
    )
    auto = store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话简短",
            "source": "auto",
            "strength": 3,
            "confidence": 0.9,
            "evidence": [{"type": "message", "quote": "好", "msg_ids": ["m9"]}],
        },
    )
    assert auto["trait"]["source"] == "owner"
    assert auto["trait"]["strength"] == 2
    assert len(auto["trait"]["evidence"]) == 2


def test_update_trait_recomputes_hash_and_rejects_collision(temp_db):
    one = store.upsert_trait("acc_a", {"kind": "opinion", "content": "我偏保守"})
    two = store.upsert_trait("acc_a", {"kind": "opinion", "content": "我偏激进"})
    changed = store.update_trait("acc_a", one["trait"]["id"], {"content": "我偏中性"})
    assert changed["before"]["content"] == "我偏保守"
    assert changed["trait"]["content"] == "我偏中性"
    with pytest.raises(ValueError):
        store.update_trait("acc_a", one["trait"]["id"], {"content": "我偏激进"})
    assert store.get_trait("acc_a", two["trait"]["id"])["content"] == "我偏激进"


def test_status_lifecycle(temp_db):
    row = store.upsert_trait("acc_a", {"kind": "habit", "content": "我习惯先问一句"})
    retired = store.set_trait_status("acc_a", row["trait"]["id"], "retired")
    assert retired["trait"]["status"] == "retired"
    assert retired["trait"]["retired_at"] > 0
    active = store.set_trait_status("acc_a", row["trait"]["id"], "active")
    assert active["trait"]["status"] == "active"
    assert active["trait"]["retired_at"] == 0


def test_content_hash_normalizes_whitespace():
    assert sanitize.content_hash("voice", "global", "", "  我 爱  钓鱼 ") == sanitize.content_hash(
        "voice", "global", "", "我 爱 钓鱼"
    )


def test_journal_one_per_day_and_delete(temp_db):
    first = store.upsert_journal("acc_a", "2026-10-27", "今天学会了接梗", run_id=1)
    assert first["action"] == "added"
    second = store.upsert_journal("acc_a", "2026-10-27", "今天学会了接梗，还学会了停顿", run_id=2)
    assert second["action"] == "updated"
    assert second["before"]["content"] == "今天学会了接梗"
    assert len(store.list_journal("acc_a")) == 1
    assert store.list_journal("acc_b") == []
    deleted = store.delete_journal("acc_a", first["journal"]["id"])
    assert deleted["journal"]["content"].startswith("今天学会了接梗")
    assert store.list_journal("acc_a") == []


def test_goals_lifecycle(temp_db):
    goal = store.add_goal("acc_a", "想学会接老王的钓鱼梗")
    assert goal["status"] == "open"
    updated = store.update_goal("acc_a", goal["id"], {"status": "done", "progress": "接住两个"})
    assert updated["goal"]["status"] == "done"
    assert updated["before"]["status"] == "open"
    assert store.list_goals("acc_a", status="open") == []
    assert store.list_goals("acc_a", status="done")[0]["progress"] == "接住两个"


def test_proposals_roundtrip_and_decide(temp_db):
    proposal = store.create_proposal(
        "acc_a",
        "trait",
        {"op": "update", "id": 1, "content": "我改主意了"},
        before={"content": "我原来的看法"},
        reason="立场大幅转弯",
        evidence=[{"type": "message", "quote": "其实也对", "msg_ids": ["m1"]}],
    )
    assert proposal["status"] == "pending"
    assert proposal["payload"]["content"] == "我改主意了"
    assert proposal["evidence"][0]["quote"] == "其实也对"
    assert len(store.list_proposals("acc_a")) == 1
    decided = store.decide_proposal("acc_a", proposal["id"], "rejected")
    assert decided["status"] == "rejected"
    assert store.list_proposals("acc_a") == []


def test_events_and_runs(temp_db):
    event = store.add_growth_event(
        "acc_a", "trait.add", "trait", 7, before=None, after={"content": "x"}
    )
    assert event["after"] == {"content": "x"}
    assert store.list_events("acc_a")[0]["id"] == event["id"]

    run = store.start_run("acc_a", "auto", "2026-10-27")
    assert run["status"] == "running"
    finished = store.finish_run(run["id"], "ok", stats={"messages": 12}, cost=0.01)
    assert finished["status"] == "ok"
    assert finished["stats"]["messages"] == 12
    assert store.last_successful_run("acc_a")["id"] == run["id"]
    assert store.count_runs_today("acc_a", "2026-10-27") == 1
    assert store.count_runs_today("acc_a", "2026-10-27", trigger="manual") == 0


def test_mark_stale_runs(temp_db):
    run = store.start_run("acc_a", "auto", "2026-10-27")
    assert store.mark_stale_runs("acc_a") == 1
    assert store.list_runs("acc_a")[0]["id"] == run["id"]
    assert store.list_runs("acc_a")[0]["status"] == "error"


def test_export_markdown_and_json(temp_db):
    store.set_summary("acc_a", "我现在更爱接梗")
    store.upsert_trait("acc_a", {"kind": "voice", "content": "我说话先停半拍"})
    store.upsert_journal("acc_a", "2026-10-27", "今天学会了停顿")
    store.add_goal("acc_a", "接住老王的梗")
    md = store.export_markdown("acc_a")
    assert "# 自我成长导出" in md
    assert "我现在更爱接梗" in md
    assert "我说话先停半拍" in md
    assert "今天学会了停顿" in md
    payload = store.export_json("acc_a")
    assert payload["state"]["self_summary"] == "我现在更爱接梗"
    assert payload["traits"][0]["content"] == "我说话先停半拍"
