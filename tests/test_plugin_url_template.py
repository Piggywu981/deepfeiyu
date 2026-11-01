"""插件工具的 URL 模板不可被模型整体接管（钉住现有行为）。"""
import asyncio

import httpx

from app.capabilities import plugins


def test_bare_arg_url_template_is_rejected():
    # 整串 "{target}" 不是 http(s) 开头，scheme 检查在渲染之前就把它拦下了
    spec = {"url": "{target}", "method": "GET"}
    out = asyncio.run(
        plugins.call_http_tool(spec, {"target": "http://127.0.0.1:8765/api/accounts"})
    )
    assert isinstance(out, dict) and "error" in out
    assert "http/https" in out["error"]


def test_arg_cannot_rewrite_host(monkeypatch):
    captured: list[str] = []
    real = httpx.AsyncClient

    class _Stop:
        def __init__(self, **kw):
            self._c = real(**kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            captured.append(str(url))
            raise RuntimeError("stop after capture")

    monkeypatch.setattr(plugins.httpx, "AsyncClient", _Stop)
    spec = {"url": "https://api.example.com/{p}", "method": "GET"}
    out = asyncio.run(plugins.call_http_tool(spec, {"p": "../../@127.0.0.1"}))
    assert captured, "没有走到请求"
    parsed = httpx.URL(captured[0])
    # 要守的是请求打到哪：主机与 userinfo 不能被参数改写，路径里出现 IP 字面量无害
    assert parsed.host == "api.example.com"
    assert not parsed.username and not parsed.password
    assert parsed.port in (None, 443)
    assert "error" in out


def test_rendered_path_value_is_percent_encoded():
    # quote(safe="") 让参数不能靠 / 或 @ 逃出原来的路径段
    out = plugins._render("https://api.example.com/{p}", {"p": "a/b@c"}, url=True)
    assert out == "https://api.example.com/a%2Fb%40c"
