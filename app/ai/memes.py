"""梗百科：教 AI 认识表情包里的梗，打标和聊天两条链路共用。

三层数据：内置速查（兜底）＋ 独立知识库 meme_kb.json（meme-kb 项目定期更新产出，
萌百词条与 CHIME 文字梗）＋ 用户私梗 data/meme_dict.md（最高优先，适合圈子黑话）。
"""
from __future__ import annotations

import json
import logging

from ..config import DATA_DIR

log = logging.getLogger("memes")

MEME_FILE = DATA_DIR / "meme_dict.md"
KB_FILE = DATA_DIR / "meme_kb.json"
MAX_CHARS = 3600
MAX_KB_LINES = 60

TEMPLATE = """\
# 梗百科：一行一个梗，格式「梗名：画面特征；含义/用法」
# AI 给表情包打标和聊天看图都会读到这份文件，把你圈子里的私梗写在这里它就认识了。
# 例：鲸鲸：蓝发女仆小人张嘴傻笑；傻乐、装可爱
"""

BUILTIN = """\
- 奶蛙：黄色胖龙捧腹大笑（大笑奶龙/变异奶龙变体，2026 热梗）或抱奶瓶喝奶的小青蛙，按画面认；傻乐、卖萌
- 滑稽脸：黄褐色圆脸、弯眉眯眼、嘴咧到耳根（贴吧经典）；调侃、玩梗、贱笑。圆脸不是青蛙
- 熊猫头：熊猫头上P一张金馆长式大笑人脸；吐槽、嘲讽、无语
- 金馆长：韩国演员崔成国的魔性大笑脸；狂笑、嘲笑
- 蘑菇头：发型像蘑菇盖的卡通小人；贱萌、吐槽
- 悲伤蛙：绿色大嘴蛙嘴角下撇（Pepe）；丧、难过、破防
- 小蓝：蓝色圆头小人；尴尬、无语、暗中观察
- 狗头：柴犬狗头；反讽、抖机灵保命
- 微笑黄脸：微信自带黄色圆脸微笑；阴阳怪气、表面和善
- 鹦鹉兄弟：两只黄脸玄凤鹦鹉张嘴；魔性、兴奋
"""


def _kb_lines() -> list[str]:
    """独立知识库的注入条目（meme-kb 项目标记 inject 的萌百词条）；缺文件或损坏都当不存在。"""
    try:
        data = json.loads(KB_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if data.get("version") != 1 or not isinstance(entries, list):
        return []
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("inject") or not entry.get("name"):
            continue
        summary = " ".join(str(entry.get("summary") or "").split())[:120]
        lines.append(f"- {entry['name']}：{summary}" if summary else f"- {entry['name']}")
        if len(lines) >= MAX_KB_LINES:
            break
    return lines


def load_meme_knowledge() -> str:
    """内置速查 + 知识库条目 + 用户私梗；meme_dict 缺失时落模板，总量超长截断。"""
    text = BUILTIN.strip()
    kb = _kb_lines()
    if kb:
        text += "\n" + "\n".join(kb)
    try:
        if not MEME_FILE.exists():
            MEME_FILE.write_text(TEMPLATE, encoding="utf-8")
        else:
            user = "\n".join(
                ln.strip()
                for ln in MEME_FILE.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            )
            if user:
                text += "\n" + user
    except OSError as exc:
        log.warning("读取梗百科失败: %s", exc)
    return text[:MAX_CHARS]


def prompt_block() -> str:
    """拼进系统/用户提示词的梗知识块；始终有内置内容，不会返回空串。"""
    return "【梗百科】认表情包时先对照下面的速查，知名梗用它的公认名字理解：\n" + load_meme_knowledge()
