"""心情纯逻辑：半衰、涨跌、解析、显著性、注入文案。无 IO。"""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from .constants import (
    DIM_KEYS, DIM_LABELS, DIM_PLAIN, DIM_SHORT, MOOD_STEP_DIVISOR,
)

_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_SPEAK = "说|问|讲|答|喊|回|笑|想|觉得|解释|抱怨|嘟囔|念叨"
# 与 _SPEAK 共用同一动词集合，避免两处字面量漂移；心理动词同样视为「别人在说/想」
_OTHER_SPEAK = re.compile(r"(^|[^她他])([\u4e00-\u9fa5]{2,4})(?:" + _SPEAK + ")")


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def mood_step(delta: int | float) -> int:
    return int(round(float(delta) / MOOD_STEP_DIVISOR))


def significant(delta: dict[str, Any], sig_mood: float, sig_dim: float) -> bool:
    if abs(float(delta.get("moodDelta") or 0)) >= sig_mood:
        return True
    dims = delta.get("dims") or {}
    return any(abs(float(dims.get(k) or 0)) >= sig_dim for k in DIM_KEYS)


def decay(state: dict[str, Any], base: float, now: float, half_hours: float = 12) -> dict | None:
    """心情向 base、七项向 0 半衰。返回一条衰减事件（够 3 格才记），否则 None。"""
    last = float(state.get("last_decay_at") or 0)
    age_min = max(0.0, now - last) / 60.0
    if age_min < 1:
        return None
    half = clamp(float(half_hours or 12), 1, 168) * 60.0
    km = 1 - math.exp(-(math.log(2) * age_min) / half)
    state["mood"] = float(state["mood"]) + (float(base) - float(state["mood"])) * km
    ke = 1 - math.exp(-(math.log(2) * age_min) / 90.0)
    for k in DIM_KEYS:
        state["dims"][k] = max(0.0, float(state["dims"].get(k, 0)) * (1 - ke))
    state["last_decay_at"] = float(now)
    if abs(float(state["mood"]) - float(state.get("last_noted_mood") or 0)) < 3:
        return None
    falling = float(state["mood"]) < float(state.get("last_noted_mood") or 0)
    reason = (
        "过了几个钟头，刚才那点劲儿慢慢淡了，心里回到平常的样子"
        if falling else
        "过了几个钟头，心里那点别扭自己散了，人也松快回来"
    )
    state["last_reason"] = reason
    state["last_confidence"] = 100
    state["updated_at"] = float(now)
    state["last_noted_mood"] = float(state["mood"])
    state["minor"] = 0
    state["kind"] = "decay"
    return {
        "at": float(now),
        "kind": "decay",
        "reason": reason,
        "confidence": 100,
        "delta": 0.0,
        "dims_delta": {},
    }


def apply_delta(state: dict[str, Any], delta: dict[str, Any], floor: float, now: float) -> dict:
    step = mood_step(delta.get("moodDelta") or 0)
    state["mood"] = clamp(float(state["mood"]) + step, floor, 100.0)
    for k in DIM_KEYS:
        state["dims"][k] = clamp(float(state["dims"].get(k, 0)) + float(delta["dims"].get(k) or 0), 0.0, 100.0)
    state["last_reason"] = str(delta.get("reason") or "")
    state["last_confidence"] = int(delta.get("confidence") or 0)
    state["kind"] = "judge"
    state["last_noted_mood"] = float(state["mood"])
    state["updated_at"] = float(now)
    state["last_decay_at"] = float(now)
    return state


def norm_reason(text: str, owner: str = "对方") -> str:
    """只替换「够确定」的第三人称代词：整句仅一个代词，且没有别人+说话/心理动词当主语。"""
    name = str(owner or "").strip()
    raw = str(text or "")
    if not name:
        return raw
    out: list[str] = []
    for sent in _SENT_SPLIT.split(raw):
        if len(re.findall("[她他]", sent)) != 1:
            out.append(sent)
            continue
        if _OTHER_SPEAK.search(re.sub("[她他]", "", sent)):
            out.append(sent)
            continue
        out.append(re.sub(r"([她他])(?=(?:" + _SPEAK + "))", name, sent))
    return "".join(out)


def _finite(value: Any) -> bool:
    """数值/数字字符串须为有限数；None 与非数字返回 True，沿用 _int 的兜底语义。"""
    if value is None:
        return True
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _int(value: Any, lo: int, hi: int) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = 0
    return int(clamp(n, lo, hi))


def parse_delta(text: str, owner: str = "对方") -> dict[str, Any] | None:
    s = str(text or "").strip()
    if not s:
        return None
    obj = None
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        try:
            obj = json.loads(s[a:b + 1])
        except (TypeError, ValueError):
            obj = None
    if obj is None:
        try:
            obj = json.loads(s)
        except (TypeError, ValueError):
            return None
    if not isinstance(obj, dict):
        return None
    dims_raw = obj.get("dims") if isinstance(obj.get("dims"), dict) else {}
    if not _finite(obj.get("moodDelta")):
        return None
    if any(not _finite(dims_raw.get(k)) for k in DIM_KEYS):
        return None
    if not _finite(obj.get("confidence")):
        return None
    dims = {k: _int(dims_raw.get(k), -10, 10) for k in DIM_KEYS}
    conf = obj.get("confidence")
    confidence = 70 if conf is None or str(conf).strip() == "" else _int(conf, 0, 100)
    return {
        "moodDelta": _int(obj.get("moodDelta"), -10, 10),
        "dims": dims,
        "reason": norm_reason(str(obj.get("reason") or ""), owner)[:120],
        "confidence": confidence,
    }


def label_of(v: float) -> str:
    v = float(v)
    if v >= 80:
        return "欢喜"
    if v >= 62:
        return "开心"
    if v >= 42:
        return "一般"
    if v >= 25:
        return "心疼"
    return "难过"


def face_of(v: float) -> str:
    v = float(v)
    if v >= 80:
        return "face-great.png"
    if v >= 62:
        return "face-good.png"
    if v >= 42:
        return "face-ok.png"
    if v >= 25:
        return "face-worry.png"
    return "face-sad.png"


def basis_of(v: float) -> str:
    v = float(v)
    if v >= 80:
        return "心里是亮堂的"
    if v >= 62:
        return "心里是松的"
    if v >= 42:
        return "心里还算平"
    if v >= 25:
        return "心里有点沉"
    return "心里挺沉的"


def top_dims(state: dict[str, Any], n: int = 2) -> list[dict[str, Any]]:
    items = [
        {"key": k, "label": DIM_LABELS[k], "value": int(round(float(state["dims"].get(k, 0))))}
        for k in DIM_KEYS
    ]
    items = [x for x in items if x["value"] >= 15]
    items.sort(key=lambda x: x["value"], reverse=True)
    return items[:n]


def time_word(ts: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    hours = (now - float(ts or 0)) / 3600.0
    if hours < 2:
        return "刚才"
    if hours < 12:
        return "早些时候"
    if hours < 48:
        return "昨天"
    return "前几天"


def mood_line(state: dict[str, Any], mode: str, owner: str, now: float | None = None) -> str:
    who = str(owner or "").strip() or "对方"
    reason = str(state.get("last_reason") or "").strip()
    if mode == "event":
        if not reason:
            return ""
        top = top_dims(state, 1)
        head = "（这会儿）" + basis_of(float(state["mood"]))
        if top:
            head += "，还" + DIM_SHORT.get(top[0]["key"], top[0]["label"])
        origin = reason if state.get("kind") == "decay" else (
            f"（{time_word(float(state.get('updated_at') or 0), now)}）{reason}"
        )
        return (
            f"{head}\n{origin}\n\n"
            f"这是这会儿的底子，不是非这样不可——{who}说别的，你按自己的判断接，也别把这段念出来。"
        )
    parts = [DIM_PLAIN.get(t["key"], t["label"]) for t in top_dims(state, 2)]
    return (
        f"（此刻你）心情{label_of(float(state['mood']))}"
        + ("，" + "、".join(parts) if parts else "")
        + f"。这是背景，不是这一轮必须有的情绪；{who}说别的事，你就按自己的判断来，别把这句话念出来。"
    )
