"""心情纯逻辑：半衰、步长、解析、显著性与注入文案。"""
from __future__ import annotations

import json

import pytest

from app.mood import model
from app.mood.constants import DIM_KEYS, DEFAULT_MOOD


def _state(mood=70.0, **dims):
    state = {
        "mood": float(mood),
        "dims": {k: 0.0 for k in DIM_KEYS},
        "last_reason": "",
        "last_confidence": 0,
        "last_noted_mood": float(mood),
        "last_decay_at": 0.0,
        "injected_for": 0.0,
        "last_injected_at": 0.0,
        "last_injected_text": "",
        "said_at": [],
        "minor": 0,
        "kind": "judge",
        "updated_at": 0.0,
    }
    state["dims"].update(dims)
    return state


def test_decay_half_life_mood_and_dims():
    state = _state(100.0, happy=40.0)
    state["last_decay_at"] = 0.0
    event = model.decay(state, base=70.0, now=12 * 3600, half_hours=12)
    assert state["mood"] == pytest.approx(85.0)
    # 情绪半衰 90 分钟是规格 §5.4，不是 12 小时：720 分钟 = 8 个半衰，40 * 2**-8 = 0.15625
    assert state["dims"]["happy"] == pytest.approx(0.15625, rel=1e-3)
    assert event is not None
    assert event["kind"] == "decay"
    assert event["confidence"] == 100
    assert "过了几个钟头" in event["reason"]


def test_decay_under_one_minute_is_noop():
    state = _state(100.0, happy=40.0)
    state["last_decay_at"] = 1000.0
    event = model.decay(state, base=70.0, now=1030.0, half_hours=12)
    assert state["mood"] == 100.0
    assert state["dims"]["happy"] == 40.0
    assert event is None


def test_decay_note_only_when_three_points_moved():
    state = _state(72.0)
    state["last_decay_at"] = 0.0
    state["last_noted_mood"] = 72.0
    assert model.decay(state, base=70.0, now=60.0, half_hours=12) is None
    state = _state(80.0)
    state["last_decay_at"] = 0.0
    state["last_noted_mood"] = 80.0
    event = model.decay(state, base=70.0, now=12 * 3600, half_hours=12)
    assert event is not None
    assert state["last_noted_mood"] == pytest.approx(state["mood"])
    assert state["kind"] == "decay"
    assert state["minor"] == 0


def test_apply_delta_uses_third_and_clamps():
    state = _state(70.0, happy=98.0)
    delta = {
        "moodDelta": 5,
        "dims": {**{k: 0 for k in DIM_KEYS}, "happy": 5, "sad": -5},
        "reason": "号主夸我，我心里甜甜的",
        "confidence": 90,
    }
    model.apply_delta(state, delta, floor=0.0, now=123.0)
    assert state["mood"] == 72.0          # round(5/3)=2
    assert state["dims"]["happy"] == 100.0
    assert state["dims"]["sad"] == 0.0
    assert state["last_reason"] == "号主夸我，我心里甜甜的"
    assert state["last_confidence"] == 90
    assert state["updated_at"] == 123.0
    assert state["last_decay_at"] == 123.0


def test_apply_delta_floor():
    state = _state(2.0)
    delta = {"moodDelta": -10, "dims": {k: 0 for k in DIM_KEYS}, "reason": "x", "confidence": 80}
    model.apply_delta(state, delta, floor=0.0, now=1.0)
    assert state["mood"] == 0.0
    assert model.mood_step(-1) == 0
    assert model.mood_step(3) == 1
    assert model.mood_step(-2) == -1


def test_significant_rules():
    zero = {k: 0 for k in DIM_KEYS}
    assert model.significant({"moodDelta": 3, "dims": zero}, 3, 5) is True
    assert model.significant({"moodDelta": 2, "dims": {**zero, "worry": 5}}, 3, 5) is True
    assert model.significant({"moodDelta": 2, "dims": {**zero, "worry": 4}}, 3, 5) is False


def test_parse_delta_accepts_fences_and_clamps():
    raw = '```json\n{"moodDelta": 99, "dims": {"happy": 99, "sad": -99}, "reason": "她说好", "confidence": 101}\n```'
    d = model.parse_delta(raw, "号主")
    assert d is not None
    assert d["moodDelta"] == 10
    assert d["dims"]["happy"] == 10
    assert d["dims"]["sad"] == -10
    assert d["confidence"] == 100
    assert d["reason"] == "号主说好"


def test_parse_delta_defaults_confidence_and_truncates_reason():
    d = model.parse_delta(json.dumps({"moodDelta": 1, "dims": {}, "reason": "长" * 300}), "号主")
    assert d["confidence"] == 70
    assert len(d["reason"]) == 120


def test_parse_delta_rejects_garbage():
    assert model.parse_delta("") is None
    assert model.parse_delta("今天天气不错") is None
    assert model.parse_delta("{oops") is None


def test_parse_delta_rejects_non_finite_numbers():
    assert model.parse_delta('{"moodDelta": Infinity}', "号主") is None
    assert model.parse_delta('{"moodDelta": -Infinity}', "号主") is None
    assert model.parse_delta('{"moodDelta": NaN}', "号主") is None
    assert model.parse_delta('{"moodDelta": "inf"}', "号主") is None
    assert model.parse_delta('{"moodDelta": 1, "dims": {"happy": NaN}}', "号主") is None
    assert model.parse_delta('{"moodDelta": 1, "dims": {"happy": Infinity}}', "号主") is None
    assert model.parse_delta('{"moodDelta": 1, "dims": {"sad": "-inf"}}', "号主") is None
    assert model.parse_delta('{"moodDelta": 1, "confidence": NaN}', "号主") is None
    assert model.parse_delta('{"moodDelta": 1, "confidence": "nan"}', "号主") is None
    # 非数字字符串仍走 _int 的旧兜底，不因非有限数规则整条作废
    d = model.parse_delta('{"moodDelta": "abc", "confidence": "70"}', "号主")
    assert d is not None
    assert d["moodDelta"] == 0
    assert d["confidence"] == 70


def test_norm_reason_only_replaces_safe_pronoun():
    assert model.norm_reason("她说想我了", "号主") == "号主说想我了"
    assert model.norm_reason("她说他说想我了", "号主") == "她说他说想我了"
    assert model.norm_reason("小明说她来了", "号主") == "小明说她来了"


def test_norm_reason_keeps_psych_verb_third_party_subject():
    assert model.norm_reason("老王觉得她想我了", "号主") == "老王觉得她想我了"
    assert model.norm_reason("号主觉得她想要这个", "号主") == "号主觉得她想要这个"
    assert model.norm_reason("老王想她了", "号主") == "老王想她了"
    assert model.norm_reason("老王念叨她没吃饭", "号主") == "老王念叨她没吃饭"
    assert model.norm_reason("小李解释她来晚了", "号主") == "小李解释她来晚了"


def test_labels_and_top_dims():
    assert model.label_of(80) == "欢喜"
    assert model.label_of(62) == "开心"
    assert model.label_of(42) == "一般"
    assert model.label_of(25) == "心疼"
    assert model.label_of(24) == "难过"
    state = _state(70.0, happy=40.0, worry=20.0, sad=10.0)
    top = model.top_dims(state, 2)
    assert [t["key"] for t in top] == ["happy", "worry"]
    assert model.top_dims(state, 2)[0]["value"] == 40


def test_time_words():
    now = 100 * 3600
    assert model.time_word(now - 3600, now) == "刚才"
    assert model.time_word(now - 5 * 3600, now) == "早些时候"
    assert model.time_word(now - 24 * 3600, now) == "昨天"
    assert model.time_word(now - 72 * 3600, now) == "前几天"


def test_mood_line_event_exact_shape():
    state = _state(72.0, happy=40.0)
    state.update(
        last_reason="号主说没吃饭，我心里咯噔一下",
        last_confidence=88,
        kind="judge",
        updated_at=1000.0,
    )
    line = model.mood_line(state, "event", "号主", now=1000.0 + 1800)
    assert line.splitlines() == [
        "（这会儿）心里是松的，还挺高兴",
        "（刚才）号主说没吃饭，我心里咯噔一下",
        "",
        "这是这会儿的底子，不是非这样不可——号主说别的，你按自己的判断接，也别把这段念出来。",
    ]


def test_mood_line_decay_has_no_relative_time_prefix():
    state = _state(55.0)
    state.update(last_reason="过了几个钟头，心里那点别扭自己散了", kind="decay", updated_at=1.0)
    line = model.mood_line(state, "event", "号主", now=999999.0)
    assert "（刚才）" not in line
    assert "过了几个钟头" in line


def test_mood_line_label_and_empty_reason():
    state = _state(72.0, happy=40.0, calm=20.0)
    state.update(last_reason="", updated_at=1.0)
    label = model.mood_line(state, "label", "号主")
    assert label.startswith("（此刻你）心情开心，心里挺高兴、心里踏实。")
    assert "这是背景，不是这一轮必须有的情绪" in label
    assert model.mood_line(state, "event", "号主") == ""
