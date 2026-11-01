"""主动搭话发送闸门 check_proactive：检查顺序、边界与拦截文案。

闸门不检查 auto.enabled、工作时段与群聊；群聊是否开口由白名单显式勾选控制。
"""
from __future__ import annotations

import time

import pytest

from app import rules


def _cfg(**auto_over) -> dict:
    """最小配置：只带闸门会用到的 auto 段，其余键按需覆盖。"""
    auto = {"blacklist": [], "only_whitelist": False, "whitelist": []}
    auto.update(auto_over)
    return {"auto": auto}


def _chat(**over) -> dict:
    chat = {"chat_id": "wxid_alice", "name": "Alice", "ai_enabled": True, "pause_until": 0}
    chat.update(over)
    return chat


# ------------------------------------------------------------------ 正常放行


def test_private_chat_allowed():
    assert rules.check_proactive(_cfg(), {"id": "acc_default"}, _chat()) == (True, "允许主动搭话")


def test_group_chat_allowed_without_extra_gate():
    """群聊不在策略层一刀切；is_group 不参与判定。"""
    chat = _chat(chat_id="123456@chatroom", name="产品群", is_group=True)
    assert rules.check_proactive(_cfg(), None, chat) == (True, "允许主动搭话")


def test_account_may_be_none():
    assert rules.check_proactive(_cfg(), None, _chat()) == (True, "允许主动搭话")
    assert rules.check_proactive(_cfg(blacklist=["Alice"]), None, _chat()) == (False, "命中黑名单")


# ------------------------------------------------------------------ 会话状态


def test_missing_chat():
    assert rules.check_proactive(_cfg(), None, None) == (False, "会话不存在")


def test_ai_disabled():
    assert rules.check_proactive(_cfg(), None, _chat(ai_enabled=False)) == (
        False,
        "该会话已关闭 AI",
    )


@pytest.mark.parametrize(
    "pause_until,allowed",
    [
        (999.0, True),
        (1000.0, True),  # 严格大于：恰好等于 now 放行
        (1000.5, False),
        ("1000.5", False),  # JSON/表单里可能是字符串
        ("999.5", True),
        (None, True),
        ("", True),
        (" ", True),  # 脏字符串按 0 处理，不炸也不误拦
    ],
)
def test_pause_until_boundary(pause_until, allowed):
    ok, reason = rules.check_proactive(
        _cfg(), None, _chat(pause_until=pause_until), now=1000.0
    )
    assert ok is allowed
    assert reason == ("允许主动搭话" if allowed else "人工接管中，AI 暂停")


def test_pause_until_falls_back_to_real_time():
    """不传 now 时按当前时间比较。"""
    future = time.time() + 3600
    assert rules.check_proactive(_cfg(), None, _chat(pause_until=future))[0] is False


# ------------------------------------------------------------------ 内部会话


@pytest.mark.parametrize(
    "chat_id", ["filehelper", "brandsessionholder", "brandservicesessionholder"]
)
def test_internal_chats_blocked(chat_id):
    chat = _chat(chat_id=chat_id, name="文件传输助手")
    assert rules.check_proactive(_cfg(), None, chat) == (False, "内部会话不主动搭话")


# ------------------------------------------------------------------ 黑名单


def test_blacklist_matches_chat_id():
    assert rules.check_proactive(_cfg(blacklist=["wxid_alice"]), None, _chat()) == (
        False,
        "命中黑名单",
    )


def test_blacklist_matches_name():
    assert rules.check_proactive(_cfg(blacklist=["Alice"]), None, _chat()) == (
        False,
        "命中黑名单",
    )


def test_blacklist_empty_entry_is_not_a_hit():
    cfg = _cfg(blacklist=["", None, "wxid_other"])
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


# ------------------------------------------------------------------ 白名单


def test_only_whitelist_hit_by_name_allows():
    cfg = _cfg(only_whitelist=True, whitelist=["Bob", "Alice"])
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


def test_only_whitelist_hit_by_chat_id_allows():
    cfg = _cfg(only_whitelist=True, whitelist=["wxid_alice"])
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


def test_only_whitelist_miss_blocks():
    cfg = _cfg(only_whitelist=True, whitelist=["Bob", "wxid_bob"])
    assert rules.check_proactive(cfg, None, _chat()) == (False, "不在自动回复白名单")


def test_only_whitelist_with_empty_list_blocks():
    """与 check() 同一口径：只放行开关开着、名单却空着，等于谁都不许发。"""
    assert rules.check_proactive(_cfg(only_whitelist=True, whitelist=[]), None, _chat()) == (
        False,
        "自动回复白名单为空",
    )
    # 全空串等价于没有白名单
    cfg = _cfg(only_whitelist=True, whitelist=["", None])
    assert rules.check_proactive(cfg, None, _chat()) == (False, "自动回复白名单为空")


def test_whitelist_ignored_when_only_whitelist_off():
    cfg = _cfg(only_whitelist=False, whitelist=["Bob"])
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


# ------------------------------------------------------------------ 不检查的项


def test_global_auto_switch_is_not_checked():
    cfg = _cfg(enabled=False)
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


def test_working_hours_are_not_checked(monkeypatch):
    """即使当前时间在配置的工作时段之外，主动搭话也不受其影响。"""
    cfg = _cfg(working_hours={"enabled": True, "start": "09:00", "end": "18:00"})
    monkeypatch.setattr(rules.time, "strftime", lambda fmt: "22:00")
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


def test_disabled_working_hours_do_not_block():
    cfg = _cfg(working_hours={"enabled": False, "start": "09:00", "end": "18:00"})
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")


# ------------------------------------------------------------------ 检查顺序


def test_ai_disabled_reported_before_internal_chat():
    chat = _chat(chat_id="filehelper", ai_enabled=False)
    assert rules.check_proactive(_cfg(), None, chat) == (False, "该会话已关闭 AI")


def test_internal_chat_reported_before_pause():
    chat = _chat(chat_id="brandsessionholder", pause_until="9999999999")
    assert rules.check_proactive(_cfg(), None, chat) == (False, "内部会话不主动搭话")


def test_pause_reported_before_blacklist():
    chat = _chat(pause_until="9999999999")
    assert rules.check_proactive(_cfg(blacklist=["Alice"]), None, chat) == (
        False,
        "人工接管中，AI 暂停",
    )


def test_blacklist_reported_before_whitelist_miss():
    cfg = _cfg(blacklist=["Alice"], only_whitelist=True, whitelist=["Bob"])
    assert rules.check_proactive(cfg, None, _chat()) == (False, "命中黑名单")


# ------------------------------------------------------------------ 坏配置结构


def test_null_lists_do_not_crash():
    """config.json 里写成 null 时按空名单处理，不能把整个闸门打崩。"""
    cfg = {"auto": {"blacklist": None, "only_whitelist": False, "whitelist": None}}
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")
    cfg = {"auto": {"blacklist": None, "only_whitelist": True, "whitelist": None}}
    assert rules.check_proactive(cfg, None, _chat()) == (False, "自动回复白名单为空")


def test_non_list_policy_values_are_ignored():
    """单个字符串/数字不是名单；不能按字符迭代，也不能让 x in ident 抛错。"""
    cfg = {"auto": {"blacklist": "Alice", "only_whitelist": False, "whitelist": "Alice"}}
    assert rules.check_proactive(cfg, None, _chat()) == (True, "允许主动搭话")
    # 只放行开着但名单是坏值：按空名单拦下，而不是按字符匹配
    cfg = {"auto": {"blacklist": "Alice", "only_whitelist": True, "whitelist": "Alice"}}
    assert rules.check_proactive(cfg, None, _chat()) == (False, "自动回复白名单为空")


def test_policy_list_skips_non_string_entries():
    """混进 null/数字/嵌套列表的条目一律忽略：黑名单不命中，白名单按空拦下。"""
    cfg = _cfg(blacklist=[None, 123, ["Alice"], "Bob"], only_whitelist=True, whitelist=[None, 5])
    assert rules.check_proactive(cfg, None, _chat()) == (False, "自动回复白名单为空")


def test_now_zero_is_not_treated_as_missing():
    """显式 now=0 必须按 0 比较；不能用 `now or time.time()`。"""
    chat = _chat(pause_until=0.5)
    assert rules.check_proactive(_cfg(), None, chat, now=0) == (False, "人工接管中，AI 暂停")


def test_existing_check_survives_null_lists(monkeypatch):
    """同一次修复覆盖原有 check：黑名单/白名单为 null 时按空处理。"""
    from app import db
    from app.wechat.base import Message

    msg = Message(chat_id="wxid_alice", chat_name="Alice", content="hi")
    cfg = {
        "auto": {
            "enabled": True,
            "blacklist": None,
            "only_whitelist": True,
            "whitelist": None,
            "min_interval_sec": 0,
            "daily_limit": 0,
            "working_hours": {"enabled": False},
        },
        "ai": {},
    }
    monkeypatch.setattr(db, "list_messages", lambda *a, **k: [])
    monkeypatch.setattr(db, "count_ai_replies_today", lambda *a, **k: 0)
    assert rules.check(cfg, {"id": "acc_default"}, _chat(), msg) == (False, "白名单为空")


def test_pause_until_non_finite_or_overflow_is_not_paused():
    """NaN/±inf/超大数不是合法时间戳：按 0 处理，不能永久拦住会话。"""
    for bad in (float("inf"), float("-inf"), float("nan"), "inf", 10**400, "abc", None):
        assert rules.check_proactive(_cfg(), None, _chat(pause_until=bad)) == (
            True,
            "允许主动搭话",
        )


def test_cross_visit_survives_null_lists():
    """串门不看 pause_until，但同样的 null 名单不能把它打崩。"""
    from app.wechat.base import Message

    msg = Message(chat_id="wxid_alice", chat_name="Alice", content="hi", is_self=True)
    cfg = {
        "auto": {
            "enabled": True,
            "blacklist": None,
            "only_whitelist": True,
            "whitelist": None,
        },
        "cross": {"enabled": True, "visit": True},
    }
    assert rules.check_cross_visit(cfg, None, _chat(), msg) == (True, "允许串门")
