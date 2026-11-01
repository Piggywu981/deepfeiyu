"""自动回复策略：所有拦截原因都返回可读文案，便于前端解释“为什么不回”。"""
from __future__ import annotations

import math
import re
import time
from typing import Any

from . import db
from .wechat.base import (
    Message,
    format_pat_notice,
    is_internal_chat,
    parse_pat_notice,
    parse_refermsg,
)


def _in_hours(cfg: dict[str, Any]) -> bool:
    wh = cfg["auto"].get("working_hours") or {}
    if not wh.get("enabled"):
        return True
    try:
        now = time.strftime("%H:%M")
        start, end = wh.get("start", "00:00"), wh.get("end", "23:59")
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end
    except Exception:
        return True


def mentioned(content: str, persona: str) -> bool:
    if not content:
        return False
    if persona and (f"@{persona}" in content or f"@{persona}\u2005" in content):
        return True
    return "@我" in content


def strip_mention(content: str, persona: str) -> str:
    text = content or ""
    if persona:
        text = text.replace(f"@{persona}\u2005", "").replace(f"@{persona}", "")
    text = text.replace("@我", "")
    return text.strip()


def pat_meta(msg: Message) -> dict[str, Any]:
    """拍一拍的结构化信息；驱动没带 meta 时从文案兜底解析。"""
    info = (msg.raw or {}).get("pat")
    if isinstance(info, dict) and info:
        return info
    parsed = parse_pat_notice(msg.content)
    if not parsed:
        return {}
    return format_pat_notice(parsed)[1]


def _policy_list(value: Any) -> list[str]:
    """黑/白名单配置：只接受字符串列表。

    手改 config.json 或旧版本可能写出 null、单个字符串、混入 null；之前直接迭代
    会在 null 上抛 TypeError，把「是否回消息」这种判断整个打崩。坏结构按空名单处理。
    """
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, str) and x]


def _pause_until(chat: dict[str, Any]) -> float:
    """pause_until 可能来自 JSON/表单，字符串或 null 都不能让策略判断崩掉。

    NaN/±inf 不是合法时间戳：按 0 处理，避免一条坏配置把会话永久停摆。
    """
    try:
        value = float(chat.get("pause_until") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def check(
    cfg: dict[str, Any], account: dict[str, Any] | None, chat: dict[str, Any] | None, msg: Message
) -> tuple[bool, str]:
    auto = cfg["auto"]
    persona = (account or {}).get("nickname") or cfg["ai"].get("persona_name", "")
    if not auto.get("enabled"):
        return False, "全局自动回复已关闭"
    if msg.is_self:
        return False, "自己发出的消息"
    if msg.msg_type == "image":
        if not cfg["ai"].get("vision_enabled"):
            return False, "图片消息：识图未开启"
    elif msg.msg_type == "emoji":
        if not auto.get("emoji_reply", True):
            return False, "表情消息：表情自动回复未开启"
    elif msg.msg_type == "pat":
        if not auto.get("pat_reply", True):
            return False, "拍一拍消息：自动回复未开启"
    elif msg.msg_type == "link":
        # 合并转发的聊天记录是 link 类型（带 <recorditem> 结构），放行让 AI 能读到
        # 解析出的对话内容；引用消息（appmsg type=57，带 <refermsg>）入库正文就是
        # 「回复文本 + [引用 …]」，群友引用一句来问话不能按广告拦。其余链接/文件/
        # 卡片维持拦截，避免广告推送触发回复
        content = msg.content or ""
        if (
            "聊天记录" not in content
            and "<recorditem" not in content
            and parse_refermsg(content) is None
        ):
            return False, "非文本消息（link）"
    elif msg.msg_type != "text":
        return False, f"非文本消息（{msg.msg_type}）"
    if msg.msg_type == "text" and not (msg.content or "").strip():
        return False, "空消息"
    if chat is None:
        return False, "会话不存在"
    if not chat.get("ai_enabled", True):
        return False, "该会话已关闭 AI"
    if _pause_until(chat) > time.time():
        return False, "人工接管中，AI 暂停"

    ident = {str(x) for x in (msg.chat_id, msg.chat_name, chat.get("chat_id"), chat.get("name")) if x}
    wl = _policy_list(auto.get("whitelist"))
    if any(x in ident for x in _policy_list(auto.get("blacklist"))):
        return False, "命中黑名单"
    if auto.get("only_whitelist"):
        if wl and not any(x in ident for x in wl):
            return False, "不在白名单"

    if msg.is_group:
        if msg.msg_type == "pat" and not pat_meta(msg).get("to_self"):
            return False, "群里的拍一拍不是拍我"
        if auto.get("group_need_at", True) and msg.msg_type != "pat":
            if not mentioned(msg.content, persona):
                return False, "群聊未 @ 我"
    elif auto.get("only_whitelist") and not wl:
        return False, "白名单为空"

    if not _in_hours(cfg):
        return False, "不在工作时段"

    min_interval = float(auto.get("min_interval_sec", 0) or 0)
    if min_interval > 0:
        recent = db.list_messages(chat["uid"], limit=6)
        for m in reversed(recent):
            if m["is_self"] and m.get("source") == "agent":
                if time.time() - m["ts"] < min_interval:
                    return False, f"触发频率限制（{min_interval:.0f}s）"
                break

    limit = int(auto.get("daily_limit", 0) or 0)
    if limit > 0 and db.count_ai_replies_today((account or {}).get("id", "")) >= limit:
        return False, "已达今日回复上限"

    return True, "允许回复"


# 主动串门排除的「自己跟自己」会话：文件传输助手、几个内部聚合会话
_CROSS_VISIT_SKIP_IDS = frozenset({"filehelper", "brandsessionholder", "brandservicesessionholder"})


def check_cross_visit(
    cfg: dict[str, Any], account: dict[str, Any] | None, chat: dict[str, Any] | None, msg: Message
) -> tuple[bool, str]:
    """主动串门策略：只对「号主本人在私聊里刚发的文本消息」开放。

    是否已处于人工接管暂停由调用方用「这条消息之前」的状态判断（见 manager），
    这里不查 pause_until，避免刚设置的新暂停把本次判定挡掉。
    """
    cross = cfg.get("cross") or {}
    auto = cfg.get("auto") or {}
    if not cross.get("enabled"):
        return False, "跨对话沟通已关闭"
    if not cross.get("visit"):
        return False, "主动串门未开启"
    if not auto.get("enabled", True):
        return False, "全局自动回复已关闭"
    if not msg.is_self:
        return False, "不是号主发出的消息"
    if msg.msg_type != "text":
        return False, "只对文本消息考虑串门"
    if msg.is_group:
        return False, "群聊不主动串门"
    if str(msg.chat_id or "") in _CROSS_VISIT_SKIP_IDS or "文件传输助手" in str(msg.chat_name or ""):
        return False, "文件传输助手/内部会话不串门"
    if not (msg.content or "").strip():
        return False, "空消息"
    if chat is None:
        return False, "会话不存在"
    if not chat.get("ai_enabled", True):
        return False, "该会话已关闭 AI"
    ident = {str(x) for x in (msg.chat_id, msg.chat_name, chat.get("chat_id"), chat.get("name")) if x}
    wl = _policy_list(auto.get("whitelist"))
    if any(x in ident for x in _policy_list(auto.get("blacklist"))):
        return False, "命中黑名单"
    if auto.get("only_whitelist"):
        if wl and not any(x in ident for x in wl):
            return False, "不在白名单"
    if not _in_hours(cfg):
        return False, "不在工作时段"
    return True, "允许串门"


def check_proactive(
    cfg: dict[str, Any],
    account: dict[str, Any] | None,
    chat: dict[str, Any] | None,
    now: float | None = None,
) -> tuple[bool, str]:
    """主动搭话的发前闸门：会话状态、内部会话、人工接管、黑/白名单。

    主动搭话是独立开关，这里刻意不检查 ``auto.enabled`` 与工作时段（安静时段
    由调度层判断）；群聊也不在这一层拦，是否开口由白名单显式勾选决定。
    ``account`` 只为与其它规则保持同一签名，不参与判定。
    """
    if chat is None:
        return False, "会话不存在"
    if not chat.get("ai_enabled", True):
        return False, "该会话已关闭 AI"
    # 文件传输助手不在 INTERNAL_CHAT_IDS 里，但同样是自己跟自己的会话，
    # 主动搭话必须排除；品牌会话等交给 is_internal_chat 统一判断
    if is_internal_chat(chat.get("chat_id")) or str(chat.get("chat_id") or "") == "filehelper":
        return False, "内部会话不主动搭话"
    if _pause_until(chat) > (now if now is not None else time.time()):
        return False, "人工接管中，AI 暂停"

    auto = cfg.get("auto") or {}
    ident = {str(x) for x in (chat.get("chat_id"), chat.get("name")) if x}
    wl = _policy_list(auto.get("whitelist"))
    if any(x in ident for x in _policy_list(auto.get("blacklist"))):
        return False, "命中黑名单"
    if auto.get("only_whitelist"):
        # 与 check() 同一口径：只放行白名单开着时，名单为空就意味着谁都不许发
        if not wl:
            return False, "自动回复白名单为空"
        if not any(x in ident for x in wl):
            return False, "不在自动回复白名单"
    return True, "允许主动搭话"


# 形似密钥/令牌的内容一律打码：真实密钥不会进入模型上下文，这里防的是模型编造或转述出假凭据
_SECRET_RE = re.compile(
    r"\bsk-[A-Za-z0-9_-]{8,}"  # OpenAI/DeepSeek 风格
    r"|\b(?:xox[bpars]|gh[pous]|github_pat)[_-][A-Za-z0-9_-]{8,}"  # Slack/GitHub 风格
    r"|\b[A-Z][A-Z0-9_]*_(?:API_?KEY|TOKEN|SECRET)\s*[=：:]\s*\S+"  # FOO_API_KEY=xxx
)


def sanitize_reply(text: str, max_chars: int = 300) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[\w]*\n?|```$", "", text).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _SECRET_RE.sub("＊＊＊", text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


# 多条拆分：AI 用单独一行的 --- 分隔每条消息（见 _build_prompt 的拆分说明）；
# 没写分隔符但用了换行的，同样拆开——用户要求「分开发送，不是换行发送」
_SPLIT_RE = re.compile(r"(?m)^[ \t]*-{2,}[ \t]*$")


def split_reply(text: str, max_parts: int = 3) -> list[str]:
    """把 AI 的一次回复拆成多条独立微信消息。

    优先按单独一行的 ``---`` 分隔；没有分隔符（或分隔后仍有换行）时按行拆。
    用户要求「分开发送，不是换行发送」：任何一条消息里都不允许带换行，
    多行内容一律拆成多条；超过 max_parts 时多余部分用空格并入最后一条，不丢内容。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    for chunk in _SPLIT_RE.split(text):
        for line in chunk.split("\n"):
            line = line.strip()
            if line:
                chunks.append(line)
    if not chunks:
        return []
    if len(chunks) > max_parts:
        chunks = chunks[: max_parts - 1] + [" ".join(chunks[max_parts - 1 :])]
    # 兜底：一条消息里绝不留换行/连续空白
    return [re.sub(r"\s+", " ", c).strip() for c in chunks if c.strip()]
