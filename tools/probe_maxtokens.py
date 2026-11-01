"""受控复现：验证 MiMo 在 max_tokens 上限下是否把工具调用截断（finish_reason=length）。

只读探针，不碰微信。用法： python tools/probe_maxtokens.py
"""
import asyncio
import io
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def key() -> str:
    for line in io.open(".env", encoding="utf-8", errors="ignore"):
        if line.startswith("MIMO_API_KEY="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("MIMO_API_KEY 未找到")


async def probe(name: str, messages: list, max_tokens: int, with_tools: bool) -> None:
    payload = {
        "model": "mimo-v2.6-flash",
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    if with_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    text, reason, calls, finish, usage = [], [], {}, "", {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
        async with client.stream(
            "POST",
            "https://api.xiaomimimo.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
            json=payload,
        ) as resp:
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
    t = "".join(text)
    r = "".join(reason)
    print(f"\n=== {name} | max_tokens={max_tokens} ===")
    print(f"finish_reason = {finish!r}")
    print(f"reasoning 字符={len(r)}  content 字符={len(t)}  usage={usage}")
    print(f"content 尾部: {t[-60:]!r}")
    for idx, c in sorted(calls.items()):
        try:
            json.loads(c["args"] or "{}")
            ok = "完整"
        except json.JSONDecodeError:
            ok = "★JSON被截断★"
        print(f"tool_call[{idx}] name={c['name']!r} args={c['args'][:80]!r} {ok}")
    if not calls:
        print("tool_call: 无")


async def main() -> None:
    await probe(
        "A 长文本会不会被掐",
        [{"role": "user", "content": "请写一篇 600 字的散文，主题是深夜的便利店。直接给正文。"}],
        800,
        False,
    )
    msgs = [
        {"role": "system", "content": "你是微信助手，可以先说一句简短的话，再调用工具查资料。"},
        {"role": "user", "content": "鲸鲸那边有新消息，你去看看聊天记录，然后告诉我她说了什么，并逐条分析要不要回、怎么回，说详细点。"},
    ]
    await probe("B 带工具 800", msgs, 800, True)
    await probe("C 带工具 3000", msgs, 3000, True)



if __name__ == "__main__":
    asyncio.run(main())
