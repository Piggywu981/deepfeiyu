"""记忆 v2：分层、过滤、混合打分、档案、压缩、整理、API。"""
import asyncio
import time

import pytest

from app import db
from app.ai import memory


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    db.init()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


class ScriptLLM:
    configured = True

    def __init__(self, text: str = "这是一段摘要") -> None:
        self.text = text
        self.calls = 0

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        yield {"type": "text", "text": self.text}
        yield {"type": "finish", "reason": "stop"}


def test_layers_filters_and_stats(temp_db):
    db.add_memory("acc_a", "主人喜欢钓鱼", layer="core", importance=3)
    db.add_memory("acc_a", "周五去吃火锅", layer="episodic", kind="event")
    db.add_memory("acc_a", "过期摘要", layer="session", expires_at=time.time() - 10)
    db.add_memory("acc_a", "有效摘要", layer="session", expires_at=time.time() + 3600)

    assert [m["content"] for m in db.list_memories("acc_a", layer="core")] == ["主人喜欢钓鱼"]
    assert [m["content"] for m in db.list_memories("acc_a", layer="episodic")] == ["周五去吃火锅"]
    sessions = db.list_memories("acc_a", layer="session")
    assert [m["content"] for m in sessions] == ["有效摘要"]
    assert len(db.list_memories("acc_a", layer="session", include_expired=True)) == 2

    stats = db.memory_stats("acc_a")
    assert stats["total"] == 3
    assert stats["core"] == 1
    assert stats["episodic"] == 1
    assert stats["session"] == 1


def test_content_change_clears_stale_embedding(temp_db):
    row = db.add_memory("acc_a", "旧内容", embedding=[1.0, 0.0])
    assert db.list_memories_with_embeddings("acc_a")[0]["embedding"] == [1.0, 0.0]
    db.update_memory("acc_a", row["id"], content="新内容")
    assert db.list_memories_with_embeddings("acc_a")[0]["embedding"] == []
    db.set_memory_embedding("acc_a", row["id"], [0.0, 1.0])
    assert db.list_memories_with_embeddings("acc_a")[0]["embedding"] == [0.0, 1.0]


def test_score_prefers_keyword_importance_pinned_and_vector():
    now = time.time()
    base = {"content": "主人喜欢钓鱼", "importance": 3, "layer": "core", "updated_at": now}
    hit = memory.score_memory({**base, "pinned": False}, "钓鱼")
    miss = memory.score_memory({**base, "content": "今天天气不错"}, "钓鱼")
    assert hit > miss

    pinned = memory.score_memory({**base, "pinned": True}, "钓鱼")
    unpinned = memory.score_memory({**base, "pinned": False}, "钓鱼")
    assert pinned > unpinned

    vec_hit = memory.score_memory(
        {**base, "embedding": [1.0, 0.0]}, "钓鱼", query_vec=[1.0, 0.0]
    )
    vec_miss = memory.score_memory(
        {**base, "embedding": [0.0, 1.0]}, "钓鱼", query_vec=[1.0, 0.0]
    )
    assert vec_hit > vec_miss
    assert memory.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert memory.cosine([1, 0], [0, 1]) == pytest.approx(0.0)


def test_build_context_includes_profile_and_layers(temp_db):
    db.add_memory("acc_a", "主人叫老王，喜欢钓鱼", layer="core", importance=3)
    db.add_memory("acc_a", "周五约了去水库", layer="episodic", kind="event")
    db.add_memory("acc_a", "这段对话在聊周末安排", layer="session")
    db.upsert_memory_profile(
        "acc_a",
        "acc_a:c1",
        summary="老王是钓鱼爱好者",
        relationship="老朋友",
        preferences="钓鱼、安静",
        taboos="别约早起",
        recent_topics="周末水库",
    )
    block = memory.build_context("acc_a", "acc_a:c1", query="周末钓鱼", limit=10)
    assert "【你记得的事】" in block
    assert "【人物档案】" in block
    assert "老王是钓鱼爱好者" in block
    assert "【长期事实】" in block
    assert "主人叫老王" in block
    assert "【相关往事】" in block
    assert "周五约了去水库" in block
    assert "【最近会话】" in block

    rows = db.list_memories("acc_a", limit=10)
    assert all(m["access_count"] == 0 for m in rows)


def test_build_context_respects_char_budget(temp_db):
    db.add_memory("acc_a", "很长的记忆" * 200, layer="core")
    block = memory.build_context("acc_a", "acc_a:c1", query="记忆", limit=10)
    assert len(block) <= 1300
    assert "很长的记忆" in block


def test_retrieve_context_falls_back_without_embedding(temp_db, monkeypatch):
    class OffClient:
        configured = False

    monkeypatch.setattr(memory, "EmbeddingClient", OffClient)
    db.add_memory("acc_a", "主人喜欢钓鱼", layer="core")
    block = asyncio.run(memory.retrieve_context("acc_a", "acc_a:c1", "钓鱼"))
    assert "主人喜欢钓鱼" in block


def test_parse_extract_new_fields_keeps_legacy_shape():
    text = (
        '{"add": [{"content": "主人喜欢钓鱼", "kind": "preference", "layer": "core",'
        ' "subject": "contact", "importance": 3, "confidence": 0.9, "scope": "global"}]}'
    )
    out = memory.parse_extract_reply(text)
    assert out["add"][0] == {
        "content": "主人喜欢钓鱼",
        "kind": "preference",
        "importance": 3,
        "scope": "global",
        "layer": "core",
        "subject": "contact",
        "confidence": 0.9,
    }
    legacy = memory.parse_extract_reply('{"add": [{"content": "旧格式"}]}')
    assert legacy["add"][0] == {
        "content": "旧格式",
        "kind": "fact",
        "importance": 2,
        "scope": "global",
    }


def test_apply_decisions_dedupes_exact_text(temp_db):
    db.add_memory("acc_a", "主人喜欢钓鱼", layer="core")
    changes = memory.apply_decisions(
        "acc_a",
        "acc_a:c1",
        {"add": [{"content": "主人喜欢钓鱼", "kind": "preference", "importance": 3}]},
    )
    assert any("合并" in c for c in changes)
    rows = db.list_memories("acc_a")
    assert len(rows) == 1
    assert rows[0]["kind"] == "preference"
    assert rows[0]["importance"] == 3


def test_compress_history_stores_session_memory(temp_db, monkeypatch):
    monkeypatch.setattr(
        memory,
        "_cfg",
        lambda: {
            "compress_after_messages": 8,
            "compress_keep_recent": 4,
            "session_ttl_days": 7,
        },
    )
    history = [
        {"content": f"第{i}条消息", "is_self": False, "sender_name": "小王"} for i in range(10)
    ]
    changes = asyncio.run(
        memory.compress_history(ScriptLLM("周末约了去水库钓鱼"), "acc_a", "acc_a:c1", "小王", history)
    )
    assert changes
    rows = db.list_memories("acc_a", layer="session", chat_uid="acc_a:c1")
    assert len(rows) == 1
    assert rows[0]["content"] == "周末约了去水库钓鱼"
    assert rows[0]["expires_at"] > time.time()


def test_consolidate_merges_duplicates_and_rebuilds_profile(temp_db):
    db.add_memory("acc_a", "主人喜欢钓鱼", chat_uid="acc_a:c1", layer="core", importance=2)
    db.add_memory("acc_a", "主人喜欢钓鱼", chat_uid="acc_a:c1", layer="core", importance=3, pinned=True)
    db.add_memory("acc_a", "讨厌香菜", chat_uid="acc_a:c1", layer="core", kind="preference")
    result = memory.consolidate("acc_a")
    assert result["merged"] >= 1
    rows = db.list_memories("acc_a", chat_uid="acc_a:c1")
    assert len(rows) == 2
    kept = [m for m in rows if "钓鱼" in m["content"]][0]
    assert kept["pinned"] is True
    assert kept["importance"] == 3
    profile = db.get_memory_profile("acc_a", "acc_a:c1")
    assert profile and "钓鱼" in profile["summary"]


def test_rebuild_embeddings_reports_unconfigured(temp_db, monkeypatch):
    class OffClient:
        configured = False

    monkeypatch.setattr(memory, "EmbeddingClient", OffClient)
    result = asyncio.run(memory.rebuild_embeddings("acc_a"))
    assert result["ok"] is False
    assert "未配置" in result["error"]


class _FakeSession:
    pass


@pytest.fixture()
def api(temp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "acc_id", lambda request: "acc_a")
    monkeypatch.setattr(main, "session_of", lambda request: _FakeSession())
    return TestClient(main.app)


def test_memory_v2_api_filters_stats_profiles_and_import(api):
    db.add_memory("acc_a", "主人喜欢钓鱼", layer="core", importance=3)
    db.add_memory("acc_a", "周五去水库", layer="episodic", kind="event")
    db.upsert_memory_profile("acc_a", "acc_a:c1", summary="钓鱼爱好者")

    resp = api.get("/api/accounts/acc_a/memories?layer=core")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["content"] for m in body["items"]] == ["主人喜欢钓鱼"]
    assert body["stats"]["core"] == 1

    profiles = api.get("/api/accounts/acc_a/memory-profiles").json()["items"]
    assert profiles[0]["summary"] == "钓鱼爱好者"

    imported = api.post(
        "/api/accounts/acc_a/memories/import",
        json={"items": [{"content": "新导入的记忆", "layer": "core"}]},
    )
    assert imported.status_code == 200
    assert imported.json()["added"] == 1

    consolidated = api.post("/api/accounts/acc_a/memories/consolidate")
    assert consolidated.status_code == 200
    assert consolidated.json()["ok"] is True

    reindexed = api.post("/api/accounts/acc_a/memories/reindex")
    assert reindexed.status_code == 200
    assert reindexed.json()["ok"] is False  # 默认没配 embedding


def test_pinned_survives_budget_before_core(temp_db, monkeypatch):
    monkeypatch.setattr(memory, "_cfg", lambda: {"context_chars": 260, "max_items": 20})
    db.add_memory("acc_a", "置顶的往事" * 3, layer="episodic", pinned=True)
    for i in range(8):
        db.add_memory("acc_a", f"很长的事实{i}" * 20, layer="core")
    block = memory.build_context("acc_a", "acc_a:c1", query="往事", limit=20)
    assert "【置顶】" in block
    assert "置顶的往事" in block
    assert len(block) <= 400


def test_refresh_profile_scopes_global_and_chat(temp_db):
    db.add_memory("acc_a", "全局：主人叫老王", chat_uid="", layer="core")
    db.add_memory("acc_a", "会话：周五去水库", chat_uid="acc_a:c1", layer="episodic", kind="event")
    chat_profile = memory.refresh_profile("acc_a", "acc_a:c1")
    assert chat_profile and "老王" in chat_profile["summary"] and "水库" in chat_profile["recent_topics"]
    global_profile = memory.refresh_profile("acc_a", "")
    assert global_profile and "老王" in global_profile["summary"]
    assert "水库" not in (global_profile["summary"] + global_profile["recent_topics"])


def test_compress_history_watermark_prevents_repeat(temp_db, monkeypatch):
    monkeypatch.setattr(
        memory,
        "_cfg",
        lambda: {"compress_after_messages": 8, "compress_keep_recent": 4, "session_ttl_days": 7},
    )
    history = [
        {"content": f"第{i}条消息", "ts": float(i), "is_self": False, "sender_name": "小王"}
        for i in range(10)
    ]
    llm = ScriptLLM("第一次摘要")
    first = asyncio.run(memory.compress_history(llm, "acc_a", "acc_a:c1", "小王", history))
    assert first
    second = asyncio.run(memory.compress_history(llm, "acc_a", "acc_a:c1", "小王", history))
    assert second == []
    assert len(db.list_memories("acc_a", layer="session", chat_uid="acc_a:c1")) == 1


def test_duplicate_add_keeps_existing_kind(temp_db):
    db.add_memory("acc_a", "主人喜欢钓鱼", layer="core", kind="preference", importance=3)
    changes = memory.apply_decisions(
        "acc_a",
        "acc_a:c1",
        {"add": [{"content": "主人喜欢钓鱼", "_provided": ["content"], "kind": "fact", "importance": 2}]},
    )
    assert any("合并" in c for c in changes)
    row = db.list_memories("acc_a")[0]
    assert row["kind"] == "preference"
    assert row["importance"] == 3


def test_delete_string_not_split_into_digits():
    out = memory.parse_extract_reply('{"delete": "123"}')
    assert out["delete"] == [123]


def test_v5_migration_is_idempotent(temp_db):
    db.add_memory("acc_a", "周五去水库", layer="episodic", kind="event", source="manual")
    conn = db._conn()
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    db.init()
    row = db.list_memories("acc_a")[0]
    assert row["layer"] == "episodic"
    assert row["source"] == "manual"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_global_change_refreshes_chat_profiles(temp_db):
    db.add_memory("acc_a", "全局：主人叫老王", chat_uid="", layer="core")
    memory.refresh_profile("acc_a", "acc_a:c1")
    profile = db.get_memory_profile("acc_a", "acc_a:c1")
    assert profile and "老王" in profile["summary"]
    db.add_memory("acc_a", "全局：主人养了猫", chat_uid="", layer="core")
    memory.refresh_profiles_for_change("acc_a", "")
    profile = db.get_memory_profile("acc_a", "acc_a:c1")
    assert profile and "养了猫" in profile["summary"]


def test_delete_bool_and_float_rejected():
    assert memory.parse_extract_reply('{"delete": true}')["delete"] == []
    assert memory.parse_extract_reply('{"delete": [true, 1.5, 2]}')["delete"] == [2]


def test_promote_session_clears_ttl(temp_db):
    row = db.add_memory(
        "acc_a", "临时摘要", layer="session", expires_at=time.time() + 3600
    )
    assert row["expires_at"] > 0
    db.update_memory("acc_a", row["id"], layer="core")
    assert db.list_memories("acc_a")[0]["expires_at"] == 0


def test_similarity_detects_paraphrase_and_rejects_unrelated():
    a = "PiggyWu太困了不飞了准备睡觉，我调侃他开闸放水憋回去了"
    b = "PiggyWu说太困了不飞了准备睡觉，我调侃他开闸放水憋回去了"
    assert memory.similarity(a, b) >= 0.6
    assert memory.similarity("主人喜欢钓鱼", "今天天气不错") < 0.2
    assert memory.similarity("", "任何内容") == 0.0


def test_apply_decisions_normalizes_layer_and_importance(temp_db):
    decisions = {
        "add": [
            {
                "content": "老王是多年的钓友",
                "kind": "relationship",
                "confidence": 0.9,
                "scope": "global",
                "_provided": ["content", "kind", "confidence", "scope"],
            },
            {
                "content": "老王不喜欢早起",
                "kind": "preference",
                "scope": "global",
                "_provided": ["content", "kind", "scope"],
            },
            {
                "content": "周五去水库",
                "kind": "event",
                "scope": "chat",
                "_provided": ["content", "kind", "scope"],
            },
        ]
    }
    memory.apply_decisions("acc_a", "acc_a:c1", decisions)
    rows = {m["content"]: m for m in db.list_memories("acc_a")}
    assert rows["老王是多年的钓友"]["layer"] == "core"
    assert rows["老王不喜欢早起"]["layer"] == "core"
    assert rows["周五去水库"]["layer"] == "episodic"
    assert rows["周五去水库"]["importance"] == 1


def test_select_existing_keeps_core_and_relevant():
    rows = [
        {"id": 1, "content": "主人喜欢钓鱼", "layer": "core", "kind": "preference",
         "importance": 3, "pinned": False, "updated_at": 1},
        {"id": 2, "content": "今天天气不错", "layer": "episodic", "kind": "event",
         "importance": 1, "pinned": False, "updated_at": 3},
        {"id": 3, "content": "周末去水库钓鱼", "layer": "episodic", "kind": "event",
         "importance": 1, "pinned": False, "updated_at": 2},
    ]
    picked = memory._select_existing_for_extract(rows, "水库钓鱼", limit=2)
    ids = [m["id"] for m in picked]
    assert 1 in ids
    assert 3 in ids


def test_extract_prompt_requires_core_and_update():
    assert "layer=core" in memory._EXTRACT_INSTRUCTION
    assert "换说法" in memory._EXTRACT_INSTRUCTION
    assert "importance" in memory._EXTRACT_INSTRUCTION


def test_recent_enough_window():
    now = time.time()
    assert memory._recent_enough({"updated_at": now - 3600}, now)
    assert not memory._recent_enough({"updated_at": now - 73 * 3600}, now)


def test_apply_decisions_merges_near_duplicates(temp_db):
    first = "PiggyWu太困了不飞了准备睡觉，我调侃他开闸放水憋回去了，他回怼那不就炸膛了"
    second = "PiggyWu说太困了不飞了准备睡觉，我调侃他开闸放水憋回去了，他回怼那不就炸膛了"
    item = {"kind": "event", "scope": "chat", "_provided": ["content", "kind", "scope"]}
    memory.apply_decisions("acc_a", "acc_a:c1", {"add": [{**item, "content": first}]})
    memory.apply_decisions("acc_a", "acc_a:c1", {"add": [{**item, "content": second}]})
    rows = db.list_memories("acc_a", chat_uid="acc_a:c1")
    assert len(rows) == 1
    assert rows[0]["meta"]["merged_texts"]


def test_near_duplicate_does_not_cross_chats(temp_db):
    text = "周五去水库钓鱼，下雨了"
    item = {"kind": "event", "scope": "chat", "_provided": ["content", "kind", "scope"]}
    memory.apply_decisions("acc_a", "acc_a:c1", {"add": [{**item, "content": text}]})
    memory.apply_decisions("acc_a", "acc_a:c2", {"add": [{**item, "content": text}]})
    assert len(db.list_memories("acc_a")) == 2


def test_consolidate_merges_near_duplicates(temp_db):
    a = "PiggyWu太困了不飞了准备睡觉，我调侃他开闸放水憋回去了"
    b = "PiggyWu说太困了不飞了准备睡觉，我调侃他开闸放水憋回去了"
    db.add_memory("acc_a", a, chat_uid="acc_a:c1", kind="event", layer="episodic")
    db.add_memory("acc_a", b, chat_uid="acc_a:c1", kind="event", layer="episodic")
    result = memory.consolidate("acc_a")
    assert result["merged"] == 1
    assert len(db.list_memories("acc_a")) == 1


def test_profile_summary_non_empty_without_core(temp_db):
    db.add_memory("acc_a", "讨厌香菜", chat_uid="acc_a:c1", kind="preference",
                  layer="episodic", importance=2)
    db.add_memory("acc_a", "周五去水库", chat_uid="acc_a:c1", kind="event",
                  layer="episodic", importance=1)
    profile = memory.refresh_profile("acc_a", "acc_a:c1")
    assert profile["preferences"] == "讨厌香菜"
    assert "偏好：讨厌香菜" in profile["summary"]
    assert "最近：" in profile["summary"]


def test_profile_block_does_not_repeat_fields(temp_db):
    db.upsert_memory_profile(
        "acc_a", "acc_a:c1", summary="关系：老朋友；偏好：钓鱼",
        relationship="老朋友", preferences="钓鱼",
    )
    block = memory._profile_block(db.get_memory_profile("acc_a", "acc_a:c1"))
    assert block.count("关系：") == 1
    assert "偏好：钓鱼" in block


def test_default_episodic_ttl_config():
    from app import config

    assert config.DEFAULTS["memory"]["episodic_ttl_days"] == 30


def test_extract_event_gets_ttl_and_promotion_clears(temp_db):
    memory.apply_decisions(
        "acc_a", "acc_a:c1",
        {"add": [{"content": "周五去水库", "kind": "event", "scope": "chat",
                  "_provided": ["content", "kind", "scope"]}]},
    )
    row = db.list_memories("acc_a")[0]
    assert row["expires_at"] > time.time()
    db.update_memory("acc_a", row["id"], importance=2)
    assert db.get_memory("acc_a", row["id"])["expires_at"] == 0


def test_extract_core_has_no_ttl(temp_db):
    memory.apply_decisions(
        "acc_a", "acc_a:c1",
        {"add": [{"content": "主人叫老王", "kind": "fact", "scope": "global",
                  "_provided": ["content", "kind", "scope"]}]},
    )
    row = db.list_memories("acc_a")[0]
    assert row["layer"] == "core"
    assert row["expires_at"] == 0


def test_consolidate_backfills_ttl_and_promotes(temp_db):
    db.add_memory("acc_a", "旧的琐事", chat_uid="acc_a:c1", kind="event",
                  layer="episodic", importance=1, source="extract")
    db.add_memory("acc_a", "老王不喜欢早起", chat_uid="acc_a:c1", kind="preference",
                  layer="episodic", importance=2, source="extract")
    result = memory.consolidate("acc_a")
    rows = {m["content"]: m for m in db.list_memories("acc_a")}
    assert rows["旧的琐事"]["expires_at"] > time.time()
    assert rows["老王不喜欢早起"]["layer"] == "core"
    assert result["promoted"] == 1
    again = memory.consolidate("acc_a")
    assert again["ttl"] == 0
    assert again["promoted"] == 0


def test_decay_skips_core(temp_db):
    row = db.add_memory("acc_a", "主人叫老王", layer="core", importance=3)
    conn = db._conn()
    conn.execute(
        "UPDATE memories SET updated_at=?, last_access=0 WHERE id=?",
        (time.time() - 100 * 86400, row["id"]),
    )
    conn.commit()
    memory.consolidate("acc_a")
    assert db.get_memory("acc_a", row["id"])["importance"] == 3


def test_pin_session_keeps_ttl(temp_db):
    row = db.add_memory("acc_a", "临时摘要", layer="session",
                        expires_at=time.time() + 3600)
    db.update_memory("acc_a", row["id"], pinned=True)
    assert db.get_memory("acc_a", row["id"])["expires_at"] > 0


def test_raise_importance_session_keeps_ttl(temp_db):
    row = db.add_memory("acc_a", "临时摘要", layer="session",
                        expires_at=time.time() + 3600)
    db.update_memory("acc_a", row["id"], importance=3)
    assert db.get_memory("acc_a", row["id"])["expires_at"] > 0


def test_near_duplicate_promotion_clears_ttl(temp_db):
    a = "PiggyWu太困了不飞了准备睡觉，我调侃他开闸放水憋回去了"
    b = "PiggyWu说太困了不飞了准备睡觉，我调侃他开闸放水憋回去了"
    memory.apply_decisions(
        "acc_a", "acc_a:c1",
        {"add": [{"content": a, "kind": "event", "scope": "chat",
                  "_provided": ["content", "kind", "scope"]}]},
    )
    assert db.list_memories("acc_a")[0]["expires_at"] > 0
    memory.apply_decisions(
        "acc_a", "acc_a:c1",
        {"add": [{"content": b, "kind": "event", "scope": "chat", "importance": 3,
                  "_provided": ["content", "kind", "scope", "importance"]}]},
    )
    assert db.list_memories("acc_a")[0]["expires_at"] == 0


def test_recall_tool_touches_access(temp_db):
    import asyncio

    from app.ai.tools import build_tools

    row = db.add_memory("acc_a", "主人喜欢钓鱼", kind="fact")

    class Session:
        account_id = "acc_a"
        name = "A"
        llm = None

    tools = build_tools(Session(), allow_send=False, emoji_tools=False)
    tool = next(t for t in tools if t.name == "recall_memory")
    asyncio.run(tool.handler({"query": "钓鱼"}))
    assert db.get_memory("acc_a", row["id"])["access_count"] == 1
