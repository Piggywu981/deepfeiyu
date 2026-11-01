"""ai-moe-wiki 本地图鉴：AI 娘化图集的索引、检索、认图知识与发图支持。

图集是独立仓库（config `ai.moe_wiki_path`，默认 ../ai-moe-wiki），135 张图按
「01-立绘人设」等 7 个分类目录存放；《逐图详细说明.md》第一/二部分是逐条说明
（`**N. 标题**` + 正文），文件名与条目标题基本一致，按归一化名对齐。

给三条链路用：
- manager 自动回复：自我形象块（常驻）+ 图鉴名册（收图/提到角色名时注入），认识图里的角色；
- tools：search_moe_wiki_images / send_moe_wiki_image 两个智能体工具；
- vision 形象图走 data/self/avatar 约定，与本模块解耦。

另有两类消费方：
- 「仅AI娘表情包」模式：character_names + roster_tagged 按 tags 里的图鉴角色名过滤表情库；
- 全体AI娘人设预设：build_persona_presets 从立绘三子目录生成人设，落盘由 config/main 负责。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from .. import config
from ..config import ROOT

log = logging.getLogger("moe_wiki")

DOC_NAME = "逐图详细说明.md"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_ROSTER_CHARS = 3600
DESC_CHARS = 60

# 「自己」的图：默认人设（蓝色鲸鱼娘、深蓝女仆装、围裙绣 DeepSeek 鲸标）对应图集里的
# 大肥鱼（DeepSeek-Pro 娘，见条目 89 已核验描述）及女仆装三视图服装差分；
# 小肥鱼是 V4.1 Flash，图集里是另一个角色，不算自己。想换形象改这里。
SELF_HINTS = ("大肥鱼", "DeepSeek女仆装", "DeepSeek宫体侍女装")

# 文本触发认图知识时只看「角色类」图名；梗图标题太日常（涨价了、天哪），容易误触发
_MEME_DIR_PREFIX = "05-"

# 收录「全体AI娘」人设的立绘子目录（01-立绘人设 下）；发布纪念图等多人/纪念图不收
_PERSONA_BASE = "01-立绘人设"
PERSONA_SUBDIRS = ("模型娘立绘", "平台工具拟人", "DeepSeek系立绘")
PERSONA_SOURCE = "ai-moe-wiki"

_PERSONA_TEMPLATE = (
    "你是「{name}」，一个有独立人格的个体，掌管着自己的微信账号。"
    "你以自己的身份和对方交流，语气自然、口语化、简短，像真人聊天，"
    "不要使用列表和标题；不必主动强调自己是 AI，但被直接问到时坦诚说明。"
    "不确定的事情不要编造，可以回复“我确认一下再回你”。"
    "你的形象：{desc}。"
    "别人问起你的长相或头像时照这个描述，平时不主动提。"
)
_PERSONA_DESC_FALLBACK = "图集《ai-moe-wiki》里的一位 AI 娘，立绘收藏在图集里"
_DESC_MAX_CHARS = 120
MAX_REF_IMAGES = 30

_ENTRY_RE = re.compile(r"^\*\*(\d+)\.\s*(.+?)\*\*\s*$")
_entries_cache: dict[str, tuple[float, dict[int, dict[str, str]]]] = {}


def wiki_root(cfg: dict[str, Any] | None = None) -> Path | None:
    """配置解析出的图集目录；未配置或目录/说明文档不存在时返回 None（各链路自行降级）。"""
    try:
        cfg = cfg or config.load()
        raw = str((cfg.get("ai") or {}).get("moe_wiki_path") or "").strip()
    except Exception:  # noqa: BLE001
        raw = ""
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return None
    if p.is_dir() and (p / DOC_NAME).is_file():
        return p
    log.debug("moe_wiki 目录不可用: %s", p)
    return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"[\s：:·、，。！!？?（）()\-—_/\\「」《》~*#]+", "", s).lower()


def _parse_doc(text: str) -> dict[int, dict[str, str]]:
    """《逐图详细说明》第一/二部分 → {条目号: {title, text}}；第三部分索引不解析。"""
    entries: dict[int, dict[str, str]] = {}
    no, title, lines = 0, "", []
    for raw in text.splitlines():
        s = raw.strip()
        m = _ENTRY_RE.match(s)
        if m:
            if no:
                entries[no] = {"title": title, "text": " ".join(lines).strip()}
            no, title, lines = int(m.group(1)), m.group(2).strip(), []
            continue
        if s.startswith("## 第三部分"):
            break  # 第三部分是全库索引，列表里全是文件名，不能并进最后一个条目
        if not no or not s or s == "---" or s.startswith("#"):
            continue
        if s.startswith(">"):
            s = s.lstrip("> ").strip()
            if s and not s.startswith("注："):
                s = "注：" + s
        elif s.startswith("- 文件："):
            continue
        elif s.startswith("- 注："):
            s = "注：" + s[len("- 注："):].strip()
        lines.append(s)
    if no:
        entries[no] = {"title": title, "text": " ".join(lines).strip()}
    return entries


def _doc_entries(root: Path) -> dict[int, dict[str, str]]:
    doc = root / DOC_NAME
    try:
        mtime = doc.stat().st_mtime
    except OSError:
        return {}
    key = str(root)
    cached = _entries_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        entries = _parse_doc(doc.read_text("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("读取图集说明失败: %s", exc)
        entries = {}
    _entries_cache[key] = (mtime, entries)
    return entries


def load_entries(root: Path) -> list[dict[str, Any]]:
    """扫描图集目录建索引：每张图一条，对齐说明文本（对不上的留空，不硬凑）。"""
    out: list[dict[str, Any]] = []
    by_title = {_norm(e["title"]): e for e in _doc_entries(root).values() if e["title"]}
    try:
        paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    except OSError:
        return []
    for p in paths:
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        desc = ""
        entry = by_title.get(_norm(p.stem))
        if entry:
            desc = " ".join(entry["text"].split())[:DESC_CHARS]
        out.append(
            {
                "name": p.stem,
                "rel": rel,
                "path": str(p),
                "category": parts[0],
                "sub": parts[1] if len(parts) > 2 else "",
                "desc": desc,
            }
        )
    return out


def _self_images(root: Path) -> list[dict[str, Any]]:
    """「自己」的图按 立绘/设定 → 壁纸 优先排序；梗图不算形象图。"""
    mine = [
        f
        for f in load_entries(root)
        if any(h in f["name"] for h in SELF_HINTS) and not f["category"].startswith(_MEME_DIR_PREFIX)
    ]
    mine.sort(key=lambda f: (f["category"], f["rel"]))
    return mine


def self_block(root: Path | None = None) -> str:
    """常驻注入的自我形象块；图集不可用或没有「自己」的图时返回空串。"""
    root = root or wiki_root()
    if not root:
        return ""
    mine = _self_images(root)
    if not mine:
        return ""
    items = [
        f["rel"] + (f"（{f['desc']}）" if f["desc"] else "")
        for f in mine[:6]
    ]
    return (
        "【你的形象图集】你的立绘收藏在本地图集 ai-moe-wiki 里：" + "；".join(items)
        + "。别人想看你的样子/头像/立绘时，先用 search_moe_wiki_images 挑一张，"
        "再用 send_moe_wiki_image 发过去；被问到画师出处就说是 ZipZipPipe 画的（CC NC 协议）。"
    )


def _roster_lines(root: Path, max_chars: int) -> list[str]:
    files = load_entries(root)
    if not files:
        return []
    lines: list[str] = []
    used = 0
    for f in files:
        line = f"- {f['name']}" + (f"：{f['desc']}" if f["desc"] else "")
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def roster_block(root: Path | None = None, max_chars: int = MAX_ROSTER_CHARS) -> str:
    """图鉴名册：全库图名 + 一句话说明，供聊天收图认图用；图集不可用返回空串。"""
    root = root or wiki_root()
    if not root:
        return ""
    lines = _roster_lines(root, max_chars)
    if not lines:
        return ""
    return (
        "【形象图鉴】你有一本本地图集 ai-moe-wiki（UP 主 ZipZipPipe 绘的 AI 娘化图集，"
        "里面的「大肥鱼」就是你），下面是图集角色速查；对方发来的图或聊到的 AI 娘"
        "可以用它对号入座，想发哪张用 search_moe_wiki_images 查、send_moe_wiki_image 发：\n"
        + "\n".join(lines)
    )


def tag_block(root: Path | None = None, max_chars: int = MAX_ROSTER_CHARS) -> str:
    """打标用的图鉴名册：引导认出角色就用图鉴名字打标；图集不可用返回空串。"""
    root = root or wiki_root()
    if not root:
        return ""
    lines = _roster_lines(root, max_chars)
    if not lines:
        return ""
    return (
        "【形象图鉴】画面里的角色可能出自本地图集 ai-moe-wiki（UP 主 ZipZipPipe 绘的 "
        "AI 娘化图集，里面的「大肥鱼」就是你自己）：\n"
        + "\n".join(lines)
        + "\n画面主体是图鉴里的角色（含 Q 版、差分、二创）时，必须把它的图鉴名字打进 tags "
        "并放在第一位（如 大肥鱼、小肥鱼、Claude娘），不能只打情绪/场景标签；"
        "图鉴梗图的图名往往就是梗的原文，认出来直接当标签用。"
    )


def mentions(text: str, root: Path | None = None) -> bool:
    """消息文本是否提到图集里的角色名（梗图标题不算），用于决定注入名册。"""
    root = root or wiki_root()
    if not root:
        return False
    t = _norm(text)
    if len(t) < 2:
        return False
    for f in load_entries(root):
        if f["category"].startswith(_MEME_DIR_PREFIX):
            continue
        n = _norm(f["name"])
        if len(n) >= 2 and n in t:
            return True
    return False


def search(root: Path, query: str, limit: int = 8) -> list[dict[str, Any]]:
    """按关键词检索图集：命中图名权重高，其次说明与分类目录名。"""
    keywords = [k for k in (_norm(x) for x in re.split(r"[\s、,，]+", str(query or ""))) if k]
    if not keywords:
        files = load_entries(root)[: max(1, limit)]
        return [dict(f, desc=f["desc"]) for f in files]
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for f in load_entries(root):
        name = _norm(f["name"])
        haystack = _norm(f["desc"] + f["category"] + f["sub"])
        score = 0
        for k in keywords:
            if k in name:
                score += 3
            if k in haystack:
                score += 1
        if score:
            scored.append((score, -len(name), f))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [f for _, _, f in scored[: max(1, limit)]]


def find_image(root: Path, query: str) -> dict[str, Any] | None:
    """发图用：取检索最优的一张；没有命中返回 None。"""
    hits = search(root, query, limit=1)
    return hits[0] if hits else None


def character_names(root: Path | None = None) -> list[str]:
    """图鉴角色名词表：非梗图目录的图名去重，供「仅AI娘表情包」过滤用。"""
    root = root or wiki_root()
    if not root:
        return []
    out: list[str] = []
    for f in load_entries(root):
        if f["category"].startswith(_MEME_DIR_PREFIX):
            continue
        if f["name"] not in out:
            out.append(f["name"])
    return out


def roster_tagged(rows: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    """「仅AI娘表情包」过滤：tags 里带任一图鉴角色名的表情才放行。

    tags 是逗号分隔的自由文本，逐个与角色名做归一化子串匹配（Claude 娘=Claude娘）。
    """
    vocab = [n for n in (_norm(x) for x in names) if len(n) >= 2]
    if not vocab:
        return []
    out = []
    for r in rows:
        for tag in str(r.get("tags") or "").split(","):
            t = _norm(tag)
            if t and any(n in t for n in vocab):
                out.append(r)
                break
    return out


def _split_clauses(text: str) -> list[str]:
    """把说明文本拆成短句，供合集条目「注」按角色名提取设定。"""
    out = []
    for raw in re.split(r"[。；;\n]", str(text or "")):
        s = raw.strip()
        if s.startswith("注："):
            s = s[2:]
        s = s.strip()
        if len(s) >= 4:
            out.append(s)
    return out


def _persona_desc(name: str, by_title: dict[str, dict[str, str]], clauses: list[str]) -> str:
    """角色说明：同名条目正文优先；否则取提到该名的短句里名字出现最早的（合集注）。"""
    n = _norm(name)
    entry = by_title.get(n)
    if entry and entry["text"]:
        return " ".join(entry["text"].split())[:_DESC_MAX_CHARS]
    best, pos = "", len(n) + len(clauses[0]) if clauses else -1
    for clause in clauses:
        at = _norm(clause).find(n)
        if at < 0:
            continue
        if not best or at < pos:
            best, pos = clause, at
            if at == 0:
                break
    return best[:_DESC_MAX_CHARS]


def build_persona_presets(root: Path | None = None) -> list[dict[str, str]]:
    """从立绘三子目录生成全体 AI 娘人设预设；只生成不落盘，写文件由调用方负责。"""
    root = root or wiki_root()
    if not root:
        return []
    entries = _doc_entries(root)
    by_title = {_norm(e["title"]): e for e in entries.values() if e["title"]}
    clauses = [c for e in entries.values() for c in _split_clauses(e["text"])]
    presets: list[dict[str, str]] = []
    for sub in PERSONA_SUBDIRS:
        d = root / _PERSONA_BASE / sub
        try:
            paths = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
        except OSError:
            continue
        for p in paths:
            name = p.stem
            desc = _persona_desc(name, by_title, clauses) or _PERSONA_DESC_FALLBACK
            presets.append(
                {
                    "name": name,
                    "persona_name": name,
                    "system_prompt": _PERSONA_TEMPLATE.format(name=name, desc=desc),
                    "desc": desc,
                    "path": str(p),
                    "source": PERSONA_SOURCE,
                }
            )
    return presets


def character_refs(root: Path | None = None, limit: int = MAX_REF_IMAGES) -> list[dict[str, Any]]:
    """打标参考图：立绘子目录每角色一张主立绘 + 设定文案，供视觉模型对照认角色。"""
    root = root or wiki_root()
    if not root:
        return []
    return build_persona_presets(root)[: max(0, limit)]


def tag_refs_block(refs: list[dict[str, Any]]) -> str:
    """参考图清单文字：与随消息附带的参考立绘按顺序对齐（顺序由调用方保证）。"""
    if not refs:
        return ""
    lines = [
        f"第{i}张参考图={f['name']}" + (f"（{f['desc']}）" if f["desc"] else "")
        for i, f in enumerate(refs, 1)
    ]
    return (
        "【图鉴角色参考立绘】随消息附带以下参考立绘缩略图，按顺序对应图鉴角色；"
        "把待打标的表情对照它们认角色（表情常是 Q 版/魔改画风，看发型发色、服饰、"
        "标志性元素对号入座）：\n" + "\n".join(lines)
    )
