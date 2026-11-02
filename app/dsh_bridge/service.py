"""DSH 融合桥接：绑定账号、共享心情读写、宠物事件。

状态源永远在控制台（data/agent.db / data/config.json）；这里只做参数校验与转发，
不保存任何业务状态，避免两套数据漂移。
"""
from __future__ import annotations

import hashlib
from typing import Any

from .. import config
from ..mood import prompt as mood_prompt
from ..mood import service as mood_service
from ..mood import settings as mood_settings
from ..mood import store as mood_store
from ..mood.constants import DIM_KEYS
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


def _normalize_delta(body: dict[str, Any]) -> dict[str, Any]:
    try:
        mood_delta = float(body.get("moodDelta"))
    except (TypeError, ValueError) as exc:
        raise ValueError("moodDelta 必须是数字") from exc
    if not -10.0 <= mood_delta <= 10.0:
        raise ValueError("moodDelta 超出 [-10, 10]")
    raw_dims = body.get("dims") if isinstance(body.get("dims"), dict) else {}
    dims: dict[str, float] = {}
    for key in DIM_KEYS:
        try:
            value = float(raw_dims.get(key) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"dims.{key} 必须是数字") from exc
        if not -10.0 <= value <= 10.0:
            raise ValueError(f"dims.{key} 超出 [-10, 10]")
        dims[key] = value
    try:
        confidence = int(body.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence 必须是整数") from exc
    if not 0 <= confidence <= 100:
        raise ValueError("confidence 超出 [0, 100]")
    reason = "".join(
        ch for ch in str(body.get("reason") or "") if ch.isprintable()
    ).strip()[:60]
    return {"moodDelta": mood_delta, "dims": dims,
            "confidence": confidence, "reason": reason}


def judge_prompt_payload() -> dict[str, Any]:
    """按绑定账号的人设渲染判断系统提示；DSH 只负责拼本轮对话并解析 JSON。"""
    account_id, _ = bound_account()
    slot_id = mood_settings.resolve_slot(account_id, "")
    self_name, owner, aliases = mood_settings.names(account_id, slot_id, chat_name="")
    system = mood_prompt.system(self_name, owner, aliases)
    schema = (
        '{"moodDelta":0,"dims":{"happy":0,"calm":0,"sweet":0,"heartache":0,'
        '"worry":0,"sad":0,"jealous":0},"reason":"","confidence":80}'
    )
    return {
        "version": hashlib.sha256(system.encode("utf-8")).hexdigest()[:12],
        "system": system,
        "schema": schema,
    }


def apply_mood_event(body: dict[str, Any]) -> dict[str, Any]:
    sec = section()
    if not sec["judge_enabled"]:
        return {"applied": False, "skipped": "judge-disabled"}
    account_id, _ = bound_account()
    if not mood_settings.enabled(account_id):
        return {"applied": False, "skipped": "mood-disabled"}
    slot_id = mood_settings.resolve_slot(account_id, "")
    delta = _normalize_delta(body)
    result = mood_service.apply_judged_delta(
        account_id, slot_id, delta, source="dsh", chat_uid="", reason_limit=60,
    )
    return {**result, "accountId": account_id,
            "mood": mood_service.state_payload(account_id)}
