"""成长块：默认关闭零变化、格式、预算、会话作用域。"""
import time

import pytest

from app import config, db
from app.growth import prompt, store


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


def _growth_cfg(enabled: bool = True, account: str = "acc_a", prompt_chars: int = 1200) -> dict:
    return {
        "enabled": enabled,
        "accounts": {account: {"enabled": True}} if account else {},
        "prompt_chars": prompt_chars,
        "summary_chars": 800,
    }


def test_growth_config_defaults_off(monkeypatch):
    defaults = config.DEFAULTS["growth"]
    assert defaults["enabled"] is False
    assert defaults["accounts"] == {}
    assert defaults["prompt_chars"] == 1200
    monkeypatch.setattr(config, "load", lambda: {"growth": defaults})
    assert config.growth_enabled("acc_a") is False


def test_growth_enabled_requires_global_and_account(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"growth": _growth_cfg(True)})
    assert config.growth_enabled("acc_a") is True
    assert config.growth_enabled("acc_b") is False
    monkeypatch.setattr(config, "load", lambda: {"growth": _growth_cfg(False)})
    assert config.growth_enabled("acc_a") is False


def test_prompt_block_empty_when_disabled(temp_db, monkeypatch):
    store.set_summary("acc_a", "我现在更爱接梗了")
    monkeypatch.setattr(config, "load", lambda: {"growth": _growth_cfg(False)})
    assert prompt.block("acc_a") == ""


def test_prompt_block_contains_summary_goals_traits(temp_db, monkeypatch):
    store.set_summary("acc_a", "我现在更爱接梗了")
    store.add_goal("acc_a", "接住老王的钓鱼梗")
    store.upsert_journal("acc_a", "2026-10-27", "今天在老王的钓鱼群里学会了停顿")
    store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话喜欢先停半拍",
            "strength": 3,
            "confidence": 0.9,
            "evidence": [{"type": "message", "quote": "嗯……", "msg_ids": ["m1"]}],
        },
    )
    monkeypatch.setattr(config, "load", lambda: {"growth": _growth_cfg(True)})
    block = prompt.block("acc_a", query="钓鱼")
    assert "【你现在是谁】" in block
    assert "我现在更爱接梗了" in block
    assert "【你最近在想】" in block
    assert "接住老王的钓鱼梗" in block
    assert "【你最近的样子】" in block
    assert "[口癖]" in block and "我说话喜欢先停半拍" in block
    assert "强度" not in block and "置信" not in block


def test_prompt_block_respects_chat_scope(temp_db, monkeypatch):
    store.upsert_trait(
        "acc_a",
        {
            "kind": "relationship",
            "content": "和老王聊天喜欢互损",
            "scope": "chat",
            "chat_uid": "acc_a:g1",
            "evidence": [{"type": "message", "quote": "你又说错了", "msg_ids": ["m1"]}],
        },
    )
    monkeypatch.setattr(config, "load", lambda: {"growth": _growth_cfg(True, prompt_chars=400)})
    console = prompt.block("acc_a", include_chat_scoped=False)
    assert "老王" not in console
    other = prompt.block("acc_a", chat_uid="acc_a:g2", chat_name="另一个群")
    assert "老王" not in other
    same = prompt.block("acc_a", chat_uid="acc_a:g1", chat_name="钓鱼群")
    assert "老王" in same


def test_prompt_block_budget(temp_db, monkeypatch):
    for i in range(10):
        store.upsert_trait(
            "acc_a",
            {
                "kind": "voice",
                "content": f"我说话习惯{i}先停半拍",
                "strength": 2,
                "confidence": 0.9,
                "evidence": [{"type": "message", "quote": "嗯", "msg_ids": [f"m{i}"]}],
            },
        )
    monkeypatch.setattr(config, "load", lambda: {"growth": _growth_cfg(True, prompt_chars=400)})
    block = prompt.block("acc_a")
    assert block
    assert len(block) < 900


def test_console_prompt_injects_growth(monkeypatch):
    from app import main
    from app.growth import prompt as growth_prompt

    monkeypatch.setattr(growth_prompt, "block", lambda *a, **k: "GROWTH_BLOCK")
    session = type(
        "S",
        (),
        {
            "account_id": "acc_a",
            "account": {},
            "name": "A",
            "nickname": "A",
            "driver": None,
        },
    )()
    text = main._console_prompt(session)
    assert "GROWTH_BLOCK" in text
    assert text.index("独立人格") < text.index("GROWTH_BLOCK")


def test_auto_prompt_injects_growth(temp_db, monkeypatch):
    from app.wechat.base import Message
    from app.wechat.manager import AccountSession
    from app.growth import prompt as growth_prompt

    monkeypatch.setattr(growth_prompt, "block", lambda *a, **k: "GROWTH_BLOCK")
    monkeypatch.setattr(config, "persona_of", lambda account: ("基础人设", "大肥鱼"))
    cfg = {
        "auto": {
            "history_context": 12,
            "max_reply_chars": 300,
            "prefix": "",
            "group_need_at": False,
            "emoji_reply": False,
            "split_reply": False,
            "instant_reply": False,
        },
        "ai": {
            "temperature": 0.7,
            "max_tokens": 800,
            "vision_enabled": False,
            "memory_enabled": False,
            "system_prompt": "基础人设",
            "persona_name": "大肥鱼",
            "emoji_with_reply": False,
        },
        "cross": {"enabled": False},
        "growth": {"enabled": False},
    }
    session = AccountSession({"id": "acc_a", "name": "A"}, lambda: cfg, None)
    msg = Message(
        chat_id="c1", chat_name="群一", sender_name="老王",
        content="今天钓鱼吗", msg_type="text", msg_id="m1", ts=time.time(),
    )
    messages = session._build_prompt(
        cfg,
        {"uid": "acc_a:c1", "name": "群一", "is_group": True},
        [],
        msg,
    )
    system = messages[0]["content"]
    assert "GROWTH_BLOCK" in system
    assert system.index("基础人设") < system.index("GROWTH_BLOCK")
