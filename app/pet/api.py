"""桌宠 REST：状态、配置、素材复检、预览。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config
from . import assets, install, protocol, reducer, settings
from .supervisor import supervisor

router = APIRouter(prefix="/api/pet", tags=["pet"])


class ConfigBody(BaseModel):
    enabled: bool | None = None
    scale: float | None = None
    bubbleScale: float | None = None
    activityLevel: str | None = None
    bubbleMode: str | None = None
    bubbleStates: list[str] | None = None
    reducedMotion: bool | None = None
    soundEnabled: bool | None = None
    soundStates: list[str] | None = None
    showChatName: bool | None = None
    showChatDigest: bool | None = None


class TestBody(BaseModel):
    state: str
    text: str = ""


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "桌宠设置只允许在本机控制台执行")


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    _require_local(request)
    return {"state": supervisor.status(), "config": settings.section(config.load()),
            "install": install.status()}


@router.post("/config")
async def post_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    _require_local(request)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = settings.save(patch)
    if patch.get("enabled") is True:
        await supervisor.start()
    elif patch.get("enabled") is False:
        await supervisor.stop()
    elif supervisor.running:
        await supervisor.apply_config()
    return {"config": cfg, "state": supervisor.status()}


@router.post("/reload-assets")
def reload_assets(request: Request) -> dict[str, Any]:
    _require_local(request)
    ok, reason = assets.verify_bundle()
    return {"ok": ok, "reason": reason, "state": supervisor.status()}


@router.post("/test")
async def post_test(request: Request, body: TestBody) -> dict[str, Any]:
    _require_local(request)
    if body.state not in protocol.STATES:
        raise HTTPException(400, f"未知状态：{body.state}")
    if not supervisor.running:
        raise HTTPException(409, "桌宠未运行，先在设置页开启")
    await supervisor.preview(body.state, body.text)
    return {"sent": {"state": body.state, "states": list(reducer.STATES)}}


@router.post("/install")
async def post_install(request: Request) -> dict[str, Any]:
    """一键准备：装 PySide6-Essentials、装配素材、启用并拉起。"""
    _require_local(request)
    return await install.start_install()


@router.get("/install/status")
def get_install_status(request: Request) -> dict[str, Any]:
    _require_local(request)
    return install.status()


@router.get("/install/log")
async def get_install_log(request: Request, offset: int = 0) -> dict[str, Any]:
    _require_local(request)
    return await install.log_since(max(0, offset))
