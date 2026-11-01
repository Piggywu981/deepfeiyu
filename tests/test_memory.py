"""人格长期记忆：db CRUD、会话/全局过滤、注入块、提取决策解析。"""
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


def test_memory_crud_roundtrip(temp_db):
    row = db.add_memory("acc_a", "主人叫老王，喜欢钓鱼", kind="fact", importance=3)
    assert row["id"] > 0
    assert row["chat_uid"] == ""
    assert row["importance"] == 3

    items = db.list_memories("acc_a")
    assert len(items) == 1
    assert items[0]["content"] == "主人叫老王，喜欢钓鱼"

    assert db.update_memory("acc_a", row["id"], content="主人叫老王，喜欢爬山")
    assert db.list_memories("acc_a")[0]["content"] == "主人叫老王，喜欢爬山"

    # 记忆按账号隔离
    assert db.list_memories("acc_b") == []
    assert db.update_memory("acc_b", row["id"], content="越权") is False

    assert db.delete_memory("acc_a", row["id"]) is True
    assert db.list_memories("acc_a") == []


def test_recall_filters_by_chat_and_global(temp_db):
    db.add_memory("acc_a", "小王的生日是6月12日")  # 全局
    db.add_memory("acc_a", "这个群每周五聚餐", chat_uid="acc_a:group_1")
    db.add_memory("acc_a", "别的会话的记忆", chat_uid="acc_a:group_2")

    got = db.recall_memories("acc_a", "acc_a:group_1", limit=12)
    contents = {m["content"] for m in got}
    assert contents == {"小王的生日是6月12日", "这个群每周五聚餐"}

    got = db.recall_memories("acc_a", "acc_a:group_2", limit=12)
    assert {m["content"] for m in got} == {"小王的生日是6月12日", "别的会话的记忆"}


def test_inject_block_format(temp_db):
    assert memory.inject_block("acc_a", "uid") == ""

    db.add_memory("acc_a", "主人喜欢钓鱼")
    db.add_memory("acc_a", "讨厌香菜", kind="preference")
    block = memory.inject_block("acc_a", "acc_a:c1", limit=12)
    assert "【你的长期记忆】" in block
    assert "- 主人喜欢钓鱼" in block
    assert "- [preference] 讨厌香菜" in block
    # limit 生效
    block = memory.inject_block("acc_a", "acc_a:c1", limit=1)
    assert block.count("\n- ") == 1


def test_parse_extract_reply_handles_noise():
    assert memory.parse_extract_reply("") == {"add": [], "update": [], "delete": []}
    assert memory.parse_extract_reply("我觉得没什么可记的") == {"add": [], "update": [], "delete": []}
    assert memory.parse_extract_reply("{ broken json") == {"add": [], "update": [], "delete": []}

    text = '好的，这是结果：\n{"add": [{"content": "用户叫小明", "kind": "relationship", "scope": "chat"}], "update": [{"id": "3", "content": "换新工作了"}], "delete": ["x", 7]}'
    out = memory.parse_extract_reply(text)
    assert out["add"] == [
        {"content": "用户叫小明", "kind": "relationship", "importance": 2, "scope": "chat"}
    ]
    assert out["update"] == [{"id": 3, "content": "换新工作了"}]
    assert out["delete"] == [7]


def test_parse_extract_reply_caps_adds(temp_db):
    data = {"add": [{"content": f"记忆{i}"} for i in range(10)]}
    out = memory.parse_extract_reply(__import__("json").dumps(data, ensure_ascii=False))
    assert len(out["add"]) == memory.MAX_ADDS


def test_apply_decisions_writes_db(temp_db):
    mid = db.add_memory("acc_a", "在A公司上班")["id"]
    changes = memory.apply_decisions(
        "acc_a",
        "acc_a:c1",
        {
            "add": [{"content": "用户叫小明", "kind": "fact", "importance": 3, "scope": "global"},
                    {"content": "只在群1说的事", "kind": "event", "importance": 2, "scope": "chat"}],
            "update": [{"id": mid, "content": "跳槽到B公司了"}],
            "delete": [99999],
        },
    )
    assert len(changes) == 3

    items = {m["content"]: m for m in db.list_memories("acc_a")}
    assert "跳槽到B公司了" in items
    assert "在A公司上班" not in items
    assert items["用户叫小明"]["chat_uid"] == ""  # global
    assert items["只在群1说的事"]["chat_uid"] == "acc_a:c1"  # chat 作用域


# ------------------------------------------------------------- 控制台多选批处理
def test_remove_memories_skips_foreign_and_bad_ids(temp_db):
    a1 = db.add_memory("acc_a", "记忆一")["id"]
    a2 = db.add_memory("acc_a", "记忆二")["id"]
    foreign = db.add_memory("acc_b", "别人的记忆")["id"]
    deleted = db.remove_memories("acc_a", [a1, a2, foreign, 99999, "不是数字"])
    assert deleted == 2
    assert [m["content"] for m in db.list_memories("acc_a")] == []
    assert db.list_memories("acc_b")[0]["content"] == "别人的记忆"  # 别人账号的动不了


def test_remove_memories_empty_noop(temp_db):
    row = db.add_memory("acc_a", "还在")
    assert db.remove_memories("acc_a", []) == 0
    assert db.list_memories("acc_a")[0]["id"] == row["id"]


class _FakeSession:
    pass


@pytest.fixture()
def api(temp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "acc_id", lambda request: "acc_a")
    monkeypatch.setattr(main, "session_of", lambda request: _FakeSession())
    return TestClient(main.app)  # 不用 with，避免触发 lifespan 启动 Hub


def test_batch_delete_endpoint_removes_only_own(api):
    a1 = db.add_memory("acc_a", "记忆一")["id"]
    a2 = db.add_memory("acc_a", "记忆二")["id"]
    foreign = db.add_memory("acc_b", "别人的记忆")["id"]
    resp = api.post("/api/accounts/acc_a/memories/batch-delete", json={"ids": [a1, a2, foreign]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted": 2}
    assert db.list_memories("acc_a") == []
    assert db.list_memories("acc_b")[0]["content"] == "别人的记忆"


def test_batch_delete_rejects_other_account(api):
    row = db.add_memory("acc_b", "别人的记忆")
    resp = api.post("/api/accounts/acc_b/memories/batch-delete", json={"ids": [row["id"]]})
    assert resp.status_code == 400
    assert db.list_memories("acc_b")[0]["id"] == row["id"]


def test_batch_delete_endpoint_empty_ids_noop(api):
    row = db.add_memory("acc_a", "还在")
    resp = api.post("/api/accounts/acc_a/memories/batch-delete", json={"ids": []})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
    assert db.list_memories("acc_a")[0]["id"] == row["id"]
