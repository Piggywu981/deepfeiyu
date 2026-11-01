"""联网找表情包：搜图片直链 → 下载校验 → 入库（md5 去重）→ 自动打标。

来源按序尝试：Bing 图片搜索（覆盖面最广，query 可带 site: 限定贴吧等站点）→
斗图啦 → 发表情。搜到的图落到表情缓存目录，和微信表情共用一套缓存与入库逻辑，
入库后 search_emojis / send_wechat_emoji 立即可用。
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .. import db
from ..emoji import EmojiURLError, content_md5, fetch_emoji
from . import tagger, web

log = logging.getLogger("emoji_search")

CANDIDATES_PER_SOURCE = 12
MAX_ATTEMPTS = 12
MIN_IMAGE_BYTES = 2048
HUNT_TIMEOUT = 20.0

_HEADERS = {"User-Agent": web.USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

# 图床 → Referer 防盗链；不在名单里的图床不带 Referer 直取
_REFERER_RULES: tuple[tuple[str, str], ...] = (
    ("tiebapic.baidu.com", "https://tieba.baidu.com/"),
    ("imgsrc.baidu.com", "https://tieba.baidu.com/"),
    ("bdimg.com", "https://tieba.baidu.com/"),
    ("hdslb.com", "https://www.bilibili.com/"),
    ("doutula.com", "https://www.doutula.com/"),
    ("sinaimg.cn", "https://www.fabiaoqing.com/"),
)

# 只认带图片扩展名的直链（顺手剥掉 !dt-common 这类 CDN 裁剪后缀）
_IMG_URL_RE = re.compile(
    r"^(https?://[^?]+?\.(?:gif|jpe?g|png|webp))(?:![A-Za-z0-9_-]+)?$", re.I
)
# 头像/logo/占位图混在列表页里，按路径关键词排掉
_JUNK_HINTS = ("logo", "avatar", "icon", "loading", "sprite", "blank")


def referer_for(url: str) -> str:
    """按图床域名挑 Referer；不在名单里返回空串（请求时不带 Referer）。"""
    host = (urlparse(url).hostname or "").lower()
    for suffix, ref in _REFERER_RULES:
        if host == suffix or host.endswith("." + suffix):
            return ref
    return ""


def sniff_image_ext(data: bytes) -> str:
    """按魔数认图片格式，返回 .gif/.png/.jpg/.webp；不是这四类返回空串。"""
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def _parse_bing_images(raw: str, limit: int) -> list[dict[str, str]]:
    """Bing 图片搜索页的 iusc 缩略块：m 属性是 HTML 转义的 JSON，murl 即原图直链。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r'\sm="([^"]+)"', raw):
        try:
            data = json.loads(html_lib.unescape(m.group(1)))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        url = str(data.get("murl") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "image_url": url,
                "page_url": str(data.get("purl") or ""),
                "title": str(data.get("t") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def _parse_img_tags(raw: str, limit: int) -> list[dict[str, str]]:
    """表情站列表页通用解析：img 的 data-original 优先于 src，只留带扩展名的公网直链。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r"(?is)<img\b[^>]*>", raw):
        tag = m.group(0)
        src = re.search(r'(?i)\bdata-original="([^"]+)"', tag) or re.search(
            r'(?i)\bsrc="([^"]+)"', tag
        )
        if not src:
            continue
        url = html_lib.unescape(src.group(1)).strip()
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("http://", "https://")):
            continue
        match = _IMG_URL_RE.match(url)
        if not match or any(hint in match.group(1).lower() for hint in _JUNK_HINTS):
            continue
        url = match.group(1)
        if url in seen:
            continue
        seen.add(url)
        alt = re.search(r'(?i)\balt="([^"]*)"', tag)
        out.append(
            {
                "image_url": url,
                "page_url": "",
                "title": html_lib.unescape(alt.group(1)).strip() if alt else "",
            }
        )
        if len(out) >= limit:
            break
    return out


async def _bing_images(query: str, limit: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=8), follow_redirects=True, headers=_HEADERS
    ) as client:
        resp = await client.get(
            "https://www.bing.com/images/search",
            params={"q": query, "mkt": "zh-CN", "count": max(limit * 2, 20)},
        )
    resp.raise_for_status()
    return _parse_bing_images(resp.text, limit)


async def _doutula(query: str, limit: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=8), follow_redirects=True, headers=_HEADERS
    ) as client:
        resp = await client.get("https://www.doutula.com/photo/list/", params={"keyword": query})
    resp.raise_for_status()
    return _parse_img_tags(resp.text, limit)


async def _fabiaoqing(query: str, limit: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=8), follow_redirects=True, headers=_HEADERS
    ) as client:
        resp = await client.get(
            "https://www.fabiaoqing.com/search/bqb/keyword/" + quote(query, safe="") + ".html"
        )
    resp.raise_for_status()
    return _parse_img_tags(resp.text, limit)


async def search_images(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[dict[str, str]]:
    """依序尝试三个来源，返回 [{image_url, page_url, title, source}]；全失败抛 WebError。"""
    limit = max(1, min(int(limit or CANDIDATES_PER_SOURCE), 30))
    failures: list[str] = []
    for name, fn in (("Bing图片", _bing_images), ("斗图啦", _doutula), ("发表情", _fabiaoqing)):
        try:
            results = await fn(query, limit)
        except httpx.HTTPError as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            continue
        except web.WebError as exc:
            failures.append(f"{name}: {exc}")
            continue
        if results:
            for item in results:
                item["source"] = name
            return results
        failures.append(f"{name}: 无结果")
    raise web.WebError("；".join(failures) or "没有可用的图片搜索来源")


def _fetch_and_validate(url: str) -> Path:
    """下载到表情缓存并按魔数校验；不是图/太小直接删掉，后缀不对按魔数修正。"""
    path = fetch_emoji(url, "", timeout=HUNT_TIMEOUT, referer=referer_for(url))
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EmojiURLError(f"缓存读取失败：{exc}") from exc
    ext = sniff_image_ext(data)
    if not ext or len(data) < MIN_IMAGE_BYTES:
        path.unlink(missing_ok=True)
        raise EmojiURLError("不是有效图片或体积太小")
    if path.suffix.lower() != ext:
        fixed = path.with_suffix(ext)
        path.replace(fixed)
        path = fixed
    return path


async def hunt(
    account_id: str, llm: Any, query: str, context: str = "", limit: int = 3
) -> dict[str, Any]:
    """联网找表情主流程，返回 AI 可读摘要；不抛异常，失败都折进返回值。"""
    query = str(query or "").strip()
    if not query:
        return {"error": "query 不能为空"}
    limit = max(1, min(int(limit or 3), 5))
    try:
        candidates = await search_images(query)
    except web.WebError as exc:
        return {"error": f"联网搜索失败：{exc}"}

    found: list[dict[str, Any]] = []
    already = 0
    failed = 0
    attempts = 0
    seen_urls: set[str] = set()
    for cand in candidates:
        if len(found) >= limit or attempts >= MAX_ATTEMPTS:
            break
        url = str(cand.get("image_url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        attempts += 1
        try:
            path = await asyncio.to_thread(_fetch_and_validate, url)
        except Exception:  # noqa: BLE001 下载失败/不是图/内网地址：跳过换下一张
            failed += 1
            continue
        md5 = content_md5(path)
        if md5 and db.get_emoji_by_md5(account_id, md5):
            already += 1
            continue
        row = db.add_emoji(account_id, url, md5=md5)
        if not row:
            failed += 1
            continue
        if getattr(llm, "configured", False):
            try:
                out = await tagger.tag_emoji(llm, str(path), context)
            except Exception as exc:  # noqa: BLE001
                log.warning("联网表情打标失败 #%s: %s", row["id"], exc)
                out = {}
            if out:
                db.update_emoji_tags(
                    account_id, int(row["id"]), ",".join(out["tags"]), str(out.get("description") or "")
                )
                row = db.get_emoji(account_id, int(row["id"])) or row
        found.append(
            {
                "emoji_id": int(row["id"]),
                "tags": str(row.get("tags") or ""),
                "description": str(row.get("description") or ""),
                "source": str(cand.get("source") or ""),
            }
        )

    if not found:
        msg = "没有找到能入库的新表情"
        if already:
            msg += f"（{already} 张已经在库里）"
        if failed:
            msg += f"，另有 {failed} 张下载失败"
        return {"ok": False, "query": query, "message": msg + "，可以换个关键词再试"}
    return {
        "ok": True,
        "query": query,
        "found": found,
        "already_in_library": already,
        "download_failed": failed,
        "note": "新表情已入库，可用 send_wechat_emoji 按 emoji_id 发送",
    }
