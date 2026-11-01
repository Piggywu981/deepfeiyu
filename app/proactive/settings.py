"""账号级主动搭话设置：默认值、深拷贝合并、范围夹取与白名单规范化。"""
from __future__ import annotations

import copy
import math
from typing import Any

from .. import config

MAX_WHITELIST = 50
MAX_TRIGGER_CHARS = 200

_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off", ""}

ACCOUNT_DEFAULTS: dict[str, Any] = {
    "whitelist": [],           # [{"chat_uid","name","is_group"}]
    "interval_min": 30,        # 同一会话两次掷骰最小间隔
    "cooldown_min": 20,        # 上次主动开口后的冷却
    "quiet_start": 1,          # 安静时段含起不含止，支持跨午夜；起==止关闭
    "quiet_end": 8,
    "daily_limit": 6,          # 账号每日自动发送上限；0=不限
    "per_chat_daily": 2,       # 单会话每日自动发送上限；0=不限
    "hit_peak": 20,            # 峰时命中线（本地周一至周五 09–12、14–18）
    "hit_offpeak": 60,
    "recent_min": 20,          # 距上次会话消息 < 该值 → recent_factor
    "recent_factor": 0.15,
    "cold_min": 360,           # 距上次会话消息 > 该值 → cold_factor
    "cold_factor": 1.5,
    "miss_bonus": 10,          # 每次未命中给下一次加多少命中线
    "bonus_cap": 40,
    "trigger_text": "好久没人跟我说话了，我该主动去打个招呼",
    "max_chars": 200,
}


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(n):  # NaN/±inf（如手写成 1e999）一律回退默认值
        return default
    return max(lo, min(hi, n))


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = float(value)
        if not math.isfinite(n):
            return default
        n = int(round(n))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, n))


def _bool(value: Any, default: bool) -> bool:
    """宽松布尔解析：JSON 里手写 "false"/"0" 不能被 bool() 当成 True。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
    return default


def root() -> dict[str, Any]:
    cfg = config.load().get("proactive")
    return copy.deepcopy(cfg) if isinstance(cfg, dict) else {}


def enabled() -> bool:
    return _bool(root().get("enabled"), False)


def valid_entry(entry: Any) -> dict[str, Any] | None:
    """白名单条目规范化；聊天 uid 是唯一身份，缺失或非字符串的条目直接丢弃。"""
    if not isinstance(entry, dict):
        return None
    uid = entry.get("chat_uid")
    if not isinstance(uid, str) or not uid.strip():
        return None
    name = entry.get("name")
    return {
        "chat_uid": uid.strip(),
        "name": name.strip() if isinstance(name, str) else "",
        # 与全局开关同一套宽松解析：手写 "false" 不能把私聊标成群聊。
        "is_group": _bool(entry.get("is_group"), False),
    }


def _normalize_whitelist(value: Any) -> list[dict[str, Any]]:
    """去重（同一 chat_uid 保留第一条）并截到上限；每次返回全新列表。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(value, list):
        return out
    for item in value:
        entry = valid_entry(item)
        if entry is None or entry["chat_uid"] in seen:
            continue
        seen.add(entry["chat_uid"])
        out.append(entry)
        if len(out) >= MAX_WHITELIST:
            break
    return out


def _trigger_text(value: Any) -> str:
    text = value.strip()[:MAX_TRIGGER_CHARS] if isinstance(value, str) else ""
    return text or ACCOUNT_DEFAULTS["trigger_text"]


def settings(account_id: str) -> dict[str, Any]:
    # config.json 是手改也可能被旧版本写坏，结构不对时按默认值走，不让调度层崩。
    cfg = config.load().get("proactive")
    cfg = cfg if isinstance(cfg, dict) else {}
    accounts = cfg.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    raw = accounts.get(account_id)
    raw = raw if isinstance(raw, dict) else {}
    merged = copy.deepcopy(ACCOUNT_DEFAULTS)
    for key in ACCOUNT_DEFAULTS:
        if key in raw:
            merged[key] = raw[key]
    merged["whitelist"] = _normalize_whitelist(merged["whitelist"])
    merged["interval_min"] = _int(merged["interval_min"], 30, 1, 1440)
    merged["cooldown_min"] = _int(merged["cooldown_min"], 20, 0, 1440)
    merged["quiet_start"] = _int(merged["quiet_start"], 1, 0, 23)
    merged["quiet_end"] = _int(merged["quiet_end"], 8, 0, 23)
    merged["daily_limit"] = _int(merged["daily_limit"], 6, 0, 100)
    merged["per_chat_daily"] = _int(merged["per_chat_daily"], 2, 0, 100)
    merged["hit_peak"] = _num(merged["hit_peak"], 20, 0, 100)
    merged["hit_offpeak"] = _num(merged["hit_offpeak"], 60, 0, 100)
    merged["recent_min"] = _int(merged["recent_min"], 20, 0, 1440)
    merged["recent_factor"] = _num(merged["recent_factor"], 0.15, 0, 10)
    merged["cold_min"] = _int(merged["cold_min"], 360, 0, 10080)
    merged["cold_factor"] = _num(merged["cold_factor"], 1.5, 0, 10)
    merged["miss_bonus"] = _int(merged["miss_bonus"], 10, 0, 100)
    merged["bonus_cap"] = _int(merged["bonus_cap"], 40, 0, 100)
    merged["trigger_text"] = _trigger_text(merged["trigger_text"])
    merged["max_chars"] = _int(merged["max_chars"], 200, 20, 1000)
    return merged


def whitelist(account_id: str) -> list[dict[str, Any]]:
    return _normalize_whitelist(settings(account_id)["whitelist"])
