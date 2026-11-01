"""朋友圈数据层：动态入库去重、互动刷新、互动通知与搜索。"""
import pytest

from app import db


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


FEED_A = {
    "id": "10001",
    "username": "wxid_alice",
    "nickname": "小爱",
    "text": "今天去爬山了，风景不错",
    "location": {"latitude": "30.1", "longitude": "120.2"},
    "create_time": 1761000000,
    "images": [{"md5": "c" * 32, "url": "https://cdn/a.jpg", "size": 1, "width": 10, "height": 10}],
    "videos": [],
    "likes": [{"username": "wxid_bob", "nickname": "阿波", "content": ""}],
    "comments": [{"username": "wxid_bob", "nickname": "阿波", "content": "带我下次"}],
}


def test_upsert_moment_new_and_refresh(temp_db):
    assert db.upsert_moment("acc_a", FEED_A) is True
    assert db.upsert_moment("acc_a", FEED_A) is False  # 同一条不再算新
    row = db.get_moment("acc_a", "10001", self_wxid="wxid_me")
    assert row["nickname"] == "小爱"
    assert row["location"] == "30.1,120.2"
    assert row["likes"][0]["nickname"] == "阿波"
    assert row["is_self"] is False


def test_upsert_moment_updates_interactions(temp_db):
    db.upsert_moment("acc_a", FEED_A)
    updated = dict(FEED_A, likes=FEED_A["likes"] + [{"username": "wxid_c", "nickname": "小陈", "content": ""}])
    db.upsert_moment("acc_a", updated)
    row = db.get_moment("acc_a", "10001")
    assert len(row["likes"]) == 2


def test_upsert_moment_requires_feed_id(temp_db):
    assert db.upsert_moment("acc_a", dict(FEED_A, id="")) is False
    assert db.list_moments("acc_a") == []


def test_list_moments_keyword_and_account_scope(temp_db):
    db.upsert_moment("acc_a", FEED_A)
    db.upsert_moment("acc_a", dict(FEED_A, id="10002", nickname="阿波", text="加班到深夜", username="wxid_bob"))
    db.upsert_moment("acc_b", dict(FEED_A, id="10003"))
    assert db.count_moments("acc_a") == 2
    assert db.count_moments("acc_b") == 1
    hits = db.list_moments("acc_a", keyword="爬山")
    assert [r["feed_id"] for r in hits] == ["10001"]
    hits = db.list_moments("acc_a", keyword="小爱")
    assert [r["feed_id"] for r in hits] == ["10001"]
    # create_time 倒序
    newest = db.list_moments("acc_a")[0]
    assert newest["feed_id"] == "10001"


def test_is_self_flag(temp_db):
    db.upsert_moment("acc_a", dict(FEED_A, username="wxid_me", nickname="我"))
    row = db.get_moment("acc_a", "10001", self_wxid="wxid_me")
    assert row["is_self"] is True
    row_other = db.get_moment("acc_a", "10001", self_wxid="wxid_someone")
    assert row_other["is_self"] is False


def test_interactions_dedupe_and_seen(temp_db):
    item = {
        "local_id": 7,
        "create_time": 1761000100,
        "type": 2,
        "feed_id": "10001",
        "from_username": "wxid_bob",
        "from_nickname": "阿波",
        "content": "带我下次",
        "relative_me": 1,
    }
    assert db.add_moment_interaction("acc_a", item) is True
    assert db.add_moment_interaction("acc_a", item) is False
    assert db.count_unread_interactions("acc_a") == 1
    rows = db.list_moment_interactions("acc_a")
    assert rows[0]["from_nickname"] == "阿波"
    assert rows[0]["relative_me"] == 1
    assert db.list_moment_interactions("acc_b") == []
    assert db.mark_moment_interactions_seen("acc_a") == 1
    assert db.count_unread_interactions("acc_a") == 0
    assert db.mark_moment_interactions_seen("acc_a") == 0


def test_interaction_without_local_id_ignored(temp_db):
    assert db.add_moment_interaction("acc_a", {"type": 1}) is False
