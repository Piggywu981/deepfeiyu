"""端到端验证：真机 API + 长输出任务，看被 800 掐断后 Agent 能不能自己续上。

会花掉一点 API 额度（约几分钱），按需手动跑：
    python tools/verify_agent_truncation.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402
from app.ai.agent import Agent  # noqa: E402
from app.ai.llm import LLM  # noqa: E402


class CountingLLM:
    def __init__(self, inner: LLM) -> None:
        self.inner = inner
        self.attempts: list[int] = []

    async def stream(self, messages, tools=None, **kwargs):
        self.attempts.append(kwargs.get("max_tokens"))
        async for evt in self.inner.stream(messages, tools=tools, **kwargs):
            yield evt


LONG_NOTE = (
    "产品群今天的记录：上午讨论了会员体系改版。小王说现在的三档会员转化率低，"
    "建议把中间档拆成月付和季付两种，价格分别是 18 和 48；小李反对，认为拆档会让支付页变长，"
    "要求先做 A/B；设计那边说这周排不出人，最快下周三出图；运营提了个诉求，希望续费提醒提前 3 天推送，"
    "并且要能选微信服务号还是短信；后端说提醒通道改造要 2 天，但要先确认推送配额；"
    "老板最后说这周必须定方案，周五给他结论，另外把上季度流失的 1200 个会员做一次召回实验，"
    "预算不超过 5000 块。请把这些整理成三点结论，每点都要写清楚结论、依据、待办和负责人，"
    "再单独给一份周五之前的排期表，写详细一点，方便直接转发给老板。"
)


async def one(cfg, prompt: str, label: str) -> bool:
    llm = CountingLLM(LLM(lambda: cfg))
    events: list[dict] = []

    async def on_event(evt):
        events.append(evt)

    result = await Agent(llm, [], max_rounds=3).run(
        [{"role": "user", "content": prompt}], on_event=on_event, max_tokens=800
    )
    text = result.get("text", "")
    print(
        f"\n--- {label} --- 每轮 max_tokens={llm.attempts} "
        f"truncated={result.get('truncated')} 正文字符={len(text)}"
    )
    print("正文尾部:", repr(text[-140:]))
    print("note:", [e["text"] for e in events if e["type"] == "note"])
    assert text.strip() or any(e["type"] == "note" for e in events), "既没正文又没提示 = 老 bug 复现"
    return bool(text.strip()) and not result.get("truncated")


async def main() -> None:
    cfg = config.load()
    if await one(cfg, LONG_NOTE, "长输出任务（真实形态）"):
        print("\n结论：800 被掐断后自动提到 2048 重跑，这一轮把完整答案吐出来了，不再静默收尾。")
        return
    print("\n结论：这一轮仍被截断，但给出了显式提示，不会再假装回答完了。")


if __name__ == "__main__":
    asyncio.run(main())
