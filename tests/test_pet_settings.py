from __future__ import annotations

import copy

import pytest

from app import config
from app.pet import settings


def test_defaults_ship_disabled():
    assert config.DEFAULTS["pet"]["enabled"] is False
    assert config.DEFAULTS["pet"]["soundEnabled"] is False
    assert config.DEFAULTS["pet"]["soundStates"] == ["ERROR"]
    assert config.DEFAULTS["pet"]["showChatName"] is True
    assert config.DEFAULTS["pet"]["showChatDigest"] is False


@pytest.mark.parametrize("raw,expected", [(0.1, 0.55), (9.0, 1.4), (1.0, 1.0)])
def test_scale_clamped_to_helper_bounds(raw, expected):
    # 边界值 0.55/1.4 抄自上游 helper.py 的 clamp
    assert settings.normalize({"scale": raw})["scale"] == expected


@pytest.mark.parametrize("raw,expected", [(0.5, 0.8), (3.0, 1.2), (1.0, 1.0)])
def test_bubble_scale_clamped(raw, expected):
    assert settings.normalize({"bubbleScale": raw})["bubbleScale"] == expected


def test_activity_level_accepts_only_known_values():
    assert settings.normalize({"activityLevel": "lively"})["activityLevel"] == "lively"
    # 非法值整条丢弃，而不是悄悄兜一个 "auto"：前端拿到 200 却什么都没存是最难查的
    assert "activityLevel" not in settings.normalize({"activityLevel": "hysterical"})
    assert settings.normalize({"soundStates": ["ERROR", "MEOW"]})["soundStates"] == ["ERROR"]


def test_unknown_keys_dropped():
    out = settings.normalize({"evil": 1, "enabled": True})
    assert "evil" not in out and out["enabled"] is True


def test_public_exposes_pet_section(monkeypatch):
    # 用 DEFAULTS 的深拷贝而不是手搓字段：public() 会走 active_id()，手搓极易漏键而假红
    cfg = copy.deepcopy(config.DEFAULTS)
    monkeypatch.setattr(config, "load", lambda: cfg)
    public = config.public()
    assert public["pet"]["enabled"] is False
    assert public["pet"]["soundStates"] == ["ERROR"]


def test_env_maps_to_upstream_variable_names(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"server": {"port": 8765}, "pet": settings.DEFAULTS})
    env = settings.env_for_process()
    assert env["DSH_DAFEIYU_WEBUI_URL"] == "http://127.0.0.1:8765/"
    assert env["DSH_DAFEIYU_OPEN_LABEL"] == "打开 Deep肥鱼控制台"
    assert env["DSH_DAFEIYU_LAYOUT_PATH"].endswith("layout.json")
    assert env["DSH_DAFEIYU_LOG_FILE"].endswith("helper.log")
    assert env["DSH_DAFEIYU_SOUND_ENABLED"] == "0"
    assert env["DSH_DAFEIYU_SOUND_STATES"] == "ERROR"


def _saved_cfg(**pet) -> dict:
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["pet"].update(pet)
    return cfg


def test_config_message_keeps_saved_scale_after_supervisor_passes_section(monkeypatch):
    """supervisor 传的是 section() 的结果；config_message 不能再按整份配置取一层 "pet"。

    回归：两层取法叠加后会落回 DEFAULTS，控制台改尺寸、Helper 仍按 1.0 画。
    """
    monkeypatch.setattr(config, "load", lambda: _saved_cfg(scale=1.3, bubbleScale=1.2))
    msg = settings.config_message(settings.section())
    assert msg["scale"] == 1.3
    assert msg["bubbleScale"] == 1.2


def test_env_for_process_keeps_saved_scale(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _saved_cfg(scale=0.6, bubbleScale=0.85))
    env = settings.env_for_process(settings.section())
    assert env["DSH_DAFEIYU_SCALE"] == "0.6"
    assert env["DSH_DAFEIYU_BUBBLE_SCALE"] == "0.85"


def test_bubble_mode_states_mapped_to_helper_custom(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: _saved_cfg(bubbleMode="states"))
    assert settings.config_message(settings.section())["bubbleMode"] == "custom"
    assert settings.env_for_process(settings.section())["DSH_DAFEIYU_BUBBLE_MODE"] == "custom"
