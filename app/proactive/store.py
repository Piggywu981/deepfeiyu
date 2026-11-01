"""主动搭话持久层：每会话掷骰状态、日计数与发送记录。

读走 db.read_conn()、写走 db.transaction()；SQL 异常不在这里吞掉，
由服务/调度层决定降级，避免「写入失败却以为已记账」。
"""
from __future__ import annotations

import json
from typing import Any

from .. import db

STATE_DEFAULTS: dict[str, Any] = {
    "last_roll_at": 0.0,
    "last_self_at": 0.0,
    "misses": 0,
    "day": "",
    "sent_today": 0,
}


def _state_row(row: Any) -> dict[str, Any]:
    return {
        "account_id": row["account_id"],
        "chat_uid": row["chat_uid"],
        "last_roll_at": float(row["last_roll_at"] or 0),
        "last_self_at": float(row["last_self_at"] or 0),
        "misses": int(row["misses"] or 0),
        "day": row["day"] or "",
        "sent_today": int(row["sent_today"] or 0),
    }


def _json_obj(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def get_state(account_id: str, chat_uid: str) -> dict[str, Any]:
    row = db.read_conn().execute(
        "SELECT * FROM proactive_state WHERE account_id=? AND chat_uid=?",
        (account_id, chat_uid),
    ).fetchone()
    if row:
        return _state_row(row)
    return {"account_id": account_id, "chat_uid": chat_uid, **STATE_DEFAULTS}


def list_states(account_id: str) -> list[dict[str, Any]]:
    rows = db.read_conn().execute(
        "SELECT * FROM proactive_state WHERE account_id=?"
        " ORDER BY last_roll_at DESC, chat_uid ASC",
        (account_id,),
    ).fetchall()
    return [_state_row(r) for r in rows]


def _read_state(conn: Any, account_id: str, chat_uid: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM proactive_state WHERE account_id=? AND chat_uid=?",
        (account_id, chat_uid),
    ).fetchone()
    return _state_row(row) if row else {
        "account_id": account_id, "chat_uid": chat_uid, **STATE_DEFAULTS
    }


def record_roll(account_id: str, chat_uid: str, at: float, hit: bool) -> dict[str, Any]:
    """记录一次掷骰；命中清零连败，未命中累加，供下一次命中线加成。"""
    flag = 1 if hit else 0
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO proactive_state(account_id, chat_uid, last_roll_at, misses)
               VALUES(?,?,?,?)
               ON CONFLICT(account_id, chat_uid) DO UPDATE SET
                   last_roll_at=excluded.last_roll_at,
                   misses=CASE WHEN ? THEN 0 ELSE proactive_state.misses+1 END""",
            (account_id, chat_uid, float(at), 0 if flag else 1, flag),
        )
        return _read_state(conn, account_id, chat_uid)


def record_sent(
    account_id: str, chat_uid: str, at: float, day: str, count: bool = True
) -> dict[str, Any]:
    """记录一次主动开口；跨日先清零再计数，count=False 只更新开口时间。"""
    day = str(day or "")
    flag = 1 if count else 0
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO proactive_state(account_id, chat_uid, last_self_at, day, sent_today)
               VALUES(?,?,?,?,?)
               ON CONFLICT(account_id, chat_uid) DO UPDATE SET
                   last_self_at=excluded.last_self_at,
                   sent_today=CASE WHEN proactive_state.day<>excluded.day
                                   THEN 0 ELSE proactive_state.sent_today END + ?,
                   day=excluded.day""",
            (account_id, chat_uid, float(at), day, flag, flag),
        )
        return _read_state(conn, account_id, chat_uid)


def account_sent_today(account_id: str, day: str) -> int:
    row = db.read_conn().execute(
        "SELECT COALESCE(SUM(sent_today),0) AS n FROM proactive_state"
        " WHERE account_id=? AND day=?",
        (account_id, str(day or "")),
    ).fetchone()
    return int(row["n"] if row else 0)


def add_event(
    account_id: str,
    chat_uid: str,
    at: float,
    trigger: str,
    hit_line: float,
    roll: float,
    hit: bool,
    status: str,
    message: str = "",
    error: str = "",
    meta: dict[str, Any] | None = None,
) -> int:
    """写一条搭话记录，返回自增 id；正文/错误按列用途截断，防止异常长文本撑库。"""
    with db.transaction() as conn:
        cur = conn.execute(
            """INSERT INTO proactive_events(account_id, chat_uid, at, trigger, hit_line,
                   roll, hit, status, message, error, meta)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id,
                str(chat_uid),
                float(at),
                str(trigger or "")[:20],
                float(hit_line or 0),
                float(roll or 0),
                1 if hit else 0,
                str(status or "")[:20],
                str(message or "")[:2000],
                str(error or "")[:1000],
                json.dumps(meta if isinstance(meta, dict) else {}, ensure_ascii=False),
            ),
        )
        return int(cur.lastrowid or 0)


def list_events(account_id: str, limit: int = 50, before: float = 0) -> list[dict[str, Any]]:
    sql = "SELECT * FROM proactive_events WHERE account_id=?"
    params: list[Any] = [account_id]
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = 50
    if before:
        try:
            before_at = float(before)
        except (TypeError, ValueError):
            before_at = 0.0
        if before_at > 0:
            sql += " AND at<?"
            params.append(before_at)
    sql += " ORDER BY at DESC, id DESC LIMIT ?"
    params.append(max(1, min(200, cap)))
    rows = db.read_conn().execute(sql, tuple(params)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["meta"] = _json_obj(item.get("meta"))
        out.append(item)
    return out
