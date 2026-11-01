"""B 站只读 MCP API。"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import config, db
from ..capabilities.registry import registry as capability_registry
from . import client as bili_client
from . import setup as bili_setup

router = APIRouter(prefix="/api/bili", tags=["bili"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "B 站操作只允许在本机控制台执行")


def _aid(request: Request) -> str:
    aid = request.headers.get("x-account-id") or request.query_params.get("account") or ""
    return aid if config.account(aid) else config.active_id()


class BiliConfigBody(BaseModel):
    enabled: bool | None = None
    pip_mirror: str | None = None
    patrol: dict[str, Any] | None = None


def _patrol_cfg(account_id: str) -> dict[str, Any]:
    from . import patrol

    return patrol.cfg_for(account_id)


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    out = await bili_client.status()
    out["patrol"] = _patrol_cfg(_aid(request))
    return out


@router.post("/setup", dependencies=[Depends(_require_local)])
async def post_setup() -> dict[str, Any]:
    return await bili_setup.start_setup()


@router.get("/setup/status")
async def get_setup_status() -> dict[str, Any]:
    return bili_setup.status()


@router.get("/setup/log")
async def get_setup_log(offset: int = 0, max_bytes: int = 200_000) -> dict[str, Any]:
    return await bili_setup.log_since(max(0, int(offset or 0)), max(1000, min(500_000, int(max_bytes or 200_000))))


@router.post("/login/start", dependencies=[Depends(_require_local)])
async def post_login_start() -> dict[str, Any]:
    return await bili_client.login_start()


@router.get("/login/poll")
async def get_login_poll() -> dict[str, Any]:
    return await bili_client.login_poll()


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    aid = _aid(request)
    root = config.load().get("bili") or {}
    return {
        "ok": True,
        "enabled": bool(root.get("enabled")),
        "pip_mirror": str(root.get("pip_mirror") or ""),
        "patrol": _patrol_cfg(aid),
    }


@router.post("/config", dependencies=[Depends(_require_local)])
async def post_config(request: Request, body: BiliConfigBody) -> dict[str, Any]:
    aid = _aid(request)
    current = config.load().get("bili") or {}
    enabling = body.enabled is True and not bool(current.get("enabled"))
    if enabling and not bili_setup.status()["installed"]:
        raise HTTPException(400, "B 站运行环境未安装，请先点「安装 / 修复」")
    patch: dict[str, Any] = {}
    if body.enabled is not None:
        patch["enabled"] = bool(body.enabled)
    if body.pip_mirror is not None:
        patch["pip_mirror"] = body.pip_mirror.strip()[:300]
    if body.patrol is not None:
        patch["accounts"] = {aid: {"patrol": body.patrol}}
    config.save({"bili": patch})
    if body.enabled is True:
        bili_setup.write_mcp_config(bili_setup.venv_python())
        await capability_registry.reload()
    elif body.enabled is False:
        await bili_setup.disable()
    return {"ok": True, "config": await get_config(request)}


@router.post("/patrol/run", dependencies=[Depends(_require_local)])
async def post_patrol_run(request: Request) -> dict[str, Any]:
    scheduler = getattr(request.app.state, "bili_patrol", None)
    if scheduler is None:
        raise HTTPException(503, "B 站巡逻未启动")
    return await scheduler.run_now(_aid(request), "manual")


@router.get("/patrol/active")
async def get_patrol_active(request: Request, after: int = 0) -> dict[str, Any]:
    scheduler = getattr(request.app.state, "bili_patrol", None)
    if scheduler is None:
        return {"ok": True, "run_id": 0, "items": [], "done": True, "error": "", "started_at": 0}
    return {"ok": True, **scheduler.active_snapshot(_aid(request), max(0, int(after or 0)))}


@router.get("/patrol/runs")
async def get_patrol_runs(request: Request, limit: int = 30) -> dict[str, Any]:
    return {"ok": True, "items": db.list_bili_patrol_runs(_aid(request), limit)}


@router.get("/patrol/runs/{run_id}/events")
async def get_patrol_events(request: Request, run_id: int, after: int = 0) -> dict[str, Any]:
    scheduler = getattr(request.app.state, "bili_patrol", None)
    snap = scheduler.trace_snapshot(run_id, max(0, int(after or 0))) if scheduler else None
    if snap is not None:
        return {"ok": True, **snap}
    path = bili_setup.PATROL_DIR / "logs" / f"{int(run_id)}.log"
    if not path.is_file():
        raise HTTPException(404, "没有这一轮的巡逻日志")
    text = await asyncio.to_thread(path.read_text, "utf-8", "replace")
    return {
        "ok": True,
        "run_id": int(run_id),
        "items": [{"seq": 0, "time": "", "kind": "file", "text": text}],
        "done": True,
        "error": "",
        "started_at": 0,
    }


@router.get("/patrol/report")
async def get_patrol_report(date: str) -> dict[str, Any]:
    if not _DATE_RE.match(date or ""):
        raise HTTPException(400, "日期格式必须是 YYYY-MM-DD")
    path = bili_setup.PATROL_DIR / f"{date}.md"
    if not path.is_file():
        raise HTTPException(404, "没有这一天的巡逻报告")
    return {"ok": True, "date": date, "text": path.read_text("utf-8", errors="replace")}
