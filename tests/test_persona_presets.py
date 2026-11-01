"""人格预设：data/personas/*.json 的保存、读取、删除与文件名校验。"""
from __future__ import annotations

import json

import pytest

from app import config


@pytest.fixture()
def temp_personas(tmp_path, monkeypatch):
    d = tmp_path / "personas"
    d.mkdir()
    monkeypatch.setattr(config, "PERSONAS_DIR", d)
    return d


def test_save_and_list_roundtrip(temp_personas):
    config.save_persona("大肥鱼", "大肥鱼", "你是大肥鱼，说话简短。")
    items = config.list_personas()
    assert [x["name"] for x in items] == ["大肥鱼"]
    assert items[0]["persona_name"] == "大肥鱼"
    assert items[0]["system_prompt"] == "你是大肥鱼，说话简短。"
    # 落盘为独立 json 文件，可手工编辑
    raw = json.loads((temp_personas / "大肥鱼.json").read_text("utf-8"))
    assert raw["system_prompt"].startswith("你是")


def test_name_suffix_and_overwrite(temp_personas):
    config.save_persona("a.json", "旧", "旧人设")
    config.save_persona("a", "新", "新人设")
    items = config.list_personas()
    assert [x["name"] for x in items] == ["a"]
    assert items[0]["system_prompt"] == "新人设"


def test_invalid_name_rejected(temp_personas):
    for bad in ["", "   ", "a/b", "a\\b", "a:b", "a?b", "x" * 81]:
        with pytest.raises(ValueError):
            config.save_persona(bad, "n", "p")
    assert config.list_personas() == []


def test_read_and_delete(temp_personas):
    config.save_persona("b", "B", "p")
    assert config.read_persona("b")["persona_name"] == "B"
    assert config.read_persona("nope") is None
    assert config.read_persona("a/b") is None
    assert config.delete_persona("nope") is False
    assert config.delete_persona("b") is True
    assert config.list_personas() == []


def test_source_field_roundtrip(temp_personas):
    # 图集来源标记：写入、透传，缺省为空串
    config.save_persona("大肥鱼", "大肥鱼", "你是大肥鱼。", source="ai-moe-wiki")
    assert config.read_persona("大肥鱼")["source"] == "ai-moe-wiki"
    config.save_persona("自建", "自建", "p")
    assert config.read_persona("自建")["source"] == ""
    assert [p.get("source") for p in config.list_personas()] == ["ai-moe-wiki", ""]
