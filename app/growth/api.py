"""成长 REST API：所有接口按 X-Account-Id / account 参数隔离。"""
from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .. import config
from ..bus import bus
from . import reflect, sanitize, scheduler as scheduler_mod, store

router = APIRouter(prefix="/api/growth", tags=["growth"])


def _aid(request: Request) -> str:
    aid = request.headers.get("x-account-id") or request.query_params.get("account") or ""
    if aid and config.account(aid):
        return aid
    return config.active_id()


def _check_account(request: Request) -> str:
    aid = _aid(request)
    if not config.account(aid):
        raise HTTPException(404, "账号不存在")
    return aid


def _llm(request: Request) -> Any:
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(503, "模型未初始化")
    return llm


class TraitBody(BaseModel):
    content: str
    kind: str = "other"
    tags: list[str] | str = []
    scope: str = "global"
    chat_uid: str = ""
    strength: int = 2


class TraitPatch(BaseModel):
    content: str | None = None
    kind: str | None = None
    tags: list[str] | str | None = None
    scope: str | None = None
    chat_uid: str | None = None
    strength: int | None = None
    status: str | None = None


class GoalBody(BaseModel):
    content: str
    due_at: float = 0


class GoalPatch(BaseModel):
    content: str | None = None
    status: str | None = None
    progress: str | None = None
    due_at: float | None = None


class ProposalAction(BaseModel):
    payload: dict[str, Any] | None = None
    reason: str = ""


class RollbackBody(BaseModel):
    force: bool = False


class SummaryBody(BaseModel):
    self_summary: str


class SettingsBody(BaseModel):
    global_enabled: bool | None = None
    account_enabled: bool | None = None
    reflection_hour: int | None = None
    reflection_minute: int | None = None
    prompt_chars: int | None = None
    summary_chars: int | None = None
    min_new_messages: int | None = None
    manual_max_per_day: int | None = None


def _range(name: str, value: int, low: int, high: int) -> int:
    if not low <= int(value) <= high:
        raise HTTPException(400, f"{name} 必须在 {low}-{high}")
    return int(value)


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    aid = _check_account(request)
    cfg = config.growth_cfg()
    runs = store.list_runs(aid, limit=10)
    last = store.last_successful_run(aid)
    pending = store.list_proposals(aid, status="pending", limit=500)
    sched = getattr(request.app.state, "growth_scheduler", None)
    next_run = scheduler_mod.next_run_at(aid) if sched else None
    return {
        "ok": True,
        "account_id": aid,
        "enabled": config.growth_enabled(aid),
        "global_enabled": bool(cfg.get("enabled")),
        "config": cfg,
        "pending": len(pending),
        "running": any(r["status"] == "running" for r in runs),
        "last_run": last,
        "recent_runs": runs,
        "next_run": next_run,
    }


@router.get("/traits")
def traits(
    request: Request,
    q: str = "",
    kind: str = "",
    status: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    aid = _check_account(request)
    items = store.list_traits(
        aid, q=q, kind=kind, status=status, limit=limit, offset=offset
    )
    return {"ok": True, "items": items}


@router.post("/traits")
def add_trait(request: Request, body: TraitBody) -> dict[str, Any]:
    aid = _check_account(request)
    content, err = sanitize.sanitize_trait(body.content)
    if err:
        raise HTTPException(400, err)
    try:
        res = store.upsert_trait(
            aid,
            {
                "kind": body.kind,
                "content": content,
                "tags": body.tags,
                "scope": body.scope,
                "chat_uid": body.chat_uid,
                "strength": body.strength,
                "confidence": 1.0,
                "source": "owner",
                "status": "active",
                "evidence": [{"type": "console", "quote": "号主手动添加"}],
            },
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    store.add_growth_event(
        aid, "trait.add", "trait", res["trait"]["id"],
        before=res["before"], after=res["trait"],
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "trait"}})
    return {"ok": True, "trait": res["trait"]}


@router.patch("/traits/{trait_id}")
def patch_trait(request: Request, trait_id: int, body: TraitPatch) -> dict[str, Any]:
    aid = _check_account(request)
    fields = body.model_dump(exclude_none=True)
    if "content" in fields:
        content, err = sanitize.sanitize_trait(str(fields["content"]))
        if err:
            raise HTTPException(400, err)
        fields["content"] = content
    try:
        res = store.update_trait(aid, trait_id, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not res:
        raise HTTPException(404, "特质不存在")
    store.add_growth_event(
        aid, "trait.update", "trait", res["trait"]["id"],
        before=res["before"], after=res["trait"],
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "trait"}})
    return {"ok": True, "trait": res["trait"]}


@router.post("/traits/{trait_id}/retire")
def retire_trait(request: Request, trait_id: int) -> dict[str, Any]:
    return _set_trait_status(request, trait_id, "retired")


@router.post("/traits/{trait_id}/activate")
def activate_trait(request: Request, trait_id: int) -> dict[str, Any]:
    return _set_trait_status(request, trait_id, "active")


def _set_trait_status(request: Request, trait_id: int, status: str) -> dict[str, Any]:
    aid = _check_account(request)
    res = store.set_trait_status(aid, trait_id, status)
    if not res:
        raise HTTPException(404, "特质不存在")
    store.add_growth_event(
        aid, f"trait.{status}", "trait", res["trait"]["id"],
        before=res["before"], after=res["trait"],
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "trait"}})
    return {"ok": True, "trait": res["trait"]}


@router.get("/journal")
def journal(request: Request, limit: int = 30, before: str = "") -> dict[str, Any]:
    aid = _check_account(request)
    return {
        "ok": True,
        "items": store.list_journal(aid, limit=limit, before_day=before),
    }


@router.delete("/journal/{journal_id}")
def delete_journal(request: Request, journal_id: int) -> dict[str, Any]:
    aid = _check_account(request)
    res = store.delete_journal(aid, journal_id)
    if not res:
        raise HTTPException(404, "日记不存在")
    store.add_growth_event(
        aid, "journal.delete", "journal", journal_id,
        before=res["journal"], reversible=False,
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "journal"}})
    return {"ok": True}


@router.get("/goals")
def goals(request: Request, status: str = "") -> dict[str, Any]:
    aid = _check_account(request)
    return {"ok": True, "items": store.list_goals(aid, status=status)}


@router.post("/goals")
def add_goal(request: Request, body: GoalBody) -> dict[str, Any]:
    aid = _check_account(request)
    try:
        goal = store.add_goal(aid, body.content, source="owner", due_at=body.due_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    store.add_growth_event(aid, "goal.add", "goal", goal["id"], after=goal)
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "goal"}})
    return {"ok": True, "goal": goal}


@router.patch("/goals/{goal_id}")
def patch_goal(request: Request, goal_id: int, body: GoalPatch) -> dict[str, Any]:
    aid = _check_account(request)
    res = store.update_goal(aid, goal_id, body.model_dump(exclude_none=True))
    if not res:
        raise HTTPException(404, "目标不存在")
    store.add_growth_event(
        aid, "goal.update", "goal", res["goal"]["id"],
        before=res["before"], after=res["goal"],
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "goal"}})
    return {"ok": True, "goal": res["goal"]}


@router.get("/proposals")
def proposals(request: Request, status: str = "pending") -> dict[str, Any]:
    aid = _check_account(request)
    return {"ok": True, "items": store.list_proposals(aid, status=status)}


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    request: Request, proposal_id: int, body: ProposalAction
) -> dict[str, Any]:
    aid = _check_account(request)
    prop = store.get_proposal(aid, proposal_id)
    if not prop:
        raise HTTPException(404, "提案不存在")
    if prop["status"] != "pending":
        raise HTTPException(409, "提案已处理")
    payload = copy.deepcopy(body.payload or prop["payload"])
    if prop["kind"] != "trait":
        raise HTTPException(400, "本期只支持特质提案")
    op = str(payload.get("op") or "add")
    before = prop.get("before")
    if op != "retire":
        evidence, err = sanitize.validate_evidence(
            aid, payload.get("evidence") or prop.get("evidence") or []
        )
        if err:
            raise HTTPException(400, err)
        payload["evidence"] = evidence
    if op == "retire":
        res = store.set_trait_status(aid, int(payload.get("id") or 0), "retired")
        event_kind = "trait.retire"
    elif op == "update" and payload.get("id"):
        content, err = sanitize.sanitize_trait(str(payload.get("content") or ""))
        if err:
            raise HTTPException(400, err)
        fields = {
            key: payload.get(key)
            for key in (
                "kind", "content", "tags", "scope", "chat_uid",
                "strength", "confidence", "status", "source", "evidence",
            )
            if key in payload
        }
        fields["content"] = content
        try:
            res = store.update_trait(aid, int(payload["id"]), fields)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        event_kind = "trait.update"
    else:
        content, err = sanitize.sanitize_trait(str(payload.get("content") or ""))
        if err:
            raise HTTPException(400, err)
        payload["content"] = content
        res = store.upsert_trait(aid, {**payload, "source": "auto", "status": "active"})
        event_kind = "trait.add"
    if not res:
        raise HTTPException(404, "特质不存在")
    event = store.add_growth_event(
        aid, event_kind, "trait", res["trait"]["id"],
        before=res.get("before") or before, after=res["trait"],
        proposal_id=proposal_id,
    )
    store.decide_proposal(aid, proposal_id, "applied")
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "proposal"}})
    return {"ok": True, "trait": res["trait"], "event": event}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    request: Request, proposal_id: int, body: ProposalAction
) -> dict[str, Any]:
    aid = _check_account(request)
    prop = store.get_proposal(aid, proposal_id)
    if not prop:
        raise HTTPException(404, "提案不存在")
    if prop["status"] != "pending":
        raise HTTPException(409, "提案已处理")
    store.decide_proposal(aid, proposal_id, "rejected")
    store.add_growth_event(
        aid, "proposal.reject", "proposal", proposal_id,
        before=prop, after={"reason": body.reason}, reversible=False,
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "proposal"}})
    return {"ok": True}


@router.get("/events")
def events(request: Request, limit: int = 100, before: int = 0) -> dict[str, Any]:
    aid = _check_account(request)
    return {"ok": True, "items": store.list_events(aid, limit=limit, before_id=before)}


@router.post("/events/{event_id}/rollback")
def rollback(
    request: Request, event_id: int, body: RollbackBody
) -> dict[str, Any]:
    aid = _check_account(request)
    try:
        res = store.rollback_event(aid, event_id, force=body.force)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "rollback"}})
    return {"ok": True, **res}


@router.get("/summary")
def get_summary(request: Request) -> dict[str, Any]:
    aid = _check_account(request)
    return {"ok": True, "self_summary": store.get_state(aid)["self_summary"]}


@router.patch("/summary")
def set_summary(request: Request, body: SummaryBody) -> dict[str, Any]:
    aid = _check_account(request)
    text, err = sanitize.sanitize_summary(body.self_summary)
    if err:
        raise HTTPException(400, err)
    res = store.set_summary(aid, text, 0)
    store.add_growth_event(
        aid, "summary.update", "state", 0,
        before={"self_summary": res["before"]},
        after={"self_summary": res["after"]},
    )
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "summary"}})
    return {"ok": True, "self_summary": res["after"]}


@router.post("/reflect")
async def trigger_reflect(request: Request) -> dict[str, Any]:
    aid = _check_account(request)
    if not config.growth_enabled(aid):
        raise HTTPException(400, "成长未启用")
    if reflect.account_lock(aid).locked():
        raise HTTPException(409, "正在反思")
    cfg = config.growth_cfg()
    day = time.strftime("%Y-%m-%d")
    max_manual = int(cfg.get("manual_max_per_day") or 3)
    if store.count_runs_today(aid, day, trigger="manual") >= max_manual:
        raise HTTPException(429, "今天手动反思次数已用完")
    run = store.start_run(aid, "manual", day)
    asyncio.create_task(
        reflect.run_reflection(_llm(request), aid, "manual", run=run)
    )
    return {"ok": True, "run_id": run["id"]}


@router.patch("/settings")
def settings(request: Request, body: SettingsBody) -> dict[str, Any]:
    aid = _check_account(request)
    growth = copy.deepcopy(config.growth_cfg())
    if body.global_enabled is not None:
        growth["enabled"] = bool(body.global_enabled)
    if body.account_enabled is not None:
        accounts = growth.setdefault("accounts", {})
        accounts.setdefault(aid, {})["enabled"] = bool(body.account_enabled)
        if body.account_enabled:
            growth["enabled"] = True
    if body.global_enabled is False:
        growth["enabled"] = False
    if body.reflection_hour is not None:
        growth["reflection_hour"] = _range("reflection_hour", body.reflection_hour, 0, 23)
    if body.reflection_minute is not None:
        growth["reflection_minute"] = _range("reflection_minute", body.reflection_minute, 0, 59)
    if body.prompt_chars is not None:
        growth["prompt_chars"] = _range("prompt_chars", body.prompt_chars, 400, 8000)
    if body.summary_chars is not None:
        growth["summary_chars"] = _range("summary_chars", body.summary_chars, 200, 2000)
    if body.min_new_messages is not None:
        growth["min_new_messages"] = _range("min_new_messages", body.min_new_messages, 1, 1000)
    if body.manual_max_per_day is not None:
        growth["manual_max_per_day"] = _range(
            "manual_max_per_day", body.manual_max_per_day, 0, 10
        )
    config.save({"growth": growth})
    bus.publish({"type": "growth", "data": {"account_id": aid, "kind": "settings"}})
    return {
        "ok": True,
        "growth": config.growth_cfg(),
        "enabled": config.growth_enabled(aid),
    }


@router.get("/export")
def export(request: Request, format: str = "md"):
    aid = _check_account(request)
    filename = f"growth-{aid}-{time.strftime('%Y%m%d-%H%M%S')}"
    if format == "json":
        return JSONResponse(
            store.export_json(aid),
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )
    return PlainTextResponse(
        store.export_markdown(aid),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )
