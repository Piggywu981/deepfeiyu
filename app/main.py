"""FastAPI 入口：多账号 REST + WebSocket + 智能体 SSE。"""
from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlparse

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, db, eula, helpdoc, update, usage, whale
from .ai import chuanzi, memory, moe_wiki
from .bili.api import router as bili_router
from .bili.patrol import BiliPatrolScheduler
from .capabilities.registry import registry as capability_registry
from .growth import prompt as growth_prompt
from .growth.api import router as growth_router
from .growth.scheduler import GrowthScheduler
from .mood import service as mood_service
from .mood.api import router as mood_router
from .pet.api import router as pet_router
from .pet.supervisor import supervisor as pet_supervisor
from .proactive.api import router as proactive_router
from .proactive.scheduler import ProactiveScheduler
from .ai.agent import Agent
from .ai.llm import LLM, LLMError
from .ai.tools import build_tools
from .ai.vision import MAX_IMAGE_BYTES, build_user_content, resolve_upload_ref, self_image_messages
from .bus import bus
from .wechat.diagnose import diagnose, list_windows
from .wechat.manager import AccountSession, Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"
UPLOAD_DIR = config.DATA_DIR / "agent_uploads"
llm = LLM(config.load)
hub = Hub(config.load, llm)
growth_scheduler = GrowthScheduler(llm)
bili_patrol_scheduler = BiliPatrolScheduler(llm, hub)
proactive_scheduler = ProactiveScheduler(hub)
console_locks: dict[str, asyncio.Lock] = {}
EFFORT_VALUES = {"auto", "off", "on", "low", "medium", "high", "max"}
SHUTDOWN: dict[str, Any] = {"requested": False, "reason": "", "restart": False, "task": None}
# 导入请求体上限（压缩前）：解压总量另有 1GB 预算，防止超大 multipart 先把临时盘写满
IMPORT_REQUEST_MAX_BYTES = 1024 * 1024 * 1024


def _request_shutdown(reason: str) -> None:
    """智能体请求自毁：先记下请求，等本轮回复落库送达后再真正退出。"""
    SHUTDOWN["requested"] = True
    SHUTDOWN["reason"] = (reason or "AI 自主关闭").strip()[:200]


def _request_restart(reason: str) -> None:
    """智能体请求重启：退出后由分离进程自动重新拉起 run.bat。"""
    SHUTDOWN["requested"] = True
    SHUTDOWN["restart"] = True
    SHUTDOWN["reason"] = (reason or "AI 请求重启").strip()[:200]


def _spawn_restarter() -> None:
    """起一个分离进程：等本进程退出、端口释放后，重新拉起 run.bat（或等价的 python 入口）。"""
    bat = config.ROOT / "run.bat"
    if os.name == "nt":
        target = ["cmd", "/c", str(bat)] if bat.is_file() else [sys.executable, "-m", "app.main"]
        # 不要直接把带引号的 cmd 串交给 Popen：list2cmdline 会把它转义成 \"，cmd 不认这个转义，
        # 于是 start 拿到的是 "\Deep肥鱼\" 这种残缺路径并弹「找不到文件」。
        # 改用 Python 子进程负责“延时 + 拉起”，参数走 argv，不受 cmd 引号解析影响。
        code = (
            "import subprocess, sys, time;"
            "time.sleep(2);"
            "subprocess.Popen(sys.argv[2:], cwd=sys.argv[1],"
            " creationflags=0x00000010, close_fds=True)"
        )
        subprocess.Popen(
            [sys.executable, "-c", code, str(config.ROOT), *target],
            cwd=str(config.ROOT),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
    else:
        subprocess.Popen(
            ["sh", "-c", "sleep 2; exec python3 -m app.main"],
            cwd=str(config.ROOT),
            start_new_session=True,
            close_fds=True,
        )
    log.info("已安排重启：旧进程退出后自动重新启动服务")


async def _graceful_exit(reason: str, delay: float) -> None:
    """保存数据 → 停止微信通道 →（重启时）安排拉起进程 → 结束后端进程。"""
    await asyncio.sleep(delay)
    log.info("正在关闭服务：%s", reason)
    try:
        db.flush()
    except Exception:  # noqa: BLE001
        log.exception("数据库落盘失败")
    try:
        await capability_registry.stop()
    except Exception:  # noqa: BLE001
        log.exception("停止能力系统失败")
    try:
        await hub.stop_all()
    except Exception:  # noqa: BLE001
        log.exception("停止微信通道失败")
    if SHUTDOWN.get("restart"):
        try:
            _spawn_restarter()
        except Exception:  # noqa: BLE001
            log.exception("安排重启进程失败，需手动运行 run.bat")
    log.info("Deep肥鱼已退出（%s）", reason)
    os._exit(0)


def _exit_later(reason: str, delay: float = 1.0) -> None:
    """延迟退出，留出时间把接口响应 / 流式回复发给前端。"""
    if SHUTDOWN.get("task"):
        return
    SHUTDOWN["task"] = asyncio.create_task(_graceful_exit(reason, delay))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init()
    await hub.start()
    try:
        await capability_registry.start()
    except Exception as exc:  # noqa: BLE001
        log.warning("能力系统启动失败：%s", exc)
    await growth_scheduler.start()
    await bili_patrol_scheduler.start()
    await proactive_scheduler.start()
    try:
        await pet_supervisor.start()
    except Exception as exc:  # noqa: BLE001 - 桌宠不参与业务，起不来只留原因
        log.warning("桌宠启动失败：%s", exc)
    cfg = config.load()["server"]
    log.info("服务已启动 http://%s:%s（能力：%s）", cfg["host"], cfg["port"],
             "开" if capability_registry.enabled() else "关")
    yield
    await pet_supervisor.stop()
    await proactive_scheduler.stop()
    await bili_patrol_scheduler.stop()
    await growth_scheduler.stop()
    try:
        await capability_registry.stop()
    except Exception:  # noqa: BLE001
        pass
    await hub.stop_all()


app = FastAPI(title="DeepFeiyu WeChat Agent", version=__version__, lifespan=lifespan)
app.include_router(growth_router)
app.include_router(bili_router)
app.include_router(mood_router)
app.include_router(proactive_router)
app.include_router(pet_router)
app.state.llm = llm
app.state.hub = hub
app.state.growth_scheduler = growth_scheduler
app.state.bili_patrol = bili_patrol_scheduler
app.state.proactive_scheduler = proactive_scheduler


# 本机来源白名单的唯一出处在 config，路由与 WebSocket 共用同一份，避免两处漂移
LOCAL_HOSTS = config.LOCAL_HOSTS


def _token_match(supplied: str, expected: str) -> bool:
    """恒时比较；按字节比较，避免非 ASCII 输入触发 compare_digest 的 TypeError。"""
    return hmac.compare_digest(
        (supplied or "").encode("utf-8"), (expected or "").encode("utf-8")
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    cfg = config.load()
    token = (cfg.get("server", {}).get("access_token") or "").strip()
    host = request.client.host if request.client else ""
    # OPTIONS 是 CORS 预检，不返回数据，也不改状态，放行
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        if token:
            # 令牌只认请求头；查询参数仅限只读请求（图片/下载链接没法带自定义头）
            supplied = request.headers.get("x-access-token") or ""
            if request.method in ("GET", "HEAD"):
                supplied = supplied or request.query_params.get("token") or ""
            if not _token_match(supplied, token):
                return JSONResponse({"detail": "未授权：请提供访问令牌"}, status_code=401)
        elif host not in LOCAL_HOSTS:
            # 未设令牌时整族接口只对本机开放：否则把 host 改成 0.0.0.0
            # 就等于把聊天记录与配置暴露给整个局域网
            return JSONResponse({"detail": "未设置访问令牌时，接口只允许本机访问"}, status_code=403)
    # 写操作只允许本机：有人把 server.host 改成 0.0.0.0 时，远程仍不能改配置或发消息
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "写操作只允许在本机控制台执行"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    """挡掉浏览器跨站写请求（CSRF）：带 Origin 时必须与当前站点同源。

    非浏览器客户端（Webhook 网关、脚本、curl）通常不发 Origin，照常放行；
    真正的外部网关本来也只能在本机执行（写操作限制在回环地址）。
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != request.url.netloc:
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def import_body_guard(request: Request, call_next):
    """表情导入的请求体上限：在 multipart 解析前挡掉超大上传。"""
    if request.url.path == "/api/emojis/import":
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > IMPORT_REQUEST_MAX_BYTES:
            return JSONResponse(
                {"detail": "导入内容超过 1GB 上限，请分批导入"}, status_code=413
            )
    return await call_next(request)


def acc_id(request: Request) -> str:
    aid = request.headers.get("x-account-id") or request.query_params.get("account") or ""
    if aid and config.account(aid):
        return aid
    return config.active_id()


def session_of(request: Request) -> AccountSession:
    try:
        return hub.require(acc_id(request))
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/eula", response_class=HTMLResponse)
async def eula_page(lang: str = "zh") -> HTMLResponse:
    try:
        return HTMLResponse(eula.page(lang))
    except OSError:
        return HTMLResponse(eula.missing_page(lang), status_code=404)


@app.get("/api/eula")
async def eula_data(lang: str = "zh") -> Any:
    try:
        return eula.payload(lang)
    except OSError:
        return JSONResponse({"detail": "协议文件缺失，请从仓库恢复 EULA.md"}, status_code=404)


@app.get("/api/usage")
async def usage_doc_data() -> Any:
    """控制台「说明」页：每次现读 docs/使用说明.md，不做缓存。"""
    try:
        return JSONResponse(helpdoc.payload(), headers={"Cache-Control": "no-store"})
    except OSError:
        return JSONResponse(
            {"detail": "使用说明缺失，请从仓库恢复 docs/使用说明.md"},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "ts": time.time(), "version": __version__}


@app.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    session = hub.get(acc_id(request))
    if not session:
        return {
            "account_id": acc_id(request),
            "account_name": "未启用",
            "connected": False,
            "driver": "none",
            "display_name": "未启动",
            "capabilities": [],
            "ai": {"configured": llm.configured, "model": config.load()["ai"]["model"]},
            "auto": config.load()["auto"],
        }
    return await session.status()


@app.get("/api/accounts")
async def accounts() -> list[dict[str, Any]]:
    active = config.active_id()
    out = []
    for acc in config.accounts():
        session = hub.sessions.get(acc["id"])
        st = await session.status() if session else {
            "connected": False,
            "display_name": "未启用" if not acc.get("enabled", True) else "未启动",
            "detail": "",
            "error": "",
            "driver": "none",
        }
        out.append(
            {
                **{k: acc.get(k) for k in ("id", "name", "nickname", "persona", "driver", "enabled", "wcferry", "weixin4", "webhook")},
                "active": acc["id"] == active,
                "connected": st.get("connected", False),
                "running_driver": st.get("driver", "none"),
                "display_name": st.get("display_name", ""),
                "detail": st.get("detail", ""),
                "error": st.get("error", ""),
            }
        )
    return out


class AccountBody(BaseModel):
    name: str | None = None
    nickname: str | None = None
    persona: dict[str, Any] | None = None
    driver: str | None = None
    enabled: bool | None = None
    wcferry: dict[str, Any] | None = None
    weixin4: dict[str, Any] | None = None
    webhook: dict[str, Any] | None = None


@app.post("/api/accounts")
async def create_account(body: AccountBody) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    payload.setdefault("name", "新账号")
    acc = config.upsert_account(payload)
    await hub.reload_account(acc["id"])
    return {"ok": True, "account": acc}


async def _reload_account_bg(account_id: str) -> None:
    try:
        await hub.reload_account(account_id)
    except Exception as exc:  # 后台重启失败只记日志，不打断保存响应
        log.warning("后台重启通道失败 %s: %s", account_id, exc)


@app.put("/api/accounts/{account_id}")
async def update_account(account_id: str, body: AccountBody) -> dict[str, Any]:
    if not config.account(account_id):
        raise HTTPException(404, "账号不存在")
    payload = body.model_dump(exclude_none=True)
    payload["id"] = account_id
    acc = config.upsert_account(payload)
    # 通道重启放后台：weixin4 启动含密钥提取（10s+），不能卡住保存响应
    asyncio.create_task(_reload_account_bg(account_id))
    return {"ok": True, "account": acc, "restarting": True}


@app.delete("/api/accounts/{account_id}")
async def remove_account(account_id: str) -> dict[str, Any]:
    if not config.delete_account(account_id):
        raise HTTPException(400, "至少要保留一个账号")
    await hub.remove_account(account_id)
    return {"ok": True, "active_account": config.active_id()}


@app.post("/api/accounts/{account_id}/activate")
async def activate_account(account_id: str) -> dict[str, Any]:
    if not config.set_active(account_id):
        raise HTTPException(404, "账号不存在")
    bus.publish({"type": "accounts_dirty"})
    return {"ok": True, "active_account": account_id}


@app.get("/api/weixin4/accounts")
async def weixin4_accounts() -> dict[str, Any]:
    """枚举本机微信 4.x 可选账号（昵称 + 微信号 + 在线状态）。"""
    from .wechat.diagnose import list_weixin4_accounts

    running: dict[str, str] = {}
    for s in hub.sessions.values():
        drv = s.driver
        if getattr(drv, "name", "") == "weixin4" and getattr(drv, "_connected", False) and getattr(drv, "db", None):
            running[drv.db.account] = drv._self_name
    result = await asyncio.to_thread(list_weixin4_accounts, running)
    active = config.account(config.active_id()) or {}
    result["selected"] = (active.get("weixin4") or {}).get("account_dir", "")
    return result


@app.get("/api/instances")
async def instances() -> dict[str, Any]:
    clients = hub.instances()
    windows = list_windows()
    for w in windows:
        w["hookable"] = (w.get("version") or "").startswith("3.9.")
    return {
        "clients": clients,
        "windows": windows,
        "hookable": any(c["version"].startswith("3.9.") for c in clients),
        "count": len(windows),
    }


@app.get("/api/stats")
async def stats(request: Request) -> dict[str, Any]:
    aid = acc_id(request)
    now = time.time()
    start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    session = hub.get(aid)
    return {
        "account_id": aid,
        "today": {
            "inbound": db.count_messages(aid, start, now, is_self=False),
            "outbound": db.count_messages(aid, start, now, is_self=True),
            "ai": db.count_ai_replies_today(aid),
            "chats": len(session.visible_chats(limit=500)) if session else len(db.list_chats(aid, limit=500)),
        },
        "usage": db.get_usage(aid),
        "daily": db.daily_counts(aid, 7),
        "driver": (await session.status()) if session else None,
    }


@app.get("/api/chats")
async def chats(request: Request, keyword: str = "", limit: int = 200) -> list[dict[str, Any]]:
    session = hub.get(acc_id(request))
    if session:
        # 统一可见性：隐藏微信内部聚合会话；真实驱动再按账号白名单过滤
        return session.visible_chats(keyword=keyword, limit=limit)
    return db.list_chats(acc_id(request), keyword=keyword, limit=limit)


@app.get("/api/chats/{uid}/messages")
async def messages(uid: str, limit: int = 80) -> dict[str, Any]:
    chat = db.get_chat(uid)
    if not chat:
        raise HTTPException(404, "会话不存在")
    db.clear_unread(uid)
    rows = db.list_messages(uid, limit=limit)
    session = hub.get(db.split_uid(uid)[0])
    resolve = getattr(getattr(session, "driver", None), "resolve_name", None)
    if callable(resolve):
        names: dict[str, str] = {}
        for row in rows:
            if row.get("is_self"):
                continue
            wxid = row.get("sender_name") or ""
            if not wxid:
                continue
            if wxid not in names:
                try:
                    names[wxid] = resolve(wxid) or wxid
                except Exception:
                    names[wxid] = wxid
            row["sender_name"] = names[wxid]
    return {"chat": chat, "messages": rows}


@app.get("/api/chats/{uid}/messages/{msg_id}/image")
async def message_image(uid: str, msg_id: str) -> FileResponse:
    row = db.get_message(uid, msg_id)
    meta = (row or {}).get("meta") or {}
    refer = meta.get("refer") or {}
    # 引用图片消息：本体是文本卡片，被引用的原图按 refermsg 的 svrid 反查
    is_refer_image = str(refer.get("type") or "") == "3" and refer.get("svrid")
    if not row or (row.get("msg_type") != "image" and not is_refer_image):
        raise HTTPException(404, "图片消息不存在")
    session = hub.get(db.split_uid(uid)[0])
    driver = getattr(session, "driver", None)
    if is_refer_image:
        getter = getattr(driver, "get_image_by_server_id", None)
        if getter is None:
            raise HTTPException(404, "当前通道不支持读取图片")
        path = await asyncio.to_thread(getter, row.get("chat_id") or "", str(refer["svrid"]))
    else:
        getter = getattr(driver, "get_image", None)
        if getter is None:
            raise HTTPException(404, "当前通道不支持读取图片")
        path = await asyncio.to_thread(
            getter,
            row.get("chat_id") or "",
            msg_id,
            str(meta.get("shard") or ""),
        )
    if not path or not Path(path).is_file():
        raise HTTPException(404, "本机没有这张图片的缓存")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


class SendBody(BaseModel):
    text: str
    # human = 控制台人工发送（不排队）；dsh = Harness 智能体发送（走既有节流队列）。
    # 默认 human，非法值由 Pydantic 返回 422，不在这里手工校验。
    source: Literal["human", "dsh"] = "human"


@app.post("/api/chats/{uid}/send")
async def send(request: Request, uid: str, body: SendBody) -> dict[str, Any]:
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "内容不能为空")
    session = session_of(request)
    try:
        record = await session.send_text(uid, text, source=body.source)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    pause = int(config.load()["auto"].get("takeover_pause_min", 0) or 0)
    if pause:
        db.set_chat_pause(uid, time.time() + pause * 60)
    return {"ok": True, "message": record}


# ---------------------------------------------------------------- 表情
class EmojiBody(BaseModel):
    url: str
    md5: str = ""
    width: int = 0
    height: int = 0


@app.get("/api/emojis")
async def emojis(request: Request, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """表情墙分页拉取：默认每页 200，前端「加载更多」带 offset 翻页。"""
    return db.list_emojis(acc_id(request), max(1, min(limit, 5000)), max(0, offset))


@app.post("/api/emojis")
async def add_emoji(request: Request, body: EmojiBody) -> dict[str, Any]:
    from .emoji import EmojiURLError, sanitize_emoji_url

    try:
        url = sanitize_emoji_url(body.url)
    except EmojiURLError as exc:
        raise HTTPException(400, str(exc))
    row = db.add_emoji(
        acc_id(request), url, body.md5 or "", body.width or 0, body.height or 0
    )
    # 手动收藏的也补打标（无聊天上下文，仅视觉模型可用时有效）；失败不影响收藏
    try:
        session_of(request).queue_emoji_collect(
            {"url": url, "md5": body.md5 or "", "width": body.width or 0, "height": body.height or 0},
            force=True,
        )
    except Exception:
        pass
    return {"ok": True, "emoji": row}


@app.post("/api/emojis/import-qq")
async def import_qq_emojis(request: Request) -> dict[str, Any]:
    """扫描本机 QQ 缓存目录（Ori 原图），把没入库的表情导入当前账号。"""
    from .emoji import adopt_emoji_file, qq_cache_roots, scan_qq_cache_files

    account_id = acc_id(request)
    if not qq_cache_roots():
        raise HTTPException(404, "没找到本机 QQ 表情缓存目录（Documents/Tencent Files）")
    imported = skipped = broken = rejected_skip = 0
    rejects = db.list_reject_md5s(account_id)
    for src in scan_qq_cache_files():
        # QQNT 的 Ori/Thumb 文件名就是内容 md5：命中拒收名单连文件都不用读
        if src.stem.lower() in rejects:
            rejected_skip += 1
            continue
        adopted = await asyncio.to_thread(adopt_emoji_file, src)
        if adopted is None:
            broken += 1
            continue
        md5 = adopted[0]
        if md5 in rejects or db.get_emoji_by_md5(account_id, md5):
            skipped += 1
            continue
        db.add_emoji(account_id, f"local://{md5}", md5)
        imported += 1
    return {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "rejected": rejected_skip,
        "broken": broken,
    }


class EmojiLockBody(BaseModel):
    locked: bool


class TagLockBody(BaseModel):
    tag: str = ""
    locked: bool = True


class EmojiIdsBody(BaseModel):
    ids: list[int] = []


class EmojiBatchLockBody(BaseModel):
    ids: list[int] = []
    locked: bool = True


class EmojiTagBody(BaseModel):
    tags: str = ""
    description: str = ""


@app.post("/api/emojis/{eid}/lock")
async def lock_emoji(request: Request, eid: int, body: EmojiLockBody) -> dict[str, Any]:
    """锁定/解锁单张表情：锁定后禁改标签描述、禁 AI 重打标、禁删除、导入不再合并。"""
    row = db.set_emoji_locked(acc_id(request), eid, body.locked)
    if not row:
        raise HTTPException(404, "表情不存在")
    return {"ok": True, "emoji": row}


@app.delete("/api/emojis/{eid}")
async def delete_emoji(request: Request, eid: int) -> dict[str, Any]:
    account_id = acc_id(request)
    row = db.get_emoji(account_id, eid)
    if not row:
        raise HTTPException(404, "收藏不存在")
    if row.get("locked"):
        raise HTTPException(409, "已锁定，先解锁再删除")
    if not db.remove_emoji(account_id, eid):
        raise HTTPException(404, "收藏不存在")
    return {"ok": True}


@app.post("/api/emojis/batch-delete")
async def batch_delete_emojis(request: Request, body: EmojiIdsBody) -> dict[str, Any]:
    """表情墙多选批删；锁定行跳过，返回实际删除与跳过数量。"""
    account_id = acc_id(request)
    skipped_locked = db.count_locked_emojis(account_id, body.ids)
    deleted = db.remove_emojis(account_id, body.ids)
    return {"ok": True, "deleted": deleted, "skipped_locked": skipped_locked}


@app.post("/api/emojis/batch-lock")
async def batch_lock_emojis(request: Request, body: EmojiBatchLockBody) -> dict[str, Any]:
    """表情墙多选批量锁定/解锁；别人的/不存在的 id 自动跳过，返回实际命中条数。"""
    updated = db.set_emojis_locked(acc_id(request), body.ids, body.locked)
    return {"ok": True, "updated": updated, "locked": bool(body.locked)}


@app.put("/api/emojis/{eid}")
async def update_emoji(request: Request, eid: int, body: EmojiTagBody) -> dict[str, Any]:
    account_id = acc_id(request)
    row = db.get_emoji(account_id, eid)
    if not row:
        raise HTTPException(404, "表情不存在")
    if row.get("locked"):
        raise HTTPException(409, "已锁定，先解锁再修改")
    tags = ",".join(db.normalize_tags(body.tags))
    db.set_emoji_tags(account_id, eid, tags, body.description)
    return {"ok": True, "emoji": db.get_emoji(account_id, eid)}


@app.post("/api/emojis/{eid}/retag")
async def retag_emoji(request: Request, eid: int) -> dict[str, Any]:
    """AI 重打标（覆盖写）：编排放端点里，不走 manager 的自动打标链路。"""
    from .ai import tagger
    from .emoji import cache_path

    session = session_of(request)
    account_id = acc_id(request)
    row = db.get_emoji(account_id, eid)
    if not row:
        raise HTTPException(404, "表情不存在")
    # 锁校验排在模型调用与缓存检查之前：锁定行绝不能走到下面的 reject_emoji
    if row.get("locked"):
        raise HTTPException(409, "已锁定，先解锁再重新打标")
    if not getattr(session.llm, "configured", False):
        raise HTTPException(400, "未配置模型，无法 AI 打标")
    path = cache_path(row["url"], str(row.get("md5") or ""))
    if not path.is_file():
        raise HTTPException(400, "图片缓存不存在，请先在表情墙打开这张图再重试")
    vocab = [t for t, _ in db.tag_frequency(account_id)[:120]]
    out = await tagger.tag_emoji(session.llm, str(path), "", vocab=vocab)
    if out.get("not_sticker"):
        # AI 判定根本不是表情包（普通照片/截图等）：删行 + 记拒收名单，下次导入跳过
        from .emoji import discard_emoji_cache

        md5 = str(row.get("md5") or "")
        # 兜底：行在这期间被锁定（或属于别人）时 reject_emoji 返回 None，
        # 此时必须原样返回，不能把别的表情仍在引用的共享缓存文件删掉。
        if db.reject_emoji(account_id, eid, "AI 打标判定为非表情包图片") is None:
            raise HTTPException(409, "已锁定，先解锁再删除")
        if not db.any_emoji_with_md5(md5, exclude_account=account_id):
            await asyncio.to_thread(discard_emoji_cache, md5)
        return {"ok": True, "rejected": True}
    tags = out.get("tags") or []
    if not tags:
        raise HTTPException(502, "模型未返回标签，请重试")
    db.set_emoji_tags(account_id, eid, ",".join(tags), str(out.get("description") or ""))
    return {"ok": True, "emoji": db.get_emoji(account_id, eid)}


@app.post("/api/emojis/consolidate-tags")
async def consolidate_emoji_tags(request: Request) -> dict[str, Any]:
    """LLM 把同义标签归并；锁定标签与锁定行不参与。"""
    from .ai import tagger

    session = session_of(request)
    account_id = acc_id(request)
    if not getattr(session.llm, "configured", False):
        raise HTTPException(400, "未配置模型，无法整理标签")
    freq = db.tag_frequency(account_id)
    if len(freq) < 2:
        return {"ok": True, "mapping": {}, "changed": 0, "locked_tags_skipped": 0}
    mapping = await tagger.consolidate_tags(session.llm, freq)
    locked_tags = set(db.list_locked_tags(account_id))
    safe = {
        k: v for k, v in (mapping or {}).items()
        if k not in locked_tags and v not in locked_tags
    }
    changed = db.apply_tag_mapping(account_id, safe) if safe else 0
    return {
        "ok": True,
        "mapping": safe,
        "changed": changed,
        "locked_tags_skipped": len(mapping or {}) - len(safe),
    }


@app.get("/api/emoji/tag-locks")
async def get_tag_locks(request: Request) -> dict[str, Any]:
    """当前账号被锁定的标签名（只锁名字，不冻结带该标签的表情）。"""
    return {"ok": True, "tags": db.list_locked_tags(acc_id(request))}


@app.post("/api/emoji/tag-locks")
async def post_tag_lock(request: Request, body: TagLockBody) -> dict[str, Any]:
    """锁定/解锁一个标签名：锁定要求标签当前存在，解锁永远放行。"""
    account_id = acc_id(request)
    parts = db.normalize_tags(body.tag)
    if len(parts) != 1:
        raise HTTPException(400, "标签不能为空，且一次只能锁定一个标签")
    tag = parts[0]
    if body.locked:
        existing = {t for t, _ in db.tag_frequency(account_id)}
        if tag not in existing:
            raise HTTPException(400, "标签不存在")
    return {"ok": True, "tags": db.set_tag_locked(account_id, tag, body.locked)}


@app.get("/api/emoji/proxy")
async def emoji_proxy(request: Request, url: str, md5: str = "", download: int = 0) -> FileResponse:
    """服务端代取微信表情 CDN 图片：展示、下载共用本地缓存。"""
    from .emoji import EmojiURLError, fetch_emoji

    # 不能在这里先 sanitize：QQ 导入的表情 url 是 local:// 占位，fetch_emoji 会在
    # md5 命中本地缓存时先于 URL 校验返回缓存文件，提前校验会把这类表情全判成 400。
    try:
        path = await asyncio.to_thread(fetch_emoji, url, md5)
    except EmojiURLError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"表情下载失败：{exc}")
    headers: dict[str, str] = {"Cache-Control": "private, max-age=86400"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="emoji{path.suffix}"'
    return FileResponse(path, headers=headers)


async def _stream_export_file(path: Path) -> AsyncIterator[bytes]:
    """流式发送导出包，客户端中断时在 finally 里删掉临时文件。"""
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
    finally:
        path.unlink(missing_ok=True)


class EmojiExportBody(BaseModel):
    ids: list[int] = []
    all: bool = False


@app.post("/api/emojis/batch-zip")
async def batch_zip_emojis(request: Request, body: EmojiExportBody) -> StreamingResponse:
    """表情墙批量导出：图片 + manifest.json（标签/描述），可选选中或全部。

    fetch_emoji 对 md5 命中缓存的 local:// 占位链接先于 URL 校验返回，
    QQ/导入的表情无需真实链接也能打包。写临时文件再流式返回，避免全库塞进内存。
    """
    from .emoji import build_export_zip

    account_id = acc_id(request)
    if body.all:
        rows = db.list_all_emojis(account_id)
    else:
        rows = [r for eid in body.ids if (r := db.get_emoji(account_id, int(eid)))]
    if not rows:
        raise HTTPException(404, "没有可导出的表情")

    tmp = tempfile.NamedTemporaryFile(prefix="emojis-", suffix=".zip", delete=False)
    tmp.close()
    path = Path(tmp.name)
    try:
        stats = await asyncio.to_thread(build_export_zip, rows, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if not stats["packed"]:
        path.unlink(missing_ok=True)
        raise HTTPException(502, "表情图片都取不到，请稍后重试")
    filename = f"emojis-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        _stream_export_file(path),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(path.stat().st_size),
            "X-Emoji-Skipped": str(stats["skipped"]),
            "X-Emoji-Packed": str(stats["packed"]),
        },
    )


@app.post("/api/emojis/import")
async def import_emojis(
    request: Request,
    files: list[UploadFile] = File(...),
    tags: str = Form(""),
) -> dict[str, Any]:
    """批量导入 zip / 散图；zip 按 manifest 还原标签，散图用表单 tags 统一打标。"""
    from .emoji import EmojiImportError, IMPORT_MAX_FILES, import_upload, new_import_budget

    account_id = acc_id(request)
    if len(files) > IMPORT_MAX_FILES:
        raise HTTPException(400, f"单次最多导入 {IMPORT_MAX_FILES} 个文件，请分批导入")
    totals = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    budget = new_import_budget()
    errors: list[str] = []
    for upload in files:
        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
        try:
            await asyncio.to_thread(
                import_upload,
                upload.file,
                upload.filename or "",
                account_id,
                tags,
                budget,
                stats,
            )
        except EmojiImportError as exc:
            errors.append(str(exc))
            totals["skipped"] += 1
        except Exception as exc:
            log.warning("表情导入失败 %s: %s", upload.filename, exc)
            errors.append(f"{upload.filename or '文件'}：{exc}")
            totals["skipped"] += 1
        finally:
            # 中途因限额/坏条目抛错时，已经入库的部分也要计入结果
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + int(value)

    if not (totals["added"] or totals["updated"] or totals["unchanged"]):
        detail = errors[0] if errors else "没有可导入的图片（支持 zip 压缩包和 JPG/PNG/GIF/WebP）"
        raise HTTPException(400, detail)
    return {"ok": True, **totals}


@app.post("/api/chats/{uid}/send-emoji")
async def send_emoji(request: Request, uid: str, body: EmojiBody) -> dict[str, Any]:
    session = session_of(request)
    try:
        record = await session.send_emoji(
            uid,
            {"url": body.url, "md5": body.md5, "width": body.width, "height": body.height},
            source="human",
        )
    except Exception as exc:
        raise HTTPException(500, str(exc))
    pause = int(config.load()["auto"].get("takeover_pause_min", 0) or 0)
    if pause:
        db.set_chat_pause(uid, time.time() + pause * 60)
    return {"ok": True, "message": record}


# ---------------------------------------------------------------- 朋友圈
class MomentCommentBody(BaseModel):
    content: str
    reply_to: str = ""


def _self_wxid_of(account_id: str) -> str:
    session = hub.get(account_id)
    return str(getattr(getattr(session, "driver", None), "_self_wxid", "") or "")


def _moments_of(request: Request):
    session = session_of(request)
    service = getattr(session, "moments", None)
    if service is None:
        raise HTTPException(400, "当前通道不支持朋友圈：仅微信 4.x 数据库通道可用")
    return session, service


@app.get("/api/moments")
async def moments_list(
    request: Request, keyword: str = "", limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    account_id = acc_id(request)
    session = hub.get(account_id)
    service = getattr(session, "moments", None)
    return {
        "items": db.list_moments(
            account_id,
            keyword=keyword.strip(),
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            self_wxid=_self_wxid_of(account_id),
        ),
        "total": db.count_moments(account_id, keyword=keyword.strip()),
        "unread_interactions": db.count_unread_interactions(account_id),
        "status": service.status() if service else None,
    }


@app.get("/api/moments/interactions")
async def moments_interactions(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    return db.list_moment_interactions(acc_id(request), limit=max(1, min(int(limit), 200)))


@app.post("/api/moments/interactions/seen")
async def moments_interactions_seen(request: Request) -> dict[str, Any]:
    marked = db.mark_moment_interactions_seen(acc_id(request))
    return {"ok": True, "marked": marked}


class MomentSyncBody(BaseModel):
    deep: bool = True  # 默认打开微信朋友圈页让客户端拉一次最新动态，再读库


@app.post("/api/moments/sync")
async def moments_sync(
    request: Request, body: MomentSyncBody | None = None
) -> dict[str, Any]:
    _, service = _moments_of(request)
    deep = True if body is None else bool(body.deep)
    try:
        stat = await asyncio.to_thread(service.refresh, deep)
    except Exception as exc:
        raise HTTPException(502, f"同步失败：{exc}")
    return {"ok": True, **stat}


@app.post("/api/moments/{feed_id}/like")
async def moments_like(request: Request, feed_id: str) -> dict[str, Any]:
    _, service = _moments_of(request)
    feed = db.get_moment(acc_id(request), feed_id)
    if not feed:
        raise HTTPException(404, "朋友圈动态不存在（可能还没同步）")
    try:
        result = await asyncio.to_thread(service.like, feed)
    except Exception as exc:
        raise HTTPException(502, f"点赞失败：{exc}")
    if not result.get("ok"):
        raise HTTPException(502, result.get("message") or "点赞未成功")
    return result


@app.post("/api/moments/{feed_id}/comment")
async def moments_comment(request: Request, feed_id: str, body: MomentCommentBody) -> dict[str, Any]:
    _, service = _moments_of(request)
    feed = db.get_moment(acc_id(request), feed_id)
    if not feed:
        raise HTTPException(404, "朋友圈动态不存在（可能还没同步）")
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "评论内容不能为空")
    try:
        result = await asyncio.to_thread(
            service.comment, feed, content, body.reply_to.strip()
        )
    except Exception as exc:
        raise HTTPException(502, f"评论失败：{exc}")
    if not result.get("ok"):
        raise HTTPException(502, result.get("message") or "评论未成功")
    return result


@app.get("/api/moments/{feed_id}/media/{index}")
async def moments_media(request: Request, feed_id: str, index: int, kind: str = "image") -> FileResponse:
    if kind not in ("image", "video"):
        raise HTTPException(400, "kind 只支持 image/video")
    account_id = acc_id(request)
    session = hub.get(account_id)
    service = getattr(session, "moments", None)
    if service is None:
        raise HTTPException(400, "当前通道不支持朋友圈媒体")
    feed = db.get_moment(account_id, feed_id)
    if not feed:
        raise HTTPException(404, "朋友圈动态不存在")
    try:
        path = await asyncio.to_thread(service.download_media, feed, int(index), kind)
    except Exception as exc:
        raise HTTPException(502, f"读取媒体失败：{exc}")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "本机没有这条媒体：朋友圈图片只在本机缓存或 CDN 还有效时可取")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})



class ToggleBody(BaseModel):
    enabled: bool


class BatchAIBody(BaseModel):
    uids: list[str]
    enabled: bool


@app.post("/api/chats/batch-ai")
async def batch_ai(body: BatchAIBody) -> dict[str, Any]:
    changed = 0
    for uid in body.uids:
        if db.get_chat(uid):
            db.set_chat_ai(uid, body.enabled)
            changed += 1
    bus.publish({"type": "chats_dirty", "data": {"account_id": acc_id_for_batch(body.uids)}})
    return {"ok": True, "changed": changed, "ai_enabled": body.enabled}


def acc_id_for_batch(uids: list[str]) -> str:
    chat = db.get_chat(uids[0]) if uids else None
    return chat["account_id"] if chat else ""


@app.post("/api/chats/{uid}/ai")
async def toggle_ai(uid: str, body: ToggleBody) -> dict[str, Any]:
    db.set_chat_ai(uid, body.enabled)
    chat = db.get_chat(uid)
    bus.publish({"type": "chats_dirty", "data": {"account_id": chat["account_id"] if chat else ""}})
    return {"ok": True, "ai_enabled": body.enabled}


@app.post("/api/chats/{uid}/resume")
async def resume(uid: str) -> dict[str, Any]:
    db.set_chat_pause(uid, 0)
    chat = db.get_chat(uid)
    bus.publish({"type": "chats_dirty", "data": {"account_id": chat["account_id"] if chat else ""}})
    return {"ok": True}


@app.get("/api/events")
async def events(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    return db.list_events(acc_id(request), limit)


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return config.public()


@app.put("/api/settings")
async def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = config.save(patch)
    bus.publish({"type": "settings", "data": config.public()})
    return {"ok": True, "settings": config.public(), "saved": bool(cfg)}


@app.get("/api/capabilities")
async def get_capabilities() -> dict[str, Any]:
    return capability_registry.status()


@app.post("/api/capabilities/reload")
async def reload_capabilities() -> dict[str, Any]:
    await capability_registry.reload()
    bus.publish({"type": "capabilities", "data": capability_registry.status()})
    return {"ok": True, **capability_registry.status()}


def _allowed_mcp_config(path: Path) -> bool:
    resolved = path.resolve()
    if resolved.is_relative_to(config.DATA_DIR.resolve()):
        return True
    return resolved.name in (".mcp.json", "mcp.json") and resolved.parent == config.ROOT.resolve()


_MCP_SECRET_HINTS = ("authorization", "token", "secret", "api-key", "apikey", "password", "cookie")


def _is_secret_header(key: object) -> bool:
    low = str(key).lower().replace("_", "-")
    return any(hint in low for hint in _MCP_SECRET_HINTS)


def _mask_mcp_secrets(text: str) -> str:
    """mcp.json 里的凭据头只回显后 4 位：整份文件原来会被原样 GET 回去。"""
    try:
        data = json.loads(text or "{}")
    except Exception:  # noqa: BLE001 - 读不出 JSON 就原样回，让前端报解析错
        return text
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return text
    for entry in servers.values():
        headers = entry.get("headers") if isinstance(entry, dict) else None
        if not isinstance(headers, dict):
            continue
        for k, v in headers.items():
            value = str(v or "")
            if _is_secret_header(k) and value and not value.startswith("•"):
                headers[k] = "•" * 8 + value[-4:]
    return json.dumps(data, ensure_ascii=False, indent=2)


def _restore_mcp_secrets(data: dict[str, Any], previous_text: str) -> dict[str, Any]:
    """保存时把没动过的 • 串换回上一次的真值，避免编辑器里的脱敏串覆盖凭据。"""
    try:
        old = json.loads(previous_text or "{}")
    except Exception:  # noqa: BLE001
        return data
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    old_servers = old.get("mcpServers") if isinstance(old, dict) else None
    if not isinstance(servers, dict) or not isinstance(old_servers, dict):
        return data
    for name, entry in servers.items():
        prev = old_servers.get(name)
        headers = entry.get("headers") if isinstance(entry, dict) else None
        if not isinstance(headers, dict) or not isinstance(prev, dict):
            continue
        prev_headers = prev.get("headers")
        if not isinstance(prev_headers, dict):
            continue
        for k, v in headers.items():
            if str(v or "").startswith("•") and k in prev_headers:
                headers[k] = prev_headers[k]
    return data


@app.get("/api/capabilities/mcp-config")
async def get_mcp_config() -> dict[str, Any]:
    path = capability_registry.mcp_config_path()
    if not _allowed_mcp_config(path):
        raise HTTPException(400, "MCP 配置只能放在 data/ 目录或项目根的 .mcp.json")
    text = ""
    if path.exists():
        try:
            text = path.read_text("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"读取失败：{exc}") from exc
    return {"path": str(path), "text": _mask_mcp_secrets(text)}


class McpConfigBody(BaseModel):
    text: str


@app.put("/api/capabilities/mcp-config")
async def put_mcp_config(body: McpConfigBody) -> dict[str, Any]:
    try:
        data = json.loads(body.text or "{}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"JSON 不合法：{exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "MCP 配置必须是 JSON 对象")
    path = capability_registry.mcp_config_path()
    if not _allowed_mcp_config(path):
        raise HTTPException(400, "MCP 配置只能放在 data/ 目录或项目根的 .mcp.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text("utf-8") if path.exists() else ""
    data = _restore_mcp_secrets(data, previous)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    await capability_registry.reload()
    return {"ok": True, "path": str(path), **capability_registry.status()}


class CapabilitySettingsBody(BaseModel):
    enabled: bool | None = None
    auto_reply_enabled: bool | None = None
    console_enabled: bool | None = None
    skills_in_prompt: bool | None = None


@app.put("/api/capabilities/settings")
async def put_capability_settings(body: CapabilitySettingsBody) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    config.save({"capabilities": patch})
    await capability_registry.reload()
    return {"ok": True, **capability_registry.status()}


@app.get("/api/capabilities/skills/{name}")
async def get_skill(name: str) -> dict[str, Any]:
    if not capability_registry.skills:
        raise HTTPException(404, "技能系统未启用")
    skill = capability_registry.skills.get(name)
    if not skill:
        raise HTTPException(404, "技能不存在")
    return skill.public(with_body=True)


@app.post("/api/capabilities/mcp/{name}/test")
async def test_mcp_server(name: str) -> dict[str, Any]:
    if not capability_registry.mcp:
        raise HTTPException(404, "MCP 未启用")
    client = capability_registry.mcp.clients.get(name)
    if not client:
        item = next(
            (i for i in capability_registry.mcp.status()["items"] if i.get("name") == name),
            None,
        )
        if not item:
            raise HTTPException(404, "MCP 服务器不存在")
        return {"ok": False, "status": item}
    return {"ok": bool(client.connected), "status": client.status()}


class TestBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class PersonaBody(BaseModel):
    name: str
    persona_name: str = ""
    system_prompt: str = ""


@app.get("/api/personas")
async def get_personas() -> dict[str, Any]:
    return {"ok": True, "personas": config.list_personas()}


@app.post("/api/personas")
async def post_persona(body: PersonaBody) -> dict[str, Any]:
    try:
        config.save_persona(body.name, body.persona_name, body.system_prompt)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "personas": config.list_personas()}


@app.delete("/api/personas/{name}")
async def delete_persona(name: str) -> dict[str, Any]:
    if not config.delete_persona(name):
        raise HTTPException(404, "人格文件不存在")
    return {"ok": True, "personas": config.list_personas()}


@app.post("/api/personas/sync_wiki")
async def post_personas_sync_wiki() -> dict[str, Any]:
    """从图集生成全体 AI 娘人设预设：新建/更新图集来源的文件，跳过用户自建同名。"""
    root = moe_wiki.wiki_root()
    if not root:
        raise HTTPException(400, "图集不可用（ai.moe_wiki_path 未配置或目录不存在）")
    created = updated = 0
    skipped: list[str] = []
    for preset in moe_wiki.build_persona_presets(root):
        existing = config.read_persona(preset["name"])
        if existing and existing.get("source") != moe_wiki.PERSONA_SOURCE:
            skipped.append(preset["name"])
            continue
        config.save_persona(
            preset["name"],
            preset["persona_name"],
            preset["system_prompt"],
            source=preset["source"],
        )
        if existing:
            updated += 1
        else:
            created += 1
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "personas": config.list_personas(),
    }


@app.post("/api/settings/test-ai")
async def test_ai(body: TestBody) -> dict[str, Any]:
    if body.base_url or body.api_key or body.model:
        patch: dict[str, Any] = {"ai": {}}
        if body.base_url:
            patch["ai"]["base_url"] = body.base_url
        if body.api_key:
            patch["ai"]["api_key"] = body.api_key
        if body.model:
            patch["ai"]["model"] = body.model
        config.save(patch)
    if not llm.configured:
        raise HTTPException(400, "请先填写 API Key")
    started = time.time()
    try:
        result = await Agent(llm, []).run(
            [{"role": "user", "content": "只回复两个字：可用"}], temperature=0, max_tokens=20
        )
    except LLMError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "latency_ms": int((time.time() - started) * 1000),
        "reply": result.get("text", ""),
        "model": config.load()["ai"]["model"],
    }


@app.get("/api/diagnose")
async def get_diagnose(request: Request) -> dict[str, Any]:
    session = hub.get(acc_id(request))
    st = await session.status() if session else None
    return diagnose(config.load(), st)


class RestartBody(BaseModel):
    driver: str | None = None


@app.post("/api/driver/restart")
async def restart_driver(request: Request, body: RestartBody) -> dict[str, Any]:
    aid = acc_id(request)
    acc = config.account(aid)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if body.driver:
        config.upsert_account({"id": aid, "driver": body.driver})
    st = await hub.reload_account(aid)
    return {"ok": bool(st and st.get("connected")), "status": st}


class ShutdownBody(BaseModel):
    reason: str | None = None


@app.get("/api/update/check")
async def check_update(force: int = 0) -> dict[str, Any]:
    """对比本地 git 提交与 Gitee 远端 master；force=1 跳过缓存强制检查。"""
    return await asyncio.to_thread(update.check, force=bool(force))


@app.post("/api/server/shutdown")
async def shutdown_server(body: ShutdownBody | None = None) -> dict[str, Any]:
    """一键关闭：保存数据后结束后端进程。"""
    reason = ((body.reason if body else None) or "控制台一键关闭").strip()[:200]
    _exit_later(reason, delay=0.8)
    return {"ok": True, "message": "已保存，服务正在关闭"}


@app.post("/api/server/restart")
async def restart_server(body: ShutdownBody | None = None) -> dict[str, Any]:
    """一键重启：保存数据 → 退出 → 分离进程自动重新拉起 run.bat。"""
    reason = ((body.reason if body else None) or "控制台一键重启").strip()[:200]
    SHUTDOWN["restart"] = True
    _exit_later(reason, delay=0.8)
    return {"ok": True, "message": "已保存，服务正在重启，约 10-20 秒后自动恢复"}


@app.post("/api/webhook/inbound")
async def webhook_inbound(payload: dict[str, Any]) -> dict[str, Any]:
    aid = str(payload.get("account") or "")
    session = hub.get(aid) if aid else None
    # account 只是路由标识，不是凭据：该账号配了 token 就必须带上，
    # 否则「指定 account」会成为绕过 token 的注入口
    if session is not None:
        expected = str((session.account.get("webhook") or {}).get("token") or "")
        if expected and not _token_match(str(payload.get("token") or ""), expected):
            raise HTTPException(403, "该账号已配置 webhook token，入站消息必须带匹配的 token")
    else:
        token = str(payload.get("token") or "")
        candidates = [
            s for s in hub.sessions.values()
            if (s.account.get("webhook") or {}).get("token")
            and _token_match(str((s.account.get("webhook") or {}).get("token")), token)
        ]
        if len(candidates) == 1:
            session = candidates[0]

    if session is None:
        raise HTTPException(400, "无法确定入站消息属于哪个账号，请在 payload 里带 account 或 token")
    if not hasattr(session.driver, "inject"):
        raise HTTPException(400, "当前账号不是 webhook 通道")
    msg = session.driver.inject(payload)
    return {"ok": True, "id": msg.msg_id, "account_id": session.account_id}


_CONSOLE_CHUANZI_BLOCK = (
    "【串子模式已开启】\n"
    "设置里的「串子模式」当前是开的：微信自动回复会按串子方式接话。\n"
    "{site}\n"
    "你在控制台里也能直接复用这些素材：用户要串子/台词库/站内截图时，"
    "先用 search_emojis 搜「串子站」列出站内素材，再按对话挑一张"
    "（也可以加台词关键词），挑中后用 send_wechat_emoji 发。\n"
    "控制台没有查串子站的工具：search_emojis 只查本地库，"
    "find_emoji_online 仍走 Bing/斗图啦/发表情。不要声称自己查过串子站；"
    "本地没有合适素材就如实说，别编台词或假图。"
)

_CONSOLE_SITE_ON = (
    "「串子站表情包」开关当前开着：自动回复会联网查 searchanythingfun 的台词/截图，"
    "这类图入库后 tags 带「串子站」，默认锁定（只禁改标签/删除，发送不受影响）。"
)
_CONSOLE_SITE_OFF = (
    "「串子站表情包」开关当前关闭：自动回复不会联网查站或新增站内图，"
    "只用本地已入库的素材（含以前入库、tags 带「串子站」的图；"
    "锁定只禁改标签/删除，发送不受影响）。"
)


def _console_chuanzi_block(cfg: dict) -> str:
    site = chuanzi.site_enabled(cfg)
    return _CONSOLE_CHUANZI_BLOCK.format(
        site=_CONSOLE_SITE_ON if site else _CONSOLE_SITE_OFF
    )


def _console_prompt(
    session: AccountSession,
    memory_block: str | None = None,
    query: str = "",
) -> str:
    cfg = config.load()
    st = session.driver.status() if session.driver else {"display_name": "未连接", "connected": False}
    persona_prompt, persona_name = config.persona_of(session.account)
    text = (
        f"你是「{persona_name or 'Deep肥鱼'}」，一个有独立人格的个体，掌管着自己的微信账号。"
        f"当前账号是「{session.name}」（昵称 {session.nickname}），"
        f"通道：{st.get('display_name')}（{'已连接' if st.get('connected') else '未连接'}）。\n"
        "你可以调用工具查询会话、读取聊天记录、发送微信消息或文件。"
        "用户问账户余额、今日花费或上一轮成本时，调用 get_whale_balance / get_last_turn_cost，不要凭记忆猜数字。"
        "回答用中文，简洁直接；调用工具前先确认会话名称准确，发送消息后简要汇报结果。"
        "涉及群发、敏感内容时先向用户确认。"
    )
    if chuanzi.enabled(cfg):
        text += "\n\n" + _console_chuanzi_block(cfg)
    growth_block = growth_prompt.block(
        session.account_id,
        query=query,
        include_chat_scoped=False,
    )
    if growth_block:
        text += "\n\n" + growth_block
    cap_block = capability_registry.prompt_block()
    if cap_block:
        text += "\n\n" + cap_block
    if cfg["ai"].get("memory_enabled", True):
        mem = memory_block
        if mem is None:
            mem = memory.inject_block(
                session.account_id,
                "",
                limit=int(
                    (cfg.get("memory") or {}).get("max_items")
                    or cfg["ai"].get("memory_max", 12)
                ),
                query=query,
            )
        if mem:
            text += "\n\n" + mem
    wiki_self = moe_wiki.self_block()
    if wiki_self:
        text += "\n\n" + wiki_self
    mood_block = mood_service.inject_block(session.account_id, "")
    if mood_block:
        text += "\n\n" + mood_block
    return text


def _console_prompt_compat(
    session: AccountSession, memory_block: str | None, query: str
) -> str:
    """测试会 monkeypatch 成只收 session 的 lambda；按签名兼容两种形态。"""
    try:
        params = inspect.signature(_console_prompt).parameters
        accepts = "memory_block" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (TypeError, ValueError):
        accepts = True
    if accepts:
        return _console_prompt(session, memory_block=memory_block, query=query)
    return _console_prompt(session)


async def _console_extract(session: AccountSession, user_text: str, answer: str) -> None:
    """控制台回合结束后后台提取长期记忆。"""
    transcript = f"用户：{user_text.strip()[:500]}\n我：{answer.strip()[:800]}"
    try:
        changes = await memory.extract_and_store(
            llm, session.account_id, "", f"控制台（{session.name}）", transcript
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("控制台记忆提取异常: %s", exc)
        return
    if changes:
        db.add_event(
            session.account_id,
            "memory",
            f"控制台的记忆更新了 {len(changes)} 条",
            "；".join(changes)[:200],
        )


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class ChatBody(BaseModel):
    text: str = ""
    conversation_id: str | None = None
    model: str | None = None
    effort: str | None = None
    images: list[str] | None = None
    display_text: str | None = None


def _fallback_models(kind: str) -> list[str]:
    if kind == "deepseek":
        return ["deepseek-chat", "deepseek-reasoner"]
    if kind == "mimo":
        return ["mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed"]
    return []


def _title_of(text: str) -> str:
    return (" ".join(text.split()) or "新对话")[:28]


def _image_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def _tool_result_text(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return raw[:4000] + "…" if len(raw) > 4000 else raw


@app.get("/api/agent/models")
async def agent_models() -> dict[str, Any]:
    current = (config.load()["ai"].get("model") or "").strip()
    live = await llm.list_models() if llm.configured else []
    ids: list[str] = []
    if current:
        ids.append(current)
    for mid in live:
        if mid not in ids:
            ids.append(mid)
    if not live:
        for mid in _fallback_models(llm.provider_kind(current)):
            if mid not in ids:
                ids.append(mid)
    return {
        "current": current,
        "provider": llm.provider_kind(current),
        "live": bool(live),
        "models": [{"id": mid, "efforts": llm.effort_options(mid)} for mid in ids],
    }


@app.post("/api/agent/upload")
async def agent_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(400, "空文件")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "图片不能超过 8MB")
    ext = _image_ext(data)
    if not ext:
        raise HTTPException(400, "只支持 JPG / PNG / GIF / WebP 图片")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + ext
    (UPLOAD_DIR / name).write_bytes(data)
    return {"url": f"/api/agent/uploads/{name}", "name": file.filename or name}


@app.get("/api/agent/uploads/{name}")
async def agent_upload_file(name: str) -> FileResponse:
    path = resolve_upload_ref(f"/api/agent/uploads/{name}", UPLOAD_DIR)
    if not path or not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})


@app.get("/api/agent/conversations")
async def agent_conversations(request: Request) -> list[dict[str, Any]]:
    return db.list_agent_conversations(acc_id(request))


@app.get("/api/agent/conversations/{cid}")
async def agent_conversation(cid: str, request: Request) -> dict[str, Any]:
    conv = db.get_agent_conversation(cid)
    if not conv or conv["account_id"] != acc_id(request):
        raise HTTPException(404, "对话不存在")
    return {"conversation": conv, "turns": db.list_agent_turns(cid)}


@app.delete("/api/agent/conversations/{cid}")
async def remove_agent_conversation(cid: str, request: Request) -> dict[str, Any]:
    if not db.delete_agent_conversation(cid, acc_id(request)):
        raise HTTPException(404, "对话不存在")
    return {"ok": True}


class MemoryBody(BaseModel):
    content: str
    kind: str = "fact"
    importance: int = 2
    chat_uid: str = ""
    layer: str | None = None
    subject: str = "contact"
    confidence: float = 0.8
    pinned: bool = False


class MemoryUpdateBody(BaseModel):
    content: str | None = None
    kind: str | None = None
    importance: int | None = None
    layer: str | None = None
    subject: str | None = None
    confidence: float | None = None
    pinned: bool | None = None


@app.get("/api/accounts/{account_id}/memories")
async def list_account_memories(
    account_id: str,
    request: Request,
    keyword: str = "",
    layer: str = "",
    kind: str = "",
    chat_uid: str | None = None,
    pinned: bool | None = None,
):
    if account_id != acc_id(request) and not config.account(account_id):
        raise HTTPException(404, "账号不存在")
    uid = (chat_uid or "").strip() if chat_uid is not None else None
    return {
        "items": db.list_memories(
            account_id,
            keyword=keyword.strip(),
            layer=(layer or "").strip(),
            kind=(kind or "").strip(),
            chat_uid=uid,
            pinned=pinned,
        ),
        "stats": db.memory_stats(account_id),
    }


def _refresh_profile_quiet(account_id: str, chat_uid: str = "") -> None:
    try:
        memory.refresh_profiles_for_change(account_id, chat_uid or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("刷新记忆档案失败 %s/%s: %s", account_id, chat_uid, exc)


@app.post("/api/accounts/{account_id}/memories")
async def add_account_memory(account_id: str, body: MemoryBody, request: Request):
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    row = db.add_memory(
        account_id,
        content,
        chat_uid=body.chat_uid.strip(),
        kind=body.kind.strip() or "fact",
        importance=body.importance,
        layer=body.layer,
        subject=body.subject,
        confidence=body.confidence,
        pinned=body.pinned,
        source="manual",
    )
    _refresh_profile_quiet(account_id, row.get("chat_uid") or "")
    return row


class MemoryIdsBody(BaseModel):
    ids: list[int] = []


@app.post("/api/accounts/{account_id}/memories/batch-delete")
async def batch_delete_memories(account_id: str, body: MemoryIdsBody, request: Request):
    """记忆页多选批删；别人的/不存在的 id 自动跳过。"""
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    rows = [db.get_memory(account_id, int(mid)) for mid in body.ids if str(mid).isdigit()]
    deleted = db.remove_memories(account_id, body.ids)
    for uid in {str(r.get("chat_uid") or "") for r in rows if r}:
        _refresh_profile_quiet(account_id, uid)
    return {"ok": True, "deleted": deleted}


@app.put("/api/accounts/{account_id}/memories/{mid}")
async def update_account_memory(account_id: str, mid: int, body: MemoryUpdateBody, request: Request):
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    old = db.get_memory(account_id, mid)
    if not db.update_memory(
        account_id,
        mid,
        content=body.content,
        kind=body.kind,
        importance=body.importance,
        layer=body.layer,
        subject=body.subject,
        confidence=body.confidence,
        pinned=body.pinned,
    ):
        raise HTTPException(404, "记忆不存在")
    _refresh_profile_quiet(account_id, (old or {}).get("chat_uid") or "")
    return {"ok": True, "memory": db.get_memory(account_id, mid)}


@app.delete("/api/accounts/{account_id}/memories/{mid}")
async def delete_account_memory(account_id: str, mid: int, request: Request):
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    old = db.get_memory(account_id, mid)
    if not db.delete_memory(account_id, mid):
        raise HTTPException(404, "记忆不存在")
    _refresh_profile_quiet(account_id, (old or {}).get("chat_uid") or "")
    return {"ok": True}


@app.get("/api/accounts/{account_id}/memory-profiles")
async def list_account_memory_profiles(account_id: str, request: Request):
    if account_id != acc_id(request) and not config.account(account_id):
        raise HTTPException(404, "账号不存在")
    return {
        "items": db.list_memory_profiles(account_id),
        "stats": db.memory_stats(account_id),
    }


@app.post("/api/accounts/{account_id}/memory-profiles/{chat_uid}/rebuild")
async def rebuild_account_memory_profile(account_id: str, chat_uid: str, request: Request):
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    profile = memory.refresh_profile(account_id, chat_uid)
    if not profile:
        raise HTTPException(404, "这个会话还没有可整理的记忆")
    return {"ok": True, "profile": profile}


@app.post("/api/accounts/{account_id}/memories/consolidate")
async def consolidate_account_memories(account_id: str, request: Request):
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    result = await asyncio.to_thread(memory.consolidate, account_id)
    return {"ok": True, **result}


@app.post("/api/accounts/{account_id}/memories/reindex")
async def reindex_account_memories(account_id: str, request: Request, force: bool = False):
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    result = await memory.rebuild_embeddings(account_id, force=force)
    return result


class MemoryImportBody(BaseModel):
    items: list[dict[str, Any]] = []


@app.post("/api/accounts/{account_id}/memories/import")
async def import_account_memories(account_id: str, body: MemoryImportBody, request: Request):
    """从导出的 JSON 恢复记忆；同内容同作用域会合并，不重复插入。"""
    if account_id != acc_id(request):
        raise HTTPException(400, "只能管理当前账号的记忆")
    added = 0
    touched: set[str] = set()
    for item in body.items[:1000]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        uid = str(item.get("chat_uid") or "").strip()
        if db.find_memory_like(account_id, content, uid):
            continue
        try:
            importance = max(1, min(3, int(item.get("importance") or 2)))
        except (TypeError, ValueError):
            importance = 2
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.8)))
        except (TypeError, ValueError):
            confidence = 0.8
        db.add_memory(
            account_id,
            content,
            chat_uid=uid,
            kind=str(item.get("kind") or "fact"),
            importance=importance,
            layer=item.get("layer"),
            subject=str(item.get("subject") or "contact"),
            confidence=confidence,
            pinned=bool(item.get("pinned")),
            source="import",
            meta=item.get("meta") if isinstance(item.get("meta"), dict) else None,
        )
        added += 1
        touched.add(uid)
    for uid in touched:
        _refresh_profile_quiet(account_id, uid)
    return {"ok": True, "added": added}


@app.post("/api/agent/chat")
async def agent_chat(request: Request, body: ChatBody) -> StreamingResponse:
    refs = [str(x) for x in (body.images or []) if isinstance(x, str)][:4]
    image_paths: list[str] = []
    for ref in refs:
        path = resolve_upload_ref(ref, UPLOAD_DIR)
        if not path or not path.is_file():
            raise HTTPException(400, "图片不存在或已失效")
        image_paths.append(str(path))
    text = body.text.strip()
    if image_paths and not config.load()["ai"].get("vision_enabled"):
        raise HTTPException(400, "识图未开启，请到「设置 → 模型」打开")
    if not text and not image_paths:
        raise HTTPException(400, "内容不能为空")
    if not text:
        text = "看看这张图"
    if not llm.configured:
        raise HTTPException(400, "尚未配置 API Key，请到设置页填写")
    session = session_of(request)
    aid = session.account_id
    lock = console_locks.setdefault(aid, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "上一条指令还在处理中，请先停止或稍等")

    cid = (body.conversation_id or "").strip()
    conv = db.get_agent_conversation(cid) if cid else None
    if cid and (not conv or conv["account_id"] != aid):
        raise HTTPException(404, "对话不存在")

    model = (body.model or "").strip()[:100]
    effort = (body.effort or "auto").strip().lower()
    if effort not in EFFORT_VALUES:
        effort = "auto"
    if conv:
        cid = conv["id"]
        if body.model is None:
            model = (conv.get("model") or "").strip()
        if body.effort is None:
            effort = (conv.get("effort") or "auto").strip().lower()
        if (conv.get("title") or "").strip() in ("", "新对话"):
            conv["title"] = _title_of(text)
            db.touch_agent_conversation(cid, title=conv["title"])
    else:
        conv = db.create_agent_conversation(aid, title=_title_of(text), model=model, effort=effort)
        cid = conv["id"]

    meta = {"images": refs} if refs else None
    if (body.display_text or "").strip():
        meta = dict(meta or {})
        meta["display_text"] = body.display_text.strip()[:200]
    user_turn = db.add_agent_turn(
        cid,
        "user",
        content=text,
        meta=meta,
        model=model,
        effort=effort,
    )
    history = db.list_agent_turns(cid, limit=40)
    memory_block = None
    if config.load()["ai"].get("memory_enabled", True):
        try:
            memory_block = await memory.retrieve_context(
                aid,
                "",
                text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("控制台记忆检索失败: %s", exc)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": _console_prompt_compat(session, memory_block, text),
        }
    ]
    # 形象注入：开着识图且配置了形象图时，让控制台对话也能“看见”自己
    if config.load()["ai"].get("vision_enabled"):
        messages.extend(self_image_messages(aid))
    attached = 0
    for turn in history:
        if turn["id"] == user_turn["id"]:
            continue
        if turn["role"] in ("user", "assistant") and turn.get("content"):
            content: Any = turn["content"]
            turn_refs = (turn.get("meta") or {}).get("images") or []
            if turn["role"] == "user" and turn_refs and attached < 8:
                paths = []
                for ref in turn_refs:
                    path = resolve_upload_ref(str(ref), UPLOAD_DIR)
                    if path and path.is_file():
                        paths.append(str(path))
                if paths:
                    content = build_user_content(content, paths)
                    attached += len(paths)
            messages.append({"role": turn["role"], "content": content})
    messages.append({"role": "user", "content": build_user_content(text, image_paths)})
    messages = [messages[0]] + messages[1:][-20:]

    title = conv.get("title") or _title_of(text)
    used_model = model or (llm.cfg.get("model") or "")
    # 思考型模型的 reasoning 和正文抢同一份 max_tokens，800 会被思考吃光，
    # 于是「只有思考不回复」。控制台是长任务入口，起步就给够；再被掐断由 Agent 逐次翻倍。
    agent_max_tokens = max(int(config.load()["ai"].get("max_tokens", 800) or 0), 2048)

    async def gen() -> AsyncIterator[str]:
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        tools_log: list[dict[str, Any]] = []
        note_parts: list[str] = []
        turn_usage: dict[str, Any] = {}
        error_text = ""
        truncated = False
        finished = False

        async def on_event(evt: dict[str, Any]) -> None:
            nonlocal error_text, truncated
            kind = evt.get("type")
            if kind == "delta":
                answer_parts.append(evt.get("text") or "")
            elif kind == "reasoning":
                reasoning_parts.append(evt.get("text") or "")
            elif kind == "tool_start":
                tools_log.append(
                    {
                        "id": evt.get("id") or "",
                        "name": evt.get("name") or "",
                        "args": evt.get("args") or {},
                        "ok": None,
                        "result": "",
                    }
                )
            elif kind == "tool_end":
                target = None
                for item in reversed(tools_log):
                    if evt.get("id") and item.get("id") == evt.get("id"):
                        target = item
                        break
                if target is None:
                    target = {"id": evt.get("id") or "", "name": evt.get("name") or "", "args": {}}
                    tools_log.append(target)
                target["ok"] = bool(evt.get("ok"))
                target["result"] = _tool_result_text(evt.get("result"))
            elif kind == "note":
                note_parts.append(evt.get("text") or "")
            elif kind in ("done", "error"):
                if isinstance(evt.get("usage"), dict):
                    turn_usage.clear()
                    turn_usage.update(evt["usage"])
                if kind == "error":
                    error_text = evt.get("message") or ""
                else:
                    truncated = bool(evt.get("truncated"))

        try:
            yield _sse(
                {
                    "type": "meta",
                    "conversation_id": cid,
                    "title": title,
                    "model": used_model,
                    "effort": effort,
                }
            )
            async with lock:
                agent = Agent(
                    llm,
                    build_tools(
                        session,
                        allow_send=True,
                        shutdown=_request_shutdown,
                        allow_web=bool(config.load()["ai"].get("web_enabled", True)),
                        allow_persona=True,
                        restart=_request_restart,
                        billing_tools=True,
                        conversation_id=cid,
                    ),
                    max_rounds=6,
                )
                async for chunk in _run_agent_stream(
                    agent, messages, on_event, model=model or None, effort=effort,
                    max_tokens=agent_max_tokens,
                ):
                    yield chunk
            finished = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            error_text = error_text or str(exc)
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            answer = "".join(answer_parts)
            reasoning = "".join(reasoning_parts)
            note = "".join(note_parts).strip()
            stopped = not finished and not error_text
            if answer or reasoning or tools_log or error_text or stopped or note:
                turn_meta = {"stopped": stopped, "error": error_text, "truncated": truncated}
                if note:
                    turn_meta["note"] = note
                turn_meta.update(usage.turn_usage_meta(turn_usage))
                db.add_agent_turn(
                    cid,
                    "assistant",
                    content=answer,
                    reasoning=reasoning[:20000],
                    tools=tools_log,
                    meta=turn_meta,
                    model=model,
                    effort=effort,
                )
            db.touch_agent_conversation(cid, model=model, effort=effort)
            if (
                answer
                and not error_text
                and not stopped
                and not truncated
                and config.load()["ai"].get("memory_enabled", True)
            ):
                asyncio.create_task(_console_extract(session, text, answer))
            if answer and not error_text:
                mood_service.observe(
                    session.account_id,
                    "",
                    text,
                    answer,
                    source="console",
                    llm=llm,
                )
            if SHUTDOWN.get("requested"):
                _exit_later(SHUTDOWN.get("reason") or "AI 自主关闭", delay=1.2)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def _run_agent_stream(
    agent: Agent,
    messages: list[dict[str, Any]],
    on_event,
    model: str | None = None,
    effort: str | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(evt: dict[str, Any]) -> None:
        await on_event(evt)
        await queue.put(evt)

    async def runner() -> None:
        try:
            await agent.run(messages, on_event=emit, model=model, effort=effort, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put({"type": "__end__"})

    task = asyncio.create_task(runner())
    try:
        while True:
            evt = await queue.get()
            if evt.get("type") == "__end__":
                break
            yield _sse(evt)
    finally:
        if not task.done():
            task.cancel()


def guard_ws(token_supplied: str, host: str) -> int | None:
    """实时通道准入：返回要关闭的状态码，None 表示放行。"""
    token = (config.load()["server"].get("access_token") or "").strip()
    if token and not _token_match(token_supplied, token):
        return 4401
    if host not in LOCAL_HOSTS:
        return 4403
    return None


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # http 中间件不覆盖 WebSocket：必须在 accept 之前自己校验，accept 之后再拒已经回了 101
    code = guard_ws(
        ws.query_params.get("token") or ws.headers.get("x-access-token") or "",
        ws.client.host if ws.client else "",
    )
    if code is not None:
        await ws.close(code=code)
        return
    await ws.accept()
    queue = bus.subscribe()

    async def pump() -> None:
        try:
            while True:
                evt = await queue.get()
                await ws.send_json(evt)
        except Exception:  # noqa: BLE001
            pass  # 连接已关闭：交给下面的 receive 循环收尾

    pump_task: asyncio.Task | None = None
    try:
        session = hub.get(config.active_id())
        await ws.send_json(
            {
                "type": "hello",
                "data": (await session.status()) if session else None,
                "accounts": await accounts(),
            }
        )
        pump_task = asyncio.create_task(pump())
        # 必须主动等客户端的断开帧：只阻塞在 queue.get() 上收不到服务端关闭信号，
        # uvicorn 优雅关闭（Ctrl+C / --reload）会一直等这个任务，卡在
        # "Waiting for background tasks to complete"
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if pump_task is not None:
            pump_task.cancel()
        bus.unsubscribe(queue)


# ===== dsh-whale 小鲸鱼挂件（vendor/dsh-whale-widget，MIT，素材见其 PROVENANCE.md）=====
# 挂件前端是上游原样文件，约定了一组 /dsh-whale/* 宿主接口。这里实现：
# 余额（代理 DeepSeek /user/balance + 本地用量账本）、形象（image/rua）、
# 设置持久化（size.json）、音效、每轮消耗与用量面板（last-turn / usage-records / usage-settings）、
# 等待交互占位（wait.json，恒返回无挂起，避免每秒轮询刷 404）；
# 其余接口（自定义角色、泡泡编辑、多模型余额等）不实现，前端 fetch 404 后会优雅降级。
WHALE_ASSETS = config.ROOT / "vendor" / "dsh-whale-widget" / "assets"
WHALE_SIZE_FILE = config.DATA_DIR / "whale-size.json"
WHALE_SOUNDS = {"duck": ("Ya1.mp3", "Ya2.mp3"), "fx1": ("D1.mp3", "D2.mp3")}
_WHALE_VERSION = whale.VERSION


def _whale_asset(name: str, media_type: str) -> FileResponse:
    path = WHALE_ASSETS / name
    if not path.is_file():
        raise HTTPException(404, "挂件素材缺失：请先 git submodule update --init vendor/dsh-whale-widget")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@app.get("/dsh-whale/balance.json")
async def whale_balance(refresh: str = "") -> dict[str, Any]:
    return await whale.balance_payload(llm.cfg, refresh=refresh == "1")


def _whale_size_normalize(parsed: Any) -> dict[str, Any] | None:
    """镜像上游 readSizeConfig：scale 缺失视为整份配置无效（返回 None ⇒ 下发仅版本号）。"""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("scale"), (int, float)):
        return None
    scale = float(parsed["scale"])

    def flag(key: str, dflt: bool) -> bool:
        value = parsed.get(key)
        return value if isinstance(value, bool) else dflt

    def num(key: str, dflt: float) -> float:
        value = parsed.get(key)
        return float(value) if isinstance(value, (int, float)) else dflt

    def text(key: str, dflt: str) -> str:
        value = parsed.get(key)
        return value if isinstance(value, str) and value else dflt

    return {
        "scale": scale,
        "sound": flag("sound", True),
        "vol": num("vol", 0.9),
        "soundSet": text("soundSet", "duck"),
        "usageMode": text("usageMode", "ledger"),
        "peakMode": parsed.get("peakMode") if parsed.get("peakMode") in ("liangwen", "qiangqiang") else "default",
        "bubbleOn": flag("bubbleOn", True),
        "pressOn": flag("pressOn", True),
        "turnCostOn": flag("turnCostOn", True),
        "turnCostCloseMs": num("turnCostCloseMs", 5000.0),
        "scrollGapOn": flag("scrollGapOn", False),
        "scrollGapPx": num("scrollGapPx", 17.0),
        "menuBtnHide": flag("menuBtnHide", False),
        "codexStatsOn": flag("codexStatsOn", True),
    }


@app.get("/dsh-whale/size.json")
async def whale_size_get() -> dict[str, Any]:
    try:
        parsed = json.loads(WHALE_SIZE_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        parsed = None
    cfg = _whale_size_normalize(parsed)
    if cfg is None:
        return dict(_WHALE_VERSION)
    return {**cfg, **_WHALE_VERSION}


@app.put("/dsh-whale/size.json")
@app.post("/dsh-whale/size.json")
async def whale_size_put(request: Request) -> dict[str, Any]:
    try:
        parsed = await request.json()
    except Exception:  # noqa: BLE001
        parsed = None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("scale"), (int, float)):
        return {"ok": False, "error": "missing scale"}
    # 旧值必须取自已保存的文件：缺字段一律「沿用现有值」，绝不落默认（上游 issue #97 的教训）
    try:
        saved = json.loads(WHALE_SIZE_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        saved = None
    merged = dict(_whale_size_normalize(saved) or {})
    for key in ("sound", "vol", "soundSet", "usageMode", "peakMode", "bubbleOn", "pressOn",
                "turnCostOn", "turnCostCloseMs", "scrollGapOn", "scrollGapPx", "menuBtnHide", "codexStatsOn"):
        if key in parsed:
            merged[key] = parsed[key]
    merged["scale"] = parsed["scale"]
    cfg = _whale_size_normalize(merged)
    if cfg is None:
        return {"ok": False, "error": "invalid scale"}
    try:
        WHALE_SIZE_FILE.write_text(json.dumps({**cfg, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00")}, ensure_ascii=False), "utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"无法持久化挂件设置: {exc}"}
    return {"ok": True, **cfg}


@app.get("/dsh-whale/widget.js")
async def whale_widget_js() -> FileResponse:
    return _whale_asset("whale-widget.js", "text/javascript")


@app.get("/dsh-whale/last-turn.json")
async def whale_last_turn() -> dict[str, Any]:
    return {"ok": True, **usage.LEDGER.last_turn()}


@app.get("/dsh-whale/wait.json")
async def whale_wait() -> dict[str, Any]:
    # 挂起状态（等待用户提问/授权）本宿主不采集，恒返回"无挂起"：
    # 前端每秒轮询它，返回 404 会持续刷日志且自检项失败；pending 为 null 时前端走降级分支。
    return {"ok": True, "sessionName": "", "pending": None}


@app.get("/dsh-whale/usage-records.json")
async def whale_usage_records() -> dict[str, Any]:
    return usage.LEDGER.records_payload(_WHALE_VERSION)


@app.get("/dsh-whale/usage-settings.json")
async def whale_usage_settings_get() -> dict[str, Any]:
    return {"ok": True, "settings": usage.LEDGER.settings()}


@app.put("/dsh-whale/usage-settings.json")
@app.post("/dsh-whale/usage-settings.json")
async def whale_usage_settings_put(request: Request) -> dict[str, Any]:
    try:
        parsed = await request.json()
    except Exception:  # noqa: BLE001
        parsed = None
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "bad body"}
    return {"ok": True, "settings": usage.LEDGER.save_settings(parsed)}


@app.get("/dsh-whale/image.png")
async def whale_image() -> FileResponse:
    return _whale_asset("DSniang1.png", "image/png")


@app.get("/dsh-whale/rua.gif")
async def whale_rua() -> FileResponse:
    return _whale_asset("rua.gif", "image/gif")


@app.get("/dsh-whale/sound/press.mp3")
async def whale_sound_press(request: Request) -> FileResponse:
    press, _ = WHALE_SOUNDS.get(request.query_params.get("set") or "", WHALE_SOUNDS["duck"])
    return _whale_asset(press, "audio/mpeg")


@app.get("/dsh-whale/sound/release.mp3")
async def whale_sound_release(request: Request) -> FileResponse:
    _, release = WHALE_SOUNDS.get(request.query_params.get("set") or "", WHALE_SOUNDS["duck"])
    return _whale_asset(release, "audio/mpeg")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# 心情挂饰素材：只有六张 PNG，公开挂载在 /api 之外，避免令牌中间件拦掉 <img> 请求。
# check_dir=False：vendor 目录缺失时仍能启动（素材接口另有 404 提示）。
app.mount(
    "/mood-assets",
    StaticFiles(
        directory=str(config.ROOT / "vendor" / "dsh-feiyu-mood" / "assets"),
        check_dir=False,
    ),
    name="mood-assets",
)


def run() -> None:
    import uvicorn

    update.startup_check()  # 启动前检查更新：限时完成，失败不阻塞启动
    cfg = config.load()["server"]
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 8765))
    print(f"Deep肥鱼 v{__version__} · 控制台 http://{host}:{port}")
    if not (cfg.get("access_token") or "").strip():
        print("[提示] 未设置访问令牌：/api 读接口对本机其他进程开放。"
              "若要把服务暴露到非本机，请在「设置 → 服务」填访问令牌。")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[警告] 服务监听在 {host}：写操作已限制为本机来源，但能连到 {port} 的一方"
              "仍可读取聊天与配置。请确认这是你想要的。")
    if "--reload" in sys.argv:
        # 开发用自动重载：列表形式传给 uvicorn，只监听 app 包的 .py，
        # 避免 data/ 下数据库与媒体文件变动引发无谓重启
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            log_level="info",
            reload=True,
            reload_dirs=[str(Path(__file__).parent)],
        )
        return
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
