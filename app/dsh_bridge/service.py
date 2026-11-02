"""DSH 融合桥接：绑定账号、共享心情读写、宠物事件。

状态源永远在控制台（data/agent.db / data/config.json）；这里只做参数校验与转发，
不保存任何业务状态，避免两套数据漂移。
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..mood import service as mood_service
from ..mood import settings as mood_settings
from ..mood import store as mood_store
from ..pet.supervisor import supervisor as pet_supervisor

DEFAULTS: dict[str, Any] = {
    "account_id": "",
    "judge_enabled": True,
    "pet_events_enabled": True,
    "cross_reason_enabled": True,
}
_BOOL_KEYS = ("judge_enabled", "pet_events_enabled", "cross_reason_enabled")


def section() -> dict[str, Any]:
    raw = config.load().get("dsh")
    merged = dict(DEFAULTS)
    if isinstance(raw, dict):
        merged.update({key: raw[key] for key in DEFAULTS if key in raw})
    return merged


def accounts_payload() -> list[dict[str, Any]]:
    active = config.active_id()
    return [
        {"id": account["id"], "name": account.get("name") or account["id"],
         "active": account["id"] == active}
        for account in config.accounts()
    ]


def bound_account() -> tuple[str, bool]:
    """返回 (绑定账号, 是否回落到当前激活账号)。绑定失效时不让接口 500。"""
    wanted = str(section().get("account_id") or "").strip()
    if wanted and config.account(wanted) is not None:
        return wanted, False
    return config.active_id(), bool(wanted)


def timeline_payload(limit: int = 20) -> list[dict[str, Any]]:
    account_id, _ = bound_account()
    slot_id = mood_settings.resolve_slot(account_id, "")
    rows = mood_store.list_events(account_id, slot_id, limit=max(1, min(int(limit), 100)))
    return [
        {**row, "origin": "harness" if row.get("source") == "dsh" else "wechat"}
        for row in rows
    ]


def state_payload() -> dict[str, Any]:
    account_id, fallback = bound_account()
    return {
        "ok": True,
        "accountId": account_id,
        "boundFallback": fallback,
        "accounts": accounts_payload(),
        "config": section(),
        "mood": mood_service.state_payload(account_id),
        "timeline": timeline_payload(),
        "pet": pet_supervisor.status(),
    }


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    if "account_id" in patch:
        aid = str(patch.get("account_id") or "").strip()
        if aid and config.account(aid) is None:
            raise ValueError(f"账号不存在：{aid}")
        allowed["account_id"] = aid
    for key in _BOOL_KEYS:
        if key in patch:
            if not isinstance(patch[key], bool):
                raise ValueError(f"{key} 必须是布尔值")
            allowed[key] = patch[key]
    if allowed:
        config.save({"dsh": allowed})
    return state_payload()
