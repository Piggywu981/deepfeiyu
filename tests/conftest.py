"""测试共用夹具：只提供显式申请的 fixture，不用 autouse，免得改动既有用例的行为。"""
import pytest

from app import config


@pytest.fixture()
def configured_ai(monkeypatch):
    """让 LLM 认为已配置 API Key。

    CI 与全新安装的机器都没有 .env，`llm.configured` 为假会让控制台会话路由先回
    400，用例就测不到后面的链路。这里喂给实例一个带假 Key 的配置副本，而不是
    覆盖 configured 属性——「没配 Key 就拒绝发起会话」这条真实判断仍然被走到。
    """
    from app import main as main_mod

    def cfg():
        base = dict(config.load())
        base["ai"] = {**base.get("ai", {}), "api_key": "test-key-local-only"}
        return base

    monkeypatch.setattr(main_mod.llm, "_get_cfg", cfg)
    return cfg
