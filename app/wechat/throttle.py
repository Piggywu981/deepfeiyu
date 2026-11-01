"""发送节流：全局最小间隔 + 同会话冷却，均带随机抖动。

风控视角：机器人发送的间隔分布均匀、无停顿，是强机器特征。这里把每次
发送放进两层排队——任意两次发送之间至少隔 min_gap，同一会话内再叠加
chat_gap，具体取值在区间内随机。并发调用在锁内预订槽位、锁外 sleep，
多会话同时回复时依旧严格串行，谁先预订谁先发。
"""
from __future__ import annotations

import random
import threading
import time


class SendThrottle:
    """线程安全；wait() 会阻塞到允许发送，应放在 to_thread 里调用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_global = 0.0
        self._next_chat: dict[str, float] = {}

    def wait(
        self,
        chat_id: str,
        min_gap: tuple[float, float],
        chat_gap: tuple[float, float],
        burst: bool = False,
    ) -> float:
        """预订一个发送槽位并等到时间到，返回实际等待的秒数。

        burst=True 用于「文字后跟表情」的组合发送：不等已预订的全局/会话槽位
        立即发出（1-3 秒的跟图间隔由调用方控制），发出后照常刷新两层下次可发
        时间，冷却从此刻重新起算。
        """
        with self._lock:
            now = time.monotonic()
            start = now if burst else max(self._next_global, self._next_chat.get(chat_id, 0.0), now)
            self._next_global = start + random.uniform(*min_gap)
            self._next_chat[chat_id] = start + random.uniform(*chat_gap)
            if len(self._next_chat) > 512:
                self._next_chat = {k: v for k, v in self._next_chat.items() if v > now}
        delay = start - now
        if delay > 0:
            time.sleep(delay)
        return delay
