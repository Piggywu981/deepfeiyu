"""发送节流：全局间隔、同会话冷却与并发排队语义。"""
import threading
import time

from app.config import DEFAULTS
from app.wechat.base import Message
from app.wechat.manager import (
    AccountSession,
    _addressed_to_me,
    _gap_range,
    _in_live_conversation,
)
from app.wechat.throttle import SendThrottle


def _send_cfg(**over) -> dict:
    send = {
        "enabled": True,
        "min_gap_sec": [6, 14],
        "chat_gap_sec": [20, 45],
        "direct_gap_sec": [8, 12],
        "burst_gap_sec": [1, 3],
    }
    send.update(over)
    return {"send": send}


def test_first_send_waits_zero():
    t = SendThrottle()
    assert t.wait("c1", (0.02, 0.04), (0.05, 0.1)) == 0.0


def test_same_chat_respects_cooldown():
    t = SendThrottle()
    t.wait("c1", (0.0, 0.0), (0.08, 0.12))
    start = time.monotonic()
    waited = t.wait("c1", (0.0, 0.0), (0.08, 0.12))
    assert waited >= 0.06
    assert time.monotonic() - start >= waited - 0.02


def test_other_chat_only_global_gap():
    t = SendThrottle()
    t.wait("c1", (0.05, 0.07), (0.0, 0.0))
    waited = t.wait("c2", (0.05, 0.07), (0.0, 0.0))
    assert 0.03 <= waited <= 0.1


def test_concurrent_sends_serialize():
    t = SendThrottle()
    waited: list[float] = []
    lock = threading.Lock()

    def send() -> None:
        w = t.wait("c1", (0.02, 0.03), (0.05, 0.08))
        with lock:
            waited.append(w)

    threads = [threading.Thread(target=send) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(waited) == 3
    assert min(waited) < 0.02  # 头一个立即发出
    assert max(waited) >= 0.09  # 最后一个至少排到两轮之后


def test_gap_range_config_parsing():
    default = (1.0, 2.0)
    assert _gap_range([6, 14], default) == (6.0, 14.0)
    assert _gap_range(10, default) == (10.0, 10.0)
    assert _gap_range([6], default) == default
    assert _gap_range([9, 3], default) == default  # max < min
    assert _gap_range([-1, 5], default) == default
    assert _gap_range("x", default) == default
    assert _gap_range(None, default) == default


def test_burst_ignores_booked_slots_and_refreshes():
    t = SendThrottle()
    t.wait("c1", (0.0, 0.0), (0.5, 0.5))  # 预订了 0.5s 的同会话冷却
    start = time.monotonic()
    assert t.wait("c1", (0.0, 0.0), (0.05, 0.05), burst=True) == 0.0  # burst 不等已预订槽位
    waited = t.wait("c1", (0.0, 0.0), (0.0, 0.0))
    assert 0.03 <= waited <= 0.15  # burst 发出后冷却按 burst 的时间重算，而不是 0.5s
    assert time.monotonic() - start >= waited - 0.02


def test_burst_refreshes_global_gap_for_other_chats():
    t = SendThrottle()
    t.wait("c1", (0.05, 0.07), (0.0, 0.0), burst=True)  # burst 也占用全局槽位
    waited = t.wait("c2", (0.0, 0.0), (0.0, 0.0))
    assert 0.03 <= waited <= 0.1  # 其他会话仍要排队等 burst 预订的全局间隔


def test_default_config_has_direct_gap():
    """「对方冲我来」的短冷却得有默认值，且落在长冷却之下才有意义。"""
    lo, hi = _gap_range(DEFAULTS["send"]["direct_gap_sec"], (0.0, 0.0))
    assert (lo, hi) == (8.0, 12.0)
    chat_lo, chat_hi = _gap_range(DEFAULTS["send"]["chat_gap_sec"], (0.0, 0.0))
    assert hi <= chat_lo


def test_send_gaps_prefers_direct_gap():
    s = AccountSession({"id": "a", "name": "A", "nickname": "B"}, lambda: _send_cfg(), None)
    assert s._send_gaps() == ((6.0, 14.0), (20.0, 45.0))
    assert s._send_gaps(direct=True) == ((6.0, 14.0), (8.0, 12.0))


def test_send_gaps_without_direct_key_falls_back_to_default():
    cfg = _send_cfg()
    cfg["send"].pop("direct_gap_sec")
    s = AccountSession({"id": "a", "name": "A", "nickname": "B"}, lambda: cfg, None)
    assert s._send_gaps(direct=True)[1] == (8.0, 12.0)


def test_send_gaps_disabled_send_means_no_cooldown():
    cfg = _send_cfg()
    cfg["send"]["enabled"] = False
    s = AccountSession({"id": "a", "name": "A", "nickname": "B"}, lambda: cfg, None)
    assert s._send_gaps(direct=True) == ((0.0, 0.0), (0.0, 0.0))


def test_addressed_to_me_covers_private_at_and_pat():
    assert _addressed_to_me(Message(chat_id="wxid_a", chat_name="朋友", content="在吗"), "鱼") is True
    at = Message(chat_id="r@chatroom", chat_name="群", content="@鱼\u2005看下这个", is_group=True)
    assert _addressed_to_me(at, "鱼") is True
    plain = Message(chat_id="r@chatroom", chat_name="群", content="随便聊聊", is_group=True)
    assert _addressed_to_me(plain, "鱼") is False
    mine = Message(
        chat_id="r@chatroom", chat_name="群", content='你拍了拍"鱼"', is_group=True,
        msg_type="pat", raw={"pat": {"to_self": True}},
    )
    assert _addressed_to_me(mine, "鱼") is True
    others = Message(
        chat_id="r@chatroom", chat_name="群", content='"张三" 拍了拍 "李四"', is_group=True,
        msg_type="pat", raw={"pat": {"to_self": False}},
    )
    assert _addressed_to_me(others, "鱼") is False


def test_default_chat_gap_shortened_but_still_above_direct_gap():
    """群聊冷却整体降档，但仍要比点名短冷却慢，否则分层就没意义了。"""
    assert DEFAULTS["send"]["chat_gap_sec"] == [12, 25]
    chat_lo, chat_hi = _gap_range(DEFAULTS["send"]["chat_gap_sec"], (0.0, 0.0))
    dir_lo, dir_hi = _gap_range(DEFAULTS["send"]["direct_gap_sec"], (0.0, 0.0))
    assert dir_hi <= chat_lo
    assert DEFAULTS["send"]["conversation_window_sec"] == 60


def test_send_gaps_chat_gap_fallback_matches_new_default():
    cfg = _send_cfg()
    cfg["send"].pop("chat_gap_sec")
    s = AccountSession({"id": "a", "name": "A", "nickname": "B"}, lambda: cfg, None)
    assert s._send_gaps()[1] == (12.0, 25.0)


def _row(ts: float, is_self: int, source: str = "wechat") -> dict:
    return {"ts": ts, "is_self": is_self, "source": source}


def test_live_conversation_needs_recent_ai_outbound():
    now = 1000.0
    rows = [_row(now - 200, 0), _row(now - 30, 1, "agent"), _row(now - 10, 0)]
    assert _in_live_conversation(rows, now=now, window=60.0) is True
    # AI 上一条已经是三分钟前：这轮算重新插话，按长冷却
    cold = [_row(now - 200, 1, "agent"), _row(now - 10, 0)]
    assert _in_live_conversation(cold, now=now, window=60.0) is False
    # 只有人工出站（微信回显）不算 AI 在接话
    manual = [_row(now - 30, 1, "wechat"), _row(now - 10, 0)]
    assert _in_live_conversation(manual, now=now, window=60.0) is False
    # 没有任何来消息 / 空记录
    assert _in_live_conversation([_row(now - 30, 1, "agent")], now=now, window=60.0) is False
    assert _in_live_conversation([], now=now, window=60.0) is False


def test_live_conversation_window_is_the_config_value():
    now = 1000.0
    rows = [_row(now - 70, 1, "agent"), _row(now - 5, 0)]
    assert _in_live_conversation(rows, now=now, window=60.0) is False
    assert _in_live_conversation(rows, now=now, window=120.0) is True
