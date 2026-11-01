"""AI 只能发送程序自己产生的文件，不能把 .env 或 data/agent.db 发出去。"""
from pathlib import Path

import pytest

from app import config
from app.ai import tools


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把 DATA_DIR 指到临时目录，避免碰到真实的 data/agent.db。"""
    data = tmp_path / "data"
    (data / "downloads").mkdir(parents=True)
    (data / "agent_uploads").mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(tools.web, "DOWNLOAD_DIR", data / "downloads")
    return data


def test_dotenv_is_refused(sandbox):
    secret = sandbox.parent / ".env"
    secret.write_text("DEEPSEEK_API_KEY=sk-real", encoding="utf-8")
    with pytest.raises(ValueError, match="不允许发送"):
        tools._assert_sendable_path(str(secret))


def test_database_is_refused(sandbox):
    db_file = sandbox / "agent.db"
    db_file.write_bytes(b"SQLite format 3")
    with pytest.raises(ValueError, match="不允许发送"):
        tools._assert_sendable_path(str(db_file))


def test_downloads_dir_is_allowed(sandbox):
    f = sandbox / "downloads" / "a.png"
    f.write_bytes(b"x")
    assert tools._assert_sendable_path(str(f)) == str(f.resolve())


def test_uploads_dir_is_allowed(sandbox):
    f = sandbox / "agent_uploads" / "report.pdf"
    f.write_bytes(b"x")
    tools._assert_sendable_path(str(f))


def test_traversal_out_of_allowed_root_is_refused(sandbox):
    evil = sandbox / "downloads" / ".." / "agent.db"
    (sandbox / "agent.db").write_bytes(b"x")
    with pytest.raises(ValueError, match="不允许发送"):
        tools._assert_sendable_path(str(evil))


def test_missing_file_is_refused(sandbox):
    gone = sandbox / "downloads" / "nope.png"
    with pytest.raises(ValueError, match="不允许发送"):
        tools._assert_sendable_path(str(gone))
