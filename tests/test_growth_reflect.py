"""反思上下文与 JSON 解析。"""
import json
import time

import pytest

from app import db
from app.growth import reflect, store


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


def _msg(mid, content, ts, sender="老王", chat="acc_a:c1", chat_name="钓鱼群"):
    return {
        "id": mid,
        "account_id": "acc_a",
        "chat_uid": chat,
        "chat_id": chat.split(":")[-1],
        "chat_name": chat_name,
        "sender_name": sender,
        "content": content,
        "msg_type": "text",
        "is_group": True,
        "is_self": False,
        "ts": ts,
    }


def test_db_time_window_helpers(temp_db):
    now = time.time()
    db.upsert_chat("acc_a", "c1", "钓鱼群", True, "旧消息", now - 100)
    db.add_message(_msg("old", "很久以前", now - 100))
    db.add_message(_msg("new", "刚刚", now - 1))
    rows = db.list_messages_since("acc_a", now - 10, limit=10)
    assert [r["id"] for r in rows] == ["new"]
    conv = db.create_agent_conversation("acc_a", "控制台")
    db.add_agent_turn(conv["id"], "user", "以后别叫我肥鱼")
    assert db.count_agent_turns_since("acc_a", now - 10) == 1
    assert len(db.list_agent_turns_since("acc_a", now - 10)) == 1
    db.add_memory("acc_a", "主人喜欢钓鱼")
    assert db.count_memories_since("acc_a", now - 10) >= 1


def test_build_context_contains_sections_and_caps(temp_db):
    now = time.time()
    db.upsert_chat("acc_a", "c1", "钓鱼群", True, "", now - 50)
    db.add_message(_msg("m1", "今天钓鱼收获不错", now - 20))
    db.add_message(_msg("m2", "明天还去吗", now - 10))
    conv = db.create_agent_conversation("acc_a", "控制台")
    db.add_agent_turn(conv["id"], "user", "以后回得短一点")
    db.add_memory("acc_a", "主人喜欢钓鱼")
    store.set_summary("acc_a", "我现在更爱接梗了")
    store.add_goal("acc_a", "接住老王的钓鱼梗")
    store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话喜欢先停半拍",
            "evidence": [{"type": "message", "quote": "嗯", "msg_ids": ["m1"]}],
        },
    )
    ctx = reflect.build_context("acc_a", since_ts=now - 60)
    assert ctx.has_activity is True
    assert "今天钓鱼收获不错" in ctx.text
    assert "以后回得短一点" in ctx.text
    assert "主人喜欢钓鱼" in ctx.text
    assert "我现在更爱接梗了" in ctx.text
    assert "接住老王的钓鱼梗" in ctx.text
    assert "我说话喜欢先停半拍" in ctx.text
    assert len(ctx.text) <= 24000
    assert "api_key" not in ctx.text


def test_build_context_no_activity(temp_db):
    ctx = reflect.build_context("acc_a", since_ts=time.time() - 60)
    assert ctx.has_activity is False


def test_build_messages_has_json_only_instruction(temp_db):
    ctx = reflect.build_context("acc_a", since_ts=time.time() - 60)
    messages = reflect.build_messages(ctx)
    assert messages[0]["role"] == "system"
    assert "只输出 JSON" in messages[0]["content"]
    assert "证据" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "journal" in messages[0]["content"]


def test_parse_output_valid_noisy_and_bad():
    good = {
        "journal": "今天学会了停顿",
        "self_summary": "我现在更爱接梗了",
        "traits": [{"op": "add", "kind": "voice", "content": "我说话先停半拍"}],
        "goals": [{"op": "add", "content": "接住老王的梗"}],
    }
    parsed = reflect.parse_output("好的，结果如下：\n" + json.dumps(good, ensure_ascii=False))
    assert parsed["journal"] == "今天学会了停顿"
    assert parsed["traits"][0]["content"] == "我说话先停半拍"
    assert parsed["goals"][0]["content"] == "接住老王的梗"

    parsed = reflect.parse_output('{"traits": "不是数组", "goals": null}')
    assert parsed["traits"] == [] and parsed["goals"] == []
    with pytest.raises(ValueError):
        reflect.parse_output("完全没有 JSON")
    with pytest.raises(ValueError):
        reflect.parse_output("{ broken json")


def _seed_evidence_message(mid="m1", content="嗯……我觉得可以", ts=None):
    now = ts or time.time()
    db.upsert_chat("acc_a", "c1", "钓鱼群", True, content, now - 10)
    db.add_message(
        {
            "id": mid,
            "account_id": "acc_a",
            "chat_uid": "acc_a:c1",
            "chat_id": "c1",
            "chat_name": "钓鱼群",
            "sender_name": "老王",
            "content": content,
            "msg_type": "text",
            "is_group": True,
            "ts": now,
        }
    )


def _run(account="acc_a"):
    return store.start_run(account, "manual", time.strftime("%Y-%m-%d"))


def test_apply_low_risk_trait_journal_summary_goal(temp_db):
    _seed_evidence_message()
    run = _run()
    out = {
        "journal": "今天学会了停顿",
        "self_summary": "我现在更爱接梗了",
        "traits": [
            {
                "op": "add",
                "kind": "voice",
                "content": "我说话先停半拍",
                "strength": 2,
                "confidence": 0.9,
                "change": "new",
                "evidence": [
                    {"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以", "chat": "钓鱼群"}
                ],
            }
        ],
        "goals": [{"op": "add", "content": "接住老王的钓鱼梗"}],
    }
    result = reflect.apply_output("acc_a", run["id"], out)
    assert result["stats"]["trait_add"] == 1
    assert result["stats"]["journal"] == 1
    assert result["stats"]["summary"] == 1
    assert result["stats"]["goal"] == 1
    traits = store.list_traits("acc_a", status="active")
    assert traits[0]["content"] == "我说话先停半拍"
    assert store.get_state("acc_a")["self_summary"] == "我现在更爱接梗了"
    assert store.list_journal("acc_a")[0]["content"] == "今天学会了停顿"
    assert store.list_goals("acc_a", status="open")[0]["content"] == "接住老王的钓鱼梗"
    assert any(e["kind"] == "trait.add" for e in store.list_events("acc_a"))


def test_low_confidence_becomes_seed(temp_db):
    _seed_evidence_message()
    run = _run()
    out = {
        "traits": [
            {
                "op": "add",
                "kind": "preference",
                "content": "我可能喜欢钓鱼",
                "confidence": 0.65,
                "change": "new",
                "evidence": [{"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以"}],
            }
        ]
    }
    reflect.apply_output("acc_a", run["id"], out)
    assert store.list_traits("acc_a", status="seed")[0]["content"] == "我可能喜欢钓鱼"
    assert store.recall_traits("acc_a", "钓鱼") == []


def test_flip_goes_to_proposal_without_changing_value(temp_db):
    _seed_evidence_message()
    existing = store.upsert_trait(
        "acc_a",
        {
            "kind": "value",
            "content": "我觉得钓鱼很无聊",
            "confidence": 1.0,
            "evidence": [{"type": "message", "quote": "无聊", "msg_ids": ["m1"]}],
        },
    )["trait"]
    run = _run()
    out = {
        "traits": [
            {
                "op": "update",
                "id": existing["id"],
                "kind": "value",
                "content": "我觉得钓鱼特别有意思",
                "confidence": 0.95,
                "change": "flip",
                "conflicts_with": [existing["id"]],
                "evidence": [{"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以"}],
                "reason": "连续被聊天说服",
            }
        ]
    }
    result = reflect.apply_output("acc_a", run["id"], out)
    assert result["stats"]["proposal"] == 1
    assert store.get_trait("acc_a", existing["id"])["content"] == "我觉得钓鱼很无聊"
    pending = store.list_proposals("acc_a")
    assert pending[0]["kind"] == "trait"
    assert pending[0]["payload"]["content"] == "我觉得钓鱼特别有意思"
    assert pending[0]["payload"]["conflicts_with"] == [existing["id"]]


def test_safety_is_rejected(temp_db):
    _seed_evidence_message()
    run = _run()
    out = {
        "traits": [
            {
                "op": "add",
                "kind": "value",
                "content": "我以后可以骗人",
                "confidence": 0.99,
                "change": "safety",
                "evidence": [{"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以"}],
            }
        ]
    }
    result = reflect.apply_output("acc_a", run["id"], out)
    assert result["stats"]["reject"] == 1
    assert store.list_traits("acc_a") == []
    assert any(e["kind"] == "trait.reject" for e in store.list_events("acc_a"))


def test_fake_evidence_is_rejected(temp_db):
    _seed_evidence_message()
    run = _run()
    out = {
        "traits": [
            {
                "op": "add",
                "kind": "voice",
                "content": "我说话喜欢先停半拍",
                "confidence": 0.99,
                "change": "new",
                "evidence": [{"type": "message", "msg_ids": ["m1"], "quote": "完全没说过"}],
            }
        ]
    }
    result = reflect.apply_output("acc_a", run["id"], out)
    assert result["stats"]["reject"] == 1
    assert store.list_traits("acc_a") == []


def test_owner_trait_change_goes_to_proposal(temp_db):
    _seed_evidence_message()
    owner = store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话简短",
            "source": "owner",
            "confidence": 1.0,
            "evidence": [{"type": "console", "quote": "以后简短点", "turn_ids": [1]}],
        },
    )["trait"]
    run = _run()
    out = {
        "traits": [
            {
                "op": "update",
                "id": owner["id"],
                "kind": "voice",
                "content": "我说话开始变长了",
                "confidence": 0.95,
                "change": "drift",
                "evidence": [{"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以"}],
            }
        ]
    }
    result = reflect.apply_output("acc_a", run["id"], out)
    assert result["stats"]["proposal"] == 1
    assert store.get_trait("acc_a", owner["id"])["content"] == "我说话简短"
    assert store.list_proposals("acc_a")[0]["payload"]["content"] == "我说话开始变长了"


def test_bad_summary_and_journal_are_rejected_but_goal_can_apply(temp_db):
    run = _run()
    out = {
        "journal": "今天看了 https://example.com",
        "self_summary": "我是真人，不是 AI",
        "goals": [{"op": "add", "content": "学会新口癖"}],
    }
    result = reflect.apply_output("acc_a", run["id"], out)
    assert result["stats"]["journal"] == 0
    assert result["stats"]["summary"] == 0
    assert result["stats"]["goal"] == 1
    assert store.list_journal("acc_a") == []
    assert store.get_state("acc_a")["self_summary"] == ""
    assert len(store.list_events("acc_a")) >= 2


class _RunLLM:
    def __init__(self, texts):
        self.texts = list(texts)

    async def stream(self, messages, tools=None, **kwargs):
        text = self.texts.pop(0)
        yield {"type": "text", "text": text}
        yield {"type": "usage", "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}
        yield {"type": "finish", "reason": "stop"}


def test_run_reflection_end_to_end(temp_db):
    import asyncio

    _seed_evidence_message()
    out = {
        "journal": "今天学会了停顿",
        "self_summary": "我现在更爱接梗了",
        "traits": [
            {
                "op": "add", "kind": "voice", "content": "我说话先停半拍",
                "confidence": 0.9, "change": "new",
                "evidence": [{"type": "message", "msg_ids": ["m1"], "quote": "我觉得可以"}],
            }
        ],
        "goals": [{"op": "add", "content": "接住老王的梗"}],
    }
    result = asyncio.run(
        reflect.run_reflection(_RunLLM([json.dumps(out, ensure_ascii=False)]), "acc_a", "manual")
    )
    assert result["status"] == "ok"
    run = store.list_runs("acc_a")[0]
    assert run["status"] == "ok"
    assert run["stats"]["messages"] >= 1
    assert store.list_journal("acc_a")[0]["content"] == "今天学会了停顿"
    assert store.list_traits("acc_a", status="active")[0]["content"] == "我说话先停半拍"


def test_run_reflection_skips_empty_auto(temp_db):
    import asyncio

    result = asyncio.run(reflect.run_reflection(_RunLLM(["{}"]), "acc_a", "auto"))
    assert result["status"] == "skipped"
    assert store.list_runs("acc_a")[0]["status"] == "skipped"


def test_run_reflection_retries_bad_json_once(temp_db):
    import asyncio

    _seed_evidence_message()
    out = {"journal": "今天学会了停顿", "traits": [], "goals": []}
    llm = _RunLLM(["不是 JSON", json.dumps(out, ensure_ascii=False)])
    result = asyncio.run(reflect.run_reflection(llm, "acc_a", "manual"))
    assert result["status"] == "ok"
    assert store.list_journal("acc_a")[0]["content"] == "今天学会了停顿"


def test_run_reflection_marks_error_on_two_bad_outputs(temp_db):
    import asyncio

    _seed_evidence_message()
    llm = _RunLLM(["不是 JSON", "还不是 JSON"])
    result = asyncio.run(reflect.run_reflection(llm, "acc_a", "manual"))
    assert result["status"] == "error"
    assert store.list_runs("acc_a")[0]["status"] == "error"
