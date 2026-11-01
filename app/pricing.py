"""模型牌价与服务商识别：每轮/今日金额估算的数据源。

DeepSeek 沿用可覆盖的全局 `ai.price_*`；MiMo 已知模型按官方人民币牌价计价，
避免切到 MiMo 后仍按 DeepSeek 全局价格估算。自定义模型可在对应 profile
补同名字段覆盖。官方调价时只需改本文件的 `MIMO_PRICING`。
"""
from __future__ import annotations

from typing import Any

CURRENCY = "CNY"

# 元 / 百万 tokens。来源：小米 MiMo API 按量计费（2026-10-26）。
MIMO_PRICING: dict[str, dict[str, float]] = {
    "mimo-v2.6-flash": {"input_hit": 0.02, "input_miss": 1.0, "output": 2.0},
    "mimo-v2.6-pro": {"input_hit": 0.025, "input_miss": 3.0, "output": 6.0},
    "mimo-v2.6-pro-ultraspeed": {"input_hit": 0.25, "input_miss": 30.0, "output": 60.0},
}


def _num(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return max(num, 0.0)


def provider_kind(cfg: dict[str, Any] | None, model: str | None = None) -> str:
    cfg = cfg or {}
    base = (cfg.get("base_url") or "").lower()
    name = (model or cfg.get("model") or "").lower()
    if "deepseek" in base or name.startswith("deepseek"):
        return "deepseek"
    if "mimo" in base or name.startswith("mimo"):
        return "mimo"
    return "generic"


def _global_price(cfg: dict[str, Any]) -> dict[str, float]:
    return {
        "input_hit": _num(cfg.get("price_input_hit")) or 0.0,
        "input_miss": _num(cfg.get("price_input_miss")) or 0.0,
        "output": _num(cfg.get("price_output")) or 0.0,
    }


def _matching_profile(cfg: dict[str, Any], model: str) -> dict[str, Any]:
    profiles = cfg.get("profiles")
    if not isinstance(profiles, list):
        return {}
    name = (model or "").lower()
    for item in profiles:
        if isinstance(item, dict) and str(item.get("model") or "").lower() == name:
            return item
    active = str(cfg.get("active_profile") or "").lower()
    for item in profiles:
        if isinstance(item, dict) and str(item.get("id") or "").lower() == active:
            return item
    return {}


def _profile_price(profile: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    mapping = {
        "input_hit": "price_input_hit",
        "input_miss": "price_input_miss",
        "output": "price_output",
    }
    for key, field in mapping.items():
        value = _num(profile.get(field))
        if value is not None:
            out[key] = value
    return out


def _mimo_price(model: str) -> dict[str, float] | None:
    name = (model or "").lower().strip()
    for key in sorted(MIMO_PRICING, key=len, reverse=True):
        if name == key or name.startswith(key):
            return dict(MIMO_PRICING[key])
    return None


def price_for_model(cfg: dict[str, Any] | None, model: str = "") -> dict[str, Any]:
    """返回本次调用的计价口径（元/百万 tokens）。"""
    cfg = cfg or {}
    # 模型名比 base_url 更能说明“这笔钱该按哪家价目算”：代理地址也可能跑 MiMo。
    builtin = _mimo_price(model)
    price = builtin or _global_price(cfg)
    source = "mimo_builtin" if builtin else "global"

    override = _profile_price(_matching_profile(cfg, model))
    if override:
        price.update(override)
        source = "profile"
    return {**price, "source": source}


def cost_for_usage(cfg: dict[str, Any] | None, model: str, usage: dict[str, Any]) -> float | None:
    """按当前模型牌价估算本次调用金额；结构异常返回 None。"""
    try:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        miss = int(usage.get("prompt_cache_miss_tokens") or max(prompt - hit, 0))
        price = price_for_model(cfg, model)
        cost = (
            hit * float(price["input_hit"])
            + miss * float(price["input_miss"])
            + completion * float(price["output"])
        ) / 1_000_000
        return round(max(cost, 0.0), 8)
    except (TypeError, ValueError):
        return None
