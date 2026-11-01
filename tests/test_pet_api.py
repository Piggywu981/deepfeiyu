from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.pet import api as pet_api
from app.pet import settings as pet_settings
from app.pet import assets as pet_assets


def _client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture()
def fake_supervisor(monkeypatch):
    calls: list[dict] = []

    class _Fake:
        running = False

        def status(self):
            return {"enabled": False, "running": False, "disabledReason": "",
                    "restarts": 0, "state": "IDLE", "bundleCommit": "x", "helperPath": "y"}

        async def start(self):
            calls.append({"action": "start"})

        async def stop(self):
            calls.append({"action": "stop"})

        async def apply_config(self):
            calls.append({"action": "apply_config"})

        async def preview(self, state, text=""):
            calls.append({"action": "preview", "state": state, "text": text})

    fake = _Fake()
    monkeypatch.setattr(pet_api, "supervisor", fake)
    return fake, calls


def test_state_endpoint(monkeypatch, fake_supervisor):
    # 桩掉 section：否则会读真实 data/config.json（一键准备会把 enabled 写成 true）
    monkeypatch.setattr(pet_api.settings, "section", lambda *a, **kw: {"enabled": False})
    monkeypatch.setattr(pet_api.install, "status",
                        lambda: {"phase": "idle", "depsReady": False, "assetsReady": True})
    res = _client().get("/api/pet/state")
    assert res.status_code == 200
    body = res.json()
    assert body["state"]["running"] is False
    assert body["config"]["enabled"] is False
    assert body["install"] == {"phase": "idle", "depsReady": False, "assetsReady": True}


def test_config_roundtrip_clamps_scale(monkeypatch, fake_supervisor):
    """只桩最底层 config.save，让真实 normalize 跑完——
    桩掉 settings.save 的话，「夹到上界才落库」这句断言就是空的。"""
    persisted: dict = {}

    def fake_config_save(payload):
        persisted.update(payload)
        return {"pet": {**pet_settings.DEFAULTS, **payload["pet"]}}

    fake, calls = fake_supervisor
    monkeypatch.setattr(pet_api.config, "save", fake_config_save)
    res = _client().post("/api/pet/config", json={"enabled": True, "scale": 99})
    assert res.status_code == 200
    assert persisted["pet"] == {"enabled": True, "scale": 1.4}   # 上游 clamp 上界
    assert calls == [{"action": "start"}]                        # 开启动作真的起进程


def test_config_change_without_toggle_applies_hot(monkeypatch, fake_supervisor):
    fake, calls = fake_supervisor
    fake.running = True
    monkeypatch.setattr(pet_api.settings, "save", lambda patch: dict(patch))
    _client().post("/api/pet/config", json={"scale": 1.2})
    assert calls == [{"action": "apply_config"}]


def test_toggle_off_stops(monkeypatch, fake_supervisor):
    fake, calls = fake_supervisor
    fake.running = True
    monkeypatch.setattr(pet_api.settings, "save", lambda patch: dict(patch))
    _client().post("/api/pet/config", json={"enabled": False})
    assert calls == [{"action": "stop"}]


def test_preview_rejects_unknown_state(monkeypatch, fake_supervisor):
    fake, _ = fake_supervisor
    fake.running = True
    res = _client().post("/api/pet/test", json={"state": "BROWSING"})
    assert res.status_code == 400


def test_preview_rejects_when_not_running(fake_supervisor):
    res = _client().post("/api/pet/test", json={"state": "ERROR"})
    assert res.status_code == 409


def test_preview_forwards_state(fake_supervisor):
    fake, calls = fake_supervisor
    fake.running = True
    res = _client().post("/api/pet/test", json={"state": "ERROR", "text": "预览"})
    assert res.status_code == 200
    assert calls[-1] == {"action": "preview", "state": "ERROR", "text": "预览"}


def test_reload_assets_reports_reason(monkeypatch):
    monkeypatch.setattr(pet_api.assets, "verify_bundle",
                        lambda: (False, "[missing] 未装配"))
    res = _client().post("/api/pet/reload-assets")
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["reason"].startswith("[missing]")


def test_install_starts_and_reports_offset(monkeypatch):
    started = {"ok": True, "started": True, "log_offset": 120, "phase": "deps"}

    async def fake_start():
        return started

    monkeypatch.setattr(pet_api.install, "start_install", fake_start)
    res = _client().post("/api/pet/install")
    assert res.status_code == 200
    assert res.json()["started"] is True and res.json()["log_offset"] == 120


def test_install_status_shape(monkeypatch):
    monkeypatch.setattr(pet_api.install, "status",
                        lambda: {"phase": "assets", "error": "", "step": "装配",
                                 "depsReady": True, "assetsReady": False})
    res = _client().get("/api/pet/install/status")
    assert res.status_code == 200
    assert res.json()["phase"] == "assets"


def test_install_log_passes_offset_through(monkeypatch):
    seen: dict = {}

    async def fake_log(offset: int = 0):
        seen["offset"] = offset
        return {"ok": True, "offset": offset + 10, "text": "新的一行\n", "phase": "deps",
                "done": False, "error": ""}

    monkeypatch.setattr(pet_api.install, "log_since", fake_log)
    res = _client().get("/api/pet/install/log?offset=42")
    assert res.status_code == 200
    assert seen["offset"] == 42
    assert res.json()["offset"] == 52


def test_install_guard_rejects_remote_host():
    """本机闸门对安装端点同样生效——它本质是执行 pip，不能给远端调。"""
    from fastapi import HTTPException

    class _Remote:
        client = type("C", (), {"host": "8.8.8.8"})()

    with pytest.raises(HTTPException) as caught:
        pet_api._require_local(_Remote())
    assert caught.value.status_code == 403
