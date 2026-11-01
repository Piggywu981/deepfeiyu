"""weixin4 通道发送：UIA 搜索定位链路 + 剪贴板文件构造与前置校验。"""
import ctypes
import threading
import types

import pytest

from app.wechat import weixin4_driver
from app.wechat.base import DriverError
from app.wechat.weixin4_driver import Weixin4Driver, _DROPFILES, hdrop_blob


def test_hdrop_blob_single_path():
    blob = hdrop_blob([r"C:\tmp\a.gif"])
    header = _DROPFILES.from_buffer_copy(blob)
    assert header.pFiles == ctypes.sizeof(_DROPFILES)
    assert header.fWide
    assert blob[header.pFiles:].decode("utf-16-le") == "C:\\tmp\\a.gif\0\0"


def test_hdrop_blob_multiple_paths():
    blob = hdrop_blob([r"C:\tmp\a.gif", r"C:\tmp\b.png"])
    header = _DROPFILES.from_buffer_copy(blob)
    payload = blob[header.pFiles:].decode("utf-16-le")
    assert payload == "C:\\tmp\\a.gif\0C:\\tmp\\b.png\0\0"


def test_send_emoji_requires_connected():
    driver = Weixin4Driver(None)
    with pytest.raises(DriverError, match="未就绪"):
        driver.send_emoji("filehelper", r"C:\tmp\missing.gif")


def test_rhythm_profile_pinned_to_fast(monkeypatch):
    """wechatauto 自带的拟人节奏要压到 fast：natural 会在每次写前再隔 2.5–6s，
    120s 内写满 6 次直接睡 30–75s，与本项目 chat_gap 叠成两重节流。"""
    from wechatauto import rhythm

    calls: list[str] = []
    monkeypatch.delenv("WECHATAUTO_RHYTHM", raising=False)
    monkeypatch.setattr(rhythm, "set_profile", lambda name: calls.append(name))
    assert weixin4_driver._apply_rhythm_profile() == "fast"
    assert calls == ["fast"]


def test_rhythm_profile_respects_env_override(monkeypatch):
    from wechatauto import rhythm

    calls: list[str] = []
    monkeypatch.setenv("WECHATAUTO_RHYTHM", "natural")
    monkeypatch.setattr(rhythm, "set_profile", lambda name: calls.append(name))
    assert weixin4_driver._apply_rhythm_profile() == "natural"
    assert calls == []  # 用户显式指定过就不覆盖


def test_send_file_requires_connected():
    driver = Weixin4Driver(None)
    with pytest.raises(DriverError, match="未就绪"):
        driver.send_file("filehelper", r"C:\tmp\missing.gif")


def test_send_clipboard_file_missing_path():
    driver = Weixin4Driver(None)
    driver._connected = True
    with pytest.raises(DriverError, match="文件不存在"):
        driver._send_clipboard_file("filehelper", r"C:\tmp\definitely-missing.gif")


def test_msg_id_includes_chat_to_avoid_cross_chat_collision():
    """微信 4.x 每个会话一张 Msg 表、local_id 每表从 1 起：
    不同会话的同号行若共用「分片#local_id」会互相吞消息、跨会话覆盖。"""
    driver = Weixin4Driver(None)
    driver._self_wxid = "wxid_self"
    driver._chats = {"111@chatroom": "甲群", "222@chatroom": "乙群"}
    row = {
        "shard": "message__message_0.db",
        "local_id": "11",
        "type": "文本",
        "sender_username": "wxid_a",
        "sender_id": 5,
        "create_time": 1792938711.0,
        "content": "你好",
    }
    a = driver._row_to_message("111@chatroom", dict(row))
    b = driver._row_to_message("222@chatroom", dict(row))
    assert a.msg_id == "111@chatroom#message__message_0.db#11"
    assert a.msg_id != b.msg_id


def test_split_msg_id_supports_all_formats():
    # 现行格式：会话#分片#local_id
    assert Weixin4Driver._split_msg_id("111@chatroom#message__message_0.db#11") == (
        "message__message_0.db",
        "11",
    )
    # 旧格式（历史入库数据）：分片#local_id
    assert Weixin4Driver._split_msg_id("message__message_0.db#798") == (
        "message__message_0.db",
        "798",
    )
    # 裸 local_id + 调用方传的 shard 优先
    assert Weixin4Driver._split_msg_id("42", "message__message_1.db") == (
        "message__message_1.db",
        "42",
    )
    assert Weixin4Driver._split_msg_id("chat#s1#9", "s2") == ("s2", "9")


# ---------------------------------------------------------------- UIA 搜索定位发送
class _FakeCtrl:
    class BoundingRectangle:
        left, top, right, bottom = 10, 20, 400, 60


class FakeUIA:
    """替身 UIA 引擎：只记录调用顺序，模拟搜索/打开/校验/输入。"""

    hwnd = 0

    def __init__(self, *, current: str = "", open_ok: bool = True,
                 opened: str = "", send_ok: bool = True,
                 thread_names: list[str] | None = None):
        self._current = current
        self._open_ok = open_ok
        self._opened = opened
        self._send_ok = send_ok
        self.calls: list[tuple] = []
        self._thread_names = thread_names

    def _mark(self, name: str) -> None:
        self.calls.append((name, threading.current_thread().name))
        if self._thread_names is not None:
            self._thread_names.append(threading.current_thread().name)

    def ensure_window(self) -> bool:
        self._mark("ensure_window")
        return True

    def current_chat(self) -> str:
        self._mark("current_chat")
        return self._current

    def open_chat(self, name: str) -> bool:
        self._mark("open_chat")
        self.calls.append(("open_chat_arg", name))
        if not self._open_ok:
            return False
        self._current = self._opened or name
        return True

    def send_text(self, text: str) -> bool:
        self._mark("send_text")
        self.calls.append(("send_text_arg", text))
        return self._send_ok

    def _chat_input(self):
        self._mark("chat_input")
        return _FakeCtrl()

    def _click_ctrl(self, ctrl) -> bool:
        self._mark("click_ctrl")
        return True


def _driver_with_uia(monkeypatch, uia: FakeUIA, chats: dict[str, str] | None = None) -> Weixin4Driver:
    driver = Weixin4Driver(None)
    driver._connected = True
    driver._chats = dict(chats or {})
    driver._find_window = lambda: 4242  # type: ignore[assignment]
    monkeypatch.setattr(weixin4_driver, "_uia_cls", lambda **kw: uia)
    return driver


def _names(fake: FakeUIA) -> list[str]:
    return [c[0] for c in fake.calls if isinstance(c, tuple) and len(c) == 2 and c[0] != "open_chat_arg" and c[0] != "send_text_arg"]


def _args(fake: FakeUIA, kind: str) -> list:
    return [c[1] for c in fake.calls if isinstance(c, tuple) and c[0] == kind]


def test_send_text_searches_nickname_then_verifies_target(monkeypatch):
    uia = FakeUIA(current="别人", open_ok=True)
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    driver.send_text("wxid_x", "你好")
    assert _args(uia, "open_chat_arg") == ["93"]      # 搜的是对方昵称
    assert _args(uia, "send_text_arg") == ["你好"]
    # 顺序：先确保窗口 → 打开并校验目标 → 才输入发送
    names = [c[0] for c in uia.calls if isinstance(c[0], str)]
    assert names.index("open_chat") < names.index("send_text")
    assert names.count("current_chat") >= 1


def test_send_text_falls_back_to_resolve_name(monkeypatch):
    uia = FakeUIA(current="", open_ok=True)
    driver = _driver_with_uia(monkeypatch, uia)
    monkeypatch.setattr(driver, "resolve_name", lambda username: "小号")
    driver.send_text("wxid_x", "在吗")
    assert _args(uia, "open_chat_arg") == ["小号"]


def test_send_text_aborts_when_search_misses(monkeypatch):
    uia = FakeUIA(current="", open_ok=False)
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "找不到的人"})
    with pytest.raises(DriverError, match="没找到会话"):
        driver.send_text("wxid_x", "你好")
    assert _args(uia, "send_text_arg") == []  # 没打开目标就绝不发送


def test_send_text_aborts_when_opened_wrong_chat(monkeypatch):
    uia = FakeUIA(current="", open_ok=True, opened="别人")
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    with pytest.raises(DriverError, match="会话校验失败"):
        driver.send_text("wxid_x", "你好")
    assert _args(uia, "send_text_arg") == []


def test_send_text_aborts_when_paste_not_confirmed(monkeypatch):
    uia = FakeUIA(current="93", send_ok=False)
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    with pytest.raises(DriverError, match="未回车"):
        driver.send_text("wxid_x", "你好")


def test_send_runs_on_dedicated_rpa_thread(monkeypatch):
    uia = FakeUIA(current="93")
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    try:
        driver.send_text("wxid_x", "你好")
        names = [c[1] for c in uia.calls if isinstance(c, tuple) and len(c) == 2 and c[0] == "ensure_window"]
        assert names and all(n.startswith("weixin4-rpa-") for n in names)
    finally:
        driver._stop_rpa_thread()


def test_send_file_pastes_into_verified_target(monkeypatch, tmp_path):
    path = tmp_path / "a.gif"
    path.write_bytes(b"GIF89a" + b"\x00" * 32)
    uia = FakeUIA(current="别人", open_ok=True)
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    events: list[str] = []
    monkeypatch.setattr(driver, "_copy_hdrop", lambda paths: events.append("copy:" + paths[0]))
    monkeypatch.setattr(weixin4_driver.Weixin4Driver, "_ctrl_v", staticmethod(lambda: events.append("ctrl_v")))
    monkeypatch.setattr(weixin4_driver.Weixin4Driver, "_key", staticmethod(lambda vk: events.append(f"key:{vk}")))
    driver.send_file("wxid_x", str(path))
    assert _args(uia, "open_chat_arg") == ["93"]
    assert [e.split(":")[0] for e in events] == ["copy", "ctrl_v", "key"]
    assert events[-1] == "key:13"  # 回车发送


def test_stop_stops_rpa_thread(monkeypatch):
    uia = FakeUIA(current="93")
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    driver.send_text("wxid_x", "你好")
    driver.stop()
    assert driver._rpa_thread is None and driver._rpa_q is None


# ---------------------------------------------------------------- UIA 引擎绑定（回归）
def _install_fake_uia_module(monkeypatch, base_cls):
    """伪造 wechatauto.uia_driver，隔离测试「hwnd 有没有传进库的 __init__」。"""
    import sys
    import types

    mod = types.ModuleType("wechatauto.uia_driver")
    mod.MAIN_CLASSES = ("mmui::MainWindow",)
    mod.WeChatUIA = base_cls
    pkg = types.ModuleType("wechatauto")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "wechatauto", pkg)
    monkeypatch.setitem(sys.modules, "wechatauto.uia_driver", mod)
    monkeypatch.setattr(weixin4_driver, "_uia_cls", None)


def test_uia_engine_passes_hwnd_into_library(monkeypatch):
    """回归：hwnd 必须传进库的 __init__，否则 _bound_pid 为空、
    _candidate_hwnds() 恒为空 → 微信重启后 gate 归零时 ensure_window 静默失败。"""
    from types import SimpleNamespace

    class FakeBase:
        def __init__(self, timeout=15.0, search_timeout=2.0, hwnd=None):
            self.hwnd = hwnd
            self._bound_pid = 4242 if hwnd else None

        def _anchor(self, hwnd):
            return SimpleNamespace(ClassName="mmui::MainWindow")

    _install_fake_uia_module(monkeypatch, FakeBase)
    cls = weixin4_driver._uia_class()
    engine = cls(hwnd=777)
    assert engine.hwnd == 777
    assert engine._bound_pid == 4242


def test_uia_engine_falls_back_when_library_rejects_hwnd(monkeypatch):
    """旧版库不收 hwnd 参数时也要补上 _bound_pid，不能退化成永远无法热激活。"""
    from types import SimpleNamespace

    class OldBase:
        def __init__(self, timeout=15.0, search_timeout=2.0):
            self.hwnd = None
            self._bound_pid = None

        def _anchor(self, hwnd):
            return SimpleNamespace(ClassName="mmui::MainWindow")

        def _pid_from_hwnd(self, hwnd):
            return 999

    _install_fake_uia_module(monkeypatch, OldBase)
    cls = weixin4_driver._uia_class()
    engine = cls(hwnd=888)
    assert engine.hwnd == 888
    assert engine._bound_pid == 999
# ---------------------------------------------------------------- 发送后恢复前台
def test_send_restores_previous_foreground(monkeypatch):
    """发送任务结束后把发送前的前台窗口还回去（微信本来就在前台时不抢）。"""
    uia = FakeUIA(current="93")
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "93"})
    restored: list[int] = []
    monkeypatch.setattr(driver, "_foreground_hwnd", lambda: 111)
    monkeypatch.setattr(driver, "_restore_foreground", lambda prev: restored.append(prev))
    try:
        driver.send_text("wxid_x", "你好")
        assert restored == [111]
    finally:
        driver._stop_rpa_thread()


# ---------------------------------------------------------------- UIA 扫描范围（性能）
class _Node:
    """假 UIA 节点：只需要 GetChildren + 两个属性，够复刻库的 _find_by 契约。"""

    def __init__(self, aid: str = "", ct: str = "PaneControl", kids=(), tag: str = ""):
        self.AutomationId = aid
        self.ControlTypeName = ct
        self._kids = list(kids)
        self.tag = tag
        self.walked = 0

    def GetChildren(self):
        self.walked += 1
        return list(self._kids)


def _find_by_recorder(roots_seen):
    """复刻 wechatauto.uia_driver._find_by：记录每次被要求遍历的根节点。"""

    def find_by(root, pred, max_depth=40):
        roots_seen.append(root)
        stack = [(root, 0)]
        while stack:
            el, d = stack.pop()
            if d > max_depth:
                continue
            if pred(el):
                return el
            stack.extend((k, d + 1) for k in el.GetChildren())
        return None

    return find_by


def test_search_list_scoped_walks_only_given_roots():
    """搜索列表必须在「微信自己的窗口」子树里找：上游那版从桌面根节点全扫，
    实测本机单轮 6.6s/4282 节点、无界搜索 8.8s 还落空，而每条消息都要付一遍。"""
    seen: list = []
    inner = _Node(aid="search_list", ct="ListControl", tag="inside_wechat")
    win = _Node(kids=[_Node(kids=[inner])], tag="wechat_main")
    outside = _Node(aid="search_list", ct="ListControl", tag="other_app")

    hit = weixin4_driver._search_list_in_roots(
        [win], _find_by_recorder(seen), lambda aid, cands: aid in cands, ("search_list",)
    )
    assert hit is inner
    assert seen == [win]  # 只从传入的微信窗口起扫
    assert outside.walked == 0  # 别的应用的子树一次都没碰


def test_search_list_scoped_returns_none_instead_of_escaping_to_desktop():
    """微信子树里没有就是没有：绝不为了「再找找」去遍历桌面根节点。"""
    seen: list = []
    win = _Node(tag="empty_wechat")
    hit = weixin4_driver._search_list_in_roots(
        [win], _find_by_recorder(seen), lambda aid, cands: aid in cands, ("search_list",)
    )
    assert hit is None
    assert seen == [win]  # 确实扫过，但没有逃去桌面


def test_scoped_roots_are_wechat_windows_only(monkeypatch):
    """根节点 = 主窗口 + 同进程的其它顶层窗口（搜索下拉是独立弹窗的情况），
    绝不包含桌面根，也不包含别的进程。"""
    main = _Node(tag="main")

    class FakeEngine:
        hwnd = 0  # 主窗口句柄未知：靠 _find_main 定位，弹窗按 pid 补
        _win = None
        _bound_pid = 99

        def _find_main(self):
            return main

        def _anchor(self, hwnd):
            return _Node(tag=f"popup{hwnd}")

    monkeypatch.setattr(weixin4_driver, "_wechat_top_hwnds", lambda pid: [4242, 5555])
    roots = weixin4_driver._scoped_roots(FakeEngine())
    assert [r.tag for r in roots] == ["main", "popup4242", "popup5555"]


def test_scoped_roots_dedup_main_window_and_skip_dead_handles(monkeypatch):
    """主窗口句柄已知时不再重复遍历同一棵树（一遍就是几秒）；
    _anchor 返回 None（句柄已失效）就跳过。"""
    main = _Node(tag="main")
    seen_main_calls: list[str] = []

    class FakeEngine:
        hwnd = 4242
        _win = main
        _bound_pid = 99

        def _find_main(self):
            seen_main_calls.append("find_main")
            return main

        def _anchor(self, hwnd):
            if hwnd == 4242:
                return _Node(tag="dup_main")  # 与 _win 同一棵树，应被丢掉
            if hwnd == 6666:
                return None  # 句柄已失效
            return _Node(tag=f"popup{hwnd}")

    monkeypatch.setattr(weixin4_driver, "_wechat_top_hwnds", lambda pid: [4242, 5555, 6666])
    roots = weixin4_driver._scoped_roots(FakeEngine())
    assert [r.tag for r in roots] == ["main", "popup5555"]
    assert seen_main_calls == []  # _win 已有，不必再定位主窗口


def test_engine_uses_scoped_search_and_falls_back_only_when_helpers_missing(monkeypatch):
    """接线：引擎实例的 _find_search_list 走限定范围那条路；找不到也直接返回 None
    （不能因为「这次没命中」退回桌面全扫）。只有库内部名字漂移时才退回上游实现。"""

    class FakeBase:
        def __init__(self, timeout=15.0, search_timeout=2.0, hwnd=None):
            self.hwnd = hwnd

        def _find_search_list(self, timeout: float = 3.0):
            return "DESKTOP_WIDE"

        def _anchor(self, hwnd):
            return None

        def _pid_from_hwnd(self, hwnd):
            return 4242

    _install_fake_uia_module(monkeypatch, FakeBase)
    cls = weixin4_driver._uia_class()
    engine = cls(hwnd=777)
    monkeypatch.setattr(weixin4_driver, "_search_list_scoped", lambda uia, timeout: "SCOPED")
    assert engine._find_search_list(0.5) == "SCOPED"
    monkeypatch.setattr(weixin4_driver, "_search_list_scoped", lambda uia, timeout: None)
    assert engine._find_search_list(0.5) is None  # 没命中就报没命中，不去扫桌面
    monkeypatch.setattr(
        weixin4_driver, "_search_list_scoped", lambda uia, timeout: weixin4_driver._SCOPE_UNAVAILABLE
    )
    assert engine._find_search_list(0.5) == "DESKTOP_WIDE"


# ---------------------------------------------------------------- 启动不预热 UIA
def test_uia_warmup_is_lazy_by_default():
    """开项目就预热 = 开项目那一刻就去强开微信无障碍 gate、物化控件树、扫跨进程 UIA；
    号主报「一开本项目微信就点不动」，所以默认改成首次发送时再热。"""
    assert Weixin4Driver(None).warmup_on_start is False
    assert Weixin4Driver(None, warmup_on_start=True).warmup_on_start is True


def test_manager_passes_warmup_flag_from_account():
    """旧行为要能一键回来：账号配置 weixin4.warmup_on_start=true 时驱动必须收到它。"""
    from app.wechat.manager import AccountSession

    on = AccountSession(
        {"id": "acc_a", "name": "A", "weixin4": {"account_dir": "", "warmup_on_start": True}},
        lambda: {},
        None,
    )
    off = AccountSession({"id": "acc_b", "name": "B"}, lambda: {}, None)
    assert on._build("weixin4").warmup_on_start is True
    assert off._build("weixin4").warmup_on_start is False



def test_send_failure_still_restores_foreground(monkeypatch):
    """发送抛错也要恢复前台，不能把微信留在前台。"""
    uia = FakeUIA(current="", open_ok=False)
    driver = _driver_with_uia(monkeypatch, uia, {"wxid_x": "找不到的人"})
    restored: list[int] = []
    monkeypatch.setattr(driver, "_foreground_hwnd", lambda: 222)
    monkeypatch.setattr(driver, "_restore_foreground", lambda prev: restored.append(prev))
    try:
        with pytest.raises(DriverError, match="没找到会话"):
            driver.send_text("wxid_x", "你好")
        assert restored == [222]
    finally:
        driver._stop_rpa_thread()


class _FakeUser32:
    def __init__(self, fg: int, *, valid: bool = True, visible: bool = True,
                 iconic: bool = False):
        self.fg = fg
        self.valid = valid
        self.visible = visible
        self.iconic = iconic
        self.calls: list[tuple] = []

    def GetForegroundWindow(self):
        return self.fg

    def GetWindowThreadProcessId(self, hwnd, ptr):
        return 7777

    def IsWindow(self, hwnd):
        return self.valid

    def IsWindowVisible(self, hwnd):
        return self.visible

    def IsIconic(self, hwnd):
        return self.iconic

    def AttachThreadInput(self, cur, target, on):
        self.calls.append(("attach", cur, target, on))
        return 1

    def BringWindowToTop(self, hwnd):
        self.calls.append(("bring", hwnd))
        return 1

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("setfg", hwnd))
        self.fg = hwnd
        return 1


def _restore_driver(monkeypatch, fake: _FakeUser32, pids: dict[int, int]):
    monkeypatch.setattr(weixin4_driver, "_u", fake)
    monkeypatch.setattr(weixin4_driver, "_k", types.SimpleNamespace(GetCurrentThreadId=lambda: 5))
    driver = Weixin4Driver(None)
    driver._pid = 999
    monkeypatch.setattr(driver, "_window_pid", lambda hwnd: pids.get(int(hwnd), 0))
    return driver


def test_restore_foreground_moves_focus_back(monkeypatch):
    fake = _FakeUser32(fg=222)
    driver = _restore_driver(monkeypatch, fake, {222: 999, 111: 111})
    driver._restore_foreground(111)
    assert ("setfg", 111) in fake.calls
    assert ("attach", 5, 7777, True) in fake.calls
    assert ("attach", 5, 7777, False) in fake.calls


def test_restore_foreground_skips_when_user_was_in_wechat(monkeypatch):
    fake = _FakeUser32(fg=222)
    driver = _restore_driver(monkeypatch, fake, {222: 999, 333: 999})
    driver._restore_foreground(333)
    assert ("setfg", 333) not in fake.calls


def test_restore_foreground_skips_when_foreground_is_other_app(monkeypatch):
    fake = _FakeUser32(fg=222)
    driver = _restore_driver(monkeypatch, fake, {222: 111, 111: 111})
    driver._restore_foreground(111)
    assert not any(c[0] == "setfg" for c in fake.calls)


def test_restore_foreground_skips_dead_or_minimized_window(monkeypatch):
    for kwargs in ({"valid": False}, {"visible": False}, {"iconic": True}):
        fake = _FakeUser32(fg=222, **kwargs)
        driver = _restore_driver(monkeypatch, fake, {222: 999, 111: 111})
        driver._restore_foreground(111)
        assert not any(c[0] == "setfg" for c in fake.calls)
