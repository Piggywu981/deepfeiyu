"""账号级心情设置：默认值、深合并、槽位解析、称呼回退。"""
from __future__ import annotations

from typing import Any

from .. import config
from . import constants, store

ACCOUNT_DEFAULTS: dict[str, Any] = {
    "mode": "account",          # account | chat
    "base": 70.0,
    "floor": 0.0,
    "decay_half_hours": 12.0,
    "inject_mode": "event",     # off | label | event
    "inject_confidence": 60,
    "sig_mood": 3,
    "sig_dim": 5,
    "inject_per_day": 6,
    "auto_judge": True,
    "judge_model": "",
    "self_name": "",
    "owner_name": "",
    "self_aliases": "",
}


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def settings(account_id: str) -> dict[str, Any]:
    raw = ((config.load().get("mood") or {}).get("accounts") or {}).get(account_id) or {}
    merged = {k: raw.get(k, v) for k, v in ACCOUNT_DEFAULTS.items()}
    merged["mode"] = "chat" if str(merged["mode"]).lower() == "chat" else "account"
    merged["base"] = _num(merged["base"], 70.0, 0.0, 100.0)
    merged["floor"] = _num(merged["floor"], 0.0, 0.0, merged["base"])
    merged["decay_half_hours"] = _num(merged["decay_half_hours"], 12.0, 1.0, 168.0)
    merged["inject_mode"] = (
        merged["inject_mode"] if merged["inject_mode"] in constants.INJECT_MODES else "event"
    )
    merged["inject_confidence"] = _int(merged["inject_confidence"], 60, 0, 100)
    merged["sig_mood"] = _int(merged["sig_mood"], 3, 1, 10)
    merged["sig_dim"] = _int(merged["sig_dim"], 5, 1, 10)
    merged["inject_per_day"] = _int(merged["inject_per_day"], 6, 0, 100)
    merged["auto_judge"] = bool(merged["auto_judge"])
    merged["judge_model"] = str(merged["judge_model"] or "").strip()
    merged["self_name"] = str(merged["self_name"] or "").strip()
    merged["owner_name"] = str(merged["owner_name"] or "").strip()
    merged["self_aliases"] = str(merged["self_aliases"] or "").strip()
    return merged


def enabled(account_id: str | None = None) -> bool:
    return bool((config.load().get("mood") or {}).get("enabled"))


def save_account(account_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: patch[k] for k in patch if k in ACCOUNT_DEFAULTS}
    cfg = config.load()
    accounts = dict(((cfg.get("mood") or {}).get("accounts") or {}))
    accounts[account_id] = {**accounts.get(account_id, {}), **clean}
    config.save({"mood": {"accounts": {account_id: accounts[account_id]}}})
    return settings(account_id)


def resolve_slot(account_id: str, chat_uid: str = "") -> str:
    if settings(account_id)["mode"] != "chat" or not chat_uid:
        return store.DEFAULT_SLOT
    return store.slot_of_chat(account_id, chat_uid) or store.DEFAULT_SLOT


def names(account_id: str, slot_id: str, chat_name: str = "") -> tuple[str, str, str]:
    st = settings(account_id)
    slot = store.get_slot(account_id, slot_id) or {}
    account = config.account(account_id) or {}
    try:
        _, persona = config.persona_of(account)
    except Exception:
        persona = ""
    self_name = (slot.get("self_name") or st["self_name"]
                 or persona or account.get("nickname") or "伙伴").strip()
    owner = (slot.get("owner_name") or st["owner_name"]
             or (chat_name or "").strip() or "对方").strip()
    aliases = (slot.get("self_aliases") or st["self_aliases"] or "").strip()
    return self_name, owner, aliases
