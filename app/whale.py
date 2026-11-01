"""DeepSeek 账户余额查询：挂件路由与智能体工具共用（25 秒缓存）。

挂件前端和小鲸鱼账单都从同一份余额/今日用量数据取数，避免页面、挂件、
智能体工具三处各查一次 DeepSeek 接口。MiMo 没有用 API Key 查余额的官方接口，
这里只返回本地账本的今日估算，不拿第三方地址冒充 DeepSeek 余额端点。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import usage, __version__

VERSION: dict[str, str] = {"version": __version__, "versionLabel": f"Deep肥鱼 v{__version__}"}
BALANCE_TTL = 25.0

_cache: dict[str, Any] = {}
_lock = asyncio.Lock()


def _num_or(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num == num else None


async def _unavailable_payload(code: str, error: str, provider: str) -> dict[str, Any]:
    """余额不可用时仍把本地估算账本带下去，页面和用量面板不至于空白。"""
    return {
        "ok": False,
        "code": code,
        "error": error,
        "provider": provider,
        "balanceAvailable": False,
        "totalBalance": None,
        "currency": "CNY",
        "todayUsage": usage.LEDGER.today_total(),
        "todayUsageCurrency": "CNY",
        "usageLabel": "本地估算",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        **VERSION,
    }


async def balance_payload(cfg: dict[str, Any], refresh: bool = False) -> dict[str, Any]:
    """查询 DeepSeek 余额；失败也返回结构化 payload（ok=False + error）。"""
    if not isinstance(cfg, dict):
        cfg = {}
    # 余额接口按 base_url 决定去向：模型名不能把 DeepSeek key 发到第三方地址。
    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    model = str(cfg.get("model") or "").lower()
    if "deepseek" in base.lower():
        provider = "deepseek"
    elif "mimo" in base.lower() or model.startswith("mimo"):
        provider = "mimo"
    else:
        provider = "generic"
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        return await _unavailable_payload(
            "NO_KEY", "尚未配置 API Key，请到「设置 → 模型」填写", provider
        )
    if provider == "mimo":
        return await _unavailable_payload(
            "NO_BALANCE_API",
            "MiMo API Key 没有官方余额查询接口；每轮和今日金额仍按 MiMo 牌价本地估算，"
            "余额/用量请到小米开放平台控制台查看",
            provider,
        )
    if provider != "deepseek":
        return await _unavailable_payload(
            "NOT_DEEPSEEK", "当前模型服务不是 DeepSeek，没有余额可查", provider
        )
    if not refresh:
        cached = _cache.get("payload")
        if cached and time.time() - _cache.get("at", 0.0) < BALANCE_TTL:
            return cached
    async with _lock:
        if not refresh:
            cached = _cache.get("payload")
            if cached and time.time() - _cache.get("at", 0.0) < BALANCE_TTL:
                return cached
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4)) as client:
                resp = await client.get(
                    base + "/user/balance",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.HTTPError as exc:
            return await _unavailable_payload("HTTP", f"余额接口请求失败: {exc}", provider)
        if resp.status_code != 200:
            return await _unavailable_payload(
                "HTTP", f"余额接口请求失败: HTTP {resp.status_code}", provider
            )
        try:
            data = resp.json()
        except ValueError:
            data = None
        infos = data.get("balance_infos") if isinstance(data, dict) else None
        info = None
        if isinstance(infos, list) and infos:
            info = next(
                (i for i in infos if isinstance(i, dict) and i.get("currency") == "CNY"),
                infos[0],
            )
        total = _num_or(info.get("total_balance")) if isinstance(info, dict) else None
        if total is None:
            return await _unavailable_payload("SHAPE", "余额接口返回结构异常", provider)
        payload: dict[str, Any] = {
            "ok": True,
            "totalBalance": total,
            "bonusBalance": _num_or(info.get("granted_balance")),
            "rechargeBalance": _num_or(info.get("topped_up_balance")),
            "currency": str(info.get("currency") or "CNY"),
            "provider": provider,
            "balanceSource": "apikey",
            # 今日已用来自本地用量账本（Deep肥鱼 自己调用量按牌价的估算）
            "todayUsage": usage.LEDGER.today_total(),
            "todayUsageCurrency": "CNY",
            "usageLabel": "本地估算",
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            **VERSION,
        }
        _cache["at"] = time.time()
        _cache["payload"] = payload
        return payload
