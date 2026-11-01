"""表情/标签锁定：迁移、编辑冻结、标签锁防归并、站点入库默认锁。"""
from __future__ import annotations

import pytest

from app import db

MD5_A = "a" * 32
MD5_B = "b" * 32


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


def test_new_emoji_has_source_and_unlocked(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["source"] == ""
    assert fresh["locked"] == 0


def test_locked_emoji_blocks_tag_writes_and_delete(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_tags("acc_a", row["id"], "旧", "旧描述")
    assert db.set_emoji_locked("acc_a", row["id"], True)["locked"] == 1
    assert db.set_emoji_tags("acc_a", row["id"], "新", "新描述") is False
    assert db.update_emoji_tags("acc_a", row["id"], "新", "新描述") is False
    assert db.remove_emoji("acc_a", row["id"]) is False
    assert db.remove_emojis("acc_a", [row["id"]]) == 0
    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["tags"] == "旧"
    assert fresh["description"] == "旧描述"
    assert db.set_emoji_locked("acc_a", row["id"], False)["locked"] == 0
    assert db.set_emoji_tags("acc_a", row["id"], "新", "新描述") is True


def test_tag_lock_protects_name_and_locked_rows(temp_db):
    unlocked = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    locked = db.add_emoji("acc_a", "https://cdn/b", md5=MD5_B)
    db.set_emoji_tags("acc_a", unlocked["id"], "串子站,无语", "")
    db.set_emoji_tags("acc_a", locked["id"], "串子站,无语", "")
    db.set_emoji_locked("acc_a", locked["id"], True)
    db.set_tag_locked("acc_a", "串子站", True)

    changed = db.apply_tag_mapping("acc_a", {"串子站": "表情", "无语": "无语子"})

    assert changed == 1
    assert db.get_emoji("acc_a", unlocked["id"])["tags"] == "串子站,无语子"
    assert db.get_emoji("acc_a", locked["id"])["tags"] == "串子站,无语"
    assert db.list_locked_tags("acc_a") == ["串子站"]
    db.set_tag_locked("acc_a", "串子站", False)
    assert db.list_locked_tags("acc_a") == []


def test_import_site_emoji_marks_and_locks(temp_db):
    row, status = db.import_site_emoji(
        "acc_a", MD5_A, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    assert status == "added"
    assert row["source"] == "searchanythingfun"
    assert row["locked"] == 1
    assert row["tags"] == "串子站,新三国"
    db.ensure_tag_lock_default("acc_a", "串子站")
    assert db.list_locked_tags("acc_a") == ["串子站"]

    # 用户主动解锁后，重复入库不得锁回
    db.set_emoji_locked("acc_a", row["id"], False)
    db.set_tag_locked("acc_a", "串子站", False)
    again, status2 = db.import_site_emoji(
        "acc_a", MD5_A, ["串子站", "新三国"], "新描述"
    )
    assert status2 == "existing"
    assert again["locked"] == 0
    assert db.list_locked_tags("acc_a") == []
    db.ensure_tag_lock_default("acc_a", "串子站")
    assert db.list_locked_tags("acc_a") == []


def test_import_emoji_skips_locked_row(temp_db):
    row, _ = db.import_site_emoji("acc_a", MD5_A, ["串子站"], "旧")
    assert db.import_emoji("acc_a", MD5_A, tags="新标签", description="新描述") == "unchanged"
    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["tags"] == "串子站"
    assert fresh["description"] == "旧"
