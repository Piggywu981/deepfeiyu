"""B 站 API：状态、登录、配置、本机限制。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bili import api as bili_api
from app.bili import client as bili_client
from app.bili import setup as bili_setup


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(bili_api.router)
    return TestClient(app)


def test_require_local_rejects_lan():
    req = SimpleNamespace(client=SimpleNamespace(host="192.168.1.9"))
    with pytest.raises(Exception) as exc:
        bili_api._require_local(req)
    assert "本机" in str(exc.value)


def test_status_and_config(client, monkeypatch):
    async def fake_status():
        return {
            "ok": True,
            "setup": {"installed": True},
            "mcp": {"connected": True},
            "credential": {"logged_in": True},
            "tools": [],
        }

    monkeypatch.setattr(bili_client, "status", fake_status)
    monkeypatch.setattr(bili_api, "_patrol_cfg", lambda aid: {"enabled": False})
    res = client.get("/api/bili/status")
    assert res.status_code == 200
    assert res.json()["credential"]["logged_in"] is True
    cfg = client.get("/api/bili/config")
    assert cfg.status_code == 200
    assert cfg.json()["patrol"]["enabled"] is False


def test_login_routes(client, monkeypatch):
    async def fake_start():
        return {"status": "qrcode_ready", "qrcode_image": "data:image/png;base64,AAA"}

    async def fake_poll():
        return {"status": "done"}

    monkeypatch.setattr(bili_client, "login_start", fake_start)
    monkeypatch.setattr(bili_client, "login_poll", fake_poll)
    assert client.post("/api/bili/login/start").json()["status"] == "qrcode_ready"
    assert client.get("/api/bili/login/poll").json()["status"] == "done"


def test_config_enable_requires_install(client, monkeypatch):
    monkeypatch.setattr(bili_api, "_aid", lambda request: "acc_default")
    monkeypatch.setattr(bili_setup, "status", lambda: {"installed": False})
    monkeypatch.setattr(bili_api.config, "load", lambda: {"bili": {"enabled": False}})
    res = client.post("/api/bili/config", json={"enabled": True})
    assert res.status_code == 400
    assert "未安装" in res.json()["detail"]


def test_setup_log_endpoint(client, monkeypatch):
    async def fake_log(offset: int, max_bytes: int = 200_000):
        return {
            "ok": True,
            "offset": offset + 7,
            "text": "install\n",
            "phase": "deps",
            "error": "",
            "done": False,
        }

    monkeypatch.setattr(bili_setup, "log_since", fake_log)
    res = client.get("/api/bili/setup/log?offset=3")
    assert res.status_code == 200
    assert res.json() == {
        "ok": True,
        "offset": 10,
        "text": "install\n",
        "phase": "deps",
        "error": "",
        "done": False,
    }


def test_config_saves_and_writes_mcp(client, monkeypatch):
    monkeypatch.setattr(bili_api, "_aid", lambda request: "acc_default")
    monkeypatch.setattr(bili_setup, "status", lambda: {"installed": True})
    monkeypatch.setattr(bili_api.config, "load", lambda: {"bili": {"enabled": False}})
    saved = {}

    def fake_save(patch):
        saved.update(patch)
        return patch

    monkeypatch.setattr(bili_api.config, "save", fake_save)
    monkeypatch.setattr(bili_setup, "write_mcp_config", lambda py: saved.update({"mcp": str(py)}))
    monkeypatch.setattr(bili_setup, "venv_python", lambda: "python.exe")

    async def fake_reload():
        return None

    monkeypatch.setattr(bili_api.capability_registry, "reload", fake_reload)
    monkeypatch.setattr(bili_api, "_patrol_cfg", lambda aid: {"enabled": False})
    res = client.post("/api/bili/config", json={"enabled": True, "patrol": {"enabled": True}})
    assert res.status_code == 200
    assert saved["bili"]["enabled"] is True
    assert saved["mcp"] == "python.exe"


def _route_paths(routes):
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            yield path
        sub = getattr(route, "routes", None)
        if sub:
            yield from _route_paths(sub)
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _route_paths(getattr(original, "routes", []) or [])
        router = getattr(route, "router", None)
        if router is not None:
            yield from _route_paths(getattr(router, "routes", []) or [])


def test_patrol_routes_registered():
    from app import main

    paths = set(_route_paths(main.app.routes))
    assert "/api/bili/status" in paths
    assert "/api/bili/patrol/run" in paths
    assert "/api/bili/patrol/runs" in paths
    assert "/api/bili/patrol/active" in paths
    assert "/api/bili/patrol/runs/{run_id}/events" in paths


def test_patrol_active_and_events(client, monkeypatch):
    class FakeScheduler:
        def active_snapshot(self, account_id, after):
            return {
                "run_id": 3,
                "items": [
                    {"seq": 1, "time": "12:00:00", "kind": "think", "text": "思考：x"}
                ],
                "done": False,
                "error": "",
                "started_at": 1.0,
            }

        def trace_snapshot(self, run_id, after):
            return None

    monkeypatch.setattr(client.app.state, "bili_patrol", FakeScheduler(), raising=False)
    monkeypatch.setattr(bili_api, "_aid", lambda request: "acc_default")
    res = client.get("/api/bili/patrol/active?after=0")
    assert res.status_code == 200
    assert res.json()["run_id"] == 3
    assert res.json()["items"][0]["text"].startswith("思考")


def test_patrol_events_falls_back_to_log_file(client, monkeypatch, tmp_path):
    class FakeScheduler:
        def trace_snapshot(self, run_id, after):
            return None

    monkeypatch.setattr(client.app.state, "bili_patrol", FakeScheduler(), raising=False)
    monkeypatch.setattr(bili_setup, "PATROL_DIR", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "9.log").write_text("[12:00:00] 巡逻完成\n", "utf-8")
    res = client.get("/api/bili/patrol/runs/9/events")
    assert res.status_code == 200
    assert "巡逻完成" in res.json()["items"][0]["text"]
    assert client.get("/api/bili/patrol/runs/10/events").status_code == 404


def test_patrol_run_uses_app_state(client, monkeypatch):
    class FakeScheduler:
        async def run_now(self, account_id, trigger):
            return {"ok": True, "account_id": account_id, "trigger": trigger}

    monkeypatch.setattr(client.app.state, "bili_patrol", FakeScheduler(), raising=False)
    monkeypatch.setattr(bili_api, "_aid", lambda request: "acc_default")
    res = client.post("/api/bili/patrol/run")
    assert res.status_code == 200
    assert res.json()["trigger"] == "manual"
