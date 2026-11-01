"""心情注入：一次变化只说一次、minor/可信度/日上限、两种注入文案。"""
from __future__ import annotations

import time

import pytest

from app import config, db
from app.mood import service, settings, store
from app.mood.constants import DIM_KEYS


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


@pytest.fixture()
def mood_cfg(monkeypatch):
    def _set(**account):
        base = {
            "inject_mode": "event",
            "inject_confidence": 60,
            "inject_per_day": 6,
        }
        base.update(account)
        monkeypatch.setattr(
            config, "load",
            lambda: {"mood": {"enabled": True, "accounts": {"acc_a": base}}},
        )
        monkeypatch.setattr(config, "account", lambda aid: {"id": aid})
        monkeypatch.setattr(config, "persona_of", lambda account: ("p", "大肥鱼"))
    _set()
    return _set


def _seed(account_id="acc_a", slot_id="default", **kw):
    state = store.load_state(account_id, slot_id)
    state.update({
        "mood": kw.get("mood", 72.0),
        "last_reason": kw.get("reason", "号主说没吃饭，我心里咯噔一下"),
        "last_confidence": kw.get("conf", 88),
        "last_noted_mood": kw.get("mood", 72.0),
        "minor": kw.get("minor", 0),
        "kind": kw.get("kind", "judge"),
        "updated_at": kw.get("updated_at", 1000.0),
        # 远古 last_decay_at 会把七项情绪按 90 分钟半衰清零；默认按「刚更新过」造状态
        "last_decay_at": kw.get("last_decay_at", time.time()),
        "said_at": kw.get("said_at", []),
    })
    store.save_state(account_id, slot_id, state)
    return state


def test_event_mode_injected_once(temp_db, mood_cfg):
    _seed()
    line = service.inject_block("acc_a")
    assert line.startswith("（这会儿）心里是松的")
    assert "号主说没吃饭" in line
    assert service.inject_block("acc_a") == ""
    state = store.load_state("acc_a", "default")
    assert state["last_injected_text"] == line
    assert len(state["said_at"]) == 1


def test_minor_change_is_not_injected_but_marked(temp_db, mood_cfg):
    _seed(minor=1)
    assert service.inject_block("acc_a") == ""
    state = store.load_state("acc_a", "default")
    assert state["injected_for"] == state["updated_at"]


def test_low_confidence_not_injected(temp_db, mood_cfg):
    _seed(conf=30)
    assert service.inject_block("acc_a") == ""
    assert store.load_state("acc_a", "default")["injected_for"] == 1000.0


def test_daily_quota_blocks_injection(temp_db, mood_cfg):
    _seed(said_at=[service._today_start() + 1] * 6)
    assert service.inject_block("acc_a") == ""
    state = store.load_state("acc_a", "default")
    assert state["injected_for"] == state["updated_at"]


def test_off_mode_returns_empty(temp_db, mood_cfg):
    mood_cfg(inject_mode="off")
    _seed()
    assert service.inject_block("acc_a") == ""


def test_label_mode_text(temp_db, mood_cfg):
    mood_cfg(inject_mode="label")
    _seed(mood=72.0, reason="")
    state = store.load_state("acc_a", "default")
    state["dims"]["happy"] = 40.0
    state["dims"]["calm"] = 20.0
    store.save_state("acc_a", "default", state)
    line = service.inject_block("acc_a")
    assert line.startswith("（此刻你）心情开心，心里挺高兴、心里踏实。")


def test_event_downgrades_to_label_for_other_chat_and_group(temp_db, mood_cfg):
    """event 的具体来历只留给同一个会话；群聊/别的会话降级成 label。"""
    state = _seed()
    state["last_chat_uid"] = "u1"
    store.save_state("acc_a", "default", state)

    line = service.inject_block("acc_a", chat_uid="u2")
    assert line.startswith("（此刻你）心情")
    assert "号主说没吃饭" not in line

    # 同会话下一次变化仍然可以讲来历
    state = store.load_state("acc_a", "default")
    state["injected_for"] = 0.0
    store.save_state("acc_a", "default", state)
    line = service.inject_block("acc_a", chat_uid="u1")
    assert line.startswith("（这会儿）")
    assert "号主说没吃饭" in line

    # 群聊即使来源会话相同也要降级
    state = store.load_state("acc_a", "default")
    state["injected_for"] = 0.0
    store.save_state("acc_a", "default", state)
    line = service.inject_block("acc_a", chat_uid="u1", is_group=True)
    assert line.startswith("（此刻你）心情")
    assert "号主说没吃饭" not in line


def test_decay_event_uses_decay_text(temp_db, mood_cfg):
    state = _seed(mood=85.0, conf=100, reason="x")
    state["kind"] = "decay"
    state["last_reason"] = "过了几个钟头，刚才那点劲儿慢慢淡了"
    state["updated_at"] = 5000.0
    state["last_decay_at"] = 5000.0
    store.save_state("acc_a", "default", state)
    assert "过了几个钟头" in service.inject_block("acc_a")


def test_mark_event_injected(temp_db, mood_cfg):
    _seed()
    store.add_event("acc_a", "default", at=1000.0, mood=72, delta=2, dims={}, dims_delta={},
                    reason="r", confidence=88, kind="judge", source="user", minor=0)
    service.inject_block("acc_a")
    assert store.list_events("acc_a", "default")[0]["injected"] == 1


def test_disabled_returns_empty(temp_db, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"mood": {"enabled": False, "accounts": {}}})
    _seed()
    assert service.inject_block("acc_a") == ""


def test_manager_prompt_appends_block(temp_db, monkeypatch):
    from app.wechat.manager import AccountSession
    from app.wechat.base import Message

    monkeypatch.setattr(config, "load", lambda: {
        "ai": {"vision_enabled": False},
        "auto": {},
        "persona": {},
        "chuanzi": {},
    })
    calls: list[tuple[str, str, str, bool]] = []

    def fake_inject(aid: str, uid: str = "", chat_name: str = "",
                    is_group: bool = False) -> str:
        calls.append((aid, uid, chat_name, is_group))
        return "【心情块】"

    monkeypatch.setattr(service, "inject_block", fake_inject)
    session = AccountSession({"id": "acc_a", "name": "号"}, lambda: config.load(), None)
    msg = Message(chat_id="c1", chat_name="号主", msg_id="m1", msg_type="text", content="在吗",
                  is_group=False, is_self=False, sender_name="号主")
    chat = {"uid": "acc_a:c1", "name": "号主", "is_group": 0}
    out = session._build_prompt(config.load(), chat, [], msg)
    assert calls == [("acc_a", "acc_a:c1", "号主", False)]
    assert "【心情块】" in out[0]["content"]
    assert out[0]["content"].rstrip().endswith("【心情块】")


def test_manager_prompt_passes_group_flag(temp_db, monkeypatch):
    """群聊必须把 is_group=True 传给注入，才能在服务层降级掉具体来历。"""
    from app.wechat.manager import AccountSession
    from app.wechat.base import Message

    monkeypatch.setattr(config, "load", lambda: {
        "ai": {"vision_enabled": False},
        "auto": {},
        "persona": {},
        "chuanzi": {},
    })
    calls: list[dict] = []

    def fake_inject(*args, **kwargs) -> str:
        calls.append(kwargs)
        return ""

    monkeypatch.setattr(service, "inject_block", fake_inject)
    session = AccountSession({"id": "acc_a", "name": "号"}, lambda: config.load(), None)
    msg = Message(chat_id="g1", chat_name="群聊", msg_id="m2", msg_type="text", content="在吗",
                  is_group=True, is_self=False, sender_name="群友")
    chat = {"uid": "acc_a:g1", "name": "群聊", "is_group": 1}
    session._build_prompt(config.load(), chat, [], msg)
    assert calls and calls[-1].get("is_group") is True


def test_inject_block_returns_empty_when_load_state_fails(mood_cfg, monkeypatch):
    """数据库读失败只该丢心情块，不能把异常抛给提示词构建。"""

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(service.store, "load_state", boom)
    assert service.inject_block("acc_a") == ""


def test_inject_block_returns_empty_when_mark_save_fails(temp_db, mood_cfg, monkeypatch):
    """标记路径（minor 跳过）落库失败时同样只返回空，不抛出。"""
    _seed(minor=1)

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(service.store, "save_state", boom)
    assert service.inject_block("acc_a") == ""


class _MinimalSession:
    """只满足 _console_prompt 所需字段，避免碰真驱动/真账号。"""

    account_id = "acc_a"
    account = {"id": "acc_a", "name": "号"}
    name = "号"
    nickname = "小鲸"
    driver = None


def test_console_prompt_appends_mood_block(monkeypatch):
    """控制台接缝：心情块拼在系统提示最后。"""
    from app import main
    from app.ai import moe_wiki
    from app.growth import prompt as growth_prompt

    monkeypatch.setattr(config, "load", lambda: {
        "ai": {"memory_enabled": False, "chuanzi_mode": False},
    })
    monkeypatch.setattr(config, "persona_of", lambda account: ("人设", "大肥鱼"))
    monkeypatch.setattr(growth_prompt, "block", lambda *a, **k: "")
    monkeypatch.setattr(moe_wiki, "self_block", lambda: "")
    monkeypatch.setattr(main.capability_registry, "prompt_block", lambda: "")
    monkeypatch.setattr(
        main.mood_service, "inject_block", lambda *a, **k: "【心情块】"
    )

    text = main._console_prompt(_MinimalSession())
    assert text.rstrip().endswith("【心情块】")
