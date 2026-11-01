"""朋友圈互动与消息收发必须共用一把界面锁，失败原因要按现场区分。

回归的是 2026-10-26 那次「评论未成功：未能定位到目标朋友圈」：评论正在滚动
时间线时，发送线程开头的 back_to_chat_tab() 把页面点掉了，库缓存的列表控件
rect 塌成全零，之后 5 次重试全对着一个看不见的控件空转。
"""
from __future__ import annotations

import contextlib
import queue
import threading
import time

import pytest

from app.wechat import moments as moments_mod
from app.wechat import ui_gate
from app.wechat.moments import MomentsService
from app.wechat.weixin4_driver import Weixin4Driver

FEED = {"nickname": "ＭＩＯＳ", "username": "wxid_a", "text": "台式机三年不坏是因为它没被塞进背包"}


@contextlib.contextmanager
def gate_held_by_other_thread():
    """界面锁是可重入的，同线程占着照样能再拿到——必须换个线程模拟发送侧。"""
    acquired, stop = threading.Event(), threading.Event()

    def _hold():
        with ui_gate.hold():
            acquired.set()
            stop.wait(10)

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    assert acquired.wait(5)
    try:
        yield
    finally:
        stop.set()
        t.join(5)


class FakeMoment:
    """只实现 MomentsService 用到的那几个库接口。"""

    def __init__(self, rect=(10, 10, 900, 800)) -> None:
        self._rect = rect
        self._list = "缓存的列表控件"
        self._api = "缓存的时间线窗口"
        self.commented = None
        self.locate_ok = True

    def _time_line_rect(self):
        return self._rect

    def CommentMoment(self, publisher, keyword, content, db):  # noqa: N802 - 对齐库命名
        self.commented = (publisher, keyword, content)
        status = "成功" if self.locate_ok else "失败"
        message = "" if self.locate_ok else MomentsService._LOCATE_FAIL
        return {"status": status, "message": message, "data": {}}


class FakeGUI:
    def __init__(self) -> None:
        self.back_to_chat = 0

    def _get_uia(self):
        gui = self

        class _Uia:
            def back_to_chat_tab(self):
                gui.back_to_chat += 1
                return True

        return _Uia()


class FakeWX:
    def __init__(self, moment: FakeMoment) -> None:
        self.Moment = moment
        self._gui = FakeGUI()


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setattr(moments_mod, "_com_init", lambda: None)
    monkeypatch.setattr(moments_mod, "_restore_wechat_window", lambda wx: None)
    session = type("S", (), {"account_id": "acc"})()
    svc = MomentsService(session, driver=object())
    svc._mdb = lambda: object()  # 标尺库只作为参数传给假对象
    return svc


def _with_page(service, moment):
    """把假的 wx 装到 service 上。"""
    wx = FakeWX(moment)
    service._wechat = lambda: wx
    return wx


# ------------------------------------------------------------------ 界面锁


def test_rpa_loop_waits_for_the_shared_gate():
    """驱动 RPA 线程里的每个操作都要占住 ui_gate，朋友圈互动期间不能插队。"""
    driver = Weixin4Driver.__new__(Weixin4Driver)  # 不跑 __init__：只要那条循环
    q: queue.Queue = queue.Queue()
    driver._rpa_q = q
    box = {"done": threading.Event(), "value": None, "error": None}
    ran = threading.Event()

    def _op():
        ran.set()
        return "ok"

    loop = threading.Thread(target=driver._rpa_loop, daemon=True)
    with gate_held_by_other_thread():  # 模拟朋友圈评论正独占界面
        loop.start()
        q.put((_op, (), box))
        time.sleep(0.3)
        assert not ran.is_set(), "界面被占着时 RPA 线程照样执行了操作"
    assert box["done"].wait(5)
    assert ran.is_set() and box["value"] == "ok"
    q.put(None)
    loop.join(5)


def test_comment_gives_up_when_ui_is_busy(service, monkeypatch):
    monkeypatch.setattr(moments_mod, "_UI_WAIT_SECONDS", 0.05)
    called = []
    service._wechat = lambda: called.append(1)
    with gate_held_by_other_thread():
        res = service.comment(FEED, "通勤路才是杀手")
    assert "界面" in res["message"] and "先不操作" in res["message"]
    assert not called, "等不到界面却还是去抢窗口了"


# ------------------------------------------------------------------ 现场判定


def test_comment_refuses_when_timeline_not_visible(service):
    """时间线 rect 全零 = 页面没停在朋友圈，别再去滚动空转。"""
    moment = FakeMoment(rect=(0, 0, 0, 0))
    wx = _with_page(service, moment)
    res = service.comment(FEED, "台式机三年不坏")
    assert "没能停在朋友圈页" in res["message"]
    assert moment.commented is None
    assert wx._gui.back_to_chat == 0


def test_interaction_clears_stale_uia_cache(service):
    """每次互动前必须清掉 Moment 的 _list/_api 缓存，否则永远不会重新切页。"""
    moment = FakeMoment()
    _with_page(service, moment)
    assert service._ensure_moments_page(FakeWX(moment)) == ""
    assert moment._list is None and moment._api is None


def test_comment_success_returns_window_to_chat_page(service):
    moment = FakeMoment()
    wx = _with_page(service, moment)
    res = service.comment(FEED, "win本的敌人是通勤路")
    assert res["ok"] is True
    assert moment.commented[0] == "ＭＩＯＳ"
    assert moment.commented[2] == "win本的敌人是通勤路"
    assert wx._gui.back_to_chat == 1, "操作完没把微信带回聊天页"


def test_locate_failure_distinguishes_page_stolen(service):
    """库只会说「未能定位到目标朋友圈」，得按现场证据分成两种原因。"""
    moment = FakeMoment()
    moment.locate_ok = False
    _with_page(service, moment)
    res = service.comment(FEED, "评论")
    assert "翻遍了也没找到" in res["message"]

    class Stolen(FakeMoment):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.probes = 0

        def _time_line_rect(self):
            # 进场时可见，评论滚到一半被消息收发把页面切走了
            self.probes += 1
            return self._rect if self.probes == 1 else (0, 0, 0, 0)

    moment2 = Stolen(rect=(10, 10, 900, 800))
    moment2.locate_ok = False
    _with_page(service, moment2)
    res2 = service.comment(FEED, "评论")
    assert "被切走" in res2["message"]


def test_explain_passes_through_other_messages():
    assert MomentsService._explain("评论内容不能为空", True) == "评论内容不能为空"


def test_like_uses_the_same_gate(service, monkeypatch):
    monkeypatch.setattr(moments_mod, "_UI_WAIT_SECONDS", 0.05)
    with gate_held_by_other_thread():
        res = service.like(FEED)
    assert "先不操作" in res["message"]
