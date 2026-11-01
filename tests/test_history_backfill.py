"""历史回填：会话登记不覆盖预览、回填幂等去重、合并聊天记录卡片放行并被 AI 知情。"""
from __future__ import annotations

import asyncio

import pytest

from app import db
from app.rules import check
from app.wechat.base import Message
from app.wechat.manager import AccountSession
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


def _msg(uid: str, mid: str, ts: float, content: str, sender: str = "对方") -> dict:
    return {
        "id": mid,
        "account_id": "acc_a",
        "chat_uid": uid,
        "chat_id": "c1",
        "chat_name": "会话一",
        "sender_id": "w1",
        "sender_name": sender,
        "content": content,
        "msg_type": "text",
        "is_group": False,
        "is_self": False,
        "source": "history",
        "ts": ts,
        "meta": {},
    }


def test_upsert_chat_meta_preserves_preview(temp_db):
    uid = db.upsert_chat("acc_a", "c1", "旧名", False, "早上好", 1000.0)
    db.upsert_chat_meta("acc_a", "c1", "新名", False)
    row = db.get_chat(uid)
    assert row["name"] == "新名"
    assert row["last_message"] == "早上好"
    assert row["last_ts"] == 1000.0


def test_upsert_chat_meta_inserts_new_chat_without_preview(temp_db):
    uid = db.upsert_chat_meta("acc_a", "c9", "新会话", True)
    row = db.get_chat(uid)
    assert row["name"] == "新会话"
    assert row["last_message"] == ""
    assert row["last_ts"] == 0


def test_add_history_messages_idempotent(temp_db):
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    batch = [_msg(uid, "s0#1", 1000.0, "你好"), _msg(uid, "s0#2", 1001.0, "在吗")]
    assert db.add_history_messages("acc_a", batch) == 2
    # 同一批重复回填（重启再来一次）一条都不加
    assert db.add_history_messages("acc_a", batch) == 0
    rows = db.list_messages(uid, limit=10)
    assert [r["content"] for r in rows] == ["你好", "在吗"]
    assert all(r["source"] == "history" for r in rows)


def test_add_history_messages_dedupes_against_live_rows(temp_db):
    # 旧版本实时入库的消息 id 是 uuid，回填时只能按 会话+时间+内容 兜底去重
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    live = _msg(uid, "uuid-old", 1000.0, "你好")
    live["source"] = "wechat"
    db.add_message(live)
    assert db.add_history_messages("acc_a", [_msg(uid, "s0#1", 1000.5, "你好")]) == 0
    assert len(db.list_messages(uid, limit=10)) == 1


def test_add_history_messages_upgrades_title_only_record_rows(temp_db):
    """旧版只存了标题的记录卡片，回填用完整转录按同 id 升级。"""
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    old = _msg(uid, "s0#7", 1000.0, "[文件] 群聊的聊天记录")
    old["msg_type"] = "link"
    db.add_message(old)
    upgraded = _msg(uid, "s0#7", 1000.0, "[聊天记录]\n测试丙：铃声第一句")
    upgraded["msg_type"] = "link"
    assert db.add_history_messages("acc_a", [upgraded]) == 1
    rows = db.list_messages(uid, limit=10)
    assert [r["content"] for r in rows] == ["[聊天记录]\n测试丙：铃声第一句"]


def test_add_history_messages_upgrades_mislabeled_link_rows(temp_db):
    """旧版把链接卡片标成「[文件] 标题」且没存网址，回填按同 id 换成带网址的 [链接]。"""
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    old = _msg(uid, "s0#8", 1000.0, "[文件] 只有奶鲸才能稳稳地接住你！")
    old["msg_type"] = "link"
    db.add_message(old)
    upgraded = _msg(
        uid,
        "s0#8",
        1000.0,
        "[链接] 只有奶鲸才能稳稳地接住你！｜https://mp.weixin.qq.com/s?__biz=MzA5&chksm=8a15",
    )
    upgraded["msg_type"] = "link"
    assert db.add_history_messages("acc_a", [upgraded]) == 1
    rows = db.list_messages(uid, limit=10)
    assert len(rows) == 1
    assert "https://mp.weixin.qq.com/s?" in rows[0]["content"]


def test_replace_in_message_content_upgrades_marker(temp_db):
    """打标完成后，记录转录里的占位标记按 md5 原位升级成描述。"""
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    md5 = "2700f1de11dfa49e2c1a2b7e72c6fa49"
    row = _msg(uid, "s0#9", 1000.0, f"[聊天记录]\n测试丙：[动画表情 md5={md5}]")
    row["msg_type"] = "link"
    db.add_message(row)
    n = db.replace_in_message_content(
        "acc_a", f"[动画表情 md5={md5}]", "[表情包：仓鼠张嘴震惊]"
    )
    assert n == 1
    assert db.list_messages(uid, limit=5)[0]["content"] == "[聊天记录]\n测试丙：[表情包：仓鼠张嘴震惊]"


def test_refresh_chat_preview_fills_but_never_regresses(temp_db):
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    db.refresh_chat_preview(uid, "下午见", 2000.0)
    assert db.get_chat(uid)["last_message"] == "下午见"
    db.refresh_chat_preview(uid, "早上好", 1000.0)
    row = db.get_chat(uid)
    assert row["last_message"] == "下午见"
    assert row["last_ts"] == 2000.0


def _rules_cfg() -> dict:
    return {"auto": {"enabled": True}, "ai": {}}


def _chat() -> dict:
    return {"uid": "u1", "chat_id": "c1", "name": "会话一", "ai_enabled": True, "pause_until": 0}


def test_rules_allows_merged_chat_record_link():
    msg = Message(chat_id="c1", chat_name="会话一", content="[文件] 群聊的聊天记录", msg_type="link")
    allowed, reason = check(_rules_cfg(), {"id": "acc_a", "nickname": "鱼"}, _chat(), msg)
    assert allowed, reason


def test_rules_blocks_other_link_messages():
    msg = Message(chat_id="c1", chat_name="会话一", content="[链接] 某公众号文章", msg_type="link")
    allowed, reason = check(_rules_cfg(), {"id": "acc_a", "nickname": "鱼"}, _chat(), msg)
    assert not allowed and "link" in reason


RECORD_XML = (
    '<msg><appmsg appid="" sdkver="0"><title>群聊的聊天记录</title><type>19</type>'
    "<appattach><totallen>1</totallen></appattach></appmsg></msg>"
)

# 结构取自真实 4.x 解密库：被转发的消息完整内嵌在 recorditem CDATA 里
RECORD_FULL_XML = """<msg>
\t<appmsg>
\t\t<title>群聊的聊天记录</title>
\t\t<type>19</type>
\t\t<recorditem><![CDATA[<recordinfo>
\t<title>群聊的聊天记录</title>
\t<datalist count="3">
\t\t<dataitem datatype="1" dataid="a1">
\t\t\t<datadesc>你可知道我给你打电话的时候</datadesc>
\t\t\t<sourcename>测试丙</sourcename>
\t\t</dataitem>
\t\t<dataitem datatype="1" dataid="a2">
\t\t\t<datadesc>铃声第一句</datadesc>
\t\t\t<sourcename>测试丙</sourcename>
\t\t</dataitem>
\t\t<dataitem datatype="37" dataid="a3">
\t\t\t<datadesc>[动画表情]</datadesc>
\t\t\t<sourcename>测试丙</sourcename>
\t\t\t<emojiitem>
\t\t\t\t<md5>2700F1DE11DFA49E2C1A2B7E72C6FA49</md5>
\t\t\t\t<cdnurlstring>http://cdn.example.com/stodownload?m=x&amp;filekey=y</cdnurlstring>
\t\t\t</emojiitem>
\t\t</dataitem>
\t</datalist>
</recordinfo>]]></recorditem>
\t</appmsg>
</msg>"""


def test_parse_chat_record_extracts_transcript():
    from app.wechat.base import parse_chat_record

    text = parse_chat_record(RECORD_FULL_XML)
    assert text is not None
    assert "测试丙：你可知道我给你打电话的时候" in text
    assert "测试丙：铃声第一句" in text


def test_parse_chat_record_marks_emoji_with_md5():
    from app.wechat.base import parse_chat_record

    text = parse_chat_record(RECORD_FULL_XML)
    assert "[动画表情 md5=2700f1de11dfa49e2c1a2b7e72c6fa49]" in text


def _record_xml(*items: tuple[str, str]) -> str:
    dataitems = "".join(
        f"<dataitem datatype=\"1\" dataid=\"d{i}\">"
        f"<datadesc>{desc}</datadesc><sourcename>{name}</sourcename></dataitem>"
        for i, (name, desc) in enumerate(items)
    )
    return (
        '<msg><appmsg><title>群聊的聊天记录</title><type>19</type>'
        f"<recorditem><![CDATA[<recordinfo><datalist count=\"{len(items)}\">"
        f"{dataitems}</datalist></recordinfo>]]></recorditem></appmsg></msg>"
    )


def test_parse_chat_record_keeps_more_than_80_chars_across_items():
    """条数上限按「条」算：整段超过 80 字不代表条数到了，不能提前截断。"""
    from app.wechat.base import parse_chat_record

    xml = _record_xml(
        ("PiggyWu", "发现刚跑完步的野生刘长河"),
        ("PiggyWu", "[动画表情 md5=" + "a" * 32 + "]"),
        ("Deep肥鱼", "这照片是隔着毛玻璃拍的吗，野生是野生，就是有点糊"),
        ("PiggyWu", "哈哈哈哈哈哈"),
    )
    text = parse_chat_record(xml)
    assert "后略" not in text
    assert text.count("\n") == 3
    assert "哈哈哈哈哈哈" in text


def test_parse_chat_record_truncates_on_char_budget():
    from app.wechat.base import parse_chat_record

    xml = _record_xml(("A", "早" * 800), ("B", "晚" * 800))
    text = parse_chat_record(xml)
    assert text.endswith("……（后略）")
    assert text.count("早" * 800) == 1
    assert "晚" not in text


def test_parse_chat_record_truncates_on_item_budget():
    from app.wechat.base import parse_chat_record

    xml = _record_xml(("A", "一"), ("A", "二"), ("A", "三"), ("A", "四"))
    text = parse_chat_record(xml, max_items=3)
    assert text.splitlines() == ["A：一", "A：二", "A：三", "……（后略）"]


def test_extract_record_emojis_returns_md5_and_unescaped_url():
    from app.wechat.base import extract_record_emojis

    infos = extract_record_emojis(RECORD_FULL_XML)
    assert len(infos) == 1
    assert infos[0]["md5"] == "2700f1de11dfa49e2c1a2b7e72c6fa49"
    assert infos[0]["url"] == "http://cdn.example.com/stodownload?m=x&filekey=y"


def test_enrich_record_emojis_uses_library(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    row = db.add_emoji("acc_a", "http://cdn.example.com/e", md5="2700f1de11dfa49e2c1a2b7e72c6fa49")
    db.update_emoji_tags("acc_a", row["id"], "仓鼠,震惊", "仓鼠张嘴震惊")
    out = session._enrich_record_emojis("[聊天记录]\n测试丙：[动画表情 md5=2700f1de11dfa49e2c1a2b7e72c6fa49]")
    assert "[表情包：仓鼠张嘴震惊]" in out


def test_enrich_record_emojis_keeps_unknown(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    marker = "[动画表情 md5=" + "0" * 32 + "]"
    out = session._enrich_record_emojis(f"[聊天记录]\n测试丙：{marker}")
    assert marker in out


def test_hydrate_record_row_skips_plain_messages(temp_db):
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    row = {"msg_type": "text", "content": "你好"}
    assert session._hydrate_record_row(row, "你好", "u1", "会话一")["content"] == "你好"
    # loop 未绑定时（测试环境）收集静默跳过，不报错
    link_row = {"msg_type": "link", "content": "[聊天记录]\n测试丙：[动画表情 md5=" + "0" * 32 + "]"}
    out = session._hydrate_record_row(link_row, RECORD_FULL_XML, "u1", "会话一")
    assert out["content"].startswith("[聊天记录]")


def test_parse_chat_record_returns_none_for_plain_card():
    from app.wechat.base import parse_chat_record

    assert parse_chat_record(RECORD_XML) is None
    assert parse_chat_record("") is None
    assert parse_chat_record("普通文本") is None


def test_to_dict_stores_record_transcript():
    msg = Message(chat_id="c1", chat_name="会话一", content=RECORD_FULL_XML, msg_type="link")
    row = msg.to_dict()
    assert row["content"].startswith("[聊天记录]")
    assert "测试丙：铃声第一句" in row["content"]


def test_to_dict_keeps_summary_without_recorditem():
    msg = Message(chat_id="c1", chat_name="会话一", content=RECORD_XML, msg_type="link")
    assert msg.to_dict()["content"] == "[文件] 群聊的聊天记录"


def test_build_prompt_feeds_record_transcript_to_model(temp_db, monkeypatch):
    from app.wechat import manager as manager_mod

    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    monkeypatch.setattr(manager_mod.config, "persona_of", lambda acc: ("你是测试人设", "鱼"))
    msg = Message(chat_id="c1", chat_name="会话一", content=RECORD_FULL_XML, msg_type="link")
    out = session._build_prompt(
        {"ai": {"memory_enabled": False}, "auto": {}}, {"uid": "u1", "name": "会话一"}, [], msg
    )
    system = out[0]["content"]
    assert "完整对话内容已附在下面的消息里" in system
    assert "读不了" not in system
    current = out[-1]["content"]
    assert "测试丙：铃声第一句" in current
    assert "<recorditem" not in current


def test_wx4_row_to_message_composite_id_and_skips_self():
    driver = Weixin4Driver(None)
    driver._self_wxid = "wxid_me"
    driver._chats = {"c1": "会话一"}
    rows = [
        {"local_id": 5, "type": "文本", "sender_id": 3, "sender_username": "wxid_a",
         "create_time": 1000.0, "content": "hello", "sort_seq": 9, "shard": "message__message_0.db"},
        {"local_id": 6, "type": "文本", "sender_id": 1, "sender_username": "wxid_me",
         "create_time": 1001.0, "content": "我自己", "sort_seq": 10, "shard": "message__message_0.db"},
    ]

    class StubDB:
        def get_messages(self, user, limit):
            return rows

        def nickname_map(self):
            return {"wxid_a": "阿A"}

    driver.db = StubDB()
    history = driver.fetch_history("c1", limit=10)
    assert len(history) == 1
    assert history[0].sender_name == "阿A"
    # msg_id 带 chat_id：4.x 每个会话一张 Msg 表，local_id 跨会话会重复
    assert history[0].msg_id == "c1#message__message_0.db#5"
    assert history[0].raw["local_id"] == "5"


def test_build_prompt_explains_unreadable_record(monkeypatch):
    from app.wechat import manager as manager_mod

    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    monkeypatch.setattr(manager_mod.config, "persona_of", lambda acc: ("你是测试人设", "鱼"))
    msg = Message(chat_id="c1", chat_name="会话一", content=RECORD_XML, msg_type="link")
    out = session._build_prompt({"ai": {"memory_enabled": False}, "auto": {}}, {"uid": "u1", "name": "会话一"}, [], msg)
    system = out[0]["content"]
    assert "[文件] 群聊的聊天记录" in system
    assert "读不了" in system
    # 当前消息给模型的是摘要，不是整段 XML
    current = out[-1]["content"]
    assert "<appmsg" not in current
    assert "[文件] 群聊的聊天记录" in current


LINK_CARD_PROMPT_XML = """<msg><appmsg><title>只有奶鲸才能稳稳地接住你！</title>
<action>view</action><type>5</type>
<url>https://mp.weixin.qq.com/s?__biz=MzA5&amp;mid=2655&amp;chksm=8a15#rd</url>
<appattach><cdnthumburl>305f02</cdnthumburl><cdnthumbmd5>b48c49</cdnthumbmd5></appattach>
</appmsg></msg>"""


def test_build_prompt_lets_model_open_link_card(monkeypatch):
    """链接卡片带网址时，提示词要告诉模型能用 fetch_page 读，不再一口咬定读不了。"""
    from app.wechat import manager as manager_mod

    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    monkeypatch.setattr(manager_mod.config, "persona_of", lambda acc: ("你是测试人设", "鱼"))
    msg = Message(chat_id="c1", chat_name="会话一", content=LINK_CARD_PROMPT_XML, msg_type="link")
    out = session._build_prompt(
        {"ai": {"memory_enabled": False}, "auto": {}}, {"uid": "u1", "name": "会话一"}, [], msg
    )
    system = out[0]["content"]
    assert "fetch_page" in system
    assert "你只能看到它的标题" not in system
    current = out[-1]["content"]
    assert current.startswith("[链接] 只有奶鲸才能稳稳地接住你！｜https://mp.weixin.qq.com/s?")
    assert "<appmsg" not in current


def test_backfill_history_survives_real_to_dict_rows(temp_db):
    """回归：to_dict() 里没有 chat_uid/account_id，回填前必须补上，
    否则整个循环 KeyError 哑火且不留痕（真实事故：回填静默未生效）。"""
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)
    uid = db.upsert_chat_meta("acc_a", "c1", "会话一", False)
    msg = Message(
        chat_id="c1", chat_name="会话一", content="历史消息", msg_type="text",
        ts=1000.0, msg_id="s0#1",
    )

    class StubDriver:
        def list_chats(self, limit=100):
            return [{"chat_id": "c1", "name": "会话一"}]

        def fetch_history(self, chat_id, limit):
            return [msg]

    stub = StubDriver()
    session.driver = stub
    session._backfill_history(stub.fetch_history, 50, 100)

    rows = db.list_messages(uid, limit=10)
    assert [r["content"] for r in rows] == ["历史消息"]
    assert db.get_chat(uid)["last_message"] == "历史消息"
    # 完成事件总是落一条，方便从事件流确认回填确实跑过
    assert any("回填历史消息" in e["title"] for e in db.list_events("acc_a", limit=10))
