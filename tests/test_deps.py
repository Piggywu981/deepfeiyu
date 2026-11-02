"""可选功能依赖（/api/deps）：固定命令、状态探测、增量日志与端点契约。

不真的跑 pip / 网络：_run、模块探测与 pet/bili 的既有流程全部打桩。
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app import config
from app.bili import setup as bili_setup
from app.deps import api as deps_api
from app.deps import features, installer
from app.pet import install as pet_install


def _client(*, host: str | None = None) -> TestClient:
    if host:
        return TestClient(main.app, client=(host, 50000))
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _reset_installer_globals(monkeypatch):
    """安装状态是模块级全局：每个用例从干净状态开始，避免上个用例的任务漏到下一个。"""
    monkeypatch.setattr(installer, "_task", None)
    monkeypatch.setattr(installer, "_running", False)
    monkeypatch.setattr(installer, "_current", None)
    yield


@pytest.fixture()
def deps_paths(tmp_path, monkeypatch):
    """状态与日志改到 tmp_path，避免污染真实 data/；同时清掉镜像变量。"""
    monkeypatch.setattr(installer, "DEPS_DIR", tmp_path)
    monkeypatch.setattr(installer, "STATE_PATH", tmp_path / "install.json")
    monkeypatch.setattr(installer, "LOG_PATH", tmp_path / "install.log")
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("PIP_INDEX", raising=False)
    return tmp_path


@pytest.fixture()
def delegates(monkeypatch):
    """pet / bili 的既有流程全程打桩，测试不碰真实 data/pet、data/bili。"""
    pet = {"phase": "idle", "depsReady": False, "assetsReady": False, "step": "", "error": ""}
    bili = {"phase": "idle", "installed": False, "error": ""}
    monkeypatch.setattr(pet_install, "status", lambda: dict(pet))
    monkeypatch.setattr(bili_setup, "status", lambda: dict(bili))
    return {"pet": pet, "bili": bili}


@pytest.fixture()
def fake_install(monkeypatch):
    """替换 pip 子进程与模块探测：记录命令，按用例预设的退出码/缺失模块返回。"""
    calls = {"run": [], "invalidate": 0}
    state = {
        "code": 0,
        # 默认所有已知模块都可用；用例删掉某项即可模拟"装完仍缺模块"
        "available": {module for feature in features.FEATURES for module, _ in feature.modules},
    }

    def fake_run(cmd: list[str]) -> int:
        calls["run"].append(list(cmd))
        installer.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with installer.LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("$ " + " ".join(map(str, cmd)) + "\n")
            fh.write("Successfully installed fake-pkg\n[exit 0]\n")
        return state["code"]

    def fake_invalidate() -> None:
        calls["invalidate"] += 1

    monkeypatch.setattr(installer, "_run", fake_run)
    monkeypatch.setattr(installer, "invalidate_caches", fake_invalidate)
    monkeypatch.setattr(features, "module_available", lambda name: name in state["available"])
    return {"calls": calls, "state": state}


# ---------- 注册表与固定命令 ----------

def test_feature_registry_has_six_entries():
    assert [feature.id for feature in features.FEATURES] == [
        "core", "weixin4", "moments", "pet", "wcferry", "bili",
    ]
    labels = {feature.id: feature.label for feature in features.FEATURES}
    assert labels["core"] == "核心依赖"
    assert labels["weixin4"] == "微信 4.x 通道"
    assert labels["wcferry"] == "WCFerry（微信 3.9.x）"
    kinds = {feature.id: feature.kind for feature in features.FEATURES}
    assert kinds["pet"] == "delegated" and kinds["bili"] == "delegated"
    assert kinds["core"] == "pip" and kinds["wcferry"] == "pip"
    assert features.get("nope") is None
    assert features.get("") is None and features.get(None) is None


def test_install_command_is_fixed_per_feature(deps_paths):
    exe = installer.sys.executable
    assert installer.install_command("core") == [
        exe, "-m", "pip", "install", "-r", str(config.ROOT / "requirements.txt"),
    ]
    assert installer.install_command("weixin4") == [
        exe, "-m", "pip", "install", "-r", str(config.ROOT / "requirements-weixin4.txt"),
    ]
    assert installer.install_command("moments") == [
        exe, "-m", "pip", "install", "-r", str(config.ROOT / "requirements-moments.txt"),
    ]
    # wcferry 没有 requirements 文件：固定包名、不带版本号
    assert installer.install_command("wcferry") == [
        exe, "-m", "pip", "install", "wcferry",
    ]


def test_mirror_env_adds_isolated_and_index(deps_paths, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    cmd = installer.install_command("weixin4")
    assert cmd[:5] == [installer.sys.executable, "-m", "pip", "--isolated", "install"]
    assert cmd[5:7] == ["-i", "https://mirror.example/simple"]
    assert cmd[7:] == ["-r", str(config.ROOT / "requirements-weixin4.txt")]

    monkeypatch.delenv("PIP_INDEX_URL")
    monkeypatch.setenv("PIP_INDEX", "https://p2.example/simple")
    wc = installer.install_command("wcferry")
    assert wc[5:7] == ["-i", "https://p2.example/simple"]
    assert wc[7:] == ["wcferry"]


def test_forged_feature_input_never_becomes_a_command(deps_paths):
    """前端只能传 id：伪造的包名/参数不是命令的一部分，直接拒绝。"""
    for forged in (
        "--extra-index-url=https://evil.example",
        "wcferry; rm -rf /",
        "fastapi",
        "requirements.txt",
        "wcferry==39.3.5",
        "PET",
        "",
    ):
        with pytest.raises(KeyError):
            installer.install_command(forged)
    # 委托型不给 pip 命令，避免绕过既有一键流程
    with pytest.raises(ValueError):
        installer.install_command("pet")


def test_platform_gate_marks_windows_only_features(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    for feature_id in ("weixin4", "moments", "pet", "wcferry"):
        assert features.supported(features.get(feature_id)) is True
    monkeypatch.setattr(os, "name", "posix")
    for feature_id in ("weixin4", "moments", "pet", "wcferry"):
        assert features.supported(features.get(feature_id)) is False
    for feature_id in ("core", "bili"):
        assert features.supported(features.get(feature_id)) is True


def test_probe_reports_missing_package_labels(monkeypatch):
    available = {"fastapi", "uvicorn", "httpx", "pydantic", "multipart", "PIL"}
    monkeypatch.setattr(features, "module_available", lambda name: name in available)
    monkeypatch.setattr(pet_install, "status",
                        lambda: {"phase": "idle", "depsReady": False, "assetsReady": False})
    monkeypatch.setattr(bili_setup, "status", lambda: {"phase": "idle", "installed": False})

    core = features.describe(features.get("core"))
    assert core["installed"] is True and core["partial"] is False
    assert core["present"] == 6 and core["total"] == 6 and core["missing"] == []
    assert "重启控制台" in core["note"]

    moments = features.describe(features.get("moments"))
    assert moments["installed"] is False and moments["partial"] is False
    assert moments["present"] == 0 and moments["total"] == 6
    # 报给用户的是包名标签，不是导入名
    assert set(moments["missing"]) == {
        "pywin32", "numpy", "opencv-python", "pyautogui", "winsdk", "psutil",
    }

    available.update({"win32api", "numpy", "psutil"})
    moments = features.describe(features.get("moments"))
    assert moments["partial"] is True and moments["present"] == 3
    assert set(moments["missing"]) == {"opencv-python", "pyautogui", "winsdk"}

    wcferry = features.describe(features.get("wcferry"))
    assert wcferry["total"] == 1 and wcferry["missing"] == ["wcferry"]
    available.add("wcferry")
    assert features.describe(features.get("wcferry"))["installed"] is True


def test_delegated_describe_uses_existing_status(delegates):
    delegates["pet"].update({"depsReady": True, "assetsReady": False})
    pet = features.describe(features.get("pet"))
    assert pet["installed"] is True and pet["present"] == 1 and pet["total"] == 1
    assert pet["assetsReady"] is False
    assert "桌宠" in pet["note"]

    delegates["pet"].update({"assetsReady": True})
    assert features.describe(features.get("pet"))["note"] == ""

    bili = features.describe(features.get("bili"))
    assert bili["installed"] is False and bili["total"] == 0 and bili["missing"] == []
    assert "data/bili/setup.log" in bili["note"]
    delegates["bili"]["installed"] = True
    assert features.describe(features.get("bili"))["note"] == ""


# ---------- pip 状态机与并发 ----------

def test_fake_pip_marks_feature_ok(deps_paths, delegates, fake_install):
    asyncio.run(installer._pipeline("weixin4"))
    state = installer.phase_of(features.get("weixin4"))
    assert state == {"phase": "ok", "step": "就绪", "error": ""}
    assert fake_install["calls"]["run"] == [installer.install_command("weixin4")]
    assert fake_install["calls"]["invalidate"] == 1   # 装完先失效缓存再复检
    data = json.loads(installer.STATE_PATH.read_text("utf-8"))
    assert data["feature"] == "weixin4" and data["phase"] == "ok"
    assert {"feature", "phase", "step", "error", "updatedAt"} <= set(data)


def test_install_command_installing_state_step(deps_paths, delegates, fake_install, monkeypatch):
    seen: dict = {}

    def fake_run(cmd: list[str]) -> int:
        seen.update(json.loads(installer.STATE_PATH.read_text("utf-8")))
        return 0

    monkeypatch.setattr(installer, "_run", fake_run)
    asyncio.run(installer._pipeline("moments"))
    assert seen["feature"] == "moments" and seen["phase"] == "installing"
    assert seen["step"] == "pip install requirements-moments.txt"


def test_pip_failure_marks_error(deps_paths, delegates, fake_install):
    fake_install["state"]["code"] = 1
    asyncio.run(installer._pipeline("core"))
    state = installer.phase_of(features.get("core"))
    assert state["phase"] == "error" and state["error"]
    assert "退出码 1" in state["error"]
    assert fake_install["calls"]["invalidate"] == 0


def test_missing_module_after_pip_marks_error(deps_paths, delegates, fake_install):
    fake_install["state"]["available"].discard("uiautomation")
    asyncio.run(installer._pipeline("weixin4"))
    state = installer.phase_of(features.get("weixin4"))
    assert state["phase"] == "error"
    assert "uiautomation" in state["error"]
    assert fake_install["calls"]["invalidate"] == 1


def test_second_start_rejected_while_pip_running(deps_paths, delegates, monkeypatch):
    async def scenario():
        blocker = asyncio.Event()

        async def slow_pipeline(feature_id: str) -> None:
            await blocker.wait()

        monkeypatch.setattr(installer, "_pipeline", slow_pipeline)
        first = await installer.start_install("weixin4")
        second = await installer.start_install("weixin4")
        third = await installer.start_install("core")
        blocker.set()
        await installer.wait_idle()
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first["ok"] is True and first["started"] is True
    assert first["feature"] == "weixin4" and isinstance(first["log_offset"], int)
    for rejected in (second, third):
        assert rejected["ok"] is False and rejected["started"] is False
        assert "正在安装" in rejected["error"]
        assert rejected["log_offset"] == 0


def test_pip_start_rejected_when_delegated_busy(deps_paths, delegates, fake_install):
    delegates["pet"]["phase"] = "assets"
    out = asyncio.run(installer.start_install("weixin4"))
    assert out["ok"] is False and out["started"] is False
    assert out["feature"] == "weixin4" and "正在安装" in out["error"]
    assert fake_install["calls"]["run"] == []      # 被拒时不执行任何命令


def test_installing_feature_reports_pip_and_delegated(deps_paths, delegates, monkeypatch):
    monkeypatch.setattr(installer, "_running", True)
    monkeypatch.setattr(installer, "_current", "weixin4")
    assert installer.installing_feature() == "weixin4"
    monkeypatch.setattr(installer, "_running", False)
    monkeypatch.setattr(installer, "_current", None)
    assert installer.installing_feature() is None
    delegates["bili"]["phase"] = "venv"
    assert installer.installing_feature() == "bili"
    assert installer.busy_feature() == "bili"


def test_delegated_phase_mapping(deps_paths, delegates):
    pet = features.get("pet")
    bili = features.get("bili")
    delegates["pet"].update({"phase": "deps", "depsReady": False})
    assert installer.phase_of(pet) == {"phase": "installing", "step": "安装 PySide6 依赖", "error": ""}
    delegates["pet"].update({"phase": "ok", "error": ""})
    assert installer.phase_of(pet) == {"phase": "ok", "step": "就绪", "error": ""}
    delegates["pet"].update({"phase": "error", "error": "素材缺失", "step": "装配素材"})
    failed = installer.phase_of(pet)
    assert failed["phase"] == "error" and failed["error"] == "素材缺失"
    assert failed["step"] == "装配素材"

    delegates["bili"].update({"phase": "copying"})
    assert installer.phase_of(bili) == {"phase": "installing", "step": "复制运行文件", "error": ""}
    delegates["bili"].update({"phase": "mcp"})
    assert installer.phase_of(bili)["step"] == "写入 MCP 配置"
    delegates["bili"].update({"phase": "idle"})
    assert installer.phase_of(bili)["phase"] == "idle"


def test_stale_state_from_other_feature_is_ignored(deps_paths, delegates):
    installer._write_state("moments", "error", step="x", error="失败了")
    assert installer.phase_of(features.get("moments"))["phase"] == "error"
    assert installer.phase_of(features.get("core"))["phase"] == "idle"


def test_delegated_start_passes_through(deps_paths, delegates, monkeypatch):
    async def pet_start():
        return {"ok": True, "started": True, "log_offset": 21, "error": "", "phase": "deps"}

    monkeypatch.setattr(pet_install, "start_install", pet_start)
    out = asyncio.run(installer.start_install("pet"))
    assert out == {"ok": True, "started": True, "feature": "pet", "log_offset": 21, "error": ""}

    async def bili_start():
        return {"ok": True, "started": True, "log_offset": 7, "error": ""}

    monkeypatch.setattr(bili_setup, "start_setup", bili_start)
    out = asyncio.run(installer.start_install("bili"))
    assert out["ok"] is True and out["log_offset"] == 7 and out["feature"] == "bili"


def test_delegated_start_rejected_when_other_feature_busy(deps_paths, delegates, monkeypatch):
    async def bili_start():
        raise AssertionError("不应启动另一个安装")

    monkeypatch.setattr(bili_setup, "start_setup", bili_start)
    delegates["pet"]["phase"] = "restart"
    out = asyncio.run(installer.start_install("bili"))
    assert out["ok"] is False and "正在安装" in out["error"] and out["log_offset"] == 0


# ---------- 增量日志 ----------

def test_stale_installing_state_becomes_retryable_error(deps_paths, delegates):
    """进程退出会把 installing 永久留在 state 文件里；必须归一成可重装的失败态，
    否则前端看到 phase=installing 会永远禁用一键安装按钮。"""
    installer._write_state("weixin4", "installing", step="pip install requirements-weixin4.txt")
    state = installer.phase_of(features.get("weixin4"))
    assert state["phase"] == "error"
    assert "中断" in state["error"]
    assert state["step"] == "pip install requirements-weixin4.txt"   # 保留现场便于排查


def test_real_installing_state_is_kept(deps_paths, delegates, monkeypatch):
    installer._write_state("weixin4", "installing", step="pip install requirements-weixin4.txt")
    monkeypatch.setattr(installer, "_running", True)
    monkeypatch.setattr(installer, "_current", "weixin4")
    state = installer.phase_of(features.get("weixin4"))
    assert state == {
        "phase": "installing",
        "step": "pip install requirements-weixin4.txt",
        "error": "",
    }
    # 别的功能在跑时，这条安装中的记录同样算陈旧
    monkeypatch.setattr(installer, "_current", "core")
    assert installer.phase_of(features.get("weixin4"))["phase"] == "error"


def test_log_since_incremental_and_reset(deps_paths, delegates, monkeypatch):
    installer.LOG_PATH.write_text("第一行\n第二行\n", encoding="utf-8")
    installer._write_state("weixin4", "installing", step="pip install requirements-weixin4.txt")
    monkeypatch.setattr(installer, "_running", True)
    monkeypatch.setattr(installer, "_current", "weixin4")
    head = asyncio.run(installer.log_since("weixin4", 0))
    assert "第一行" in head["text"] and head["offset"] == installer.LOG_PATH.stat().st_size
    assert head["phase"] == "installing" and head["done"] is False
    assert head["step"].startswith("pip install") and head["feature"] == "weixin4"

    with installer.LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write("第三行\n")
    tail = asyncio.run(installer.log_since("weixin4", head["offset"]))
    assert "第三行" in tail["text"] and "第一行" not in tail["text"]
    assert tail["reset"] is False

    reset = asyncio.run(installer.log_since("weixin4", -5))
    assert reset["reset"] is True and "第三行" in reset["text"]
    past = asyncio.run(installer.log_since("weixin4", 10**9))
    assert past["reset"] is True

    monkeypatch.setattr(installer, "_running", False)
    monkeypatch.setattr(installer, "_current", None)
    installer._write_state("weixin4", "ok", step="就绪")
    done = asyncio.run(installer.log_since("weixin4", 10**9))
    assert done["phase"] == "ok" and done["done"] is True


def test_log_defaults_to_last_feature(deps_paths, delegates):
    installer._write_state("moments", "error", step="pip install requirements-moments.txt",
                           error="网络超时")
    installer.LOG_PATH.write_text("boom\n", encoding="utf-8")
    out = asyncio.run(installer.log_since(None, 0))
    assert out["feature"] == "moments" and out["phase"] == "error"
    assert out["error"] == "网络超时" and out["done"] is True


def test_delegated_log_is_passed_through(deps_paths, delegates, monkeypatch):
    delegates["pet"]["phase"] = "assets"

    async def fake_log(offset: int = 0, max_bytes: int = installer.MAX_LOG_BYTES):
        return {"ok": True, "offset": offset + 3, "size": 3, "reset": False, "text": "装配中\n"}

    monkeypatch.setattr(pet_install, "log_since", fake_log)
    out = asyncio.run(installer.log_since("pet", 0))
    assert out["feature"] == "pet" and out["phase"] == "installing"
    assert out["step"] == "装配素材" and out["done"] is False
    assert "装配中" in out["text"] and out["offset"] == 3
    assert "来源" in out["text"]          # 开头补一行来源说明，方便用户知道去哪个文件看
    assert "data/pet/install.log" in out["text"]


# ---------- HTTP 端点 ----------

def test_state_endpoint_shape(deps_paths, delegates, fake_install):
    fake_install["state"]["available"].discard("uiautomation")
    res = _client().get("/api/deps/state")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["installing"] is None
    assert [f["id"] for f in body["features"]] == [
        "core", "weixin4", "moments", "pet", "wcferry", "bili",
    ]
    required = {
        "id", "label", "desc", "kind", "platform", "supported", "installed",
        "partial", "missing", "present", "total", "phase", "step", "error", "note",
    }
    assert all(required <= set(feature) for feature in body["features"])
    core = body["features"][0]
    assert core["installed"] is True and core["phase"] == "idle"
    assert core["platform"] == "any" and core["supported"] is True
    wx = body["features"][1]
    assert wx["partial"] is True and "uiautomation" in wx["missing"]
    pet = body["features"][3]
    assert pet["assetsReady"] is False and "桌宠" in pet["note"]
    bili = body["features"][5]
    assert bili["installed"] is False and "data/bili/setup.log" in bili["note"]


def test_state_endpoint_scopes_install_phase_to_feature(deps_paths, delegates):
    installer._write_state("moments", "error", step="pip install requirements-moments.txt",
                           error="网络超时")
    body = _client().get("/api/deps/state").json()
    by_id = {feature["id"]: feature for feature in body["features"]}
    assert by_id["moments"]["phase"] == "error" and by_id["moments"]["error"] == "网络超时"
    assert by_id["core"]["phase"] == "idle" and by_id["core"]["error"] == ""
    assert body["installing"] is None


def test_install_endpoint_unknown_feature_is_400(deps_paths, delegates):
    res = _client().post("/api/deps/install", json={"feature": "evil; pip install x"})
    assert res.status_code == 400
    assert "未知功能" in res.json()["detail"]


def test_install_endpoint_rejects_other_os(deps_paths, delegates, monkeypatch):
    called: list[str] = []

    async def fake_start(feature_id: str):
        called.append(feature_id)
        return {"ok": True, "started": True, "feature": feature_id, "log_offset": 0, "error": ""}

    monkeypatch.setattr(installer, "start_install", fake_start)
    monkeypatch.setattr(os, "name", "posix")
    res = _client().post("/api/deps/install", json={"feature": "moments"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False and body["started"] is False
    assert body["error"] == "该功能仅支持 Windows" and body["log_offset"] == 0
    assert called == []


def test_install_endpoint_starts_and_ignores_extra_fields(deps_paths, delegates, monkeypatch):
    seen: list[str] = []

    async def fake_start(feature_id: str):
        seen.append(feature_id)
        return {"ok": True, "started": True, "feature": feature_id, "log_offset": 42, "error": ""}

    monkeypatch.setattr(installer, "start_install", fake_start)
    res = _client().post(
        "/api/deps/install",
        json={"feature": "weixin4", "package": "evil-pkg", "pip_args": ["-i", "https://evil"]},
    )
    assert res.status_code == 200
    assert seen == ["weixin4"]      # 多余字段被忽略，不会变成命令参数
    body = res.json()
    assert body == {"ok": True, "started": True, "feature": "weixin4",
                    "log_offset": 42, "error": ""}


def test_install_endpoint_busy_is_http_200(deps_paths, delegates):
    delegates["pet"]["phase"] = "assets"       # 桌宠既有一键流程还在跑
    res = _client().post("/api/deps/install", json={"feature": "weixin4"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False and body["started"] is False
    assert "正在安装" in body["error"] and body["log_offset"] == 0


def test_install_endpoint_delegated_passthrough(deps_paths, delegates, monkeypatch):
    async def fake_start():
        return {"ok": True, "started": True, "log_offset": 9, "error": ""}

    monkeypatch.setattr(pet_install, "start_install", fake_start)
    body = _client().post("/api/deps/install", json={"feature": "pet"}).json()
    assert body["feature"] == "pet" and body["started"] is True and body["log_offset"] == 9


def test_log_endpoint_reads_pip_log_and_rejects_unknown(deps_paths, delegates, monkeypatch):
    installer.LOG_PATH.write_text("安装中\n", encoding="utf-8")
    installer._write_state("weixin4", "installing", step="pip install requirements-weixin4.txt")
    # 模拟进程内真的在跑：否则陈旧 installing 会被归一成 error（见上面的用例）
    monkeypatch.setattr(installer, "_running", True)
    monkeypatch.setattr(installer, "_current", "weixin4")
    res = _client().get("/api/deps/install/log?feature=weixin4&offset=0")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["feature"] == "weixin4"
    assert body["phase"] == "installing" and body["step"].startswith("pip install")
    assert "安装中" in body["text"] and body["done"] is False

    bad = _client().get("/api/deps/install/log?feature=whatever&offset=0")
    assert bad.status_code == 400
    assert "未知功能" in bad.json()["detail"]

    default = _client().get("/api/deps/install/log?offset=0")
    assert default.json()["feature"] == "weixin4"   # 未指定时回落最近一次


def test_log_endpoint_keeps_negative_offset_as_reset(deps_paths, delegates):
    installer.LOG_PATH.write_text("旧日志\n", encoding="utf-8")
    body = _client().get("/api/deps/install/log?feature=core&offset=-1").json()
    assert body["reset"] is True and "旧日志" in body["text"]


def test_log_endpoint_delegated_returns_existing_log(deps_paths, delegates, monkeypatch):
    delegates["pet"]["phase"] = "assets"

    async def fake_log(offset: int = 0, max_bytes: int = installer.MAX_LOG_BYTES):
        return {"ok": True, "offset": 9, "size": 9, "reset": False, "text": "装配素材中\n"}

    monkeypatch.setattr(pet_install, "log_since", fake_log)
    body = _client().get("/api/deps/install/log?feature=pet&offset=0").json()
    assert body["phase"] == "installing" and body["step"] == "装配素材"
    assert "装配素材中" in body["text"] and body["offset"] == 9
    assert "data/pet/install.log" in body["text"]


def test_install_guard_rejects_remote_host():
    """本机闸门：它会真的执行 pip，不能给远端调。"""
    class _Remote:
        client = type("C", (), {"host": "8.8.8.8"})()

    with pytest.raises(HTTPException) as caught:
        deps_api._require_local(_Remote())
    assert caught.value.status_code == 403
    assert "本机" in caught.value.detail


def test_remote_request_is_403_end_to_end(deps_paths, delegates):
    res = _client(host="8.8.8.8").post("/api/deps/install", json={"feature": "core"})
    assert res.status_code == 403
