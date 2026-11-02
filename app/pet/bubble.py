"""气泡文案：状态词 + 会话名 + 心情语气。

桌宠是常驻桌面窗口，泄露面比控制台页面大：只允许状态词、会话名（可关）
与短摘要，正文原文一律不进气泡。

mood 入参是 `moodlink.snapshot()` 的归一形状 `{"base": float, "dims": dict[str, float],
"confidence": float}`。不要直接塞 `mood.service.state_payload()`：那个返回的 `dims`
是 `[{key, label, value}]` 列表，而且每次调用都顺带算 30 条 timeline。
"""
from __future__ import annotations

from typing import Any

from ..growth import sanitize
from ..mood import constants as mood_constants
from . import protocol

HELPER_STATES = protocol.STATES
MOOD_CONFIDENCE_GATE = 60
CHAT_NAME_MAX = 24
DIGEST_MAX = 20
PULSE_TTL_DEFAULT = 2000

STATE_COPY: dict[str, tuple[str, str]] = {
    "IDLE": ("待着", "没消息的时候我自己待会儿"),
    "THINKING": ("在想事情", "正在组织回复"),
    "WORKING": ("在忙手头的活", "正在调工具"),
    "WAITING": ("等你点头", "有事需要你确认"),
    "SUCCESS": ("这条回出去了", "本轮完成"),
    "ERROR": ("出岔子了", "刚才那条没发出去"),
    "DISCONNECTED": ("微信没连上", "通道断了，我先待着"),
}
# 只有这几个状态允许带情绪措辞；IDLE/DISCONNECTED 不带，免得宠物自说自话
_MOODY = ("THINKING", "WORKING", "SUCCESS", "ERROR")


def _clean(value: str, limit: int) -> str:
    return sanitize.clean_single_line(sanitize.normalize_text(value or ""))[:limit]


def _leading_dim(mood: dict[str, Any] | None) -> str | None:
    if not mood or float(mood.get("confidence") or 0) < MOOD_CONFIDENCE_GATE:
        return None
    dims = mood.get("dims") or {}
    if not dims:
        return None
    name, value = max(dims.items(), key=lambda item: float(item[1] or 0))
    # 措辞取 mood/constants.DIM_SHORT（心情移植时已有的表），桌宠不另造第二份形容
    if float(value) < 40 or name not in mood_constants.DIM_SHORT:
        return None
    return mood_constants.DIM_SHORT[name]


def decorate(state: str, *, mood: dict[str, Any] | None, pet_cfg: dict[str, Any],
             chat_name: str = "", digest: str = "",
             source_label: str = "") -> tuple[str, str]:
    message, detail = STATE_COPY.get(state, STATE_COPY["IDLE"])
    flavour = _leading_dim(mood) if state in _MOODY else None
    if flavour:
        message = f"{message}，{flavour}"
    parts = [detail]
    if source_label:
        parts.append(_clean(source_label, CHAT_NAME_MAX))
    if pet_cfg.get("showChatName") and chat_name:
        parts.append(_clean(chat_name, CHAT_NAME_MAX))
    # scan_secret 只拦凭证样式；正文原文永远不进这里，摘要另受 20 字截断约束
    if pet_cfg.get("showChatDigest") and digest and not sanitize.scan_secret(digest):
        parts.append(_clean(digest, DIGEST_MAX))
    return message, " · ".join(part for part in parts if part)


def message_for(state: str, *, mood: dict[str, Any] | None, pet_cfg: dict[str, Any],
                chat_name: str = "", digest: str = "", kind: str = "state",
                source_label: str = "", **extra: Any) -> dict[str, Any]:
    message, detail = decorate(state, mood=mood, pet_cfg=pet_cfg,
                               chat_name=chat_name, digest=digest,
                               source_label=source_label)
    out: dict[str, Any] = {"kind": kind, "state": state, "message": message, "detail": detail}
    out.update(extra)
    return out
