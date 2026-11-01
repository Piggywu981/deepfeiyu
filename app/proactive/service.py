"""主动搭话服务：调度掷骰、发送前闸门、生成、发送与额度记账。

调度层只按周期调用 :meth:`ProactiveService.maybe_roll`；控制台的试运行与
手动发送分别走 :meth:`preview` / :meth:`send_one`。所有时间与随机数都从构造
函数注入，测试可以用假时钟、假随机数精确复现每一次命中与拦截。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from .. import config, db, rules
from ..mood import service as mood_service
from . import engine, settings, store

log = logging.getLogger("proactive")


class ProactiveService:
    """主动搭话业务入口；同一会话的预览/发送串行，避免重复开口。"""

    def __init__(self, hub: Any, now=time.time, rng=random.random) -> None:
        self._hub = hub
        self._now = now
        self._rng = rng
        # (account_id, chat_uid) -> 正在生成/发送；防同一会话并发预览与真发
        self._running: set[tuple[str, str]] = set()
        # account_id -> 锁：重查闸门 → 发送 → 记账必须是一段临界区，
        # 否则两条并发轮次（手动 + 自动）能在额度只剩 1 条时同时过闸
        self._send_locks: dict[str, asyncio.Lock] = {}

    def _account_lock(self, account_id: str) -> asyncio.Lock:
        return self._send_locks.setdefault(account_id, asyncio.Lock())

    def _gate(
        self,
        account_id: str,
        uid: str,
        *,
        trigger: str,
        ignore_quota: bool,
        at: float,
    ) -> tuple[str, Any]:
        """发前闸门：返回 ``(拦截原因, 可用 session)``，原因为空串表示放行。

        第一轮和发送前重查都走这一套，避免「生成期间关掉总开关或移出白名单」
        这种只重查部分条件就照发的情况。
        """
        if not settings.enabled():
            return "主动搭话未开启", None
        st = settings.settings(account_id)
        if not any(item["chat_uid"] == uid for item in st["whitelist"]):
            return "不在主动搭话白名单", None
        chat = db.get_chat(uid)
        if (
            not uid.startswith(f"{account_id}:")
            or chat is None
            or chat.get("account_id") != account_id
        ):
            return "会话不存在或不属于该账号", None
        session = self._hub.get(account_id)
        if session is None or getattr(session, "driver", None) is None:
            return "微信通道未启动", None
        # 只有测试触发才允许跳过额度；manual 带 ignore_quota 也不能绕过日上限
        if not (ignore_quota and trigger == "test"):
            reason = self._quota_reason(account_id, uid, st, at)
            if reason:
                return reason, None
        allowed, reason = rules.check_proactive(
            config.load(), config.account(account_id), chat, at
        )
        if not allowed:
            return reason, None
        return "", session

    # ------------------------------------------------------------------ 公共入口

    async def maybe_roll(
        self, account_id: str, entry: dict[str, Any], trigger: str = "auto"
    ) -> dict[str, Any]:
        """按周期为一条白名单会话掷骰；只有命中才生成并发送。

        未命中写 ``miss`` 事件并累加连败；静默跳过（关开关、离线、安静时段、
        冷却、间隔未到、日上限）不写事件，避免刷屏。
        """
        if not settings.enabled():
            return self._result("disabled")
        normalized = settings.valid_entry(entry)
        if normalized is None:
            return self._result("invalid")
        uid = normalized["chat_uid"]

        # 只处理当前白名单里的会话：非白名单条目不能消耗掷骰、写 miss 或清零连败
        whitelist = settings.whitelist(account_id)
        if not any(item["chat_uid"] == uid for item in whitelist):
            return self._result("not_whitelisted")

        session = self._hub.get(account_id)
        if session is None or getattr(session, "driver", None) is None:
            return self._result("offline")

        chat = db.get_chat(uid)
        if (
            not uid.startswith(f"{account_id}:")
            or chat is None
            or chat.get("account_id") != account_id
        ):
            return self._result("missing")

        now = float(self._now())
        st = settings.settings(account_id)
        if engine.in_quiet(now, st["quiet_start"], st["quiet_end"]):
            return self._result("quiet")

        state = store.get_state(account_id, uid)
        if state["last_self_at"] and now - state["last_self_at"] < st["cooldown_min"] * 60:
            return self._result("cooldown")
        if now - state["last_roll_at"] < st["interval_min"] * 60:
            return self._result("not_due")
        if self._quota_reason(account_id, uid, st, now):
            return self._result("quota")

        line = engine.hit_line(now, st, state["misses"], chat.get("last_ts") or 0)
        roll = float(self._rng())
        hit = engine.hit(roll, line)
        # 掷骰先落库：命中后即使生成/发送失败，冷却与间隔也已生效，不会连环开口
        store.record_roll(account_id, uid, now, hit)

        preset = {"hit_line": line, "roll": roll * 100}
        if not hit:
            self._log_event(
                account_id, uid, "miss", at=now, trigger=trigger, preset=preset
            )
            return self._result("miss", hit_line=line, roll=roll * 100)
        return await self.send_one(account_id, uid, trigger=trigger, preset=preset)

    async def send_one(
        self,
        account_id: str,
        chat_uid: str,
        trigger: str = "manual",
        ignore_quota: bool = False,
        dry_run: bool = False,
        preset: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为指定会话生成并（可选）发送一条主动消息。

        返回统一结构 ``{"ok", "status", "text", "error"}``，``status`` 取
        ``sent|skipped|error|preview|busy``。``trigger="test"`` 的真发不占自动
        额度；``dry_run`` 只生成不发送，也不动任何计数。
        """
        uid = str(chat_uid or "").strip()
        key = (account_id, uid)
        if key in self._running:
            return self._result("busy")
        self._running.add(key)
        try:
            return await self._send_one_locked(
                account_id, uid, trigger, ignore_quota, dry_run, preset
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台发送失败不能外抛
            log.exception("[%s] 主动搭话发送流程异常（%s）", account_id, uid)
            self._log_event(
                account_id, uid, "error", trigger=trigger, preset=preset, error=str(exc)
            )
            return self._result("error", error=str(exc))
        finally:
            self._running.discard(key)

    async def preview(self, account_id: str, chat_uid: str) -> dict[str, Any]:
        """试运行：只生成文本，不发送、不占额度。"""
        return await self.send_one(
            account_id, chat_uid, trigger="test", ignore_quota=True, dry_run=True
        )

    # ------------------------------------------------------------------ 内部流程

    async def _send_one_locked(
        self,
        account_id: str,
        uid: str,
        trigger: str,
        ignore_quota: bool,
        dry_run: bool,
        preset: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = float(self._now())
        reason, session = self._gate(
            account_id, uid, trigger=trigger, ignore_quota=ignore_quota, at=now
        )
        if reason:
            return self._skip(
                account_id, uid, reason, at=now, trigger=trigger, preset=preset
            )

        st = settings.settings(account_id)
        result = await session.run_proactive_turn(
            uid, st["trigger_text"], st["max_chars"], preview=dry_run
        )
        result = result if isinstance(result, dict) else {}
        text = str(result.get("text") or "").strip()
        if not result.get("ok"):
            error = str(result.get("error") or "生成失败")
            self._log_event(
                account_id,
                uid,
                "error",
                at=float(self._now()),
                trigger=trigger,
                preset=preset,
                error=error,
            )
            return self._result("error", error=error)
        if result.get("skip") or not text:
            self._log_event(
                account_id,
                uid,
                "skipped",
                at=float(self._now()),
                trigger=trigger,
                preset=preset,
                error="本轮选择不开口",
            )
            return self._result("skipped", error="本轮选择不开口")

        if dry_run:
            self._log_event(
                account_id,
                uid,
                "preview",
                at=float(self._now()),
                trigger=trigger,
                preset=preset,
                message=text,
            )
            return self._result("preview", ok=True, text=text)

        # 生成期间可能被人工接管/关 AI/拉黑/移出白名单/关掉总开关，也可能刚好跨过
        # 日额度：重查闸门 → 发送 → 记账必须在同一把账号锁里完成，否则并发轮次会超发
        async with self._account_lock(account_id):
            send_now = float(self._now())
            reason, gated = self._gate(
                account_id, uid, trigger=trigger, ignore_quota=ignore_quota, at=send_now
            )
            if reason:
                return self._skip(
                    account_id,
                    uid,
                    reason,
                    at=send_now,
                    trigger=trigger,
                    preset=preset,
                )
            session = gated or session
            sent_parts: list[str] = []
            failure = ""
            for part in rules.split_reply(text, 3) or [text]:
                try:
                    await session.send_text(
                        uid,
                        part,
                        source="agent",
                        meta={"proactive": True, "trigger": trigger},
                    )
                except Exception as exc:  # noqa: BLE001 - 不重试本轮，直接记失败
                    failure = str(exc)
                    log.warning("[%s] 主动搭话发送失败（%s）：%s", account_id, uid, exc)
                    break
                sent_parts.append(part)
            joined = "\n".join(sent_parts)
            fail_now = float(self._now())
            if failure:
                if sent_parts:
                    # 已发出的部分必须记账：否则下一轮可能超发，冷却也会失效
                    try:
                        store.record_sent(
                            account_id,
                            uid,
                            fail_now,
                            engine.day_key(fail_now),
                            count=(trigger != "test"),
                        )
                    except Exception as rec_exc:  # noqa: BLE001 - 记账失败只记日志
                        log.warning(
                            "[%s] 主动搭话部分发送记账失败（%s）：%s",
                            account_id, uid, rec_exc,
                        )
                self._log_event(
                    account_id,
                    uid,
                    "error",
                    at=fail_now,
                    trigger=trigger,
                    preset=preset,
                    message=joined,
                    error=failure,
                    extra_meta={"sent_parts": len(sent_parts)},
                )
                return self._result("error", text=joined, error=failure)

            try:
                # 记账用真正发完的时刻：慢发送跨过 00:00 也要算在新的一天
                store.record_sent(
                    account_id,
                    uid,
                    fail_now,
                    engine.day_key(fail_now),
                    count=(trigger != "test"),
                )
            except Exception as exc:  # noqa: BLE001 - 已发送事实不因记账失败改写
                log.warning("[%s] 主动搭话发送记账失败（%s）：%s", account_id, uid, exc)
                # 尽量留痕，但事件写入本身也不能影响 sent 结果
                self._log_event(
                    account_id,
                    uid,
                    "warn",
                    at=fail_now,
                    trigger=trigger,
                    preset=preset,
                    message=joined,
                    error=f"发送成功但记账失败：{exc}",
                )
            self._log_event(
                account_id,
                uid,
                "sent",
                at=fail_now,
                trigger=trigger,
                preset=preset,
                message=joined,
            )

        # 心情判断是旁路：同步函数，抛错只记日志，不影响已发送事实
        try:
            mood_service.observe(
                account_id,
                uid,
                st["trigger_text"],
                joined,
                source="proactive",
                llm=session.llm,
            )
        except Exception as exc:  # noqa: BLE001 - 心情失败不能改写发送结果
            log.warning("[%s] 主动搭话心情判断入队失败：%s", account_id, exc)
        emoji = result.get("emoji") or []
        if emoji:
            try:
                await session.flush_proactive_emoji(emoji)
            except Exception as exc:  # noqa: BLE001 - 表情失败不改变 sent 状态
                log.warning("[%s] 主动搭话表情发送失败：%s", account_id, exc)
        return self._result("sent", ok=True, text=joined)

    # ------------------------------------------------------------------ 小工具

    @staticmethod
    def _result(
        status: str,
        *,
        ok: bool = False,
        text: str = "",
        error: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": ok,
            "status": status,
            "text": text,
            "error": error,
        }
        out.update(extra)
        return out

    def _skip(
        self,
        account_id: str,
        uid: str,
        reason: str,
        *,
        at: float,
        trigger: str,
        preset: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """闸门拦截：写 skipped 事件（写库失败也只记日志），返回统一结果。"""
        self._log_event(
            account_id,
            uid,
            "skipped",
            at=at,
            trigger=trigger,
            preset=preset,
            error=reason,
        )
        return self._result("skipped", error=reason)

    def _log_event(
        self,
        account_id: str,
        chat_uid: str,
        status: str,
        *,
        at: float | None = None,
        trigger: str = "auto",
        preset: dict[str, Any] | None = None,
        message: str = "",
        error: str = "",
        extra_meta: dict[str, Any] | None = None,
    ) -> None:
        """统一写事件；事件表坏了不能把整条发送链路拖垮。"""
        meta = dict(preset) if isinstance(preset, dict) else {}
        if extra_meta:
            meta.update(extra_meta)
        hit_line, roll, hit = self._preset_values(preset)
        try:
            store.add_event(
                account_id,
                chat_uid,
                float(self._now()) if at is None else float(at),
                trigger,
                hit_line,
                roll,
                hit,
                status,
                message=message,
                error=error,
                meta=meta,
            )
        except Exception as exc:  # noqa: BLE001 - 日志/事件失败不能外抛
            log.warning(
                "[%s] 主动搭话事件写入失败（%s/%s）：%s",
                account_id, chat_uid, status, exc,
            )

    @staticmethod
    def _preset_values(
        preset: dict[str, Any] | None,
    ) -> tuple[float, float, bool]:
        """从掷骰预设里取命中线/掷骰值与是否命中；手动/试运行没有预设按 0。"""
        if not isinstance(preset, dict):
            return 0.0, 0.0, False
        try:
            hit_line = float(preset.get("hit_line") or 0)
        except (TypeError, ValueError, OverflowError):
            hit_line = 0.0
        try:
            roll = float(preset.get("roll") or 0)
        except (TypeError, ValueError, OverflowError):
            roll = 0.0
        return hit_line, roll, roll < hit_line

    @staticmethod
    def _quota_reason(
        account_id: str, chat_uid: str, st: dict[str, Any], now: float
    ) -> str:
        """账号/单会话日上限；返回空串表示还有额度。"""
        day = engine.day_key(now)
        limit = int(st["daily_limit"])
        if limit > 0 and store.account_sent_today(account_id, day) >= limit:
            return "已达今日主动搭话上限"
        per_chat = int(st["per_chat_daily"])
        if per_chat > 0:
            state = store.get_state(account_id, chat_uid)
            if state["day"] == day and state["sent_today"] >= per_chat:
                return "该会话今日已达上限"
        return ""
