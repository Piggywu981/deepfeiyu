"""动态召回：相关性、强度、新近度、作用域与预算。"""
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


def _add(account, content, kind="voice", scope="global", chat_uid="", strength=2):
    return store.upsert_trait(
        account,
        {
            "kind": kind,
            "content": content,
            "scope": scope,
            "chat_uid": chat_uid,
            "strength": strength,
            "confidence": 0.9,
            "evidence": [{"type": "message", "quote": content, "msg_ids": ["m1"], "run_id": 1}],
        },
    )["trait"]


def test_recall_prefers_query_match(temp_db):
    _add("acc_a", "我说话喜欢先停半拍")
    _add("acc_a", "我对钓鱼完全没有兴趣")
    got = store.recall_traits("acc_a", "钓鱼", limit_chars=500)
    assert got[0]["content"] == "我对钓鱼完全没有兴趣"
    assert got[0]["_score"] > got[1]["_score"]


def test_recall_relationship_not_cross_chat(temp_db):
    _add("acc_a", "和老王聊天喜欢互损", kind="relationship", scope="chat", chat_uid="acc_a:g1")
    _add("acc_a", "我喜欢安静", kind="preference")
    got = store.recall_traits("acc_a", "老王", chat_uid="acc_a:g2", limit_chars=500)
    assert all("老王" not in t["content"] for t in got)
    got2 = store.recall_traits("acc_a", "老王", chat_uid="acc_a:g1", limit_chars=500)
    assert any("老王" in t["content"] for t in got2)


def test_recall_budget_and_kind_diversity(temp_db):
    for i in range(8):
        _add("acc_a", f"口癖{i}喜欢说嗯", kind="voice")
    for i in range(3):
        _add("acc_a", f"关系{i}和群友互损", kind="relationship", scope="chat", chat_uid="acc_a:g1")
    got = store.recall_traits("acc_a", "嗯 互损", chat_uid="acc_a:g1", limit_chars=120)
    assert sum(1 for t in got if t["kind"] == "voice") <= 4
    assert sum(len(t["content"]) for t in got) <= 120


def test_recall_skips_seed_and_retired(temp_db):
    seed = _add("acc_a", "我可能会喜欢钓鱼")
    store.set_trait_status("acc_a", seed["id"], "seed")
    retired = _add("acc_a", "我以前喜欢钓鱼")
    store.set_trait_status("acc_a", retired["id"], "retired")
    got = store.recall_traits("acc_a", "钓鱼", limit_chars=500)
    assert got == []
