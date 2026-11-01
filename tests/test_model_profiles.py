"""模型配置（profile）：三档默认值、切档镜像、按档取密钥与存量配置迁移。"""
from __future__ import annotations

import json
import os

import pytest

from app import config

KEY_ENVS = (
    "DEEPSEEK_API_KEY",
    "MIMO_API_KEY",
    "CUSTOM_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把 config.json / .env 与进程内密钥都隔离到临时目录。"""
    cfg_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setattr(config, "_cache", None)
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    yield cfg_path, env_path
    for k in KEY_ENVS:
        os.environ.pop(k, None)
    config._cache = None


def _profile(cfg, pid):
    return next(p for p in cfg["ai"]["profiles"] if p["id"] == pid)


def test_default_profiles_include_mimo(home):
    cfg = config.load()
    assert [p["id"] for p in cfg["ai"]["profiles"]] == [
        "deepseek-flash",
        "mimo-v2.6-flash",
        "custom",
    ]
    mimo = _profile(cfg, "mimo-v2.6-flash")
    assert mimo["base_url"] == "https://api.xiaomimimo.com/v1"
    assert mimo["model"] == "mimo-v2.6-flash"
    assert mimo["key_env"] == "MIMO_API_KEY"
    # 默认生效 DeepSeek 档，全局 base_url/model 只是它的镜像
    assert cfg["ai"]["active_profile"] == "deepseek-flash"
    assert cfg["ai"]["base_url"] == "https://api.deepseek.com"
    assert cfg["ai"]["model"] == "deepseek-flash"


def test_switch_profile_moves_endpoint_and_key(home):
    _, env_path = home
    env_path.write_text("DEEPSEEK_API_KEY=sk-deep\nMIMO_API_KEY=sk-mimo\n", "utf-8")
    assert config.load()["ai"]["api_key"] == "sk-deep"

    config.save({"ai": {"active_profile": "mimo-v2.6-flash"}})
    cfg = config.load()
    assert cfg["ai"]["base_url"] == "https://api.xiaomimimo.com/v1"
    assert cfg["ai"]["model"] == "mimo-v2.6-flash"
    assert cfg["ai"]["api_key"] == "sk-mimo"

    config.save({"ai": {"active_profile": "deepseek-flash"}})
    cfg = config.load()
    assert cfg["ai"]["base_url"] == "https://api.deepseek.com"
    assert cfg["ai"]["api_key"] == "sk-deep"


def test_edits_belong_to_active_profile_only(home):
    config.save({"ai": {"active_profile": "mimo-v2.6-flash"}})
    config.save({"ai": {"model": "mimo-v2.6-pro", "base_url": "https://mimo.example/v1"}})
    cfg = config.load()
    mimo = _profile(cfg, "mimo-v2.6-flash")
    assert mimo["model"] == "mimo-v2.6-pro"
    assert mimo["base_url"] == "https://mimo.example/v1"
    # DeepSeek 档不受牵连
    assert _profile(cfg, "deepseek-flash")["model"] == "deepseek-flash"
    assert _profile(cfg, "deepseek-flash")["base_url"] == "https://api.deepseek.com"


def test_new_key_lands_in_profile_env_not_config_json(home):
    cfg_path, env_path = home
    config.save({"ai": {"active_profile": "mimo-v2.6-flash"}})
    config.save({"ai": {"api_key": "sk-mimo-new"}})
    lines = env_path.read_text("utf-8").splitlines()
    assert "MIMO_API_KEY=sk-mimo-new" in lines
    assert not any(line.startswith("DEEPSEEK_API_KEY=") for line in lines)
    assert config.load()["ai"]["api_key"] == "sk-mimo-new"
    assert json.loads(cfg_path.read_text("utf-8"))["ai"]["api_key"] == ""


def test_switch_and_fill_in_one_request_targets_new_profile(home):
    """切档同时带字段时，字段归目标档（= 切过去并配好这一档）。"""
    config.save({"ai": {"active_profile": "mimo-v2.6-flash", "api_key": "sk-mimo"}})
    cfg = config.load()
    assert cfg["ai"]["active_profile"] == "mimo-v2.6-flash"
    assert cfg["ai"]["api_key"] == "sk-mimo"
    config.save({"ai": {"active_profile": "custom", "base_url": "http://127.0.0.1:9/v1", "model": "m"}})
    cfg = config.load()
    assert cfg["ai"]["base_url"] == "http://127.0.0.1:9/v1"
    assert cfg["ai"]["model"] == "m"
    assert _profile(cfg, "mimo-v2.6-flash")["model"] == "mimo-v2.6-flash"


def test_empty_key_keeps_existing_one(home):
    config.save({"ai": {"api_key": "sk-first"}})
    config.save({"ai": {"api_key": "", "temperature": 0.3}})
    cfg = config.load()
    assert cfg["ai"]["api_key"] == "sk-first"
    assert cfg["ai"]["temperature"] == 0.3


def test_masked_key_never_written_to_env(home):
    _, env_path = home
    config.save({"ai": {"api_key": "sk-real"}})
    masked = config.public()["ai"]["api_key"]
    config.save({"ai": {"api_key": masked}})
    text = env_path.read_text("utf-8")
    assert "DEEPSEEK_API_KEY=sk-real" in text
    assert "•" not in text


def test_public_exposes_configured_flags_without_keys(home):
    config.save({"ai": {"active_profile": "mimo-v2.6-flash", "api_key": "sk-mimo"}})
    pub = config.public()
    flags = {p["id"]: p["configured"] for p in pub["ai"]["profiles"]}
    assert flags == {"deepseek-flash": False, "mimo-v2.6-flash": True, "custom": False}
    assert all("api_key" not in p for p in pub["ai"]["profiles"])
    assert pub["ai"]["api_key"].startswith("•")


def test_legacy_config_without_profiles_migrates(home):
    cfg_path, env_path = home
    legacy = {
        "ai": {"base_url": "https://old.example", "model": "old-model", "api_key": "sk-legacy"}
    }
    cfg_path.write_text(json.dumps(legacy), "utf-8")
    config._cache = None
    cfg = config.load()
    # 原来接的不是 DeepSeek → 地址与模型名归到「自定义」档，并且成为生效档
    assert cfg["ai"]["active_profile"] == "custom"
    assert _profile(cfg, "custom")["base_url"] == "https://old.example"
    assert _profile(cfg, "custom")["model"] == "old-model"
    assert cfg["ai"]["base_url"] == "https://old.example"
    # 明文密钥按生效档迁进 .env，config.json 里清空
    assert "CUSTOM_API_KEY=sk-legacy" in env_path.read_text("utf-8").splitlines()
    assert json.loads(cfg_path.read_text("utf-8"))["ai"]["api_key"] == ""


def test_legacy_deepseek_config_stays_in_deepseek_profile(home):
    cfg_path, env_path = home
    legacy = {
        "ai": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "sk-legacy",
        }
    }
    cfg_path.write_text(json.dumps(legacy), "utf-8")
    config._cache = None
    cfg = config.load()
    assert cfg["ai"]["active_profile"] == "deepseek-flash"
    # 沿用用户原本在用的模型名，升级不偷偷换模型
    assert _profile(cfg, "deepseek-flash")["model"] == "deepseek-chat"
    assert cfg["ai"]["model"] == "deepseek-chat"
    assert "DEEPSEEK_API_KEY=sk-legacy" in env_path.read_text("utf-8").splitlines()


def test_invalid_active_profile_falls_back(home):
    config.save({"ai": {"active_profile": "no-such-profile"}})
    cfg = config.load()
    assert cfg["ai"]["active_profile"] == "deepseek-flash"
    assert cfg["ai"]["model"] == "deepseek-flash"


def test_custom_profile_is_editable(home, monkeypatch):
    config.save({"ai": {"active_profile": "custom"}})
    config.save({"ai": {"base_url": "http://127.0.0.1:8000/v1", "model": "my-vllm"}})
    cfg = config.load()
    assert cfg["ai"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert cfg["ai"]["model"] == "my-vllm"
    assert _profile(cfg, "custom")["key_env"] == "CUSTOM_API_KEY"
    # 自定义档不读 DeepSeek 的密钥
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    assert config.load()["ai"]["api_key"] == ""


def test_env_overrides_only_touch_deepseek_profile(home, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    config.save({"ai": {"active_profile": "mimo-v2.6-flash"}})
    cfg = config.load()
    assert _profile(cfg, "deepseek-flash")["base_url"] == "https://proxy.example"
    assert _profile(cfg, "deepseek-flash")["model"] == "deepseek-reasoner"
    assert _profile(cfg, "mimo-v2.6-flash")["base_url"] == "https://api.xiaomimimo.com/v1"
    # 生效的是 mimo 档，环境变量不该把它的地址改掉
    assert cfg["ai"]["base_url"] == "https://api.xiaomimimo.com/v1"


def test_llm_follows_active_profile(home):
    from app.ai.llm import LLM

    llm = LLM(config.load)
    config.save({"ai": {"active_profile": "mimo-v2.6-flash"}})
    assert llm.provider_kind() == "mimo"
    assert [e["value"] for e in llm.effort_options()] == ["auto", "off", "on"]
    assert llm.profile_label() == "MiMo v2.6 Flash"
    assert llm._reasoning_payload("off", "mimo-v2.6-flash") == {"thinking": {"type": "disabled"}}

    config.save({"ai": {"active_profile": "deepseek-flash"}})
    assert llm.provider_kind() == "deepseek"
    assert llm._reasoning_payload("high", "deepseek-flash") == {"reasoning_effort": "high"}
