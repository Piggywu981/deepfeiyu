"""撤回消息：通知解析、回溯标记、读取出口的 is_revoked 字段。"""
import pytest

from app import db
from app.wechat.base import parse_revoke_notice


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


UID = "acc_default:wxid_demo"


def _add(content, sender="张三", ts=1000.0, is_self=False, msg_type="text", mid=None):
    db.add_message(
        {
            "id": mid or f"m-{content}-{ts}",
            "chat_uid": UID,
            "sender_name": "我" if is_self else sender,
            "content": content,
            "msg_type": msg_type,
            "is_group": True,
            "is_self": is_self,
            "ts": ts,
        }
    )


def test_parse_notice_self():
    assert parse_revoke_notice("你撤回了一条消息") == ""
    assert parse_revoke_notice("你撤回了一条消息 ") == ""


def test_parse_notice_named():
    assert parse_revoke_notice("张三 撤回了一条消息") == "张三"
    assert parse_revoke_notice("张三撤回了 一条消息") == "张三"


def test_parse_notice_xml():
    xml = (
        '<sysmsg type="revokemsg"><revokemsg>'
        "<newmsgid>123</newmsgid>"
        "<replacemsg><![CDATA[李四 撤回了一条消息]]></replacemsg>"
        "</revokemsg></sysmsg>"
    )
    assert parse_revoke_notice(xml) == "李四"


def test_parse_notice_non_revoke():
    assert parse_revoke_notice("你已添加了张三，现在可以开始聊天了。") is None
    assert parse_revoke_notice("张三 领取了你的红包") is None
    assert parse_revoke_notice("") is None
    assert parse_revoke_notice(None) is None


def test_mark_revoked_latest_match(temp_db):
    _add("第一条", ts=1000.0)
    _add("第二条", ts=1200.0)
    revoked = db.mark_message_revoked(UID, "张三", notice_ts=1250.0)
    assert revoked and revoked["content"] == "第二条"
    rows = {m["content"]: m["is_revoked"] for m in db.list_messages(UID)}
    assert rows["第一条"] is False and rows["第二条"] is True


def test_mark_revoked_self(temp_db):
    _add("我的消息", is_self=True, ts=1000.0)
    _add("对方消息", sender="张三", ts=1001.0)
    revoked = db.mark_message_revoked(UID, "", notice_ts=1100.0)
    assert revoked and revoked["content"] == "我的消息"


def test_mark_revoked_no_match_name(temp_db):
    _add("张三的消息", sender="张三", ts=1000.0)
    assert db.mark_message_revoked(UID, "李四", notice_ts=1100.0) is None


def test_mark_revoked_out_of_window(temp_db):
    _add("太久之前", ts=100.0)
    assert db.mark_message_revoked(UID, "张三", notice_ts=1000.0) is None


def test_mark_revoked_consecutive(temp_db):
    _add("一", ts=1000.0)
    _add("二", ts=1100.0)
    first = db.mark_message_revoked(UID, "张三", notice_ts=1150.0)
    second = db.mark_message_revoked(UID, "张三", notice_ts=1160.0)
    assert first["content"] == "二" and second["content"] == "一"


def test_mark_revoked_skips_system_and_self(temp_db):
    _add("[系统消息]", msg_type="system", sender="张三", ts=1000.0)
    _add("我发的", is_self=True, ts=1001.0)
    assert db.mark_message_revoked(UID, "张三", notice_ts=1100.0) is None
