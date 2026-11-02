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


@router.get("/judge-prompt")
def get_judge_prompt(request: Request) -> dict[str, Any]:
    _require_local(request)
    return service.judge_prompt_payload()


@router.post("/mood-event")
def post_mood_event(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_local(request)
    try:
        return service.apply_mood_event(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/timeline")
def get_timeline(request: Request, limit: int = 20) -> dict[str, Any]:
    _require_local(request)
    return {"ok": True, "events": service.timeline_payload(limit)}


@router.post("/pet-event")
def post_pet_event(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_local(request)
    return service.publish_pet_event(body)
