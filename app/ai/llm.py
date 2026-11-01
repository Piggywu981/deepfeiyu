"""LLM 客户端：OpenAI 兼容协议（DeepSeek / 通义 / Kimi / 本地 vLLM 均可）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable

import httpx

from .. import pricing, usage as usage_ledger


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]]) -> None:
        self._get_cfg = get_cfg

    @property
    def cfg(self) -> dict[str, Any]:
        return self._get_cfg()["ai"]

    @property
    def configured(self) -> bool:
        return bool(self.cfg.get("api_key"))

    def _base(self) -> str:
        base = (self.cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        if base.endswith("/chat/completions"):
            return base[: -len("/chat/completions")]
        return base

    def _url(self) -> str:
        return self._base() + "/chat/completions"

    def _models_url(self) -> str:
        return self._base() + "/models"

    def provider_kind(self, model: str | None = None) -> str:
        return pricing.provider_kind(self.cfg, model)

    def effort_options(self, model: str | None = None) -> list[dict[str, str]]:
        kind = self.provider_kind(model)
        if kind == "deepseek":
            return [
                {"value": "auto", "label": "自动", "desc": "跟随模型默认设置"},
                {"value": "off", "label": "关闭", "desc": "尽量不输出思考过程"},
                {"value": "low", "label": "快速", "desc": "较轻量的思考"},
                {"value": "high", "label": "深入", "desc": "更充分的推理"},
                {"value": "max", "label": "最深", "desc": "模型允许的最大思考量"},
            ]
        if kind == "mimo":
            return [
                {"value": "auto", "label": "自动", "desc": "跟随模型默认设置"},
                {"value": "off", "label": "关闭", "desc": "不启用深度思考"},
                {"value": "on", "label": "开启", "desc": "启用深度思考"},
            ]
        return [
            {"value": "auto", "label": "自动", "desc": "不额外传参，由模型决定"},
            {"value": "off", "label": "关闭", "desc": "尝试关闭思考"},
            {"value": "low", "label": "快速", "desc": "较轻量的思考"},
            {"value": "medium", "label": "标准", "desc": "均衡"},
            {"value": "high", "label": "深入", "desc": "更充分的推理"},
        ]

    def _reasoning_payload(self, effort: str | None, model: str) -> dict[str, Any]:
        level = (effort or "auto").lower()
        if level in ("", "auto"):
            return {}
        kind = self.provider_kind(model)
        if kind == "mimo":
            if level == "off":
                return {"thinking": {"type": "disabled"}}
            return {"thinking": {"type": "enabled"}}
        if kind == "deepseek":
            if level == "off":
                return {"thinking": {"type": "disabled"}}
            if level == "on":
                return {"thinking": {"type": "enabled"}}
            mapped = {"low": "low", "medium": "high", "high": "high", "max": "max"}.get(level)
            return {"reasoning_effort": mapped} if mapped else {}
        if level == "off":
            return {"reasoning_effort": "none"}
        mapped = {"on": "medium", "low": "low", "medium": "medium", "high": "high", "max": "high"}.get(level)
        return {"reasoning_effort": mapped} if mapped else {}

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4)) as client:
                resp = await client.get(self._models_url(), headers=self._headers())
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            mid = item.get("id") if isinstance(item, dict) else None
            if isinstance(mid, str) and mid.strip():
                out.append(mid.strip())
        return sorted(set(out))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.get('api_key', '')}",
            "Content-Type": "application/json",
        }

    def profile_label(self, model: str | None = None) -> str:
        """当前模型配置档的显示名，报错与提示用它，不再写死 DeepSeek。"""
        aid = (self.cfg.get("active_profile") or "").strip()
        for p in self.cfg.get("profiles") or []:
            if isinstance(p, dict) and p.get("id") == aid:
                return (p.get("name") or aid).strip()
        return aid or self.provider_kind(model)

    def _record_usage(self, model: str, u: dict[str, Any]) -> float | None:
        """把本轮真实 usage 按牌价换算成估算金额记进账本（挂件「每轮消耗/今日已用」数据源）。

        返回本次调用成本（元），供 Agent 汇总成「本轮话费」；换算失败返回 None。
        """
        cost = pricing.cost_for_usage(self.cfg, model, u)
        if cost is None:
            return None
        try:
            prompt = int(u.get("prompt_tokens") or 0)
            completion = int(u.get("completion_tokens") or 0)
            usage_ledger.LEDGER.record(model, cost, prompt + completion)
            return round(cost, 8)
        except Exception:  # noqa: BLE001
            return None  # 记账失败不影响对话

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.configured:
            raise LLMError(f"「{self.profile_label()}」还没填 API Key，请到「设置 → 模型」填写")

        model_name = (model or self.cfg.get("model") or "deepseek-chat").strip()
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": self.cfg.get("temperature", 0.7) if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 800),
        }
        payload.update(self._reasoning_payload(effort, model_name))
        # DeepSeek 流式默认不回 usage，要显式要；其余兼容端可能拒收未知参数，只对 deepseek 开。
        # MiMo 真机账本已有 usage，无需额外参数。
        if self.provider_kind(model_name) == "deepseek":
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_error = ""
        for attempt in range(3):
            emitted = False
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
                    async with client.stream(
                        "POST", self._url(), headers=self._headers(), json=payload
                    ) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode("utf-8", "ignore")[:500]
                            if resp.status_code in (401, 403):
                                raise LLMError(f"鉴权失败（{resp.status_code}）：请检查 API Key。{body}")
                            if resp.status_code == 402:
                                raise LLMError(f"账户余额不足：{body}")
                            if resp.status_code == 429:
                                raise LLMError("触发限流（429），请稍后再试")
                            raise LLMError(f"模型接口错误 {resp.status_code}：{body}")

                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choice = (chunk.get("choices") or [{}])[0]
                            delta = choice.get("delta") or {}

                            text = delta.get("content")
                            if text:
                                emitted = True
                                yield {"type": "text", "text": text}
                            reasoning = delta.get("reasoning_content")
                            if not reasoning:
                                for key in ("reasoning", "thinking"):
                                    value = delta.get(key)
                                    if isinstance(value, str) and value:
                                        reasoning = value
                                        break
                            if isinstance(reasoning, str) and reasoning:
                                yield {"type": "reasoning", "text": reasoning}

                            for tc in delta.get("tool_calls") or []:
                                emitted = True
                                yield {
                                    "type": "tool_delta",
                                    "index": tc.get("index", 0),
                                    "id": tc.get("id") or "",
                                    "name": (tc.get("function") or {}).get("name") or "",
                                    "args": (tc.get("function") or {}).get("arguments") or "",
                                }

                            if chunk.get("usage"):
                                u = chunk["usage"]
                                prompt = int(u.get("prompt_tokens") or 0)
                                completion = int(u.get("completion_tokens") or 0)
                                cost = self._record_usage(model_name, u)
                                yield {
                                    "type": "usage",
                                    "prompt_tokens": prompt,
                                    "completion_tokens": completion,
                                    "cost": cost,
                                }
                            if choice.get("finish_reason"):
                                yield {"type": "finish", "reason": choice["finish_reason"]}
                return
            except LLMError:
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_error = f"网络错误：{exc}"
                if emitted or attempt == 2:
                    raise LLMError(last_error) from exc
                await asyncio.sleep(1.2 * (attempt + 1))
            except httpx.HTTPError as exc:
                raise LLMError(f"请求失败：{exc}") from exc

        raise LLMError(last_error or "模型调用失败")
