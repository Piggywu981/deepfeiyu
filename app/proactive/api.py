"""主动搭话 REST API：配置、白名单、候选会话、试运行/手动发送与记录。

约定与心情 API 一致：账号按 ``X-Account-Id`` / ``?account=`` 选择，失败回退
当前账号；所有写接口只允许本机控制台调用，避免局域网内被远程打开真发。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config, db
from . import settings, store

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


def _aid(request: Request) -> str:
    aid = request.headers.get("x-account-id") or request.query_params.get("account") or ""
    if aid and config.account(aid):
        return aid
    return config.active_id()


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "主动搭话设置只允许在本机控制台执行")


class ConfigBody(BaseModel):
    enabled: bool | None = None
    account: dict[str, Any] | None = None


class WhitelistBody(BaseModel):
    action: str
    chat_uid: str
    is_group: bool = False


class TestBody(BaseModel):
    chat_uid: str
    dry_run: bool = True


class RunBody(BaseModel):
    chat_uid: str


def _chat_name(chat_uid: str) -> str:
    """取会话名称；库不可用/会话已删时返回空串。"""
    try:
        chat = db.get_chat(chat_uid)
    except Exception:  # noqa: BLE001 - 列表展示不能因单条查询失败整体 500
        chat = None
    return str((chat or {}).get("name") or "")


def _whitelist_names(account_id: str) -> dict[str, str]:
    return {
        entry["chat_uid"]: entry["name"]
        for entry in settings.whitelist(account_id)
    }


def _scheduler_payload(request: Request) -> dict[str, Any]:
    """调度器状态；服务未接线或尚未启动时按停止处理。"""
    sched = getattr(request.app.state, "proactive_scheduler", None)
    if sched is None:
        return {"running": False, "interval": 60}
    task = getattr(sched, "_task", None)
    try:
        interval = int(getattr(sched, "interval", 60) or 60)
    except (TypeError, ValueError):
        interval = 60
    return {"running": bool(task is not None and not task.done()), "interval": interval}


def _validated_entry(account_id: str, entry: Any) -> dict[str, Any]:
    """校验单条白名单，归属、存在性与群聊标记一律以数据库为准。

    只校验新增/更新的那一条，历史条目（会话可能已删）不参与校验，
    避免 stale 数据把后续增删整体挡住。
    """
    item = settings.valid_entry(entry)
    if item is None:
        raise HTTPException(400, "白名单条目无效")
    uid = item["chat_uid"]
    if not uid.startswith(f"{account_id}:"):
        raise HTTPException(400, "会话不属于当前账号")
    chat = db.get_chat(uid)
    if chat is None:
        raise HTTPException(404, "会话不存在")
    # 群聊必须由调用方显式声明；私聊的 is_group 以库里的值为准
    if chat.get("is_group") and not item["is_group"]:
        raise HTTPException(400, "群聊必须显式勾选")
    return {
        "chat_uid": uid,
        "name": str(chat.get("name") or ""),
        "is_group": bool(chat.get("is_group")),
    }


def _save_proactive(patch: dict[str, Any]) -> None:
    """落盘主动搭话配置；写盘/序列化失败统一转 503，不能吞掉后仍报成功。"""
    try:
        config.save({"proactive": patch})
    except Exception as exc:  # noqa: BLE001 - 落盘失败必须让调用方感知
        raise HTTPException(503, "保存主动搭话配置失败") from exc


def _policy_list(value: Any) -> set[str]:
    """auto.blacklist / auto.whitelist 容错：非列表（含 null）按空名单处理。"""
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if item is not None and str(item)}


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    aid = _aid(request)
    whitelist = settings.whitelist(aid)
    # 一次读全账号状态，再按白名单补默认行：白名单最多 50 条，也不必逐条查库
    existing = {row["chat_uid"]: row for row in store.list_states(aid)}
    states: list[dict[str, Any]] = []
    for entry in whitelist:
        uid = entry["chat_uid"]
        state = existing.get(uid) or store.get_state(aid, uid)
        state["chat_name"] = _chat_name(uid)
        states.append(state)
    # 最近主动开口的会话排前面，方便控制台一眼看到“刚跟谁说过话”
    states.sort(key=lambda item: float(item.get("last_self_at") or 0), reverse=True)
    return {
        "ok": True,
        "account_id": aid,
        "enabled": settings.enabled(),
        "settings": settings.settings(aid),
        "whitelist": whitelist,
        "states": states,
        "scheduler": _scheduler_payload(request),
    }


@router.post("/config")
def post_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    _require_local(request)
    aid = _aid(request)
    # 白名单必须走 /whitelist：/config 不做整体重校验，避免绕过归属校验
    if body.account is not None and "whitelist" in body.account:
        raise HTTPException(400, "请使用白名单接口修改会话名单")

    root = settings.root()
    accounts = root.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    current = accounts.get(aid)
    current = current if isinstance(current, dict) else {}

    effective_enabled = settings.enabled() if body.enabled is None else bool(body.enabled)
    # enabled 判定只看当前已保存的名单，/config 本身不再改白名单
    effective_whitelist = settings.whitelist(aid)

    patch: dict[str, Any] = {}
    if body.enabled is not None:
        patch["enabled"] = bool(body.enabled)
    if body.account is not None:
        # 只接受账号默认表里认识的键，未知字段忽略
        clean = {
            key: value
            for key, value in body.account.items()
            if key in settings.ACCOUNT_DEFAULTS and key != "whitelist"
        }
        merged = {**current, **clean}
        patch["accounts"] = {aid: merged}

    if effective_enabled and not effective_whitelist:
        raise HTTPException(400, "没有白名单不能启用主动搭话")

    # 一次保存同时落 enabled 与账号参数，避免深层合并被拆成两次顶层覆盖
    if patch:
        _save_proactive(patch)
    return {
        "ok": True,
        "enabled": settings.enabled(),
        "settings": settings.settings(aid),
        "whitelist": settings.whitelist(aid),
    }


@router.get("/chats")
def chats(request: Request) -> dict[str, Any]:
    aid = _aid(request)
    app_hub = getattr(request.app.state, "hub", None)
    session = app_hub.get(aid) if app_hub is not None else None
    if session is None:
        return {"ok": True, "chats": []}
    rows = session.visible_chats(limit=500)

    auto = config.load().get("auto")
    auto = auto if isinstance(auto, dict) else {}
    blacklist = _policy_list(auto.get("blacklist"))
    auto_whitelist = _policy_list(auto.get("whitelist"))
    selected_uids = {entry["chat_uid"] for entry in settings.whitelist(aid)}

    out: list[dict[str, Any]] = []
    for row in rows:
        chat_id = str(row.get("chat_id") or "")
        # 文件传输助手不在 is_internal_chat 的清单里，但同样不能作为搭话候选
        if chat_id == "filehelper":
            continue
        name = str(row.get("name") or "")
        ident = {item for item in (chat_id, name) if item}
        uid = str(row.get("uid") or "")
        out.append({
            "uid": uid,
            "chat_id": chat_id,
            "name": name,
            "is_group": bool(row.get("is_group")),
            "ai_enabled": bool(row.get("ai_enabled")),
            "paused": bool(row.get("paused")),
            "blacklisted": bool(ident & blacklist),
            "in_auto_whitelist": bool(ident & auto_whitelist),
            "selected": uid in selected_uids,
            "last_ts": row.get("last_ts") or 0,
        })
    return {"ok": True, "chats": out}


@router.post("/whitelist")
def post_whitelist(request: Request, body: WhitelistBody) -> dict[str, Any]:
    _require_local(request)
    aid = _aid(request)
    action = (body.action or "").strip().lower()
    if action not in {"add", "remove"}:
        raise HTTPException(400, "未知操作")

    uid = str(body.chat_uid or "").strip()
    if not uid.startswith(f"{aid}:"):
        raise HTTPException(400, "会话不属于当前账号")

    current = settings.whitelist(aid)
    if action == "remove":
        # 移除只校验归属：会话已删/已改名也要能把历史条目清掉
        new_list = [entry for entry in current if entry["chat_uid"] != uid]
    else:
        # 只校验新增的这一条；历史 stale 条目原样保留，不因它们失败
        entry = _validated_entry(
            aid, {"chat_uid": uid, "is_group": body.is_group}
        )
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        replaced = False
        for item in current:
            key = item["chat_uid"]
            if key in seen:
                continue
            seen.add(key)
            if key == uid:
                # 已存在则原地更新，保持原有位置
                deduped.append(entry)
                replaced = True
            else:
                deduped.append(item)
        if not replaced:
            # 上限在去重后判断：重复 uid 不占新名额
            if len(deduped) >= settings.MAX_WHITELIST:
                raise HTTPException(400, "白名单最多 50 个会话")
            deduped.append(entry)
        new_list = deduped

    root = settings.root()
    accounts = root.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    account = accounts.get(aid)
    account = account if isinstance(account, dict) else {}
    patch: dict[str, Any] = {"accounts": {aid: {**account, "whitelist": new_list}}}
    # 没有白名单不能启用：清空最后一条时同一次落盘顺手关掉开关
    if not new_list and settings.enabled():
        patch["enabled"] = False
    _save_proactive(patch)
    return {
        "ok": True,
        "whitelist": settings.whitelist(aid),
        "enabled": settings.enabled(),
    }


async def _send(
    request: Request, chat_uid: str, *, trigger: str, ignore_quota: bool, dry_run: bool
) -> dict[str, Any]:
    """试运行/手动发送共用入口；调度器缺失说明主程序没接线，直接 503。"""
    _require_local(request)
    aid = _aid(request)
    sched = getattr(request.app.state, "proactive_scheduler", None)
    service = getattr(sched, "service", None) if sched is not None else None
    if service is None:
        raise HTTPException(503, "主动搭话调度未启动")
    result = await service.send_one(
        aid,
        chat_uid,
        trigger=trigger,
        ignore_quota=ignore_quota,
        dry_run=dry_run,
    )
    out = dict(result) if isinstance(result, dict) else {
        "ok": False,
        "status": "error",
        "text": "",
        "error": "主动搭话服务返回异常",
    }
    out["chat_name"] = _chat_name(chat_uid) or _whitelist_names(aid).get(chat_uid, "")
    return out


@router.post("/test")
async def post_test(request: Request, body: TestBody) -> dict[str, Any]:
    return await _send(
        request,
        body.chat_uid,
        trigger="test",
        ignore_quota=True,
        dry_run=body.dry_run,
    )


@router.post("/run")
async def post_run(request: Request, body: RunBody) -> dict[str, Any]:
    return await _send(
        request,
        body.chat_uid,
        trigger="manual",
        ignore_quota=False,
        dry_run=False,
    )


@router.get("/events")
def events(request: Request, limit: int = 50, before: float = 0) -> dict[str, Any]:
    aid = _aid(request)
    names = _whitelist_names(aid)
    out: list[dict[str, Any]] = []
    for item in store.list_events(aid, limit=limit, before=before):
        item["chat_name"] = _chat_name(item.get("chat_uid") or "") or names.get(
            item.get("chat_uid") or "", ""
        )
        out.append(item)
    return {"ok": True, "events": out}
