"""主动搭话调度：每 60 秒为每个账号的白名单会话串行掷骰。

调度器只负责“周期到了去问一遍业务层”，真正的开关、闸门、掷骰、额度与发送
都在 :class:`ProactiveService` 里。启动即创建后台循环，但不因当前关闭而停止
循环：控制台打开开关后无需重启服务即可生效，空 tick 不产生模型调用或发送。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .. import config
from . import settings
from .service import ProactiveService

log = logging.getLogger("proactive")


class ProactiveScheduler:
    """主动搭话的后台调度器；start/stop 幂等，单条会话异常不拖垮整轮。"""

    def __init__(
        self,
        hub: Any,
        service: Any = None,
        interval: float = 60.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.hub = hub
        self._now = now
        self.interval = max(1.0, float(interval))
        self.service = service or ProactiveService(hub, now=now)
        self._task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        """启动后台循环；重复调用不会产生第二个任务。"""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停止后台循环；可重复调用，未启动时是空操作。"""
        self._stop.set()
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 后台调度异常只记日志
                log.warning("主动搭话 tick 失败：%s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> None:
        """扫一遍账号白名单；关闭、未启用、离线或无白名单时静默跳过。"""
        if not settings.enabled():
            return
        for account in config.accounts():
            if not account.get("enabled", True):
                continue
            aid = account["id"]
            session = self.hub.get(aid)
            if session is None or getattr(session, "driver", None) is None:
                continue
            st = settings.settings(aid)
            whitelist = st.get("whitelist") or []
            if not whitelist:
                continue
            # 串行掷骰：同一账号内避免并发生成与发送；单条失败只记日志
            for entry in whitelist:
                try:
                    await self.service.maybe_roll(aid, entry, "auto")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单条异常不中断其余会话
                    log.warning(
                        "主动搭话调度失败（%s/%s）：%s",
                        aid,
                        (entry or {}).get("chat_uid") if isinstance(entry, dict) else entry,
                        exc,
                    )
