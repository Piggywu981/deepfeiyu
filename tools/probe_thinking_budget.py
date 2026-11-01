"""探针 2：验证「思考 token 是否吃掉 max_tokens 预算」→ finish_reason=length → 正文/工具调用凭空消失。

用法： python tools/probe_thinking_budget.py
"""
import asyncio
import io
import json
import os
import sys

import httpx

BASE = "https://api.xiaomimimo.com/v1"
MODEL = "mimo-v2.6-flash"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_chat_history",
            "description": "读取某个微信会话的聊天记录",
            "parameters": {
                "type": "object",
                "properties": {"chat_id": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["chat_id"],
            },
        },
    }
]

# 刻意给一个需要长篇权衡、又必须先调工具的任务，逼近真实智能体的第 2 轮
CTX = "背景资料：" + "微信里有一个会话叫鲸鲸，她连着发了七条消息：在吗、你今天有空吗、我想问你个事、" \
    "上次那个表情包还有吗、我这边有点小麻烦、不确定该不该跟你说、你先忙也行。" \
    "另有一个会话叫王倩，是同事，刚发来一份排班表截图。你需要判断先回谁、怎么回、要不要发表情包、" \
    "要不要先查历史记录再决定。" * 6


async def key() -> str:
    for line in io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), encoding="utf-8"):
        if line.startswith("MIMO_API_KEY="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("no key")


async def probe(name: str, max_tokens: int, with_tools: bool) -> None:
    msgs = [
        {"role": "system", "content": "你是微信助手。可以先说一句简短的话，再调用工具查资料，最后给出结论。"},
        {"role": "user", "content": CTX + "\n\n请先仔细想清楚每一步该怎么做，再动手。"},
    ]
    payload = {"model": MODEL, "messages": msgs, "stream": True, "temperature": 0.7, "max_tokens": max_tokens}
    if with_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    text, reason, calls, finish, usage = [], [], {}, "", {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
        async with client.stream("POST", BASE + "/chat/completions",
                                 headers={"Authorization": f"Bearer {await key()}", "Content-Type": "application/json"},
                                 json=payload) as resp:
            if resp.status_code != 200:
                print(f"[{name}] HTTP {resp.status_code}: {(await resp.aread()).decode('utf-8','ignore')[:200]}")
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
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
                if delta.get("content"):
                    text.append(delta["content"])
                if delta.get("reasoning_content"):
                    reason.append(delta["reasoning_content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"name": "", "args": ""})
                    slot["name"] += (tc.get("function") or {}).get("name") or ""
                    slot["args"] += (tc.get("function") or {}).get("arguments") or ""
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                if chunk.get("usage"):
                    usage = chunk["usage"]
    t, r = "".join(text), "".join(reason)
    d = usage.get("completion_tokens_details") or {}
    print(f"\n=== {name} | max_tokens={max_tokens} tools={with_tools} ===")
    print(f"finish_reason={finish!r}  completion={usage.get('completion_tokens')}  reasoning_tokens={d.get('reasoning_tokens')}")
    print(f"思考字符={len(r)}  正文字符={len(t)}  工具调用数={len(calls)}")
    print(f"正文: {t[:80]!r} ... {t[-60:]!r}")
    for i, c in sorted(calls.items()):
        try:
            json.loads(c["args"] or "{}")
            st = "完整"
        except json.JSONDecodeError:
            st = "★截断★"
        print(f"  call[{i}] {c['name']!r} {c['args'][:70]!r} {st}")


async def main() -> None:
    await probe("D 真实档位 800", 800, True)
    await probe("E 放宽到 4000", 4000, True)



if __name__ == "__main__":
    asyncio.run(main())
