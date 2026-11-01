"""微信界面唯一入口：所有会动到同一个微信窗口的 UIA 操作必须排队。

发送侧把 UIA 操作收敛在驱动的 RPA 线程上（weixin4_driver._rpa_loop），朋友圈
点赞/评论跑在自己的工作线程上。两边各有一把锁，却操作同一个桌面窗口：评论正在
滚动时间线时，发送路径开头的 back_to_chat_tab() 会点一下导航栏「微信」把页面
切走，mmui::TimeLineListView 虽还在树里但 BoundingRectangle 全零，滚轮落点校验
（moment._send_scroll 的 expect_pid）直接把每次滚动都拦下，find_moment 一屏都没
翻过就报「未能定位到目标朋友圈」。

所以锁必须是同一把：谁在动界面，别人就等它动完。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

# 可重入：RPA 线程里一个操作可能再进一层同线程的界面动作，不能自锁
UI_LOCK = threading.RLock()


class UIBusy(RuntimeError):
    """界面被别的操作占着且等不到；调用方应放弃本次操作，别硬抢。"""


def acquire(timeout: float | None = None) -> bool:
    """尝试占用微信界面；timeout=None 表示一直等。"""
    if timeout is None:
        UI_LOCK.acquire()
        return True
    return UI_LOCK.acquire(True, float(timeout))


def release() -> None:
    UI_LOCK.release()


@contextmanager
def hold(timeout: float | None = None) -> Iterator[None]:
    """with ui_gate.hold(timeout=45): 占不到界面就抛 UIBusy。"""
    if not acquire(timeout):
        raise UIBusy("微信界面正被消息收发占用，这次先不操作")
    try:
        yield
    finally:
        release()
