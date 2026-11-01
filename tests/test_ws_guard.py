"""实时通道的准入判定：设了令牌就不能匿名连，非本机一律拒。"""
import pytest

from app import main as m


@pytest.fixture()
def with_token(monkeypatch):
    monkeypatch.setattr(
        m.config, "load",
        lambda: {"server": {"access_token": "s3cr3t"}, "ai": {}},
    )


def test_no_token_rejected_when_configured(with_token):
    assert m.guard_ws("", "testclient") == 4401


def test_wrong_token_rejected(with_token):
    assert m.guard_ws("nope", "testclient") == 4401


def test_right_token_from_local_allowed(with_token):
    assert m.guard_ws("s3cr3t", "127.0.0.1") is None


def test_right_token_from_remote_still_rejected(with_token):
    assert m.guard_ws("s3cr3t", "192.168.1.20") == 4403


def test_no_token_configured_allows_local(monkeypatch):
    monkeypatch.setattr(m.config, "load", lambda: {"server": {"access_token": ""}, "ai": {}})
    assert m.guard_ws("", "127.0.0.1") is None
    assert m.guard_ws("", "10.1.2.3") == 4403


def test_frontend_sends_token_on_websocket():
    """前端不带令牌的话，设了令牌的控制台自己会连不上实时推送。"""
    from pathlib import Path

    js = Path("app/static/app.js").read_text(encoding="utf-8")
    ws_lines = [ln for ln in js.splitlines() if "new WebSocket(" in ln]
    assert ws_lines, "app.js 里找不到 WebSocket 连接"
    assert all("withToken(" in ln for ln in ws_lines), ws_lines
