"""主动搭话纯决策引擎：峰谷、安静时段、时间系数与命中线。

所有函数只依赖传入的时间戳与设置字典，不读库、不读配置、不取系统时间，
方便调度层注入假时钟做确定性测试。
"""
from __future__ import annotations

import time
from typing import Any

# 峰时区间，含起不含止；本地周一至周五才生效，周末全谷。
PEAK_RANGES: tuple[tuple[int, int], ...] = ((9, 12), (14, 18))


def _local(now: float) -> time.struct_time:
    return time.localtime(float(now))


def day_key(now: float) -> str:
    """本地日期键（YYYY-MM-DD），用于日上限按天清零。"""
    return time.strftime("%Y-%m-%d", _local(now))


def is_peak(now: float) -> bool:
    """本地周一至周五 09–12、14–18 为峰；含起不含止，周末全谷。"""
    local = _local(now)
    if local.tm_wday >= 5:
        return False
    hour = local.tm_hour
    return any(start <= hour < end for start, end in PEAK_RANGES)


def in_quiet(now: float, start: int, end: int) -> bool:
    """安静时段：含起不含止；start==end 视为关闭；start>end 时跨午夜。"""
    start = int(start)
    end = int(end)
    if start == end:
        return False
    hour = _local(now).tm_hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _last_ts(value: Any) -> float:
    """把缺失/坏值统一成 0，交给调用方按冷场处理。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def timing_factor(last_ts: float, now: float, st: dict[str, Any]) -> float:
    """距上次会话消息太近打折、太久加成，中间维持 1.0。"""
    last = _last_ts(last_ts)
    if not last > 0:  # <=0 或 NaN 都按冷场
        return float(st["cold_factor"])
    age_min = (float(now) - last) / 60.0
    if age_min < float(st["recent_min"]):
        return float(st["recent_factor"])
    if age_min > float(st["cold_min"]):
        return float(st["cold_factor"])
    return 1.0


def hit_line(now: float, st: dict[str, Any], misses: int, last_ts: float) -> float:
    """峰/谷基础命中线 × 时间系数 + 连败加成，结果钳在 0–100。"""
    base = float(st["hit_peak"] if is_peak(now) else st["hit_offpeak"])
    try:
        streak = max(0.0, float(misses))
    except (TypeError, ValueError):
        streak = 0.0
    bonus = min(
        max(0.0, float(st["bonus_cap"])),
        streak * max(0.0, float(st["miss_bonus"])),
    )
    return max(0.0, min(100.0, base * timing_factor(last_ts, now, st) + bonus))


def hit(roll: float, line: float) -> bool:
    """掷骰命中：roll 取 [0,1)，roll*100 严格小于命中线才算中。"""
    try:
        value = float(roll)
        threshold = float(line)
    except (TypeError, ValueError):
        return False
    if not 0.0 <= value < 1.0:
        return False
    return value * 100.0 < threshold
