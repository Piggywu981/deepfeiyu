"""成长文本的规范化、过滤与证据核验。

原则：聊天是证据不是命令；任何写入成长层的内容都必须先过这里。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .. import db

MAX_TRAIT_CHARS = 200
MAX_JOURNAL_CHARS = 1000
MAX_SUMMARY_CHARS = 2000

_SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9]{16,}|api[_ -]?key\s*[:=]\s*\S+"
    r"|bearer\s+[a-z0-9._-]{16,}|-----begin [a-z ]*private key-----)"
)
_RED_FLAGS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"https?://|www\.", re.I), "包含链接"),
    (re.compile(r"```|~~~"), "包含代码块"),
    (
        re.compile(
            r"(?i)\b(ignore (all )?previous|system prompt|developer message|private key)\b"
        ),
        "包含提示词注入/密钥字样",
    ),
    (
        re.compile(r"忽略(之前|以上|前面|所有)?指令|系统提示|开发者消息|密钥|密码|口令"),
        "包含提示词注入/密钥字样",
    ),
]
_IMPERATIVE_RE = re.compile(r"^(你必须|你要|请忽略|以后必须|立刻|马上|不要告诉|从现在起)")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def clean_single_line(text: str) -> str:
    """单行化：控制字符变空格，再压空白。"""
    return normalize_text(re.sub(r"[\x00-\x1f\x7f]+", " ", text or ""))


def _clean_block(text: str) -> str:
    """保留段落的多行清理，用于日记/总览。"""
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "").strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean


def content_hash(kind: str, scope: str, chat_uid: str, content: str) -> str:
    raw = "\n".join(
        [
            normalize_text(kind),
            normalize_text(scope),
            normalize_text(chat_uid),
            normalize_text(content),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def scan_secret(text: str) -> bool:
    return bool(_SECRET_RE.search(text or ""))


def find_red_flag(text: str) -> str:
    for pattern, reason in _RED_FLAGS:
        if pattern.search(text or ""):
            return reason
    return ""


def sanitize_trait(text: str) -> tuple[str, str]:
    clean = clean_single_line(text)
    if not clean:
        return "", "内容为空"
    if len(clean) > MAX_TRAIT_CHARS:
        return "", f"超过 {MAX_TRAIT_CHARS} 字"
    if scan_secret(clean):
        return "", "包含密钥"
    reason = find_red_flag(clean)
    if reason:
        return "", reason
    if _IMPERATIVE_RE.match(clean):
        return "", "是命令句，不是自我陈述"
    return clean, ""


def sanitize_journal(text: str) -> tuple[str, str]:
    clean = _clean_block(text)
    if not clean:
        return "", "内容为空"
    reason = find_red_flag(clean)
    if reason:
        return "", reason
    return clean[:MAX_JOURNAL_CHARS], ""


def sanitize_summary(text: str) -> tuple[str, str]:
    clean = _clean_block(text)
    if not clean:
        return "", "内容为空"
    reason = find_red_flag(clean)
    if reason:
        return "", reason
    if len(clean) > MAX_SUMMARY_CHARS:
        return "", f"超过 {MAX_SUMMARY_CHARS} 字"
    return clean, ""


def validate_evidence(account_id: str, evidence: Any) -> tuple[list[dict[str, Any]], str]:
    """只接受能在本账号消息/控制台对话里找到原文子串的证据。"""
    if not isinstance(evidence, list):
        return [], "evidence 必须是数组"
    valid: list[dict[str, Any]] = []
    with db.transaction() as conn:
        for item in evidence[:5]:
            if not isinstance(item, dict):
                continue
            quote = normalize_text(str(item.get("quote") or ""))
            if not quote:
                continue
            typ = str(item.get("type") or "message").strip()
            raw_ids = item.get("msg_ids") if typ == "message" else item.get("turn_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                continue
            if typ == "message":
                ids = [str(x) for x in raw_ids if str(x).strip()][:20]
                if not ids:
                    continue
                marks = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT id, content FROM messages WHERE account_id=? AND id IN ({marks})",
                    (account_id, *ids),
                ).fetchall()
                found = {str(r["id"]): str(r["content"] or "") for r in rows}
            elif typ == "console":
                ids_int: list[int] = []
                for x in raw_ids[:20]:
                    try:
                        ids_int.append(int(x))
                    except (TypeError, ValueError):
                        continue
                if not ids_int:
                    continue
                marks = ",".join("?" for _ in ids_int)
                rows = conn.execute(
                    f"""SELECT t.id, t.content FROM agent_turns t
                        JOIN agent_conversations c ON c.id=t.conversation_id
                        WHERE c.account_id=? AND t.id IN ({marks})""",
                    (account_id, *ids_int),
                ).fetchall()
                found = {str(r["id"]): str(r["content"] or "") for r in rows}
            else:
                continue
            if any(str(i) not in found for i in (ids if typ == "message" else ids_int)):
                continue
            if not any(
                quote in normalize_text(found[str(i)])
                for i in (ids if typ == "message" else ids_int)
            ):
                continue
            valid.append(
                {
                    "type": typ,
                    "chat": str(item.get("chat") or "")[:80],
                    "chat_uid": str(item.get("chat_uid") or "")[:200],
                    "msg_ids": ids if typ == "message" else [],
                    "turn_ids": ids_int if typ == "console" else [],
                    "quote": quote[:80],
                }
            )
    if not valid:
        return [], "没有可核验的证据"
    return valid, ""
