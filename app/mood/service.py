"""心情服务：判断队列、衰减、注入、状态与槽位封装。"""
from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from datetime import datetime
from typing import Any

from .. import config
from . import judge as judge_mod, model, settings, store
from .constants import DIM_KEYS, DIM_LABELS

log = logging.getLogger("mood")

MAX_QUEUE = 3
# 系统/时间/运行时注入不是「对话」，不能当成号主说的话去判断心情。
_SKIP_SOURCES = {
    "system", "runtime-context", "time-context", "system-reminder",
    "time-sampled", "tool", "subagent", "schedule",
}
_ALLOWED_SOURCES = {"user", "console", "proactive", "bili-patrol"}
_pending: dict[tuple[str, str], int] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()
# 同一槽位的「读状态 → 改 → 写库」必须串行：事件循环里的判断、线程池里的
# 注入/重置/挂饰轮询都可能同时读写同一行，跨线程用可重入锁保护。
# 锁顺序固定为 state_lock → db 写锁，不要在 db 锁内再取 state_lock。
_state_locks: dict[tuple[str, str], threading.RLock] = {}


def state_lock(account_id: str, slot_id: str) -> threading.RLock:
    return _state_locks.setdefault((account_id, slot_id), threading.RLock())


def reset_runtime_for_tests() -> None:
    for task in list(_tasks):
        task.cancel()
    _tasks.clear()
    _pending.clear()
    _locks.clear()


async def wait_idle() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def observe(account_id: str, chat_uid: str, user_text: str, reply: str,
            source: str = "user", llm: Any = None) -> str:
    # 入队只是回复链路的旁路：配置读取、槽位解析、建任务任何一步失败都不能外抛。
    try:
        if source in _SKIP_SOURCES:
            log.debug("非对话来源（%s），跳过心情判断", source)
            return "skipped"
        if source not in _ALLOWED_SOURCES:
            log.debug("未知心情来源（%s），按 user 口径判断", source)
        st = settings.settings(account_id)
        if not settings.enabled(account_id) or not st["auto_judge"]:
            return "skipped"
        if not str(user_text or "").strip() and not str(reply or "").strip():
            return "skipped"
        slot_id = settings.resolve_slot(account_id, chat_uid)
        key = (account_id, slot_id)
        if _pending.get(key, 0) >= MAX_QUEUE:
            log.info("心情判断排队已满（%s/%s），丢弃这一轮", account_id, slot_id)
            return "dropped"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有事件循环属于正常跳过，不能落到外层兜底记成异常。
            log.debug("没有事件循环，跳过心情判断")
            return "skipped"
        # 必须在创建 task 前同步计数：同一轮里连续 observe 时，队列上限才准确。
        _pending[key] = _pending.get(key, 0) + 1
        task = loop.create_task(
            _run(account_id, chat_uid, slot_id, user_text, reply, source, llm, st)
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return "queued"
    except Exception:  # noqa: BLE001 - 心情失败不能影响整轮回复
        log.exception("心情判断入队失败")
        return "skipped"


def apply_judged_delta(
    account_id: str,
    slot_id: str,
    delta: dict[str, Any],
    *,
    source: str = "dsh",
    reason_limit: int = 60,
    chat_uid: str = "",
    st: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """把已经判断好的 delta 应用到槽位并落事件。

    微信判断与 DSH 回写共用这一套：显著阈值、minor、注入额度状态、衰减、事件格式都相同；
    唯一区别是 source 与 reason 截断（跨端理由只保留短句）。
    """
    st = st or settings.settings(account_id)
    reason = str(delta.get("reason") or "")[: max(1, int(reason_limit))]
    delta = {**delta, "reason": reason}
    step = model.mood_step(delta["moodDelta"])
    any_dim = any(delta["dims"][key] != 0 for key in DIM_KEYS)
    if step == 0 and not any_dim:
        return {"applied": False, "skipped": "no-change"}
    with state_lock(account_id, slot_id):
        state = store.load_state(account_id, slot_id, base=st["base"])
        _decay_note(account_id, slot_id, state, st, time.time())
        sig = model.significant(delta, st["sig_mood"], st["sig_dim"])
        at = time.time() if now is None else float(now)
        model.apply_delta(state, delta, st["floor"], at)
        state["minor"] = 0 if sig else 1
        state["last_chat_uid"] = chat_uid
        store.save_state_with_event(
            account_id, slot_id, state,
            event={
                "at": at,
                "mood": state["mood"],
                "delta": model.mood_step(delta["moodDelta"]),
                "dims": state["dims"],
                "dims_delta": delta["dims"],
                "reason": reason,
                "confidence": delta["confidence"],
                "kind": "judge",
                "source": source,
                "minor": state["minor"],
            },
        )
    return {
        "applied": True,
        "skipped": "",
        "mood": state["mood"],
        "dims": state["dims"],
        "confidence": state["last_confidence"],
        "minor": state["minor"],
        "significant": sig,
        "event": {"at": at, "source": source, "reason": reason},
    }


async def _run(account_id: str, chat_uid: str, slot_id: str, user_text: str,
               reply: str, source: str, llm: Any, st: dict[str, Any]) -> None:
    key = (account_id, slot_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            self_name, owner, aliases = settings.names(
                account_id, slot_id, chat_name=_chat_name(chat_uid)
            )
            mode = judge_mod.SOURCE_MODE.get(source, "")
            delta = await judge_mod.judge(
                llm,
                {"self": self_name, "owner": owner, "aliases": aliases,
                 "judge_model": st["judge_model"], "source": source},
                user_text, reply, mode,
            )
            if not delta:
                return
            result = apply_judged_delta(
                account_id, slot_id, delta, source=source, chat_uid=chat_uid, st=st,
            )
            if not result["applied"]:
                log.info("这一轮判定无变化，不记入时间线（%s）", source)
                return
            log.info("心情更新（%s）：mood=%.1f minor=%s reason=%s",
                     source, result["mood"], result["minor"],
                     result["event"]["reason"][:80])
    except Exception:  # noqa: BLE001
        log.exception("心情判断失败（%s/%s）", account_id, slot_id)
    finally:
        _pending[key] = max(0, _pending.get(key, 0) - 1)


def _chat_name(chat_uid: str) -> str:
    if not chat_uid:
        return ""
    from .. import db
    row = db.get_chat(chat_uid)
    return str((row or {}).get("name") or "")


def _decay_note(account_id: str, slot_id: str, state: dict[str, Any],
                st: dict[str, Any], now: float) -> None:
    event = model.decay(state, st["base"], now, st["decay_half_hours"])
    if not event:
        return
    store.save_state_with_event(
        account_id, slot_id, state,
        event={
            "at": event["at"],
            "mood": state["mood"],
            "delta": 0,
            "dims": state["dims"],
            "dims_delta": {},
            "reason": event["reason"],
            "confidence": event["confidence"],
            "kind": "decay",
            "source": "decay",
            "minor": 0,
        },
    )


def inject_block(account_id: str, chat_uid: str = "", chat_name: str = "",
                 is_group: bool = False) -> str:
    """返回注入到系统提示里的心情块；一次变化只说一次，说完/跳过都落库。

    注入只是锦上添花：数据库忙、落库 JSON 坏、设置异常都不能拖垮整轮回复，
    所以整个函数兜底，失败时记日志并返回空块。

    event 文案带具体来历，只在「同一个人/同一个私聊」或控制台讲；群聊和别的
    会话一律降级成 label（只说心情），避免「号主说没吃饭」漏进群或另一个私聊。
    decay 文案只讲时间流逝，没有来历，群聊里也照常可用。
    """
    try:
        if not settings.enabled(account_id):
            return ""
        st = settings.settings(account_id)
        if st["inject_mode"] == "off":
            return ""
        slot_id = settings.resolve_slot(account_id, chat_uid)
        # 读改写整段同步执行（无 await），与 _run / post_reset 串行。
        with state_lock(account_id, slot_id):
            state = store.load_state(account_id, slot_id, base=st["base"])
            _decay_note(account_id, slot_id, state, st, time.time())
            if float(state.get("updated_at") or 0) <= 0:
                return ""
            if float(state.get("injected_for") or 0) == float(state["updated_at"]):
                return ""
            if int(state.get("minor") or 0):
                _mark(account_id, slot_id, state)
                return ""
            if int(state.get("last_confidence") or 0) < int(st["inject_confidence"]):
                log.info("心情可信度 %s 低于门槛 %s，这次不告诉她",
                         state.get("last_confidence"), st["inject_confidence"])
                _mark(account_id, slot_id, state)
                return ""
            if _today_count(state) >= int(st["inject_per_day"]):
                log.info("心情日额度用完（%s/%s），这次只记账",
                         _today_count(state), st["inject_per_day"])
                _mark(account_id, slot_id, state)
                return ""
            _, owner, _ = settings.names(account_id, slot_id, chat_name=chat_name)
            mode = st["inject_mode"]
            if mode == "event" and state.get("kind") != "decay":
                last = store.last_event(account_id, slot_id) or {}
                is_dsh = last.get("source") == "dsh"
                cross_dsh = is_dsh and bool(
                    (config.load().get("dsh") or {}).get("cross_reason_enabled", True)
                )
                other_chat = bool(
                    chat_uid
                    and state.get("last_chat_uid")
                    and chat_uid != state.get("last_chat_uid")
                )
                if is_group:
                    mode = "label"          # 群聊永不带来历，跨端也一样
                elif is_dsh:
                    mode = "event" if cross_dsh else "label"
                elif other_chat:
                    mode = "label"
            line = model.mood_line(state, mode, owner)
            if not line:
                _mark(account_id, slot_id, state)
                return ""
            now = time.time()
            state["injected_for"] = state["updated_at"]
            state["last_injected_at"] = now
            state["last_injected_text"] = line
            state["said_at"] = (list(state.get("said_at") or []) + [now])[-200:]
            store.save_state(account_id, slot_id, state)
            store.mark_injected(account_id, slot_id, state["updated_at"])
            log.info("告诉她心情（%s）：%s", mode, line.replace("\n", " ")[:100])
            return line
    except Exception:  # noqa: BLE001 - 注入失败不能影响整轮回复
        log.exception("心情注入失败，本轮不注入")
        return ""


def _mark(account_id: str, slot_id: str, state: dict[str, Any]) -> None:
    """记下这次变化已被处理，避免每轮重复判断。"""
    state["injected_for"] = state["updated_at"]
    store.save_state(account_id, slot_id, state)


def state_payload(account_id: str, chat_uid: str = "") -> dict[str, Any]:
    """挂饰轮询用的只读快照：在副本上算衰减，不写库、不记事件、不建槽。"""
    st = settings.settings(account_id)
    slot_id = settings.resolve_slot(account_id, chat_uid)
    state = store.get_slot(account_id, slot_id)
    state = copy.deepcopy(state) if state is not None else store.blank_state(st["base"])
    model.decay(state, st["base"], time.time(), st["decay_half_hours"])
    dims = [
        {"key": k, "label": DIM_LABELS[k], "value": int(round(state["dims"][k]))}
        for k in DIM_KEYS
    ]
    _, owner, _ = settings.names(account_id, slot_id, chat_name=_chat_name(chat_uid))
    return {
        "ok": True,
        "enabled": settings.enabled(account_id),
        "settings": st,
        "slots": [
            {"slot_id": s["slot_id"], "label": s["label"], "mood": round(s["mood"], 1)}
            for s in store.list_slots(account_id)
        ],
        "bindings": store.list_bindings(account_id),
        "account_id": account_id,
        "slot_id": slot_id,
        "mood": int(round(state["mood"])),
        "moodValue": round(state["mood"], 1),
        "moodLabel": model.label_of(state["mood"]),
        "face": model.face_of(state["mood"]),
        "dims": dims,
        "lastReason": state["last_reason"],
        "lastConfidence": state["last_confidence"],
        "kind": state["kind"],
        "minor": bool(state["minor"]),
        "lastSource": (store.last_event(account_id, slot_id) or {}).get("source", ""),
        "updatedAt": state["updated_at"] * 1000,
        "injectedAt": state["last_injected_at"] * 1000,
        "injectedText": state["last_injected_text"],
        "injectMode": st["inject_mode"],
        "injectPerDay": st["inject_per_day"],
        "todayInjected": _today_count(state),
        "preview": model.mood_line(state, st["inject_mode"], owner),
        "timeline": timeline_payload(account_id, chat_uid, limit=30),
    }


def timeline_payload(account_id: str, chat_uid: str = "", limit: int = 50,
                     before: float = 0.0) -> list[dict[str, Any]]:
    slot_id = settings.resolve_slot(account_id, chat_uid)
    rows = store.list_events(account_id, slot_id, limit=limit, before=before)
    return [
        {
            "id": r["id"],
            "at": r["at"] * 1000,
            "mood": int(round(r["mood"])),
            "delta": r["delta"],
            "reason": r["reason"],
            "conf": r["confidence"],
            "kind": r["kind"],
            "dimsDelta": r["dims_delta"],
        }
        for r in rows
    ]


def _today_start() -> float:
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _today_count(state: dict[str, Any]) -> int:
    start = _today_start()
    return sum(1 for t in state.get("said_at") or [] if float(t) >= start)
