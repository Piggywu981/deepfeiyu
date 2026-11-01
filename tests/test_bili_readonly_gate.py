"""巡逻 Agent 的工具集里不能出现发布类工具，即使 mcp.json 被手改。"""
from app.bili import patrol as patrol_mod
from app.bili.client import FORBIDDEN_TOOLS

_ALL = [
    "mcp__bilibili__bili_read_dynamic",
    "mcp__bilibili__bili_get_user_info",
    "mcp__bilibili__bili_reply",
    "mcp__bilibili__bili_upload_video",
    "mcp__bilibili__bili_triple",
    "mcp__bilibili__bili_send_dynamic",
    "mcp__other__tool",
]


class _T:
    def __init__(self, name: str) -> None:
        self.name = name


class _Reg:
    class mcp:  # noqa: N801
        @staticmethod
        def tools_for(auto: bool = False) -> list[_T]:
            return [_T(n) for n in _ALL]


def _scheduler() -> patrol_mod.BiliPatrolScheduler:
    s = patrol_mod.BiliPatrolScheduler.__new__(patrol_mod.BiliPatrolScheduler)
    s.registry = _Reg()
    return s


def test_publish_tools_filtered_out():
    names = {t.name for t in _scheduler()._tools()}
    banned = {f"mcp__bilibili__{n}" for n in FORBIDDEN_TOOLS}
    assert not (names & banned), names
    assert "mcp__bilibili__bili_read_dynamic" in names
    assert "mcp__other__tool" not in names


def test_registry_without_mcp_yields_nothing():
    s = _scheduler()
    s.registry = type("R", (), {})()
    assert s._tools() == []
