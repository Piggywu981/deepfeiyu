"""子串匹配到多个会话时不能默默挑第一个。"""
import asyncio

import pytest

from app.wechat.manager import AccountSession


def _session(chats: list[dict]) -> AccountSession:
    s = AccountSession.__new__(AccountSession)
    s.driver = None
    s.visible_chats = lambda limit=500: chats   # type: ignore[method-assign]
    return s


def test_exact_name_wins_over_substring():
    s = _session([
        {"uid": "u1", "chat_id": "c1", "name": "小林同学"},
        {"uid": "u2", "chat_id": "c2", "name": "小林"},
    ])
    got = asyncio.run(s.resolve_chat("小林"))
    assert got and got["uid"] == "u2"


def test_single_substring_hit_still_resolves():
    s = _session([
        {"uid": "u1", "chat_id": "c1", "name": "小林同学"},
        {"uid": "u2", "chat_id": "c2", "name": "阿强"},
    ])
    got = asyncio.run(s.resolve_chat("小林"))
    assert got and got["uid"] == "u1"


def test_ambiguous_substring_is_refused():
    s = _session([
        {"uid": "u1", "chat_id": "c1", "name": "小林 A"},
        {"uid": "u2", "chat_id": "c2", "name": "小林 B"},
    ])
    with pytest.raises(ValueError, match="多个会话"):
        asyncio.run(s.resolve_chat("小林"))


def test_no_hit_returns_none_not_error():
    s = _session([{"uid": "u1", "chat_id": "c1", "name": "阿强"}])
    assert asyncio.run(s.resolve_chat("不存在的人")) is None
