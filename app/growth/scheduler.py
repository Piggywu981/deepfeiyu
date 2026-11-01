"""成长调度：默认 03:30 反思；关机则启动后补跑；每账号每天最多一次自动/补跑。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .. import config, db
from . import reflect, store

log = logging.getLogger("growth.scheduler")


def _today(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _scheduled_at(now: float, hour: int, minute: int) -> float:
    tm = time.localtime(now)
    return time.mktime(
        (tm.tm_year, tm.tm_mon, tm.tm_mday, hour, minute, 0, 0, 0, -1)
    )


def next_run_at(account_id: str, now: float | None = None) -> float | None:
    now = now or time.time()
    cfg = config.growth_cfg()
    hour = int(cfg.get("reflection_hour") or 3)
    minute = int(cfg.get("reflection_minute") or 30)
    today = _today(now)
    scheduled = _scheduled_at(now, hour, minute)
    if store.count_runs_today(account_id, today) > 0:
        return scheduled + 86400
    return scheduled if now < scheduled else now


class GrowthScheduler:
    def __init__(
        self,
        llm: Any,
        interval: float = 60.0,
        now: Callable[[], float] = time.time,
        runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.llm = llm
        self.interval = max(0.01, float(interval))
        self._now = now
        self._runner = runner or reflect.run_reflection
        self._started_at = now()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        for account in config.accounts():
            if not config.growth_enabled(account["id"]):
                continue
            try:
                store.mark_stale_runs(account["id"])
            except Exception as exc:  # noqa: BLE001
                log.warning("清理遗留成长运行失败 %s: %s", account.get("id"), exc)
        self._started_at = self._now()
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
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
            except Exception as exc:  # noqa: BLE001
                log.warning("成长调度 tick 失败: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> None:
        now = self._now()
        for account in config.accounts():
            account_id = str(account.get("id") or "")
            if not account_id or not config.growth_enabled(account_id):
                continue
            trigger = self.due_trigger(account_id, now)
            if trigger:
                await self._runner(self.llm, account_id, trigger)

    def due_trigger(self, account_id: str, now: float | None = None) -> str | None:
        now = now or self._now()
        cfg = config.growth_cfg()
        today = _today(now)
        if store.count_runs_today(account_id, today) > 0:
            return None
        last = store.last_successful_run(account_id)
        since = (
            float(last.get("finished_at") or last.get("started_at") or 0)
            if last
            else 0.0
        )
        if last and now - since < 18 * 3600:
            return None
        hour = int(cfg.get("reflection_hour") or 3)
        minute = int(cfg.get("reflection_minute") or 30)
        scheduled = _scheduled_at(now, hour, minute)
        if now < scheduled:
            if not cfg.get("catch_up", True):
                return None
            if not last:
                return None
            if now - self._started_at < float(cfg.get("catch_up_delay_sec") or 120):
                return None
            trigger = "catchup"
            window_start = now - 86400
        else:
            trigger = "auto"
            window_start = since or (now - 86400)
        minimum = int(cfg.get("min_new_messages") or 20)
        if db.count_messages(account_id, window_start, now) >= minimum:
            return trigger
        if db.count_agent_turns_since(account_id, window_start) > 0:
            return trigger
        if db.count_memories_since(account_id, window_start) > 0:
            return trigger
        return None
