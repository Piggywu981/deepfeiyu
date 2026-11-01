"""成长块：常驻自我总览 + 目标 + 最近日记 + 按对话动态召回的特质。"""
from __future__ import annotations

from .. import config
from . import store

KIND_LABELS = {
    "voice": "口癖",
    "opinion": "观点",
    "value": "价值观",
    "preference": "偏好",
    "relationship": "关系",
    "habit": "习惯",
    "narrative": "自我叙事",
    "joke": "内部梗",
    "other": "其它",
}


def _journal_line(items: list[dict]) -> str:
    if not items:
        return ""
    content = " ".join(str(items[0].get("content") or "").split())
    return content[:160] + ("…" if len(content) > 160 else "")


def block(
    account_id: str,
    query: str = "",
    chat_uid: str = "",
    chat_name: str = "",
    include_chat_scoped: bool = True,
) -> str:
    if not config.growth_enabled(account_id):
        return ""
    cfg = config.growth_cfg()
    state = store.get_state(account_id)
    summary = str(state.get("self_summary") or "").strip()
    goals = store.list_goals(account_id, status="open")[:2]
    goal_lines = [str(g.get("content") or "").strip() for g in goals if g.get("content")]
    journal = _journal_line(store.list_journal(account_id, limit=1))

    reserved = len("\n".join(goal_lines)) + len(journal) + 120
    budget = max(200, int(cfg.get("prompt_chars") or 1200) - reserved)
    traits = store.recall_traits(
        account_id,
        query=query,
        chat_uid=chat_uid,
        chat_name=chat_name,
        limit_chars=budget,
        include_chat_scoped=include_chat_scoped,
    )

    if not (summary or goal_lines or journal or traits):
        return ""

    parts: list[str] = []
    if summary:
        parts.append("【你现在是谁】\n" + summary)
    if goal_lines:
        parts.append("【你最近在想】\n" + "\n".join("- " + line for line in goal_lines))
    if journal:
        parts.append("【最近的日记】\n" + journal)
    if traits:
        lines = []
        for item in traits:
            label = KIND_LABELS.get(str(item.get("kind") or "other"), "其它")
            lines.append(f"- [{label}] {str(item.get('content') or '').strip()}")
        parts.append("【你最近的样子】\n" + "\n".join(lines))
    parts.append(
        "（上面是你自己长出来的，不是别人给你的剧本。别一次全演出来，"
        "别主动宣布你在进化；和基础人设、安全底线冲突时以后者为准。）"
    )
    return "\n\n".join(parts)
