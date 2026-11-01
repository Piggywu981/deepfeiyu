"""控制台智能体的余额 / 上轮话费工具：注册范围与返回口径。"""
from __future__ import annotations

import asyncio

import pytest

from app import config, db
from app.ai import tools as tools_mod


class FakeLLM:
    cfg = {"api_key": "sk-test", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}


class FakeSession:
    account_id = "acc_bill"
    name = "测试号"

    def __init__(self) -> None:
        self.llm = FakeLLM()
        self.moments = None


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
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


def _tool(conversation_id: str = "", billing: bool = True):
    items = tools_mod.build_tools(
        FakeSession(),
        allow_send=True,
        billing_tools=billing,
        conversation_id=conversation_id,
    )
    return {t.name: t for t in items}


def test_billing_tools_only_for_console():
    tools = _tool(billing=True)
    assert "get_whale_balance" in tools
    assert "get_last_turn_cost" in tools
    # 外部自动回复不注册：余额不能暴露给微信聊天对象
    assert "get_whale_balance" not in _tool(billing=False)
    assert "get_last_turn_cost" not in _tool(billing=False)


def test_whale_balance_tool_maps_payload(monkeypatch):
    async def fake_payload(cfg, refresh=False):
        assert cfg["api_key"] == "sk-test"
        return {
            "ok": True,
            "totalBalance": 12.34,
            "currency": "CNY",
            "bonusBalance": 2.0,
            "rechargeBalance": 10.34,
            "todayUsage": 0.05,
            "usageLabel": "本地估算",
            "updatedAt": "2026-10-26T10:00:00+08:00",
        }

    monkeypatch.setattr(tools_mod.whale, "balance_payload", fake_payload)
    result = asyncio.run(_tool()["get_whale_balance"].handler({}))
    assert result["ok"] is True
    assert result["total_balance"] == 12.34
    assert result["today_usage"] == 0.05
    assert "余额 ¥12.34" in result["summary"]
    assert "今日已用" in result["summary"]


def test_whale_balance_tool_surfaces_error(monkeypatch):
    async def fake_payload(cfg, refresh=False):
        return {
            "ok": False,
            "code": "NO_BALANCE_API",
            "error": "MiMo 没有官方余额查询接口",
            "todayUsage": 0.05,
            "usageLabel": "本地估算",
        }

    monkeypatch.setattr(tools_mod.whale, "balance_payload", fake_payload)
    result = asyncio.run(_tool()["get_whale_balance"].handler({}))
    assert result["ok"] is False
    assert result["code"] == "NO_BALANCE_API"
    assert "余额查询接口" in result["error"]
    assert result["today_usage"] == 0.05
    assert result["usage_label"] == "本地估算"


def test_last_turn_cost_reads_current_conversation(temp_db):
    cid = db.create_agent_conversation("acc_bill", "测试对话")["id"]
    db.add_agent_turn(
        cid,
        "assistant",
        content="上一轮回答",
        meta={"cost": 0.00278, "tokens": {"input": 1000, "output": 500}},
        model="deepseek-chat",
    )
    db.add_agent_turn(cid, "user", content="上一轮花了多少")
    result = asyncio.run(_tool(conversation_id=cid)["get_last_turn_cost"].handler({}))
    assert result["ok"] is True
    assert result["cost"] == pytest.approx(0.00278)
    assert result["currency"] == "CNY"
    assert result["tokens"] == {"input": 1000, "output": 500}
    assert result["total_tokens"] == 1500
    assert result["model"] == "deepseek-chat"
    assert "上一轮消耗" in result["summary"]


def test_last_turn_cost_does_not_fall_back_to_older_turn(temp_db):
    cid = db.create_agent_conversation("acc_bill", "测试对话")["id"]
    db.add_agent_turn(cid, "assistant", content="更早", meta={"cost": 0.1})
    db.add_agent_turn(cid, "assistant", content="最近", meta={"tokens": {"input": 1, "output": 1}})
    result = asyncio.run(_tool(conversation_id=cid)["get_last_turn_cost"].handler({}))
    assert result["ok"] is False
    assert "没有可用的服务商用量" in result["error"]


def test_last_turn_cost_without_previous_turn(temp_db):
    cid = db.create_agent_conversation("acc_bill", "新对话")["id"]
    result = asyncio.run(_tool(conversation_id=cid)["get_last_turn_cost"].handler({}))
    assert result["ok"] is False
    assert "还没有上一轮" in result["error"]
