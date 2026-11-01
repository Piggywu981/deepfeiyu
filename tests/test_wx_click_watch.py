"""tools/wx_click_watch.py 的判定逻辑：「单击被注入事件吃掉」必须能被测出，
并且每一类都有负控——没有注入事件时绝不能报被吞，否则这工具就是在自证预期。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_watch():
    spec = importlib.util.spec_from_file_location("wx_click_watch", ROOT / "tools" / "wx_click_watch.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = _load_watch()


def _ev(t, msg, x, y, injected=False, kind="mouse"):
    row = {"t": t, "kind": kind, "msg": msg, "x": x, "y": y, "injected": injected}
    return row


def test_injected_cursor_move_between_press_and_release_swallows_the_click():
    events = [
        _ev(0.0, "move", 100, 100),
        _ev(0.10, "left_down", 100, 100),
        _ev(0.15, "move", 600, 500, injected=True),  # 机器人把光标甩走了
        _ev(0.30, "left_up", 100, 100),
    ]
    hits = watch.find_swallowed_clicks(events)
    assert len(hits) == 1
    assert hits[0]["deviation"] == abs(600 - 100) + abs(500 - 100)
    assert hits[0]["followed_by_dblclick"] is False


def test_no_injected_event_means_no_swallowed_click():
    """负控：同样的按下/抬起，中间没有注入事件就不许报被吞。"""
    events = [
        _ev(0.10, "left_down", 100, 100),
        _ev(0.30, "left_up", 100, 100),
    ]
    assert watch.find_swallowed_clicks(events) == []


def test_hardware_move_between_press_and_release_is_the_users_own_drag():
    """负控 2：真人自己挪了鼠标不算被机器人吞（只认 injected 事件）。"""
    events = [
        _ev(0.10, "left_down", 100, 100),
        _ev(0.15, "move", 900, 900),  # 硬件移动，injected=False
        _ev(0.30, "left_up", 900, 900),
    ]
    assert watch.find_swallowed_clicks(events) == []


def test_small_injected_jitter_under_threshold_is_not_reported():
    events = [
        _ev(0.10, "left_down", 100, 100),
        _ev(0.15, "move", 101, 101, injected=True),
        _ev(0.30, "left_up", 100, 100),
    ]
    assert watch.find_swallowed_clicks(events) == []


def test_double_click_right_after_a_swallowed_click_is_flagged():
    """号主原话：单击全废，但双击会话能弹出独立窗口。工具要能把这条对上。"""
    events = [
        _ev(0.10, "left_down", 100, 100),
        _ev(0.15, "move", 800, 200, injected=True),
        _ev(0.30, "left_up", 100, 100),
        _ev(0.55, "left_dblclk", 100, 100),
    ]
    hits = watch.find_swallowed_clicks(events)
    assert len(hits) == 1
    assert hits[0]["followed_by_dblclick"] is True


def test_bots_own_click_is_not_mistaken_for_the_users_press():
    events = [
        _ev(0.10, "left_down", 500, 500, injected=True),
        _ev(0.12, "move", 100, 100),
        _ev(0.20, "left_up", 500, 500, injected=True),
    ]
    assert watch.find_swallowed_clicks(events) == []


def test_long_press_is_treated_as_a_drag_not_a_swallowed_click():
    events = [
        _ev(0.0, "left_down", 100, 100),
        _ev(1.0, "move", 700, 700, injected=True),
        _ev(5.0, "left_up", 100, 100),
    ]
    assert watch.find_swallowed_clicks(events) == []


def test_unresponsive_spans_merges_consecutive_misses():
    samples = [
        {"t": 0.0, "ok": True},
        {"t": 0.2, "ok": False},
        {"t": 0.4, "ok": False},
        {"t": 0.6, "ok": True},
        {"t": 0.8, "ok": False},
    ]
    spans = watch.unresponsive_spans(samples)
    assert len(spans) == 2
    assert spans[0]["misses"] == 2
    assert spans[0]["start"] == 0.2 and spans[0]["end"] == 0.4
    assert spans[1]["misses"] == 1


def test_all_responsive_samples_yield_no_span():
    samples = [{"t": i * 0.2, "ok": True} for i in range(20)]
    assert watch.unresponsive_spans(samples) == []


def test_summarize_states_the_click_theft_verdict(monkeypatch):
    events = [
        _ev(0.10, "left_down", 100, 100),
        _ev(0.15, "move", 800, 200, injected=True),
        _ev(0.30, "left_up", 100, 100),
    ]
    hits = watch.find_swallowed_clicks(events)
    text = watch.summarize(events, [{"t": 0.1, "ok": True}], [], hits, [])
    assert "被吞掉的单击：1 次" in text
    assert "不是微信卡死" in text


def test_summarize_points_at_uia_when_only_the_loop_stalled():
    samples = [{"t": 0.2, "ok": False}, {"t": 0.4, "ok": False}]
    text = watch.summarize([], samples, [], [], watch.unresponsive_spans(samples))
    assert "微信消息循环真的停过" in text


def test_hooks_need_an_interactive_desktop_session():
    """装不上钩子时报的是人能看懂的话，不是裸 OSError/ctypes 错误码。
    离线环境（CI、无桌面会话）只验这条错误路径存在。"""
    if not hasattr(watch.user32, "SetWindowsHookExW"):  # 非 Windows
        pytest.skip("低级钩子是 Windows 专属")
    try:
        watch.install_hooks()
    except SystemExit as exc:
        assert "交互桌面会话" in str(exc)
    else:
        watch.remove_hooks()
