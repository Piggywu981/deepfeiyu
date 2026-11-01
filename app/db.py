"""SQLite 持久层：按账号隔离的会话、消息、事件、用量。"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR
from .wechat.base import strip_sender_prefix, summarize_message_content

DB_PATH = DATA_DIR / "agent.db"
DEFAULT_ACCOUNT = "acc_default"

_local = threading.local()
_write_lock = threading.RLock()


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def flush() -> None:
    """提交并检查点 WAL，进程退出前调用，确保数据落盘。"""
    with _write_lock:
        try:
            conn = _conn()
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass


@contextmanager
def transaction():
    """成长子系统专用事务：同一线程连接 + 现有写锁，异常整体回滚。"""
    with _write_lock:
        conn = _conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def read_conn() -> sqlite3.Connection:
    """成长模块只读查询用；不要在这里写。"""
    return _conn()


SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    uid TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    name TEXT,
    is_group INTEGER DEFAULT 0,
    avatar_seed TEXT,
    last_message TEXT,
    last_ts REAL DEFAULT 0,
    unread INTEGER DEFAULT 0,
    ai_enabled INTEGER DEFAULT 0,
    pause_until REAL DEFAULT 0,
    created_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chats_pair ON chats(account_id, chat_id);
CREATE INDEX IF NOT EXISTS idx_chats_acc ON chats(account_id, last_ts DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    chat_uid TEXT NOT NULL,
    chat_id TEXT,
    chat_name TEXT,
    sender_id TEXT,
    sender_name TEXT,
    content TEXT,
    msg_type TEXT DEFAULT 'text',
    is_group INTEGER DEFAULT 0,
    is_self INTEGER DEFAULT 0,
    is_revoked INTEGER DEFAULT 0,
    source TEXT DEFAULT 'wechat',
    ts REAL NOT NULL,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_uid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_acc ON messages(account_id, ts DESC);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT,
    ts REAL NOT NULL,
    level TEXT DEFAULT 'info',
    kind TEXT,
    title TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_acc ON events(account_id, id DESC);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    day TEXT,
    account_id TEXT,
    calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    PRIMARY KEY (day, account_id)
);

CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    title TEXT,
    created_at REAL,
    updated_at REAL,
    model TEXT DEFAULT '',
    effort TEXT DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_agent_conv_acc ON agent_conversations(account_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    reasoning TEXT,
    tools TEXT,
    meta TEXT,
    model TEXT,
    effort TEXT,
    ts REAL
);
CREATE INDEX IF NOT EXISTS idx_agent_turns_conv ON agent_turns(conversation_id, id);

CREATE TABLE IF NOT EXISTS emojis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    md5 TEXT DEFAULT '',
    url TEXT NOT NULL,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    tags TEXT DEFAULT '',
    description TEXT DEFAULT '',
    hit_count INTEGER DEFAULT 0,
    created_at REAL,
    last_seen_at REAL DEFAULT 0,
    source TEXT DEFAULT '',
    locked INTEGER DEFAULT 0,
    UNIQUE(account_id, url)
);
CREATE INDEX IF NOT EXISTS idx_emojis_acc ON emojis(account_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_emojis_acc_md5 ON emojis(account_id, md5) WHERE md5<>'';

CREATE TABLE IF NOT EXISTS emoji_rejects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    md5 TEXT NOT NULL,
    reason TEXT DEFAULT '',
    created_at REAL,
    UNIQUE(account_id, md5)
);
CREATE INDEX IF NOT EXISTS idx_emoji_rejects_acc ON emoji_rejects(account_id);

CREATE TABLE IF NOT EXISTS emoji_tag_locks (
    account_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 1,
    updated_at REAL,
    PRIMARY KEY (account_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_emoji_tag_locks_acc ON emoji_tag_locks(account_id);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    chat_uid TEXT DEFAULT '',
    content TEXT NOT NULL,
    kind TEXT DEFAULT 'fact',
    importance INTEGER DEFAULT 2,
    layer TEXT DEFAULT 'core',
    subject TEXT DEFAULT 'contact',
    confidence REAL DEFAULT 0.8,
    pinned INTEGER DEFAULT 0,
    embedding TEXT DEFAULT '',
    last_access REAL DEFAULT 0,
    access_count INTEGER DEFAULT 0,
    expires_at REAL DEFAULT 0,
    source TEXT DEFAULT 'manual',
    meta TEXT DEFAULT '{}',
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_memories_acc ON memories(account_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(account_id, layer, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_profiles (
    account_id TEXT NOT NULL,
    chat_uid TEXT NOT NULL,
    summary TEXT DEFAULT '',
    relationship TEXT DEFAULT '',
    preferences TEXT DEFAULT '',
    taboos TEXT DEFAULT '',
    recent_topics TEXT DEFAULT '',
    meta TEXT DEFAULT '{}',
    updated_at REAL,
    PRIMARY KEY (account_id, chat_uid)
);
CREATE INDEX IF NOT EXISTS idx_memory_profiles_acc ON memory_profiles(account_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS moments (
    account_id TEXT NOT NULL,
    feed_id TEXT NOT NULL,
    username TEXT DEFAULT '',
    nickname TEXT DEFAULT '',
    text TEXT DEFAULT '',
    location TEXT DEFAULT '',
    create_time REAL DEFAULT 0,
    images TEXT DEFAULT '[]',
    videos TEXT DEFAULT '[]',
    likes TEXT DEFAULT '[]',
    comments TEXT DEFAULT '[]',
    first_seen REAL DEFAULT 0,
    PRIMARY KEY (account_id, feed_id)
);
CREATE INDEX IF NOT EXISTS idx_moments_acc ON moments(account_id, create_time DESC);

CREATE TABLE IF NOT EXISTS moment_interactions (
    account_id TEXT NOT NULL,
    local_id INTEGER NOT NULL,
    create_time REAL DEFAULT 0,
    type INTEGER DEFAULT 0,
    feed_id TEXT DEFAULT '',
    from_username TEXT DEFAULT '',
    from_nickname TEXT DEFAULT '',
    content TEXT DEFAULT '',
    relative_me INTEGER DEFAULT 0,
    seen INTEGER DEFAULT 0,
    PRIMARY KEY (account_id, local_id)
);
CREATE INDEX IF NOT EXISTS idx_moment_int_acc ON moment_interactions(account_id, local_id DESC);

CREATE TABLE IF NOT EXISTS growth_state (
    account_id TEXT PRIMARY KEY,
    self_summary TEXT DEFAULT '',
    summary_run_id INTEGER DEFAULT 0,
    summary_updated_at REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS growth_traits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    scope TEXT DEFAULT 'global',
    chat_uid TEXT DEFAULT '',
    strength INTEGER DEFAULT 2,
    confidence REAL DEFAULT 0.7,
    status TEXT DEFAULT 'active',
    source TEXT DEFAULT 'auto',
    evidence TEXT DEFAULT '[]',
    content_hash TEXT NOT NULL,
    created_at REAL,
    updated_at REAL,
    last_seen_at REAL,
    retired_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_growth_traits_hash
    ON growth_traits(account_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_growth_traits_status
    ON growth_traits(account_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_growth_traits_kind
    ON growth_traits(account_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_growth_traits_scope
    ON growth_traits(account_id, scope, chat_uid, status);

CREATE TABLE IF NOT EXISTS growth_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    day TEXT NOT NULL,
    content TEXT NOT NULL,
    run_id INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_growth_journal_day
    ON growth_journal(account_id, day);

CREATE TABLE IF NOT EXISTS growth_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    progress TEXT DEFAULT '',
    due_at REAL DEFAULT 0,
    source TEXT DEFAULT 'auto',
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_growth_goals_status
    ON growth_goals(account_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS growth_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    risk TEXT DEFAULT 'high',
    payload TEXT NOT NULL,
    before TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    evidence TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    decided_by TEXT DEFAULT '',
    decided_at REAL DEFAULT 0,
    applied_event_id INTEGER DEFAULT 0,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_growth_proposals_status
    ON growth_proposals(account_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS growth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    entity_type TEXT DEFAULT '',
    entity_id INTEGER DEFAULT 0,
    before TEXT DEFAULT '',
    after TEXT DEFAULT '',
    proposal_id INTEGER DEFAULT 0,
    reversible INTEGER DEFAULT 1,
    reverted_by INTEGER DEFAULT 0,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_growth_events_acc
    ON growth_events(account_id, id DESC);

CREATE TABLE IF NOT EXISTS growth_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    day TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    started_at REAL,
    finished_at REAL,
    stats TEXT DEFAULT '{}',
    cost REAL DEFAULT 0,
    error TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_growth_runs_acc
    ON growth_runs(account_id, started_at DESC);

CREATE TABLE IF NOT EXISTS bili_patrol_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL DEFAULT 0,
    status TEXT DEFAULT 'running',
    trigger TEXT DEFAULT 'auto',
    keyword TEXT DEFAULT '',
    report_path TEXT DEFAULT '',
    delivered_to TEXT DEFAULT '',
    error TEXT DEFAULT '',
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_bili_runs_acc ON bili_patrol_runs(account_id, started_at DESC);

CREATE TABLE IF NOT EXISTS mood_slots (
    account_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    label TEXT DEFAULT '',
    self_name TEXT DEFAULT '',
    owner_name TEXT DEFAULT '',
    self_aliases TEXT DEFAULT '',
    mood REAL DEFAULT 70,
    dims TEXT DEFAULT '{}',
    last_reason TEXT DEFAULT '',
    last_confidence INTEGER DEFAULT 0,
    last_noted_mood REAL DEFAULT 70,
    last_decay_at REAL DEFAULT 0,
    injected_for REAL DEFAULT 0,
    last_injected_at REAL DEFAULT 0,
    last_injected_text TEXT DEFAULT '',
    said_at TEXT DEFAULT '[]',
    minor INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'judge',
    updated_at REAL DEFAULT 0,
    last_chat_uid TEXT DEFAULT '',
    PRIMARY KEY (account_id, slot_id)
);
CREATE TABLE IF NOT EXISTS mood_slot_chats (
    account_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    chat_uid TEXT NOT NULL,
    PRIMARY KEY (account_id, slot_id, chat_uid)
);
CREATE INDEX IF NOT EXISTS idx_mood_bind_chat ON mood_slot_chats(account_id, chat_uid);
CREATE TABLE IF NOT EXISTS mood_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    at REAL NOT NULL,
    mood REAL NOT NULL,
    delta REAL DEFAULT 0,
    dims TEXT DEFAULT '{}',
    dims_delta TEXT DEFAULT '{}',
    reason TEXT DEFAULT '',
    confidence INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'judge',
    source TEXT DEFAULT '',
    injected INTEGER DEFAULT 0,
    minor INTEGER DEFAULT 0,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mood_events_slot ON mood_events(account_id, slot_id, at DESC);

-- 主动搭话（Phase 3）：掷骰状态与发送记录独立成表，重启后 misses/日额度不清零。
CREATE TABLE IF NOT EXISTS proactive_state (
    account_id TEXT NOT NULL,
    chat_uid TEXT NOT NULL,
    last_roll_at REAL DEFAULT 0,
    last_self_at REAL DEFAULT 0,
    misses INTEGER DEFAULT 0,
    day TEXT DEFAULT '',
    sent_today INTEGER DEFAULT 0,
    PRIMARY KEY (account_id, chat_uid)
);

CREATE TABLE IF NOT EXISTS proactive_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    chat_uid TEXT NOT NULL,
    at REAL NOT NULL,
    trigger TEXT DEFAULT 'auto',
    hit_line REAL DEFAULT 0,
    roll REAL DEFAULT 0,
    hit INTEGER DEFAULT 0,
    status TEXT DEFAULT '',
    message TEXT DEFAULT '',
    error TEXT DEFAULT '',
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_proactive_events ON proactive_events(account_id, at DESC);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()
        return {r["name"] for r in rows}
    except Exception:
        return set()


def _exec_script(conn: sqlite3.Connection, sql: str) -> None:
    for stmt in sql.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _copy_old(conn: sqlite3.Connection) -> None:
    if _has_table(conn, "chats_old"):
        conn.execute(
            f"""INSERT OR IGNORE INTO chats(uid,account_id,chat_id,name,is_group,avatar_seed,last_message,
                                             last_ts,unread,ai_enabled,pause_until,created_at)
                SELECT '{DEFAULT_ACCOUNT}:' || id, '{DEFAULT_ACCOUNT}', id, name, is_group, avatar_seed,
                       last_message, last_ts, unread, ai_enabled, pause_until, created_at FROM chats_old"""
        )
    if _has_table(conn, "messages_old"):
        conn.execute(
            f"""INSERT OR IGNORE INTO messages(id,account_id,chat_uid,chat_id,chat_name,sender_id,sender_name,
                                               content,msg_type,is_group,is_self,source,ts,meta)
                SELECT id, '{DEFAULT_ACCOUNT}', '{DEFAULT_ACCOUNT}:' || chat_id, chat_id, chat_name,
                       sender_id, sender_name, content, msg_type, is_group, is_self, source, ts, meta
                FROM messages_old"""
        )
    if _has_table(conn, "usage_old"):
        conn.execute(
            f"""INSERT OR IGNORE INTO usage(day,account_id,calls,prompt_tokens,completion_tokens)
                SELECT day, '{DEFAULT_ACCOUNT}', calls, prompt_tokens, completion_tokens FROM usage_old"""
        )


# 后续版本给已有表新增的列：CREATE TABLE IF NOT EXISTS 不会更新已有表结构，
# 必须用 ALTER TABLE 增量补列，否则 INSERT/SELECT 新列会报 "no column named ..."。
# DDL 的表名列名不支持绑定参数，只能逐表写死字面语句（表名列名均为代码内常量）。


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    if _has_table(conn, "events") and "account_id" not in _columns(conn, "events"):
        conn.execute("ALTER TABLE events ADD COLUMN account_id TEXT")
    if _has_table(conn, "chats") and "avatar_seed" not in _columns(conn, "chats"):
        conn.execute("ALTER TABLE chats ADD COLUMN avatar_seed TEXT")
    if _has_table(conn, "messages") and "meta" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
    if _has_table(conn, "messages") and "is_revoked" not in _columns(conn, "messages"):
        conn.execute("ALTER TABLE messages ADD COLUMN is_revoked INTEGER DEFAULT 0")
    if _has_table(conn, "emojis") and "md5" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN md5 TEXT DEFAULT ''")
    if _has_table(conn, "emojis") and "tags" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN tags TEXT DEFAULT ''")
    if _has_table(conn, "emojis") and "description" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN description TEXT DEFAULT ''")
    if _has_table(conn, "emojis") and "hit_count" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN hit_count INTEGER DEFAULT 0")
    if _has_table(conn, "emojis") and "last_seen_at" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN last_seen_at REAL DEFAULT 0")
    if _has_table(conn, "emojis") and "source" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN source TEXT DEFAULT ''")
    if _has_table(conn, "emojis") and "locked" not in _columns(conn, "emojis"):
        conn.execute("ALTER TABLE emojis ADD COLUMN locked INTEGER DEFAULT 0")
    # v5 记忆分层：老库只补列，不重写数据
    memory_columns = {
        "layer": "TEXT DEFAULT 'core'",
        "subject": "TEXT DEFAULT 'contact'",
        "confidence": "REAL DEFAULT 0.8",
        "pinned": "INTEGER DEFAULT 0",
        "embedding": "TEXT DEFAULT ''",
        "last_access": "REAL DEFAULT 0",
        "access_count": "INTEGER DEFAULT 0",
        "expires_at": "REAL DEFAULT 0",
        "source": "TEXT DEFAULT 'manual'",
        "meta": "TEXT DEFAULT '{}'",
    }
    if _has_table(conn, "memories"):
        existing = _columns(conn, "memories")
        for name, ddl in memory_columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
    # 心情注入按会话收口：记录最后一次判断来自哪个会话，老库缺列时补上
    if _has_table(conn, "mood_slots") and "last_chat_uid" not in _columns(conn, "mood_slots"):
        conn.execute("ALTER TABLE mood_slots ADD COLUMN last_chat_uid TEXT DEFAULT ''")


def _migrate(conn: sqlite3.Connection) -> None:
    """v1 单账号表 → v2 多账号表。可重入：中途失败后再次启动能继续完成。"""
    with conn:  # 事务：失败整体回滚，避免半迁移状态
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            _exec_script(conn, SCHEMA)
            conn.execute("PRAGMA user_version=6")
            return

        # 先补列（含 v1 旧表），否则 SCHEMA 的索引、_copy_old 的 SELECT 会因缺列失败
        _add_missing_columns(conn)

        # 表情身份按 md5 收敛：签名 url 会变，同一 md5 可能对应多行，先去重再建唯一索引
        if _has_table(conn, "emojis") and "md5" in _columns(conn, "emojis"):
            conn.execute(
                "DELETE FROM emojis WHERE md5<>'' AND id NOT IN "
                "(SELECT MIN(id) FROM emojis WHERE md5<>'' GROUP BY account_id, md5)"
            )

        legacy = "chats" in tables and "uid" not in _columns(conn, "chats")
        if legacy:
            conn.execute("ALTER TABLE chats RENAME TO chats_old")
            if "messages" in tables:
                conn.execute("ALTER TABLE messages RENAME TO messages_old")
            if "usage" in tables:
                conn.execute("ALTER TABLE usage RENAME TO usage_old")
            # 旧索引名与新表冲突，先删（新表会重建）
            conn.execute("DROP INDEX IF EXISTS idx_messages_chat")
            conn.execute("DROP INDEX IF EXISTS idx_messages_ts")

        _exec_script(conn, SCHEMA)
        _copy_old(conn)

        conn.execute("DROP TABLE IF EXISTS chats_old")
        conn.execute("DROP TABLE IF EXISTS messages_old")
        conn.execute("DROP TABLE IF EXISTS usage_old")

        # 只在真正从 v1 迁过来时重置到 2；已有库不能每次启动都回退版本，
        # 否则 v3 的“默认人工接管”和 v5 的层归位会反复重跑。
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if legacy:
            conn.execute("PRAGMA user_version=2")
            version = 2

        # v3（一次性）：所有存量会话默认改为人工接管，之后用户手动设置不再被覆盖
        if version < 3:
            conn.execute("UPDATE chats SET ai_enabled=0")
            conn.execute("PRAGMA user_version=3")
            version = 3

        if version < 4:
            conn.execute("PRAGMA user_version=4")
            version = 4

        # v5：记忆分层。老数据按 kind 归位，避免全部落进 core 或空 layer。
        if version < 5:
            conn.execute(
                "UPDATE memories SET layer='episodic' "
                "WHERE (layer IS NULL OR layer='' OR layer='core') AND kind='event'"
            )
            conn.execute(
                "UPDATE memories SET layer='core' WHERE layer IS NULL OR layer=''"
            )
            conn.execute(
                "UPDATE memories SET source='extract' WHERE source IS NULL OR source=''"
            )
            conn.execute("PRAGMA user_version=5")
            version = 5

        if version < 6:
            conn.execute("PRAGMA user_version=6")


def init() -> None:
    with _write_lock:
        conn = _conn()
        _migrate(conn)


def uid_of(account_id: str, chat_id: str) -> str:
    return f"{account_id}:{chat_id}"


def split_uid(uid: str) -> tuple[str, str]:
    account_id, _, chat_id = uid.partition(":")
    return account_id, chat_id


def _new_chat_ai_enabled() -> int:
    """新会话的初始 AI 接管状态，跟随设置「默认全 AI 接管」；只影响新建，已有会话不覆盖。"""
    from . import config  # 函数内导入：测试里 monkeypatch config.load 才能生效

    return int(bool((config.load().get("auto") or {}).get("default_ai_on", False)))


def upsert_chat(
    account_id: str,
    chat_id: str,
    name: str,
    is_group: bool,
    last_message: str = "",
    ts: float | None = None,
) -> str:
    now = time.time()
    ts = ts or now
    uid = uid_of(account_id, chat_id)
    with _write_lock:
        conn = _conn()
        row = conn.execute("SELECT uid FROM chats WHERE uid=?", (uid,)).fetchone()
        if row:
            conn.execute(
                "UPDATE chats SET name=?, is_group=?, last_message=?, last_ts=? WHERE uid=?",
                (name, int(is_group), last_message, ts, uid),
            )
        else:
            conn.execute(
                """INSERT INTO chats(uid,account_id,chat_id,name,is_group,avatar_seed,
                                     last_message,last_ts,ai_enabled,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uid, account_id, chat_id, name, int(is_group), name, last_message, ts,
                 _new_chat_ai_enabled(), now),
            )
        conn.commit()
    return uid


def _chat_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["is_group"] = bool(d["is_group"])
    d["ai_enabled"] = bool(d["ai_enabled"])
    d["paused"] = (d.get("pause_until") or 0) > time.time()
    if d.get("last_message"):
        d["last_message"] = summarize_message_content(d["last_message"])
    return d


def list_chats(account_id: str, keyword: str = "", limit: int = 200) -> list[dict[str, Any]]:
    conn = _conn()
    if keyword:
        rows = conn.execute(
            "SELECT * FROM chats WHERE account_id=? AND name LIKE ? ORDER BY last_ts DESC LIMIT ?",
            (account_id, f"%{keyword}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM chats WHERE account_id=? ORDER BY last_ts DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [_chat_row(r) for r in rows]


def get_chat(uid: str) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM chats WHERE uid=?", (uid,)).fetchone()
    return _chat_row(row) if row else None


def get_chat_by(account_id: str, chat_id: str) -> dict[str, Any] | None:
    return get_chat(uid_of(account_id, chat_id))


def set_chat_ai(uid: str, enabled: bool) -> None:
    with _write_lock:
        conn = _conn()
        conn.execute("UPDATE chats SET ai_enabled=? WHERE uid=?", (int(enabled), uid))
        conn.commit()


def set_chat_pause(uid: str, until: float) -> None:
    with _write_lock:
        conn = _conn()
        conn.execute("UPDATE chats SET pause_until=? WHERE uid=?", (until, uid))
        conn.commit()


def clear_unread(uid: str) -> None:
    with _write_lock:
        conn = _conn()
        conn.execute("UPDATE chats SET unread=0 WHERE uid=?", (uid,))
        conn.commit()


def bump_unread(uid: str) -> None:
    with _write_lock:
        conn = _conn()
        conn.execute("UPDATE chats SET unread=unread+1 WHERE uid=?", (uid,))
        conn.commit()


def add_message(msg: dict[str, Any]) -> None:
    with _write_lock:
        conn = _conn()
        conn.execute(
            """INSERT OR REPLACE INTO messages
               (id,account_id,chat_uid,chat_id,chat_name,sender_id,sender_name,content,
                msg_type,is_group,is_self,source,ts,meta)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                msg["id"],
                msg.get("account_id", ""),
                msg["chat_uid"],
                msg.get("chat_id", ""),
                msg.get("chat_name", ""),
                msg.get("sender_id", ""),
                msg.get("sender_name", ""),
                msg.get("content", ""),
                msg.get("msg_type", "text"),
                int(msg.get("is_group", False)),
                int(msg.get("is_self", False)),
                msg.get("source", "wechat"),
                msg["ts"],
                json.dumps(msg.get("meta", {}), ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE chats SET last_message=?, last_ts=? WHERE uid=?",
            (msg.get("content", "")[:80], msg["ts"], msg["chat_uid"]),
        )
        conn.commit()


def upsert_chat_meta(account_id: str, chat_id: str, name: str, is_group: bool) -> str:
    """只登记会话身份（名称/类型），不动最后消息预览与时间。

    用于白名单同步等仅确认“会话存在”的场景；带消息内容的收发路径仍走
    upsert_chat，否则每次驱动启动都会把全部会话的预览清空、时间重置成启动时刻。
    """
    now = time.time()
    uid = uid_of(account_id, chat_id)
    with _write_lock:
        conn = _conn()
        row = conn.execute("SELECT uid FROM chats WHERE uid=?", (uid,)).fetchone()
        if row:
            conn.execute(
                "UPDATE chats SET name=?, is_group=? WHERE uid=?",
                (name, int(is_group), uid),
            )
        else:
            conn.execute(
                """INSERT INTO chats(uid,account_id,chat_id,name,is_group,avatar_seed,
                                     last_message,last_ts,ai_enabled,created_at)
                   VALUES(?,?,?,?,?,?,?,0,?,?)""",
                (uid, account_id, chat_id, name, int(is_group), name, "",
                 _new_chat_ai_enabled(), now),
            )
        conn.commit()
    return uid


def mark_message_revoked(uid: str, actor: str, notice_ts: float) -> dict[str, Any] | None:
    """撤回通知回溯标记原消息，返回被标记的消息；找不到匹配返回 None。

    通知里没有原消息 id（4.x 本地库的撤回通知是纯文本/无 id XML），只能按
    「同会话 + 撤回者 + 未标记过」取最近一条；窗口取撤回时限 2 分钟再放宽
    到 10 分钟，防止通知延迟或回填乱序时误标到太早的消息。
    """
    with _write_lock:
        conn = _conn()
        if actor:
            row = conn.execute(
                """SELECT * FROM messages WHERE chat_uid=? AND is_revoked=0
                   AND msg_type NOT IN ('system','pat') AND is_self=0 AND sender_name=? AND ts<=?
                   AND ts>=? ORDER BY ts DESC LIMIT 1""",
                (uid, actor, notice_ts, notice_ts - 600),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM messages WHERE chat_uid=? AND is_revoked=0
                   AND msg_type NOT IN ('system','pat') AND is_self=1 AND ts<=? AND ts>=?
                   ORDER BY ts DESC LIMIT 1""",
                (uid, notice_ts, notice_ts - 600),
            ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE messages SET is_revoked=1 WHERE id=?", (row["id"],))
        conn.commit()
    d = dict(row)
    d["is_group"] = bool(d["is_group"])
    d["is_self"] = bool(d["is_self"])
    d["is_revoked"] = bool(d["is_revoked"])
    d["is_revoked"] = True
    return d


def add_history_messages(account_id: str, msgs: list[dict[str, Any]]) -> int:
    """批量回填历史消息：不进事件总线、不触发自动回复/未读，返回新增条数。

    回填源是微信解密库，可能和既往实时入库的同一批消息重复（旧记录的 id
    不是确定性格式），所以除主键外再按 会话+时间±2s+内容+发送者 兜底去重；
    会话预览由调用方用 refresh_chat_preview 校准。
    """
    added = 0
    with _write_lock:
        conn = _conn()
        for msg in msgs:
            dup = conn.execute(
                """SELECT 1 FROM messages
                   WHERE chat_uid=? AND ABS(ts-?)<2 AND content=? AND is_self=? LIMIT 1""",
                (
                    msg["chat_uid"],
                    msg["ts"],
                    msg.get("content", ""),
                    int(msg.get("is_self", False)),
                ),
            ).fetchone()
            if dup:
                continue
            # 旧版把拍一拍压成「[系统消息]」入库，content 与新文案对不上，上面的
            # 兜底去重会漏；同会话同时间窗内若只有一条发送者对得上（或为空）的旧
            # system 行，原地升级，避免每次启动回填都多出一条重复的拍一拍。
            # 同一窗口还有别的系统通知时不猜，宁可多一条也不改错行。
            if msg.get("msg_type") == "pat":
                legacy_rows = conn.execute(
                    """SELECT id, sender_name FROM messages
                       WHERE chat_uid=? AND ABS(ts-?)<2 AND is_self=?
                         AND msg_type='system' AND content='[系统消息]'
                       ORDER BY ABS(ts-?)""",
                    (msg["chat_uid"], msg["ts"], int(msg.get("is_self", False)), msg["ts"]),
                ).fetchall()
                new_sender = str(msg.get("sender_name") or "").strip()
                candidates = [
                    r
                    for r in legacy_rows
                    if not str(r["sender_name"] or "").strip()
                    or str(r["sender_name"] or "").strip() == new_sender
                ]
                if len(candidates) == 1:
                    conn.execute(
                        """UPDATE messages SET sender_id=?, sender_name=?, content=?,
                           msg_type='pat', meta=? WHERE id=?""",
                        (
                            msg.get("sender_id", ""),
                            msg.get("sender_name", ""),
                            msg.get("content", ""),
                            json.dumps(msg.get("meta", {}), ensure_ascii=False),
                            candidates[0]["id"],
                        ),
                    )
                    added += 1
                    continue
            # 旧版只存了「[文件] 群聊的聊天记录」标题的记录卡片，用解析出的完整
            # 转录按同 id 升级（REPLACE）；引用消息旧摘要会把回复文本压丢（整条
            # 变成「[文件] 回复文本」或只剩「[图片]」），同样按同 id 升级；
            # 拍一拍此前被压成「[系统消息]」，回填时也按同 id 换成可读文案；
            # 链接卡片曾被缩略图 <appattach> 误判成「[文件] 标题」且丢掉网址，
            # 旧行里没有网址无法就地修，只能靠回填从解密库重读原文升级
            is_refer = bool((msg.get("meta") or {}).get("refer"))
            sql = (
                "INSERT OR REPLACE"
                if msg.get("content", "").startswith("[聊天记录]")
                or msg.get("content", "").startswith("[链接] ")
                or is_refer
                or msg.get("msg_type") == "pat"
                else "INSERT OR IGNORE"
            )
            cur = conn.execute(
                f"""{sql} INTO messages
                   (id,account_id,chat_uid,chat_id,chat_name,sender_id,sender_name,content,
                    msg_type,is_group,is_self,source,ts,meta)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    msg["id"],
                    msg.get("account_id", ""),
                    msg["chat_uid"],
                    msg.get("chat_id", ""),
                    msg.get("chat_name", ""),
                    msg.get("sender_id", ""),
                    msg.get("sender_name", ""),
                    msg.get("content", ""),
                    msg.get("msg_type", "text"),
                    int(msg.get("is_group", False)),
                    int(msg.get("is_self", False)),
                    msg.get("source", "history"),
                    msg["ts"],
                    json.dumps(msg.get("meta", {}), ensure_ascii=False),
                ),
            )
            if cur.rowcount > 0:
                added += 1
        conn.commit()
    return added


def refresh_chat_preview(uid: str, last_message: str, ts: float) -> None:
    """回填后校准会话预览：库里时间更新（有实时消息）时不回退，预览为空则补齐。"""
    with _write_lock:
        conn = _conn()
        conn.execute(
            "UPDATE chats SET last_message=?, last_ts=? WHERE uid=? AND (last_ts < ? OR last_message='')",
            (last_message[:80], ts, uid, ts),
        )
        conn.commit()


def list_messages(chat_uid: str, limit: int = 50, before: float | None = None) -> list[dict[str, Any]]:
    conn = _conn()
    # 同一时间戳（Windows 时钟精度下连发消息常见）用 rowid 兜底，保证按发出顺序返回
    if before:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_uid=? AND ts<? ORDER BY ts DESC, rowid DESC LIMIT ?",
            (chat_uid, before, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_uid=? ORDER BY ts DESC, rowid DESC LIMIT ?",
            (chat_uid, limit),
        ).fetchall()
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["is_group"] = bool(d["is_group"])
        d["is_self"] = bool(d["is_self"])
        d["is_revoked"] = bool(d["is_revoked"])
        d["content"] = strip_sender_prefix(
            summarize_message_content(d.get("content") or "", d.get("msg_type") or "text"),
            d.get("sender_name") or "",
            d.get("sender_id") or "",
        )
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except Exception:
            d["meta"] = {}
        out.append(d)
    return out


def list_messages_since(
    account_id: str, since_ts: float, limit: int = 300
) -> list[dict[str, Any]]:
    """按账号取 since 之后的最近消息，返回按时间正序。"""
    rows = _conn().execute(
        """SELECT * FROM messages
           WHERE account_id=? AND ts>=? AND is_revoked=0
           ORDER BY ts DESC, rowid DESC LIMIT ?""",
        (account_id, float(since_ts), max(1, min(1000, int(limit)))),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_message(chat_uid: str, msg_id: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM messages WHERE chat_uid=? AND id=?", (chat_uid, msg_id)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["is_group"] = bool(d["is_group"])
    d["is_self"] = bool(d["is_self"])
    d["is_revoked"] = bool(d["is_revoked"])
    d["content"] = strip_sender_prefix(
        summarize_message_content(d.get("content") or "", d.get("msg_type") or "text"),
        d.get("sender_name") or "",
        d.get("sender_id") or "",
    )
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except Exception:
        d["meta"] = {}
    return d


def search_messages(account_id: str, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM messages WHERE account_id=? AND content LIKE ? ORDER BY ts DESC LIMIT ?",
        (account_id, f"%{keyword}%", limit),
    ).fetchall()
    return [
        {
            **dict(r),
            "content": strip_sender_prefix(
                summarize_message_content(r["content"] or "", r["msg_type"] or "text"),
                r["sender_name"] or "",
                r["sender_id"] or "",
            ),
        }
        for r in rows
    ]


def count_messages(account_id: str, start_ts: float, end_ts: float, is_self: bool | None = None) -> int:
    conn = _conn()
    if is_self is None:
        row = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE account_id=? AND ts>=? AND ts<?",
            (account_id, start_ts, end_ts),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE account_id=? AND ts>=? AND ts<? AND is_self=?",
            (account_id, start_ts, end_ts, int(is_self)),
        ).fetchone()
    return int(row["c"])


def daily_counts(account_id: str, days: int = 7) -> list[dict[str, Any]]:
    now = time.time()
    start = now - days * 86400
    rows = _conn().execute(
        """SELECT date(ts,'unixepoch','localtime') d,
                  SUM(CASE WHEN is_self=0 THEN 1 ELSE 0 END) inbound,
                  SUM(CASE WHEN is_self=1 AND source='agent' THEN 1 ELSE 0 END) ai
           FROM messages WHERE account_id=? AND ts>=? GROUP BY d""",
        (account_id, start),
    ).fetchall()
    mapping = {r["d"]: r for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
        r = mapping.get(day)
        out.append(
            {
                "day": day,
                "label": day[5:],
                "inbound": int(r["inbound"]) if r else 0,
                "ai": int(r["ai"]) if r else 0,
            }
        )
    return out


def add_event(
    account_id: str, kind: str, title: str, detail: str = "", level: str = "info"
) -> dict[str, Any]:
    ts = time.time()
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO events(account_id,ts,level,kind,title,detail) VALUES(?,?,?,?,?,?)",
            (account_id, ts, level, kind, title, detail),
        )
        conn.commit()
        eid = cur.lastrowid
    return {
        "id": eid,
        "account_id": account_id,
        "ts": ts,
        "level": level,
        "kind": kind,
        "title": title,
        "detail": detail,
    }


def list_events(account_id: str, limit: int = 60) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM events WHERE account_id=? ORDER BY id DESC LIMIT ?", (account_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def add_usage(account_id: str, prompt_tokens: int, completion_tokens: int) -> None:
    day = time.strftime("%Y-%m-%d")
    with _write_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO usage(day,account_id,calls,prompt_tokens,completion_tokens)
               VALUES(?,?,1,?,?)
               ON CONFLICT(day,account_id) DO UPDATE SET calls=calls+1,
                 prompt_tokens=prompt_tokens+excluded.prompt_tokens,
                 completion_tokens=completion_tokens+excluded.completion_tokens""",
            (day, account_id, prompt_tokens, completion_tokens),
        )
        conn.commit()


def get_usage(account_id: str, day: str | None = None) -> dict[str, Any]:
    day = day or time.strftime("%Y-%m-%d")
    row = _conn().execute(
        "SELECT * FROM usage WHERE day=? AND account_id=?", (day, account_id)
    ).fetchone()
    return dict(row) if row else {
        "day": day,
        "account_id": account_id,
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def count_ai_replies_today(account_id: str) -> int:
    start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    row = _conn().execute(
        "SELECT COUNT(*) c FROM messages WHERE account_id=? AND is_self=1 AND source='agent' AND ts>=?",
        (account_id, start),
    ).fetchone()
    return int(row["c"])


# ---------------------------------------------------------------- agent console
def _agent_conv_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["turns"] = int(d.get("turns") or 0)
    d["preview"] = (d.get("preview") or "").strip()[:80]
    return d


def create_agent_conversation(
    account_id: str, title: str = "", model: str = "", effort: str = "auto"
) -> dict[str, Any]:
    cid = f"conv_{uuid.uuid4().hex[:12]}"
    now = time.time()
    with _write_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO agent_conversations(id,account_id,title,created_at,updated_at,model,effort)
               VALUES(?,?,?,?,?,?,?)""",
            (cid, account_id, title or "新对话", now, now, model or "", effort or "auto"),
        )
        conn.commit()
    return get_agent_conversation(cid) or {}


def get_agent_conversation(cid: str) -> dict[str, Any] | None:
    row = _conn().execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM agent_turns t WHERE t.conversation_id=c.id) turns
           FROM agent_conversations c WHERE c.id=?""",
        (cid,),
    ).fetchone()
    return _agent_conv_row(row) if row else None


def list_agent_conversations(account_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = _conn().execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM agent_turns t WHERE t.conversation_id=c.id) turns,
                  (SELECT substr(content,1,80) FROM agent_turns t WHERE t.conversation_id=c.id
                    AND t.role='assistant' AND t.content<>'' ORDER BY t.id DESC LIMIT 1) preview
           FROM agent_conversations c
           WHERE c.account_id=? ORDER BY c.updated_at DESC LIMIT ?""",
        (account_id, limit),
    ).fetchall()
    return [_agent_conv_row(r) for r in rows]


def touch_agent_conversation(
    cid: str,
    title: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> None:
    sets = ["updated_at=?"]
    args: list[Any] = [time.time()]
    if title is not None:
        sets.append("title=?")
        args.append(title)
    if model is not None:
        sets.append("model=?")
        args.append(model)
    if effort is not None:
        sets.append("effort=?")
        args.append(effort)
    args.append(cid)
    with _write_lock:
        conn = _conn()
        conn.execute(f"UPDATE agent_conversations SET {', '.join(sets)} WHERE id=?", args)
        conn.commit()


def add_agent_turn(
    cid: str,
    role: str,
    content: str = "",
    reasoning: str = "",
    tools: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    model: str = "",
    effort: str = "",
) -> dict[str, Any]:
    ts = time.time()
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            """INSERT INTO agent_turns(conversation_id,role,content,reasoning,tools,meta,model,effort,ts)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                role,
                content,
                reasoning,
                json.dumps(tools or [], ensure_ascii=False),
                json.dumps(meta or {}, ensure_ascii=False),
                model,
                effort,
                ts,
            ),
        )
        conn.execute("UPDATE agent_conversations SET updated_at=? WHERE id=?", (ts, cid))
        conn.commit()
        tid = cur.lastrowid
    return {"id": tid, "conversation_id": cid, "role": role, "content": content, "ts": ts}


def list_agent_turns(cid: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM agent_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (cid, limit)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in reversed(rows):
        d = dict(r)
        try:
            d["tools"] = json.loads(d.get("tools") or "[]")
        except Exception:
            d["tools"] = []
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except Exception:
            d["meta"] = {}
        out.append(d)
    return out


def list_agent_turns_since(
    account_id: str, since_ts: float, limit: int = 40
) -> list[dict[str, Any]]:
    rows = _conn().execute(
        """SELECT t.* FROM agent_turns t
           JOIN agent_conversations c ON c.id=t.conversation_id
           WHERE c.account_id=? AND t.ts>=? AND t.role IN ('user','assistant')
           ORDER BY t.ts DESC, t.id DESC LIMIT ?""",
        (account_id, float(since_ts), max(1, min(200, int(limit)))),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def count_agent_turns_since(account_id: str, since_ts: float) -> int:
    row = _conn().execute(
        """SELECT COUNT(*) AS n FROM agent_turns t
           JOIN agent_conversations c ON c.id=t.conversation_id
           WHERE c.account_id=? AND t.ts>=? AND t.role IN ('user','assistant')""",
        (account_id, float(since_ts)),
    ).fetchone()
    return int(row["n"] if row else 0)


def delete_agent_conversation(cid: str, account_id: str) -> bool:
    with _write_lock:
        conn = _conn()
        row = conn.execute(
            "SELECT id FROM agent_conversations WHERE id=? AND account_id=?", (cid, account_id)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM agent_turns WHERE conversation_id=?", (cid,))
        conn.execute("DELETE FROM agent_conversations WHERE id=?", (cid,))
        conn.commit()
    return True


def kv_set(k: str, v: Any) -> None:
    with _write_lock:
        conn = _conn()
        conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
        conn.commit()


def kv_get(k: str, default: Any = None) -> Any:
    row = _conn().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["v"])
    except Exception:
        return default


# ---------------------------------------------------------------- emoji 收藏
def valid_md5(s: str) -> str:
    """微信表情 XML 的 md5 属性：32 位 hex 才可信，否则当作缺失。"""
    s = (s or "").strip().lower()
    return s if len(s) == 32 and all(c in "0123456789abcdef" for c in s) else ""


def add_emoji(
    account_id: str, url: str, md5: str = "", width: int = 0, height: int = 0
) -> dict[str, Any]:
    """表情入库；md5 可信时按 md5 识别同一表情。

    cdnurl 是带签名的 CDN 链接，同一表情每次出现 url 都可能不同，
    不能当身份：命中 md5 时刷新 url 并累计命中次数，未命中才插入。
    """
    md5 = valid_md5(md5)
    now = time.time()
    with _write_lock:
        conn = _conn()
        row = None
        if md5:
            row = conn.execute(
                "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
            ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM emojis WHERE account_id=? AND url=?", (account_id, url)
            ).fetchone()
        if row:
            conn.execute(
                "UPDATE emojis SET url=?, last_seen_at=?, hit_count=hit_count+1 WHERE id=?",
                (url, now, row["id"]),
            )
            conn.commit()
            fresh = conn.execute("SELECT * FROM emojis WHERE id=?", (row["id"],)).fetchone()
            return dict(fresh) if fresh else {}
        try:
            conn.execute(
                """INSERT INTO emojis(account_id,md5,url,width,height,created_at,last_seen_at,hit_count)
                   VALUES(?,?,?,?,?,?,?,1)""",
                (account_id, md5, url, int(width or 0), int(height or 0), now, now),
            )
        except sqlite3.IntegrityError:
            # md5 查不到但撞了 url 唯一约束（同一 url 配了不同 md5），按已有行处理
            row = conn.execute(
                "SELECT * FROM emojis WHERE account_id=? AND url=?", (account_id, url)
            ).fetchone()
            if row is None:
                raise
            conn.commit()
            return dict(row)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM emojis WHERE account_id=? AND url=?", (account_id, url)
        ).fetchone()
        return dict(row) if row else {}


def list_emojis(account_id: str, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM emojis WHERE account_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (account_id, limit, max(0, offset)),
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_emojis(account_id: str) -> list[dict[str, Any]]:
    """全量拉取（导出用，不受控制台分页上限约束）。"""
    rows = _conn().execute(
        "SELECT * FROM emojis WHERE account_id=? ORDER BY id DESC", (account_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_emoji(account_id: str, eid: int) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM emojis WHERE account_id=? AND id=?", (account_id, eid)
    ).fetchone()
    return dict(row) if row else None


def get_emoji_by_md5(account_id: str, md5: str) -> dict[str, Any] | None:
    """按内容 md5 查表情（联网找图先查重用）；md5 不可信时返回 None。"""
    md5 = valid_md5(md5)
    if not md5:
        return None
    row = _conn().execute(
        "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
    ).fetchone()
    return dict(row) if row else None


def remove_emoji(account_id: str, eid: int) -> bool:
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM emojis WHERE account_id=? AND id=? AND locked=0",
            (account_id, eid),
        )
        conn.commit()
        return cur.rowcount > 0


def remove_emojis(account_id: str, eids: list[int]) -> int:
    """批量删除表情；别人的/不存在的 id 自动跳过，返回实际删除条数。"""
    ids: list[int] = []
    for eid in eids or []:
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            continue
        if eid > 0:
            ids.append(eid)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            f"DELETE FROM emojis WHERE account_id=? AND id IN ({marks}) AND locked=0",
            (account_id, *ids),
        )
        conn.commit()
        return cur.rowcount


def set_emoji_locked(account_id: str, eid: int, locked: bool) -> dict[str, Any] | None:
    """锁定/解锁单张表情；不存在或不属于当前账号返回 None。"""
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE emojis SET locked=? WHERE account_id=? AND id=?",
            (1 if locked else 0, account_id, int(eid)),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None
        conn.commit()
        row = conn.execute(
            "SELECT * FROM emojis WHERE account_id=? AND id=?", (account_id, int(eid))
        ).fetchone()
        return dict(row) if row else None


def set_emojis_locked(account_id: str, eids: list[int], locked: bool) -> int:
    """批量锁定/解锁表情；别人的/不存在的 id 自动跳过，返回实际命中条数。"""
    ids: list[int] = []
    for eid in eids or []:
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            continue
        if eid > 0:
            ids.append(eid)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            f"UPDATE emojis SET locked=? WHERE account_id=? AND id IN ({marks})",
            (1 if locked else 0, account_id, *ids),
        )
        conn.commit()
        return cur.rowcount


def count_locked_emojis(account_id: str, eids: list[int]) -> int:
    """批删前统计锁定条数；非法 id 自动跳过。"""
    ids: list[int] = []
    for eid in eids or []:
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            continue
        if eid > 0:
            ids.append(eid)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    row = _conn().execute(
        f"SELECT COUNT(*) AS n FROM emojis "
        f"WHERE account_id=? AND locked=1 AND id IN ({marks})",
        (account_id, *ids),
    ).fetchone()
    return int(row["n"] if row else 0)


def import_site_emoji(
    account_id: str,
    md5: str,
    tags: object = "",
    description: str = "",
    source: str = "searchanythingfun",
) -> tuple[dict[str, Any] | None, str]:
    """站点截图入库：新行 source/locked=1；已存在原样返回，不改来源/标签/锁。"""
    md5 = valid_md5(md5)
    if not md5:
        return None, "skipped"
    if isinstance(tags, (list, tuple, set)):
        raw_tags = ",".join(str(t) for t in tags)
    else:
        raw_tags = str(tags or "")
    incoming_tags = normalize_tags(raw_tags)
    desc = " ".join(str(description or "").split())[:200]
    now = time.time()
    with _write_lock:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM emojis WHERE account_id=? AND url=?",
                (account_id, f"local://{md5}"),
            ).fetchone()
        if row is not None:
            return dict(row), "existing"
        try:
            conn.execute(
                """INSERT INTO emojis(account_id,md5,url,tags,description,source,locked,
                                      created_at,last_seen_at,hit_count)
                   VALUES(?,?,?,?,?,?,?,?,?,0)""",
                (
                    account_id,
                    md5,
                    f"local://{md5}",
                    ",".join(incoming_tags),
                    desc,
                    str(source or "")[:40],
                    1,
                    now,
                    0,
                ),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute(
                "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
            ).fetchone()
            return (dict(row) if row else None), "existing"
        conn.commit()
        row = conn.execute(
            "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
        ).fetchone()
        return (dict(row) if row else None), "added"


def list_locked_tags(account_id: str) -> list[str]:
    rows = _conn().execute(
        "SELECT tag FROM emoji_tag_locks WHERE account_id=? AND locked=1 ORDER BY tag",
        (account_id,),
    ).fetchall()
    return [str(r["tag"]) for r in rows]


def ensure_tag_lock_default(account_id: str, tag: str) -> None:
    """首次站点入库时锁住来源标签；已有行（含用户解锁的 locked=0）不覆盖。"""
    tag = (tag or "").strip()[:20]
    if not tag:
        return
    with _write_lock:
        conn = _conn()
        conn.execute(
            "INSERT OR IGNORE INTO emoji_tag_locks(account_id,tag,locked,updated_at) "
            "VALUES(?,?,1,?)",
            (account_id, tag, time.time()),
        )
        conn.commit()


def set_tag_locked(account_id: str, tag: str, locked: bool) -> list[str]:
    """锁定/解锁标签名，返回当前锁定标签列表。"""
    tag = (tag or "").strip()[:20]
    if not tag:
        return list_locked_tags(account_id)
    with _write_lock:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO emoji_tag_locks(account_id,tag,locked,updated_at) "
            "VALUES(?,?,?,?)",
            (account_id, tag, 1 if locked else 0, time.time()),
        )
        conn.commit()
    return list_locked_tags(account_id)


def tag_frequency(account_id: str) -> list[tuple[str, int]]:
    """统计账号表情库标签使用次数，按频次降序；供打标词表与标签归并用。"""
    freq: dict[str, int] = {}
    for row in _conn().execute(
        "SELECT tags FROM emojis WHERE account_id=? AND tags<>''", (account_id,)
    ).fetchall():
        for tag in (row["tags"] or "").split(","):
            tag = tag.strip()
            if tag:
                freq[tag] = freq.get(tag, 0) + 1
    return sorted(freq.items(), key=lambda kv: -kv[1])


def apply_tag_mapping(account_id: str, mapping: dict[str, str]) -> int:
    """按 {旧标签: 规范标签} 重写 tags；锁定标签与锁定行一律跳过。"""
    if not mapping:
        return 0
    locked_tags = set(list_locked_tags(account_id))
    safe = {
        k: v
        for k, v in mapping.items()
        if k not in locked_tags and v not in locked_tags
    }
    if not safe:
        return 0
    changed = 0
    with _write_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT id, tags FROM emojis WHERE account_id=? AND tags<>'' AND locked=0",
            (account_id,),
        ).fetchall()
        for r in rows:
            new_tags: list[str] = []
            for t in (r["tags"] or "").split(","):
                t = t.strip()
                if not t:
                    continue
                nt = safe.get(t, t)
                if nt and nt not in new_tags:
                    new_tags.append(nt)
            new = ",".join(new_tags[:8])
            if new != r["tags"]:
                conn.execute("UPDATE emojis SET tags=? WHERE id=?", (new, r["id"]))
                changed += 1
        conn.commit()
    return changed


# ----------------------------------------------------- 非表情包拒收名单
def reject_emoji(account_id: str, eid: int, reason: str = "") -> dict[str, Any] | None:
    """删除表情行并按内容 md5 记入拒收名单；锁定行不删、不入名单。"""
    row = get_emoji(account_id, eid)
    if not row or row.get("locked"):
        return None
    md5 = valid_md5(row.get("md5"))
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM emojis WHERE account_id=? AND id=? AND locked=0",
            (account_id, eid),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None
        if md5:
            conn.execute(
                "INSERT OR REPLACE INTO emoji_rejects(account_id, md5, reason, created_at)"
                " VALUES(?,?,?,?)",
                (account_id, md5, reason[:200], time.time()),
            )
        conn.commit()
    return row


def list_reject_md5s(account_id: str) -> set[str]:
    rows = _conn().execute(
        "SELECT md5 FROM emoji_rejects WHERE account_id=?", (account_id,)
    ).fetchall()
    return {r["md5"] for r in rows}


def any_emoji_with_md5(md5: str, exclude_account: str = "") -> bool:
    """除指定账号外是否还有表情行引用该 md5（决定能否清掉共享缓存文件）。"""
    md5 = valid_md5(md5)
    if not md5:
        return False
    sql = "SELECT 1 FROM emojis WHERE md5=?"
    args: list[str] = [md5]
    if exclude_account:
        sql += " AND account_id<>?"
        args.append(exclude_account)
    return _conn().execute(sql + " LIMIT 1", args).fetchone() is not None


def replace_in_message_content(account_id: str, old: str, new: str) -> int:
    """把含 old 子串的消息内容整体替换为 new，返回改写的条数。

    用于表情打标完成后，把聊天记录转录里的「[动画表情 md5=x]」占位升级成描述。
    """
    if not old or old == new:
        return 0
    with _write_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT id, content FROM messages WHERE account_id=? AND content LIKE ?",
            (account_id, f"%{old}%"),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE messages SET content=? WHERE id=?",
                (r["content"].replace(old, new), r["id"]),
            )
        conn.commit()
    return len(rows)


def find_emoji_by_md5(account_id: str, md5: str) -> dict[str, Any] | None:
    """按内容 md5 查表情（历史/占位符渲染标签用）；空 md5 或未命中返回 None。"""
    md5 = valid_md5(md5)
    if not md5:
        return None
    row = _conn().execute(
        "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
    ).fetchone()
    return dict(row) if row else None


def sender_emoji_habit(uid: str, sender_id: str = "", limit: int = 80) -> dict[str, Any] | None:
    """会话内某发言人的表情包习惯；样本不足（<8 条）返回 None，调用方用中性表述。

    私聊不传 sender_id，统计对方全部消息；群聊按 sender_id 过滤。
    trailing = 该表情的前一条同发言人消息是文字且间隔 ≤30s（「打完字跟一张」的习惯）。
    """
    rows = list_messages(uid, limit=limit)
    mine = [r for r in rows if not r["is_self"] and r.get("msg_type") not in ("system", "pat")]
    if sender_id:
        mine = [r for r in mine if r.get("sender_id") == sender_id]
    total = len(mine)
    if total < 8:
        return None
    emoji_idx = [i for i, r in enumerate(mine) if r.get("msg_type") == "emoji"]
    trailing = 0
    for i in emoji_idx:
        if i == 0:
            continue
        prev = mine[i - 1]
        if prev.get("msg_type") == "text" and 0 < mine[i]["ts"] - prev["ts"] <= 30:
            trailing += 1
    emoji_n = len(emoji_idx)
    return {
        "total": total,
        "emoji_n": emoji_n,
        "trailing_n": trailing,
        "emoji_share": emoji_n / total,
        "trailing_share": (trailing / emoji_n) if emoji_n else 0.0,
    }


def update_emoji_tags(account_id: str, eid: int, tags: str, description: str = "") -> bool:
    """打标结果写回；已有标签不覆盖（防旧任务/并发写回冲掉新结果）。"""
    tags = (tags or "").strip()
    if not tags:
        return False
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            """UPDATE emojis SET tags=?, description=?
               WHERE account_id=? AND id=? AND (tags='' OR tags IS NULL) AND locked=0""",
            (tags[:200], (description or "").strip()[:200], account_id, int(eid)),
        )
        conn.commit()
        return cur.rowcount > 0


def search_emojis(account_id: str, query: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """AI 检索表情库：query 按空格/顿号/逗号拆成多个关键词，任一命中 tags 或描述
    即入选（子串匹配，英文大小写不敏感），命中的关键词越多越靠前，再按常用度。

    整串匹配会让「鲸鱼、无语、晚安」这类多词查询全军覆没，必须先拆词。
    query 为空时按热度返回整个库。"""
    conn = _conn()
    cap = max(1, min(20, int(limit or 8)))
    tokens = [t for t in re.split(r"[、，,\s/]+", (query or "").strip()) if t]
    if not tokens:
        rows = conn.execute(
            "SELECT * FROM emojis WHERE account_id=? "
            "ORDER BY hit_count DESC, last_seen_at DESC, id DESC LIMIT ?",
            (account_id, cap),
        ).fetchall()
        return [dict(r) for r in rows]
    lower_tokens = [t.lower() for t in tokens]
    scored: list[tuple[int, int, float, int, dict[str, Any]]] = []
    for r in conn.execute("SELECT * FROM emojis WHERE account_id=?", (account_id,)):
        hay = f"{r['tags'] or ''},{r['description'] or ''}".lower()
        hits = sum(1 for t in lower_tokens if t in hay)
        if hits:
            scored.append((hits, r["hit_count"] or 0, r["last_seen_at"] or 0.0, r["id"], dict(r)))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], -x[3]))
    return [item[4] for item in scored[:cap]]


def normalize_tags(raw: str | None) -> list[str]:
    """标签规范化：逗号/顿号/空白拆分，去空去重，单个 ≤20 字，最多 8 个。"""
    out: list[str] = []
    for part in re.split(r"[,，、\s]+", str(raw or "").strip()):
        tag = part.strip()[:20]
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= 8:
            break
    return out


MAX_EMOJI_DIMENSION = 20000


def clamp_emoji_dimension(value: object) -> int:
    """宽高兜底：负数归零，荒谬大值夹到上限，非数字归零（防 SQLite 绑定溢出）。"""
    try:
        return max(0, min(MAX_EMOJI_DIMENSION, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def set_emoji_tags(account_id: str, eid: int, tags: str, description: str = "") -> bool:
    """覆盖写标签与描述（手动编辑 / AI 重打标用）；与 update_emoji_tags 的「只写空标签」语义相反。"""
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE emojis SET tags=?, description=? "
            "WHERE account_id=? AND id=? AND locked=0",
            ((tags or "").strip()[:200], (description or "").strip()[:200], account_id, int(eid)),
        )
        conn.commit()
        return cur.rowcount > 0


def import_emoji(
    account_id: str,
    md5: str,
    tags: object = "",
    description: str = "",
    width: int = 0,
    height: int = 0,
) -> str:
    """导入一张表情（标签不丢、已有数据不被轻率覆盖），返回 added/updated/unchanged/skipped。

    - 按内容 md5 识别同一表情；跨账号各自独立入库；
    - 已存在时标签取并集（原标签在前、最多 8 个），描述只在原来为空时补；
    - 新行用 local://<md5> 占位，图片文件由调用方写入表情缓存，展示/发送复用现有链路。
    """
    md5 = valid_md5(md5)
    if not md5:
        return "skipped"
    if isinstance(tags, (list, tuple, set)):
        raw_tags = ",".join(str(t) for t in tags)
    else:
        raw_tags = str(tags or "")
    incoming_tags = normalize_tags(raw_tags)
    incoming_desc = str(description or "").strip()[:200]
    now = time.time()
    with _write_lock:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM emojis WHERE account_id=? AND md5=?", (account_id, md5)
        ).fetchone()
        if row is None:
            # 极端情况下同一 local:// 已存在但 md5 为空/被改坏：按 url 找到它再合并
            row = conn.execute(
                "SELECT * FROM emojis WHERE account_id=? AND url=?",
                (account_id, f"local://{md5}"),
            ).fetchone()
        if row is not None:
            if int(row["locked"] or 0):
                return "unchanged"
            merged = normalize_tags(row["tags"] or "")
            for tag in incoming_tags:
                if tag not in merged:
                    merged.append(tag)
            merged = merged[:8]
            old_desc = str(row["description"] or "").strip()
            desc = old_desc or incoming_desc
            old_w = clamp_emoji_dimension(row["width"])
            old_h = clamp_emoji_dimension(row["height"])
            new_w = old_w or clamp_emoji_dimension(width)
            new_h = old_h or clamp_emoji_dimension(height)
            new_tags = ",".join(merged)
            if (
                new_tags == (row["tags"] or "")
                and desc == (row["description"] or "")
                and new_w == old_w
                and new_h == old_h
            ):
                return "unchanged"
            conn.execute(
                "UPDATE emojis SET tags=?, description=?, width=?, height=? WHERE id=?",
                (new_tags, desc, new_w, new_h, row["id"]),
            )
            conn.commit()
            return "updated"
        try:
            conn.execute(
                """INSERT INTO emojis(account_id,md5,url,width,height,tags,description,
                                      created_at,last_seen_at,hit_count)
                   VALUES(?,?,?,?,?,?,?,?,?,0)""",
                (
                    account_id,
                    md5,
                    f"local://{md5}",
                    clamp_emoji_dimension(width),
                    clamp_emoji_dimension(height),
                    ",".join(incoming_tags),
                    incoming_desc,
                    now,
                    0,
                ),
            )
        except sqlite3.IntegrityError:
            # 并发导入同一张图：另一条路径刚插进去，交给下一次导入合并
            conn.rollback()
            return "unchanged"
        conn.commit()
        return "added"


# ---------------------------------------------------------------- 人格长期记忆
_MEMORY_COLUMNS = (
    "id,account_id,chat_uid,content,kind,importance,layer,subject,confidence,"
    "pinned,last_access,access_count,expires_at,source,meta,created_at,updated_at"
)
VALID_MEMORY_LAYERS = ("core", "episodic", "session")
VALID_MEMORY_SUBJECTS = ("contact", "self", "world")


def _json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _json_list(raw: Any) -> list[float]:
    if isinstance(raw, list):
        return [float(x) for x in raw]
    try:
        value = json.loads(raw or "[]")
        return [float(x) for x in value] if isinstance(value, list) else []
    except Exception:
        return []


def _memory_row(row: sqlite3.Row, include_embedding: bool = False) -> dict[str, Any]:
    d = dict(row)
    d["importance"] = max(1, min(3, int(d.get("importance") or 2)))
    d["chat_uid"] = d.get("chat_uid") or ""
    d["layer"] = d.get("layer") if d.get("layer") in VALID_MEMORY_LAYERS else "core"
    d["subject"] = d.get("subject") if d.get("subject") in VALID_MEMORY_SUBJECTS else "contact"
    d["confidence"] = float(d.get("confidence") if d.get("confidence") is not None else 0.8)
    d["pinned"] = bool(d.get("pinned"))
    d["last_access"] = float(d.get("last_access") or 0)
    d["access_count"] = int(d.get("access_count") or 0)
    d["expires_at"] = float(d.get("expires_at") or 0)
    d["source"] = d.get("source") or "manual"
    d["meta"] = _json_obj(d.get("meta"))
    if include_embedding:
        d["embedding"] = _json_list(d.get("embedding"))
    else:
        d.pop("embedding", None)
    return d


def add_memory(
    account_id: str,
    content: str,
    chat_uid: str = "",
    kind: str = "fact",
    importance: int = 2,
    *,
    layer: str | None = None,
    subject: str = "contact",
    confidence: float = 0.8,
    pinned: bool = False,
    embedding: list[float] | None = None,
    expires_at: float = 0,
    source: str = "manual",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = (kind or "fact").strip() or "fact"
    if layer not in VALID_MEMORY_LAYERS:
        layer = "episodic" if kind == "event" else "core"
    subject = subject if subject in VALID_MEMORY_SUBJECTS else "contact"
    now = time.time()
    emb = json.dumps([float(x) for x in embedding], ensure_ascii=False) if embedding else ""
    meta_json = json.dumps(meta or {}, ensure_ascii=False, default=str)
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            """INSERT INTO memories(account_id,chat_uid,content,kind,importance,layer,subject,
                                    confidence,pinned,embedding,expires_at,source,meta,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id,
                chat_uid or "",
                content.strip(),
                kind,
                max(1, min(3, int(importance or 2))),
                layer,
                subject,
                max(0.0, min(1.0, float(confidence if confidence is not None else 0.8))),
                1 if pinned else 0,
                emb,
                float(expires_at or 0),
                source or "manual",
                meta_json,
                now,
                now,
            ),
        )
        conn.commit()
        mid = cur.lastrowid
    row = _conn().execute(
        f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id=?", (mid,)
    ).fetchone()
    return _memory_row(row)


def list_memories(
    account_id: str,
    keyword: str = "",
    limit: int = 500,
    *,
    layer: str = "",
    kind: str = "",
    chat_uid: str | None = None,
    pinned: bool | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """记忆页/工具用列表；默认不含已过期的 session 记忆，也不返回 embedding。"""
    sql = f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE account_id=?"
    args: list[Any] = [account_id]
    if keyword:
        sql += " AND content LIKE ?"
        args.append(f"%{keyword}%")
    if layer:
        sql += " AND layer=?"
        args.append(layer)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if chat_uid is not None:
        sql += " AND chat_uid=?"
        args.append(chat_uid)
    if pinned is not None:
        sql += " AND pinned=?"
        args.append(1 if pinned else 0)
    if not include_expired:
        sql += " AND (expires_at=0 OR expires_at>?)"
        args.append(time.time())
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    args.append(max(1, min(5000, int(limit or 500))))
    rows = _conn().execute(sql, args).fetchall()
    return [_memory_row(r) for r in rows]


def list_memories_for_context(
    account_id: str,
    chat_uid: str = "",
    *,
    include_expired: bool = False,
    with_embeddings: bool = False,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """检索候选：空 chat_uid 只取全局，非空取「全局 + 该会话」。"""
    cols = _MEMORY_COLUMNS + (",embedding" if with_embeddings else "")
    sql = f"SELECT {cols} FROM memories WHERE account_id=?"
    args: list[Any] = [account_id]
    if chat_uid:
        sql += " AND (chat_uid='' OR chat_uid=?)"
        args.append(chat_uid)
    else:
        sql += " AND chat_uid=''"
    if not include_expired:
        sql += " AND (expires_at=0 OR expires_at>?)"
        args.append(time.time())
    sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
    args.append(max(1, min(5000, int(limit or 2000))))
    rows = _conn().execute(sql, args).fetchall()
    return [_memory_row(r, include_embedding=with_embeddings) for r in rows]


def list_memories_with_embeddings(
    account_id: str,
    chat_uid: str | None = None,
    *,
    include_expired: bool = False,
    limit: int = 3000,
) -> list[dict[str, Any]]:
    """兼容旧调用：带 embedding 的候选集，调用方自己做余弦打分。"""
    sql = f"SELECT {_MEMORY_COLUMNS},embedding FROM memories WHERE account_id=?"
    args: list[Any] = [account_id]
    if chat_uid is not None:
        sql += " AND (chat_uid='' OR chat_uid=?)"
        args.append(chat_uid)
    if not include_expired:
        sql += " AND (expires_at=0 OR expires_at>?)"
        args.append(time.time())
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(max(1, min(5000, int(limit or 3000))))
    rows = _conn().execute(sql, args).fetchall()
    return [_memory_row(r, include_embedding=True) for r in rows]


def list_memories_missing_embeddings(account_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """补向量用：只查没有 embedding 的行，不解析已有向量。"""
    rows = _conn().execute(
        f"""SELECT {_MEMORY_COLUMNS} FROM memories
            WHERE account_id=? AND (embedding IS NULL OR embedding='')
              AND (expires_at=0 OR expires_at>?)
            ORDER BY updated_at DESC LIMIT ?""",
        (account_id, time.time(), max(1, min(2000, int(limit or 200)))),
    ).fetchall()
    return [_memory_row(r) for r in rows]


def search_memories(account_id: str, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    """recall 工具用：按关键词找该人格的记忆。"""
    rows = _conn().execute(
        f"""SELECT {_MEMORY_COLUMNS} FROM memories
            WHERE account_id=? AND content LIKE ?
              AND (expires_at=0 OR expires_at>?)
            ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?""",
        (account_id, f"%{keyword}%", time.time(), max(1, int(limit or 10))),
    ).fetchall()
    return [_memory_row(r) for r in rows]


def recall_memories(account_id: str, chat_uid: str, limit: int = 12) -> list[dict[str, Any]]:
    """注入用：当前会话的记忆 + 跨会话全局记忆，置顶/最近的优先。"""
    rows = _conn().execute(
        f"""SELECT {_MEMORY_COLUMNS} FROM memories
            WHERE account_id=? AND (chat_uid='' OR chat_uid=?)
              AND (expires_at=0 OR expires_at>?)
            ORDER BY pinned DESC, updated_at DESC LIMIT ?""",
        (account_id, chat_uid, time.time(), max(1, int(limit or 12))),
    ).fetchall()
    return [_memory_row(r) for r in rows]


def get_memory(account_id: str, mid: int) -> dict[str, Any] | None:
    row = _conn().execute(
        f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE account_id=? AND id=?",
        (account_id, int(mid)),
    ).fetchone()
    return _memory_row(row) if row else None


def find_memory_like(account_id: str, content: str, chat_uid: str = "") -> dict[str, Any] | None:
    """完全相同的归一化文本：抽取去重的最便宜一层。"""
    text = " ".join((content or "").split())
    if not text:
        return None
    row = _conn().execute(
        f"""SELECT {_MEMORY_COLUMNS} FROM memories
            WHERE account_id=? AND chat_uid=? AND TRIM(content)=?
              AND (expires_at=0 OR expires_at>?)
            ORDER BY updated_at DESC LIMIT 1""",
        (account_id, chat_uid or "", text, time.time()),
    ).fetchone()
    return _memory_row(row) if row else None


def update_memory(
    account_id: str,
    mid: int,
    content: str | None = None,
    kind: str | None = None,
    importance: int | None = None,
    *,
    layer: str | None = None,
    subject: str | None = None,
    confidence: float | None = None,
    pinned: bool | None = None,
    embedding: list[float] | None = None,
    expires_at: float | None = None,
    source: str | None = None,
    meta: dict[str, Any] | None = None,
) -> bool:
    sets: list[str] = ["updated_at=?"]
    args: list[Any] = [time.time()]
    clear_expiry = False
    if content is not None:
        sets.append("content=?")
        args.append(content.strip())
        # 内容变了，旧向量必然失效；调用方没给新向量就清空，避免拿错向量检索
        if embedding is None:
            sets.append("embedding=''")
    if kind is not None:
        sets.append("kind=?")
        args.append(kind)
    if importance is not None:
        sets.append("importance=?")
        args.append(max(1, min(3, int(importance))))
        if int(importance) >= 2:
            current = get_memory(account_id, int(mid))
            if current is not None and current.get("layer") != "session":
                clear_expiry = True
    if layer is not None:
        sets.append("layer=?")
        args.append(layer if layer in VALID_MEMORY_LAYERS else "core")
        # 从 session 提为 core/episodic 时清掉过期时间，否则摘要会按旧 TTL 消失
        if layer != "session" and expires_at is None:
            sets.append("expires_at=0")
    if subject is not None:
        sets.append("subject=?")
        args.append(subject if subject in VALID_MEMORY_SUBJECTS else "contact")
    if confidence is not None:
        sets.append("confidence=?")
        args.append(max(0.0, min(1.0, float(confidence))))
    if pinned is not None:
        sets.append("pinned=?")
        args.append(1 if pinned else 0)
        if pinned:
            current = get_memory(account_id, int(mid))
            if current is not None and current.get("layer") != "session":
                clear_expiry = True
    if embedding is not None:
        sets.append("embedding=?")
        args.append(json.dumps([float(x) for x in embedding], ensure_ascii=False) if embedding else "")
    if clear_expiry and expires_at is None:
        sets.append("expires_at=0")
    if expires_at is not None:
        sets.append("expires_at=?")
        args.append(float(expires_at))
    if source is not None:
        sets.append("source=?")
        args.append(source)
    if meta is not None:
        sets.append("meta=?")
        args.append(json.dumps(meta or {}, ensure_ascii=False, default=str))
    args += [account_id, int(mid)]
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE account_id=? AND id=?", args
        )
        conn.commit()
        return cur.rowcount > 0


def set_memory_embedding(account_id: str, mid: int, embedding: list[float]) -> bool:
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE memories SET embedding=? WHERE account_id=? AND id=?",
            (json.dumps([float(x) for x in embedding], ensure_ascii=False), account_id, int(mid)),
        )
        conn.commit()
        return cur.rowcount > 0


def clear_memory_embeddings(account_id: str) -> int:
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE memories SET embedding='' WHERE account_id=? AND embedding<>''",
            (account_id,),
        )
        conn.commit()
        return cur.rowcount


def touch_memories(account_id: str, mids: list[int]) -> int:
    ids = [int(m) for m in mids or [] if str(m).isdigit() and int(m) > 0]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    now = time.time()
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            f"""UPDATE memories SET last_access=?, access_count=access_count+1
                WHERE account_id=? AND id IN ({marks})""",
            (now, account_id, *ids),
        )
        conn.commit()
        return cur.rowcount


def delete_expired_memories(account_id: str) -> int:
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM memories WHERE account_id=? AND expires_at>0 AND expires_at<=?",
            (account_id, time.time()),
        )
        conn.commit()
        return cur.rowcount


def delete_memory(account_id: str, mid: int) -> bool:
    with _write_lock:
        conn = _conn()
        cur = conn.execute("DELETE FROM memories WHERE account_id=? AND id=?", (account_id, mid))
        conn.commit()
        return cur.rowcount > 0


def remove_memories(account_id: str, mids: list[int]) -> int:
    """批量删除记忆；别人的/不存在的 id 自动跳过，返回实际删除条数。"""
    ids: list[int] = []
    for mid in mids or []:
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            ids.append(mid)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            f"DELETE FROM memories WHERE account_id=? AND id IN ({marks})",
            (account_id, *ids),
        )
        conn.commit()
        return cur.rowcount


def count_memories(account_id: str, layer: str = "") -> int:
    sql = "SELECT COUNT(*) c FROM memories WHERE account_id=?"
    args: list[Any] = [account_id]
    if layer:
        sql += " AND layer=?"
        args.append(layer)
    row = _conn().execute(sql, args).fetchone()
    return int(row["c"])


def count_memories_since(account_id: str, since_ts: float) -> int:
    row = _conn().execute(
        "SELECT COUNT(*) AS n FROM memories WHERE account_id=? AND updated_at>=?",
        (account_id, float(since_ts)),
    ).fetchone()
    return int(row["n"] if row else 0)


def memory_stats(account_id: str) -> dict[str, Any]:
    now = time.time()
    rows = _conn().execute(
        """SELECT layer, COUNT(*) c FROM memories
           WHERE account_id=? AND (expires_at=0 OR expires_at>?)
           GROUP BY layer""",
        (account_id, now),
    ).fetchall()
    layers = {str(r["layer"] or "core"): int(r["c"]) for r in rows}
    pinned = _conn().execute(
        "SELECT COUNT(*) c FROM memories WHERE account_id=? AND pinned=1", (account_id,)
    ).fetchone()
    profiles = _conn().execute(
        "SELECT COUNT(*) c FROM memory_profiles WHERE account_id=?", (account_id,)
    ).fetchone()
    total = sum(layers.values())
    return {
        "total": total,
        "core": int(layers.get("core", 0)),
        "episodic": int(layers.get("episodic", 0)),
        "session": int(layers.get("session", 0)),
        "pinned": int(pinned["c"] if pinned else 0),
        "profiles": int(profiles["c"] if profiles else 0),
    }


def _profile_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("summary", "relationship", "preferences", "taboos", "recent_topics"):
        d[key] = d.get(key) or ""
    d["meta"] = _json_obj(d.get("meta"))
    d["updated_at"] = float(d.get("updated_at") or 0)
    return d


def get_memory_profile(account_id: str, chat_uid: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM memory_profiles WHERE account_id=? AND chat_uid=?",
        (account_id, chat_uid or ""),
    ).fetchone()
    return _profile_row(row) if row else None


def list_memory_profiles(account_id: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = _conn().execute(
        """SELECT p.*, c.name AS chat_name, c.is_group
           FROM memory_profiles p LEFT JOIN chats c
             ON c.account_id=p.account_id AND p.chat_uid=c.uid
           WHERE p.account_id=? ORDER BY p.updated_at DESC LIMIT ?""",
        (account_id, max(1, int(limit or 200))),
    ).fetchall()
    out = []
    for r in rows:
        d = _profile_row(r)
        d["chat_name"] = d.get("chat_name") or ""
        d["is_group"] = bool(d.get("is_group"))
        out.append(d)
    return out


def upsert_memory_profile(
    account_id: str,
    chat_uid: str,
    *,
    summary: str | None = None,
    relationship: str | None = None,
    preferences: str | None = None,
    taboos: str | None = None,
    recent_topics: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    meta_json = None if meta is None else json.dumps(meta or {}, ensure_ascii=False, default=str)
    with _write_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO memory_profiles(account_id,chat_uid,summary,relationship,preferences,
                                           taboos,recent_topics,meta,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_id,chat_uid) DO UPDATE SET
                 summary=COALESCE(?,summary),
                 relationship=COALESCE(?,relationship),
                 preferences=COALESCE(?,preferences),
                 taboos=COALESCE(?,taboos),
                 recent_topics=COALESCE(?,recent_topics),
                 meta=COALESCE(?,meta),
                 updated_at=excluded.updated_at""",
            (
                account_id,
                chat_uid or "",
                summary or "",
                relationship or "",
                preferences or "",
                taboos or "",
                recent_topics or "",
                meta_json or "{}",
                now,
                summary,
                relationship,
                preferences,
                taboos,
                recent_topics,
                meta_json,
            ),
        )
        conn.commit()
    return get_memory_profile(account_id, chat_uid) or {}


def delete_memory_profile(account_id: str, chat_uid: str) -> bool:
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM memory_profiles WHERE account_id=? AND chat_uid=?",
            (account_id, chat_uid or ""),
        )
        conn.commit()
        return cur.rowcount > 0


def iter_chats_for_stats() -> Iterable[dict[str, Any]]:
    rows = _conn().execute("SELECT * FROM chats ORDER BY last_ts DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 朋友圈
def _moment_row(row: sqlite3.Row, self_wxid: str = "") -> dict[str, Any]:
    d = dict(row)
    for key in ("images", "videos", "likes", "comments"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except Exception:
            d[key] = []
    d["is_self"] = bool(self_wxid) and d.get("username") == self_wxid
    return d


def upsert_moment(account_id: str, feed: dict[str, Any]) -> bool:
    """朋友圈动态入库（feed 为 wechatauto MomentDB.parse_feed 的结构）。

    返回是否为新动态；重复出现时刷新互动（点赞/评论随服务端更新）。
    """
    feed_id = str(feed.get("id") or "")
    if not feed_id:
        return False
    loc = feed.get("location") or {}
    location = ",".join(str(loc.get(k) or "") for k in ("latitude", "longitude")).strip(",")
    now = time.time()
    with _write_lock:
        conn = _conn()
        exists = conn.execute(
            "SELECT 1 FROM moments WHERE account_id=? AND feed_id=?", (account_id, feed_id)
        ).fetchone()
        conn.execute(
            """INSERT INTO moments(account_id,feed_id,username,nickname,text,location,
                                   create_time,images,videos,likes,comments,first_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_id, feed_id) DO UPDATE SET
                   text=excluded.text, likes=excluded.likes, comments=excluded.comments""",
            (
                account_id,
                feed_id,
                str(feed.get("username") or ""),
                str(feed.get("nickname") or ""),
                str(feed.get("text") or ""),
                location,
                float(feed.get("create_time") or 0),
                json.dumps(feed.get("images") or [], ensure_ascii=False),
                json.dumps(feed.get("videos") or [], ensure_ascii=False),
                json.dumps(feed.get("likes") or [], ensure_ascii=False),
                json.dumps(feed.get("comments") or [], ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        return exists is None


def list_moments(
    account_id: str, keyword: str = "", limit: int = 50, offset: int = 0, self_wxid: str = ""
) -> list[dict[str, Any]]:
    conn = _conn()
    if keyword:
        rows = conn.execute(
            """SELECT * FROM moments WHERE account_id=? AND (text LIKE ? OR nickname LIKE ?)
               ORDER BY create_time DESC LIMIT ? OFFSET ?""",
            (account_id, f"%{keyword}%", f"%{keyword}%", int(limit), int(offset)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM moments WHERE account_id=? ORDER BY create_time DESC LIMIT ? OFFSET ?",
            (account_id, int(limit), int(offset)),
        ).fetchall()
    return [_moment_row(r, self_wxid) for r in rows]


def count_moments(account_id: str, keyword: str = "") -> int:
    conn = _conn()
    if keyword:
        row = conn.execute(
            "SELECT COUNT(*) c FROM moments WHERE account_id=? AND (text LIKE ? OR nickname LIKE ?)",
            (account_id, f"%{keyword}%", f"%{keyword}%"),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) c FROM moments WHERE account_id=?", (account_id,)
        ).fetchone()
    return int(row["c"])


def get_moment(account_id: str, feed_id: str, self_wxid: str = "") -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM moments WHERE account_id=? AND feed_id=?", (account_id, feed_id)
    ).fetchone()
    return _moment_row(row, self_wxid) if row else None


def add_moment_interaction(account_id: str, item: dict[str, Any]) -> bool:
    """互动通知（赞/评论）入库，返回是否为新通知。"""
    local_id = int(item.get("local_id") or 0)
    if not local_id:
        return False
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            """INSERT OR IGNORE INTO moment_interactions(account_id,local_id,create_time,type,
                   feed_id,from_username,from_nickname,content,relative_me,seen)
               VALUES(?,?,?,?,?,?,?,?,?,0)""",
            (
                account_id,
                local_id,
                float(item.get("create_time") or 0),
                int(item.get("type") or 0),
                str(item.get("feed_id") or ""),
                str(item.get("from_username") or ""),
                str(item.get("from_nickname") or ""),
                str(item.get("content") or ""),
                1 if item.get("relative_me") else 0,
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def list_moment_interactions(
    account_id: str, limit: int = 50, only_unread: bool = False
) -> list[dict[str, Any]]:
    conn = _conn()
    where = " AND seen=0" if only_unread else ""
    rows = conn.execute(
        f"SELECT * FROM moment_interactions WHERE account_id=?{where} "
        "ORDER BY local_id DESC LIMIT ?",
        (account_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def count_unread_interactions(account_id: str) -> int:
    row = _conn().execute(
        "SELECT COUNT(*) c FROM moment_interactions WHERE account_id=? AND seen=0",
        (account_id,),
    ).fetchone()
    return int(row["c"])


def mark_moment_interactions_seen(account_id: str) -> int:
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "UPDATE moment_interactions SET seen=1 WHERE account_id=? AND seen=0",
            (account_id,),
        )
        conn.commit()
        return cur.rowcount


def clear_moments(account_id: str) -> dict[str, int]:
    """清空某个账号的朋友圈缓存（动态 + 互动通知），返回删除条数。

    用于账号切换重建：多开机器上历史上可能从另一个微信号的 sns.db 同步过
    朋友圈，缓存里会混进号主个人微信的好友动态；按当前解密账号重建前先清掉。
    """
    with _write_lock:
        conn = _conn()
        feeds = conn.execute(
            "DELETE FROM moments WHERE account_id=?", (account_id,)
        ).rowcount
        inters = conn.execute(
            "DELETE FROM moment_interactions WHERE account_id=?", (account_id,)
        ).rowcount
        conn.commit()
    return {"feeds": int(feeds or 0), "interactions": int(inters or 0)}


def _bili_run_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["meta"] = _json_obj(d.get("meta"))
    d["started_at"] = float(d.get("started_at") or 0)
    d["finished_at"] = float(d.get("finished_at") or 0)
    return d


def get_bili_patrol_run(run_id: int) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM bili_patrol_runs WHERE id=?", (run_id,)).fetchone()
    return _bili_run_row(row) if row else None


def add_bili_patrol_run(account_id: str, trigger: str, keyword: str = "") -> dict[str, Any]:
    now = time.time()
    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO bili_patrol_runs(account_id,started_at,status,trigger,keyword,meta) VALUES(?,?,?,?,?,?)",
            (account_id, now, "running", trigger, keyword, "{}"),
        )
        conn.commit()
        rid = cur.lastrowid
    return get_bili_patrol_run(rid) or {}


def update_bili_patrol_run(run_id: int, **fields: Any) -> bool:
    allowed = {"finished_at", "status", "report_path", "delivered_to", "error", "meta"}
    sets: list[str] = []
    args: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "meta" and isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False, default=str)
        sets.append(f"{key}=?")
        args.append(value)
    if not sets:
        return False
    args.append(run_id)
    with _write_lock:
        conn = _conn()
        cur = conn.execute(f"UPDATE bili_patrol_runs SET {', '.join(sets)} WHERE id=?", args)
        conn.commit()
        return cur.rowcount > 0


def list_bili_patrol_runs(account_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = _conn().execute(
        "SELECT * FROM bili_patrol_runs WHERE account_id=? ORDER BY id DESC LIMIT ?",
        (account_id, max(1, min(200, int(limit or 30)))),
    ).fetchall()
    return [_bili_run_row(r) for r in rows]


def count_bili_patrol_runs_today(account_id: str) -> int:
    row = _conn().execute(
        "SELECT COUNT(*) FROM bili_patrol_runs WHERE account_id=? AND status='ok' "
        "AND date(started_at,'unixepoch','localtime')=date('now','localtime')",
        (account_id,),
    ).fetchone()
    return int(row[0] if row else 0)


def last_bili_patrol_run(account_id: str) -> dict[str, Any] | None:
    row = _conn().execute(
        "SELECT * FROM bili_patrol_runs WHERE account_id=? ORDER BY id DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    return _bili_run_row(row) if row else None
