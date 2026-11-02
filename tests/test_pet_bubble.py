from __future__ import annotations

from app.pet import bubble


def _cfg(**patch):
    base = {"showChatName": True, "showChatDigest": False}
    base.update(patch)
    return base


def _mood(**dims):
    values = {k: 0.0 for k in ("happy", "calm", "sweet", "heartache", "worry", "sad", "jealous")}
    values.update(dims)
    return {"base": 70.0, "dims": values, "confidence": 80}


def test_base_copy_always_present():
    message, detail = bubble.decorate("THINKING", mood=None, pet_cfg=_cfg())
    assert message == "在想事情"
    assert "DSH" not in message and "DSH" not in detail


def test_unknown_state_falls_back_to_idle_copy():
    message, _ = bubble.decorate("NOT_A_STATE", mood=None, pet_cfg=_cfg())
    assert message == bubble.STATE_COPY["IDLE"][0]


def test_chat_name_appended_when_enabled():
    _, detail = bubble.decorate("THINKING", mood=None, pet_cfg=_cfg(), chat_name="小组-张三")
    assert "小组-张三" in detail
    hidden = bubble.decorate("THINKING", mood=None, pet_cfg=_cfg(showChatName=False),
                             chat_name="小组-张三")[1]
    assert "张三" not in hidden


def test_long_chat_name_truncated():
    _, detail = bubble.decorate("THINKING", mood=None, pet_cfg=_cfg(), chat_name="名" * 60)
    assert "名" * (bubble.CHAT_NAME_MAX + 1) not in detail


def test_chat_name_control_chars_stripped():
    _, detail = bubble.decorate("THINKING", mood=None, pet_cfg=_cfg(), chat_name="甲\n乙\t丙")
    assert "\n" not in detail and "\t" not in detail and "甲 乙 丙" in detail


def test_mood_words_only_when_confident():
    worried = _mood(worry=70)
    high = bubble.decorate("ERROR", mood=worried, pet_cfg=_cfg())[0]
    low = bubble.decorate("ERROR", mood={**worried, "confidence": 30}, pet_cfg=_cfg())[0]
    assert "挂心" in high          # 措辞取自 mood/constants.DIM_SHORT，不另造一套形容
    assert high != low and "挂心" not in low


def test_idle_and_disconnect_never_carry_mood_words():
    excited = _mood(happy=95)
    for state in ("IDLE", "DISCONNECTED"):
        assert "高兴" not in bubble.decorate(state, mood=excited, pet_cfg=_cfg())[0]


def test_digest_off_by_default():
    _, detail = bubble.decorate("SUCCESS", mood=None, pet_cfg=_cfg(),
                                chat_name="李四", digest="会议改到下午三点")
    assert "会议改到下午三点" not in detail


def test_source_label_rendered_for_harness():
    _, detail = bubble.decorate("WORKING", mood=None, pet_cfg=_cfg(),
                                source_label="Harness · 修复登录崩溃")
    assert "Harness" in detail and "修复登录崩溃" in detail


def test_digest_truncated_when_enabled():
    cfg = _cfg(showChatDigest=True)
    _, detail = bubble.decorate("SUCCESS", mood=None, pet_cfg=cfg, digest="长" * 60)
    assert "长" * (bubble.DIGEST_MAX + 1) not in detail
    assert "长" * 3 in detail


def test_credential_like_digest_blocked():
    """scan_secret 拦的是凭证样式（sk-/api key/bearer/私钥），不是手机号：
    手机号那类隐私靠默认关 + 截断，别把两件事混成一条假保证。"""
    cfg = _cfg(showChatDigest=True)
    _, detail = bubble.decorate("SUCCESS", mood=None, pet_cfg=cfg,
                                digest="api_key=sk-abcdef0123456789abcd")
    assert "sk-abcdef" not in detail


def test_message_for_carries_kind_and_extras():
    out = bubble.message_for("WORKING", mood=None, pet_cfg=_cfg(), kind="state", activity="searching")
    assert out["kind"] == "state" and out["state"] == "WORKING" and out["activity"] == "searching"
    assert out["message"] and out["detail"]


def test_all_seven_states_have_copy():
    assert set(bubble.STATE_COPY) == set(bubble.HELPER_STATES)
    for state in bubble.STATE_COPY:
        message, _ = bubble.decorate(state, mood=None, pet_cfg=_cfg())
        assert message and len(message) <= 12
