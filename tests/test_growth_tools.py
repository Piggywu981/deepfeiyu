"""成长只读工具：控制台有、自动回复没有。"""
import asyncio

import pytest

from app import config, db
from app.ai.tools import build_tools
from app.growth import store
from app.wechat.manager import AccountSession


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


def _session():
    cfg = {
        "ai": {"temperature": 0.7, "max_tokens": 800, "memory_enabled": False},
        "auto": {},
        "cross": {},
        "growth": {"enabled": True, "accounts": {"acc_a": {"enabled": True}}},
        "capabilities": {"enabled": False},
    }
    return AccountSession({"id": "acc_a", "name": "A"}, lambda: cfg, None)


def test_growth_tools_only_in_console(temp_db, monkeypatch):
    monkeypatch.setattr(config, "growth_enabled", lambda aid: aid == "acc_a")
    session = _session()
    console = {t.name for t in build_tools(session, tool_context="console")}
    auto = {t.name for t in build_tools(session, tool_context="auto", chat_uid="acc_a:c1")}
    assert "recall_growth" in console and "read_journal" in console
    assert "recall_growth" not in auto and "read_journal" not in auto


def test_recall_growth_tool_reads_traits_and_journal(temp_db, monkeypatch):
    monkeypatch.setattr(config, "growth_enabled", lambda aid: True)
    store.upsert_trait(
        "acc_a",
        {
            "kind": "voice",
            "content": "我说话先停半拍",
            "evidence": [{"type": "message", "quote": "嗯", "msg_ids": ["m1"]}],
        },
    )
    store.upsert_journal("acc_a", "2026-10-27", "今天学会了停顿")
    session = _session()
    tools = {t.name: t for t in build_tools(session, tool_context="console")}
    result = asyncio.run(tools["recall_growth"].handler({"query": "口癖"}))
    assert result["traits"][0]["content"] == "我说话先停半拍"
    assert result["journal"][0]["content"] == "今天学会了停顿"

    journal = asyncio.run(tools["read_journal"].handler({"limit": 5}))
    assert journal["items"][0]["day"] == "2026-10-27"
