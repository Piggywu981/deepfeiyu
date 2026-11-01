"""写操作只允许本机：直接校验中间件行为。"""
import asyncio

from fastapi.responses import JSONResponse

from app import main as m


class _Req:
    def __init__(self, method: str, path: str, host: str) -> None:
        self.method = method
        self.url = type("U", (), {"path": path})()
        self.client = type("C", (), {"host": host})()
        self.headers = {}
        self.query_params = {}


async def _run(req: _Req):
    called: dict[str, bool] = {}

    async def call_next(_r):
        called["v"] = True
        return "ok"

    res = await m.auth_middleware(req, call_next)   # noqa: SLF001
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


def test_remote_get_passes():
    res, called = asyncio.run(_run(_Req("GET", "/api/status", "192.168.1.50")))
    assert res == "ok"


def test_remote_options_preflight_passes():
    res, called = asyncio.run(_run(_Req("OPTIONS", "/api/settings", "192.168.1.50")))
    assert res == "ok"


def test_remote_delete_is_403():
    res, _ = asyncio.run(_run(_Req("DELETE", "/api/emojis/x", "10.0.0.9")))
    assert res.status_code == 403
