"""探针 3：强制模型长时间思考，验证 reasoning 是否计入 max_tokens 并被掐成 finish_reason=length。

用法： python tools/probe_reasoning_cap.py
"""
import asyncio
import io
import json
import os

import httpx

BASE = "https://api.xiaomimimo.com/v1"
MODEL = "mimo-v2.6-flash"

# 一个必须逐步追踪状态、天然会想很长的任务（贴近智能体第 2 轮要复盘聊天记录的场景）
PUZZLE = (
    "我们在玩一个手指游戏，规则：每人左右手各有一个点数，初始都是 1。"
    "用自己的手去碰对方的手：若我的点数 a 碰对方的点数 b，则对方那只手变成 (a+b)，"
    "若结果大于 5，则那只手收回变为空手（空手可以用 1 复活）。"
    "双方轮流出手。下面是过程记录，请你逐步追踪每一只手的点数，最后告诉我现在双方四只手各是多少、该谁出手、"
    "有没有必胜的一手，并解释推理过程：\n"
    "甲左手碰乙左手；乙右手碰甲左手；甲右手碰乙右手；乙左手复活碰甲右手；甲左手碰乙左手；"
    "乙右手碰甲左手；甲右手碰乙左手；乙左手碰甲右手；甲左手碰乙右手；乙右手复活碰甲左手；"
    "甲右手碰乙右手；乙左手碰甲左手；甲左手碰乙左手；乙右手碰甲右手；甲右手碰乙左手；"
    "乙左手复活碰甲左手；甲左手碰乙右手；乙左手碰甲右手；甲右手碰乙左手；乙右手碰甲左手。"
)


async def key() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    for line in io.open(path, encoding="utf-8"):
        if line.startswith("MIMO_API_KEY="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("no key")


async def probe(name: str, max_tokens: int) -> None:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PUZZLE + "\n\n请认真想清楚再回答。"}],
        "stream": True,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    text, reason, finish, usage = [], [], "", {}
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
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                if chunk.get("usage"):
                    usage = chunk["usage"]
    t, r = "".join(text), "".join(reason)
    d = usage.get("completion_tokens_details") or {}
    print(f"\n=== {name} | max_tokens={max_tokens} ===")
    print(f"finish_reason={finish!r} completion={usage.get('completion_tokens')} reasoning_tokens={d.get('reasoning_tokens')}")
    print(f"思考字符={len(r)} 正文字符={len(t)}")
    print(f"思考尾部: {r[-90:]!r}")
    print(f"正文: {t[:120]!r}")


async def main() -> None:
    await probe("F 真实档位 800", 800)
    await probe("G 放宽到 4000", 4000)


if __name__ == "__main__":
    asyncio.run(main())
