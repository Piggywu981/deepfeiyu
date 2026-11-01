"""心情 REST API 与静态素材。"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config
from . import judge as judge_mod, model, service, settings, store

router = APIRouter(prefix="/api/mood", tags=["mood"])

ASSETS_DIR = config.ROOT / "vendor" / "dsh-feiyu-mood" / "assets"
ASSET_NAMES = {
    "face-great.png", "face-good.png", "face-ok.png",
    "face-worry.png", "face-sad.png", "feiyu.png",
}


def _aid(request: Request) -> str:
    aid = request.headers.get("x-account-id") or request.query_params.get("account") or ""
    if aid and config.account(aid):
        return aid
    return config.active_id()


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "心情设置只允许在本机控制台执行")


class ConfigBody(BaseModel):
    enabled: bool | None = None
    account: dict[str, Any] | None = None


class SlotBody(BaseModel):
    action: str
    slot_id: str = ""
    label: str = ""
    self_name: str = ""
    owner_name: str = ""
    self_aliases: str = ""
    chat_uid: str = ""


class ResetBody(BaseModel):
    mood: float | None = None
    reason: str = ""
    chat_uid: str = ""


class JudgeBody(BaseModel):
    text: str
    reply: str = ""
    chat_uid: str = ""


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    aid = _aid(request)
    chat = request.query_params.get("chat") or ""
    return service.state_payload(aid, chat)


@router.get("/timeline")
def get_timeline(request: Request) -> dict[str, Any]:
    aid = _aid(request)
    chat = request.query_params.get("chat") or ""
    before = float(request.query_params.get("before") or 0)
    limit = int(request.query_params.get("limit") or 30)
    return {
        "ok": True,
        "account_id": aid,
        "events": service.timeline_payload(aid, chat, limit=limit, before=before),
    }


@router.post("/config")
def post_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    _require_local(request)
    aid = _aid(request)
    patch: dict[str, Any] = {}
    if body.enabled is not None:
        patch["enabled"] = bool(body.enabled)
    if body.account:
        # 一次 save 同时落 enabled 与账号参数，避免深层合并被拆成两次顶层覆盖。
        clean = {k: v for k, v in body.account.items() if k in settings.ACCOUNT_DEFAULTS}
        current = ((config.load().get("mood") or {}).get("accounts") or {}).get(aid) or {}
        patch["accounts"] = {aid: {**current, **clean}}
    if patch:
        config.save({"mood": patch})
    return {
        "ok": True,
        "enabled": settings.enabled(aid),
        "settings": settings.settings(aid),
    }


@router.post("/slots")
def post_slots(request: Request, body: SlotBody) -> dict[str, Any]:
    _require_local(request)
    aid = _aid(request)
    action = (body.action or "").strip()
    try:
        if action == "create":
            slot = store.create_slot(aid, body.slot_id or uuid.uuid4().hex[:8], body.label)
        elif action == "rename":
            slot = store.update_slot(
                aid, body.slot_id, label=body.label, self_name=body.self_name,
                owner_name=body.owner_name, self_aliases=body.self_aliases,
            )
        elif action == "delete":
            store.delete_slot(aid, body.slot_id)
            slot = {}
        elif action == "bind":
            store.bind_chat(aid, body.slot_id, body.chat_uid)
            slot = {}
        elif action == "unbind":
            store.unbind_chat(aid, body.chat_uid)
            slot = {}
        else:
            raise HTTPException(400, "未知操作")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "slot": slot,
        "slots": [
            {"slot_id": s["slot_id"], "label": s["label"], "mood": s["mood"]}
            for s in store.list_slots(aid)
        ],
        "bindings": store.list_bindings(aid),
    }


@router.post("/reset")
def post_reset(request: Request, body: ResetBody) -> dict[str, Any]:
    _require_local(request)
    aid = _aid(request)
    st = settings.settings(aid)
    slot_id = settings.resolve_slot(aid, body.chat_uid)
    base = st["base"] if body.mood is None else max(0.0, min(100.0, float(body.mood)))
    now = time.time()
    # 与判断线程的「读 → 改 → 写」串行，避免重置结果被旧状态回写覆盖。
    with service.state_lock(aid, slot_id):
        state = store.load_state(aid, slot_id, base=st["base"])
        state.update(
            mood=base,
            dims={k: 0.0 for k in state["dims"]},
            last_reason=str(body.reason or ""),
            last_confidence=100,
            last_noted_mood=base,
            last_decay_at=now,
            injected_for=0.0,
            last_injected_text="",
            last_injected_at=0.0,
            minor=0,
            kind="manual",
            updated_at=now,
            # 手动重置是账号级动作，清除会话来源，之后哪个会话都可重新讲
            last_chat_uid="",
        )
        store.save_state(aid, slot_id, state)
        store.add_event(
            aid, slot_id, at=now, mood=base, delta=0, dims=state["dims"], dims_delta={},
            reason=str(body.reason or "手动重置心情"), confidence=100,
            kind="manual", source="console", minor=0,
        )
    return service.state_payload(aid, body.chat_uid)


@router.post("/judge-test")
async def post_judge_test(request: Request, body: JudgeBody) -> dict[str, Any]:
    _require_local(request)
    aid = _aid(request)
    st = settings.settings(aid)
    slot_id = settings.resolve_slot(aid, body.chat_uid)
    self_name, owner, aliases = settings.names(aid, slot_id)
    llm = getattr(request.app.state, "llm", None)
    delta = await judge_mod.judge(
        llm,
        {"self": self_name, "owner": owner, "aliases": aliases,
         "judge_model": st["judge_model"], "source": "console"},
        body.text, body.reply, "",
    )
    return {
        "ok": True,
        "delta": delta,
        "preview": model.mood_line(
            {**store.load_state(aid, slot_id, base=st["base"]),
             "last_reason": (delta or {}).get("reason", ""),
             "last_confidence": (delta or {}).get("confidence", 0),
             "kind": "judge",
             "updated_at": time.time()},
            st["inject_mode"], owner,
        ) if delta else "",
    }


@router.get("/asset/{name}")
def get_asset(name: str) -> FileResponse:
    if name not in ASSET_NAMES:
        raise HTTPException(404, "素材不存在")
    path = ASSETS_DIR / name
    if not path.is_file():
        raise HTTPException(404, "素材缺失：请检查 vendor/dsh-feiyu-mood")
    return FileResponse(path, media_type="image/png")


@router.get("/widget.js")
def get_widget() -> FileResponse:
    path = config.ROOT / "app" / "static" / "mood-widget.js"
    if not path.is_file():
        raise HTTPException(404, "挂饰脚本缺失")
    return FileResponse(path, media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})
