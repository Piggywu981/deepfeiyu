"""桌宠子进程监管：起停、stdin 驱动、退避重启、降级不拖累主链路。

上游 helper.py 把「stdin 关闭」当作生命周期信号，我们据此收口。
所有写往 stdin 的消息都走 send() 这一个出口，由 protocol.encode 盖信封——
减少一处「忘了盖版本号」的机会，比在 reducer/bubble/settings 各盖一次可靠。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from typing import Any

from .. import bus, config, db
from . import assets, bubble, moodlink, protocol, reducer as reducer_mod, settings

READY_TIMEOUT_S = 30
HANDSHAKE_TIMEOUT_S = 8
BACKOFF_S = (1, 4, 9)
MAX_RESTARTS = 3
TICK_S = 2.0
ERROR_STREAK_LIMIT = 5


class PetSupervisor:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.reducer: reducer_mod.PetReducer | None = None
        self.reader_task: asyncio.Task | None = None
        self.pump_task: asyncio.Task | None = None
        self.writer_task: asyncio.Task | None = None
        self.outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self.running = False
        self.starting = False
        self.disabled_reason = ""
        self.restarts = 0
        self.error_streak = 0
        self.last_dims: dict[str, float] = {}
        self.last_pulse_s: int | None = None

    # ---------- 对外状态 ----------
    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(settings.section().get("enabled")),
            "running": self.running,
            "disabledReason": self.disabled_reason,
            "restarts": self.restarts,
            "state": self.reducer.current()["state"] if self.reducer else "IDLE",
            "bundleCommit": assets.UPSTREAM_COMMIT,
            "helperPath": str(assets.helper_script()),
        }

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        if self.running or self.starting:
            return
        cfg = settings.section()
        if not cfg.get("enabled"):
            self.disabled_reason = ""
            return
        ok, reason = assets.verify_bundle()
        if not ok:
            self.disabled_reason = reason
            return
        self.starting = True
        try:
            await self._spawn(cfg)
            await self._await_ready()
            await self._handshake()
        except Exception as exc:  # noqa: BLE001 - 桌宠失败绝不能冒泡到 lifespan
            self.disabled_reason = f"[spawn] {exc}"
            await self._kill_proc()
            self.starting = False
            return
        self.starting = False
        self.running = True
        self.disabled_reason = ""
        self.error_streak = 0
        self.reducer = reducer_mod.PetReducer(config.active_id())
        self.reader_task = asyncio.create_task(self._read_stdout())
        self.writer_task = asyncio.create_task(self._drain_outbox())
        self.pump_task = asyncio.create_task(self._pump_bus())
        for task in (self.reader_task, self.writer_task, self.pump_task):
            self._watch(task)
        await self.push(settings.config_message(cfg, await self._mood_payload()))
        await self.push(self._state_message(self.reducer.current()["state"]))

    async def stop(self) -> None:
        self.running = False
        for task in (self.pump_task, self.writer_task, self.reader_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.pump_task = self.writer_task = self.reader_task = None
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.stdin.write(protocol.encode_bytes({"kind": "shutdown"}))
                await proc.stdin.drain()
            with contextlib.suppress(Exception):
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, TimeoutError):
                proc.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=3)
        self.reducer = None

    async def restart(self) -> None:
        self.restarts = 0
        await self.stop()
        await self.start()

    async def apply_config(self) -> None:
        cfg = settings.section()
        await self.push(settings.config_message(cfg, await self._mood_payload()))

    async def preview(self, state: str, text: str = "") -> None:
        """预览与真实事件共用 send()，不留第二条投递路径。"""
        cfg = settings.section()
        message = bubble.message_for(
            state, mood=await self._mood_payload(), pet_cfg=cfg,
            chat_name=text if cfg.get("showChatName") else "",
        )
        await self.send(message)

    # ---------- 进程 IO ----------
    async def _spawn(self, cfg: dict[str, Any]) -> None:
        env = {**os.environ, **settings.env_for_process(cfg)}
        args = [sys.executable, str(assets.helper_script())]
        if _headless():
            args.append("--headless")
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=env, **kwargs,
        )

    async def _await_ready(self) -> None:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("Helper 没有 stdout")
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=READY_TIMEOUT_S)
        reply = json_line(line)
        if not reply or reply.get("kind") != "ready":
            raise RuntimeError(f"Helper 未就绪：{line.decode('utf-8', 'replace')[:200]}")

    async def _handshake(self) -> None:
        """ready 只是 Helper 的自述；ping/pong 才证明我们的写入通路是通的。

        线上教训：stdin 写入因类型不对整条崩掉时，Helper 仍然 ready、界面仍显示运行中。
        """
        if not self.proc or not self.proc.stdin or not self.proc.stdout:
            raise RuntimeError("Helper 管道不全")
        self.proc.stdin.write(protocol.encode_bytes({"kind": "ping"}))
        await self.proc.stdin.drain()
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=HANDSHAKE_TIMEOUT_S)
        reply = json_line(line)
        if not reply or reply.get("kind") != "pong":
            raise RuntimeError(f"Helper 未回应 ping：{line.decode('utf-8', 'replace')[:120]}")

    async def _read_stdout(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            reply = json_line(line)
            if not reply:
                continue
            if reply.get("kind") == "error":
                self.error_streak += 1
                if self.error_streak >= ERROR_STREAK_LIMIT:
                    self.disabled_reason = f"[protocol] {str(reply.get('message', ''))[:120]}"
                    await self.stop()
                    return
            else:
                self.error_streak = 0
        code = await proc.wait() if proc.returncode is None else proc.returncode
        self.running = False
        if code not in (0, None) and self.restarts < MAX_RESTARTS:
            delay = BACKOFF_S[min(self.restarts, len(BACKOFF_S) - 1)]
            self.restarts += 1
            await asyncio.sleep(delay)
            await self.start()
        elif code not in (0, None):
            self.disabled_reason = f"[crash] Helper 连续退出 {MAX_RESTARTS} 次（exit {code}）"

    async def _drain_outbox(self) -> None:
        while self.running:
            try:
                message = await asyncio.wait_for(self.outbox.get(), timeout=TICK_S)
            except (asyncio.TimeoutError, TimeoutError):
                await self._tick()
                continue
            await self.send(message)

    async def _tick(self) -> None:
        if self.reducer is None:
            return
        for message in self.reducer.tick():
            await self.push(message)
        await self._emit_mood_pulse()

    def _watch(self, task: asyncio.Task) -> None:
        """后台任务意外崩掉时不许还对外说 running=True：把原因记下来并落成不可用。

        线上教训：writer 任务因 bytes/str 不匹配静默死掉，界面却显示"运行中 · IDLE"，
        只有点预览时才以 500 的形式暴露。
        """

        def done(finished: asyncio.Task) -> None:
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                self.disabled_reason = f"[task] {exc!r}"[:160]
                self.running = False

        task.add_done_callback(done)

    async def send(self, message: dict[str, Any]) -> None:
        proc = self.proc
        if not message or proc is None or proc.returncode is not None or not proc.stdin:
            return
        try:
            proc.stdin.write(protocol.encode_bytes(message))
            await proc.stdin.drain()
        except (ConnectionResetError, ValueError, OSError):
            return

    async def push(self, message: dict[str, Any] | None) -> None:
        if not message:
            return
        if self.outbox.full():      # 桌宠可以丢帧，不能拖垮总线订阅
            with contextlib.suppress(asyncio.QueueEmpty):
                self.outbox.get_nowait()
        await self.outbox.put(message)

    # ---------- 事件与心情 ----------
    async def _pump_bus(self) -> None:
        queue = bus.bus.subscribe()
        try:
            while self.running and self.reducer is not None:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=TICK_S)
                except (asyncio.TimeoutError, TimeoutError):
                    await self._tick()
                    continue
                for message in self.reducer.on_event(event):
                    await self.push(await self._decorate(message))
        finally:
            bus.bus.unsubscribe(queue)

    async def _decorate(self, message: dict[str, Any]) -> dict[str, Any]:
        cfg = settings.section()
        state = message.get("state") or "IDLE"
        if message.get("kind") == "pulse":
            return {
                **message,
                **bubble.message_for(
                    state, mood=await self._mood_payload(), pet_cfg=cfg,
                    kind="pulse", ttlMs=message.get("ttlMs", bubble.PULSE_TTL_DEFAULT),
                    resumeState=message.get("resumeState", "IDLE"),
                ),
            }
        chat_name = self._chat_name() if (
            cfg.get("showChatName") and state not in {"IDLE", "DISCONNECTED"}
        ) else ""
        out = bubble.message_for(
            state, mood=await self._mood_payload(), pet_cfg=cfg, chat_name=chat_name,
            kind=message.get("kind", "state"),
        )
        if message.get("activity"):
            out["activity"] = message["activity"]
        return out

    async def _emit_mood_pulse(self) -> None:
        payload = await self._mood_payload()
        if not payload or self.reducer is None:
            return
        import time as _time

        now_s = int(_time.time())
        pulse = moodlink.pulse_for_change(self.last_dims, payload["dims"],
                                          now_s=now_s, last_pulse_s=self.last_pulse_s)
        self.last_dims = payload["dims"]
        if pulse and not self.reducer.latched:
            self.last_pulse_s = now_s
            await self.push(await self._decorate(pulse))

    def _state_message(self, state: str) -> dict[str, Any]:
        return {"kind": "state", "state": state}

    async def _mood_payload(self) -> dict[str, Any] | None:
        try:
            return moodlink.snapshot(config.active_id(), self._chat_uid())
        except Exception:  # noqa: BLE001 - 读不到就退回无情绪文案
            return None

    def _chat_uid(self) -> str:
        return getattr(self.reducer, "last_chat_uid", "") if self.reducer else ""

    def _chat_name(self) -> str:
        """会话名现取本地库；名字本身按不可信文本处理（清洗在 bubble 里做）。"""
        uid = self._chat_uid()
        if not uid:
            return ""
        chat = db.get_chat(uid) or {}
        return str(chat.get("name") or "")

    async def _kill_proc(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)


def json_line(raw: bytes) -> dict[str, Any] | None:
    """Helper 的 stdout 混杂 Qt 自身的告警行，解析不了就跳过，不许因此判死进程。"""
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _headless() -> bool:
    """自检/CI 用的开关：不弹窗，只验协议通路。"""
    return bool(os.environ.get("DEEPFEIYU_PET_HEADLESS"))


supervisor = PetSupervisor()
