"""心情 → 桌宠表现。动作仍由状态决定，这里只改待机节奏与瞬时脉冲。

上游只有三档 activityLevel 与七个状态，没有情绪专属帧，所以心情的表达面是
「文案措辞 + 待机频率 + pulse」，不新造动画语义。心情关闭时三条通道各自退化：
文案只剩状态词、节奏固定 normal、pulse 停用——桌宠不依赖心情功能可用。
"""
from __future__ import annotations

import time
from typing import Any

from ..mood import constants as mood_constants
from ..mood import model as mood_model
from ..mood import settings as mood_settings
from ..mood import store as mood_store

LIVELY_FLOOR = 75.0
NORMAL_FLOOR = 45.0
PULSE_COOLDOWN_S = 60
PULSE_RISE = 20.0
PULSE_FLOOR = 60.0
PULSE_TTL_BASE = 1500
PULSE_TTL_MAX = 2500
_PULSE_STATE_BY_DIM = {
    "happy": "SUCCESS", "sweet": "SUCCESS", "calm": "IDLE",
    "worry": "THINKING", "heartache": "ERROR", "sad": "IDLE", "jealous": "THINKING",
}


def snapshot(account_id: str, chat_uid: str = "") -> dict[str, Any] | None:
    """轻量读心情：基线 + 七维 + 可信度。

    走 store 直读而不是 service.state_payload——后者返回的 dims 是 [{key,label,value}]
    列表，而且每次调用都顺带算 30 条 timeline，气泡每轮都要读一次不该那么贵。
    """
    if not mood_settings.enabled(account_id):
        return None
    st = mood_settings.settings(account_id)
    slot_id = mood_settings.resolve_slot(account_id, chat_uid)
    state = mood_store.get_slot(account_id, slot_id) or mood_store.blank_state(st["base"])
    mood_model.decay(state, st["base"], time.time(), st["decay_half_hours"])
    return {
        "base": float(state["mood"]),
        "dims": {k: float(state["dims"].get(k, 0) or 0) for k in mood_constants.DIM_KEYS},
        "confidence": float(state.get("last_confidence") or 0),
    }


def activity_level(base: float) -> str:
    if base >= LIVELY_FLOOR:
        return "lively"
    if base >= NORMAL_FLOOR:
        return "normal"
    return "quiet"


def resolve_level(cfg: dict[str, Any], payload: dict[str, Any] | None) -> str:
    manual = cfg.get("activityLevel") or "auto"
    if manual != "auto":
        return manual
    if not payload:
        return "normal"
    return activity_level(float(payload.get("base") or 0.0))


def pulse_for_change(prev_dims: dict[str, Any], dims: dict[str, Any], *, now_s: int,
                     last_pulse_s: int | None) -> dict[str, Any] | None:
    """只在「显著上升且够高」时跳一下；60 秒最多一次，否则桌面会变成抽风。"""
    if last_pulse_s is not None and now_s - last_pulse_s < PULSE_COOLDOWN_S:
        return None
    best: tuple[str, float] | None = None
    for name, raw in (dims or {}).items():
        value = float(raw or 0)
        rise = value - float((prev_dims or {}).get(name, 0) or 0)
        if value >= PULSE_FLOOR and rise >= PULSE_RISE and name in _PULSE_STATE_BY_DIM:
            if best is None or rise > best[1]:
                best = (name, rise)
    if best is None:
        return None
    state = _PULSE_STATE_BY_DIM[best[0]]
    if state == "IDLE":
        return None            # 平静/低落不值得一次弹跳，只会添噪音
    ttl = min(PULSE_TTL_MAX, PULSE_TTL_BASE + int((best[1] - PULSE_RISE) * 25))
    return {"kind": "pulse", "state": state, "ttlMs": ttl,
            "resumeState": "IDLE", "dim": best[0]}
