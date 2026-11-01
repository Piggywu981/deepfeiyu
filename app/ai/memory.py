"""人格长期记忆 v2：分层、混合检索、人物档案与压缩。

三层：
- core      长期事实/关系/偏好，跨会话注入
- episodic  发生过的事、约定，按相关度注入
- session   一段对话的压缩摘要，过期自动清理

检索不依赖外部服务：默认是关键词 + 重要度 + 新近度；配置 OpenAI 兼容
embedding 后自动加上余弦相似度。没有 embedding 时一切照常工作。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any

from .. import config, db
from .agent import Agent
from .embedding import EmbeddingClient, EmbeddingError

log = logging.getLogger("memory")

MAX_ADDS = 3
VALID_KINDS = ("fact", "preference", "event", "relationship")
VALID_SCOPES = ("global", "chat")
VALID_LAYERS = ("core", "episodic", "session")
VALID_SUBJECTS = ("contact", "self", "world")
NEAR_DUP_THRESHOLD = 0.6
NEAR_DUP_WINDOW_HOURS = 72
EXTRACT_EXISTING_LIMIT = 20
EXTRACT_CORE_RESERVE = 10

EXTRACT_SYSTEM = (
    "你是记忆管理器，负责从一个 AI 人格的对话中提取值得长期保留的信息。"
    "只输出 JSON，不要输出任何其他文字。"
)

_EXTRACT_INSTRUCTION = (
    "请判断这次对话里有没有值得长期记住的信息：对方的名字、称呼、关系、喜好厌恶、"
    "生日纪念日、职业、住址、重要经历、约定、你的重要决定等。"
    "琐碎寒暄、一次性话题、已经记过且内容没变化的东西不要输出。"
    "新信息和已有记忆冲突时用 update 覆盖对应 id（比如“换工作了”要更新旧职业），"
    "已有记忆明显过时或错误时用 delete。\n"
    "分级规则：\n"
    "- layer=core：稳定信息——称呼、身份、关系、长期偏好、雷区、生日、职业、住址、长期习惯；\n"
    "- layer=episodic：一次性事件、近况、临时计划；\n"
    "- layer=session：只对当前这段对话有用的临时上下文（通常不用你输出，系统会压缩）。\n"
    "importance：3=身份/关系/雷区，2=稳定偏好/重要事实，1=一次性琐事。\n"
    "同一件事换说法、补充细节时，用 update 指向已有 id，不要 add；只有确实不同的事才 add。\n"
    "subject=contact 表示关于对方，self 表示关于你自己，world 表示外部世界。\n"
    "scope=global 表示任何会话都该记得，chat 表示只在当前会话注入。\n"
    "只输出 JSON：\n"
    '{"add": [{"content": "一句话记忆", "kind": "fact|preference|event|relationship", '
    '"layer": "core|episodic|session", "subject": "contact|self|world", '
    '"importance": 1到3, "confidence": 0到1, "scope": "global|chat"}],'
    ' "update": [{"id": 记忆id, "content": "新内容", "layer": "core|episodic|session", '
    '"importance": 1到3}], "delete": [记忆id]}'
    "\n没有值得记的就输出 {}"
)

_COMPRESS_SYSTEM = (
    "你负责把一段微信对话压缩成一段可长期保存的摘要。只输出摘要正文，不要标题、"
    "不要 Markdown、不要分点，控制在 300 字以内。保留人物、约定、情绪转折和未完成的事。"
)

_CONSOLIDATE_FALLBACK = "记忆整理完成"


def _cfg() -> dict[str, Any]:
    return config.load().get("memory") or {}


def _max_items(limit: int | None = None) -> int:
    if limit:
        return max(1, min(200, int(limit)))
    raw = _cfg().get("max_items")
    if raw:
        return max(1, min(200, int(raw)))
    return max(1, min(200, int(config.load().get("ai", {}).get("memory_max", 12) or 12)))


def _context_chars() -> int:
    raw = _cfg().get("context_chars")
    return max(200, min(8000, int(raw or 1200)))


def _norm_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _as_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        mid = int(value)
    except (TypeError, ValueError):
        return None
    return mid if mid > 0 else None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _terms(text: str) -> set[str]:
    text = _norm_text(text).lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = {"".join(pair) for pair in zip(cjk, cjk[1:])}
    return words | bigrams | set(cjk)


def _bigrams(text: str) -> set[str]:
    text = _norm_text(text).lower()
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    grams = {"".join(pair) for pair in zip(cjk, cjk[1:])}
    grams |= set(re.findall(r"[a-z0-9_]{2,}", text))
    return grams


def similarity(a: str, b: str) -> float:
    """两条记忆的文本相似度；用于拦同义复述，空文本返回 0。"""
    ga = _bigrams(a or "")
    gb = _bigrams(b or "")
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _infer_layer(kind: str) -> str:
    return "episodic" if kind == "event" else "core"


def _normalize_add(item: dict[str, Any]) -> tuple[str, str, int, float, str]:
    """把模型的 add 项规范成确定的 kind/layer/importance/confidence/scope。"""
    kind = str(item.get("kind") or "fact").strip()
    if kind not in VALID_KINDS:
        kind = "fact"
    scope = str(item.get("scope") or "global").strip()
    if scope not in VALID_SCOPES:
        scope = "global"
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence", 0.8))))
    except (TypeError, ValueError):
        confidence = 0.8
    provided = set(item.get("_provided") or [])
    layer = str(item.get("layer") or "").strip()
    if layer not in VALID_LAYERS:
        layer = _infer_layer(kind)
    if (kind == "relationship" and confidence >= 0.7) or (
        kind == "preference" and scope == "global"
    ):
        layer = "core"
    try:
        importance = int(item.get("importance") or 2)
    except (TypeError, ValueError):
        importance = 2
    importance = max(1, min(3, importance))
    if kind == "event" and "_provided" in item and "importance" not in provided:
        importance = 1
    return kind, layer, importance, confidence, scope


def _recent_enough(row: dict[str, Any], now: float) -> bool:
    ts = max(float(row.get("updated_at") or 0), float(row.get("created_at") or 0))
    return bool(ts) and now - ts <= NEAR_DUP_WINDOW_HOURS * 3600


def _find_near_duplicate(
    account_id: str, content: str, chat_uid: str, kind: str
) -> dict[str, Any] | None:
    now = time.time()
    rows = db.list_memories(account_id, limit=500, chat_uid=chat_uid or "")
    best: tuple[float, dict[str, Any]] | None = None
    for row in rows:
        if str(row.get("layer") or "") == "session":
            continue
        if str(row.get("kind") or "fact") != kind:
            continue
        if not _recent_enough(row, now):
            continue
        score = similarity(content, str(row.get("content") or ""))
        if score >= NEAR_DUP_THRESHOLD and (best is None or score > best[0]):
            best = (score, row)
    return best[1] if best else None


def _merge_near_duplicate(
    account_id: str,
    existing: dict[str, Any],
    content: str,
    layer: str,
    importance: int,
) -> str:
    old_text = str(existing.get("content") or "")
    meta = dict(existing.get("meta") or {})
    merged = [str(x) for x in (meta.get("merged_texts") or [])][-2:]
    if content and content != old_text and content not in merged:
        merged.append(content[:200])
    meta["merged_texts"] = merged[-3:]
    final_importance = max(int(existing.get("importance") or 1), importance)
    kwargs: dict[str, Any] = {"importance": final_importance, "meta": meta}
    if len(content) > len(old_text):
        kwargs["content"] = content
    if layer == "core":
        kwargs["layer"] = "core"
    if layer == "core" or final_importance >= 2 or existing.get("pinned"):
        kwargs["expires_at"] = 0
    elif str(existing.get("source") or "") == "extract":
        kwargs["expires_at"] = _ttl_expiry("episodic", final_importance, False, "extract")
    db.update_memory(account_id, int(existing["id"]), **kwargs)
    return f"合并记忆 #{existing['id']}：{content}"


def _ttl_days() -> int:
    raw = _cfg().get("episodic_ttl_days", 30)
    try:
        return max(0, min(365, int(raw)))
    except (TypeError, ValueError):
        return 30


def _ttl_expiry(layer: str, importance: int, pinned: bool, source: str) -> float:
    days = _ttl_days()
    if days and source == "extract" and layer == "episodic" and importance <= 1 and not pinned:
        return time.time() + days * 86400
    return 0


def keyword_score(query: str, content: str) -> float:
    """轻量中文友好的关键词重合度；embedding 不可用时它就是主信号。"""
    q = _terms(query)
    if not q:
        return 0.0
    c = _terms(content)
    if not c:
        return 0.0
    return len(q & c) / max(1, len(q))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
        na += float(x) * float(x)
        nb += float(y) * float(y)
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _recency(updated_at: float, now: float | None = None) -> float:
    now = now or time.time()
    days = max(0.0, (now - float(updated_at or 0)) / 86400)
    return math.exp(-days / 30.0)


def score_memory(
    item: dict[str, Any],
    query: str,
    query_vec: list[float] | None = None,
    now: float | None = None,
) -> float:
    """混合打分：语义/关键词/重要度/新近度/置顶，再给层级一点偏置。"""
    now = now or time.time()
    content = str(item.get("content") or "")
    kw = keyword_score(query, content)
    cos = cosine(query_vec or [], item.get("embedding") or [])
    importance = max(1, min(3, int(item.get("importance") or 2))) / 3.0
    rec = _recency(float(item.get("updated_at") or 0), now)
    pinned = 1.0 if item.get("pinned") else 0.0
    if query_vec:
        score = 0.48 * cos + 0.22 * kw + 0.15 * importance + 0.10 * rec + 0.05 * pinned
    else:
        score = 0.52 * kw + 0.25 * importance + 0.16 * rec + 0.07 * pinned
    score += {"core": 0.06, "episodic": 0.02, "session": 0.0}.get(
        str(item.get("layer") or "core"), 0.0
    )
    return round(score, 6)


def inject_block(
    account_id: str,
    chat_uid: str,
    limit: int = 12,
    query: str = "",
) -> str:
    """组 prompt 用的记忆块。

    兼容旧调用：query 为空且没有人物档案时，仍输出原来的「【你的长期记忆】」格式。
    新链路（自动回复/控制台）传 query，走混合检索 + 档案 + 分层。
    """
    profile = None
    if chat_uid and _cfg().get("profile_enabled", True):
        try:
            profile = db.get_memory_profile(account_id, chat_uid)
        except Exception as exc:  # noqa: BLE001
            log.warning("读取人物档案失败 %s: %s", account_id, exc)
    if query or profile:
        return build_context(account_id, chat_uid, query=query, limit=limit)

    try:
        memories = db.recall_memories(account_id, chat_uid, limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取记忆失败 %s: %s", account_id, exc)
        return ""
    if not memories:
        return ""
    lines = [
        "【你的长期记忆】下面是你之前记住的关于对方和自己生活的事，"
        "运用时自然融入对话即可，不要罗列、不要复述原文："
    ]
    for m in memories:
        kind = m.get("kind") or "fact"
        prefix = f"[{kind}] " if kind != "fact" else ""
        lines.append(f"- {prefix}{m['content']}")
    return "\n".join(lines)


def _profile_block(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    summary = _norm_text(str(profile.get("summary") or ""))
    if summary:
        return "【人物档案】" + summary[:600]
    parts: list[str] = []
    for label, key in (
        ("关系", "relationship"),
        ("偏好", "preferences"),
        ("雷区", "taboos"),
        ("最近", "recent_topics"),
    ):
        value = _norm_text(str(profile.get(key) or ""))
        if value:
            parts.append(f"{label}：{value[:180]}")
    return "【人物档案】" + "；".join(parts) if parts else ""


def _select_candidates(
    candidates: list[dict[str, Any]],
    query: str,
    query_vec: list[float] | None,
    max_items: int,
) -> list[dict[str, Any]]:
    scored = sorted(
        candidates,
        key=lambda m: score_memory(m, query, query_vec),
        reverse=True,
    )
    pinned = sorted(
        [m for m in candidates if m.get("pinned")],
        key=lambda m: score_memory(m, query, query_vec),
        reverse=True,
    )
    core = [m for m in candidates if m.get("layer") == "core"]
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for group in (pinned, core[:6], scored):
        for m in group:
            mid = int(m.get("id") or 0)
            if mid and mid not in seen:
                seen.add(mid)
                selected.append(m)
            if len(selected) >= max_items:
                return selected
    return selected


def _render_context(
    account_id: str,
    candidates: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    query: str,
    query_vec: list[float] | None = None,
    limit: int | None = None,
) -> str:
    budget = _context_chars()
    selected = _select_candidates(candidates, query, query_vec, _max_items(limit))
    if not selected and not profile:
        return ""

    lines: list[str] = ["【你记得的事】"]
    used_ids: set[int] = set()

    def add(title: str, items: list[dict[str, Any]]) -> None:
        nonlocal budget
        rows: list[str] = []
        for m in items:
            mid = int(m.get("id") or 0)
            if mid and mid in used_ids:
                continue
            text = _norm_text(str(m.get("content") or ""))
            if not text:
                continue
            line = f"- {text[:160]}"
            if len(line) + 1 > budget:
                break
            rows.append(line)
            budget -= len(line) + 1
            if mid:
                used_ids.add(mid)
        if rows:
            head = f"\n{title}"
            if len(head) + 1 <= budget:
                lines.append(head)
                budget -= len(head) + 1
                lines.extend(rows)

    # 置顶先渲染，避免长期事实把预算吃光后置顶往事/摘要被静默丢掉
    add("【置顶】", [m for m in selected if m.get("pinned")])
    block = _profile_block(profile)
    if block and len(block) + 1 <= budget:
        lines.append(block)
        budget -= len(block) + 1
    add("【长期事实】", [m for m in selected if m.get("layer") == "core"])
    add("【相关往事】", [m for m in selected if m.get("layer") == "episodic"])
    add("【最近会话】", [m for m in selected if m.get("layer") == "session"])

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def build_context(
    account_id: str,
    chat_uid: str,
    query: str = "",
    limit: int | None = None,
) -> str:
    """同步注入：不查向量，走关键词/重要度/新近度；主链路优先用 retrieve_context。"""
    cfg = _cfg()
    profile = None
    if chat_uid and cfg.get("profile_enabled", True):
        try:
            profile = db.get_memory_profile(account_id, chat_uid)
        except Exception:  # noqa: BLE001
            profile = None
    try:
        candidates = db.list_memories_for_context(account_id, chat_uid or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("读取记忆候选失败 %s: %s", account_id, exc)
        return ""
    return _render_context(account_id, candidates, profile, query, None, limit)


async def retrieve_context(
    account_id: str,
    chat_uid: str,
    query: str,
    limit: int | None = None,
) -> str:
    """async 注入：配置了 embedding 且确实有候选时才请求向量，避免空库白花钱。"""
    cfg = _cfg()
    profile = None
    if chat_uid and cfg.get("profile_enabled", True):
        try:
            profile = db.get_memory_profile(account_id, chat_uid)
        except Exception:  # noqa: BLE001
            profile = None
    client = EmbeddingClient()
    if not client.configured:
        return build_context(account_id, chat_uid, query=query, limit=limit)
    try:
        candidates = db.list_memories_for_context(
            account_id, chat_uid or "", with_embeddings=True
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("读取记忆候选失败 %s: %s", account_id, exc)
        return build_context(account_id, chat_uid, query=query, limit=limit)
    if not candidates:
        # 只有档案时不需要 embedding；直接渲染档案
        return _render_context(account_id, [], profile, query, None, limit) if profile else ""
    query_vec: list[float] | None = None
    if query:
        try:
            query_vec = (await client.embed([query]))[0]
        except EmbeddingError as exc:
            log.info("query embedding 失败，退回关键词：%s", exc)
    if not query_vec:
        return _render_context(account_id, candidates, profile, query, None, limit)
    return _render_context(account_id, candidates, profile, query, query_vec, limit)


def parse_extract_reply(text: str, *, with_meta: bool = False) -> dict[str, Any]:
    """防御式解析提取模型的输出；任何非法输入都返回空操作。

    兼容旧模型：没有 layer/subject/confidence 时，输出结构与旧版完全一致。
    """
    empty: dict[str, Any] = {"add": [], "update": [], "delete": []}
    if not text:
        return empty
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return empty
    try:
        data = json.loads(text[start : end + 1])
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty

    adds: list[dict[str, Any]] = []
    for item in _as_list(data.get("add"))[:MAX_ADDS]:
        if isinstance(item, str):
            item = {"content": item}
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        kind = str(item.get("kind") or "fact").strip()
        if kind not in VALID_KINDS:
            kind = "fact"
        scope = str(item.get("scope") or "global").strip()
        if scope not in VALID_SCOPES:
            scope = "global"
        try:
            importance = int(item.get("importance") or 2)
        except (TypeError, ValueError):
            importance = 2
        entry: dict[str, Any] = {
            "content": content[:200],
            "kind": kind,
            "importance": importance,
            "scope": scope,
        }
        layer = str(item.get("layer") or "").strip()
        if layer in VALID_LAYERS:
            entry["layer"] = layer
        subject = str(item.get("subject") or "").strip()
        if subject in VALID_SUBJECTS:
            entry["subject"] = subject
        if "confidence" in item:
            try:
                entry["confidence"] = max(0.0, min(1.0, float(item.get("confidence"))))
            except (TypeError, ValueError):
                pass
        if with_meta:
            entry["_provided"] = [
                key
                for key in ("content", "kind", "importance", "scope", "layer", "subject", "confidence")
                if key in item
            ]
        adds.append(entry)

    updates: list[dict[str, Any]] = []
    for item in _as_list(data.get("update"))[:MAX_ADDS]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        mid = _as_id(item.get("id"))
        if mid is None:
            continue
        entry = {"id": mid, "content": content[:200]}
        layer = str(item.get("layer") or "").strip()
        if layer in VALID_LAYERS:
            entry["layer"] = layer
        if "importance" in item:
            try:
                entry["importance"] = max(1, min(3, int(item.get("importance"))))
            except (TypeError, ValueError):
                pass
        updates.append(entry)

    deletes: list[int] = []
    for item in _as_list(data.get("delete"))[:MAX_ADDS]:
        mid = _as_id(item)
        if mid is not None:
            deletes.append(mid)

    return {"add": adds, "update": updates, "delete": deletes}


def apply_decisions(
    account_id: str,
    chat_uid: str,
    decisions: dict[str, Any],
    *,
    dedupe: bool = True,
    touched: set[str] | None = None,
) -> list[str]:
    """执行提取决策，返回变更描述（空列表 = 没有变化）。"""
    changes: list[str] = []
    for item in decisions.get("add") or []:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        kind, layer, importance, confidence, scope = _normalize_add(item)
        target_uid = "" if scope == "global" else chat_uid
        if touched is not None:
            touched.add(target_uid)
        existing = db.find_memory_like(account_id, content, target_uid) if dedupe else None
        if existing is None and dedupe:
            existing = _find_near_duplicate(account_id, content, target_uid, kind)
            if existing is not None:
                changes.append(
                    _merge_near_duplicate(account_id, existing, content, layer, importance)
                )
                continue
        if existing:
            declared = set(item.get("_provided") or [])
            kwargs: dict[str, Any] = {"content": content, "layer": layer}
            if not declared or "kind" in declared:
                kwargs["kind"] = kind
            if not declared or "importance" in declared:
                kwargs["importance"] = importance
            if not declared or "subject" in declared:
                kwargs["subject"] = item.get("subject") or "contact"
            if not declared or "confidence" in declared:
                kwargs["confidence"] = confidence
            final_importance = int(kwargs.get("importance", existing.get("importance") or 1))
            final_layer = str(kwargs.get("layer") or existing.get("layer") or "core")
            kwargs["expires_at"] = _ttl_expiry(
                final_layer,
                final_importance,
                bool(existing.get("pinned")),
                str(existing.get("source") or "manual"),
            )
            db.update_memory(account_id, int(existing["id"]), **kwargs)
            changes.append(f"合并记忆 #{existing['id']}：{content}")
            continue
        db.add_memory(
            account_id,
            content,
            chat_uid=target_uid,
            kind=kind,
            importance=importance,
            layer=layer,
            subject=item.get("subject") or "contact",
            confidence=confidence,
            source="extract",
            expires_at=_ttl_expiry(layer, importance, False, "extract"),
        )
        changes.append(f"记住：{content}")
    for item in decisions.get("update") or []:
        mid = _as_id(item.get("id"))
        if mid is None:
            continue
        kwargs: dict[str, Any] = {"content": item["content"]}
        for key in ("kind", "importance", "layer", "subject", "confidence"):
            if key in item:
                kwargs[key] = item[key]
        old = db.get_memory(account_id, mid)
        final_layer = str(item.get("layer") or (old or {}).get("layer") or "core")
        final_importance = int(item.get("importance") or (old or {}).get("importance") or 1)
        kwargs["expires_at"] = _ttl_expiry(
            final_layer,
            final_importance,
            bool((old or {}).get("pinned")),
            str((old or {}).get("source") or "manual"),
        )
        if touched is not None and old:
            touched.add(str(old.get("chat_uid") or ""))
        if db.update_memory(account_id, mid, **kwargs):
            changes.append(f"更新记忆 #{mid}：{item['content']}")
    for raw_mid in decisions.get("delete") or []:
        mid = _as_id(raw_mid)
        if mid is None:
            continue
        old = db.get_memory(account_id, mid)
        if touched is not None and old:
            touched.add(str(old.get("chat_uid") or ""))
        if db.delete_memory(account_id, mid):
            changes.append(f"忘掉记忆 #{mid}")
    return changes


async def _embed_pending(account_id: str, limit: int = 40) -> int:
    """给最近没有向量的记忆补 embedding；未配置/失败都静默返回 0。"""
    client = EmbeddingClient()
    if not client.configured:
        return 0
    rows = db.list_memories_missing_embeddings(
        account_id, limit=max(1, min(200, limit))
    )
    if not rows:
        return 0
    try:
        vectors = await client.embed([str(m.get("content") or "") for m in rows])
    except EmbeddingError as exc:
        log.info("补记忆向量失败（不影响主流程）：%s", exc)
        return 0
    for m, vec in zip(rows, vectors):
        db.set_memory_embedding(account_id, int(m["id"]), vec)
    return len(rows)


def _select_existing_for_extract(
    memories: list[dict[str, Any]], transcript: str, limit: int = EXTRACT_EXISTING_LIMIT
) -> list[dict[str, Any]]:
    """core/置顶优先保留，其余按与本次对话的关键词相关度补足。"""
    prioritized = sorted(
        [m for m in memories if m.get("layer") == "core" or m.get("pinned")],
        key=lambda m: (
            1 if m.get("pinned") else 0,
            int(m.get("importance") or 2),
            float(m.get("updated_at") or 0),
        ),
        reverse=True,
    )[:EXTRACT_CORE_RESERVE]
    seen = {int(m.get("id") or 0) for m in prioritized}
    ranked = sorted(
        (m for m in memories if int(m.get("id") or 0) not in seen),
        key=lambda m: keyword_score(transcript, str(m.get("content") or "")),
        reverse=True,
    )
    return (prioritized + ranked)[: max(1, limit)]


async def extract_and_store(
    llm: Any,
    account_id: str,
    chat_uid: str,
    chat_name: str,
    transcript: str,
) -> list[str]:
    """对话结束后跑一次提取；返回变更描述。LLM 不可用或无新记忆时返回空列表。"""
    if not transcript.strip():
        return []
    if not getattr(llm, "configured", False):
        return []
    existing = _select_existing_for_extract(
        db.list_memories_for_context(account_id, chat_uid), transcript
    )
    existing_lines = (
        "\n".join(
            f"{m['id']}. [{m.get('layer') or 'core'}] {m['content']}"
            for m in existing
        )
        or "（还没有任何记忆）"
    )
    prompt = (
        f"当前会话：{chat_name}\n\n"
        f"【最近对话】\n{transcript.strip()[:2400]}\n\n"
        f"【已有的记忆】\n{existing_lines}\n\n"
        f"{_EXTRACT_INSTRUCTION}"
    )
    agent = Agent(llm, [], max_rounds=1)
    result = await agent.run(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        # 与 tagger 同理：思考型模型的思考计入 completion 预算，预算太小正文为空
        max_tokens=1200,
        effort="off",
    )
    if result.get("error"):
        log.warning("记忆提取失败（%s）: %s", account_id, result["error"])
        return []
    decisions = parse_extract_reply(result.get("text") or "", with_meta=True)
    touched: set[str] = set()
    changes = apply_decisions(account_id, chat_uid, decisions, touched=touched)
    if changes:
        await _embed_pending(account_id)
        if "" in touched:
            refresh_profiles_for_change(account_id, "")
        else:
            for uid in touched:
                refresh_profile(account_id, uid)
            if not touched and chat_uid:
                refresh_profile(account_id, chat_uid)
    return changes


async def compress_history(
    llm: Any,
    account_id: str,
    chat_uid: str,
    chat_name: str,
    history: list[dict[str, Any]],
) -> list[str]:
    """把较早的对话压成一条 session 记忆；阈值以下或失败都不做事。"""
    cfg = _cfg()
    threshold = max(8, int(cfg.get("compress_after_messages") or 24))
    keep = max(4, int(cfg.get("compress_keep_recent") or 12))
    watermark_key = f"memory:compress:{account_id}:{chat_uid}"
    try:
        watermark = float(db.kv_get(watermark_key, 0) or 0)
    except Exception:  # noqa: BLE001
        watermark = 0.0
    if any(h.get("ts") for h in history):
        fresh = [
            h
            for h in history
            if float(h.get("ts") or 0) > watermark
        ]
    else:
        # 测试/旧调用可能没有 ts；退化成整段判断
        fresh = list(history)
    if len(fresh) < threshold:
        return []
    older = fresh[:-keep]
    if len(older) < 4:
        return []
    lines: list[str] = []
    for h in older[-40:]:
        content = _norm_text(str(h.get("content") or ""))
        if not content:
            continue
        who = "我" if h.get("is_self") else (h.get("sender_name") or chat_name or "对方")
        lines.append(f"{who}：{content[:300]}")
    transcript = "\n".join(lines)
    if not transcript:
        return []
    agent = Agent(llm, [], max_rounds=1)
    result = await agent.run(
        [
            {"role": "system", "content": _COMPRESS_SYSTEM},
            {"role": "user", "content": f"会话：{chat_name}\n\n{transcript}"},
        ],
        temperature=0.2,
        max_tokens=500,
        effort="off",
    )
    if result.get("error"):
        log.info("对话压缩失败（%s）：%s", account_id, result["error"])
        return []
    summary = _norm_text(result.get("text") or "")
    if not summary:
        return []
    ttl_days = max(1, int(cfg.get("session_ttl_days") or 7))
    db.add_memory(
        account_id,
        summary[:800],
        chat_uid=chat_uid,
        kind="event",
        importance=2,
        layer="session",
        subject="contact",
        confidence=0.7,
        expires_at=time.time() + ttl_days * 86400,
        source="compress",
        meta={"kind": "history_summary", "messages": len(older)},
    )
    # 同一会话只留最近两条摘要，防止无限膨胀
    sessions = db.list_memories(account_id, limit=50, layer="session", chat_uid=chat_uid)
    if len(sessions) > 2:
        db.remove_memories(account_id, [int(m["id"]) for m in sessions[2:]])
    latest_ts = max([float(h.get("ts") or 0) for h in older] or [time.time()])
    try:
        db.kv_set(watermark_key, latest_ts)
    except Exception:  # noqa: BLE001
        pass
    return [f"压缩了 {len(older)} 条对话"]


def refresh_profile(account_id: str, chat_uid: str = "") -> dict[str, Any] | None:
    """从全局 + 当前会话的记忆重建档案；chat_uid 为空时只建全局档案。

    全局事实（chat_uid=''）对每个会话都该可见；反之，某个会话的私事不能
    混进控制台/全局档案。没有记忆就清空对应档案。
    """
    if chat_uid:
        rows = db.list_memories(account_id, limit=500, chat_uid="")
        rows += db.list_memories(account_id, limit=500, chat_uid=chat_uid)
        seen: set[int] = set()
        memories: list[dict[str, Any]] = []
        for row in rows:
            mid = int(row.get("id") or 0)
            if mid and mid not in seen:
                seen.add(mid)
                memories.append(row)
    else:
        memories = db.list_memories(account_id, limit=500, chat_uid="")
    if not memories:
        db.delete_memory_profile(account_id, chat_uid or "")
        return None
    core = [m for m in memories if m.get("layer") == "core"]
    episodic = [m for m in memories if m.get("layer") == "episodic"]
    session = [m for m in memories if m.get("layer") == "session"]
    strong = [m for m in episodic if int(m.get("importance") or 1) >= 2]

    def pick(kind: str, limit: int = 4) -> str:
        rows = [m["content"] for m in (core + strong) if m.get("kind") == kind][:limit]
        return "；".join(rows)

    taboo_words = (
        "不喜欢", "讨厌", "受不了", "禁忌", "不要", "别再", "别约", "别催", "别叫", "别发",
    )
    taboos = [
        m["content"]
        for m in core + episodic
        if any(word in str(m.get("content") or "") for word in taboo_words)
    ][:3]
    recent = [m["content"] for m in (session + episodic)[:4]]
    relationship = pick("relationship")
    preferences = pick("preference")

    summary_items: list[str] = []
    for label, value in (
        ("关系", relationship),
        ("偏好", preferences),
        ("雷区", "；".join(taboos)),
        ("最近", "；".join(recent)),
    ):
        if value:
            summary_items.append(f"{label}：{value}")
    facts = [m["content"] for m in core[:3]]
    if facts:
        summary_items.append("事实：" + "；".join(facts))

    profile = db.upsert_memory_profile(
        account_id,
        chat_uid or "",
        summary="；".join(summary_items)[:600],
        relationship=relationship,
        preferences=preferences,
        taboos="；".join(taboos)[:400],
        recent_topics="；".join(recent)[:400],
        meta={"sources": len(memories)},
    )
    return profile


def refresh_profiles_for_change(account_id: str, chat_uid: str = "") -> None:
    """记忆变更后刷新档案：全局变了就刷所有会话，会话变了只刷该会话。"""
    targets: set[str] = set()
    if chat_uid:
        targets.add(chat_uid)
    else:
        try:
            rows = db.list_memories(account_id, limit=5000)
        except Exception:  # noqa: BLE001
            rows = []
        targets.update(str(m.get("chat_uid") or "") for m in rows if m.get("chat_uid"))
        # 只有全局记忆、但已有会话档案的情况也要刷到
        try:
            targets.update(
                str(p.get("chat_uid") or "")
                for p in db.list_memory_profiles(account_id, limit=5000)
                if p.get("chat_uid")
            )
        except Exception:  # noqa: BLE001
            pass
        targets.add("")
    for uid in targets:
        refresh_profile(account_id, uid)


def consolidate(account_id: str) -> dict[str, Any]:
    """整理记忆：清过期、合并重复、衰减长期未用、重建档案。"""
    deleted_expired = db.delete_expired_memories(account_id)
    memories = db.list_memories(account_id, limit=5000)
    # 1) 同会话同类型：原文相同，或 72h 内近义，合并到置顶/重要/较新的一条
    merged = 0
    removed: list[int] = []
    now = time.time()
    for uid in {str(m.get("chat_uid") or "") for m in memories}:
        rows = [m for m in memories if str(m.get("chat_uid") or "") == uid]
        rows.sort(
            key=lambda x: (
                1 if x.get("pinned") else 0,
                int(x.get("importance") or 2),
                float(x.get("updated_at") or 0),
            ),
            reverse=True,
        )
        kept: list[dict[str, Any]] = []
        for row in rows:
            content = str(row.get("content") or "")
            kind = str(row.get("kind") or "fact")
            if str(row.get("layer") or "") == "session":
                kept.append(row)
                continue
            dup = None
            for candidate in kept:
                if str(candidate.get("kind") or "fact") != kind:
                    continue
                same_text = _norm_text(content).lower() == _norm_text(
                    str(candidate.get("content") or "")
                ).lower()
                if not same_text and not (
                    _recent_enough(candidate, now) and _recent_enough(row, now)
                ):
                    continue
                if same_text or similarity(content, str(candidate.get("content") or "")) >= NEAR_DUP_THRESHOLD:
                    dup = candidate
                    break
            if dup is None:
                kept.append(row)
                continue
            meta = dict(dup.get("meta") or {})
            texts = [str(x) for x in (meta.get("merged_texts") or [])][-2:]
            if content and content != str(dup.get("content") or "") and content not in texts:
                texts.append(content[:200])
            meta["merged_texts"] = texts[-3:]
            db.update_memory(
                account_id,
                int(dup["id"]),
                importance=max(int(dup.get("importance") or 1), int(row.get("importance") or 1)),
                meta=meta,
            )
            removed.append(int(row["id"]))
            merged += 1
    if removed:
        db.remove_memories(account_id, removed)

    # 2) 只衰减 episodic 中长期未用的非置顶记忆
    now = time.time()
    decayed = 0
    for m in memories:
        if m.get("pinned") or int(m.get("importance") or 2) <= 1:
            continue
        if str(m.get("layer") or "core") != "episodic":
            continue
        last = float(m.get("last_access") or m.get("updated_at") or 0)
        if last and now - last > 45 * 86400:
            db.update_memory(account_id, int(m["id"]), importance=int(m["importance"]) - 1)
            decayed += 1

    # 3) 存量回填：旧的稳定事实提升 core
    memories = db.list_memories(account_id, limit=5000)
    promoted = 0
    for m in memories:
        if (
            str(m.get("layer") or "") == "episodic"
            and str(m.get("kind") or "") in ("fact", "preference", "relationship")
            and int(m.get("importance") or 1) >= 2
        ):
            if db.update_memory(account_id, int(m["id"]), layer="core"):
                promoted += 1

    # 4) 存量回填：低价值 episodic 补 TTL
    memories = db.list_memories(account_id, limit=5000)
    ttl_added = 0
    for m in memories:
        if (
            str(m.get("source") or "") == "extract"
            and str(m.get("layer") or "") == "episodic"
            and int(m.get("importance") or 1) <= 1
            and not m.get("pinned")
            and float(m.get("expires_at") or 0) == 0
        ):
            exp = _ttl_expiry("episodic", 1, False, "extract")
            if exp and db.update_memory(account_id, int(m["id"]), expires_at=exp):
                ttl_added += 1

    # 5) 重建所有会话档案
    chat_uids = sorted({str(m.get("chat_uid") or "") for m in memories})
    profiles = 0
    for uid in chat_uids:
        if uid and refresh_profile(account_id, uid):
            profiles += 1
    if refresh_profile(account_id, ""):
        profiles += 1
    return {
        "expired": deleted_expired,
        "merged": merged,
        "decayed": decayed,
        "promoted": promoted,
        "ttl": ttl_added,
        "profiles": profiles,
        "total": db.memory_stats(account_id)["total"],
    }


async def rebuild_embeddings(account_id: str, force: bool = False) -> dict[str, Any]:
    """重建向量索引；未配置 embedding 时返回明确原因，不假装成功。"""
    client = EmbeddingClient()
    if not client.configured:
        return {"ok": False, "updated": 0, "error": "embedding 未配置（memory.embedding）"}
    if force:
        db.clear_memory_embeddings(account_id)
    total = db.count_memories(account_id)
    pending = db.list_memories_missing_embeddings(account_id, limit=5000)
    if not pending:
        return {"ok": True, "updated": 0, "total": total}
    updated = 0
    for start in range(0, len(pending), 32):
        batch = pending[start : start + 32]
        try:
            vectors = await client.embed([str(m.get("content") or "") for m in batch])
        except EmbeddingError as exc:
            return {"ok": False, "updated": updated, "error": str(exc)}
        for m, vec in zip(batch, vectors):
            if db.set_memory_embedding(account_id, int(m["id"]), vec):
                updated += 1
    return {"ok": True, "updated": updated, "total": total}


def _scope_rows(account_id: str, chat_uid: str | None, with_embeddings: bool = False):
    if chat_uid is None:
        if with_embeddings:
            return db.list_memories_with_embeddings(account_id, None, limit=3000)
        return db.list_memories(account_id, limit=2000)
    return db.list_memories_for_context(
        account_id, chat_uid, with_embeddings=with_embeddings
    )


def search(
    account_id: str,
    query: str,
    limit: int = 8,
    chat_uid: str | None = None,
) -> list[dict[str, Any]]:
    """给 recall 工具用：先关键词，再按混合分排序；不依赖 embedding。"""
    query = _norm_text(query)
    if not query:
        return []
    rows = _scope_rows(account_id, chat_uid)
    scored = sorted(rows, key=lambda m: score_memory(m, query), reverse=True)
    return scored[: max(1, min(50, int(limit or 8)))]


async def search_async(
    account_id: str,
    query: str,
    limit: int = 8,
    chat_uid: str | None = None,
) -> list[dict[str, Any]]:
    """recall 工具的 async 版：配置了 embedding 就带语义召回，否则退回关键词。"""
    query = _norm_text(query)
    if not query:
        return []
    client = EmbeddingClient()
    if not client.configured:
        return search(account_id, query, limit, chat_uid=chat_uid)
    rows = _scope_rows(account_id, chat_uid, with_embeddings=True)
    if not rows:
        return []
    vec: list[float] | None = None
    try:
        vec = (await client.embed([query]))[0]
    except EmbeddingError:
        vec = None
    scored = sorted(rows, key=lambda m: score_memory(m, query, vec), reverse=True)
    return scored[: max(1, min(50, int(limit or 8)))]
