"""引用消息与拍一拍卡片的回复触发。

两类消息在微信 4.x 本地库里都是 appmsg（数值 type=49），驱动一律归成 link：
- 引用消息（appmsg type=57，带 <refermsg>）正文是「回复文本 + 被引用摘要」的可读文本，
  拦掉就等于「对方在群里问话，AI 装死」；
- 拍一拍卡片（appmsg type=62，带 <patinfo>）是拍机器人本人，
  认不出来就吃不到 pat_reply 开关，被 link 分支顺手拦掉。
两条 XML 结构与 10000000001@chatroom 的真实报文一致，标识符全部为合成值（local_id 1258 / 1259）。
"""
from __future__ import annotations

from app import rules
from app.wechat.base import Message, parse_pat_notice
from app.wechat.weixin4_driver import Weixin4Driver

SELF_WXID = "wxid_self_robot"
PEER_WXID = "wxid_self_main"
ROOM = "10000000001@chatroom"

# 引用消息：回复文本「为什么会触发这个」+ 被引用的报错原文
QUOTE_XML = """wxid_self_main:
<?xml version="1.0"?>
<msg>
\t<appmsg>
\t\t<title>为什么会触发这个</title>
\t\t<action>view</action>
\t\t<type>57</type>
\t\t<refermsg>
\t\t\t<type>1</type>
\t\t\t<svrid>2167279380202813886</svrid>
\t\t\t<fromusr>10000000001@chatroom</fromusr>
\t\t\t<chatusr>wxid_self_robot</chatusr>
\t\t\t<displayname>Deep肥鱼</displayname>
\t\t\t<content>
The request was rejected because it was considered high risk</content>
\t\t\t<createtime>1792995739</createtime>
\t\t</refermsg>
\t\t<appattach />
\t</appmsg>
\t<fromusername>wxid_self_main</fromusername>
</msg>
"""

# 普通链接卡片：正文没有 refermsg，仍然不该触发回复
LINK_CARD_XML = """<?xml version="1.0"?>
<msg>
\t<appmsg>
\t\t<title>限时秒杀，点击领取优惠券</title>
\t\t<type>5</type>
\t\t<url>http://ads.example.com/a</url>
\t</appmsg>
\t<fromusername>wxid_self_main</fromusername>
</msg>
"""

# 拍一拍卡片：patinfo 带双方 wxid 与拍一拍后缀
PAT_CARD_XML = (
    '<msg><appmsg appid="" sdkver="0"><title>"测试甲" 拍了拍 "Deep肥鱼" 断成了三节</title>'
    '<type>62</type><patinfo>'
    f"<fromusername>{PEER_WXID}</fromusername>"
    f"<chatusername>{ROOM}</chatusername>"
    f"<pattedusername>{SELF_WXID}</pattedusername>"
    "<patsuffix>断成了三节</patsuffix>"
    "<template>"
    f'"${{{PEER_WXID}}}" 拍了拍 "${{{SELF_WXID}}}" 断成了三节"'
    "</template></patinfo></appmsg></msg>"
)


def _cfg(**auto) -> dict:
    base = {
        "enabled": True,
        "group_need_at": False,
        "pat_reply": True,
        "emoji_reply": True,
        "only_whitelist": False,
        "whitelist": [],
        "blacklist": [],
        "min_interval_sec": 0,
        "daily_limit": 0,
        "working_hours": {"enabled": False},
    }
    base.update(auto)
    return {"auto": base, "ai": {"vision_enabled": False}}


def _chat() -> dict:
    return {
        "uid": "acc:room",
        "chat_id": ROOM,
        "name": "吃瓜群",
        "ai_enabled": True,
        "pause_until": 0,
    }


def _link_msg(content: str) -> Message:
    return Message(
        chat_id=ROOM,
        chat_name="吃瓜群",
        sender_id=PEER_WXID,
        sender_name="张三",
        content=content,
        is_group=True,
        msg_type="link",
    )


def _wx4_driver() -> Weixin4Driver:
    driver = Weixin4Driver(None)
    driver._self_wxid = SELF_WXID
    driver._chats = {ROOM: "吃瓜群"}
    driver.resolve_name = lambda wxid: {PEER_WXID: "张三", SELF_WXID: "Deep肥鱼"}.get(wxid, wxid)
    return driver


def test_quote_message_triggers_reply():
    allowed, reason = rules.check(
        _cfg(), {"id": "acc", "nickname": "Deep肥鱼"}, _chat(), _link_msg(QUOTE_XML)
    )
    assert allowed, reason


def test_plain_link_card_still_blocked():
    allowed, reason = rules.check(
        _cfg(), {"id": "acc", "nickname": "Deep肥鱼"}, _chat(), _link_msg(LINK_CARD_XML)
    )
    assert not allowed and reason == "非文本消息（link）"


def test_patinfo_card_parsed_as_pat():
    pat = parse_pat_notice(PAT_CARD_XML)
    assert pat is not None
    assert pat["from_id"] == PEER_WXID
    assert pat["to_id"] == SELF_WXID
    assert pat["suffix"] == "断成了三节"


def test_weixin4_normalizes_patinfo_card_to_pat():
    msg = _wx4_driver()._row_to_message(
        ROOM,
        {
            "shard": "message_0.db",
            "local_id": "1258",
            "type": 49,
            "sender_id": 11,
            "sender_username": PEER_WXID,
            "create_time": 1792995797.0,
            "content": PAT_CARD_XML,
        },
    )
    assert msg is not None
    assert msg.msg_type == "pat"
    assert msg.content == "张三 拍了拍你的断成了三节"
    assert msg.raw["pat"]["to_self"] is True


def test_patinfo_card_triggers_reply():
    msg = _wx4_driver()._row_to_message(
        ROOM,
        {
            "shard": "message_0.db",
            "local_id": "1258",
            "type": 49,
            "sender_id": 11,
            "sender_username": PEER_WXID,
            "create_time": 1792995797.0,
            "content": PAT_CARD_XML,
        },
    )
    allowed, reason = rules.check(
        _cfg(), {"id": "acc", "nickname": "Deep肥鱼"}, _chat(), msg
    )
    assert allowed, reason


def test_weixin4_skips_own_patinfo_card():
    """自己拍的卡片不能回流成「有人拍了拍我」：靠 patinfo 里的 fromusername 判。"""
    msg = _wx4_driver()._row_to_message(
        ROOM,
        {
            "shard": "message_0.db",
            "local_id": "1261",
            "type": 49,
            "sender_id": 11,
            "sender_username": SELF_WXID,
            "create_time": 1792995900.0,
            "content": (
                '<msg><appmsg><type>62</type><patinfo>'
                f"<fromusername>{SELF_WXID}</fromusername>"
                f"<chatusername>{ROOM}</chatusername>"
                f"<pattedusername>{PEER_WXID}</pattedusername>"
                "<patsuffix>断成了三节</patsuffix>"
                "</patinfo></appmsg></msg>"
            ),
        },
    )
    assert msg is None
