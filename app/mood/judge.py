"""心情判断：调一次小模型，拿 JSON；任何失败都返回 None，不影响回复。"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.agent import Agent
from . import prompt
from .model import parse_delta

log = logging.getLogger("mood")

SOURCE_MODE = {"proactive": "talk", "bili-patrol": "work"}


async def judge(llm: Any, cfg: dict[str, Any], user_text: str, reply: str,
                mode: str = "") -> dict[str, Any] | None:
    if llm is None or not getattr(llm, "configured", False):
        return None
    self_name = cfg.get("self") or "伙伴"
    owner = cfg.get("owner") or "对方"
    sys_text = prompt.system(self_name, owner, cfg.get("aliases") or "")
    sys_text += prompt.mode_supplement(mode, owner)
    user = prompt.user_message(mode, user_text, reply, owner, cfg.get("source") or "")
    messages = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": user},
    ]
    try:
        result = await Agent(llm, [], max_rounds=1).run(
            messages,
            temperature=0.2,
            max_tokens=1000,
            model=cfg.get("judge_model") or None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("心情判断调用失败：%s", exc)
        return None
    text = str((result or {}).get("text") or "")
    delta = parse_delta(text, owner)
    if delta is None:
        log.info("心情判断输出无法解析：%s", text[:120].replace("\n", " "))
        return None
    log.info("心情判断：moodDelta=%s conf=%s reason=%s",
             delta["moodDelta"], delta["confidence"], delta["reason"][:80])
    return delta
