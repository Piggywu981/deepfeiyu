"""成长账本：状态、特质、事件、运行记录与动态召回。

所有写操作走 db.transaction()；growth 子系统不直接碰 db._conn。
"""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from .. import db
from ..ai import memory
from .sanitize import clean_single_line, content_hash, normalize_text

VALID_KINDS = {
    "voice",
    "opinion",
    "value",
    "preference",
    "relationship",
    "habit",
    "narrative",
    "joke",
    "other",
}
VALID_SCOPES = {"global", "chat"}
VALID_STATUS = {"seed", "active", "dormant", "retired"}
MAX_TRAIT_CHARS = 200


def _now() -> float:
    return time.time()


def _json_load(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _trait(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["evidence"] = _json_load(out.get("evidence"), [])
    strength = out.get("strength")
    confidence = out.get("confidence")
    out["strength"] = int(strength) if strength is not None else 2
    out["confidence"] = float(confidence) if confidence is not None else 0.7
    return out


def _tags(value: Any) -> str:
    if isinstance(value, str):
        parts = re.split(r"[,，、]", value)
    elif isinstance(value, list):
        parts = [str(x) for x in value]
    else:
        parts = []
    out: list[str] = []
    for part in parts:
        text = normalize_text(part)[:30]
        if text and text not in out:
            out.append(text)
        if len(out) >= 20:
            break
    return ",".join(out)


def _merge_evidence(old: list[dict], new: list[dict]) -> list[dict]:
    out = [x for x in (old or []) if isinstance(x, dict)]
    seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in out}
    for item in new or []:
        if not isinstance(item, dict):
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _normalize_trait(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "other").strip()
    if kind not in VALID_KINDS:
        kind = "other"
    scope = str(payload.get("scope") or "global").strip()
    if scope not in VALID_SCOPES:
        scope = "global"
    chat_uid = str(payload.get("chat_uid") or "").strip()[:200]
    if scope == "chat" and not chat_uid:
        scope = "global"
    content = clean_single_line(str(payload.get("content") or ""))
    if len(content) > MAX_TRAIT_CHARS:
        raise ValueError(f"特质超过 {MAX_TRAIT_CHARS} 字")
    if not content:
        raise ValueError("特质内容为空")
    status = str(payload.get("status") or "active").strip()
    if status not in VALID_STATUS:
        status = "active"
    source = "owner" if str(payload.get("source") or "auto").strip() == "owner" else "auto"
    confidence_raw = payload.get("confidence")
    confidence = (
        0.7
        if confidence_raw is None
        else max(0.0, min(1.0, float(confidence_raw)))
    )
    strength_raw = payload.get("strength")
    strength = 2 if strength_raw is None else max(1, min(3, int(strength_raw)))
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    now = _now()
    return {
        "kind": kind,
        "content": content,
        "tags": _tags(payload.get("tags")),
        "scope": scope,
        "chat_uid": chat_uid,
        "strength": strength,
        "confidence": confidence,
        "status": status,
        "source": source,
        "evidence": evidence,
        "content_hash": content_hash(kind, scope, chat_uid, content),
        "created_at": now,
        "updated_at": now,
        "last_seen_at": now,
        "retired_at": now if status == "retired" else 0.0,
    }


def get_state(account_id: str) -> dict[str, Any]:
    """只读；没有状态行时返回空默认，避免关闭成长时写库。"""
    row = db.read_conn().execute(
        "SELECT * FROM growth_state WHERE account_id=?", (account_id,)
    ).fetchone()
    if row:
        return dict(row)
    return {
        "account_id": account_id,
        "self_summary": "",
        "summary_run_id": 0,
        "summary_updated_at": 0.0,
        "updated_at": 0.0,
    }


def _set_summary_conn(
    conn: Any, account_id: str, summary: str, run_id: int = 0
) -> dict[str, Any]:
    summary = normalize_text(summary)
    current = conn.execute(
        "SELECT self_summary FROM growth_state WHERE account_id=?", (account_id,)
    ).fetchone()
    before = str(current["self_summary"] or "") if current else ""
    now = _now()
    conn.execute(
        """INSERT INTO growth_state(account_id, self_summary, summary_run_id,
               summary_updated_at, updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(account_id) DO UPDATE SET
               self_summary=excluded.self_summary,
               summary_run_id=excluded.summary_run_id,
               summary_updated_at=excluded.summary_updated_at,
               updated_at=excluded.updated_at""",
        (account_id, summary, int(run_id or 0), now, now),
    )
    return {"before": before, "after": summary}


def set_summary(account_id: str, summary: str, run_id: int = 0) -> dict[str, Any]:
    with db.transaction() as conn:
        return _set_summary_conn(conn, account_id, summary, run_id)


def _upsert_trait_conn(
    conn: Any, account_id: str, payload: dict[str, Any], owner_edit: bool = False
) -> dict[str, Any]:
    data = _normalize_trait(payload)
    existing = conn.execute(
        "SELECT * FROM growth_traits WHERE account_id=? AND content_hash=?",
        (account_id, data["content_hash"]),
    ).fetchone()
    if existing:
        before = _trait(existing)
        # 号主手写的特质：自动反思只能追加证据，不能改内容/强度/状态
        if before["source"] == "owner" and not owner_edit:
            evidence = _merge_evidence(before["evidence"], data["evidence"])
            now = _now()
            conn.execute(
                "UPDATE growth_traits SET evidence=?, last_seen_at=?, updated_at=? WHERE id=?",
                (json.dumps(evidence, ensure_ascii=False), now, now, before["id"]),
            )
            row = conn.execute(
                "SELECT * FROM growth_traits WHERE id=?", (before["id"],)
            ).fetchone()
            return {"trait": _trait(row), "action": "updated", "before": before}

        status = before["status"]
        if data["status"] == "active" and status in ("seed", "dormant", "retired"):
            status = "active"
        elif status == "active" and data["status"] == "seed":
            status = "active"
        evidence = _merge_evidence(before["evidence"], data["evidence"])
        tags = _tags(f"{before['tags']},{data['tags']}")
        strength = max(before["strength"], data["strength"])
        confidence = max(before["confidence"], data["confidence"])
        now = _now()
        conn.execute(
            """UPDATE growth_traits
               SET tags=?, strength=?, confidence=?, status=?, evidence=?,
                   last_seen_at=?, updated_at=?, retired_at=?
               WHERE id=?""",
            (
                tags,
                strength,
                confidence,
                status,
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
                0.0 if status != "retired" else before.get("retired_at") or now,
                before["id"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM growth_traits WHERE id=?", (before["id"],)
        ).fetchone()
        action = "reactivated" if before["status"] == "retired" and status == "active" else "updated"
        return {"trait": _trait(row), "action": action, "before": before}

    cur = conn.execute(
        """INSERT INTO growth_traits(
               account_id, kind, content, tags, scope, chat_uid, strength,
               confidence, status, source, evidence, content_hash, created_at,
               updated_at, last_seen_at, retired_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            account_id,
            data["kind"],
            data["content"],
            data["tags"],
            data["scope"],
            data["chat_uid"],
            data["strength"],
            data["confidence"],
            data["status"],
            data["source"],
            json.dumps(data["evidence"], ensure_ascii=False),
            data["content_hash"],
            data["created_at"],
            data["updated_at"],
            data["last_seen_at"],
            data["retired_at"],
        ),
    )
    row = conn.execute(
        "SELECT * FROM growth_traits WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return {"trait": _trait(row), "action": "added", "before": None}


def upsert_trait(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db.transaction() as conn:
        return _upsert_trait_conn(conn, account_id, payload)


def _update_trait_conn(
    conn: Any, account_id: str, trait_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM growth_traits WHERE account_id=? AND id=?",
        (account_id, int(trait_id)),
    ).fetchone()
    if not row:
        return None
    before = _trait(row)
    merged = dict(before)
    for key in (
        "kind",
        "content",
        "tags",
        "scope",
        "chat_uid",
        "strength",
        "confidence",
        "status",
        "source",
        "evidence",
    ):
        if key in fields and fields[key] is not None:
            merged[key] = fields[key]
    data = _normalize_trait(merged)
    conflict = conn.execute(
        "SELECT id FROM growth_traits WHERE account_id=? AND content_hash=? AND id<>?",
        (account_id, data["content_hash"], before["id"]),
    ).fetchone()
    if conflict:
        raise ValueError("修改后的内容与另一条特质重复")
    now = _now()
    conn.execute(
        """UPDATE growth_traits
           SET kind=?, content=?, tags=?, scope=?, chat_uid=?, strength=?,
               confidence=?, status=?, source=?, evidence=?, content_hash=?,
               updated_at=?, last_seen_at=?, retired_at=?
           WHERE account_id=? AND id=?""",
        (
            data["kind"],
            data["content"],
            data["tags"],
            data["scope"],
            data["chat_uid"],
            data["strength"],
            data["confidence"],
            data["status"],
            data["source"],
            json.dumps(data["evidence"], ensure_ascii=False),
            data["content_hash"],
            now,
            now,
            0.0 if data["status"] != "retired" else before.get("retired_at") or now,
            account_id,
            before["id"],
        ),
    )
    fresh = conn.execute(
        "SELECT * FROM growth_traits WHERE id=?", (before["id"],)
    ).fetchone()
    return {"trait": _trait(fresh), "before": before}


def update_trait(
    account_id: str, trait_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    with db.transaction() as conn:
        return _update_trait_conn(conn, account_id, trait_id, fields)


def _set_trait_status_conn(
    conn: Any, account_id: str, trait_id: int, status: str
) -> dict[str, Any] | None:
    if status not in VALID_STATUS:
        raise ValueError("非法状态")
    row = conn.execute(
        "SELECT * FROM growth_traits WHERE account_id=? AND id=?",
        (account_id, int(trait_id)),
    ).fetchone()
    if not row:
        return None
    before = _trait(row)
    now = _now()
    conn.execute(
        "UPDATE growth_traits SET status=?, retired_at=?, updated_at=? WHERE id=?",
        (status, now if status == "retired" else 0.0, now, before["id"]),
    )
    fresh = conn.execute(
        "SELECT * FROM growth_traits WHERE id=?", (before["id"],)
    ).fetchone()
    return {"trait": _trait(fresh), "before": before}


def set_trait_status(
    account_id: str, trait_id: int, status: str
) -> dict[str, Any] | None:
    with db.transaction() as conn:
        return _set_trait_status_conn(conn, account_id, trait_id, status)


def get_trait(account_id: str, trait_id: int) -> dict[str, Any] | None:
    row = db.read_conn().execute(
        "SELECT * FROM growth_traits WHERE account_id=? AND id=?",
        (account_id, int(trait_id)),
    ).fetchone()
    return _trait(row) if row else None


def list_traits(
    account_id: str,
    q: str = "",
    kind: str = "",
    status: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM growth_traits WHERE account_id=?"
    params: list[Any] = [account_id]
    if q:
        sql += " AND (content LIKE ? OR tags LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like])
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += (
        " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'seed' THEN 1"
        " WHEN 'dormant' THEN 2 ELSE 3 END, updated_at DESC, id DESC"
        " LIMIT ? OFFSET ?"
    )
    params.extend([max(1, min(500, int(limit))), max(0, int(offset))])
    rows = db.read_conn().execute(sql, tuple(params)).fetchall()
    return [_trait(r) for r in rows]


def list_active_traits(
    account_id: str, chat_uid: str | None = ""
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM growth_traits WHERE account_id=? AND status='active'"
    params: list[Any] = [account_id]
    if chat_uid is None:
        pass
    elif chat_uid:
        sql += " AND (scope='global' OR (scope='chat' AND chat_uid=?))"
        params.append(chat_uid)
    else:
        sql += " AND scope='global'"
    sql += " ORDER BY strength DESC, last_seen_at DESC, id DESC"
    rows = db.read_conn().execute(sql, tuple(params)).fetchall()
    return [_trait(r) for r in rows]


def _upsert_journal_conn(
    conn: Any, account_id: str, day: str, content: str, run_id: int = 0
) -> dict[str, Any]:
    content = normalize_text(content)
    if not content:
        raise ValueError("日记为空")
    if len(content) > 1000:
        content = content[:1000]
    today = str(day or time.strftime("%Y-%m-%d"))
    existing = conn.execute(
        "SELECT * FROM growth_journal WHERE account_id=? AND day=?",
        (account_id, today),
    ).fetchone()
    now = _now()
    if existing:
        before = dict(existing)
        conn.execute(
            "UPDATE growth_journal SET content=?, run_id=?, updated_at=? WHERE id=?",
            (content, int(run_id or 0), now, before["id"]),
        )
        row = conn.execute(
            "SELECT * FROM growth_journal WHERE id=?", (before["id"],)
        ).fetchone()
        return {"journal": dict(row), "action": "updated", "before": before}
    cur = conn.execute(
        "INSERT INTO growth_journal(account_id, day, content, run_id, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (account_id, today, content, int(run_id or 0), now, now),
    )
    row = conn.execute(
        "SELECT * FROM growth_journal WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return {"journal": dict(row), "action": "added", "before": None}


def upsert_journal(
    account_id: str, day: str, content: str, run_id: int = 0
) -> dict[str, Any]:
    with db.transaction() as conn:
        return _upsert_journal_conn(conn, account_id, day, content, run_id)


def list_journal(
    account_id: str, limit: int = 30, before_day: str = ""
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM growth_journal WHERE account_id=?"
    params: list[Any] = [account_id]
    if before_day:
        sql += " AND day<?"
        params.append(before_day)
    sql += " ORDER BY day DESC, id DESC LIMIT ?"
    params.append(max(1, min(400, int(limit))))
    return [dict(r) for r in db.read_conn().execute(sql, tuple(params)).fetchall()]


def get_journal(account_id: str, journal_id: int) -> dict[str, Any] | None:
    row = db.read_conn().execute(
        "SELECT * FROM growth_journal WHERE account_id=? AND id=?",
        (account_id, int(journal_id)),
    ).fetchone()
    return dict(row) if row else None


def delete_journal(account_id: str, journal_id: int) -> dict[str, Any] | None:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM growth_journal WHERE account_id=? AND id=?",
            (account_id, int(journal_id)),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM growth_journal WHERE id=?", (row["id"],))
    return {"journal": dict(row)}


def _add_goal_conn(
    conn: Any, account_id: str, content: str, source: str = "auto", due_at: float = 0
) -> dict[str, Any]:
    content = normalize_text(content)
    if not content:
        raise ValueError("目标为空")
    existing = conn.execute(
        "SELECT * FROM growth_goals WHERE account_id=? AND status='open' AND content=?",
        (account_id, content),
    ).fetchone()
    now = _now()
    if existing:
        conn.execute(
            "UPDATE growth_goals SET updated_at=?, due_at=? WHERE id=?",
            (now, float(due_at or 0), existing["id"]),
        )
        row = conn.execute(
            "SELECT * FROM growth_goals WHERE id=?", (existing["id"],)
        ).fetchone()
        return dict(row)
    cur = conn.execute(
        "INSERT INTO growth_goals(account_id, content, status, progress, due_at, source, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (account_id, content, "open", "", float(due_at or 0), source, now, now),
    )
    row = conn.execute(
        "SELECT * FROM growth_goals WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


def add_goal(
    account_id: str, content: str, source: str = "auto", due_at: float = 0
) -> dict[str, Any]:
    with db.transaction() as conn:
        return _add_goal_conn(conn, account_id, content, source, due_at)


def list_goals(account_id: str, status: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM growth_goals WHERE account_id=?"
    params: list[Any] = [account_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, id DESC"
    return [dict(r) for r in db.read_conn().execute(sql, tuple(params)).fetchall()]


def _update_goal_conn(
    conn: Any, account_id: str, goal_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM growth_goals WHERE account_id=? AND id=?",
        (account_id, int(goal_id)),
    ).fetchone()
    if not row:
        return None
    before = dict(row)
    merged = dict(before)
    for key in ("content", "status", "progress", "due_at"):
        if key in fields and fields[key] is not None:
            merged[key] = fields[key]
    if merged["status"] not in ("open", "done", "dropped"):
        merged["status"] = before["status"]
    content = normalize_text(str(merged["content"] or ""))
    if not content:
        raise ValueError("目标为空")
    now = _now()
    conn.execute(
        "UPDATE growth_goals SET content=?, status=?, progress=?, due_at=?, updated_at=? WHERE id=?",
        (
            content,
            merged["status"],
            normalize_text(str(merged.get("progress") or ""))[:500],
            float(merged.get("due_at") or 0),
            now,
            before["id"],
        ),
    )
    fresh = conn.execute(
        "SELECT * FROM growth_goals WHERE id=?", (before["id"],)
    ).fetchone()
    return {"goal": dict(fresh), "before": before}


def update_goal(
    account_id: str, goal_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    with db.transaction() as conn:
        return _update_goal_conn(conn, account_id, goal_id, fields)


def _create_proposal_conn(
    conn: Any,
    account_id: str,
    kind: str,
    payload: dict[str, Any],
    before: Any = None,
    reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    risk: str = "high",
) -> dict[str, Any]:
    payload_text = json.dumps(payload or {}, ensure_ascii=False)
    existing = conn.execute(
        "SELECT * FROM growth_proposals WHERE account_id=? AND kind=? AND status='pending' AND payload=?",
        (account_id, kind, payload_text),
    ).fetchone()
    if existing:
        return _proposal_row(existing)
    cur = conn.execute(
        """INSERT INTO growth_proposals(account_id, kind, risk, payload, before, reason,
               evidence, status, created_at)
           VALUES(?,?,?,?,?,?,?, 'pending', ?)""",
        (
            account_id,
            kind,
            risk,
            payload_text,
            json.dumps(before, ensure_ascii=False) if before is not None else "",
            normalize_text(reason)[:1000],
            json.dumps(evidence or [], ensure_ascii=False),
            _now(),
        ),
    )
    row = conn.execute(
        "SELECT * FROM growth_proposals WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return _proposal_row(row)


def _proposal_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["payload"] = _json_load(out.get("payload"), {})
    out["before"] = _json_load(out.get("before"), None)
    out["evidence"] = _json_load(out.get("evidence"), [])
    return out


def create_proposal(
    account_id: str,
    kind: str,
    payload: dict[str, Any],
    before: Any = None,
    reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    risk: str = "high",
) -> dict[str, Any]:
    with db.transaction() as conn:
        return _create_proposal_conn(
            conn, account_id, kind, payload, before, reason, evidence, risk
        )


def list_proposals(
    account_id: str, status: str = "pending", limit: int = 100
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM growth_proposals WHERE account_id=?"
    params: list[Any] = [account_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit))))
    return [_proposal_row(r) for r in db.read_conn().execute(sql, tuple(params)).fetchall()]


def get_proposal(account_id: str, proposal_id: int) -> dict[str, Any] | None:
    row = db.read_conn().execute(
        "SELECT * FROM growth_proposals WHERE account_id=? AND id=?",
        (account_id, int(proposal_id)),
    ).fetchone()
    return _proposal_row(row) if row else None


def decide_proposal(
    account_id: str, proposal_id: int, status: str, decided_by: str = "owner"
) -> dict[str, Any] | None:
    if status not in ("approved", "rejected", "applied", "failed"):
        raise ValueError("非法提案状态")
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM growth_proposals WHERE account_id=? AND id=?",
            (account_id, int(proposal_id)),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE growth_proposals SET status=?, decided_by=?, decided_at=? WHERE id=?",
            (status, decided_by, _now(), row["id"]),
        )
        fresh = conn.execute(
            "SELECT * FROM growth_proposals WHERE id=?", (row["id"],)
        ).fetchone()
    return _proposal_row(fresh)


def _add_event_conn(
    conn: Any,
    account_id: str,
    kind: str,
    entity_type: str = "",
    entity_id: int = 0,
    before: Any = None,
    after: Any = None,
    proposal_id: int = 0,
    reversible: bool = True,
) -> dict[str, Any]:
    cur = conn.execute(
        """INSERT INTO growth_events(account_id, kind, entity_type, entity_id,
               before, after, proposal_id, reversible, reverted_by, created_at)
           VALUES(?,?,?,?,?,?,?,?,0,?)""",
        (
            account_id,
            kind,
            entity_type,
            int(entity_id or 0),
            json.dumps(before, ensure_ascii=False) if before is not None else "",
            json.dumps(after, ensure_ascii=False) if after is not None else "",
            int(proposal_id or 0),
            1 if reversible else 0,
            _now(),
        ),
    )
    row = conn.execute(
        "SELECT * FROM growth_events WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return _event_row(row)


def _event_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["before"] = _json_load(out.get("before"), None)
    out["after"] = _json_load(out.get("after"), None)
    out["reversible"] = bool(out.get("reversible"))
    return out


def add_growth_event(
    account_id: str,
    kind: str,
    entity_type: str = "",
    entity_id: int = 0,
    before: Any = None,
    after: Any = None,
    proposal_id: int = 0,
    reversible: bool = True,
) -> dict[str, Any]:
    with db.transaction() as conn:
        return _add_event_conn(
            conn, account_id, kind, entity_type, entity_id, before, after, proposal_id, reversible
        )


def list_events(
    account_id: str, limit: int = 100, before_id: int = 0
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM growth_events WHERE account_id=?"
    params: list[Any] = [account_id]
    if before_id:
        sql += " AND id<?"
        params.append(int(before_id))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit))))
    return [_event_row(r) for r in db.read_conn().execute(sql, tuple(params)).fetchall()]


def get_event(account_id: str, event_id: int) -> dict[str, Any] | None:
    row = db.read_conn().execute(
        "SELECT * FROM growth_events WHERE account_id=? AND id=?",
        (account_id, int(event_id)),
    ).fetchone()
    return _event_row(row) if row else None


def start_run(account_id: str, trigger: str, day: str) -> dict[str, Any]:
    with db.transaction() as conn:
        cur = conn.execute(
            """INSERT INTO growth_runs(account_id, day, trigger, status, started_at)
               VALUES(?,?,?, 'running', ?)""",
            (account_id, str(day), str(trigger), _now()),
        )
        row = conn.execute(
            "SELECT * FROM growth_runs WHERE id=?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def _run_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["stats"] = _json_load(out.get("stats"), {})
    out["cost"] = float(out.get("cost") or 0)
    return out


def finish_run(
    run_id: int,
    status: str,
    stats: dict[str, Any] | None = None,
    cost: float = 0.0,
    error: str = "",
) -> dict[str, Any] | None:
    if status not in ("ok", "error", "skipped"):
        raise ValueError("非法运行状态")
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM growth_runs WHERE id=?", (int(run_id),)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE growth_runs SET status=?, finished_at=?, stats=?, cost=?, error=? WHERE id=?",
            (
                status,
                _now(),
                json.dumps(stats or {}, ensure_ascii=False),
                float(cost or 0),
                (error or "")[:1000],
                row["id"],
            ),
        )
        fresh = conn.execute(
            "SELECT * FROM growth_runs WHERE id=?", (row["id"],)
        ).fetchone()
    return _run_row(fresh)


def list_runs(account_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.read_conn().execute(
        "SELECT * FROM growth_runs WHERE account_id=? ORDER BY started_at DESC, id DESC LIMIT ?",
        (account_id, max(1, min(200, int(limit)))),
    ).fetchall()
    return [_run_row(r) for r in rows]


def last_successful_run(account_id: str) -> dict[str, Any] | None:
    row = db.read_conn().execute(
        "SELECT * FROM growth_runs WHERE account_id=? AND status='ok'"
        " ORDER BY finished_at DESC, id DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    return _run_row(row) if row else None


def count_runs_today(account_id: str, day: str, trigger: str | None = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM growth_runs WHERE account_id=? AND day=?"
    params: list[Any] = [account_id, str(day)]
    if trigger:
        sql += " AND trigger=?"
        params.append(trigger)
    row = db.read_conn().execute(sql, tuple(params)).fetchone()
    return int(row["n"] if row else 0)


def mark_stale_runs(account_id: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE growth_runs SET status='error', error='进程中断', finished_at=?"
            " WHERE account_id=? AND status='running'",
            (_now(), account_id),
        )
        return int(cur.rowcount or 0)


def _recency(ts: float) -> float:
    days = max(0.0, (_now() - float(ts or 0)) / 86400)
    return math.exp(-days / 30.0)


def recall_traits(
    account_id: str,
    query: str = "",
    chat_uid: str = "",
    chat_name: str = "",
    limit_chars: int = 1200,
    include_chat_scoped: bool = True,
) -> list[dict[str, Any]]:
    rows = list_active_traits(account_id, chat_uid if include_chat_scoped else "")
    q = (query or "").strip()
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if row["kind"] == "relationship" and row["scope"] == "chat":
            if not include_chat_scoped or row["chat_uid"] != chat_uid:
                continue
        kw = memory.keyword_score(q, row["content"]) if q else 0.0
        tag = memory.keyword_score(q, row["tags"]) if q and row["tags"] else 0.0
        rel = (
            1.0
            if row["kind"] == "relationship"
            and chat_name
            and (chat_name in row["content"] or chat_name in row["tags"])
            else 0.0
        )
        rec = _recency(float(row.get("last_seen_at") or row.get("updated_at") or 0))
        score = (
            0.55 * kw
            + 0.20 * (row["strength"] / 3.0)
            + 0.15 * rec
            + 0.10 * tag
            + 0.10 * rel
        )
        scored.append((score, row))
    scored.sort(key=lambda pair: (pair[0], pair[1].get("updated_at") or 0), reverse=True)
    budget = max(200, int(limit_chars or 1200))
    out: list[dict[str, Any]] = []
    used = 0
    per_kind: dict[str, int] = {}
    for score, row in scored:
        if per_kind.get(row["kind"], 0) >= 4:
            continue
        cost = len(row["content"]) + 2
        if used + cost > budget:
            continue
        item = dict(row)
        item["_score"] = round(score, 4)
        out.append(item)
        used += cost
        per_kind[row["kind"]] = per_kind.get(row["kind"], 0) + 1
    return out


def export_json(account_id: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "exported_at": _now(),
        "state": get_state(account_id),
        "traits": list_traits(account_id, limit=500),
        "journal": list_journal(account_id, limit=400),
        "goals": list_goals(account_id),
        "proposals": list_proposals(account_id, status="", limit=500),
        "events": list_events(account_id, limit=500),
        "runs": list_runs(account_id, limit=100),
    }


def export_markdown(account_id: str) -> str:
    data = export_json(account_id)
    lines = ["# 自我成长导出", "", f"账号：{account_id}", ""]
    lines += ["## 自我总览", "", data["state"].get("self_summary") or "（空）", ""]
    lines += ["## 特质", ""]
    if data["traits"]:
        for t in data["traits"]:
            scope = f" @{t['chat_uid']}" if t.get("scope") == "chat" else ""
            lines.append(
                f"- [{t['kind']}/{t['status']}] {t['content']}"
                f"（强度 {t['strength']}，置信 {t['confidence']:.2f}{scope}）"
            )
    else:
        lines.append("（空）")
    lines += ["", "## 目标", ""]
    if data["goals"]:
        for g in data["goals"]:
            lines.append(f"- [{g['status']}] {g['content']} {g.get('progress') or ''}".rstrip())
    else:
        lines.append("（空）")
    lines += ["", "## 日记", ""]
    if data["journal"]:
        for j in data["journal"]:
            lines += [f"### {j['day']}", "", j["content"], ""]
    else:
        lines.append("（空）")
    lines += ["", "## 时间线", ""]
    if data["events"]:
        for e in data["events"]:
            lines.append(f"- #{e['id']} [{e['kind']}] {json.dumps(e.get('after'), ensure_ascii=False)[:200]}")
    else:
        lines.append("（空）")
    return "\n".join(lines).strip() + "\n"


def apply_reflection(
    account_id: str, run_id: int, changes: dict[str, Any]
) -> dict[str, int]:
    """把分级后的变化在一个事务里落库；所有变化写审计事件。"""
    stats = {
        "trait_add": 0,
        "trait_update": 0,
        "trait_retire": 0,
        "trait_seed": 0,
        "proposal": 0,
        "reject": 0,
        "journal": 0,
        "goal": 0,
        "summary": 0,
    }
    with db.transaction() as conn:
        for change in changes.get("traits", []):
            decision = str(change.get("decision") or "reject")
            item = change.get("item") or {}
            payload = dict(change.get("payload") or {})
            if decision in ("auto", "seed"):
                op = str(payload.get("op") or "add")
                if op == "retire":
                    res = _set_trait_status_conn(
                        conn, account_id, int(payload.get("id") or 0), "retired"
                    )
                    if res:
                        _add_event_conn(
                            conn, account_id, "trait.retire", "trait",
                            res["trait"]["id"], before=res["before"], after=res["trait"],
                        )
                        stats["trait_retire"] += 1
                    continue
                if op == "update" and payload.get("id"):
                    fields = {
                        key: payload.get(key)
                        for key in (
                            "kind", "content", "tags", "scope", "chat_uid",
                            "strength", "confidence", "status", "source", "evidence",
                        )
                        if key in payload
                    }
                    try:
                        res = _update_trait_conn(
                            conn, account_id, int(payload["id"]), fields
                        )
                    except ValueError as exc:
                        _add_event_conn(
                            conn, account_id, "trait.reject", "trait",
                            int(payload.get("id") or 0), before=item,
                            after={"reason": str(exc)}, reversible=False,
                        )
                        stats["reject"] += 1
                        continue
                    if res:
                        _add_event_conn(
                            conn, account_id, "trait.update", "trait",
                            res["trait"]["id"], before=res["before"], after=res["trait"],
                        )
                        stats["trait_update"] += 1
                    continue
                payload["source"] = "auto"
                try:
                    res = _upsert_trait_conn(conn, account_id, payload)
                except ValueError as exc:
                    _add_event_conn(
                        conn, account_id, "trait.reject", "trait", 0,
                        before=item, after={"reason": str(exc)}, reversible=False,
                    )
                    stats["reject"] += 1
                    continue
                status = res["trait"]["status"]
                if status == "seed":
                    event_kind = "trait.seed"
                    stats["trait_seed"] += 1
                elif res["action"] == "added":
                    event_kind = "trait.add"
                    stats["trait_add"] += 1
                else:
                    event_kind = "trait.update"
                    stats["trait_update"] += 1
                _add_event_conn(
                    conn, account_id, event_kind, "trait",
                    res["trait"]["id"], before=res["before"], after=res["trait"],
                )
            elif decision == "proposal":
                prop = _create_proposal_conn(
                    conn, account_id, "trait", payload,
                    before=change.get("before"),
                    reason=str(change.get("reason") or ""),
                    evidence=change.get("evidence") or [],
                )
                _add_event_conn(
                    conn, account_id, "proposal.create", "proposal",
                    prop["id"], after=prop, reversible=False,
                )
                stats["proposal"] += 1
            else:
                _add_event_conn(
                    conn, account_id, "trait.reject", "trait", 0,
                    before=item, after={"reason": change.get("reason") or ""},
                    reversible=False,
                )
                stats["reject"] += 1

        journal = changes.get("journal")
        if journal and journal.get("content"):
            res = _upsert_journal_conn(
                conn, account_id, str(journal.get("day") or ""),
                str(journal["content"]), int(run_id),
            )
            _add_event_conn(
                conn, account_id,
                "journal.add" if res["action"] == "added" else "journal.update",
                "journal", res["journal"]["id"], before=res["before"], after=res["journal"],
            )
            stats["journal"] += 1

        summary = str(changes.get("summary") or "").strip()
        if summary:
            res = _set_summary_conn(conn, account_id, summary, int(run_id))
            _add_event_conn(
                conn, account_id, "summary.update", "state", 0,
                before={"self_summary": res["before"]},
                after={"self_summary": res["after"]},
            )
            stats["summary"] += 1

        for goal in changes.get("goals", []):
            op = str(goal.get("op") or "add")
            if op == "add":
                try:
                    row = _add_goal_conn(conn, account_id, str(goal.get("content") or ""))
                except ValueError:
                    continue
                _add_event_conn(conn, account_id, "goal.add", "goal", row["id"], after=row)
                stats["goal"] += 1
            elif op in ("update", "done", "drop") and goal.get("id"):
                fields: dict[str, Any] = {
                    "status": "done" if op == "done" else ("dropped" if op == "drop" else "open")
                }
                if goal.get("content"):
                    fields["content"] = goal["content"]
                if goal.get("progress") is not None:
                    fields["progress"] = goal["progress"]
                res = _update_goal_conn(conn, account_id, int(goal["id"]), fields)
                if res:
                    _add_event_conn(
                        conn, account_id, "goal.update", "goal",
                        res["goal"]["id"], before=res["before"], after=res["goal"],
                    )
                    stats["goal"] += 1

        for reject in changes.get("rejects", []):
            kind = str(reject.get("kind") or "trait")
            _add_event_conn(
                conn, account_id, f"{kind}.reject", kind, 0,
                before=reject.get("before"),
                after={"reason": reject.get("reason") or ""},
                reversible=False,
            )
            stats["reject"] += 1
    return stats


def _trait_state(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        data = dict(value)
        if not isinstance(data.get("evidence"), list):
            data = _trait(value)
    else:
        data = _trait(value)
    return {
        key: data.get(key)
        for key in (
            "kind", "content", "tags", "scope", "chat_uid", "strength",
            "confidence", "status", "source", "evidence",
        )
    }


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def rollback_event(account_id: str, event_id: int, force: bool = False) -> dict[str, Any]:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM growth_events WHERE account_id=? AND id=?",
            (account_id, int(event_id)),
        ).fetchone()
        if not row:
            raise KeyError("事件不存在")
        event = _event_row(row)
        if not event["reversible"]:
            raise ValueError("该事件不可回滚")
        kind = str(event["kind"])
        before = event["before"]
        after = event["after"]
        entity_id = int(event["entity_id"] or 0)
        current: Any = None
        restored: Any = None

        if kind.startswith("trait."):
            current_row = conn.execute(
                "SELECT * FROM growth_traits WHERE account_id=? AND id=?",
                (account_id, entity_id),
            ).fetchone()
            if not current_row:
                raise KeyError("特质不存在")
            current = _trait_state(current_row)
            if not force and _canon(current) != _canon(_trait_state(after)):
                raise RuntimeError("事件已被后续变化覆盖")
            if before is None:
                res = _set_trait_status_conn(conn, account_id, entity_id, "retired")
            else:
                fields = {
                    key: before.get(key)
                    for key in (
                        "kind", "content", "tags", "scope", "chat_uid",
                        "strength", "confidence", "status", "source", "evidence",
                    )
                    if key in before
                }
                res = _update_trait_conn(conn, account_id, entity_id, fields)
            restored = res["trait"] if res else None

        elif kind.startswith("journal."):
            current_row = conn.execute(
                "SELECT * FROM growth_journal WHERE account_id=? AND id=?",
                (account_id, entity_id),
            ).fetchone()
            current = dict(current_row) if current_row else None
            if not force and _canon(current) != _canon(after):
                raise RuntimeError("事件已被后续变化覆盖")
            if kind == "journal.add":
                conn.execute(
                    "DELETE FROM growth_journal WHERE account_id=? AND id=?",
                    (account_id, entity_id),
                )
                restored = None
            else:
                res = _upsert_journal_conn(
                    conn, account_id, str((before or {}).get("day") or ""),
                    str((before or {}).get("content") or ""),
                    int((before or {}).get("run_id") or 0),
                )
                restored = res["journal"]

        elif kind.startswith("goal."):
            current_row = conn.execute(
                "SELECT * FROM growth_goals WHERE account_id=? AND id=?",
                (account_id, entity_id),
            ).fetchone()
            current = dict(current_row) if current_row else None
            if not force and _canon(current) != _canon(after):
                raise RuntimeError("事件已被后续变化覆盖")
            if kind == "goal.add":
                res = _update_goal_conn(conn, account_id, entity_id, {"status": "dropped"})
            else:
                res = _update_goal_conn(
                    conn, account_id, entity_id,
                    {
                        key: (before or {}).get(key)
                        for key in ("content", "status", "progress", "due_at")
                        if key in (before or {})
                    },
                )
            restored = res["goal"] if res else None

        elif kind == "summary.update":
            current_row = conn.execute(
                "SELECT self_summary FROM growth_state WHERE account_id=?",
                (account_id,),
            ).fetchone()
            current = {"self_summary": current_row["self_summary"] if current_row else ""}
            if not force and _canon(current) != _canon(after):
                raise RuntimeError("事件已被后续变化覆盖")
            res = _set_summary_conn(
                conn, account_id, str((before or {}).get("self_summary") or ""), 0
            )
            restored = res

        else:
            raise ValueError("该事件类型不支持回滚")

        rollback = _add_event_conn(
            conn, account_id, "rollback", event["entity_type"],
            entity_id, before=current, after=restored, reversible=False,
        )
        conn.execute(
            "UPDATE growth_events SET reverted_by=? WHERE id=?",
            (rollback["id"], event["id"]),
        )
    return {"event": event, "rollback": rollback, "restored": restored}


def consolidate_traits(account_id: str, run_id: int = 0) -> int:
    """同 kind/作用域、内容相似度 ≥0.85 的自动特质合并为一条。"""
    import difflib

    merged = 0
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM growth_traits WHERE account_id=? AND status='active'"
            " AND source='auto' ORDER BY strength DESC, id ASC LIMIT 500",
            (account_id,),
        ).fetchall()
        traits = [_trait(r) for r in rows]
        used: set[int] = set()
        for i, left in enumerate(traits):
            if left["id"] in used:
                continue
            for right in traits[i + 1 :]:
                if right["id"] in used:
                    continue
                if (
                    left["kind"] != right["kind"]
                    or left["scope"] != right["scope"]
                    or left["chat_uid"] != right["chat_uid"]
                ):
                    continue
                if (
                    difflib.SequenceMatcher(
                        None, left["content"], right["content"]
                    ).ratio()
                    < 0.85
                ):
                    continue
                res = _update_trait_conn(
                    conn,
                    account_id,
                    left["id"],
                    {
                        "evidence": _merge_evidence(left["evidence"], right["evidence"]),
                        "strength": max(left["strength"], right["strength"]),
                        "tags": _tags(f"{left['tags']},{right['tags']}"),
                    },
                )
                _add_event_conn(
                    conn, account_id, "trait.update", "trait", left["id"],
                    before=res["before"], after=res["trait"],
                )
                left = res["trait"]
                retired = _set_trait_status_conn(conn, account_id, right["id"], "retired")
                _add_event_conn(
                    conn, account_id, "trait.retire", "trait", right["id"],
                    before=retired["before"], after=retired["trait"],
                )
                used.add(right["id"])
                merged += 1
                break
    return merged


def decay_traits(account_id: str, days: int = 30, run_id: int = 0) -> int:
    days = int(days or 30)
    if days <= 0:
        return 0
    cutoff = _now() - days * 86400
    changed = 0
    with db.transaction() as conn:
        rows = conn.execute(
            """SELECT * FROM growth_traits
               WHERE account_id=? AND status IN ('active','seed') AND source='auto'
                 AND COALESCE(last_seen_at, updated_at, 0) < ?""",
            (account_id, cutoff),
        ).fetchall()
        for row in rows:
            before = _trait(row)
            strength = int(before["strength"]) - 1
            status = before["status"]
            if strength <= 0:
                strength = 0
                status = "dormant"
            conn.execute(
                "UPDATE growth_traits SET strength=?, status=?, updated_at=? WHERE id=?",
                (strength, status, _now(), before["id"]),
            )
            fresh = conn.execute(
                "SELECT * FROM growth_traits WHERE id=?", (before["id"],)
            ).fetchone()
            _add_event_conn(
                conn, account_id, "trait.update", "trait", before["id"],
                before=before, after=_trait(fresh),
            )
            changed += 1
    return changed


def cleanup_journals(account_id: str, keep_days: int) -> int:
    keep = int(keep_days or 0)
    if keep <= 0:
        return 0
    cutoff = time.strftime("%Y-%m-%d", time.localtime(_now() - keep * 86400))
    removed = 0
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM growth_journal WHERE account_id=? AND day<?",
            (account_id, cutoff),
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM growth_journal WHERE id=?", (row["id"],))
            _add_event_conn(
                conn, account_id, "journal.delete", "journal", row["id"],
                before=dict(row), reversible=False,
            )
            removed += 1
    return removed
