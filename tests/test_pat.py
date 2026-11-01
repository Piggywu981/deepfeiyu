"""拍一拍：3.x 纯文本 / 4.x sysmsg XML 的识别、人名解析、入库与回复策略。"""
from __future__ import annotations

import time

import pytest

from app import db, rules
from app.wechat.base import (
    Message,
    format_pat_notice,
    parse_pat_notice,
    summarize_message_content,
)
from app.wechat.manager import AccountSession
from app.wechat.wcferry_driver import WcferryDriver
from app.wechat.webhook_driver import WebhookDriver
from app.wechat.weixin4_driver import Weixin4Driver


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


PAT_XML = """10000000002@chatroom:
<sysmsg type="pat">
<pat>
  <fromusername>tester_a</fromusername>
  <chatusername>10000000002@chatroom</chatusername>
  <pattedusername>wxid_friend_03</pattedusername>
  <patsuffix><![CDATA[]]></patsuffix>
  <patsuffixversion>0</patsuffixversion>
  <template><![CDATA["${tester_a}" 拍了拍我]]></template>
</pat>
</sysmsg>"""

PAT_XML_SELF = """<sysmsg type="pat"><pat>
<fromusername>wxid_self</fromusername>
<chatusername>room@chatroom</chatusername>
<pattedusername>wxid_lee</pattedusername>
<template><![CDATA[你 拍了拍"${wxid_lee}"]]></template>
</pat></sysmsg>"""


def test_parse_plain_pat_received():
    pat = parse_pat_notice('"Mandy" 拍了拍你')
    assert pat and pat["from_name"] == "Mandy"
    assert pat["to_self"] is True
    assert format_pat_notice(pat)[0] == "Mandy 拍了拍你"


def test_parse_plain_pat_self_to_other():
    pat = parse_pat_notice('你 拍了拍"李四"')
    assert pat and pat["from_self"] is True
    assert pat["to_name"] == "李四"
    assert format_pat_notice(pat)[0] == "我 拍了拍李四"


def test_parse_plain_group_pat_other_member():
    pat = parse_pat_notice('"张三" 拍了拍"李四"')
    assert pat and pat["to_self"] is False
    assert format_pat_notice(pat)[0] == "张三 拍了拍李四"


def test_parse_plain_pat_suffix():
    pat = parse_pat_notice("张三 拍了拍我的小脑袋")
    assert pat and pat["to_self"] is True
    assert pat["suffix"] == "的小脑袋"
    assert format_pat_notice(pat)[0] == "张三 拍了拍你的小脑袋"


def test_parse_quoted_nickname_containing_pat_phrase():
    # 昵称里本身带「拍了拍」时，分隔符要取引号之后那个
    pat = parse_pat_notice('"A拍了拍B" 拍了拍你')
    assert pat and pat["from_name"] == "A拍了拍B"
    assert pat["to_self"] is True


def test_parse_rejects_plain_text_with_pat_tag():
    # 普通聊天里恰好出现「<pat」不能当成系统通知
    assert parse_pat_notice("我用 <pat 标签写了个demo") is None


def test_parse_pat_xml_without_fields_is_rejected():
    assert parse_pat_notice('<sysmsg type="pat"></sysmsg>') is None


def test_parse_pat_xml_tolerates_missing_closing_pat():
    pat = parse_pat_notice(
        '<sysmsg type="pat"><fromusername>wxid_a</fromusername>'
        "<pattedusername>wxid_me</pattedusername>"
        "<template>拍了拍我</template></sysmsg>"
    )
    assert pat and pat["from_id"] == "wxid_a" and pat["to_self"] is True


def test_parse_pat_xml_resolves_wxid_names():
    text, info = format_pat_notice(
        parse_pat_notice(PAT_XML),
        lambda wxid: {"tester_a": "张三", "wxid_friend_03": "肥鱼"}.get(wxid, wxid),
        "wxid_friend_03",
    )
    assert text == "张三 拍了拍你"
    assert info["from_id"] == "tester_a"
    assert info["to_id"] == "wxid_friend_03"
    assert info["to_self"] is True and info["from_self"] is False


def test_parse_pat_xml_self_actor():
    text, info = format_pat_notice(
        parse_pat_notice(PAT_XML_SELF),
        lambda wxid: {"wxid_lee": "李四"}.get(wxid, wxid),
        "wxid_self",
    )
    assert text == "我 拍了拍李四"
    assert info["from_self"] is True and info["to_self"] is False


def test_summarize_pat_xml_is_readable():
    summary = summarize_message_content(PAT_XML, "system")
    assert summary == "tester_a 拍了拍你"
    assert "[系统消息]" not in summary


def test_pat_message_to_dict_keeps_meta():
    msg = Message(chat_id="c", chat_name="群", content=PAT_XML, msg_type="pat")
    payload = msg.to_dict()
    assert payload["msg_type"] == "pat"
    assert payload["content"] == "tester_a 拍了拍你"
    assert payload["meta"]["pat"]["to_self"] is True


def _cfg(**auto):
    base = {
        "enabled": True,
        "group_need_at": True,
        "emoji_reply": True,
        "pat_reply": True,
        "only_whitelist": False,
        "whitelist": [],
        "blacklist": [],
        "min_interval_sec": 0,
        "daily_limit": 0,
        "working_hours": {"enabled": False},
    }
    base.update(auto)
    return {"auto": base, "ai": {"vision_enabled": False}}


def _chat(group: bool) -> dict:
    return {
        "uid": "acc:chat",
        "chat_id": "room@chatroom" if group else "wxid_a",
        "name": "群" if group else "张三",
        "ai_enabled": True,
        "pause_until": 0,
    }


def _pat_msg(group: bool, to_self: bool) -> Message:
    pat = {
        "from_id": "wxid_a",
        "from_name": "张三",
        "to_id": "wxid_self" if to_self else "wxid_b",
        "to_name": "你" if to_self else "李四",
        "suffix": "",
        "from_self": False,
        "to_self": to_self,
        "text": "张三 拍了拍你" if to_self else "张三 拍了拍李四",
    }
    return Message(
        chat_id="room@chatroom" if group else "wxid_a",
        chat_name="群" if group else "张三",
        sender_id="wxid_a",
        sender_name="张三",
        content=pat["text"],
        msg_type="pat",
        is_group=group,
        raw={"pat": pat},
    )


def test_rules_allow_private_pat():
    allowed, reason = rules.check(_cfg(), {"id": "acc", "nickname": "肥鱼"}, _chat(False), _pat_msg(False, True))
    assert allowed, reason


def test_rules_group_pat_me_needs_no_at():
    allowed, reason = rules.check(_cfg(), {"id": "acc", "nickname": "肥鱼"}, _chat(True), _pat_msg(True, True))
    assert allowed, reason


def test_rules_group_pat_others_is_ignored():
    allowed, reason = rules.check(_cfg(), {"id": "acc", "nickname": "肥鱼"}, _chat(True), _pat_msg(True, False))
    assert not allowed and "不是拍我" in reason


def test_rules_pat_reply_can_be_disabled():
    allowed, reason = rules.check(
        _cfg(pat_reply=False), {"id": "acc", "nickname": "肥鱼"}, _chat(False), _pat_msg(False, True)
    )
    assert not allowed and "自动回复未开启" in reason


class _WcfMsg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _wcferry() -> tuple[WcferryDriver, list[Message]]:
    got: list[Message] = []
    driver = WcferryDriver(on_message=got.append)
    driver._self_wxid = "wxid_self"
    driver._contacts = {"wxid_a": "张三", "room@chatroom": "吃瓜群"}
    driver._contacts_at = time.time()
    return driver, got


def test_wcferry_receives_pat_instead_of_dropping_system():
    driver, got = _wcferry()
    driver._dispatch(
        _WcfMsg(
            type=10000,
            content='"张三" 拍了拍你',
            sender="wxid_a",
            is_group=False,
            is_self=False,
            id="p1",
            ts=1000.0,
        )
    )
    assert len(got) == 1
    msg = got[0]
    assert msg.msg_type == "pat"
    assert msg.content == "张三 拍了拍你"
    assert msg.raw["pat"]["to_self"] is True


def test_wcferry_group_pat_of_someone_else():
    driver, got = _wcferry()
    driver._dispatch(
        _WcfMsg(
            type=10000,
            content='wxid_a:\n"张三" 拍了拍"李四"',
            sender="wxid_a",
            roomid="room@chatroom",
            is_group=True,
            is_self=False,
            id="p2",
            ts=1001.0,
        )
    )
    assert len(got) == 1
    assert got[0].msg_type == "pat"
    assert got[0].content == "张三 拍了拍李四"
    assert got[0].raw["pat"]["to_self"] is False


def test_wcferry_still_drops_other_system_messages():
    driver, got = _wcferry()
    driver._dispatch(
        _WcfMsg(
            type=10000,
            content="张三 领取了你的红包",
            sender="wxid_a",
            is_group=False,
            is_self=False,
            id="p3",
            ts=1002.0,
        )
    )
    assert got == []


def test_webhook_inbound_pat_is_normalized():
    driver = WebhookDriver()
    msg = driver.inject(
        {
            "chat_id": "wxid_a",
            "chat_name": "张三",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": '"张三" 拍了拍你',
            "msg_type": "system",
        }
    )
    assert msg.msg_type == "pat"
    assert msg.content == "张三 拍了拍你"
    assert msg.raw["pat"]["to_self"] is True


def test_webhook_xml_pat_uses_given_sender_name_and_strips_prefix():
    driver = WebhookDriver()
    msg = driver.inject(
        {
            "chat_id": "room@chatroom",
            "chat_name": "吃瓜群",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "is_group": True,
            "content": 'wxid_a:\n<sysmsg type="pat"><pat>'
            "<fromusername>wxid_a</fromusername>"
            "<pattedusername>wxid_me</pattedusername>"
            "<template><![CDATA[拍了拍我]]></template>"
            "</pat></sysmsg>",
            "msg_type": "system",
        }
    )
    assert msg.msg_type == "pat"
    assert msg.content == "张三 拍了拍你"
    assert msg.sender_name == "张三"
    assert msg.raw["pat"]["from_name"] == "张三"
    assert msg.raw["pat"]["to_self"] is True


def test_webhook_strips_double_sender_prefix():
    driver = WebhookDriver()
    msg = driver.inject(
        {
            "chat_id": "wxid_a",
            "chat_name": "张三",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": "张三:\n张三:\n张三 拍了拍你",
            "msg_type": "system",
        }
    )
    assert msg.content == "张三 拍了拍你"


def test_webhook_xml_pat_without_sender_name_keeps_wxid_actor():
    driver = WebhookDriver()
    msg = driver.inject(
        {
            "chat_id": "room@chatroom",
            "chat_name": "吃瓜群",
            "sender_id": "wxid_a",
            "is_group": True,
            "content": '<sysmsg type="pat"><pat>'
            "<fromusername>wxid_a</fromusername>"
            "<pattedusername>wxid_me</pattedusername>"
            "<template><![CDATA[拍了拍我]]></template>"
            "</pat></sysmsg>",
            "msg_type": "system",
        }
    )
    assert msg.content == "wxid_a 拍了拍你"
    assert msg.sender_name == "wxid_a"


def _wx4_driver(self_id: str = "wxid_friend_03") -> Weixin4Driver:
    driver = Weixin4Driver(None)
    driver._self_wxid = self_id
    driver._chats = {"10000000002@chatroom": "吃瓜群"}
    driver.resolve_name = lambda wxid: {
        "tester_a": "张三",
        "wxid_lee": "李四",
        "wxid_friend_03": "肥鱼",
    }.get(wxid, wxid)
    return driver


def test_weixin4_history_parses_pat_and_resolves_names():
    driver = _wx4_driver()
    msg = driver._row_to_message(
        "10000000002@chatroom",
        {
            "shard": "message_0.db",
            "local_id": "88",
            "type": "系统消息",
            "sender_id": 2,  # 1.1.x 的「自己」约定不能压过 pat XML 里的人名
            "sender_username": "",
            "create_time": 1792984023.0,
            "content": PAT_XML,
        },
    )
    assert msg is not None
    assert msg.msg_type == "pat"
    assert msg.content == "张三 拍了拍你"
    assert msg.sender_id == "tester_a"
    assert msg.sender_name == "张三"
    assert msg.raw["pat"]["to_self"] is True


def test_weixin4_history_skips_own_pat():
    driver = _wx4_driver("wxid_self")
    msg = driver._row_to_message(
        "10000000002@chatroom",
        {
            "shard": "message_0.db",
            "local_id": "89",
            "type": "系统消息",
            "sender_id": 7,
            "sender_username": "wxid_self",
            "create_time": 1792984024.0,
            "content": PAT_XML_SELF,
        },
    )
    assert msg is None


def test_weixin4_realtime_pat_delivery():
    got: list[Message] = []
    driver = _wx4_driver()
    driver.on_message = got.append
    driver._on_db_message(
        {
            "username": "10000000002@chatroom",
            "shard": "message_0.db",
            "local_id": "90",
            "type": "系统消息",
            "sender_id": 2,
            "sender_username": "",
            "create_time": 1792984025.0,
            "content": PAT_XML,
        }
    )
    assert len(got) == 1
    assert got[0].msg_type == "pat"
    assert got[0].is_self is False
    assert got[0].content == "张三 拍了拍你"


def test_weixin4_plain_text_with_pat_tag_stays_text():
    driver = _wx4_driver()
    msg = driver._row_to_message(
        "10000000002@chatroom",
        {
            "shard": "message_0.db",
            "local_id": "91",
            "type": "文本",
            "sender_id": 5,
            "sender_username": "wxid_a",
            "create_time": 1792984026.0,
            "content": "我用 <pat 标签写了个demo",
        },
    )
    assert msg is not None
    assert msg.msg_type == "text"


def test_pat_is_not_treated_as_revocable_original_message(temp_db):
    uid = db.upsert_chat("acc", "wxid_a", "张三", False, "", 1000.0)
    db.add_message(
        {
            "id": "text-1",
            "account_id": "acc",
            "chat_uid": uid,
            "sender_name": "张三",
            "content": "刚才那条",
            "msg_type": "text",
            "is_group": False,
            "is_self": False,
            "ts": 2000.0,
        }
    )
    db.add_message(
        {
            "id": "pat-1",
            "account_id": "acc",
            "chat_uid": uid,
            "sender_name": "张三",
            "content": "张三 拍了拍你",
            "msg_type": "pat",
            "is_group": False,
            "is_self": False,
            "ts": 2050.0,
        }
    )
    revoked = db.mark_message_revoked(uid, "张三", notice_ts=2100.0)
    assert revoked and revoked["id"] == "text-1"


def test_legacy_system_pat_is_upgraded_in_place_on_backfill(temp_db):
    uid = db.upsert_chat("acc", "wxid_a", "张三", False, "", 1000.0)
    db.add_message(
        {
            "id": "legacy-1",
            "account_id": "acc",
            "chat_uid": uid,
            "chat_id": "wxid_a",
            "sender_name": "张三",
            "content": "[系统消息]",
            "msg_type": "system",
            "is_group": False,
            "is_self": False,
            "ts": 2000.0,
        }
    )
    added = db.add_history_messages(
        "acc",
        [
            {
                "id": "wxid_a#message_0.db#99",
                "account_id": "acc",
                "chat_uid": uid,
                "chat_id": "wxid_a",
                "chat_name": "张三",
                "sender_id": "wxid_a",
                "sender_name": "张三",
                "content": "张三 拍了拍你",
                "msg_type": "pat",
                "is_group": False,
                "is_self": False,
                "ts": 2000.5,
                "meta": {"pat": {"from_id": "wxid_a", "to_self": True, "text": "张三 拍了拍你"}},
            }
        ],
    )
    assert added == 1
    rows = db.list_messages(uid, limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == "legacy-1"
    assert rows[0]["msg_type"] == "pat"
    assert rows[0]["content"] == "张三 拍了拍你"
    assert rows[0]["meta"]["pat"]["to_self"] is True


def test_legacy_ambiguous_system_rows_are_not_rewritten(temp_db):
    uid = db.upsert_chat("acc", "wxid_a", "张三", False, "", 1000.0)
    for mid, ts in (("sys-1", 2000.0), ("sys-2", 2000.9)):
        db.add_message(
            {
                "id": mid,
                "account_id": "acc",
                "chat_uid": uid,
                "chat_id": "wxid_a",
                "sender_name": "",
                "content": "[系统消息]",
                "msg_type": "system",
                "is_group": False,
                "is_self": False,
                "ts": ts,
            }
        )
    db.add_history_messages(
        "acc",
        [
            {
                "id": "wxid_a#message_0.db#100",
                "account_id": "acc",
                "chat_uid": uid,
                "chat_id": "wxid_a",
                "chat_name": "张三",
                "sender_id": "wxid_a",
                "sender_name": "张三",
                "content": "张三 拍了拍你",
                "msg_type": "pat",
                "is_group": False,
                "is_self": False,
                "ts": 2000.5,
                "meta": {"pat": {"from_id": "wxid_a", "to_self": True, "text": "张三 拍了拍你"}},
            }
        ],
    )
    rows = {r["id"]: r for r in db.list_messages(uid, limit=10)}
    assert rows["sys-1"]["msg_type"] == "system"
    assert rows["sys-2"]["msg_type"] == "system"
    assert rows["wxid_a#message_0.db#100"]["msg_type"] == "pat"
    assert len(rows) == 3


def test_legacy_system_notice_with_other_sender_is_not_rewritten(temp_db):
    uid = db.upsert_chat("acc", "wxid_a", "张三", False, "", 1000.0)
    db.add_message(
        {
            "id": "sys-other",
            "account_id": "acc",
            "chat_uid": uid,
            "chat_id": "wxid_a",
            "sender_name": "李四",
            "content": "[系统消息]",
            "msg_type": "system",
            "is_group": False,
            "is_self": False,
            "ts": 2000.5,
        }
    )
    db.add_history_messages(
        "acc",
        [
            {
                "id": "wxid_a#message_0.db#101",
                "account_id": "acc",
                "chat_uid": uid,
                "chat_id": "wxid_a",
                "chat_name": "张三",
                "sender_id": "wxid_a",
                "sender_name": "张三",
                "content": "张三 拍了拍你",
                "msg_type": "pat",
                "is_group": False,
                "is_self": False,
                "ts": 2000.5,
                "meta": {"pat": {"from_id": "wxid_a", "to_self": True, "text": "张三 拍了拍你"}},
            }
        ],
    )
    rows = {r["id"]: r for r in db.list_messages(uid, limit=10)}
    assert rows["sys-other"]["msg_type"] == "system"
    assert rows["sys-other"]["content"] == "[系统消息]"
    assert rows["wxid_a#message_0.db#101"]["msg_type"] == "pat"
    assert len(rows) == 2


def test_pat_prompt_is_light_interaction_without_duplicate_name(monkeypatch, temp_db):
    from app import config

    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
    cfg = _cfg()
    cfg["ai"].update(
        {"memory_enabled": False, "emoji_with_reply": False, "system_prompt": "人设"}
    )
    cfg["auto"]["history_context"] = 12
    cfg["auto"]["max_reply_chars"] = 300
    session = AccountSession(
        {"id": "acc", "name": "A", "nickname": "肥鱼"}, lambda: cfg, type("L", (), {"configured": True})()
    )
    uid = db.upsert_chat("acc", "room@chatroom", "群", True, "", 1000.0)
    chat = db.get_chat(uid)
    prompt = session._build_prompt(cfg, chat, [], _pat_msg(True, True))
    system = prompt[0]["content"]
    current = prompt[-1]["content"]
    assert "轻互动" in system
    assert current == "（拍一拍）张三 拍了拍你"
    assert "张三(wxid_a)：" not in current
