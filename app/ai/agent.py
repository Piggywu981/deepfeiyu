"""轻量智能体运行时：流式输出 + Function Calling 工具循环。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .llm import LLM, LLMError

log = logging.getLogger("agent")

# 思考型模型（MiMo / DeepSeek）的 reasoning 与正文共用 max_tokens 预算，
# 单轮被掐断时 finish_reason 是 "length"：此时正文可能一个字都没有，
# 也可能只剩半句、工具参数只剩半截 JSON。旧代码把这种轮次当成「回答完了」，
# 于是出现「只有思考不回复」和「话说一半就断」。
RETRY_MIN_TOKENS = 2048
RETRY_MAX_TOKENS = 8192
DEFAULT_MAX_TOKENS = 800
TRUNCATED_NOTE = (
    "模型这轮的思考把输出额度占满了，回复被截断，后面的步骤没做完。"
    "可以再发一次「继续」，或把思考档位调低一档。"
)


def _args_ok(raw: str) -> bool:
    """工具参数是不是一段能解析的 JSON（空串算能解析）。"""
    raw = (raw or "").strip()
    if not raw:
        return True
    try:
        json.loads(raw)
        return True
    except json.JSONDecodeError:
        return False


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool],
        max_rounds: int = 5,
        truncation_retries: int = 2,
    ) -> None:
        self.llm = llm
        self.tools = {t.name: t for t in tools}
        self.max_rounds = max_rounds
        # 单轮被长度上限掐断时，加大额度重试的次数
        self.truncation_retries = truncation_retries

    async def run(
        self,
        messages: list[dict[str, Any]],
        on_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        effort: str | None = None,
        emit_full_result: bool = False,
    ) -> dict[str, Any]:
        convo = list(messages)
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": None, "measured": False}
        final_text = ""
        carry = ""  # 被掐断的那半句已经推给前端了，续写时要拼回最终答案
        round_budget = max_tokens

        async def emit(evt: dict[str, Any]) -> None:
            if on_event is None:
                return
            res = on_event(evt)
            if hasattr(res, "__await__"):
                await res  # type: ignore[func-returns-value]

        for round_idx in range(self.max_rounds):
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            calls: dict[int, dict[str, str]] = {}
            finish_reason = ""
            retry_used = 0

            while True:
                text_parts = []
                reasoning_parts = []
                calls = {}
                finish_reason = ""
                try:
                    async for evt in self.llm.stream(
                        convo,
                        tools=[t.schema() for t in self.tools.values()] or None,
                        temperature=temperature,
                        max_tokens=round_budget,
                        model=model,
                        effort=effort,
                    ):
                        kind = evt["type"]
                        if kind == "text":
                            text_parts.append(evt["text"])
                            await emit({"type": "delta", "text": evt["text"]})
                        elif kind == "reasoning":
                            reasoning_parts.append(evt["text"])
                            await emit({"type": "reasoning", "text": evt["text"]})
                        elif kind == "tool_delta":
                            slot = calls.setdefault(evt["index"], {"id": "", "name": "", "args": ""})
                            if evt.get("id"):
                                slot["id"] = evt["id"]
                            if evt.get("name"):
                                slot["name"] = evt["name"]
                            slot["args"] += evt.get("args") or ""
                        elif kind == "usage":
                            usage["measured"] = True
                            usage["prompt_tokens"] += evt.get("prompt_tokens", 0)
                            usage["completion_tokens"] += evt.get("completion_tokens", 0)
                            cost = evt.get("cost")
                            if cost is not None:
                                usage["cost"] = round(float(usage["cost"] or 0.0) + float(cost), 6)
                        elif kind == "finish":
                            finish_reason = evt.get("reason", "")
                except LLMError as exc:
                    await emit({"type": "error", "message": str(exc), "usage": dict(usage)})
                    return {"text": "", "error": str(exc), "usage": usage}

                if finish_reason != "length":
                    break
                # 完整的工具调用是实打实的进度，照常执行；剩下的靠下一轮的满额度
                if calls and all(_args_ok(c["args"]) for c in calls.values()):
                    break
                # 翻倍续跑；但绝不把用户配置的更大额度反向降到 RETRY_MAX_TOKENS
                base = round_budget or DEFAULT_MAX_TOKENS
                next_budget = min(max(base * 2, RETRY_MIN_TOKENS), max(RETRY_MAX_TOKENS, base))
                if retry_used >= self.truncation_retries or next_budget <= base:
                    text = carry + "".join(text_parts)
                    if text:
                        final_text = text
                    await emit({"type": "note", "text": TRUNCATED_NOTE, "usage": dict(usage)})
                    await emit({"type": "done", "text": text, "usage": dict(usage), "truncated": True})
                    return {
                        "text": text,
                        "usage": usage,
                        "finish_reason": finish_reason,
                        "truncated": True,
                    }
                retry_used += 1
                round_budget = next_budget
                log.info("单轮输出被长度上限掐断，第 %d 次把额度提到 %d tokens 续这一轮", retry_used, next_budget)
                partial = "".join(text_parts)
                if partial:
                    convo.append({"role": "assistant", "content": partial})
                    carry += partial

            text = carry + "".join(text_parts)
            round_text = "".join(text_parts)  # 已回填进 convo 的半句不再重复写一遍
            carry = ""
            if text:
                final_text = text

            if not calls:
                await emit({"type": "done", "text": text, "usage": dict(usage)})
                return {"text": text, "usage": usage, "finish_reason": finish_reason}

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": round_text or ""}
            if reasoning_parts:
                assistant_msg["reasoning_content"] = "".join(reasoning_parts)
            tool_calls = []
            for idx in sorted(calls):
                c = calls[idx]
                tool_calls.append(
                    {
                        "id": c["id"] or f"call_{round_idx}_{idx}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                    }
                )
            assistant_msg["tool_calls"] = tool_calls
            convo.append(assistant_msg)

            for tc in tool_calls:
                name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
                await emit({"type": "tool_start", "name": name, "args": args, "id": tc["id"]})

                tool = self.tools.get(name)
                if not tool:
                    result: Any = {"error": f"未知工具 {name}"}
                    ok = False
                else:
                    try:
                        result = await tool.handler(args)
                        ok = not (isinstance(result, dict) and result.get("error"))
                    except Exception as exc:
                        log.warning("工具 %s 执行失败: %s", name, exc)
                        result = {"error": str(exc)}
                        ok = False

                text_result = json.dumps(result, ensure_ascii=False, default=str)
                if len(text_result) > 4000:
                    text_result = text_result[:4000] + "…(截断)"
                convo.append({"role": "tool", "tool_call_id": tc["id"], "content": text_result})
                await emit(
                    {
                        "type": "tool_end",
                        "name": name,
                        "ok": ok,
                        "result": result if emit_full_result or len(text_result) < 1500 else text_result[:1500],
                        "id": tc["id"],
                    }
                )

        if self.tools:
            await emit(
                {
                    "type": "note",
                    "text": f"工具步数已经用完（{self.max_rounds} 步），任务可能没做完，可以让我接着做。",
                    "usage": dict(usage),
                }
            )
        await emit({"type": "done", "text": final_text, "usage": dict(usage)})
        return {"text": final_text, "usage": usage, "finish_reason": "max_rounds"}
