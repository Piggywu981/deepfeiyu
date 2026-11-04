"""机器人同行互聊停止：识别、状态机（轮数/闲置/让位）、闸门、manager 钩子与表单静态断言。

规格：docs/superpowers/specs/2026-11-04-bot-loop-stop-design.md
所有 wxid 均为合成数据（AGENTS.md 6.2 脱敏红线）。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app import botloop, config, db, rules
from app.wechat.base import Message


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


def _cfg(**auto_over) -> dict:
    auto = {"bot_peers": ["wxid_peer_bot"], "bot_chat_max_rounds": 8,
            "bot_chat_idle_stop_min": 15}
    auto.update(auto_over)
    return {"auto": auto}


def _msg(sender: str = "wxid_peer_bot", group: bool = True, mtype: str = "text",
         **over) -> Message:
    m = Message(chat_id="123@chatroom", chat_name="产品群", sender_id=sender,
                sender_name="同行", content="在吗", is_group=group, msg_type=mtype)
    for k, v in over.items():
        setattr(m, k, v)
    return m


# ------------------------------------------------------------------ 识别

def test_is_peer_hit_and_miss():
    cfg = _cfg()
    assert botloop.is_peer(cfg, _msg()) is True
    assert botloop.is_peer(cfg, _msg(sender="wxid_human")) is False


def test_is_peer_empty_sender_is_human():
    """wcferry 等通道拿不到 wxid：按真人（fail-open，最坏维持现状）。"""
    assert botloop.is_peer(_cfg(), _msg(sender="")) is False


def test_is_peer_private_chat_never_peer():
    assert botloop.is_peer(_cfg(), _msg(group=False)) is False


def test_peers_tolerates_bad_config():
    assert botloop.peers({"auto": {"bot_peers": None}}) == set()
    assert botloop.peers({"auto": {"bot_peers": "wxid_x"}}) == set()
    assert botloop.peers({"auto": {}}) == set()
    assert botloop.peers({"auto": {"bot_peers": ["a", "", None, 1]}}) == {"a"}
    assert botloop.enabled({"auto": {}}) is False


# ------------------------------------------------------------------ 状态机

def test_observe_resets_on_human(temp_db):
    cfg = _cfg()
    uid = "acc_a:g1"
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_human"))
    st = db.kv_get(f"botloop:{uid}")
    assert st["rounds"] == 0
    assert st["last_human_ts"] > time.time() - 5
    assert botloop.count_burst(cfg, uid, [_msg()]) is False   # 重置后从第 1 轮重新计
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 1


def test_observe_resets_on_emoji_and_pat(temp_db):
    """真人发的表情/拍一拍同样算真人出现（只有 system 不算）。"""
    cfg = _cfg()
    uid = "acc_a:g1e"
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_human", mtype="emoji"))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_human", mtype="pat"))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0


def test_observe_skips_peer_system_private_and_disabled(temp_db):
    """同行/system/私聊/名单空都不构成真人事件，也不写状态。"""
    cfg = _cfg()
    uid = "acc_a:g2"
    botloop.observe(cfg, uid, _msg())                              # 同行
    assert db.kv_get(f"botloop:{uid}") is None
    botloop.observe(cfg, uid, _msg(mtype="system", sender=""))     # system
    assert db.kv_get(f"botloop:{uid}") is None
    botloop.observe(cfg, uid, _msg(group=False, sender="wxid_human"))  # 私聊
    assert db.kv_get(f"botloop:{uid}") is None
    botloop.observe({"auto": {}}, uid, _msg(sender="wxid_human"))  # 名单空短路
    assert db.kv_get(f"botloop:{uid}") is None


def test_observe_manual_self_message_counts_as_human(temp_db):
    """本人在手机上手动发的消息 = 真人出现（服务回声在 manager 上游已被吞掉）。"""
    cfg = _cfg()
    uid = "acc_a:g3"
    db.kv_set(f"botloop:{uid}", {"rounds": 3, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_self", is_self=True))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0


def test_gate_initializes_at_first_sight(temp_db):
    cfg = _cfg()
    assert botloop.gate(cfg, "acc_a:g4") == (True, "")
    st = db.kv_get("botloop:acc_a:g4")
    assert st["rounds"] == 0
    assert st["last_human_ts"] == pytest.approx(time.time(), abs=5)


def test_gate_rejects_at_round_limit(temp_db):
    cfg = _cfg(bot_chat_max_rounds=2)
    db.kv_set("botloop:acc_a:g5", {"rounds": 2, "last_human_ts": time.time()})
    ok, why = botloop.gate(cfg, "acc_a:g5")
    assert ok is False
    assert "轮数上限" in why


def test_gate_zero_means_unlimited(temp_db):
    cfg = _cfg(bot_chat_max_rounds=0, bot_chat_idle_stop_min=0)
    db.kv_set("botloop:acc_a:g6", {"rounds": 999, "last_human_ts": time.time() - 10**6})
    assert botloop.gate(cfg, "acc_a:g6") == (True, "")


def test_gate_rejects_when_idle_and_recovers_on_human(temp_db):
    cfg = _cfg(bot_chat_idle_stop_min=15)
    db.kv_set("botloop:acc_a:g7", {"rounds": 0, "last_human_ts": time.time() - 16 * 60})
    ok, why = botloop.gate(cfg, "acc_a:g7")
    assert ok is False
    assert "真人" in why
    botloop.observe(cfg, "acc_a:g7", _msg(sender="wxid_human"))   # 真人发言 → 恢复
    assert botloop.gate(cfg, "acc_a:g7") == (True, "")


def test_count_burst_counts_only_peer_and_flags_final(temp_db):
    cfg = _cfg(bot_chat_max_rounds=2)
    uid = "acc_a:g8"
    assert botloop.count_burst(cfg, uid, [_msg(sender="wxid_human")]) is False
    assert db.kv_get(f"botloop:{uid}") is None                    # 纯真人触发不计数
    assert botloop.count_burst(cfg, uid, [_msg()]) is False       # 第 1 条：未到上限
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 1
    assert botloop.count_burst(cfg, uid, [_msg()]) is True        # 第 2 条：正好到上限 → 收尾
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 2
    assert botloop.count_burst({"auto": {}}, uid, [_msg()]) is False  # 名单空短路
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 2


def test_config_defaults_include_bot_loop_keys():
    auto = config.DEFAULTS["auto"]
    assert auto["bot_peers"] == []
    assert auto["bot_chat_max_rounds"] == 8
    assert auto["bot_chat_idle_stop_min"] == 15


# ------------------------------------------------------------------ rules.check 闸门

def _rule_cfg(**auto_over) -> dict:
    auto = {"enabled": True, "group_need_at": False, "emoji_reply": True,
            "pat_reply": True, "only_whitelist": False, "whitelist": [],
            "blacklist": [], "min_interval_sec": 0, "daily_limit": 0,
            "bot_peers": ["wxid_peer_bot"], "bot_chat_max_rounds": 2,
            "bot_chat_idle_stop_min": 15}
    auto.update(auto_over)
    return {"auto": auto, "ai": {}}


def _chat(**over) -> dict:
    chat = {"uid": "acc_a:123@chatroom", "chat_id": "123@chatroom", "name": "产品群",
            "ai_enabled": True, "pause_until": 0}
    chat.update(over)
    return chat


def test_rules_gate_blocks_peer_at_round_limit(temp_db):
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom", {"rounds": 2, "last_human_ts": time.time()})
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False
    assert "轮数上限" in why


def test_rules_gate_blocks_peer_when_idle(temp_db):
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 0, "last_human_ts": time.time() - 16 * 60})
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False
    assert "真人" in why


def test_rules_peer_allowed_with_fresh_state(temp_db):
    cfg = _rule_cfg()
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert (ok, why) == (True, "允许回复")


def test_rules_human_sender_not_gated(temp_db):
    """真人消息不受互聊状态影响（即使状态里轮数已满）。"""
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 99, "last_human_ts": time.time() - 9999})
    ok, _ = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg(sender="wxid_human"))
    assert ok is True


def test_rules_without_peers_configured_behaves_as_before(temp_db):
    """未配置名单：即便残留状态也不拦（机制整体关闭 = 现状）。"""
    auto = {"enabled": True, "group_need_at": False, "only_whitelist": False,
            "whitelist": [], "blacklist": [], "min_interval_sec": 0, "daily_limit": 0}
    cfg = {"auto": auto, "ai": {}}
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 99, "last_human_ts": time.time() - 9999})
    ok, _ = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is True


def test_rules_peer_still_blocked_by_takeover(temp_db):
    """停止条件④：人工接管对同行照常。"""
    cfg = _rule_cfg()
    ok, why = rules.check(cfg, {"id": "acc_a"},
                          _chat(pause_until=time.time() + 60), _msg())
    assert ok is False and "人工接管" in why


def test_rules_peer_still_blocked_by_blacklist(temp_db):
    cfg = _rule_cfg(blacklist=["产品群"])
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False and "黑名单" in why


def test_rules_peer_still_blocked_by_group_need_at(temp_db):
    cfg = _rule_cfg(group_need_at=True)
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg(content="随便聊聊"))
    assert ok is False and "群聊未 @" in why


def test_rules_peer_still_blocked_by_daily_limit(temp_db):
    """停止条件④：每日上限对同行照常（种子一条今日 agent 回复）。"""
    cfg = _rule_cfg(daily_limit=1)
    db.add_message({
        "id": "old1", "account_id": "acc_a", "chat_uid": "acc_a:123@chatroom",
        "chat_id": "123@chatroom", "chat_name": "产品群", "content": "早前回复",
        "msg_type": "text", "is_group": True, "is_self": True,
        "source": "agent", "ts": time.time(),
    })
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False
    assert why == "已达今日回复上限"


def test_rules_peer_still_blocked_outside_working_hours(temp_db, monkeypatch):
    """停止条件④：工作时段对同行照常。"""
    monkeypatch.setattr("app.rules.time.strftime", lambda fmt: "03:00")
    cfg = _rule_cfg(working_hours={"enabled": True, "start": "09:00", "end": "18:00"})
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False and "工作时段" in why


def test_rules_peer_without_wxid_falls_open(temp_db):
    """拿不到 wxid 的同行按真人放行（fail-open，维持现状）。"""
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 99, "last_human_ts": time.time() - 9999})
    ok, _ = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg(sender=""))
    assert ok is True


def test_rules_private_peer_message_ungated(temp_db):
    """私聊不进状态机（非目标），行为与现状一致。"""
    cfg = _rule_cfg()
    chat = _chat(chat_id="wxid_peer_bot", uid="acc_a:wxid_peer_bot", name="同行")
    ok, _ = rules.check(cfg, {"id": "acc_a"}, chat, _msg(group=False))
    assert ok is True
    assert db.kv_get("botloop:acc_a:123@chatroom") is None
