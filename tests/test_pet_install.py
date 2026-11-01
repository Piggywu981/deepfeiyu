"""一键准备（依赖 + 素材）：状态机、增量日志、并发与命令白名单。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.pet import install


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "STATE_PATH", tmp_path / "install.json")
    monkeypatch.setattr(install, "LOG_PATH", tmp_path / "install.log")
    monkeypatch.setattr(install, "bundle_ready", lambda: (tmp_path / "assembly.json").is_file())
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("PIP_INDEX", raising=False)

    calls: dict[str, list] = {"run": [], "assemble": 0, "restart": 0, "save": []}
    state = {"pyside": False}    # 模拟真实变化：pip 跑完后导入就可用

    def fake_run(cmd: list[str]) -> int:
        calls["run"].append(list(cmd))
        with install.LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("$ " + " ".join(map(str, cmd)) + "\nok\n[exit 0]\n")
        state["pyside"] = True
        return 0

    def fake_assemble() -> None:
        calls["assemble"] += 1
        (tmp_path / "assembly.json").write_text("{}", encoding="utf-8")

    async def fake_restart() -> None:
        calls["restart"] += 1

    def fake_save(patch):
        calls["save"].append(dict(patch))
        return patch

    monkeypatch.setattr(install, "_run", fake_run)
    monkeypatch.setattr(install, "_assemble", fake_assemble)
    monkeypatch.setattr(install.supervisor, "restart", fake_restart)
    monkeypatch.setattr(install.settings, "save", fake_save)
    monkeypatch.setattr(install, "_pyside_available", lambda: state["pyside"])
    calls["state"] = state
    return calls


def test_deps_then_assets_then_enable_and_restart(sandbox):
    asyncio.run(install._pipeline())
    assert sandbox["run"] == [[
        install.sys.executable, "-m", "pip", "install", install.PIP_PACKAGE,
    ]]
    assert sandbox["assemble"] == 1
    assert sandbox["save"] == [{"enabled": True}]
    assert sandbox["restart"] == 1
    assert install.status()["phase"] == "ok"


def test_skips_pip_when_pyside6_already_present(sandbox, monkeypatch):
    monkeypatch.setattr(install, "_pyside_available", lambda: True)
    asyncio.run(install._pipeline())
    assert sandbox["run"] == []                      # 已装就不再下 130MB
    assert install.status()["phase"] == "ok"


def test_skips_assembly_when_bundle_ready(sandbox, tmp_path):
    (tmp_path / "assembly.json").write_text("{}", encoding="utf-8")
    asyncio.run(install._pipeline())
    assert sandbox["assemble"] == 0
    assert install.status()["phase"] == "ok"


def test_pip_failure_stops_before_assets(sandbox, monkeypatch):
    monkeypatch.setattr(install, "_run", lambda cmd: 1)
    asyncio.run(install._pipeline())
    st = install.status()
    assert st["phase"] == "error" and st["error"]
    assert sandbox["assemble"] == 0 and sandbox["restart"] == 0
    assert sandbox["save"] == []                      # 失败不许悄悄启用宠物


def test_assembly_failure_is_reported_not_swallowed(sandbox, monkeypatch):
    def boom() -> None:
        raise FileNotFoundError("找不到上游素材。二选一：")

    monkeypatch.setattr(install, "_assemble", boom)
    asyncio.run(install._pipeline())
    st = install.status()
    assert st["phase"] == "error" and "上游素材" in st["error"]
    assert sandbox["save"] == []


def test_command_is_fixed_and_ignores_any_input(sandbox):
    asyncio.run(install._pipeline())
    cmd = sandbox["run"][0]
    assert cmd[:3] == [install.sys.executable, "-m", "pip"]
    assert cmd[3:] == ["install", install.PIP_PACKAGE]
    assert "PySide6-Essentials" == install.PIP_PACKAGE   # 包名写死在代码里，不接受前端传入


def test_mirror_env_forces_isolated_index(sandbox, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    asyncio.run(install._pipeline())
    cmd = sandbox["run"][0]
    assert cmd[3] == "--isolated"
    assert cmd[4:7] == ["install", "-i", "https://mirror.example/simple"]
    assert cmd[7:] == [install.PIP_PACKAGE]


def test_second_start_rejected_while_running(sandbox, monkeypatch):
    async def scenario():
        blocker = asyncio.Event()

        async def slow_pipeline():
            await blocker.wait()

        monkeypatch.setattr(install, "_pipeline", slow_pipeline)
        first = await install.start_install()
        second = await install.start_install()
        blocker.set()
        await asyncio.sleep(0)
        return first, second

    first, second = asyncio.run(scenario())
    assert first["ok"] is True and first["started"] is True
    assert second["ok"] is False and "正在" in second["error"]


def test_log_since_returns_only_new_bytes(sandbox):
    install.LOG_PATH.write_text("第一行\n第二行\n", encoding="utf-8")
    head = asyncio.run(install.log_since(0))
    assert "第一行" in head["text"] and head["offset"] == install.LOG_PATH.stat().st_size
    install.LOG_PATH.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
    tail = asyncio.run(install.log_since(head["offset"]))
    assert "第三行" in tail["text"] and "第一行" not in tail["text"]
    assert tail["done"] is False or tail["phase"] in {"idle", "ok", "error"}


def test_status_is_cheap_and_reports_readiness(sandbox, tmp_path):
    install._write_state("idle")
    st = install.status()
    assert st["phase"] == "idle"
    assert st["depsReady"] is False            # 用假的 _pyside_available
    assert st["assetsReady"] is False          # 没有 assembly.json
    (tmp_path / "assembly.json").write_text("{}", encoding="utf-8")
    assert install.status()["assetsReady"] is True


def test_state_file_shape(sandbox):
    install._write_state("deps", "", "安装 PySide6-Essentials")
    data = json.loads(install.STATE_PATH.read_text(encoding="utf-8"))
    assert set(data) >= {"phase", "error", "step", "updatedAt"}
