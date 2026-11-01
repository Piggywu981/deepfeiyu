"""MiMo 牌价与用量估算：按模型取价、无官方余额接口。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main, pricing, usage as usage_mod
from app.ai import llm as llm_mod


def _mimo_cfg() -> dict:
    return {
        "ai": {
            "api_key": "sk-mimo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.6-flash",
            # 全局值故意留成 DeepSeek 价，验证已知 MiMo 模型不会被它带偏
            "price_input_hit": 0.2,
            "price_input_miss": 2.0,
            "price_output": 3.0,
            "active_profile": "mimo-v2.6-flash",
            "profiles": [{"id": "mimo-v2.6-flash", "model": "mimo-v2.6-flash"}],
        }
    }


def _deepseek_cfg() -> dict:
    return {
        "ai": {
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "price_input_hit": 0.2,
            "price_input_miss": 2.0,
            "price_output": 3.0,
        }
    }


def test_provider_kind_uses_base_url_first_for_protocol_params():
    assert pricing.provider_kind(_mimo_cfg()["ai"]) == "mimo"
    assert pricing.provider_kind(_deepseek_cfg()["ai"]) == "deepseek"
    # base_url 决定请求参数；DeepSeek 代理即使模型名是 mimo，也先按 base 判
    assert pricing.provider_kind(_deepseek_cfg()["ai"], "mimo-v2.6-pro") == "deepseek"


def test_price_for_known_mimo_models():
    ai = _mimo_cfg()["ai"]
    assert pricing.price_for_model(ai, "mimo-v2.6-flash") == {
        "input_hit": 0.02,
        "input_miss": 1.0,
        "output": 2.0,
        "source": "mimo_builtin",
    }
    assert pricing.price_for_model(ai, "mimo-v2.6-pro")["output"] == 6.0
    assert pricing.price_for_model(ai, "mimo-v2.6-pro-ultraspeed") == {
        "input_hit": 0.25,
        "input_miss": 30.0,
        "output": 60.0,
        "source": "mimo_builtin",
    }
    # 代理地址也可能跑 MiMo：按模型名取价，不被 base 带偏
    assert pricing.price_for_model(_deepseek_cfg()["ai"], "mimo-v2.6-flash")["source"] == "mimo_builtin"


def test_unknown_mimo_model_falls_back_to_global_price():
    price = pricing.price_for_model(_mimo_cfg()["ai"], "mimo-future-model")
    assert price["source"] == "global"
    assert price["input_hit"] == 0.2


def test_profile_price_overrides_builtin():
    ai = _mimo_cfg()["ai"]
    ai["profiles"] = [
        {
            "id": "mimo-v2.6-flash",
            "model": "mimo-v2.6-flash",
            "price_input_hit": 0.03,
            "price_input_miss": 1.5,
            "price_output": 2.5,
        }
    ]
    price = pricing.price_for_model(ai, "mimo-v2.6-flash")
    assert price == {
        "input_hit": 0.03,
        "input_miss": 1.5,
        "output": 2.5,
        "source": "profile",
    }


def test_record_usage_uses_mimo_price(monkeypatch, tmp_path):
    ledger = usage_mod.UsageLedger(tmp_path / "whale-usage.json")
    monkeypatch.setattr(usage_mod, "LEDGER", ledger)
    llm = llm_mod.LLM(_mimo_cfg)
    cost = llm._record_usage(
        "mimo-v2.6-flash",
        {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "prompt_cache_hit_tokens": 400,
            "prompt_cache_miss_tokens": 600,
        },
    )
    # 400*0.02 + 600*1 + 500*2 = 1608 元 / 百万 tokens
    assert cost == pytest.approx(0.001608)
    assert ledger.last_turn()["turn"] == "mimo-v2.6-flash"


def test_mimo_stream_records_usage_without_extra_options(monkeypatch, tmp_path):
    spec = {
        "status": 200,
        "lines": [
            'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":100,"completion_tokens":50,'
            '"prompt_cache_hit_tokens":20,"prompt_cache_miss_tokens":80}}',
            "data: [DONE]",
        ],
    }
    payloads: list[dict] = []

    class FakeResponse:
        def __init__(self, value):
            self.status_code = value["status"]
            self._lines = value.get("lines", [])

        async def aread(self):
            return b""

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class FakeStreamCtx:
        async def __aenter__(self):
            return FakeResponse(spec)

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, headers=None, json=None):
            payloads.append(dict(json or {}))
            return FakeStreamCtx()

    ledger = usage_mod.UsageLedger(tmp_path / "whale-usage.json")
    monkeypatch.setattr(usage_mod, "LEDGER", ledger)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", FakeClient)

    events = asyncio.run(_collect(llm_mod.LLM(_mimo_cfg).stream([{"role": "user", "content": "hi"}])))
    assert "stream_options" not in payloads[0]
    assert any(e["type"] == "text" and e["text"] == "hi" for e in events)
    usage_event = next(e for e in events if e["type"] == "usage")
    # 20*0.02 + 80*1 + 50*2 = 180.4 元 / 百万 tokens
    assert usage_event["cost"] == pytest.approx(0.0001804)


def _collect(agen):
    async def run():
        return [item async for item in agen]

    return run()


def test_mimo_balance_has_no_api(monkeypatch):
    monkeypatch.setattr(
        main.llm,
        "_get_cfg",
        lambda: {
            "ai": {
                "api_key": "sk-mimo",
                "base_url": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.6-flash",
            }
        },
    )
    client = TestClient(main.app)
    body = client.get("/dsh-whale/balance.json").json()
    assert body["ok"] is False
    assert body["code"] == "NO_BALANCE_API"
    assert "没有官方余额查询接口" in body["error"]
    assert "本地估算" in body["usageLabel"]
