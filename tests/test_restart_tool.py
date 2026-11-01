"""restart_self 工具与重启拉起进程。"""
import asyncio

from app import main as app_main
from app.ai import tools as tools_mod
from app.wechat.manager import AccountSession


def _session() -> AccountSession:
    return AccountSession({"id": "acc_x", "name": "A", "nickname": "B"}, lambda: {"ai": {}}, None)


def test_restart_tool_only_with_callback():
    session = _session()
    assert not any(t.name == "restart_self" for t in tools_mod.build_tools(session, allow_send=False))
    assert not any(t.name == "restart_self" for t in tools_mod.build_tools(session, allow_persona=True))
    with_restart = tools_mod.build_tools(session, allow_send=True, restart=lambda r: None)
    assert any(t.name == "restart_self" for t in with_restart)


def test_restart_self_calls_callback_with_reason():
    session = _session()
    called: list[str] = []
    tool = {
        t.name: t
        for t in tools_mod.build_tools(session, allow_send=True, restart=called.append)
    }["restart_self"]

    res = asyncio.run(tool.handler({"reason": "配置需要生效"}))
    assert res["ok"] is True
    assert called == ["配置需要生效"]

    # 不带 reason 也有默认文案
    res = asyncio.run(tool.handler({}))
    assert res["ok"] is True
    assert called[-1] == "AI 请求重启"


def test_no_lifecycle_tools_without_callbacks():
    """没传 shutdown/restart 回调时，两个生命周期工具都不注册。"""
    session = _session()
    names = {t.name for t in tools_mod.build_tools(session, allow_send=True)}
    assert "shutdown_self" not in names
    assert "restart_self" not in names


def test_spawn_restarter_launches_detached_process(monkeypatch):
    captured: dict = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return None

    monkeypatch.setattr(app_main.subprocess, "Popen", fake_popen)
    app_main._spawn_restarter()
    assert "run.bat" in captured["args"][-1]
    assert captured["cwd"] == str(app_main.config.ROOT)
