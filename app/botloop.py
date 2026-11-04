"""机器人同行互聊停止机制（规格：docs/superpowers/specs/2026-11-04-bot-loop-stop-design.md）。

群里登记在册的机器人号（wxid 互认名单）发来的消息不再与真人等同：每个群聊维护
rounds（已回复同行的轮数）与 last_human_ts（最后一条真人消息时间）两个字段，
轮数超限 / 长时间无真人参与时闸门拒绝回复；真人消息重置两个字段（让位 + 重新激活）。

bot_peers 为空时所有入口整体短路，未启用用户行为与现状完全一致；
sender_id 拿不到（如 wcferry 群消息）按真人处理（fail-open，最坏维持现状）。
"""
from __future__ import annotations

import math
import time
from typing import Any

from . import db
from .wechat.base import Message

STATE_PREFIX = "botloop:"


def peers(cfg: dict[str, Any]) -> set[str]:
    """归一化后的同行 wxid 名单；结构坏掉按空名单处理（对齐 rules._policy_list）。"""
    raw = (cfg.get("auto") or {}).get("bot_peers")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if isinstance(x, str) and x.strip()}


def enabled(cfg: dict[str, Any]) -> bool:
    return bool(peers(cfg))


def is_peer(cfg: dict[str, Any], msg: Message) -> bool:
    """群消息且发送者 wxid 在名单里；私聊、空 sender 一律不是同行（fail-open）。"""
    if not msg.is_group:
        return False
    sid = str(msg.sender_id or "").strip()
    return bool(sid) and sid in peers(cfg)


def _key(chat_uid: str) -> str:
    return f"{STATE_PREFIX}{chat_uid}"


def _load(chat_uid: str, now: float) -> dict[str, Any]:
    """读每会话状态；缺失或结构坏掉按首见初始化（last_human_ts = now）并落盘。

    读路径也落盘：规格 §5.2 要求「会话首见」即建立状态，纯机器人群从此时起计闲置；
    字段级损坏同样修好后写回，避免坏时间戳每次读都被当成「刚发生」。
    """
    data = db.kv_get(_key(chat_uid))
    if not isinstance(data, dict):
        st = {"rounds": 0, "last_human_ts": now}
        _save(chat_uid, st)
        return st
    bad = False
    try:
        rounds = max(0, int(data.get("rounds") or 0))
    except (TypeError, ValueError):
        rounds = 0
        bad = True
    try:
        ts = float(data.get("last_human_ts") or 0)
    except (TypeError, ValueError, OverflowError):
        ts = 0.0
        bad = True
    if not math.isfinite(ts) or ts <= 0:
        ts = now
        bad = True
    st = {"rounds": rounds, "last_human_ts": ts}
    if bad:
        _save(chat_uid, st)
    return st


def _save(chat_uid: str, st: dict[str, Any]) -> None:
    db.kv_set(_key(chat_uid), st)


def _limits(cfg: dict[str, Any]) -> tuple[int, float]:
    """(轮数上限, 无真人分钟数)；0 = 不限；坏值按 0 处理。"""
    auto = cfg.get("auto") or {}
    try:
        max_rounds = int(auto.get("bot_chat_max_rounds", 0) or 0)
    except (TypeError, ValueError):
        max_rounds = 0
    try:
        idle_min = float(auto.get("bot_chat_idle_stop_min", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        idle_min = 0.0
    return max(0, max_rounds), max(0.0, idle_min)


def observe(cfg: dict[str, Any], chat_uid: str, msg: Message, now: float | None = None) -> None:
    """真人消息到达：重置轮数、刷新计时（让位 + 重新激活）。

    只在名单非空且是群聊时写状态；system 消息与同行消息不构成真人。
    本人在手机上手动发的消息（is_self 且非服务回声）也走这里，同样算真人。
    """
    if not enabled(cfg) or not msg.is_group:
        return
    if msg.msg_type == "system" or is_peer(cfg, msg):
        return
    now = time.time() if now is None else now
    _save(chat_uid, {"rounds": 0, "last_human_ts": now})


def gate(cfg: dict[str, Any], chat_uid: str, now: float | None = None) -> tuple[bool, str]:
    """同行消息的发前闸门：轮数超限 / 长时间无真人参与。

    只对已判定为同行的群消息调用（rules.check 内）；除首见初始化落盘外不写状态。
    """
    if not enabled(cfg):
        return True, ""
    now = time.time() if now is None else now
    st = _load(chat_uid, now)
    max_rounds, idle_min = _limits(cfg)
    if max_rounds > 0 and st["rounds"] >= max_rounds:
        return False, f"机器人互聊已达轮数上限（{max_rounds}）"
    if idle_min > 0 and now - st["last_human_ts"] >= idle_min * 60:
        return False, f"机器人互聊长时间无真人参与（{idle_min:.0f} 分钟）"
    return True, ""


def count_burst(
    cfg: dict[str, Any], chat_uid: str, burst: list[Message], now: float | None = None
) -> bool:
    """一轮回复真正开始生成时计数：burst 里含同行消息才 +1。

    返回 True 表示正好到达轮数上限——这一轮是收尾轮（提示词要加收尾指令）。
    必须在拿到会话锁之后调用：提前计数会让 _fire_reply 的窗口重检把第 N 条自己拦下，
    拿锁后调用同时保证每轮回复只计一次。
    """
    if not enabled(cfg):
        return False
    if not any(is_peer(cfg, m) for m in burst):
        return False
    now = time.time() if now is None else now
    st = _load(chat_uid, now)
    st["rounds"] += 1
    _save(chat_uid, st)
    max_rounds, _ = _limits(cfg)
    return max_rounds > 0 and st["rounds"] == max_rounds
