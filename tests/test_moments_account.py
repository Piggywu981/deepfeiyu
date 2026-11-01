"""朋友圈账号归属：缓存必须属于当前解密的微信号，切号要清空重建、水位要能修。"""
from __future__ import annotations

import time

import pytest

from app import db
from app.wechat.moments import MomentsService


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


def _feed(feed_id: str, username: str, nickname: str, text: str) -> dict:
    return {
        "id": feed_id,
        "username": username,
        "nickname": nickname,
        "text": text,
        "location": {},
        "create_time": 1761000000,
        "images": [],
        "videos": [],
        "likes": [],
        "comments": [],
    }


class FakeMdb:
    """只实现 MomentsService 用到的 MomentDB 接口。"""

    def __init__(self, feeds: list[dict], latest_tid: int | None = 100) -> None:
        self.feeds = feeds
        self.tid = latest_tid

    def latest_tid(self):
        return self.tid

    def get_moments_since(self, since_tid=None, limit=200):
        latest = self.latest_tid()
        if latest is None:
            return [], None
        if since_tid is not None and latest <= int(since_tid):
            return [], None
        return list(self.feeds[:limit]), latest

    def get_moments(self, limit=50):
        return list(self.feeds[:limit])

    def get_interactions(self, limit=100):
        return []


def _service(owner: str = "wxid_bot_abcd") -> MomentsService:
    session = type("S", (), {"account_id": "acc", "nickname": "鲸鲸"})()
    driver = type("D", (), {})()
    driver.db = type("DB", (), {"account": owner})()
    return MomentsService(session, driver=driver)


def test_sync_rebuilds_cache_when_owner_changes(temp_db, monkeypatch):
    """缓存里是另一个微信号（号主个人号）的好友动态：切到机器人号后必须清空重建。"""
    # 旧的错误缓存：号主个人号的好友
    db.upsert_moment("acc", _feed("1", "wxid_friend_of_owner", "号主好友", "旧动态"))
    db.add_moment_interaction(
        "acc",
        {
            "local_id": 1,
            "create_time": time.time(),
            "type": 1,
            "feed_id": "1",
            "from_nickname": "号主好友",
        },
    )
    db.kv_set("moments:acc:tid", 999)

    svc = _service("wxid_bot_abcd")
    mdb = FakeMdb([_feed("2", "wxid_iya", "iYa", "机器人号的好友动态")], latest_tid=100)
    monkeypatch.setattr(svc, "_mdb", lambda: mdb)

    stat = svc.sync_once()

    assert stat.get("rebuilt") is True
    assert stat["new_feeds"] == 1
    # 旧数据清空，只剩机器人号的好友动态
    rows = db.list_moments("acc", limit=50)
    assert [r["feed_id"] for r in rows] == ["2"]
    assert db.list_moment_interactions("acc") == []
    assert db.kv_get("moments:acc:owner") == "wxid_bot_abcd"
    assert db.kv_get("moments:acc:tid") == 100


def test_sync_keeps_cache_when_owner_matches(temp_db, monkeypatch):
    db.upsert_moment("acc", _feed("1", "wxid_iya", "iYa", "已有动态"))
    db.kv_set("moments:acc:owner", "wxid_bot_abcd")
    db.kv_set("moments:acc:tid", 100)

    svc = _service("wxid_bot_abcd")
    mdb = FakeMdb([_feed("1", "wxid_iya", "iYa", "已有动态")], latest_tid=100)
    monkeypatch.setattr(svc, "_mdb", lambda: mdb)

    stat = svc.sync_once()

    assert not stat.get("rebuilt")
    assert stat["new_feeds"] == 0
    assert len(db.list_moments("acc", limit=50)) == 1


def test_repair_watermark_resets_foreign_tid(temp_db, monkeypatch):
    """水位比当前账号最新 tid 还新：来自别的微信号，重置后要能重新增量同步。"""
    db.kv_set("moments:acc:owner", "wxid_bot_abcd")
    db.kv_set("moments:acc:tid", 999)  # 别的号留下的、比当前号最新的 100 还新

    svc = _service("wxid_bot_abcd")
    mdb = FakeMdb([_feed("2", "wxid_iya", "iYa", "机器人号的新动态")], latest_tid=100)
    monkeypatch.setattr(svc, "_mdb", lambda: mdb)

    stat = svc.sync_once()

    assert stat["new_feeds"] == 1
    assert db.kv_get("moments:acc:tid") == 100


def test_refresh_deep_opens_ui_then_syncs(temp_db, monkeypatch):
    svc = _service("wxid_bot_abcd")
    db.kv_set("moments:acc:owner", "wxid_bot_abcd")
    mdb = FakeMdb([_feed("3", "wxid_iya", "iYa", "刷新到的新动态")], latest_tid=200)
    monkeypatch.setattr(svc, "_mdb", lambda: mdb)
    calls: list[bool] = []

    def fake_deep_sync():
        calls.append(True)
        return {"ui_opened": True, "ui_error": ""}

    monkeypatch.setattr(svc, "deep_sync", fake_deep_sync)

    stat = svc.refresh(deep=True)

    assert calls == [True]
    assert stat["deep"] is True
    assert stat["ui_opened"] is True
    assert stat["new_feeds"] == 1


def test_clear_moments_counts_and_empties(temp_db):
    db.upsert_moment("acc", _feed("1", "u", "n", "x"))
    db.upsert_moment("acc", _feed("2", "u", "n", "y"))
    db.add_moment_interaction("acc", {"local_id": 7, "type": 2, "feed_id": "1"})

    cleared = db.clear_moments("acc")

    assert cleared == {"feeds": 2, "interactions": 1}
    assert db.count_moments("acc") == 0
    assert db.list_moment_interactions("acc") == []


def test_sync_refuses_when_driver_account_mismatch(temp_db, monkeypatch):
    """驱动解密号与配置指定的号不一致：既不清缓存，也不把错号的动态写进来。"""
    db.upsert_moment("acc", _feed("1", "wxid_iya", "iYa", "机器人号的好友"))
    db.kv_set("moments:acc:owner", "wxid_bot_abcd")

    session = type(
        "S",
        (),
        {
            "account_id": "acc",
            "nickname": "鲸鲸",
            "account": {"weixin4": {"account_dir": "wxid_bot_abcd"}},
        },
    )()
    driver = type("D", (), {})()
    driver.db = type("DB", (), {"account": "wxid_owner_personal"})()
    svc = MomentsService(session, driver=driver)
    mdb = FakeMdb([_feed("9", "wxid_owner_friend", "号主好友", "错号的动态")], latest_tid=500)
    monkeypatch.setattr(svc, "_mdb", lambda: mdb)

    with pytest.raises(RuntimeError, match="账号不一致"):
        svc.sync_once()

    # 旧缓存保留、没有被错号数据覆盖
    assert [r["feed_id"] for r in db.list_moments("acc", limit=50)] == ["1"]
    assert db.kv_get("moments:acc:owner") == "wxid_bot_abcd"


def test_poll_interval_default_and_floor(monkeypatch):
    """一轮轮询都要解密一次 sns.db（外加 WAL 合并），默认间隔不能再是 120s：
    内存吃紧的机器上，全天 720 次解密是压在微信与其它应用上的固定负载。"""
    from app import config
    from app.wechat import moments

    assert config.DEFAULTS["moments"]["poll_interval"] == 600
    monkeypatch.setattr(moments.config, "load", lambda: {"moments": {"enabled": True}})
    assert moments.poll_interval() == 600.0
    monkeypatch.setattr(moments.config, "load", lambda: {"moments": {"poll_interval": 5}})
    assert moments.poll_interval() == 30.0
    monkeypatch.setattr(moments.config, "load", lambda: {"moments": {"poll_interval": "abc"}})
    assert moments.poll_interval() == 600.0
