"""主动搭话纯决策引擎：峰谷、安静时段、时间系数与命中线边界。"""
from __future__ import annotations

import time

import pytest

from app.proactive import engine, settings

# 直接复用账号默认值，避免测试里再抄一份数字导致与配置脱节。
ST = dict(settings.ACCOUNT_DEFAULTS)


def _at(year: int, month: int, day: int, hour: int, minute: int) -> float:
    """按本地时区构造时间戳；tm_isdst=-1 交给系统判断夏令时。"""
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


MONDAY_10 = _at(2026, 10, 26, 10, 0)
MONDAY_13 = _at(2026, 10, 26, 13, 0)


def test_reference_dates_are_what_tests_assume():
    """先钉死日历，避免峰谷/周末用例因日期写错悄悄失效。"""
    assert time.localtime(_at(2026, 10, 26, 12, 0)).tm_wday == 0  # 周一
    assert time.localtime(_at(2026, 10, 30, 12, 0)).tm_wday == 4  # 周五
    assert time.localtime(_at(2026, 10, 31, 12, 0)).tm_wday == 5  # 周六
    assert time.localtime(_at(2026, 11, 1, 12, 0)).tm_wday == 6   # 周日


def test_peak_ranges_constant():
    assert engine.PEAK_RANGES == ((9, 12), (14, 18))


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (8, 59, False),
        (9, 0, True),
        (11, 59, True),
        (12, 0, False),
        (13, 59, False),
        (14, 0, True),
        (17, 59, True),
        (18, 0, False),
    ],
)
def test_is_peak_weekday_boundaries(hour, minute, expected):
    assert engine.is_peak(_at(2026, 10, 26, hour, minute)) is expected


def test_is_peak_weekend_is_always_offpeak():
    assert engine.is_peak(_at(2026, 10, 31, 10, 0)) is False  # 周六上午
    assert engine.is_peak(_at(2026, 11, 1, 15, 0)) is False   # 周日下午


def test_is_peak_friday_is_still_weekday():
    assert engine.is_peak(_at(2026, 10, 30, 10, 0)) is True


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (0, 59, False),
        (1, 0, True),
        (7, 59, True),
        (8, 0, False),
    ],
)
def test_in_quiet_same_day_window(hour, minute, expected):
    assert engine.in_quiet(_at(2026, 10, 26, hour, minute), 1, 8) is expected


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (23, 30, True),
        (0, 0, True),
        (5, 59, True),
        (6, 0, False),
    ],
)
def test_in_quiet_cross_midnight_window(hour, minute, expected):
    assert engine.in_quiet(_at(2026, 10, 26, hour, minute), 23, 6) is expected


def test_in_quiet_start_equals_end_disables_window():
    for hour in range(24):
        assert engine.in_quiet(_at(2026, 10, 26, hour, 0), 5, 5) is False
        assert engine.in_quiet(_at(2026, 10, 26, hour, 30), 5, 5) is False


def test_in_quiet_cross_year_does_not_depend_on_year():
    assert engine.in_quiet(_at(2026, 12, 31, 23, 30), 23, 6) is True
    assert engine.in_quiet(_at(2027, 1, 1, 0, 0), 23, 6) is True
    assert engine.in_quiet(_at(2027, 1, 1, 5, 59), 23, 6) is True
    assert engine.in_quiet(_at(2027, 1, 1, 6, 0), 23, 6) is False


def test_in_quiet_casts_bounds_to_int():
    assert engine.in_quiet(_at(2026, 10, 26, 1, 0), "1", "8") is True
    assert engine.in_quiet(_at(2026, 10, 26, 8, 0), "1", "8") is False


@pytest.mark.parametrize(
    "last_ts,expected",
    [
        (0, 1.5),
        (-10, 1.5),
        (None, 1.5),
        (MONDAY_10 - 600, 0.15),   # 10 分钟前：recent
        (MONDAY_10 - 1200, 1.0),   # 正好 20 分钟：区间内
        (MONDAY_10 - 3600, 1.0),   # 60 分钟：区间内
        (MONDAY_10 - 21600, 1.0),  # 正好 360 分钟：区间内
        (MONDAY_10 - 24000, 1.5),  # 400 分钟前：cold
        (MONDAY_10 + 60, 0.15),    # 未来时间：负 age 也按 recent
    ],
)
def test_timing_factor(last_ts, expected):
    assert engine.timing_factor(last_ts, MONDAY_10, ST) == pytest.approx(expected)


def test_hit_line_peak_and_offpeak_base():
    # 60 分钟前落在 recent/cold 之间，时间系数为 1。
    assert engine.hit_line(MONDAY_10, ST, 0, MONDAY_10 - 3600) == pytest.approx(20.0)
    assert engine.hit_line(MONDAY_13, ST, 0, MONDAY_13 - 3600) == pytest.approx(60.0)


def test_hit_line_applies_timing_factor():
    assert engine.hit_line(MONDAY_10, ST, 0, MONDAY_10 - 600) == pytest.approx(3.0)
    assert engine.hit_line(MONDAY_10, ST, 0, MONDAY_10 - 24000) == pytest.approx(30.0)
    assert engine.hit_line(MONDAY_10, ST, 0, 0) == pytest.approx(30.0)  # 无会话记录按冷场


@pytest.mark.parametrize(
    "misses,expected",
    [
        (0, 20.0),
        (2, 40.0),
        (-3, 20.0),
    ],
)
def test_hit_line_miss_bonus(misses, expected):
    assert engine.hit_line(MONDAY_10, ST, misses, MONDAY_10 - 3600) == pytest.approx(expected)


def test_hit_line_bonus_is_capped():
    capped = {**ST, "bonus_cap": 15}
    assert engine.hit_line(MONDAY_10, capped, 1, MONDAY_10 - 3600) == pytest.approx(30.0)
    assert engine.hit_line(MONDAY_10, capped, 2, MONDAY_10 - 3600) == pytest.approx(35.0)
    assert engine.hit_line(MONDAY_10, capped, 5, MONDAY_10 - 3600) == pytest.approx(35.0)

    no_bonus = {**ST, "miss_bonus": 0}
    assert engine.hit_line(MONDAY_10, no_bonus, 9, MONDAY_10 - 3600) == pytest.approx(20.0)


def test_hit_line_combines_timing_and_miss_bonus():
    # 峰时 20 × 0.15 + 2 次未中 × 10 = 23
    assert engine.hit_line(MONDAY_10, ST, 2, MONDAY_10 - 600) == pytest.approx(23.0)


def test_hit_line_result_is_clamped():
    high = {**ST, "hit_peak": 90}
    # 90 × cold_factor(1.5) = 135，必须封顶 100。
    assert engine.hit_line(MONDAY_10, high, 0, MONDAY_10 - 24000) == pytest.approx(100.0)

    low = {**ST, "hit_peak": 10, "recent_factor": -2}
    # 10 × -2 = -20，必须压到 0。
    assert engine.hit_line(MONDAY_10, low, 0, MONDAY_10 - 600) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "roll,line,expected",
    [
        (0.0, 20.0, True),
        (0.199, 20.0, True),
        (0.2, 20.0, False),
        (0.0, 0.0, False),
        (0.5, 0.0, False),
        (0.999, 100.0, True),
        (1.0, 100.0, False),
        (-0.1, 100.0, False),
    ],
)
def test_hit_boundaries(roll, line, expected):
    assert engine.hit(roll, line) is expected


def test_day_key_uses_local_date():
    assert engine.day_key(_at(2026, 10, 29, 23, 59)) == "2026-10-29"
    assert engine.day_key(_at(2026, 10, 30, 0, 0)) == "2026-10-30"
    assert engine.day_key(_at(2026, 12, 31, 23, 59)) == "2026-12-31"
    assert engine.day_key(_at(2027, 1, 1, 0, 1)) == "2027-01-01"
