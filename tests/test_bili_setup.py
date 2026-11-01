"""B 站 MCP 安装骨架：复制运行文件、保留凭证、生成只读配置。"""
import json
from pathlib import Path

import pytest

from app.bili import setup


@pytest.fixture()
def bili_dirs(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    data = tmp_path / "data"
    vendor.mkdir()
    data.mkdir()
    for name in ("mcp_server.py", "requirements.txt", "README.md", "LICENSE"):
        (vendor / name).write_text(f"# {name}\n", "utf-8")
    monkeypatch.setattr(setup, "VENDOR_DIR", vendor)
    monkeypatch.setattr(setup, "BILI_DIR", data)
    monkeypatch.setattr(setup, "MCP_PATH", data / "mcp.json")
    monkeypatch.setattr(setup, "SETUP_PATH", data / "setup.json")
    monkeypatch.setattr(setup, "LOG_PATH", data / "setup.log")
    monkeypatch.setattr(setup, "CREDENTIAL_PATH", data / "bili_credential.json")
    monkeypatch.setattr(setup, "PATROL_DIR", data / "patrol")
    return data


def test_ensure_runtime_files_copies_and_preserves_credentials(bili_dirs):
    cred = bili_dirs / "bili_credential.json"
    cred.write_text('{"sessdata": "keep-me"}', "utf-8")
    out = setup.ensure_runtime_files()
    assert "mcp_server.py" in out["copied"]
    assert (bili_dirs / "mcp_server.py").read_text("utf-8") == "# mcp_server.py\n"
    assert cred.read_text("utf-8") == '{"sessdata": "keep-me"}'
    assert (bili_dirs / "patrol").is_dir()


def test_mcp_payload_is_readonly(bili_dirs):
    payload = setup.mcp_payload(Path("C:/bili/python.exe"))
    server = payload["mcpServers"]["bilibili"]
    tools = set(server["env"]["BILI_MCP_TOOLS"].split(","))
    assert {"bili_search", "bili_subtitle", "bili_comments", "bili_login"} <= tools
    for forbidden in (
        "bili_reply", "bili_send_message", "bili_send_dynamic",
        "bili_upload_video", "bili_upload_video_multi", "bili_send_opus",
        "bili_triple", "bili_dm_recent", "bili_dm_sessions",
    ):
        assert forbidden not in tools
    assert server["auto_reply"] is False
    assert server["cwd"] == str(bili_dirs)
    assert server["command"] == str(Path("C:/bili/python.exe"))


def test_status_reads_setup_file(bili_dirs):
    (bili_dirs / "setup.json").write_text(
        json.dumps({"phase": "ok", "error": "", "updated_at": 123.0}),
        "utf-8",
    )
    (bili_dirs / "mcp_server.py").write_text("# x\n", "utf-8")
    st = setup.status()
    assert st["phase"] == "ok"
    assert st["updated_at"] == 123.0
    assert st["mcp_config"] is False


def test_credential_state_reports_empty_and_valid(bili_dirs):
    assert setup.credential_state()["exists"] is False
    cred = bili_dirs / "bili_credential.json"
    cred.write_text('{"sessdata": "", "dedeuserid": ""}', "utf-8")
    st = setup.credential_state()
    assert st["exists"] is True
    assert st["has_sessdata"] is False
    assert st["has_dedeuserid"] is False
    cred.write_text('{"sessdata": "s", "dedeuserid": "42"}', "utf-8")
    st = setup.credential_state()
    assert st["has_sessdata"] is True
    assert st["has_dedeuserid"] is True


def test_disable_removes_mcp_config_but_keeps_credentials(bili_dirs, monkeypatch):
    (bili_dirs / "mcp.json").write_text("{}", "utf-8")
    (bili_dirs / "bili_credential.json").write_text("{}", "utf-8")

    async def fake_reload():
        return None

    monkeypatch.setattr(setup.capability_registry, "reload", fake_reload)
    import asyncio
    asyncio.run(setup.disable())
    assert not (bili_dirs / "mcp.json").exists()
    assert (bili_dirs / "bili_credential.json").exists()


def test_log_since_returns_incremental_chunks(bili_dirs):
    import asyncio

    (bili_dirs / "setup.log").write_text("pip: collect bilibili-api-python\n", "utf-8")
    (bili_dirs / "setup.json").write_text(
        json.dumps({"phase": "deps", "error": "", "updated_at": 1}),
        "utf-8",
    )
    first = asyncio.run(setup.log_since(0))
    assert first["text"] == "pip: collect bilibili-api-python\n"
    assert first["done"] is False
    assert first["phase"] == "deps"

    with (bili_dirs / "setup.log").open("a", encoding="utf-8") as fh:
        fh.write("Successfully installed bilibili-api-python\n")
    second = asyncio.run(setup.log_since(first["offset"]))
    assert second["text"] == "Successfully installed bilibili-api-python\n"

    (bili_dirs / "setup.json").write_text(
        json.dumps({"phase": "ok", "error": "", "updated_at": 2}),
        "utf-8",
    )
    third = asyncio.run(setup.log_since(second["offset"]))
    assert third["done"] is True
    assert third["text"] == ""


def test_start_setup_returns_log_offset(bili_dirs, monkeypatch):
    import asyncio

    log = bili_dirs / "setup.log"
    log.write_text("previous output\n", "utf-8")

    async def fake_run():
        return None

    async def scenario():
        monkeypatch.setattr(setup, "_run_setup", fake_run)
        out = await setup.start_setup()
        assert out["started"] is True
        assert out["log_offset"] == log.stat().st_size
        await setup._setup_task

    asyncio.run(scenario())
