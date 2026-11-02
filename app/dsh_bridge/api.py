"""DSH 融合接口：绑定、共享心情、宠物事件、时间线与判断提示词。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config
from . import service

router = APIRouter(prefix="/api/dsh", tags=["dsh"])


class ConfigBody(BaseModel):
    account_id: str | None = None
    judge_enabled: bool | None = None
    pet_events_enabled: bool | None = None
    cross_reason_enabled: bool | None = None


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "DSH 融合接口只允许在本机控制台执行")


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    _require_local(request)
    return service.state_payload()


@router.post("/config")
def post_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    _require_local(request)
    try:
        return service.save_config(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
