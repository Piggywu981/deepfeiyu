"""只有一个 webhook 会话时，也不能免凭据注入入站消息。"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main as m


class _FakeDriver:
    def __init__(self) -> None:
        self.injected: list[dict] = []

    def inject(self, payload: dict):
        self.injected.append(payload)
        return SimpleNamespace(msg_id="m1")


class _FakeSession:
    driver_name = "webhook"
    account_id = "acc1"

    def __init__(self, token: str = "") -> None:
        self.account = {"webhook": {"token": token}}
        self.driver = _FakeDriver()


class _FakeHub:
    def __init__(self, sessions: dict) -> None:
        self.sessions = sessions

    def get(self, aid: str):
        return self.sessions.get(aid)


def test_single_webhook_session_still_requires_credentials(monkeypatch):
    s = _FakeSession()
    monkeypatch.setattr(m, "hub", _FakeHub({"acc1": s}))
    res = TestClient(m.app).post("/api/webhook/inbound", json={"content": "hello"})
    assert res.status_code == 400
    assert s.driver.injected == []


def test_token_match_still_routes(monkeypatch):
    s = _FakeSession("tok-1")
    monkeypatch.setattr(m, "hub", _FakeHub({"acc1": s}))
    res = TestClient(m.app).post("/api/webhook/inbound", json={"content": "hi", "token": "tok-1"})
    assert res.status_code == 200
    assert s.driver.injected == [{"content": "hi", "token": "tok-1"}]


def test_account_field_still_routes(monkeypatch):
    s = _FakeSession()
    monkeypatch.setattr(m, "hub", _FakeHub({"acc1": s}))
    res = TestClient(m.app).post("/api/webhook/inbound", json={"content": "hi", "account": "acc1"})
    assert res.status_code == 200
    assert len(s.driver.injected) == 1
