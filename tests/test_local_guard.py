"""本机来源与令牌：写操作只允许本机，未设令牌时读接口也只允许本机。"""
import asyncio

from fastapi.responses import JSONResponse

from app import main as m


class _Req:
    def __init__(
        self,
        method: str,
        path: str,
        host: str,
        *,
        netloc: str = "testserver",
        headers: dict | None = None,
        query: dict | None = None,
    ) -> None:
        self.method = method
        self.url = type("U", (), {"path": path, "netloc": netloc})()
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}
        self.query_params = query or {}


async def _run(req: _Req):
    called: dict[str, bool] = {}

    async def call_next(_r):
        called["v"] = True
        return "ok"

    res = await m.auth_middleware(req, call_next)   # noqa: SLF001
    return res, called


async def _run_origin(req: _Req):
    called: dict[str, bool] = {}

    async def call_next(_r):
        called["v"] = True
        return "ok"

    res = await m.origin_guard(req, call_next)   # noqa: SLF001
    return res, called


def test_remote_post_is_403():
    res, called = asyncio.run(_run(_Req("POST", "/api/settings", "192.168.1.50")))
    assert isinstance(res, JSONResponse) and res.status_code == 403
    assert "v" not in called


def test_local_post_passes():
    res, called = asyncio.run(_run(_Req("POST", "/api/settings", "127.0.0.1")))
    assert res == "ok" and called.get("v") is True


def test_testclient_post_passes():
    res, called = asyncio.run(_run(_Req("POST", "/api/agent/chat", "testclient")))
    assert res == "ok"


def test_remote_get_without_token_is_403(monkeypatch):
    """没设令牌时把服务监听到 0.0.0.0，局域网不该能读聊天与配置。"""
    monkeypatch.setattr(m.config, "load", lambda: {"server": {"access_token": ""}})
    res, called = asyncio.run(_run(_Req("GET", "/api/status", "192.168.1.50")))
    assert isinstance(res, JSONResponse) and res.status_code == 403
    assert "v" not in called


def test_local_get_without_token_passes(monkeypatch):
    monkeypatch.setattr(m.config, "load", lambda: {"server": {"access_token": ""}})
    res, _ = asyncio.run(_run(_Req("GET", "/api/status", "127.0.0.1")))
    assert res == "ok"


def test_remote_get_with_token_header_passes(monkeypatch):
    monkeypatch.setattr(m.config, "load", lambda: {"server": {"access_token": "s3cr3t"}})
    req = _Req("GET", "/api/status", "192.168.1.50", headers={"x-access-token": "s3cr3t"})
    res, _ = asyncio.run(_run(req))
    assert res == "ok"


def test_query_token_only_for_reads(monkeypatch):
    """查询参数里的令牌只给图片/下载这类带不了自定义头的只读请求用。"""
    monkeypatch.setattr(m.config, "load", lambda: {"server": {"access_token": "s3cr3t"}})
    read = _Req("GET", "/api/status", "127.0.0.1", query={"token": "s3cr3t"})
    write = _Req("POST", "/api/settings", "127.0.0.1", query={"token": "s3cr3t"})
    assert asyncio.run(_run(read))[0] == "ok"
    res, called = asyncio.run(_run(write))
    assert isinstance(res, JSONResponse) and res.status_code == 401
    assert "v" not in called


def test_remote_options_preflight_passes():
    res, _ = asyncio.run(_run(_Req("OPTIONS", "/api/settings", "192.168.1.50")))
    assert res == "ok"


def test_remote_delete_is_403():
    res, _ = asyncio.run(_run(_Req("DELETE", "/api/emojis/x", "10.0.0.9")))
    assert res.status_code == 403


def test_token_match_handles_non_ascii_without_raising():
    assert m._token_match("密码", "密码") is True   # noqa: SLF001
    assert m._token_match("密码", "other") is False   # noqa: SLF001


def test_cross_site_post_is_403():
    req = _Req(
        "POST", "/api/settings", "127.0.0.1",
        netloc="127.0.0.1:8765",
        headers={"origin": "https://evil.example"},
    )
    res, called = asyncio.run(_run_origin(req))
    assert isinstance(res, JSONResponse) and res.status_code == 403
    assert "v" not in called


def test_same_origin_post_passes():
    req = _Req(
        "POST", "/api/settings", "127.0.0.1",
        netloc="127.0.0.1:8765",
        headers={"origin": "http://127.0.0.1:8765"},
    )
    res, _ = asyncio.run(_run_origin(req))
    assert res == "ok"


def test_post_without_origin_passes():
    """Webhook 网关与 curl 不发 Origin，不能误伤。"""
    res, _ = asyncio.run(_run_origin(_Req("POST", "/api/webhook/inbound", "127.0.0.1")))
    assert res == "ok"


def test_cross_site_get_is_not_blocked_by_origin_guard():
    req = _Req(
        "GET", "/api/status", "127.0.0.1",
        netloc="127.0.0.1:8765",
        headers={"origin": "https://evil.example"},
    )
    res, _ = asyncio.run(_run_origin(req))
    assert res == "ok"
