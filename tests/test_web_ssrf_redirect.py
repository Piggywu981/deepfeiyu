"""302 指向内网时，_assert_public 必须在每一跳生效，而不是只查第一个 URL。"""
import asyncio

import httpx
import pytest

from app.ai import web as webmod

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """让 example 域名解析到公网 IP，同时保留回环地址的真实判定。"""

    def getaddrinfo(host, port, *a, **kw):
        ip = "127.0.0.1" if host == "127.0.0.1" else PUBLIC_IP
        return [(2, 0, 0, "", (ip, 0))]

    monkeypatch.setattr(webmod.socket, "getaddrinfo", getaddrinfo)


_REAL_CLIENT = httpx.AsyncClient


def _stub_client(handler):
    def factory(**kwargs):
        # 用 patch 前抓住真类，否则 factory 里再构造 AsyncClient 会无限递归
        kwargs.pop("follow_redirects", None)
        kwargs.pop("timeout", None)
        return _REAL_CLIENT(transport=httpx.MockTransport(handler),
                            headers=webmod._HEADERS)

    return factory


def _redirect_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "a.example":
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8765/api/accounts"})
    raise AssertionError(f"不该对内网地址发起第二跳：{request.url}")


def test_fetch_text_refuses_redirect_to_loopback(monkeypatch):
    monkeypatch.setattr(webmod.httpx, "AsyncClient", _stub_client(_redirect_handler))
    with pytest.raises(webmod.WebError, match="内网|本机"):
        asyncio.run(webmod.fetch_text("http://a.example/page"))


def test_fetch_text_follows_public_chain(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/final"})
        return httpx.Response(200, text="<p>done</p>")

    monkeypatch.setattr(webmod.httpx, "AsyncClient", _stub_client(handler))
    out = asyncio.run(webmod.fetch_text("http://a.example/page"))
    assert seen == ["http://a.example/page", "https://b.example/final"]
    assert "done" in out["content"]


def test_download_file_refuses_redirect_to_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(webmod, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(webmod.httpx, "AsyncClient", _stub_client(_redirect_handler))
    with pytest.raises(webmod.WebError, match="内网|本机"):
        asyncio.run(webmod.download("http://a.example/f.bin"))


def test_redirect_chain_is_capped(monkeypatch):
    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://a.example/again"})

    monkeypatch.setattr(webmod.httpx, "AsyncClient", _stub_client(loop))
    with pytest.raises(webmod.WebError, match="重定向"):
        asyncio.run(webmod.fetch_text("http://a.example/page"))
