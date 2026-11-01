"""智能体可调用的微信工具集（绑定到某个账号会话）。"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from pathlib import Path
from dataclasses import replace
from typing import Any, Callable, TYPE_CHECKING

from .. import config, db, whale
from ..emoji import history_content
from ..growth import store as growth_store
from ..wechat.base import sender_label
from . import chuanzi, emoji_search, memory as memory_mod, moe_wiki, tagger, web
from ..capabilities.registry import registry as capability_registry
from .agent import Tool

if TYPE_CHECKING:  # pragma: no cover
    from ..wechat.manager import AccountSession

log = logging.getLogger("tools")


def _sendable_roots() -> list[Path]:
    """允许 AI 发送的目录：联网下载的落点 + 控制台上传的落点。"""
    return [web.DOWNLOAD_DIR, config.DATA_DIR / "agent_uploads"]


def _assert_sendable_path(path: str) -> str:
    """把模型给的路径收敛到 _sendable_roots 之内：路径本身可自由拼接，
    但 resolve 之后必须落在其中一个目录里，否则 .env 与 data/agent.db 都能被发出去。"""
    p = Path(str(path or "")).expanduser()
    roots = [r.resolve() for r in _sendable_roots() if r.exists()]
    if p.is_file():
        p = p.resolve()
        for base in roots:
            if base in p.parents:
                return str(p)
    raise ValueError(
        "不允许发送该路径：只能发送本程序下载或上传目录里的文件"
        f"（{'、'.join(str(r) for r in roots)}）"
    )


def _fmt_ts(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def build_tools(
    session: "AccountSession",
    allow_send: bool = True,
    shutdown: Callable[[str], None] | None = None,
    allow_web: bool = True,
    chat_uid: str = "",
    allow_persona: bool = False,
    restart: Callable[[str], None] | None = None,
    defer_emoji: list[dict[str, Any]] | None = None,
    emoji_tools: bool = True,
    billing_tools: bool = False,
    conversation_id: str = "",
    chuanzi_state: dict[str, Any] | None = None,
    cross: dict[str, Any] | None = None,
    tool_context: str = "console",
) -> list[Tool]:
    chuanzi_mode = chuanzi.enabled()
    # chuanzi_on 只控制「自动查站」：自动回复轮次带 state 才会主动请求站点；
    # 控制台（state=None）仍然享受串子模式的站内素材特权，但不自动耗额度。
    chuanzi_on = chuanzi_mode and chuanzi_state is not None
    # 独立开关「串子站表情包」：只决定本轮是否联网查站/入库，默认关闭。
    # 关掉后本地已入库的「串子站」截图照常检索、发送，站内特权仍跟串子模式走。
    chuanzi_site = chuanzi_on and chuanzi.site_enabled()

    async def list_chats(args: dict[str, Any]) -> Any:
        keyword = (args.get("keyword") or "").strip()
        items = session.visible_chats(keyword=keyword, limit=int(args.get("limit", 20)))
        if chat_uid:
            # 自动回复时只暴露当前会话，防止被套出其他会话的存在和内容
            items = [c for c in items if c.get("uid") == chat_uid]
        return [
            {
                "chat_id": c["chat_id"],
                "name": c["name"],
                "is_group": c["is_group"],
                "last_message": c["last_message"],
                "last_time": _fmt_ts(c["last_ts"]) if c["last_ts"] else "",
            }
            for c in items
        ]

    async def get_history(args: dict[str, Any]) -> Any:
        chat = (args.get("chat") or "").strip()
        if not chat:
            return {"error": "请提供会话名称或 chat_id"}
        resolved = await session.resolve_chat(chat)
        if not resolved:
            return {"error": f"找不到会话「{chat}」，可以先调用 list_wechat_chats 确认名称"}
        if chat_uid and resolved["uid"] != chat_uid:
            return {"error": "自动回复模式下只能查看当前会话的聊天记录"}
        rows = db.list_messages(resolved["uid"], limit=int(args.get("limit", 20)))
        if not rows:
            return {"error": f"没有找到 {chat} 的消息记录", "chat_id": resolved["chat_id"]}
        out = []
        for r in rows:
            if r["is_self"]:
                who = "我"
            elif r.get("is_group"):
                # 群消息带上 wxid：同名群友只有 wxid 能分清
                who = sender_label(r["sender_name"], r.get("sender_id") or "")
            else:
                who = r["sender_name"] or r["chat_name"]
            out.append(
                {
                    "time": _fmt_ts(r["ts"]),
                    "from": who,
                    "text": history_content(session.account_id, r),
                }
            )
        return {
            "chat_id": resolved["chat_id"],
            "chat_name": resolved["name"],
            "messages": out,
        }

    async def send_message(args: dict[str, Any]) -> Any:
        to = (args.get("to") or "").strip()
        content = (args.get("content") or "").strip()
        if not to or not content:
            return {"error": "to 和 content 都不能为空"}
        resolved = await session.resolve_chat(to)
        if not resolved:
            return {"error": f"找不到会话「{to}」，可以先调用 list_wechat_chats 确认名称"}
        await session.send_text(resolved["uid"], content, source="agent")
        return {"ok": True, "to": resolved["name"], "account": session.name, "sent": content}

    async def relay_message(args: dict[str, Any]) -> Any:
        """自动回复里的跨会话代发：解析收件人 → 登记延迟发送（正文发出后统一真发）。"""
        conf = cross or {}
        if not conf.get("relay"):
            return {"error": "跨对话沟通未开启，无法给其他会话发消息"}
        to = (args.get("to") or "").strip()
        content = (args.get("content") or "").strip()
        if not to or not content:
            return {"error": "to 和 content 都不能为空"}
        max_chars = int(conf.get("relay_max_chars", 500) or 0)
        if max_chars and len(content) > max_chars:
            return {"error": f"单条转告最多 {max_chars} 字，请精简后再发"}
        target, ambiguous = await session.resolve_recipient(to)
        if target is None:
            if ambiguous:
                return {
                    "error": f"「{to}」对应多个会话：{'、'.join(ambiguous[:5])}；"
                    "请让对方确认是哪一个（必要时让他发准确备注/群名）"
                }
            return {
                "error": f"找不到「{to}」对应的会话；请让对方给准确的昵称/备注/群名，"
                "或确认你们之前聊过"
            }
        if chat_uid and target.get("uid") == chat_uid:
            return {"error": "这就是当前会话，直接正常回复即可，不需要跨会话发送"}
        requestor = ""
        if chat_uid:
            current = db.get_chat(chat_uid)
            requestor = (current or {}).get("name") or ""
        pending = conf.get("defer")
        if pending is None:
            # 控制台/无延迟队列场景：立刻真发
            try:
                await session.cross_send(target["uid"], content, kind="relay", requestor=requestor)
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            return {"ok": True, "to": target["name"], "sent": content}
        if any(p.get("uid") == target["uid"] and p.get("content") == content for p in pending):
            return {"ok": True, "to": target["name"], "deferred": True, "note": "这条已经登记过了"}
        if len(pending) >= 3:
            return {"error": "一轮最多代发 3 条，剩下的下一轮再说"}
        pending.append(
            {"uid": target["uid"], "name": target["name"], "content": content, "requestor": requestor}
        )
        return {
            "ok": True,
            "to": target["name"],
            "deferred": True,
            "note": "已登记，会在你这轮回复发出后发过去",
        }

    async def send_file(args: dict[str, Any]) -> Any:
        to = (args.get("to") or "").strip()
        path = (args.get("file_path") or "").strip()
        if not to or not path:
            return {"error": "to 和 file_path 都不能为空"}
        try:
            safe_path = _assert_sendable_path(path)
        except ValueError as exc:
            return {"error": str(exc)}
        resolved = await session.resolve_chat(to)
        if not resolved:
            return {"error": f"找不到会话「{to}」"}
        await session.send_file(resolved["uid"], safe_path)
        return {"ok": True, "to": resolved["name"], "file": safe_path}

    async def search_messages(args: dict[str, Any]) -> Any:
        keyword = (args.get("keyword") or "").strip()
        if not keyword:
            return {"error": "keyword 不能为空"}
        rows = db.search_messages(session.account_id, keyword, limit=int(args.get("limit", 10)))
        if chat_uid:
            rows = [r for r in rows if r.get("uid") == chat_uid]
        return [
            {"chat": r["chat_name"], "time": _fmt_ts(r["ts"]), "text": r["content"]} for r in rows
        ] or {"message": "没有找到相关内容"}

    async def now(args: dict[str, Any]) -> Any:
        return {
            "time": time.strftime("%Y-%m-%d %H:%M:%S %A"),
            "weekday": "一二三四五六日"[time.localtime().tm_wday],
            "account": session.name,
        }

    async def get_whale_balance(args: dict[str, Any]) -> Any:
        """查询 DeepSeek 余额与今日已用；仅控制台智能体注册。"""
        cfg = getattr(getattr(session, "llm", None), "cfg", None)
        if not isinstance(cfg, dict):
            return {"error": "当前对话没有可用的模型配置，无法查询余额"}
        try:
            payload = await whale.balance_payload(cfg)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"余额查询失败：{exc}"}
        if not payload.get("ok"):
            result: dict[str, Any] = {
                "ok": False,
                "code": payload.get("code") or "",
                "error": payload.get("error") or "余额查询失败",
            }
            if payload.get("todayUsage") is not None:
                result["today_usage"] = payload.get("todayUsage")
                result["usage_label"] = payload.get("usageLabel") or "本地估算"
            return result
        total = payload.get("totalBalance")
        today = payload.get("todayUsage")
        summary = f"DeepSeek 余额 ¥{float(total or 0):.2f}，今日已用（本地估算）¥{float(today or 0):.2f}"
        return {
            "ok": True,
            "summary": summary,
            "total_balance": total,
            "currency": payload.get("currency") or "CNY",
            "bonus_balance": payload.get("bonusBalance"),
            "recharge_balance": payload.get("rechargeBalance"),
            "today_usage": today,
            "usage_label": payload.get("usageLabel") or "本地估算",
            "updated_at": payload.get("updatedAt") or "",
        }

    async def get_last_turn_cost(args: dict[str, Any]) -> Any:
        """查询当前控制台对话上一轮的成本；不跨会话，也不回退到全局账本。"""
        if not conversation_id:
            return {"error": "当前不在控制台对话中，无法确定上一轮"}
        turns = db.list_agent_turns(conversation_id, limit=20)
        for turn in reversed(turns):
            if turn.get("role") != "assistant":
                continue
            meta = turn.get("meta") or {}
            cost = meta.get("cost")
            if cost is None:
                return {
                    "ok": False,
                    "error": "上一轮没有可用的服务商用量（可能是非 DeepSeek 模型，或该轮早于成本记录功能）",
                }
            tokens = meta.get("tokens") or {}
            cost_num = round(max(float(cost), 0.0), 6)
            return {
                "ok": True,
                "summary": f"上一轮消耗 ¥{cost_num:.4f}",
                "cost": cost_num,
                "currency": "CNY",
                "tokens": tokens,
                "total_tokens": int(tokens.get("input") or 0) + int(tokens.get("output") or 0),
                "model": turn.get("model") or "",
                "time": _fmt_ts(turn.get("ts") or 0) if turn.get("ts") else "",
            }
        return {"ok": False, "error": "当前对话还没有上一轮"}

    async def look_at_self(args: dict[str, Any]) -> Any:
        from . import vision

        try:
            description = await vision.describe_self(
                session.llm, str(args.get("question") or ""), session.account_id
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"看自己的形象图失败了：{exc}"}
        if not description:
            return {
                "error": "还没有设置形象图：把头像图片保存为 "
                "data/self/{账号id}/avatar.png（多账号各放各的），"
                "或共享的 data/self/avatar.png（也支持 .jpg/.jpeg/.webp/.gif）"
            }
        return {"image": str(vision.self_image_path(session.account_id)), "description": description}

    async def web_search(args: dict[str, Any]) -> Any:
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "query 不能为空"}
        try:
            results = await web.search(query, int(args.get("limit", 5)))
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"query": query, "results": results}

    async def fetch_page(args: dict[str, Any]) -> Any:
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "url 不能为空"}
        try:
            return await web.fetch_text(url, int(args.get("max_chars", web.MAX_FETCH_CHARS)))
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    async def download_file(args: dict[str, Any]) -> Any:
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "url 不能为空"}
        try:
            return await web.download(
                url,
                (args.get("filename") or "").strip() or None,
                int(args.get("max_mb", 50)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    async def shutdown_self(args: dict[str, Any]) -> Any:
        reason = (args.get("reason") or "").strip()
        if shutdown is None:
            return {"error": "当前环境不支持关闭服务"}
        shutdown(reason or "AI 自主关闭")
        return {
            "ok": True,
            "message": "已保存当前进度，本轮回复送达后后端进程将关闭。",
            "note": "关闭后控制台与微信通道都会停止，需重新运行 run.bat 才能恢复。",
        }

    async def restart_self(args: dict[str, Any]) -> Any:
        reason = (args.get("reason") or "").strip()
        if restart is None:
            return {"error": "当前环境不支持重启服务"}
        restart(reason or "AI 请求重启")
        return {
            "ok": True,
            "message": "已保存当前进度，本轮回复送达后服务将自动重启，约 10-20 秒后恢复。",
            "note": "重启会短暂中断微信通道，恢复后自动重新连接。",
        }

    async def remember(args: dict[str, Any]) -> Any:
        content = (args.get("content") or "").strip()
        if not content:
            return {"error": "content 不能为空"}
        scope = (args.get("scope") or "global").strip()
        if scope not in ("global", "chat"):
            scope = "global"
        row = db.add_memory(
            session.account_id,
            content[:200],
            chat_uid="" if scope == "global" else chat_uid,
            kind=(args.get("kind") or "fact").strip(),
            importance=int(args.get("importance", 2)),
            layer=(args.get("layer") or None),
            subject=(args.get("subject") or "contact"),
            confidence=float(args.get("confidence", 0.8) or 0.8),
            source="tool",
        )
        memory_mod.refresh_profiles_for_change(session.account_id, row.get("chat_uid") or "")
        return {"ok": True, "memory": row, "note": "已记住，之后的对话会自动带上这条记忆"}

    async def recall_memory(args: dict[str, Any]) -> Any:
        keyword = (args.get("query") or "").strip()
        if not keyword:
            rows = db.list_memories(session.account_id, limit=20)
        else:
            rows = await memory_mod.search_async(
                session.account_id,
                keyword,
                limit=int(args.get("limit", 10)),
                chat_uid=chat_uid or None,
            )
        db.touch_memories(
            session.account_id, [int(m["id"]) for m in rows if m.get("id")]
        )
        return {"memories": rows}

    async def forget_memory(args: dict[str, Any]) -> Any:
        try:
            mid = int(args.get("id"))
        except (TypeError, ValueError):
            return {"error": "需要提供要忘掉的记忆 id（先用 recall_memory 查）"}
        row = db.get_memory(session.account_id, mid)
        ok = db.delete_memory(session.account_id, mid)
        if ok:
            memory_mod.refresh_profiles_for_change(
                session.account_id, (row or {}).get("chat_uid") or ""
            )
        return {"ok": ok, "note": "已删除" if ok else "没有找到这条记忆"}

    def _wiki_emoji_names() -> list[str]:
        """仅AI娘表情包模式的角色名词表；模式关闭返回空表。"""
        if not config.load()["ai"].get("wiki_emoji_only", False):
            return []
        return moe_wiki.character_names()

    async def search_emojis_tool(args: dict[str, Any]) -> Any:
        query = (args.get("query") or "").strip()
        site_row: dict[str, Any] | None = None
        site_error = ""
        extra_local: list[dict[str, Any]] = []
        serious = bool(
            chuanzi_on and chuanzi_state and chuanzi_state.get("serious")
        )
        if chuanzi_site and not serious and not chuanzi_state.get("used"):
            chuanzi_state["used"] = True
            site_query = chuanzi.build_query(chuanzi_state.get("hint") or query)
            try:
                result = await chuanzi.hunt(session.account_id, site_query)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc)}
            if result.get("ok") and result.get("emoji_id"):
                row = db.get_emoji(session.account_id, int(result["emoji_id"]))
                if row:
                    item = {**row, "quote": str(result.get("quote") or "")}
                    if row.get("source") == chuanzi.SOURCE:
                        site_row = item
                        chuanzi_state["emoji_id"] = int(result["emoji_id"])
                        chuanzi_state["quote"] = str(result.get("quote") or "")
                    else:
                        # 站点命中的是库内其他来源的旧图：不冒充站点图，按普通本地候选过滤
                        extra_local.append(item)
            elif result.get("error") or result.get("message"):
                site_error = str(result.get("error") or result.get("message"))

        rows = db.search_emojis(
            session.account_id, query, limit=int(args.get("limit", 8))
        )
        rows = rows + extra_local
        if serious:
            # 硬闸里连历史入库的串子站图也不能放行。
            rows = [r for r in rows if r.get("source") != chuanzi.SOURCE]
        names = _wiki_emoji_names()
        if names:
            if chuanzi_mode:
                rows = [
                    r for r in rows
                    if r.get("source") == chuanzi.SOURCE
                    or moe_wiki.roster_tagged([r], names)
                ]
            else:
                rows = moe_wiki.roster_tagged(rows, names)

        if chuanzi_mode:
            # 站内图置顶：串子模式关闭时不重排，保持 db.search_emojis 的原顺序
            deduped: list[dict[str, Any]] = []
            seen_ids: set[int] = set()
            for r in ([site_row] if site_row else []) + rows:
                if r["id"] in seen_ids:
                    continue
                seen_ids.add(r["id"])
                deduped.append(r)
            rows = sorted(
                deduped,
                key=lambda r: 0 if r.get("source") == chuanzi.SOURCE else 1,
            )

        if not rows:
            if serious:
                hint = (
                    "本轮是正经/情绪场景：不查串子站，只从本地挑安慰/倾听/正经向的"
                    "表情；没有合适的就只回文字。"
                )
            elif chuanzi_site:
                if names:
                    hint = (
                        "串子站本轮已查过或暂不可用；仅AI娘模式下本地库只放行 tags 带图鉴角色名"
                        "（大肥鱼、小肥鱼、Claude娘 等）的表情，串子站入库的表情不受该限制；"
                        "可以换更短的关键词从本地库再搜一次，或只回文字。"
                    )
                else:
                    hint = (
                        "串子站本轮已查过或暂不可用；可以换更短的关键词从本地库再搜一次，"
                        "或只回文字。"
                    )
            elif chuanzi_on:
                hint = (
                    "「串子站表情包」开关未开启，本轮只查本地库；"
                    + (
                        "仅AI娘模式下本地库只放行 tags 带图鉴角色名（大肥鱼、小肥鱼、Claude娘 等）"
                        "的表情，已入库的串子站表情不受该限制；"
                        if names
                        else ""
                    )
                    + "可以换更短的关键词从本地库再搜一次，或只回文字。"
                )
            else:
                hint = (
                    "先换更短的关键词（单个词，如「晚安」「无语」「猫」）重试一次；"
                    + (
                        "仅AI娘表情包模式下只放行 tags 带图鉴角色名（大肥鱼、小肥鱼、Claude娘 等）"
                        "的表情，没有合格的就只回文字；"
                        if names
                        else "还没有就调用 find_emoji_online 联网找图自动入库，"
                             "入库成功后即可 send_wechat_emoji 发送，"
                    )
                )
            payload: dict[str, Any] = {
                "message": "表情包库里没有匹配的表情",
                "hint": hint,
            }
            if site_error:
                payload["site_error"] = site_error
            return payload

        out: list[dict[str, Any]] = []
        for r in rows:
            item = {
                "id": r["id"],
                "tags": r.get("tags") or "",
                "description": r.get("description") or "",
                "source": r.get("source") or "",
            }
            if r.get("quote"):
                item["quote"] = str(r["quote"])
            out.append(item)
        return out

    async def send_emoji_tool(args: dict[str, Any]) -> Any:
        try:
            eid = int(args.get("emoji_id"))
        except (TypeError, ValueError):
            return {"error": "需要 emoji_id（先用 search_emojis 查到）"}
        row = db.get_emoji(session.account_id, eid)
        if not row:
            return {"error": f"表情包库里没有 #{eid}"}
        if (
            chuanzi_on
            and chuanzi_state is not None
            and chuanzi_state.get("serious")
            and row.get("source") == chuanzi.SOURCE
        ):
            return {
                "error": "本轮是正经/情绪场景，不能发串子站/嘲讽图；"
                "换本地安慰/倾听/正经向的图，或只回文字"
            }
        names = _wiki_emoji_names()
        # 站内图的角色名豁免只在串子模式开着时生效：关掉后它按普通本地表情过滤
        if (
            names
            and not (chuanzi_mode and row.get("source") == chuanzi.SOURCE)
            and not moe_wiki.roster_tagged([row], names)
        ):
            return {
                "error": "仅AI娘表情包模式开启：只发 tags 带图鉴角色名（大肥鱼、小肥鱼、Claude娘 等）"
                "的表情，这张不合格；用 search_emojis 重新挑一张合格的",
            }
        to = (args.get("to") or chat_uid).strip()
        if not to:
            return {"error": "需要 to（会话名称或 chat_id），或者先选中一个会话"}
        resolved = await session.resolve_chat(to)
        if not resolved:
            return {"error": f"找不到会话「{to}」，可以先调用 list_wechat_chats 确认名称"}
        if defer_emoji is not None and chat_uid and resolved["uid"] == chat_uid:
            # 「先保证消息发出来再发表情包」：自动回复链路里只登记，正文发出后由
            # AccountSession 统一真发，避免表情抢在文字之前到对方手机上
            defer_emoji.append({"uid": resolved["uid"], "emoji": row, "to": resolved["name"]})
            return {
                "ok": True,
                "to": resolved["name"],
                "emoji_id": eid,
                "deferred": True,
                "note": "表情已选定，会在你的文字回复发出后自动发送",
            }
        await session.send_emoji(resolved["uid"], row, source="agent")
        return {"ok": True, "to": resolved["name"], "emoji_id": eid}

    async def find_emoji_online_tool(args: dict[str, Any]) -> Any:
        query = (args.get("query") or "").strip()
        if chuanzi_site:
            if chuanzi_state.get("serious"):
                return {
                    "error": "本轮是正经/情绪场景，别去串子站找图；"
                    "用 search_emojis 从本地挑安慰/倾听/正经向的表情，没有就只回文字"
                }
            if chuanzi_state.get("used"):
                return {
                    "error": "本轮已经查过串子站（每轮最多一次）；"
                    "请用 search_emojis 从本地库挑，或直接回文字"
                }
            chuanzi_state["used"] = True
            site_query = chuanzi.build_query(
                chuanzi_state.get("hint") or "", query
            )
            if not site_query:
                return {"error": "query 不能为空：用场景关键词描述想要的表情"}
            try:
                result = await chuanzi.hunt(session.account_id, site_query)
            except Exception as exc:  # noqa: BLE001
                return {
                    "error": f"串子站检索失败：{exc}",
                    "message": "本轮回落本地表情库",
                }
            if not result.get("ok"):
                return result
            if result.get("source") == chuanzi.SOURCE:
                chuanzi_state["emoji_id"] = int(result["emoji_id"])
                chuanzi_state["quote"] = str(result.get("quote") or "")
            return {
                "ok": True,
                "emoji_id": result["emoji_id"],
                "tags": result.get("tags") or "",
                "description": result.get("description") or "",
                "quote": result.get("quote") or "",
                "title": result.get("title") or "",
                "collection": result.get("collection") or "",
                "source": result.get("source") or "",
                "imported": bool(result.get("imported")),
                "note": (
                    "已入库并锁定；quote 可以原样当回复，表情用 send_wechat_emoji 发"
                    if result.get("imported")
                    else "这张图已在库中，未改来源/锁定；quote 可以原样当回复"
                ),
            }

        if not query:
            return {"error": "query 不能为空：用画面/场景关键词描述想要的表情"}
        to = (args.get("to") or chat_uid).strip()
        context = ""
        if to:
            resolved = await session.resolve_chat(to)
            if resolved:
                context = tagger.build_context(db.list_messages(resolved["uid"], limit=8))
        try:
            return await emoji_search.hunt(
                session.account_id,
                session.llm,
                query,
                context=context,
                limit=int(args.get("limit", 3)),
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    async def search_wiki_tool(args: dict[str, Any]) -> Any:
        root = moe_wiki.wiki_root()
        if not root:
            return {"error": "形象图集不可用（ai.moe_wiki_path 未配置或目录不存在）"}
        return moe_wiki.search(root, str(args.get("query") or ""), int(args.get("limit", 8)))

    async def send_wiki_tool(args: dict[str, Any]) -> Any:
        root = moe_wiki.wiki_root()
        if not root:
            return {"error": "形象图集不可用（ai.moe_wiki_path 未配置或目录不存在）"}
        query = (args.get("query") or "").strip()
        hit = moe_wiki.find_image(root, query)
        if not hit:
            return {
                "error": f"图集里没有匹配「{query}」的图",
                "hint": "先用 search_moe_wiki_images 换关键词确认图名再发",
            }
        to = (args.get("to") or chat_uid).strip()
        if not to:
            return {"error": "需要 to（会话名称或 chat_id），或者先选中一个会话"}
        resolved = await session.resolve_chat(to)
        if not resolved:
            return {"error": f"找不到会话「{to}」，可以先调用 list_wechat_chats 确认名称"}
        session.send_file(resolved["uid"], hit["path"])
        return {"ok": True, "to": resolved["name"], "sent": hit["rel"]}

    async def browse_moments_tool(args: dict[str, Any]) -> Any:
        service = getattr(session, "moments", None)
        if service is None:
            return {"error": "当前通道不支持朋友圈（仅微信 4.x 通道可用）"}
        rows = db.list_moments(
            session.account_id,
            keyword=(args.get("keyword") or "").strip(),
            limit=max(1, min(int(args.get("limit", 10)), 30)),
        )
        if not rows:
            return {"message": "朋友圈还没有同步到内容，稍后再试"}
        out = []
        for r in rows:
            out.append(
                {
                    "feed_id": r["feed_id"],
                    "from": "我" if r["is_self"] else (r["nickname"] or r["username"]),
                    # 发布者唯一标识：同名好友只有 username 分得清，点赞/评论时传它
                    "from_id": r["username"],
                    "time": _fmt_ts(r["create_time"]) if r["create_time"] else "",
                    "text": (r["text"] or "")[:200],
                    "images": len(r.get("images") or []),
                    "videos": len(r.get("videos") or []),
                    "likes": [
                        lk.get("nickname") or lk.get("username") for lk in (r.get("likes") or [])
                    ][:10],
                    "comments": [
                        f"{c.get('nickname') or c.get('username')}：{c.get('content')}"
                        for c in (r.get("comments") or [])
                    ][:10],
                }
            )
        return out

    _MEDIA_FAILS = {
        "no-cache": "本机缓存已过期，也没有可用的 CDN 地址",
        "cache-decrypt-failed": "本机缓存解密失败",
        "url-missing": "媒体记录里没有下载地址",
        "url-dead": "朋友圈 CDN 链接已失效",
        "write-failed": "写入本地缓存失败",
        "no-such-media": "这张图不存在",
        "bad-kind": "不支持的媒体类型",
        "error": "下载失败",
    }

    async def view_moment_images_tool(args: dict[str, Any]) -> Any:
        """把一条朋友圈动态的配图交给识图模型，返回文字描述（控制台专用）。"""
        from . import vision

        service = getattr(session, "moments", None)
        if service is None:
            return {"error": "当前通道不支持朋友圈（仅微信 4.x 通道可用）"}
        if not config.load()["ai"].get("vision_enabled"):
            return {
                "error": "识图未开启：先到「设置 → 模型」打开「识图（图片输入）」，再让我看朋友圈配图"
            }
        feed_id = str(args.get("feed_id") or "").strip()
        row: dict[str, Any] | None = None
        if feed_id:
            row = db.get_moment(session.account_id, feed_id)
            if row is None:
                return {
                    "error": f"没在已同步的朋友圈里找到 feed_id={feed_id} 的动态；"
                    "先用 browse_wechat_moments 确认"
                }
        else:
            publisher = str(args.get("publisher") or "").strip()
            if not publisher:
                return {
                    "error": "需要 feed_id（browse_wechat_moments 返回的动态 id），"
                    "或 publisher（发布者 from_id）"
                }
            row, err = _find_moment_row(publisher, str(args.get("keyword") or "").strip())
            if err:
                return {"error": err}
            if row is None:
                return {"error": f"没在已同步的朋友圈里找到「{publisher}」的动态"}
        images = row.get("images") or []
        if not images:
            return {"error": "这条朋友圈没有图片（可能只有文字或视频）"}
        total = len(images)
        raw_index = args.get("index")
        if raw_index in (None, ""):
            try:
                limit = int(args.get("limit") or 3)
            except (TypeError, ValueError):
                limit = 3
            picks = list(range(min(total, max(1, min(limit, 4)))))
        else:
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                return {"error": "index 要填从 1 开始的整数"}
            if index < 1 or index > total:
                return {"error": f"这条动态只有 {total} 张图，index={index} 超出范围"}
            picks = [index - 1]

        found: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for i in picks:
            try:
                res = await asyncio.to_thread(service.download_media_detail, row, i, "image")
            except Exception as exc:  # noqa: BLE001 - 下载层异常也要落到可读原因
                res = {"status": "error", "path": None, "reason": str(exc)}
            path = str(res.get("path") or "")
            if path and Path(path).is_file():
                found.append({"index": i + 1, "path": path})
            else:
                status = str(res.get("status") or "error")
                failed.append(
                    {
                        "index": i + 1,
                        "reason": _MEDIA_FAILS.get(status, status),
                        "detail": str(res.get("reason") or "")[:120],
                    }
                )
        if not found:
            return {
                "error": "朋友圈图片取不到："
                + "；".join(f"第 {f['index']} 张 {f['reason']}" for f in failed),
                "failed": failed,
            }

        question = str(args.get("question") or "").strip()
        post = str(row.get("text") or "").strip()
        seen = "、".join(str(x["index"]) for x in found)
        prompt = (
            f"这是微信朋友圈的一条动态配图（共 {total} 张，本次看第 {seen} 张）。"
            f"动态正文：{post[:120] or '（无文字）'}。"
        )
        prompt += (
            f"请针对这个问题看图回答：{question}"
            if question
            else "请逐张客观描述画面内容和图中文字，多张图按顺序分开说明，不要猜测看不到的内容。"
        )
        try:
            description = await vision.describe_images(
                session.llm, [x["path"] for x in found], prompt
            )
        except Exception as exc:  # noqa: BLE001
            payload: dict[str, Any] = {
                "error": f"图片已经取到本机，但看图失败：{exc}",
                "images": found,
            }
            if failed:
                payload["failed"] = failed
            return payload
        payload = {
            "ok": True,
            "feed_id": row.get("feed_id") or feed_id,
            "from": row.get("nickname") or row.get("username") or "",
            "text": post[:200],
            "total_images": total,
            "images": found,
            "description": description or "（识图没有返回描述）",
            "note": "描述来自本机识图；图片已存本地，要转发原图时把 path 传给 send_wechat_file",
        }
        if failed:
            payload["failed"] = failed
        return payload

    def _norm_moment_name(s: str) -> str:
        """昵称归一化：全角→半角、去空白、转小写（ＭＩＯＳ / M I O S / mios 等价）。"""
        import unicodedata

        return unicodedata.normalize("NFKC", str(s or "")).replace(" ", "").lower()

    def _find_moment_row(publisher: str, keyword: str) -> tuple[dict[str, Any] | None, str]:
        """定位朋友圈动态，识别以 username（from_id）为准。

        传 from_id 直接锁定发布者；只传昵称时若多个不同 username 的好友同名，
        拒绝操作防止点赞/评论到错误的人（返回 (None, 提示)）。
        keyword 仍按正文关键词区分同一人的多条动态。
        """
        raw = str(publisher or "").strip()
        pn = _norm_moment_name(raw)
        rows = db.list_moments(session.account_id, keyword=publisher, limit=50)
        if not rows:
            rows = db.list_moments(session.account_id, limit=200)
        exact = [r for r in rows if _norm_moment_name(r.get("username")) == pn]
        if not exact:
            exact = [r for r in rows if _norm_moment_name(r.get("nickname")) == pn]
            owners = {str(r.get("username") or "") for r in exact}
            if len(owners) > 1:
                return (
                    None,
                    f"有 {len(owners)} 位昵称都是「{publisher}」，按昵称会认错人；"
                    "请用 browse_wechat_moments 找到目标动态的 from_id（username）再试",
                )
        for row in exact:
            if not keyword or keyword in (row.get("text") or ""):
                return row, ""
        if keyword:
            kn = _norm_moment_name(keyword)
            for row in rows:
                if kn and kn in _norm_moment_name(row.get("text") or ""):
                    return row, ""
        return (exact[0] if exact else None), ""

    async def like_moment_tool(args: dict[str, Any]) -> Any:
        service = getattr(session, "moments", None)
        if service is None:
            return {"error": "当前通道不支持朋友圈（仅微信 4.x 通道可用）"}
        publisher = (args.get("publisher") or "").strip()
        if not publisher:
            return {"error": "publisher 不能为空：朋友圈发布者的 from_id（username）"}
        row, err = _find_moment_row(publisher, (args.get("keyword") or "").strip())
        if err:
            return {"error": err}
        if row is None:
            return {"error": f"没在已同步的朋友圈里找到「{publisher}」的动态"}
        result = await asyncio.to_thread(service.like, row)
        if not result.get("ok"):
            return {"error": f"点赞未成功：{result.get('message')}"}
        return {"ok": True, "to": publisher, "note": "已在微信里完成点赞"}

    async def comment_moment_tool(args: dict[str, Any]) -> Any:
        service = getattr(session, "moments", None)
        if service is None:
            return {"error": "当前通道不支持朋友圈（仅微信 4.x 通道可用）"}
        publisher = (args.get("publisher") or "").strip()
        content = (args.get("content") or "").strip()
        if not publisher or not content:
            return {"error": "publisher 和 content 都不能为空"}
        row, err = _find_moment_row(publisher, (args.get("keyword") or "").strip())
        if err:
            return {"error": err}
        if row is None:
            return {"error": f"没在已同步的朋友圈里找到「{publisher}」的动态"}
        result = await asyncio.to_thread(
            service.comment, row, content, (args.get("reply_to") or "").strip()
        )
        if not result.get("ok"):
            return {"error": f"评论未成功：{result.get('message')}"}
        return {"ok": True, "to": publisher, "sent": content}

    async def update_persona(args: dict[str, Any]) -> Any:
        """改当前账号的专属人设；改完刷新会话快照，下一次对话即生效。"""
        if args.get("clear"):
            persona: dict[str, Any] = {"system_prompt": "", "persona_name": ""}
            note = "已清除该账号的专属人设，回退为全局默认人设"
        else:
            persona = {}
            if args.get("persona_name") is not None:
                persona["persona_name"] = str(args["persona_name"]).strip()[:50]
            if args.get("system_prompt") is not None:
                prompt = str(args["system_prompt"]).strip()
                if not prompt:
                    return {"error": "system_prompt 不能为空；想恢复全局人设请传 clear=true"}
                persona["system_prompt"] = prompt[:2000]
            if not persona:
                return {"error": "至少提供 system_prompt 或 persona_name 之一，或 clear=true"}
            note = "人设已更新，从下一次对话开始生效"
        config.upsert_account({"id": session.account_id, "persona": persona})
        fresh = config.account(session.account_id)
        if fresh:
            session.account = copy.deepcopy(fresh)
        return {"ok": True, "persona": session.account.get("persona") or {}, "note": note}

    tools = [
        Tool(
            name="list_wechat_chats",
            description=(
                "查看当前正在对话的会话信息（自动回复模式下只能看到这一个会话）。"
                if chat_uid
                else "列出当前账号的微信会话（最近联系人/群），可按名称关键词过滤。发送消息前可用它确认会话名称。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "名称关键词，可留空"},
                    "limit": {"type": "integer", "description": "返回条数，默认 20"},
                },
            },
            handler=list_chats,
        ),
        Tool(
            name="get_chat_history",
            description=(
                "读取当前会话最近的聊天记录，用于理解上下文（自动回复模式下只能读当前会话）。"
                if chat_uid
                else "读取当前账号某个微信会话最近的聊天记录，用于理解上下文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chat": {"type": "string", "description": "会话名称或 chat_id"},
                    "limit": {"type": "integer", "description": "条数，默认 20"},
                },
                "required": ["chat"],
            },
            handler=get_history,
        ),
        Tool(
            name="search_messages",
            description=(
                "在当前会话已同步的微信消息中按关键词全文检索。"
                if chat_uid
                else "在当前账号已同步的微信消息中按关键词全文检索。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["keyword"],
            },
            handler=search_messages,
        ),
        Tool(
            name="get_current_time",
            description="获取当前日期、时间与正在操作的账号。",
            parameters={"type": "object", "properties": {}},
            handler=now,
        ),
        Tool(
            name="look_at_self",
            description=(
                "看一眼自己的形象图（微信头像），返回画面的文字描述。"
                "被问到你的长相/头像/形象，或需要向别人描述自己外观细节时用；"
                "人设里已有基础形象描述，这个工具能给出更细的画面细节。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "想从形象图里确认什么，可留空（留空则给完整外观描述）",
                    },
                },
            },
            handler=look_at_self,
        ),
        Tool(
            name="remember",
            description=(
                "把一条重要信息存入你的长期记忆（之后相关对话会自动带上）。"
                "用于对方主动告诉你的重要事实：名字、生日、喜好厌恶、职业、重要约定等。"
                "长期稳定的事实用 core，某次发生的事/约定用 episodic；琐碎寒暄不要存。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "一句话记忆，如“小王的生日是6月12日”"},
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "preference", "event", "relationship"],
                        "description": "记忆类型，默认 fact",
                    },
                    "layer": {
                        "type": "string",
                        "enum": ["core", "episodic"],
                        "description": "core=长期事实/关系/偏好（默认）；episodic=某次发生的事/约定",
                    },
                    "subject": {
                        "type": "string",
                        "enum": ["contact", "self", "world"],
                        "description": "关于对方（默认）/ 你自己 / 外部世界",
                    },
                    "importance": {
                        "type": "integer",
                        "enum": [1, 2, 3],
                        "description": "重要程度 1-3，默认 2",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "置信度 0-1，默认 0.8；听别人转述的事可以低一点",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "chat"],
                        "description": "global=所有会话都记得（默认）；chat=只在与当前这个会话相关时想起",
                    },
                },
                "required": ["content"],
            },
            handler=remember,
        ),
        Tool(
            name="recall_memory",
            description="检索你的长期记忆。当对话提到过去的事、某人信息而你不确定时，用它查。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词，留空返回最近 20 条"},
                    "limit": {"type": "integer", "description": "返回条数，默认 10"},
                },
            },
            handler=recall_memory,
        ),
        Tool(
            name="forget_memory",
            description="删除一条长期记忆（用户要求忘掉某事、或发现记忆过时错误时用）。先用 recall_memory 查到 id。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "要忘掉的记忆 id"},
                },
                "required": ["id"],
            },
            handler=forget_memory,
        ),
    ]

    if emoji_tools:
        # 秒回模式下文字消息不注册表情三件套：找表情的工具轮次会拖慢正文产出，
        # 表情改由正文发出后的轻量调用统一挑选补发
        tools.extend(
            [
                Tool(
                    name="search_emojis",
                    description=(
                        "在表情包库里检索表情包（按标签/描述匹配，如「无语」「加班」「猫」；"
                        "留空返回最常用的）。query 可传多个关键词，任一命中即返回。"
                        "串子模式下第一次调用会先查 searchanythingfun 并入库一张"
                        "（每轮最多一次）；结果第一条可能带 source=searchanythingfun "
                        "和 quote，quote 可原样当回复。"
                        if chuanzi_site
                        else
                        "在表情包库里检索表情包（按标签/描述匹配，如「无语」「加班」「猫」；"
                        "留空返回最常用的）。query 可传多个关键词（空格/顿号分隔），"
                        "任一命中即返回，命中最多的排最前。"
                        "想配个表情时先用它拿到 emoji_id；结果为空时换单个词重试"
                        "或改用 find_emoji_online。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "场景/情绪关键词，可留空"},
                            "limit": {"type": "integer", "description": "返回条数，默认 8"},
                        },
                    },
                    handler=search_emojis_tool,
                ),
                Tool(
                    name="send_wechat_emoji",
                    description=(
                        "向微信会话发送一个动画表情包。先用 search_emojis 挑好 emoji_id；"
                        "to 留空表示发给当前会话。这是真实发送，请挑符合聊天氛围的表情。"
                        "自动回复场景下表情会在文字回复之后跟随发出。"
                        + (
                            "串子站入库的表情不受「仅AI娘」角色名限制。"
                            if chuanzi_mode else ""
                        )
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "to": {"type": "string", "description": "联系人/群名称或 chat_id；留空=当前会话"},
                            "emoji_id": {"type": "integer", "description": "search_emojis 返回的表情 id"},
                        },
                        "required": ["emoji_id"],
                    },
                    handler=send_emoji_tool,
                ),
            ]
        )

    wiki = moe_wiki.wiki_root()
    if wiki:
        tools.append(
            Tool(
                name="search_moe_wiki_images",
                description=(
                    "在你的本地图集《ai-moe-wiki》里按角色/关键词找图（UP 主 ZipZipPipe 绘的 "
                    "AI 娘化图集，你自己的立绘也在里面）。query 传角色名（如「大肥鱼」「Claude娘」）"
                    "或关键词（壁纸、三视图、梗图），留空返回图集开头的图。"
                    "返回文件名、绝对路径和一句话说明；想看图集有什么、或发图前确认图名时用。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "角色名或关键词，可留空"},
                        "limit": {"type": "integer", "description": "返回条数，默认 8"},
                    },
                },
                handler=search_wiki_tool,
            )
        )
    if wiki and allow_send:
        tools.append(
            Tool(
                name="send_moe_wiki_image",
                description=(
                    "把图集《ai-moe-wiki》里的一张图真实发送到微信会话（按图片消息发出）。"
                    "query 传图名关键词（如「大肥鱼」发你自己的立绘）；"
                    "别人想看你的样子/立绘，或聊到图集里的角色想配图时用。"
                    "拿不准图名就先用 search_moe_wiki_images 查。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "联系人/群名称或 chat_id；留空=当前会话"},
                        "query": {"type": "string", "description": "图名关键词，如「大肥鱼」「中秋贺图」"},
                    },
                    "required": ["query"],
                },
                handler=send_wiki_tool,
            )
        )

    if shutdown is not None:
        tools.append(
            Tool(
                name="shutdown_self",
                description=(
                    "关闭自己：先自动保存配置与聊天数据，然后结束后端进程（控制台与微信通道一起停止）。"
                    "仅在用户明确要求关闭 / 退出 / 停服时调用；这是不可逆操作，调用前确认用户意图。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "关闭原因，会写入日志"},
                    },
                },
                handler=shutdown_self,
            )
        )

    if restart is not None:
        tools.append(
            Tool(
                name="restart_self",
                description=(
                    "重启服务：保存数据后结束后端进程，并由分离进程自动重新拉起（约 10-20 秒恢复）。"
                    "仅在用户明确要求重启 / 重启服务 / 重新启动时调用，"
                    "比如改了需要重启才生效的配置、服务状态异常时。调用前先向用户确认。"
                    "重启会短暂中断微信通道，控制台页面会自动刷新。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "重启原因，会写入日志"},
                    },
                },
                handler=restart_self,
            )
        )

    if allow_persona:
        tools.append(
            Tool(
                name="update_persona",
                description=(
                    "修改你自己（当前账号）的人设：人格名、性格与说话方式。"
                    "仅在用户明确提出想改变你的性格/身份/说话方式时调用，"
                    "调用前先向用户复述你打算写成什么样，确认后再执行。"
                    "system_prompt 是整体替换；clear=true 表示清除专属人设、回退全局默认。"
                    "你无权修改全局默认人设或其他账号的人设。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "system_prompt": {
                            "type": "string",
                            "description": "完整的新人设（整体替换现有内容），不修改就别传",
                        },
                        "persona_name": {"type": "string", "description": "新的人格名，不修改就别传"},
                        "clear": {"type": "boolean", "description": "true=清除专属人设，回退全局"},
                    },
                },
                handler=update_persona,
            )
        )

    if tool_context == "console" and config.growth_enabled(session.account_id):

        async def recall_growth(args: dict[str, Any]) -> Any:
            query = str(args.get("query") or "").strip()
            kind = str(args.get("kind") or "").strip()
            limit = max(1, min(20, int(args.get("limit") or 8)))
            traits = growth_store.recall_traits(
                session.account_id, query, limit_chars=2000
            )
            if kind:
                traits = [t for t in traits if t["kind"] == kind]
            goals = growth_store.list_goals(session.account_id, status="open")[:5]
            journal = growth_store.list_journal(session.account_id, limit=3)
            return {
                "traits": [
                    {"kind": t["kind"], "content": t["content"], "tags": t["tags"]}
                    for t in traits[:limit]
                ],
                "goals": [{"content": g["content"], "status": g["status"]} for g in goals],
                "journal": [
                    {"day": j["day"], "content": j["content"][:300]} for j in journal
                ],
            }

        async def read_journal(args: dict[str, Any]) -> Any:
            day = str(args.get("day") or "").strip()
            limit = max(1, min(30, int(args.get("limit") or 5)))
            items = growth_store.list_journal(session.account_id, limit=limit)
            if day:
                items = [j for j in items if j["day"] == day]
            return {
                "items": [
                    {"day": j["day"], "content": j["content"]} for j in items
                ]
            }

        tools.append(
            Tool(
                name="recall_growth",
                description=(
                    "检索你自己的成长账本：特质、在做的目标和最近日记。"
                    "聊到过去、你的变化、你对某人的态度时可用；只读。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "关键词，可留空"},
                        "kind": {
                            "type": "string",
                            "enum": sorted(growth_store.VALID_KINDS),
                            "description": "只看某一类特质",
                        },
                        "limit": {"type": "integer", "description": "条数，默认 8"},
                    },
                },
                handler=recall_growth,
            )
        )
        tools.append(
            Tool(
                name="read_journal",
                description="读取你自己的成长日记；可按日期筛选。只读。",
                parameters={
                    "type": "object",
                    "properties": {
                        "day": {"type": "string", "description": "YYYY-MM-DD，可留空"},
                        "limit": {"type": "integer", "description": "条数，默认 5"},
                    },
                },
                handler=read_journal,
            )
        )

    if allow_send:
        tools.insert(
            0,
            Tool(
                name="send_wechat_message",
                description="以当前账号向指定微信联系人或群发送一条文本消息。这是真实发送，请确认收件人正确、内容得体。",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "联系人/群名称，或 chat_id"},
                        "content": {"type": "string", "description": "消息正文"},
                    },
                    "required": ["to", "content"],
                },
                handler=send_message,
            ),
        )
        tools.append(
            Tool(
                name="send_wechat_file",
                description="以当前账号向指定微信联系人或群发送本地文件（需驱动支持）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "file_path": {"type": "string", "description": "本程序下载或上传目录里的文件绝对路径"},
                    },
                    "required": ["to", "file_path"],
                },
                handler=send_file,
            )
        )
    elif cross and cross.get("relay"):
        # 自动回复里的跨对话代发：普通会话仍被限制在 chat_uid，只放开「按明确要求转发给别人」
        tools.insert(
            0,
            Tool(
                name="send_wechat_message",
                description=(
                    "跨对话代发：把一条文本消息发给「当前会话以外」的微信联系人或群。"
                    "只在对方明确点名收件人、并要求你带话／转告时使用（如「帮我给张三发条消息」）；"
                    "to 填对方说的昵称、备注或群名，重名或找不到时会返回候选/错误，别猜。"
                    "发完在当前会话简单回一句已经转达。没有被明确要求时不要给第三方发消息。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "收件人的昵称/备注/群名（对方提到的那个）"},
                        "content": {"type": "string", "description": "要带过去的消息正文"},
                    },
                    "required": ["to", "content"],
                },
                handler=relay_message,
            ),
        )

    if allow_web:
        tools.extend(
            [
                Tool(
                    name="web_search",
                    description=(
                        "联网搜索网页（Bing 优先、DuckDuckGo 兜底），返回标题、链接和摘要。"
                        "需要最新资讯、文档地址或不确定的事实时可先用它查证。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"},
                            "limit": {"type": "integer", "description": "返回条数，默认 5，最多 10"},
                        },
                        "required": ["query"],
                    },
                    handler=web_search,
                ),
                Tool(
                    name="fetch_page",
                    description=(
                        "抓取一个网页并返回可读正文（HTML 已转纯文本），用于阅读搜索结果或文档页面。"
                        "只支持 http/https 公网地址，二进制资源请改用 download_file。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "完整网址"},
                            "max_chars": {
                                "type": "integer",
                                "description": "返回正文最大字符数，默认 8000",
                            },
                        },
                        "required": ["url"],
                    },
                    handler=fetch_page,
                ),
                Tool(
                    name="download_file",
                    description=(
                        "把网络文件下载到本机专用目录 data/downloads，返回本地绝对路径。"
                        "之后可以用 send_wechat_file 把该文件发给微信联系人。单个文件默认上限 50MB。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "文件下载地址"},
                            "filename": {
                                "type": "string",
                                "description": "保存文件名，可留空，留空时按响应头或网址推断",
                            },
                            "max_mb": {"type": "integer", "description": "大小上限（MB），默认 50"},
                        },
                        "required": ["url"],
                    },
                    handler=download_file,
                ),
            ]
        )

    if emoji_tools and (allow_web or chuanzi_site):
        tools.append(
            Tool(
                name="find_emoji_online",
                description=(
                    "从 searchanythingfun 台词库检索一句台词和一张截图"
                    "（每轮最多 1 次、只入库 1 张）。返回的 quote 可以原样当回复；"
                    "emoji_id 用 send_wechat_emoji 发送。本轮查过或站点不可用时，"
                    "用 search_emojis 从本地库挑。"
                    if chuanzi_site
                    else
                    "联网搜索表情包并自动下载入库、打标签。search_emojis 找不到合适的"
                    "表情时用它补库：query 写画面/场景关键词（如「熊猫头 加班」），"
                    "可带 site:tieba.baidu.com 这类站点限定。返回的 emoji_id 可直接"
                    "用 send_wechat_emoji 发送。搜索加下载要十几秒，一次回复别连用好几次。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "场景/画面关键词"
                                if chuanzi_site
                                else "画面/场景关键词，如「无语 猫」"
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "串子站每轮只入库 1 张"
                                if chuanzi_site
                                else "入库张数，默认 3，最多 5"
                            ),
                        },
                        "to": {
                            "type": "string",
                            "description": (
                                "为哪个会话找；留空=当前会话"
                                if chuanzi_site
                                else "为哪个会话找（取它最近的聊天记录帮打标）；留空=当前会话"
                            ),
                        },
                    },
                    "required": ["query"],
                },
                handler=find_emoji_online_tool,
            )
        )

    moments_ai = bool(config.load()["ai"].get("moments_ai_enabled", True))
    if moments_ai and getattr(session, "moments", None) is not None:
        tools.append(
            Tool(
                name="browse_wechat_moments",
                description=(
                    "浏览当前账号微信朋友圈的最近动态（已自动同步到本地）。"
                    "可按正文关键词过滤；返回发布者昵称、from_id（username）、正文、"
                    "图片数、点赞和评论列表。同名好友认人只看 from_id。"
                    "要看配图内容时，把返回的 feed_id 交给 view_wechat_moment_images。"
                    "被问到朋友圈、想刷朋友圈或需要了解好友近况时用。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "正文关键词，可留空"},
                        "limit": {"type": "integer", "description": "返回条数，默认 10，最多 30"},
                    },
                },
                handler=browse_moments_tool,
            )
        )
    if billing_tools:
        # 只给控制台智能体：余额/账单属于本机主人信息，不能暴露给微信外部会话
        tools.extend(
            [
                Tool(
                    name="get_whale_balance",
                    description=(
                        "查询当前 DeepSeek API 账户余额、赠金/充值余额和今日已用（本地估算）。"
                        "用户问「余额还有多少」「今天花了多少」「账户里还剩多少钱」时调用；"
                        "拿不到会返回明确原因，不要凭记忆猜数字。"
                    ),
                    parameters={"type": "object", "properties": {}},
                    handler=get_whale_balance,
                ),
                Tool(
                    name="get_last_turn_cost",
                    description=(
                        "查询当前控制台对话上一轮的 AI 花费（DeepSeek 按 ai.price_*、MiMo 按内置牌价估算，含输入/输出 tokens）。"
                        "用户问「上一轮花了多少」「刚才这轮多少钱」时调用；"
                        "只查当前对话，不跨会话，也不会拿全局账本里的其他调用冒充。"
                    ),
                    parameters={"type": "object", "properties": {}},
                    handler=get_last_turn_cost,
                ),
            ]
        )

    if moments_ai and getattr(session, "moments", None) is not None and allow_send:
        tools.extend(
            [
                Tool(
                    name="view_wechat_moment_images",
                    description=(
                        "读取朋友圈动态里的配图，返回画面和图中文字的描述（本机识图，控制台可用）。"
                        "先用 browse_wechat_moments 拿到 feed_id，再把用户想确认的问题放进 question；"
                        "不传 index 默认读前几张（最多 4 张），传 index 读指定第几张（从 1 开始）。"
                        "动态带图且用户要你评价/判断这条朋友圈，或明确问图里内容时调用；"
                        "需要把原图转给别人时，把返回的 path 交给 send_wechat_file。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "feed_id": {
                                "type": "string",
                                "description": "动态 id（browse_wechat_moments 返回的 feed_id，首选）",
                            },
                            "publisher": {
                                "type": "string",
                                "description": "没有 feed_id 时用发布者的 from_id（username）",
                            },
                            "keyword": {
                                "type": "string",
                                "description": "同作者多条动态时用正文关键词区分",
                            },
                            "index": {
                                "type": "integer",
                                "description": "看第几张图，从 1 开始；留空=按 limit 读前几张",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "留空 index 时读几张，默认 3，最多 4",
                            },
                            "question": {
                                "type": "string",
                                "description": "想从图里确认什么，如「是不是熊猫图鉴」「截图里写了什么」",
                            },
                        },
                    },
                    handler=view_moment_images_tool,
                ),
                Tool(
                name="like_wechat_moment",
                description=(
                    "给指定好友的朋友圈点赞（真实操作）。先在 browse_wechat_moments 里确认动态存在，"
                    "publisher 传该动态的 from_id（username，同名好友必须用它区分）；"
                    "同作者多条动态时用 keyword 指明是哪条。"
                    "操作要走微信界面，可能需要十几秒。仅在用户/对方明确提出时使用。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "publisher": {
                            "type": "string",
                            "description": "发布者的 from_id（username）；昵称仅在无同名时可用",
                        },
                            "keyword": {
                                "type": "string",
                                "description": "正文关键词，用于区分同一个人的多条动态",
                            },
                        },
                        "required": ["publisher"],
                    },
                    handler=like_moment_tool,
                ),
                Tool(
                name="comment_wechat_moment",
                description=(
                    "在指定好友的朋友圈下发表评论（真实操作，对方能看到）。"
                    "先在 browse_wechat_moments 里确认动态存在，publisher 传该动态的 "
                    "from_id（username，同名好友必须用它区分）；content 要像真人评论，"
                    "简短自然，不要写小作文。操作要走微信界面，可能需要十几秒。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "publisher": {
                            "type": "string",
                            "description": "发布者的 from_id（username）；昵称仅在无同名时可用",
                        },
                            "content": {"type": "string", "description": "评论文本"},
                            "keyword": {
                                "type": "string",
                                "description": "正文关键词，用于区分同一个人的多条动态",
                            },
                            "reply_to": {
                                "type": "string",
                                "description": "要回复某条评论时，填该评论作者的昵称；普通评论留空",
                            },
                        },
                        "required": ["publisher", "content"],
                    },
                    handler=comment_moment_tool,
                ),
            ]
        )

    # 本地技能 / 插件 / MCP：统一注册表按上下文过滤（自动回复默认只放技能）
    try:
        existing = {t.name for t in tools}
        for tool in capability_registry.tools_for(tool_context):
            if tool.name in existing:
                base = tool.name[:56]
                idx = 2
                while f"{base}_{idx}" in existing:
                    idx += 1
                tool = replace(tool, name=f"{base}_{idx}"[:64])
            existing.add(tool.name)
            tools.append(tool)
    except Exception as exc:  # noqa: BLE001
        log.warning("加载能力工具失败：%s", exc)
    return tools
