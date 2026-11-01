"""同名好友/群友用 username（wxid）识别：群聊前缀、get_history、朋友圈定位。

昵称会重名，识别一律以 wxid 为准，昵称只作展示；朋友圈按昵称找不到唯一
发布者时拒绝操作，防止点赞/评论到错误的人。
"""
from __future__ import annotations

import asyncio

import pytest

from app import config, db
from app.ai import tools as tools_mod
from app.ai.tools import build_tools
from app.wechat.base import Message, sender_label, unique_sender_id
from app.wechat.manager import AccountSession
from app.wechat.weixin4_driver import Weixin4Driver


# ---------- base：唯一标识判定与展示名 ----------


def test_unique_sender_id_guards():
    assert unique_sender_id("wxid_abc123", "张三") == "wxid_abc123"
    assert unique_sender_id("17", "张三") == ""  # weixin4 旧数据的数值行 id
    assert unique_sender_id("张三", "张三") == ""  # wcferry 从正文解析出的昵称
    assert unique_sender_id("", "张三") == ""


def test_sender_label_appends_wxid_only_when_unique():
    assert sender_label("张三", "wxid_a1") == "张三(wxid_a1)"
    assert sender_label("张三", "17") == "张三"
    assert sender_label("张三", "张三") == "张三"


# ---------- weixin4：sender_id 统一存 wxid ----------


def test_wx4_row_sender_id_uses_username():
    driver = Weixin4Driver(None)
    driver._self_wxid = "wxid_me"
    driver._chats = {"room1@chatroom": "群"}

    class StubDB:
        def nickname_map(self):
            return {"wxid_a": "张三", "wxid_b": "张三"}

    driver.db = StubDB()
    msg = driver._row_to_message(
        "room1@chatroom",
        {
            "local_id": 7,
            "type": "文本",
            "sender_id": 9,
            "sender_username": "wxid_a",
            "create_time": 1000.0,
            "content": "hello",
            "shard": "message__message_0.db",
        },
    )
    assert msg.sender_name == "张三"
    assert msg.sender_id == "wxid_a"


# ---------- 自动回复提示词：群聊前缀带 wxid ----------


def _session(monkeypatch):
    from app.wechat import manager as manager_mod

    monkeypatch.setattr(manager_mod.config, "persona_of", lambda acc: ("你是测试人设", "鱼"))
    return AccountSession({"id": "acc_a", "name": "A"}, lambda: {"ai": {}, "auto": {}}, None)


def test_group_prompt_labels_senders_with_wxid(monkeypatch):
    session = _session(monkeypatch)
    history = [
        {"msg_type": "text", "content": "早", "is_group": True, "is_self": False,
         "sender_name": "张三", "sender_id": "wxid_a"},
        {"msg_type": "text", "content": "早呀", "is_group": True, "is_self": False,
         "sender_name": "张三", "sender_id": "17"},
    ]
    msg = Message(
        chat_id="room1@chatroom", chat_name="群", content="在吗",
        is_group=True, sender_name="张三", sender_id="wxid_b",
    )
    out = session._build_prompt(
        {"ai": {"memory_enabled": False}, "auto": {}}, {"uid": "u1", "name": "群"}, history, msg
    )
    joined = "\n".join(m["content"] for m in out)
    assert "张三(wxid_a)：早" in joined
    # 数值行 id 区分不了人，不标注
    assert "张三(17)" not in joined
    assert "张三(wxid_b)：在吗" in joined
    assert "wxid 是区分同名群友的唯一标识" in out[0]["content"]


def test_private_prompt_keeps_plain_prefix(monkeypatch):
    session = _session(monkeypatch)
    msg = Message(chat_id="wxid_a", chat_name="张三", content="你好", sender_name="张三")
    out = session._build_prompt(
        {"ai": {"memory_enabled": False}, "auto": {}}, {"uid": "u1", "name": "张三"}, [], msg
    )
    joined = "\n".join(m["content"] for m in out)
    assert "张三(wxid" not in joined
    assert "wxid 是区分同名群友的唯一标识" not in joined


# ---------- 工具：get_history 群消息带 wxid ----------


def test_get_history_labels_group_senders_with_wxid(monkeypatch):
    session = AccountSession({"id": "acc_x", "name": "A", "nickname": "B"}, lambda: {"ai": {}}, None)

    async def fake_resolve(name):
        return {"uid": "uid_here", "chat_id": "room1@chatroom", "name": "群"}

    monkeypatch.setattr(session, "resolve_chat", fake_resolve)
    monkeypatch.setattr(
        tools_mod.db,
        "list_messages",
        lambda uid, limit=20: [
            {"ts": 0, "is_self": False, "is_group": True, "msg_type": "text",
             "sender_name": "张三", "sender_id": "wxid_a", "chat_name": "群", "content": "hello"},
        ],
    )
    tool = {
        t.name: t
        for t in build_tools(session, allow_send=False, chat_uid="uid_here")
    }["get_chat_history"]
    res = asyncio.run(tool.handler({"chat": "群"}))
    assert res["messages"][0]["from"] == "张三(wxid_a)"


# ---------- 朋友圈：from_id 识别 + 同名昵称拒绝 ----------


class MomentSession:
    account_id = "acc"
    llm = type("L", (), {"configured": False})()
    moments = object()

    async def resolve_chat(self, name: str):
        return {"uid": "u1", "name": name or "会话"}


class LikeService:
    def __init__(self):
        self.liked = None
        self.commented = None

    def like(self, row):
        self.liked = row
        return {"ok": True}

    def comment(self, row, content, reply_to=""):
        self.commented = (row, content, reply_to)
        return {"ok": True}


def _moment_rows():
    return [
        {"feed_id": "f1", "username": "wxid_a", "nickname": "张三", "text": "今天真累",
         "is_self": False, "create_time": 0, "images": [], "videos": [], "likes": [], "comments": []},
        {"feed_id": "f2", "username": "wxid_b", "nickname": "张三", "text": "假期开始",
         "is_self": False, "create_time": 0, "images": [], "videos": [], "likes": [], "comments": []},
        {"feed_id": "f3", "username": "wxid_c", "nickname": "李四", "text": "加班中",
         "is_self": False, "create_time": 0, "images": [], "videos": [], "likes": [], "comments": []},
    ]


@pytest.fixture()
def moments_env(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
    rows = _moment_rows()
    monkeypatch.setattr(
        db, "list_moments", lambda account_id, keyword="", limit=10: [dict(r) for r in rows]
    )
    return rows


def _moment_tool(name: str):
    session = MomentSession()
    return next(t for t in build_tools(session, allow_send=True) if t.name == name), session


def test_browse_moments_includes_from_id(moments_env):
    tool, _ = _moment_tool("browse_wechat_moments")
    res = asyncio.run(tool.handler({}))
    assert [r["from"] for r in res] == ["张三", "张三", "李四"]
    assert [r["from_id"] for r in res] == ["wxid_a", "wxid_b", "wxid_c"]


def test_like_moment_by_username_locks_right_feed(moments_env):
    tool, session = _moment_tool("like_wechat_moment")
    svc = LikeService()
    session.moments = svc
    res = asyncio.run(tool.handler({"publisher": "wxid_b"}))
    assert res.get("ok") is True
    assert svc.liked["feed_id"] == "f2"


def test_like_moment_refuses_ambiguous_nickname(moments_env):
    tool, session = _moment_tool("like_wechat_moment")
    svc = LikeService()
    session.moments = svc
    res = asyncio.run(tool.handler({"publisher": "张三"}))
    assert "error" in res and "from_id" in res["error"]
    assert svc.liked is None


def test_like_moment_unique_nickname_still_resolves(moments_env):
    tool, session = _moment_tool("like_wechat_moment")
    svc = LikeService()
    session.moments = svc
    res = asyncio.run(tool.handler({"publisher": "李四"}))
    assert res.get("ok") is True
    assert svc.liked["feed_id"] == "f3"


def test_comment_moment_by_username(moments_env):
    tool, session = _moment_tool("comment_wechat_moment")
    svc = LikeService()
    session.moments = svc
    res = asyncio.run(tool.handler({"publisher": "wxid_a", "content": "辛苦了"}))
    assert res.get("ok") is True
    assert svc.commented[0]["feed_id"] == "f1"
    assert svc.commented[1] == "辛苦了"
