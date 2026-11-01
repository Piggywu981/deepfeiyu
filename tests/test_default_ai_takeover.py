"""「默认全 AI 接管」开关：新会话的初始 ai_enabled 跟随 auto.default_ai_on，已有会话不受影响。"""
from __future__ import annotations

import pytest

from app import config, db


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


def _set_auto(monkeypatch, **auto):
    """替换 config.load（不落盘），只覆盖 auto 里的指定键。"""
    base = config.load()
    monkeypatch.setattr(config, "load", lambda: {**base, "auto": {**base["auto"], **auto}})


def test_new_chat_defaults_to_manual(temp_db, monkeypatch):
    _set_auto(monkeypatch, default_ai_on=False)  # 隔离本机 data/config.json 的真实开关
    uid = db.upsert_chat("acc_default", "wxid_a", "甲", False, "你好")
    assert db.get_chat(uid)["ai_enabled"] is False


def test_new_chat_ai_takeover_when_enabled(temp_db, monkeypatch):
    _set_auto(monkeypatch, default_ai_on=True)
    uid = db.upsert_chat("acc_default", "wxid_a", "甲", False, "你好")
    assert db.get_chat(uid)["ai_enabled"] is True


def test_meta_insert_follows_default_too(temp_db, monkeypatch):
    """白名单同步等只登记身份的路径（upsert_chat_meta）同样跟随开关。"""
    _set_auto(monkeypatch, default_ai_on=True)
    uid = db.upsert_chat_meta("acc_default", "123@chatroom", "某群", True)
    assert db.get_chat(uid)["ai_enabled"] is True


def test_existing_chat_toggle_not_overridden(temp_db, monkeypatch):
    """开关只决定新建会话的初始值；用户手动关掉 AI 的会话保持原样。"""
    uid = db.upsert_chat("acc_default", "wxid_b", "乙", False, "你好")
    db.set_chat_ai(uid, False)
    _set_auto(monkeypatch, default_ai_on=True)
    db.upsert_chat("acc_default", "wxid_b", "乙", False, "又一条消息")
    db.upsert_chat_meta("acc_default", "wxid_b", "乙", False)
    assert db.get_chat(uid)["ai_enabled"] is False


def test_default_config_option_exists():
    assert config.DEFAULTS["auto"]["default_ai_on"] is False
