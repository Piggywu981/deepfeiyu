"""心情存储：槽位、绑定、时间线。所有写入走 db 的线程连接与写锁。"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import db
from .constants import DIM_KEYS, DEFAULT_MOOD

DEFAULT_SLOT = "default"


def _loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _positive_times(value: Any) -> list[float]:
    """解析时间戳 JSON，逐项跳过非数值脏值，保留正数并截取最近 200 条。"""
    times: list[float] = []
    for item in _loads(value, []):
        try:
            n = float(item)
        except (TypeError, ValueError):
            continue
        if n > 0:
            times.append(n)
    return times[-200:]


def _slot_row(row: Any) -> dict[str, Any]:
    dims = {k: 0.0 for k in DIM_KEYS}
    for k, v in (_loads(row["dims"], {}) or {}).items():
        if k in dims:
            try:
                dims[k] = max(0.0, min(100.0, float(v)))
            except (TypeError, ValueError):
                pass
    return {
        "account_id": row["account_id"],
        "slot_id": row["slot_id"],
        "label": row["label"] or "",
        "self_name": row["self_name"] or "",
        "owner_name": row["owner_name"] or "",
        "self_aliases": row["self_aliases"] or "",
        "mood": float(row["mood"] if row["mood"] is not None else DEFAULT_MOOD),
        "dims": dims,
        "last_reason": row["last_reason"] or "",
        "last_confidence": int(row["last_confidence"] or 0),
        "last_noted_mood": float(
            row["last_noted_mood"] if row["last_noted_mood"] is not None else DEFAULT_MOOD
        ),
        "last_decay_at": float(row["last_decay_at"] or 0.0),
        "injected_for": float(row["injected_for"] or 0.0),
        "last_injected_at": float(row["last_injected_at"] or 0.0),
        "last_injected_text": row["last_injected_text"] or "",
        "said_at": _positive_times(row["said_at"]),
        "minor": int(row["minor"] or 0),
        "kind": row["kind"] or "judge",
        "updated_at": float(row["updated_at"] or 0.0),
        "last_chat_uid": row["last_chat_uid"] or "",
    }


def blank_state(base: float = DEFAULT_MOOD) -> dict[str, Any]:
    """与 load_state 同形状的默认状态；只读展示用，不落库、不建槽。"""
    return {
        "account_id": "",
        "slot_id": "",
        "label": "",
        "self_name": "",
        "owner_name": "",
        "self_aliases": "",
        "mood": float(base),
        "dims": {k: 0.0 for k in DIM_KEYS},
        "last_reason": "",
        "last_confidence": 0,
        "last_noted_mood": float(base),
        "last_decay_at": 0.0,
        "injected_for": 0.0,
        "last_injected_at": 0.0,
        "last_injected_text": "",
        "said_at": [],
        "minor": 0,
        "kind": "judge",
        "updated_at": 0.0,
        "last_chat_uid": "",
    }


def get_slot(account_id: str, slot_id: str) -> dict[str, Any] | None:
    with db._write_lock:
        conn = db._conn()
        row = conn.execute(
            "SELECT * FROM mood_slots WHERE account_id=? AND slot_id=?",
            (account_id, slot_id),
        ).fetchone()
    return _slot_row(row) if row else None


def ensure_slot(account_id: str, slot_id: str, label: str = "", base: float = DEFAULT_MOOD) -> dict:
    now = time.time()
    with db._write_lock:
        conn = db._conn()
        conn.execute(
            """INSERT OR IGNORE INTO mood_slots
               (account_id,slot_id,label,mood,dims,last_noted_mood,last_decay_at,
                updated_at,last_chat_uid)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (account_id, slot_id, label, base, "{}", base, now, 0.0, ""),
        )
        conn.commit()
    return get_slot(account_id, slot_id) or {}


def load_state(account_id: str, slot_id: str, base: float = DEFAULT_MOOD) -> dict[str, Any]:
    return get_slot(account_id, slot_id) or ensure_slot(account_id, slot_id, base=base)


def _write_slot(conn: Any, account_id: str, slot_id: str, state: dict[str, Any]) -> None:
    conn.execute(
        """UPDATE mood_slots SET mood=?, dims=?, last_reason=?, last_confidence=?,
           last_noted_mood=?, last_decay_at=?, injected_for=?, last_injected_at=?,
           last_injected_text=?, said_at=?, minor=?, kind=?, updated_at=?, last_chat_uid=?
           WHERE account_id=? AND slot_id=?""",
        (
            float(state["mood"]),
            json.dumps({k: round(float(state["dims"].get(k, 0)), 3) for k in DIM_KEYS},
                       ensure_ascii=False),
            str(state.get("last_reason") or "")[:400],
            int(state.get("last_confidence") or 0),
            float(state.get("last_noted_mood", state["mood"])),
            float(state.get("last_decay_at") or 0),
            float(state.get("injected_for") or 0),
            float(state.get("last_injected_at") or 0),
            str(state.get("last_injected_text") or "")[:600],
            json.dumps([round(float(x), 3) for x in (state.get("said_at") or [])][-200:]),
            int(bool(state.get("minor"))),
            str(state.get("kind") or ""),
            float(state.get("updated_at") or 0),
            str(state.get("last_chat_uid") or ""),
            account_id,
            slot_id,
        ),
    )


def save_state(account_id: str, slot_id: str, state: dict[str, Any]) -> None:
    with db.transaction() as conn:
        _write_slot(conn, account_id, slot_id, state)


def _insert_event(conn: Any, account_id: str, slot_id: str, *, at: float, mood: float,
                  delta: float, dims: dict[str, float], dims_delta: dict[str, float],
                  reason: str, confidence: int, kind: str, source: str, minor: int) -> int:
    cur = conn.execute(
        """INSERT INTO mood_events
           (account_id,slot_id,at,mood,delta,dims,dims_delta,reason,confidence,
            kind,source,injected,minor,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            account_id, slot_id, float(at), float(mood), float(delta),
            json.dumps(dims, ensure_ascii=False),
            json.dumps(dims_delta, ensure_ascii=False),
            str(reason or "")[:400], int(confidence), kind, source, 0, int(minor),
            time.time(),
        ),
    )
    return int(cur.lastrowid)


def save_state_with_event(account_id: str, slot_id: str, state: dict[str, Any], *,
                          event: dict[str, Any]) -> int:
    """同一事务里写槽位并插入事件，避免「状态变了但时间线没有」的中间态。"""
    with db.transaction() as conn:
        _write_slot(conn, account_id, slot_id, state)
        return _insert_event(
            conn, account_id, slot_id,
            at=event["at"], mood=event["mood"], delta=event["delta"],
            dims=event["dims"], dims_delta=event["dims_delta"],
            reason=event["reason"], confidence=event["confidence"],
            kind=event["kind"], source=event["source"], minor=event["minor"],
        )


def add_event(account_id: str, slot_id: str, *, at: float, mood: float, delta: float,
              dims: dict[str, float], dims_delta: dict[str, float], reason: str,
              confidence: int, kind: str, source: str, minor: int) -> int:
    with db.transaction() as conn:
        return _insert_event(
            conn, account_id, slot_id, at=at, mood=mood, delta=delta,
            dims=dims, dims_delta=dims_delta, reason=reason,
            confidence=confidence, kind=kind, source=source, minor=minor,
        )


def _event_row(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "account_id": row["account_id"],
        "slot_id": row["slot_id"],
        "at": float(row["at"]),
        "mood": float(row["mood"]),
        "delta": float(row["delta"] or 0),
        "dims": _loads(row["dims"], {}),
        "dims_delta": _loads(row["dims_delta"], {}),
        "reason": row["reason"] or "",
        "confidence": int(row["confidence"] or 0),
        "kind": row["kind"] or "judge",
        "source": row["source"] or "",
        "injected": int(row["injected"] or 0),
        "minor": int(row["minor"] or 0),
        "created_at": float(row["created_at"] or 0),
    }


def list_events(account_id: str, slot_id: str, limit: int = 50, before: float = 0.0) -> list[dict]:
    sql = "SELECT * FROM mood_events WHERE account_id=? AND slot_id=?"
    args: list[Any] = [account_id, slot_id]
    if before:
        sql += " AND at<?"
        args.append(float(before))
    sql += " ORDER BY at DESC, id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    with db._write_lock:
        conn = db._conn()
        rows = conn.execute(sql, args).fetchall()
    return [_event_row(r) for r in rows]


def mark_injected(account_id: str, slot_id: str, at: float) -> None:
    with db._write_lock:
        conn = db._conn()
        conn.execute(
            """UPDATE mood_events SET injected=1
               WHERE account_id=? AND slot_id=? AND at=?""",
            (account_id, slot_id, float(at)),
        )
        conn.commit()


def list_slots(account_id: str) -> list[dict[str, Any]]:
    with db._write_lock:
        conn = db._conn()
        rows = conn.execute(
            "SELECT * FROM mood_slots WHERE account_id=? ORDER BY slot_id",
            (account_id,),
        ).fetchall()
    return [_slot_row(r) for r in rows]


def create_slot(account_id: str, slot_id: str = "", label: str = "") -> dict[str, Any]:
    slot_id = (slot_id or uuid.uuid4().hex[:8]).strip()
    if not slot_id:
        raise ValueError("槽位 id 不能为空")
    return ensure_slot(account_id, slot_id, label=label)


def update_slot(account_id: str, slot_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"label", "self_name", "owner_name", "self_aliases"}
    sets = {k: str(v or "")[:120] for k, v in fields.items() if k in allowed}
    if not sets:
        return get_slot(account_id, slot_id) or {}
    with db._write_lock:
        conn = db._conn()
        conn.execute(
            "UPDATE mood_slots SET " + ", ".join(f"{k}=?" for k in sets)
            + " WHERE account_id=? AND slot_id=?",
            (*sets.values(), account_id, slot_id),
        )
        conn.commit()
    return get_slot(account_id, slot_id) or {}


def delete_slot(account_id: str, slot_id: str) -> None:
    if slot_id == DEFAULT_SLOT:
        raise ValueError("默认槽不能删除")
    with db._write_lock:
        conn = db._conn()
        conn.execute("DELETE FROM mood_slot_chats WHERE account_id=? AND slot_id=?",
                     (account_id, slot_id))
        conn.execute("DELETE FROM mood_slots WHERE account_id=? AND slot_id=?",
                     (account_id, slot_id))
        conn.commit()


def bind_chat(account_id: str, slot_id: str, chat_uid: str) -> None:
    if not chat_uid:
        raise ValueError("会话不能为空")
    with db._write_lock:
        conn = db._conn()
        conn.execute(
            "DELETE FROM mood_slot_chats WHERE account_id=? AND chat_uid=?",
            (account_id, chat_uid),
        )
        conn.execute(
            "INSERT OR IGNORE INTO mood_slot_chats(account_id,slot_id,chat_uid) VALUES(?,?,?)",
            (account_id, slot_id, chat_uid),
        )
        conn.commit()


def unbind_chat(account_id: str, chat_uid: str) -> None:
    with db._write_lock:
        conn = db._conn()
        conn.execute(
            "DELETE FROM mood_slot_chats WHERE account_id=? AND chat_uid=?",
            (account_id, chat_uid),
        )
        conn.commit()


def list_bindings(account_id: str) -> list[dict[str, str]]:
    with db._write_lock:
        conn = db._conn()
        rows = conn.execute(
            "SELECT slot_id, chat_uid FROM mood_slot_chats WHERE account_id=? ORDER BY slot_id,chat_uid",
            (account_id,),
        ).fetchall()
    return [{"slot_id": r["slot_id"], "chat_uid": r["chat_uid"]} for r in rows]


def slot_of_chat(account_id: str, chat_uid: str) -> str | None:
    with db._write_lock:
        conn = db._conn()
        row = conn.execute(
            "SELECT slot_id FROM mood_slot_chats WHERE account_id=? AND chat_uid=?",
            (account_id, chat_uid),
        ).fetchone()
    return row["slot_id"] if row else None
