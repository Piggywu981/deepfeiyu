"""发送闸门：禁发名单与身份未知时的行为。"""
import pytest

from app.wechat.base import DriverError
from app.wechat.manager import AccountSession


class _FakeDriver:
    def __init__(self, wxid: str) -> None:
        self._self_wxid = wxid
        self.sent: list[str] = []
        self.supports_contacts = False

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append(text)


def _session(wxid: str, send_cfg: dict | None = None) -> AccountSession:
    s = AccountSession.__new__(AccountSession)   # 不走 __init__，只装闸门需要的字段
    s.driver = _FakeDriver(wxid)
    s.account_id = "acc1"
    s._recent_outbound = {}
    # 闸门读会话自己的配置（与 manager 内其它地方一致），不是全局 config.load()
    s.get_cfg = lambda: {"send": send_cfg or {}}   # type: ignore[method-assign]
    return s


@pytest.fixture()
def forbidden():
    """禁发名单由账号配置提供；真实 wxid 只出现在本地 data/config.json。"""
    return {"forbidden_wxids": ["wxid_main_no"]}


def test_forbidden_wxid_refuses_send_text(forbidden):
    s = _session("wxid_main_no", forbidden)
    with pytest.raises(DriverError, match="禁发名单"):
        s._assert_send_allowed()


def test_unknown_identity_refuses_send(forbidden):
    s = _session("", forbidden)
    with pytest.raises(DriverError, match="未能确认"):
        s._assert_send_allowed()


def test_allowed_wxid_passes_gate():
    s = _session("wxid_test_ok", {"forbidden_wxids": []})
    s._assert_send_allowed()   # 不抛即通过


def test_gate_tolerates_config_without_send_section():
    """配置里没有 send 段时不能抛 KeyError：那会被上层吞掉，表现为「静默不发」。"""
    s = _session("wxid_test_ok")
    s.get_cfg = lambda: {}      # type: ignore[method-assign]
    s._assert_send_allowed()


def test_missing_driver_refuses_send():
    s = _session("wxid_test_ok")
    s.driver = None
    with pytest.raises(DriverError, match="通道未启动"):
        s._assert_send_allowed()
