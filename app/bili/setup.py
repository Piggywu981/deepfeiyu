"""B 站 MCP 安装骨架：复制运行文件、建独立 venv、生成只读 mcp.json。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import config
from ..capabilities.registry import registry as capability_registry

log = logging.getLogger("bili")

BILI_DIR = config.DATA_DIR / "bili"
VENDOR_DIR = config.ROOT / "vendor" / "bilibili-mcp"
MCP_PATH = BILI_DIR / "mcp.json"
SETUP_PATH = BILI_DIR / "setup.json"
LOG_PATH = BILI_DIR / "setup.log"
CREDENTIAL_PATH = BILI_DIR / "bili_credential.json"
PATROL_DIR = BILI_DIR / "patrol"

VENDOR_FILES = ("mcp_server.py", "requirements.txt", "README.md", "LICENSE")

READONLY_TOOLS = (
    "bili_login",
    "bili_login_check",
    "bili_check_credential",
    "bili_search",
    "bili_subtitle",
    "bili_comments",
    "bili_danmaku",
    "bili_video_info",
    "bili_crawl",
    "bili_hot_videos",
    "bili_hot_buzzwords",
    "bili_weekly_hot",
    "bili_rank",
    "bili_user_info",
    "bili_user_videos",
    "bili_favorite_lists",
    "bili_favorite_content",
    "bili_unread_messages",
    "bili_received_replies",
    "bili_received_at_and_likes",
    "bili_video_zones",
)

_setup_task: asyncio.Task[Any] | None = None
_setup_lock = asyncio.Lock()


def venv_python() -> Path:
    return BILI_DIR / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text("utf-8", errors="replace")[-limit:]
    except Exception:  # noqa: BLE001
        return ""


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _clean_log_text(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).replace("\r\n", "\n").replace("\r", "\n")


async def log_since(offset: int = 0, max_bytes: int = 200_000) -> dict[str, Any]:
    data = _read_json(SETUP_PATH, {})
    phase = str(data.get("phase") or "idle")
    done = phase in {"ok", "error", "idle"}
    try:
        size = LOG_PATH.stat().st_size
        reset = offset < 0 or offset > size
        start = 0 if reset else offset
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            if reset:
                fh.seek(max(0, size - max_bytes))
                start = fh.tell()
            else:
                fh.seek(start)
            chunk = fh.read(max_bytes)
            end = fh.tell()
        return {
            "ok": True,
            "offset": end,
            "size": size,
            "text": _clean_log_text(chunk),
            "phase": phase,
            "error": str(data.get("error") or ""),
            "done": done,
            "reset": reset,
        }
    except FileNotFoundError:
        return {
            "ok": True,
            "offset": 0,
            "size": 0,
            "text": "",
            "phase": phase,
            "error": str(data.get("error") or ""),
            "done": done,
            "reset": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "offset": int(offset or 0),
            "size": _file_size(LOG_PATH),
            "text": f"读取安装日志失败：{exc}\n",
            "phase": phase,
            "error": str(data.get("error") or exc),
            "done": done,
            "reset": False,
        }


def ensure_runtime_files() -> dict[str, Any]:
    BILI_DIR.mkdir(parents=True, exist_ok=True)
    PATROL_DIR.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in VENDOR_FILES:
        src = VENDOR_DIR / name
        if not src.is_file():
            raise FileNotFoundError(f"缺少上游文件：{src}")
        dst = BILI_DIR / name
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        if dst.is_file():
            backup = dst.with_name(dst.name + f".bak-{int(time.time())}")
            dst.replace(backup)
        shutil.copy2(src, dst)
        copied.append(name)
    return {"copied": copied}


def mcp_payload(py: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            "bilibili": {
                "transport": "stdio",
                "command": str(py),
                "args": [str(BILI_DIR / "mcp_server.py")],
                "cwd": str(BILI_DIR),
                "env": {"BILI_MCP_TOOLS": ",".join(READONLY_TOOLS)},
                "auto_reply": False,
                "timeout": 120,
                "enabled": True,
            }
        }
    }


def write_mcp_config(py: Path) -> None:
    _write_json(MCP_PATH, mcp_payload(py))


def remove_mcp_config() -> None:
    try:
        MCP_PATH.unlink()
    except FileNotFoundError:
        pass


def _write_setup(phase: str, error: str = "") -> None:
    _write_json(
        SETUP_PATH,
        {"phase": phase, "error": error, "updated_at": time.time()},
    )


def credential_state() -> dict[str, Any]:
    data = _read_json(CREDENTIAL_PATH, {})
    sessdata = str(data.get("sessdata") or "") if isinstance(data, dict) else ""
    dedeuserid = str(data.get("dedeuserid") or "") if isinstance(data, dict) else ""
    try:
        updated = CREDENTIAL_PATH.stat().st_mtime
    except OSError:
        updated = 0.0
    return {
        "exists": CREDENTIAL_PATH.is_file(),
        "has_sessdata": bool(sessdata),
        "has_dedeuserid": bool(dedeuserid),
        "updated_at": updated,
    }


def status() -> dict[str, Any]:
    data = _read_json(SETUP_PATH, {})
    return {
        "ok": not data.get("error"),
        "installed": venv_python().is_file() and (BILI_DIR / "mcp_server.py").is_file(),
        "phase": str(data.get("phase") or "idle"),
        "error": str(data.get("error") or ""),
        "python": str(venv_python()),
        "mcp_config": MCP_PATH.is_file(),
        "credential": CREDENTIAL_PATH.is_file(),
        "patrol_dir": str(PATROL_DIR),
        "updated_at": float(data.get("updated_at") or 0),
        "log_tail": _tail(LOG_PATH),
    }


def _run(cmd: list[str]) -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(map(str, cmd)) + "\n")
        fh.flush()
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(BILI_DIR),
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
        fh.write(f"[exit {proc.returncode}]\n")
        fh.flush()
    return proc.returncode


def _create_venv() -> Path:
    py = venv_python()
    if py.is_file():
        return py
    uv = shutil.which("uv")
    if uv:
        if _run([uv, "venv", "--seed", str(BILI_DIR / ".venv")]) == 0 and py.is_file():
            return py
    if _run([sys.executable, "-m", "venv", str(BILI_DIR / ".venv")]) != 0 or not py.is_file():
        raise RuntimeError("创建 B 站 venv 失败，见 data/bili/setup.log")
    return py


def _install(py: Path) -> None:
    mirror = str((config.load().get("bili") or {}).get("pip_mirror") or "").strip()
    cmd = [str(py), "-m", "pip", "install"]
    if mirror:
        cmd += ["-i", mirror]
    cmd += ["-r", str(BILI_DIR / "requirements.txt")]
    if _run(cmd) != 0:
        raise RuntimeError("安装 B 站依赖失败，见 data/bili/setup.log")


async def _run_setup() -> dict[str, Any]:
    async with _setup_lock:
        _write_setup("copying")
        try:
            ensure_runtime_files()
            _write_setup("venv")
            py = await asyncio.to_thread(_create_venv)
            _write_setup("deps")
            await asyncio.to_thread(_install, py)
            _write_setup("mcp")
            write_mcp_config(py)
            await capability_registry.reload()
            _write_setup("ok")
            return {"ok": True, **status()}
        except Exception as exc:  # noqa: BLE001
            log.warning("B 站安装失败：%s", exc)
            _write_setup("error", str(exc))
            return {"ok": False, "error": str(exc), **status()}


async def start_setup() -> dict[str, Any]:
    global _setup_task
    log_offset = _file_size(LOG_PATH)
    if _setup_lock.locked():
        return {"ok": False, "error": "正在安装，请稍候", "log_offset": log_offset, **status()}
    _setup_task = asyncio.create_task(_run_setup())
    return {"ok": True, "started": True, "log_offset": log_offset, **status()}


async def disable() -> dict[str, Any]:
    remove_mcp_config()
    await capability_registry.reload()
    return {"ok": True, **status()}
