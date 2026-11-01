"""发送来源：控制台人工发送默认 human；Harness 智能体发送传 dsh；非法值由 Pydantic 拒绝。

测试走真实的 FastAPI 路由与 Pydantic 校验（不是直接调函数），这样 422 是校验层
真的给出的，而不是测试自己伪造的；只替换账号会话与配置读取，避开真实微信通道与
数据库写入（接管暂停置 0）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main as m


class _Session:
    """只记录 send_text 的入参，覆盖来源透传这一条链路。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_text(
        self, uid: str, text: str, source: str = "human", **kwargs
    ) -> dict:
        self.calls.append((uid, text, source))
        return {"id": "m1", "uid": uid, "text": text, "source": source}


def _client(monkeypatch) -> tuple[TestClient, _Session]:
    session = _Session()
    base = config.load()
    cfg = {
        **base,
        "server": {**base.get("server", {}), "access_token": ""},
        # 发送成功后是否写接管暂停与本用例无关；置 0 避免测试碰数据库
        "auto": {**base.get("auto", {}), "takeover_pause_min": 0},
    }
    monkeypatch.setattr(m.config, "load", lambda: cfg)
    monkeypatch.setattr(m.hub, "require", lambda _account_id=None: session)
    return TestClient(m.app), session


def test_source_defaults_to_human(monkeypatch):
    client, session = _client(monkeypatch)
    res = client.post("/api/chats/uid1/send", json={"text": "你好"})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert session.calls == [("uid1", "你好", "human")]


def test_dsh_source_is_forwarded(monkeypatch):
    client, session = _client(monkeypatch)
    res = client.post("/api/chats/uid1/send", json={"text": "  你好  ", "source": "dsh"})
    assert res.status_code == 200
    assert res.json()["message"]["source"] == "dsh"
    # 文本仍由服务端去空白，来源原样透传给会话层
    assert session.calls == [("uid1", "你好", "dsh")]


def test_blank_text_is_still_rejected(monkeypatch):
    client, session = _client(monkeypatch)
    res = client.post("/api/chats/uid1/send", json={"text": "   ", "source": "dsh"})
    assert res.status_code == 400
    assert "内容不能为空" in res.json()["detail"]
    assert session.calls == []


@pytest.mark.parametrize("bad_source", ["wechat", "dsh ", "", None, 1])
def test_invalid_source_is_rejected_by_validation(monkeypatch, bad_source):
    client, session = _client(monkeypatch)
    res = client.post(
        "/api/chats/uid1/send", json={"text": "你好", "source": bad_source}
    )
    assert res.status_code == 422
    assert any(item["loc"][-1] == "source" for item in res.json()["detail"])
    assert session.calls == []
