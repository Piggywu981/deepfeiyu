"""智能体单轮输出被 max_tokens 掐断（finish_reason=length）时的行为。

背景：MiMo/DeepSeek 这类思考型模型的 reasoning 计入 completion，
800 tokens 的上限会被思考全部吃光，导致「只有思考、没有正文」或「话说半句就停」。
"""
from __future__ import annotations

import asyncio

import pytest

from app.ai.agent import Agent, Tool


class ScriptedLLM:
    """按脚本逐轮回放事件，并记录每轮实际用到的 max_tokens / messages。"""

    def __init__(self, rounds: list[list[dict]]) -> None:
        self.rounds = list(rounds)
        self.calls: list[dict] = []

    async def stream(self, messages, tools=None, **kwargs):
        self.calls.append({"max_tokens": kwargs.get("max_tokens"), "messages": list(messages)})
        for evt in self.rounds.pop(0):
            yield evt


def _run(llm: ScriptedLLM, events: list[dict], **kwargs) -> dict:
    async def on_event(evt):
        events.append(evt)

    return asyncio.run(
        Agent(llm, [], max_rounds=6).run([{"role": "user", "content": "继续回复鲸鲸"}], on_event=on_event, **kwargs)
    )


def test_只有思考没有正文时必须加大额度重试():
    llm = ScriptedLLM(
        [
            [{"type": "reasoning", "text": "想了一大堆"}, {"type": "finish", "reason": "length"}],
            [{"type": "text", "text": "回好了"}, {"type": "finish", "reason": "stop"}],
        ]
    )
    events: list[dict] = []
    result = _run(llm, events, max_tokens=800)

    assert result["text"] == "回好了"
    assert len(llm.calls) == 2, "被掐断后应当重跑这一轮"
    assert llm.calls[1]["max_tokens"] >= 2048, "重试额度要明显大于 800"
    done = next(evt for evt in events if evt["type"] == "done")
    assert done["text"] == "回好了"
    assert not done.get("truncated")


def test_半句被掐断要接着往下写且不重复已吐出的字():
    llm = ScriptedLLM(
        [
            [{"type": "text", "text": "我看看聊天记录："}, {"type": "finish", "reason": "length"}],
            [{"type": "text", "text": "她刚问了游戏怎么算"}, {"type": "finish", "reason": "stop"}],
        ]
    )
    events: list[dict] = []
    result = _run(llm, events, max_tokens=800)

    deltas = [evt["text"] for evt in events if evt["type"] == "delta"]
    assert deltas == ["我看看聊天记录：", "她刚问了游戏怎么算"], "重试不能把旧文本再发一遍"
    assert result["text"].endswith("她刚问了游戏怎么算")
    # 第二轮要把已说出的半句作为 assistant 消息带回去，模型才能接着写
    second = llm.calls[1]["messages"]
    assert second[-1] == {"role": "assistant", "content": "我看看聊天记录："}


def test_重试到顶仍被掐断必须显式告知不能静默收尾():
    truncated = [{"type": "reasoning", "text": "还在想"}, {"type": "finish", "reason": "length"}]
    llm = ScriptedLLM([truncated, truncated, truncated, truncated, truncated])
    events: list[dict] = []
    result = _run(llm, events, max_tokens=800)

    assert result["truncated"] is True
    assert len(llm.calls) == 3, "默认给 2 次重试"
    assert any(evt["type"] == "note" for evt in events), "要给用户一条提示"
    done = next(evt for evt in events if evt["type"] == "done")
    assert done["truncated"] is True


def test_参数被截断的工具调用不能拿空参数去执行():
    executed: list[dict] = []

    async def get_chat_history(args):
        executed.append(args)
        return {"ok": True}

    llm = ScriptedLLM(
        [
            [
                {"type": "tool_delta", "index": 0, "id": "c1", "name": "get_chat_history", "args": '{"chat": "鲸'},
                {"type": "finish", "reason": "length"},
            ],
            [
                {"type": "tool_delta", "index": 0, "id": "c2", "name": "get_chat_history", "args": '{"chat": "鲸鲸"}'},
                {"type": "finish", "reason": "tool_calls"},
            ],
            [{"type": "text", "text": "看完了"}, {"type": "finish", "reason": "stop"}],
        ]
    )
    events: list[dict] = []

    async def on_event(evt):
        events.append(evt)

    result = asyncio.run(
        Agent(llm, [Tool("get_chat_history", "", {}, get_chat_history)], max_rounds=6).run(
            [{"role": "user", "content": "看看鲸鲸的消息"}], on_event=on_event, max_tokens=800
        )
    )

    assert executed == [{"chat": "鲸鲸"}], "半截 JSON 不能当成 {} 去调工具"
    assert not any(evt["type"] == "tool_start" and evt["id"] == "c1" for evt in events)
    assert result["text"] == "看完了"


def test_正常收尾的轮次不受影响():
    llm = ScriptedLLM([[{"type": "text", "text": "在的"}, {"type": "finish", "reason": "stop"}]])
    events: list[dict] = []
    result = _run(llm, events, max_tokens=800)
    assert result["text"] == "在的"
    assert len(llm.calls) == 1
    assert not any(evt["type"] == "note" for evt in events)


def test_配置了更大额度时不会反向降额():
    truncated = [{"type": "reasoning", "text": "还在想"}, {"type": "finish", "reason": "length"}]
    llm = ScriptedLLM([truncated, truncated, truncated])
    events: list[dict] = []
    result = _run(llm, events, max_tokens=10000)

    assert [c["max_tokens"] for c in llm.calls] == [10000], "加不上去就立刻收手，不能降额重跑"
    assert result["truncated"] is True
    assert any(evt["type"] == "note" for evt in events)


def test_工具步数用完也要说一句():
    async def noop(args):
        return {"ok": True}

    call = [
        {"type": "tool_delta", "index": 0, "id": "c", "name": "noop", "args": "{}"},
        {"type": "finish", "reason": "tool_calls"},
    ]
    llm = ScriptedLLM([call, call, call])
    events: list[dict] = []

    async def on_event(evt):
        events.append(evt)

    result = asyncio.run(
        Agent(llm, [Tool("noop", "", {}, noop)], max_rounds=2).run(
            [{"role": "user", "content": "一直查"}], on_event=on_event, max_tokens=800
        )
    )
    assert result["finish_reason"] == "max_rounds"
    assert any(evt["type"] == "note" and "步数" in evt["text"] for evt in events)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
