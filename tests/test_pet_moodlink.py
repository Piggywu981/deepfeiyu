from __future__ import annotations

import pytest

from app.mood import constants as mood_constants
from app.pet import moodlink as ml


def _dims(**kw):
    base = {k: 10.0 for k in ("happy", "calm", "sweet", "heartache", "worry", "sad", "jealous")}
    base.update(kw)
    return base


def test_level_boundaries_copied_from_spec():
    assert ml.activity_level(75) == "lively"
    assert ml.activity_level(74.9) == "normal"
    assert ml.activity_level(45) == "normal"
    assert ml.activity_level(44.9) == "quiet"


def test_manual_level_wins_over_mood():
    assert ml.resolve_level({"activityLevel": "quiet"}, {"base": 90.0, "dims": _dims()}) == "quiet"
    assert ml.resolve_level({"activityLevel": "auto"}, {"base": 80.0, "dims": _dims()}) == "lively"
    assert ml.resolve_level({"activityLevel": "auto"}, None) == "normal"


def test_pulse_requires_rise_and_floor():
    assert ml.pulse_for_change(_dims(happy=55), _dims(happy=74), now_s=100, last_pulse_s=None) is None
    got = ml.pulse_for_change(_dims(happy=35), _dims(happy=74), now_s=100, last_pulse_s=None)
    assert got and got["state"] == "SUCCESS" and got["ttlMs"] <= ml.PULSE_TTL_MAX


def test_pulse_rate_limited_per_minute():
    # 用 happy 而不是 sad：低落映射到 IDLE，按下面的规则本来就不该弹跳
    assert ml.pulse_for_change(_dims(happy=20), _dims(happy=80), now_s=200, last_pulse_s=150) is None
    assert ml.pulse_for_change(_dims(happy=20), _dims(happy=80), now_s=261, last_pulse_s=150) is not None


def test_calm_and_low_moods_never_bounce():
    for dim in ("calm", "sad"):
        assert ml.pulse_for_change(_dims(**{dim: 10}), _dims(**{dim: 90}), now_s=100,
                                   last_pulse_s=None) is None


def test_snapshot_shape_is_light_and_dict_dims(monkeypatch):
    """bubble 依赖 {"base","dims","confidence"}；snapshot 不去算 timeline。"""
    from app.mood import service as mood_service
    from app.mood import settings as mood_settings
    from app.mood import store as mood_store

    monkeypatch.setattr(mood_settings, "enabled", lambda aid: True)
    monkeypatch.setattr(mood_settings, "settings",
                        lambda aid: {"base": 70.0, "decay_half_hours": 12.0})
    monkeypatch.setattr(mood_settings, "resolve_slot", lambda aid, chat="": "slot_a")
    monkeypatch.setattr(mood_store, "get_slot", lambda aid, slot: None)
    monkeypatch.setattr(mood_service, "timeline_payload",
                        lambda *a, **kw: pytest.fail("snapshot 不该触发 timeline 计算"))
    got = ml.snapshot("acc_a")
    assert set(got) == {"base", "dims", "confidence"}
    assert got["base"] == 70.0
    assert set(got["dims"]) == set(mood_constants.DIM_KEYS)
    assert isinstance(got["dims"], dict)


def test_snapshot_none_when_mood_disabled(monkeypatch):
    from app.mood import settings as mood_settings

    monkeypatch.setattr(mood_settings, "enabled", lambda aid: False)
    assert ml.snapshot("acc_a") is None


def test_settings_config_message_uses_snapshot_level(monkeypatch):
    from app import config
    from app.pet import settings

    monkeypatch.setattr(config, "load", lambda: {"server": {"port": 8765}, "pet": settings.DEFAULTS})
    lively = settings.config_message(mood_payload={"base": 88.0, "dims": _dims()})
    quiet = settings.config_message(mood_payload={"base": 20.0, "dims": _dims()})
    assert lively["activityLevel"] == "lively"
    assert quiet["activityLevel"] == "quiet"
