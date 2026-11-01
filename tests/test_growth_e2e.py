"""心脏端到端：反思 → 自动成长 → 成长块 → 回滚 → 关闭零变化。"""
import asyncio
import copy
import json
import time

import pytest

from app import config, db
from app.growth import prompt, reflect, store


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


class FakeLLM:
    def __init__(self, text):
        self.text = text

    async def stream(self, messages, tools=None, **kwargs):
        yield {"type": "text", "text": self.text}
        yield {"type": "usage", "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}
        yield {"type": "finish", "reason": "stop"}


def test_heart_end_to_end(temp_db, monkeypatch):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["growth"].update(
        {"enabled": True, "accounts": {"acc_a": {"enabled": True}}}
    )
    cfg["accounts"] = [
        {
            "id": "acc_a",
            "name": "A",
            "nickname": "A",
            "persona": {"system_prompt": "你是大肥鱼", "persona_name": "大肥鱼"},
        }
    ]
    monkeypatch.setattr(config, "load", lambda: cfg)

    now = time.time()
    db.upsert_chat("acc_a", "c1", "钓鱼群", True, "今天钓鱼吗", now - 10)
    db.add_message(
        {
            "id": "m1",
            "account_id": "acc_a",
            "chat_uid": "acc_a:c1",
            "chat_id": "c1",
            "chat_name": "钓鱼群",
            "sender_name": "老王",
            "content": "今天钓鱼吗，我觉得可以",
            "msg_type": "text",
            "is_group": True,
            "ts": now - 5,
        }
    )

    out = {
        "journal": "今天在钓鱼群里学会了停顿",
        "self_summary": "我现在更爱接梗了",
        "traits": [
            {
                "op": "add",
                "kind": "voice",
                "content": "我说话先停半拍",
                "confidence": 0.9,
                "change": "new",
                "evidence": [
                    {"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以"}
                ],
            }
        ],
        "goals": [{"op": "add", "content": "接住老王的钓鱼梗"}],
    }
    result = asyncio.run(
        reflect.run_reflection(
            FakeLLM(json.dumps(out, ensure_ascii=False)), "acc_a", "manual"
        )
    )
    assert result["status"] == "ok"
    assert store.list_journal("acc_a")[0]["content"].startswith("今天在钓鱼群")
    assert store.list_traits("acc_a", status="active")[0]["content"] == "我说话先停半拍"

    block = prompt.block("acc_a", query="钓鱼", chat_uid="acc_a:c1", chat_name="钓鱼群")
    assert "我现在更爱接梗了" in block
    assert "我说话先停半拍" in block

    # 回滚特质新增
    event = next(e for e in store.list_events("acc_a") if e["kind"] == "trait.add")
    store.rollback_event("acc_a", event["id"])
    assert store.get_trait("acc_a", int(event["entity_id"]))["status"] == "retired"

    # 关闭后提示词零变化
    cfg["growth"]["enabled"] = False
    assert prompt.block("acc_a", query="钓鱼") == ""
