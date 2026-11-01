"""非文本消息入库/展示前的 XML 摘要。"""
from __future__ import annotations

import pytest

from app import db
from app.emoji import EmojiURLError, cache_path, sanitize_emoji_url
from app.wechat.base import (
    Message,
    extract_emoji,
    parse_revoke_notice,
    parse_sysmsg_template,
    strip_sender_prefix,
    summarize_message_content,
)

IMAGE_XML = """<?xml version="1.0"?>
<msg>
    <img aeskey="0123456789abcdef0123456789abcdef" encryver="1"
    cdnthumbaeskey="0123456789abcdef0123456789abcdef"
    cdnthumburl="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567"
    cdnthumblength="7032" cdnthumbheight="118" cdnthumbwidth="210"
    length="67187" md5="0123456789abcdef0123456789abcdef">
    <secHashInfoBase64>eyJwZmciOiJzeW50aGV0aWMtdmFsdWUiLCAicGR2IjoieyJwZmciOiJzeW50aGV0aWMtdmFsdWUiLCAicGR2IjoieyJwZmciOiJzeW50aGV0aWMtdmFsdWUiLCAicGR2Ijoie</secHashInfoBase64>
    <live><duration>0</duration><size>0</size><md5 /><fileid /><hdsize>0</hdsize></live>
</msg>"""

FILE_XML = """<msg><appmsg appid="" sdkver="0"><title>报价单.pdf</title>
<des>报价单.pdf</des><type>6</type><appattach><totallen>1024</totallen>
<fileext>pdf</fileext></appattach></appmsg></msg>"""

LINK_XML = """<msg><appmsg appid="" sdkver="0"><title>项目周报</title>
<des>点击查看</des><type>5</type><url>https://example.com/weekly</url></appmsg></msg>"""

EMOJI_XML = """<?xml version="1.0"?>
<msg><emoji cdnurl="https://emoji.example.com/abc.gif" aeskey="k1"
len="1234" md5="AABBCCDDEEFF00112233445566778899" width="240" height="240"/></msg>"""

QUOTE_PLAIN_TEXT_XML = """<msg><appmsg appid="" sdkver="0"><title>收到，我看一下</title>
<type>57</type><refermsg><type>1</type><svrid>1001</svrid>
<displayname>张三</displayname><content>明天上午开会吗</content></refermsg></appmsg></msg>"""

# 引用文件消息：被引用原文以转义 XML 嵌在 refermsg.content 里
QUOTE_FILE_ESCAPED_XML = """<msg><appmsg appid="" sdkver="0"><title>好的，收到</title>
<type>57</type><refermsg><type>6</type><svrid>1002</svrid>
<displayname>李四</displayname><content>&lt;msg&gt;&lt;appmsg appid="" sdkver="0"&gt;&lt;title&gt;报价单.pdf&lt;/title&gt;&lt;type&gt;6&lt;/type&gt;&lt;appattach&gt;&lt;totallen&gt;1024&lt;/totallen&gt;&lt;fileext&gt;pdf&lt;/fileext&gt;&lt;/appattach&gt;&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>"""

# 4.x 库里 refermsg.content 也可能不转义，直接嵌原始 XML
QUOTE_RAW_IMG_XML = """<msg><appmsg appid="" sdkver="0"><title>哈哈，这个好看</title>
<type>57</type><refermsg><type>3</type><svrid>1003</svrid>
<displayname>王五</displayname><content><msg><img aeskey="k" cdnthumburl="u"/></msg></content></refermsg></appmsg></msg>"""

# 微信 4.x 链接卡片（appmsg type 5）：自带一个只装缩略图的 <appattach>，
# 旧判据「appattach 有子节点」把它误标成 [文件]，网址整个丢失，AI 无从读起
LINK_CARD_XML = """<?xml version="1.0"?>
<msg>
\t<appmsg>
\t\t<title>周五霜降，北京要“红”了！宝藏机位，别错过！</title>
\t\t<action>view</action>
\t\t<type>5</type>
\t\t<url>https://mp.weixin.qq.com/s?__biz=MzA5NDY5MzUzMQ==&amp;mid=2655667108&amp;idx=1&amp;sn=0123456789abcdef0123456789abcdef&amp;chksm=8a15acd7&amp;mpshare=1#rd</url>
\t\t<appattach>
\t\t\t<cdnthumburl>0123456789abcdef0123456789abcdef0123</cdnthumburl>
\t\t\t<cdnthumbmd5>0123456789abcdef0123456789abcdef</cdnthumbmd5>
\t\t\t<cdnthumbaeskey>0123456789abcdef0123456789abcdef</cdnthumbaeskey>
\t\t</appattach>
\t\t<sourcedisplayname>首都之窗</sourcedisplayname>
\t</appmsg>
\t<fromusername>wxid_self_main</fromusername>
</msg>"""

# 真文件卡片（appmsg type 6）：模板里同样有一串空标签，且 <url /> 是自闭合的
FILE_CARD_XML = """<?xml version="1.0"?>
<msg><appmsg appid="wxTESTAPPID000000" sdkver="0">
\t<title>VMware-tools-windows-13.0.5.iso</title><des /><type>6</type>
\t<url /><lowurl /><dataurl /><thumburl />
\t<appattach><totallen>112386048</totallen>
\t<attachid>@cdn_00000000000000000000000000000000_1</attachid>
\t<fileext>iso</fileext><cdnattachurl>7f0c00080220dc37</cdnattachurl>
\t</appattach></appmsg></msg>"""

# 小程序卡片：url 是 weixin:// 协议，fetch_page 打不开，不该塞进摘要
MINIAPP_CARD_XML = """<msg><appmsg><title>瑞幸咖啡点单</title><type>33</type>
<url>weixin://dl/business/?t=AbCdEf</url><username>gh_luckin</username></appmsg></msg>"""

# 4.x 库里被引用消息的内层副本带着 "wxid_xxx: " 前缀 + XML 声明，且微信会把
# 超长原文截断到 120 字；旧探测只看 startswith("<")，把整段原始 XML 当文本入库
QUOTE_LINK_WITH_SENDER_PREFIX_XML = """<msg><appmsg><title>细思极恐啊</title><type>57</type>
<refermsg><type>5</type><svrid>9001</svrid><displayname>测试甲</displayname>
<content>wxid_self_main: &lt;?xml version="1.0"?&gt; &lt;msg&gt; &lt;appmsg&gt; &lt;title&gt;DeepSeek-V4.1-Flash 搭配 Hermes 使用 REA 逆向智能音箱&lt;/title&gt; &lt;action&gt;view&lt;/action&gt; &lt;type&gt;5&lt;/type&gt;</content>
</refermsg><appattach /></appmsg></msg>"""

# 微信引用消息模板自带空的 <appattach>（totallen 0），旧逻辑因此把所有引用都判成 [文件]
QUOTE_EMPTY_ATTACH_XML = """<msg><appmsg appid="" sdkver="0"><title>嗯嗯好的</title>
<des></des><type>57</type><action>view</action><appattach><totallen>0</totallen>
<notused3>0</notused3></appattach><refermsg><type>1</type><svrid>1005</svrid>
<displayname>张三</displayname><content>今晚一起吃饭吗</content></refermsg>
<extinfo></extinfo></appmsg></msg>"""


def test_extract_emoji_parses_attributes():
    info = extract_emoji(EMOJI_XML)
    assert info["url"] == "https://emoji.example.com/abc.gif"
    assert info["md5"] == "AABBCCDDEEFF00112233445566778899"
    assert info["width"] == 240 and info["height"] == 240 and info["len"] == 1234


def test_extract_emoji_returns_none_without_url_or_xml():
    assert extract_emoji("<msg><emoji md5='x'/></msg>") is None
    assert extract_emoji("普通文本 [微笑]") is None


def test_emoji_message_keeps_url_in_meta_and_summary_text():
    msg = Message(
        chat_id="wxid_x",
        chat_name="对方",
        content=EMOJI_XML,
        msg_type="emoji",
        raw={"type": 47},
    )
    payload = msg.to_dict()
    assert payload["content"] == "[表情]"
    assert payload["meta"]["emoji"]["url"] == "https://emoji.example.com/abc.gif"
    # 其他消息类型不受影响
    msg2 = Message(chat_id="wxid_x", chat_name="对方", content=IMAGE_XML, msg_type="image", raw={"shard": "m.db"})
    assert msg2.to_dict()["meta"] == {"shard": "m.db"}


def test_image_xml_becomes_picture_summary():
    assert summarize_message_content(IMAGE_XML, "image") == "[图片]"


def test_sender_prefix_before_xml_is_dropped():
    stored = "wxid_friend_03:\n" + IMAGE_XML
    assert summarize_message_content(stored, "image") == "[图片]"


def test_text_content_is_untouched():
    assert summarize_message_content("在吗？", "text") == "在吗？"


def test_sender_prefix_is_stripped_from_text():
    assert strip_sender_prefix("wxid_friend_03:\n挺厉害的", "wxid_friend_03") == "挺厉害的"
    assert strip_sender_prefix("Note:\nhi", "wxid_friend_03") == "Note:\nhi"


def test_file_and_link_cards_keep_title():
    assert summarize_message_content(FILE_XML, "link") == "[文件] 报价单.pdf"
    assert (
        summarize_message_content(LINK_XML, "link")
        == "[链接] 项目周报｜https://example.com/weekly"
    )


def test_real_link_card_keeps_url_and_is_not_read_as_file():
    assert summarize_message_content(LINK_CARD_XML, "link") == (
        "[链接] 周五霜降，北京要“红”了！宝藏机位，别错过！｜"
        "https://mp.weixin.qq.com/s?__biz=MzA5NDY5MzUzMQ==&mid=2655667108&idx=1"
        "&sn=0123456789abcdef0123456789abcdef&chksm=8a15acd7&mpshare=1#rd"
    )


def test_real_file_card_is_still_a_file():
    # <url /> 自闭合空标签不能被当成网址，缩略图 appattach 也不能被当成附件
    assert (
        summarize_message_content(FILE_CARD_XML, "link")
        == "[文件] VMware-tools-windows-13.0.5.iso"
    )


def test_miniapp_card_url_is_not_fetchable_so_not_shown():
    assert summarize_message_content(MINIAPP_CARD_XML, "link") == "[卡片] 瑞幸咖啡点单"


def test_quote_of_link_card_does_not_leak_raw_xml():
    assert (
        summarize_message_content(QUOTE_LINK_WITH_SENDER_PREFIX_XML, "link")
        == "细思极恐啊\n[引用 测试甲：[链接] DeepSeek-V4.1-Flash 搭配 Hermes 使用 REA 逆向智能音箱]"
    )


def test_quote_text_message_keeps_reply_and_reference():
    assert (
        summarize_message_content(QUOTE_PLAIN_TEXT_XML, "link")
        == "收到，我看一下\n[引用 张三：明天上午开会吗]"
    )


def test_quote_of_file_is_not_misread_as_file():
    assert (
        summarize_message_content(QUOTE_FILE_ESCAPED_XML, "link")
        == "好的，收到\n[引用 李四：[文件] 报价单.pdf]"
    )


def test_quote_of_image_is_not_misread_as_image():
    assert (
        summarize_message_content(QUOTE_RAW_IMG_XML, "link")
        == "哈哈，这个好看\n[引用 王五：[图片]]"
    )


def test_quote_with_empty_appattach_is_not_misread_as_file():
    assert (
        summarize_message_content(QUOTE_EMPTY_ATTACH_XML, "link")
        == "嗯嗯好的\n[引用 张三：今晚一起吃饭吗]"
    )


def test_unknown_xml_never_leaks_raw_markup():
    summary = summarize_message_content("<msg><foo>bar</foo></msg>", "link")
    assert summary == "[非文本消息]"


def test_message_to_dict_sanitizes_non_text_content():
    msg = Message(
        chat_id="wxid_friend_03",
        chat_name="文件传输助手",
        content=IMAGE_XML,
        msg_type="image",
    )
    payload = msg.to_dict()
    assert payload["content"] == "[图片]"
    assert payload["msg_type"] == "image"


def test_message_meta_keeps_media_shard():
    msg = Message(
        chat_id="wxid_friend_03",
        chat_name="文件传输助手",
        content=IMAGE_XML,
        msg_type="image",
        raw={"shard": "message_0.db"},
    )
    assert msg.to_dict()["meta"] == {"shard": "message_0.db"}


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


def test_persisted_raw_xml_is_summarized_on_read(temp_db):
    uid = db.upsert_chat("acc_test", "wxid_friend_03", "测试会话", False, IMAGE_XML, 1000.0)
    db.add_message(
        {
            "id": "m1",
            "account_id": "acc_test",
            "chat_uid": uid,
            "chat_id": "wxid_friend_03",
            "chat_name": "测试会话",
            "sender_name": "对方",
            "content": IMAGE_XML,
            "msg_type": "image",
            "is_group": False,
            "is_self": False,
            "ts": 1000.0,
        }
    )

    assert db.list_messages(uid, limit=10)[-1]["content"] == "[图片]"
    assert db.get_chat(uid)["last_message"] == "[图片]"
    assert db.search_messages("acc_test", "<img", limit=10)[0]["content"] == "[图片]"


def test_wx4_media_loader_reuses_db_stub_package():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = (
        "from app.wechat import diagnose\n"
        "if diagnose.wx4_db_module() is None:\n"
        "    raise SystemExit(0)\n"
        "assert diagnose.wx4_media_module() is not None\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)


def test_emoji_crud_and_dedup(temp_db):
    row = db.add_emoji("acc_e", "https://emoji.example.com/abc.gif", "aabb", 240, 240)
    assert row["url"].endswith("abc.gif")
    dup = db.add_emoji("acc_e", "https://emoji.example.com/abc.gif", "aabb")
    assert dup["id"] == row["id"]
    db.add_emoji("acc_other", "https://emoji.example.com/abc.gif", "aabb")
    assert len(db.list_emojis("acc_e")) == 1
    assert db.remove_emoji("acc_e", row["id"])
    assert db.list_emojis("acc_e") == []
    assert not db.remove_emoji("acc_e", row["id"])


def test_emoji_url_guard_blocks_private_targets():
    assert sanitize_emoji_url("https://emoji.example.com/x.gif").endswith("x.gif")
    for bad in (
        "http://127.0.0.1/x.gif",
        "http://192.168.1.5/x.gif",
        "http://localhost/a",
        "file:///C:/x.gif",
        "ftp://emoji.example.com/x.gif",
        "",
    ):
        with pytest.raises(EmojiURLError):
            sanitize_emoji_url(bad)


def test_emoji_cache_path_distinguishes_sources():
    a = cache_path("https://a/b.gif", "")
    b = cache_path("https://a/c.gif", "")
    c = cache_path("https://a/other.gif", "AABBCCDDEEFF00112233445566778899")
    assert a.name != b.name and a.name != c.name and b.name != c.name
    assert a.suffix == ".gif"


# 微信 4.x 引用消息样本：内层 content 是 HTML 转义的 XML，外层 appmsg 带空 <appattach />；
# 旧摘要逻辑会把它误判成「[文件] @Deep肥鱼 这是什么」，引用图片时回复文本整个丢掉
QUOTE_IMAGE_XML = """<?xml version="1.0"?>
<msg>
\t<appmsg>
\t\t<title>@Deep肥鱼 这是什么</title>
\t\t<action>view</action>
\t\t<type>57</type>
\t\t<refermsg>
\t\t\t<type>3</type>
\t\t\t<svrid>5908968774205173660</svrid>
\t\t\t<fromusr>10000000001@chatroom</fromusr>
\t\t\t<chatusr>wxid_friend_03</chatusr>
\t\t\t<displayname>仰天长啸</displayname>
\t\t\t<content>&lt;?xml version="1.0"?&gt;
&lt;msg&gt;
\t&lt;img aeskey="0123456789abcdef0123456789abcdef" encryver="1" cdnthumburl="305f020100044b30" cdnthumblength="7921" cdnthumbheight="118" cdnthumbwidth="210" length="26175" md5="0123456789abcdef0123456789abcdef"&gt;
\t\t&lt;live&gt;&lt;duration&gt;0&lt;/duration&gt;&lt;/live&gt;
\t&lt;/img&gt;
&lt;/msg&gt;
</content>
\t\t\t<createtime>1792932428</createtime>
\t\t</refermsg>
\t\t<appattach />
\t</appmsg>
</msg>"""

QUOTE_TEXT_XML = """<msg>
\t<appmsg>
\t\t<title>抓我制作者？那我这算卧底被包围了</title>
\t\t<type>57</type>
\t\t<refermsg>
\t\t\t<type>1</type>
\t\t\t<svrid>2008485350217088970</svrid>
\t\t\t<displayname>Deep肥鱼</displayname>
\t\t\t<content>
你不同意哪部分？是不许抓还是不许我溜出去玩</content>
\t\t</refermsg>
\t\t<appattach />
\t</appmsg>
</msg>"""

QUOTE_FILE_XML = """<msg><appmsg appid="" sdkver="0"><title>帮我看看这个</title><type>57</type>
<refermsg><type>6</type><svrid>111</svrid><displayname>张三</displayname>
<content><![CDATA[<msg><appmsg appid="" sdkver="0"><title>报价单.pdf</title><type>6</type><appattach><totallen>1024</totallen><fileext>pdf</fileext></appattach></appmsg></msg>]]></content>
</refermsg><appattach /></appmsg></msg>"""

QUOTE_IMAGE_CDATA_XML = """<msg><appmsg><title>看这张</title><type>57</type>
<refermsg><type>3</type><svrid>222</svrid><displayname>李四</displayname>
<content><![CDATA[<msg><img aeskey="k" cdnthumburl="u" length="1"/></msg>]]></content>
</refermsg><appattach /></appmsg></msg>"""


def test_refer_image_quote_keeps_reply_and_label():
    assert summarize_message_content(QUOTE_IMAGE_XML, "link") == "@Deep肥鱼 这是什么\n[引用 仰天长啸：[图片]]"


def test_refer_text_quote_carries_original_text():
    assert (
        summarize_message_content(QUOTE_TEXT_XML, "link")
        == "抓我制作者？那我这算卧底被包围了\n[引用 Deep肥鱼：你不同意哪部分？是不许抓还是不许我溜出去玩]"
    )


def test_refer_file_quote_uses_filename():
    assert summarize_message_content(QUOTE_FILE_XML, "link") == "帮我看看这个\n[引用 张三：[文件] 报价单.pdf]"


def test_cdata_refer_image_not_swallowed():
    # 3.x CDATA 样本：旧逻辑整个命中 <img> 只剩「[图片]」，回复文本丢失
    assert summarize_message_content(QUOTE_IMAGE_CDATA_XML, "link") == "看这张\n[引用 李四：[图片]]"


def test_refer_image_quote_meta_for_refetch():
    msg = Message(
        chat_id="10000000001@chatroom",
        chat_name="群",
        sender_name="PiggyWu",
        content=QUOTE_IMAGE_XML,
        msg_type="link",
        raw={"shard": "message_0.db", "local_id": "798"},
    )
    refer = msg.to_dict()["meta"]["refer"]
    assert refer["type"] == 3
    assert refer["displayname"] == "仰天长啸"
    assert refer["svrid"] == "5908968774205173660"
    assert refer["summary"] == "[图片]"


def test_refer_meta_absent_for_plain_messages():
    assert "refer" not in Message(chat_id="c", chat_name="n", content=IMAGE_XML, msg_type="image").to_dict()["meta"]
    assert "refer" not in Message(chat_id="c", chat_name="n", content=FILE_XML, msg_type="link").to_dict()["meta"]


def test_history_backfill_upgrades_mangled_quote_rows(temp_db):
    uid = db.upsert_chat("acc_ref", "10000000001@chatroom", "群", True, "", 1792932428.0)
    old = {
        "id": "message__message_0.db#798",
        "account_id": "acc_ref",
        "chat_uid": uid,
        "chat_id": "10000000001@chatroom",
        "sender_name": "PiggyWu",
        "content": "[文件] @Deep肥鱼 这是什么",
        "msg_type": "link",
        "is_group": True,
        "is_self": False,
        "ts": 1792932428.0,
    }
    db.add_message(old)
    new = {
        **old,
        "content": "@Deep肥鱼 这是什么\n[引用 仰天长啸：[图片]]",
        "meta": {"refer": {"type": 3, "displayname": "仰天长啸", "svrid": "5908968774205173660", "summary": "[图片]"}},
    }
    assert db.add_history_messages("acc_ref", [new]) == 1
    row = db.get_message(uid, old["id"])
    assert row["content"] == "@Deep肥鱼 这是什么\n[引用 仰天长啸：[图片]]"
    assert row["meta"]["refer"]["svrid"] == "5908968774205173660"
    # 内容没变的普通回填仍走去重/IGNORE，不覆盖
    assert db.add_history_messages("acc_ref", [new]) == 0


# 微信 4.x 引用动画表情的真实样本：内层 content 转义 + 冒号被混淆成 *#*，
# URL 里的 & 存成 &amp;amp;（两层转义），控制台和 AI 都要能还原出原图
QUOTE_EMOJI_XML = """<?xml version="1.0"?>
<msg>
\t<appmsg>
\t\t<title>你为什么吞掉了这两条消息</title>
\t\t<type>57</type>
\t\t<refermsg>
\t\t\t<type>47</type>
\t\t\t<svrid>3132443958387435945</svrid>
\t\t\t<fromusr>10000000003@chatroom</fromusr>
\t\t\t<chatusr>wxid_self_main</chatusr>
\t\t\t<displayname>PiggyWu</displayname>
\t\t\t<content>&lt;msg&gt;&lt;emoji fromusername = "wxid_self_main" type="2" idbuffer="media*#*0_0" md5="0123456789abcdef0123456789abcdef" cdnurl = "http*#*//emoji.example.com/stodownload?m=851c&amp;amp;filekey=3044&amp;amp;ef=1" width= "508" height= "417" &gt;&lt;/emoji&gt;&lt;/msg&gt;</content>
\t\t\t<createtime>1792938758</createtime>
\t\t</refermsg>
\t\t<appattach />
\t</appmsg>
</msg>"""


def test_refer_emoji_quote_extracts_image_info():
    msg = Message(
        chat_id="10000000003@chatroom",
        chat_name="群",
        sender_name="*zip",
        content=QUOTE_EMOJI_XML,
        msg_type="link",
    )
    payload = msg.to_dict()
    refer = payload["meta"]["refer"]
    assert refer["type"] == 47
    assert refer["displayname"] == "PiggyWu"
    assert refer["summary"] == "[表情]"
    info = refer["emoji"]
    # *#* 还原成冒号，双层转义的 & 还原成单个 &
    assert info["url"] == "http://emoji.example.com/stodownload?m=851c&filekey=3044&ef=1"
    assert info["md5"] == "0123456789abcdef0123456789abcdef"
    assert info["width"] == 508 and info["height"] == 417


def test_refer_emoji_quote_summary_text():
    assert (
        summarize_message_content(QUOTE_EMOJI_XML, "link")
        == "你为什么吞掉了这两条消息\n[引用 PiggyWu：[表情]]"
    )


def test_refer_text_quote_has_no_emoji_info():
    refer = Message(chat_id="c", chat_name="n", content=QUOTE_TEXT_XML, msg_type="link").to_dict()["meta"]["refer"]
    assert not refer.get("emoji")


# 微信 4.x 邀请入群通知的真实结构：content 带「群id:\n」前缀，正文是
# <sysmsg type="sysmsgtemplate">，<template> 占位符由 link_list 成员填充
JOIN_SYSMSG_XML = """10000000003@chatroom:\n<sysmsg type="sysmsgtemplate">
\t<sysmsgtemplate>
\t\t<content_template type="tmpl_type_profile">
\t\t\t<plain><![CDATA[]]></plain>
\t\t\t<template><![CDATA[$username$邀请$names$加入了群聊]]></template>
\t\t\t<link_list>
\t\t\t\t<link name="username" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_friend_02]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[测试乙 🌵]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t</link>
\t\t\t\t<link name="names" type="link_profile">
\t\t\t\t\t<memberlist>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_friend_01]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[@小肥鱼]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t\t<member>
\t\t\t\t\t\t\t<username><![CDATA[wxid_abc123]]></username>
\t\t\t\t\t\t\t<nickname><![CDATA[六分之六]]></nickname>
\t\t\t\t\t\t</member>
\t\t\t\t\t</memberlist>
\t\t\t\t\t<separator><![CDATA[、]]></separator>
\t\t\t\t</link>
\t\t\t</link_list>
\t\t</content_template>
\t</sysmsgtemplate>
</sysmsg>"""

REVOKE_SYSMSG_XML = (
    '<sysmsg type="revokemsg"><revokemsg><newmsgid>123</newmsgid>'
    '<replacemsg><![CDATA[张三 撤回了一条消息]]></replacemsg></revokemsg></sysmsg>'
)


def test_group_join_sysmsg_renders_template():
    assert (
        summarize_message_content(JOIN_SYSMSG_XML, "system")
        == "测试乙 🌵邀请@小肥鱼、六分之六加入了群聊"
    )
    assert (
        parse_sysmsg_template(JOIN_SYSMSG_XML)
        == "测试乙 🌵邀请@小肥鱼、六分之六加入了群聊"
    )


def test_sysmsgtemplate_without_matching_link_keeps_placeholder():
    xml = '<sysmsg type="sysmsgtemplate"><sysmsgtemplate><template><![CDATA[$user$加入了群聊]]></template></sysmsgtemplate></sysmsg>'
    assert parse_sysmsg_template(xml) == "$user$加入了群聊"


def test_unknown_sysmsg_is_labelled_not_dumped():
    assert (
        summarize_message_content('<sysmsg type="foo"><fromusername>wxid_x</fromusername></sysmsg>', "system")
        == "[系统消息]"
    )


def test_revoke_sysmsg_summary_is_readable_and_still_parseable():
    assert summarize_message_content(REVOKE_SYSMSG_XML, "system") == "张三 撤回了一条消息"
    assert summarize_message_content('<sysmsg type="revokemsg"><revokemsg><replacemsg>你撤回了一条消息</replacemsg></revokemsg></sysmsg>', "system") == "你撤回了一条消息"
    # 回填路径要在摘要文本上仍能识别出撤回者
    assert parse_revoke_notice(summarize_message_content(REVOKE_SYSMSG_XML, "system")) == "张三"
