"""表情包自动打标：视觉模型看图 + 聊天上下文，产出便于按场景检索的标签。

有视觉就图+上下文一起用；没有视觉退化为纯上下文猜测；两者都没有就不打。
图鉴可用时随请求附带角色参考立绘（缩图），视觉模型对照认出图鉴角色后打名。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .. import config
from . import memes, moe_wiki
from .agent import Agent
from .vision import image_to_data_url, thumb_data_url

log = logging.getLogger("tagger")

MAX_TAGS = 6

SYSTEM = (
    "你是表情包库管理员，负责给微信表情包打标签，让以后能按聊天场景快速找到合适的表情。"
    "你熟悉中文互联网常见梗图，下笔前先认梗。只输出 JSON，不要输出任何其他文字。"
)

INSTRUCTION = (
    "请给这个表情包打标签：\n"
    "1. 先认梗：画面主体是知名梗图/角色（如 奶蛙、滑稽脸、熊猫头、金馆长、蘑菇头，详见梗百科）"
    "就用它的公认名字打标，认不出再按画面描述，不要张冠李戴；图里自带的文字也要读出来。\n"
    "2. tags：3-6 个短标签，覆盖梗名/角色名、情绪（如 无语/破防/开心）、"
    "场景（如 加班/摸鱼/道晚安）、画面主体（如 熊猫头/猫咪）。\n"
    "3. 词表优先：如果库内已有标签词表里有贴合的词，必须原样复用词表写法，"
    "不要造同义词变体（词表有「无语」就不要写「无言」「无话可说」）；"
    "词表实在没有才新造，新词要通用（情绪/场景/大类主体），不要一次性的超具体词。\n"
    "4. description：一句话说清「什么梗/什么形象 + 画面 + 常用来表达什么」。\n"
    "5. 如果图片根本不是表情包，而是普通照片（人物/风景/宠物照）、聊天截图、"
    "二维码、证件文档等没有表情用途的内容，输出 "
    '{"not_sticker": true}，tags 和 description 留空。\n'
    '只输出 JSON：{"tags": [".."], "description": ".."} 或 {"not_sticker": true}'
)


def _extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def parse_tag_reply(text: str) -> dict[str, Any]:
    """防御式解析打标输出；没有可用标签也不是拒收判定就返回空 dict。"""
    if not text:
        return {}
    data = _extract_json(text)
    if not data:
        return {}
    # 拒收判定优先：模型认为不是表情包时忽略它顺手给的标签
    if data.get("not_sticker") or data.get("is_sticker") is False:
        return {"not_sticker": True}
    tags: list[str] = []
    for item in (data.get("tags") or [])[:MAX_TAGS]:
        tag = str(item or "").strip()[:20]
        if tag:
            tags.append(tag)
    if not tags:
        return {}
    description = str(data.get("description") or data.get("desc") or "").strip()[:200]
    return {"tags": tags, "description": description}


def build_context(rows: list[dict[str, Any]], limit: int = 6) -> str:
    """用最近聊天记录组一段上下文，让打标模型知道表情是在什么氛围里出现的。"""
    lines: list[str] = []
    for r in rows:
        content = (r.get("content") or "").strip()
        if not content:
            continue
        who = "我" if r.get("is_self") else (r.get("sender_name") or "对方")
        lines.append(f"{who}：{content}")
    return "\n".join(lines[-limit:])[:400]


def build_tag_prompt(
    vocab: list[str] | None = None,
    context: str = "",
    refs: list[dict[str, Any]] | None = None,
) -> str:
    """打标的用户提示词：指令 + 梗百科 + 形象图鉴 + 参考立绘清单 + 词表 + 聊天上下文。

    refs 是实际随消息附上的参考立绘（已过缩图成功筛选，顺序即附图顺序）；
    None 表示本次不附参考图，清单也不注入，保证图文对得上。
    """
    user_text = INSTRUCTION + f"\n\n{memes.prompt_block()}"
    wiki = moe_wiki.tag_block()
    if wiki:
        user_text += "\n\n" + wiki
        # 仅AI娘表情包模式下角色名标签决定表情能否被使用，给打标额外的权重提示
        try:
            wiki_only = bool(config.load()["ai"].get("wiki_emoji_only", False))
        except Exception:  # noqa: BLE001
            wiki_only = False
        if wiki_only:
            user_text += (
                "\n【仅AI娘表情包模式已开启】tags 里有没有图鉴角色名，"
                "决定这张表情以后能不能被 AI 挑中发送：认得出图鉴角色就务必打上它的名字；"
                "画面确实与图鉴角色无关才只打普通标签。"
            )
    if refs:
        user_text += "\n\n" + moe_wiki.tag_refs_block(refs)
    if vocab:
        user_text += "\n\n【库内已有标签词表（优先原样复用）】\n" + "、".join(vocab[:120])
    if context.strip():
        user_text += f"\n\n【它出现的聊天上下文】\n{context.strip()[:400]}"
    return user_text


def _ref_image_parts(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """参考立绘缩图 → image_url parts；缩图失败的跳过（清单同步跳过，保持图文对齐）。"""
    parts = []
    for r in refs:
        url = thumb_data_url(r["path"])
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


async def tag_emoji(
    llm: Any,
    image_path: str | None = None,
    context: str = "",
    vocab: list[str] | None = None,
) -> dict[str, Any]:
    """单次 LLM 调用打标；图和上下文至少要有一个，否则返回空。失败返回空 dict。

    vocab 是库内已有标签词表（按频次降序的前缀），注入提示词让模型优先复用
    已有写法，避免同义标签发散。
    """
    if not getattr(llm, "configured", False):
        return {}
    data_url = image_to_data_url(image_path) if image_path else None
    if data_url is None and not context.strip():
        return {}
    # 参考立绘只在有视觉（能看图）时附带：纯上下文打标看了参考图也没意义，白耗 token
    refs: list[dict[str, Any]] = []
    if data_url:
        refs = moe_wiki.character_refs()
    user_text = build_tag_prompt(vocab=vocab, context=context, refs=refs)
    content: str | list[dict[str, Any]] = user_text
    if data_url:
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        parts.extend(_ref_image_parts(refs))
        content = parts
    agent = Agent(llm, [], max_rounds=1)
    # max_tokens 要给足：思考型模型（如 deepseek-flash）的思考也计入 completion
    # 预算，给少了正文被截成空串，解析不到标签；effort=off 省掉不必要的思考。
    result = await agent.run(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
        max_tokens=1024,
        effort="off",
    )
    if result.get("error"):
        log.warning("表情打标失败: %s", result["error"])
        return {}
    out = parse_tag_reply(result.get("text") or "")
    if not out:
        log.warning(
            "表情打标输出为空或不可解析 (finish_reason=%s): %.200s",
            result.get("finish_reason"),
            result.get("text") or "",
        )
    return out


MERGE_SYSTEM = "你是表情包库的标签管理员，负责把同义/近义标签归并成规范写法。只输出 JSON，不要输出任何其他文字。"


async def consolidate_tags(
    llm: Any, freq: list[tuple[str, int]], chunk: int = 120
) -> dict[str, str]:
    """让 LLM 把同义标签映射到规范写法，返回 {旧标签: 规范标签}（只含需要改的）。

    标签多时分批调用，防止输出 JSON 被截断；规范词优先选批次里使用次数最高的写法。
    """
    if not getattr(llm, "configured", False) or len(freq) < 2:
        return {}
    mapping: dict[str, str] = {}
    known = {t for t, _ in freq}
    for i in range(0, len(freq), chunk):
        part = freq[i : i + chunk]
        lines = "\n".join(f"{t}（{n}次）" for t, n in part)
        user = (
            "下面是表情包库里的标签和使用次数，把意思相同或高度相近的归并成同一个规范词：\n"
            f"{lines}\n\n"
            "规则：\n"
            "1. 规范词必须从本批列表里选，原样照抄次数最多的那个写法，不要改字、不要新造。\n"
            "2. 只有同义或高度相近才归并（如 微笑笑脸/咧嘴笑 → 微笑笑脸）；"
            "意思不同的保持原样，宁可少并不要错并。\n"
            "3. 专名（梗名/角色名/作品名/品牌名）不要并进泛化词。\n"
            '只输出 JSON：{"mapping": {"旧标签": "规范标签"}}，没有需要归并的就输出 {"mapping": {}}'
        )
        agent = Agent(llm, [], max_rounds=1)
        result = await agent.run(
            [
                {"role": "system", "content": MERGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=4096,
            effort="off",
        )
        if result.get("error"):
            log.warning("标签归并批次失败: %s", result["error"])
            continue
        data = _extract_json(result.get("text") or "")
        if not data:
            log.warning("标签归并输出不可解析: %.200s", result.get("text") or "")
            continue
        for old, new in (data.get("mapping") or {}).items():
            old, new = str(old).strip()[:20], str(new).strip()[:20]
            if old in known and new and new != old and new in known:
                mapping[old] = new
    return mapping
