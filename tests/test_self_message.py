"""自发消息链路：weixin4 识别自己 → manager 回声对账防双写 → 人工接管暂停。

重启补漏（listener 水位）会把停机期间的消息补推一遍，其中人工在手机/桌面端
直接发的消息只能从这里进来；服务自己发出的消息则靠发送前留底对账，防止
send_text 已入库的记录被轮询回声再写一遍。
"""
import asyncio
import time

import pytest

from app import db
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


def _session(**auto_cfg) -> AccountSession:
    cfg = {
        "send": {"enabled": False},
        "ai": {"emoji_auto_collect": False},
        "auto": dict(auto_cfg),
    }
    return AccountSession({"id": "acc_x", "name": "A", "nickname": "B"}, lambda: cfg, None)


class FakeDriver:
    _self_wxid = "wxid_test_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send_text(self, chat_id: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((chat_id, text))

    def send_emoji(self, chat_id: str, path: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((chat_id, path))

    def send_file(self, chat_id: str, path: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((chat_id, path))


# ---------- weixin4 驱动：识别自己 ----------

def _driver(wxid: str = "wxid_self") -> Weixin4Driver:
    d = Weixin4Driver(None)
    d._self_wxid = wxid
    d._chats = {"r1@chatroom": "群", "wxid_friend": "好友"}
    return d


def test_weixin4_delivers_self_message_with_prefix_stripped():
    d = _driver()
    got: list[Message] = []
    d.on_message = got.append
    d._on_db_message({
        "username": "r1@chatroom", "local_id": 11, "type": 1,
        "sender_id": 4, "sender_username": "wxid_self",
        "content": "wxid_self:\n大家好", "create_time": 1700000000,
        "shard": "message__message_0.db",
    })
    assert len(got) == 1
    m = got[0]
    assert m.is_self is True
    assert m.sender_name == "我"
    assert m.sender_id == "wxid_self"
    assert m.content == "大家好"  # 群里自发消息正文同样带 wxid 前缀，要剥掉


def test_weixin4_self_message_falls_back_to_sender_id_2():
    """1.1.x 没有 sender_username，按 sender_id==2 识别自己。"""
    d = _driver()
    got: list[Message] = []
    d.on_message = got.append
    d._on_db_message({
        "username": "wxid_friend", "local_id": 12, "type": 1,
        "sender_id": 2, "sender_username": "",
        "content": "哈哈", "create_time": 1700000000,
    })
    assert got[0].is_self is True
    assert got[0].sender_id == "wxid_self"
    assert got[0].content == "哈哈"


def test_weixin4_rowid2_member_not_self():
    """1.2.7 分片 Name2Id 里 rowid 2 可能是普通成员，不能沿袭 1.1.x 的「2=自己」。"""
    d = _driver()
    got: list[Message] = []
    d.on_message = got.append
    d._on_db_message({
        "username": "r1@chatroom", "local_id": 13, "type": 1,
        "sender_id": 2, "sender_username": "wxid_friend",
        "content": "wxid_friend:\n我是群友", "create_time": 1700000000,
        "shard": "message__message_1.db",
    })
    assert len(got) == 1
    assert got[0].is_self is False
    assert got[0].sender_name == "wxid_friend"
    assert got[0].content == "我是群友"


# ---------- manager：回声对账与人工接管 ----------

def test_send_then_poll_echo_not_duplicated(temp_db):
    session = _session()
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    asyncio.run(session.send_text(uid, "在忙", source="agent"))
    assert len(db.list_messages(uid, limit=10)) == 1

    echo = Message(chat_id="wxid_friend", chat_name="好友", content="在忙",
                   is_self=True, msg_id="9001", ts=time.time())
    asyncio.run(session.handle_message(echo))
    assert len(db.list_messages(uid, limit=10)) == 1  # 轮询回声没有再入库
    assert session._recent_outbound == {}  # 留底用完即销毁


def test_failed_send_does_not_leave_echo_key(temp_db):
    session = _session()
    session.driver = FakeDriver(fail=True)
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    with pytest.raises(RuntimeError):
        asyncio.run(session.send_text(uid, "在忙", source="agent"))
    assert (uid, "在忙") not in session._recent_outbound


def test_human_self_message_recorded_and_pauses_ai(temp_db):
    session = _session(takeover_pause_min=10)
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    before = time.time()
    msg = Message(chat_id="wxid_friend", chat_name="好友", content="我来回复",
                  is_self=True, msg_id="9002", ts=time.time())
    asyncio.run(session.handle_message(msg))
    rows = db.list_messages(uid, limit=10)
    assert len(rows) == 1
    assert rows[0]["is_self"] is True
    assert rows[0]["content"] == "我来回复"
    assert db.get_chat(uid)["pause_until"] > before  # 人工接管：AI 暂停


def test_human_self_message_no_pause_when_disabled(temp_db):
    session = _session(takeover_pause_min=0)
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    msg = Message(chat_id="wxid_friend", chat_name="好友", content="我来回复",
                  is_self=True, msg_id="9003", ts=time.time())
    asyncio.run(session.handle_message(msg))
    assert len(db.list_messages(uid, limit=10)) == 1
    assert db.get_chat(uid)["paused"] is False


def test_emoji_echo_matched_by_md5(temp_db, tmp_path, monkeypatch):
    session = _session()
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    md5 = "a" * 32
    monkeypatch.setattr("app.emoji.fetch_emoji", lambda url, md5="": str(tmp_path / "e.gif"))
    asyncio.run(session.send_emoji(uid, {"url": "https://cdn/x", "md5": md5}, source="agent"))
    assert len(db.list_messages(uid, limit=10)) == 1

    echo = Message(chat_id="wxid_friend", chat_name="好友", is_self=True, msg_type="emoji",
                   content=f'<msg><emoji cdnurl="https://cdn/x" md5="{md5}"/></msg>',
                   msg_id="9004", ts=time.time())
    asyncio.run(session.handle_message(echo))
    assert len(db.list_messages(uid, limit=10)) == 1  # 同一个表情的轮询回声不入库

    human = Message(chat_id="wxid_friend", chat_name="好友", is_self=True, msg_type="emoji",
                    content=f'<msg><emoji cdnurl="https://cdn/y" md5="{"b" * 32}"/></msg>',
                    msg_id="9005", ts=time.time())
    asyncio.run(session.handle_message(human))
    rows = db.list_messages(uid, limit=10)
    assert len(rows) == 2  # 人工发的另一个表情照常入库
    assert rows[0]["msg_type"] == "emoji"


def test_emoji_paste_echoes_as_image_without_takeover(temp_db, tmp_path, monkeypatch):
    """剪贴板粘贴发的表情，微信侧回显成 image 且不带 md5：按出站配额对账，不误判人工接管。"""
    session = _session(takeover_pause_min=10)
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    monkeypatch.setattr("app.emoji.fetch_emoji", lambda url, md5="": str(tmp_path / "e.gif"))
    asyncio.run(session.send_emoji(uid, {"url": "https://cdn/x", "md5": "c" * 32}, source="agent"))
    assert len(db.list_messages(uid, limit=10)) == 1

    echo = Message(chat_id="wxid_friend", chat_name="好友", is_self=True, msg_type="image",
                   content="[图片]", msg_id="9006", ts=time.time())
    asyncio.run(session.handle_message(echo))
    assert len(db.list_messages(uid, limit=10)) == 1  # image 回声不重复入库
    assert db.get_chat(uid)["paused"] is False  # 也不触发人工接管

    # 配额用完了：AI 没再发表情时，人工发的图就是真的，照常入库并触发接管
    human_img = Message(chat_id="wxid_friend", chat_name="好友", is_self=True, msg_type="image",
                        content="[图片]", msg_id="9007", ts=time.time())
    asyncio.run(session.handle_message(human_img))
    assert len(db.list_messages(uid, limit=10)) == 2
    assert db.get_chat(uid)["paused"] is True


def test_file_send_echo_recorded_without_takeover(temp_db, monkeypatch):
    """工具发文件没有服务侧留底：回声仍入库充当聊天记录，但不该按人工接管暂停。"""
    session = _session(takeover_pause_min=10)
    session.driver = FakeDriver()
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    monkeypatch.setattr("app.wechat.manager.AccountSession._throttled",
                        lambda self, chat_id, fn, burst=False: fn())
    asyncio.run(session.send_file(uid, "D:/tmp/a.png"))

    echo = Message(chat_id="wxid_friend", chat_name="好友", is_self=True, msg_type="file",
                   content="[文件]", msg_id="9008", ts=time.time())
    asyncio.run(session.handle_message(echo))
    rows = db.list_messages(uid, limit=10)
    assert len(rows) == 1 and rows[0]["msg_type"] == "file"  # 入库当聊天记录
    assert db.get_chat(uid)["paused"] is False  # 但不算人工接管


def test_media_echo_quota_expires(temp_db):
    session = _session()
    from collections import deque
    uid = "chat_u1"
    session._media_echo_quota[uid] = deque([time.time() - 120])  # 早过 90 秒窗口
    assert session._consume_media_echo(uid) is False
    assert uid not in session._media_echo_quota  # 空配额随手清掉


def test_backfilled_self_message_before_start_skips_takeover(temp_db):
    """重启水位补推：早于服务启动的自发消息入库，但不再把 AI 按成人工接管。"""
    session = _session(takeover_pause_min=10)
    uid = db.upsert_chat("acc_x", "wxid_friend", "好友", False)
    session.started_at = time.time()
    old = Message(chat_id="wxid_friend", chat_name="好友", content="重启前的旧消息",
                  is_self=True, msg_id="9009", ts=time.time() - 3600)
    asyncio.run(session.handle_message(old))
    assert len(db.list_messages(uid, limit=10)) == 1
    assert db.get_chat(uid)["paused"] is False

    fresh = Message(chat_id="wxid_friend", chat_name="好友", content="刚打的",
                    is_self=True, msg_id="9010", ts=time.time())
    asyncio.run(session.handle_message(fresh))
    assert db.get_chat(uid)["paused"] is True  # 启动后真人发的仍正常触发接管
