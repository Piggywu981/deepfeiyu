"""联网能力：网页搜索、正文抓取、文件下载（落到 data/downloads）。"""
from __future__ import annotations

import base64
import html as html_lib
import ipaddress
import re
import socket
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..config import DATA_DIR

DOWNLOAD_DIR = DATA_DIR / "downloads"
MAX_FETCH_CHARS = 8000
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 DeepFeiyu/1.0"
)
_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

_EXT_BY_TYPE = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/html": ".html",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
}


class WebError(Exception):
    """联网失败，文案直接回给模型。"""


def _assert_public(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebError("只支持 http / https 链接")
    if not parsed.hostname:
        raise WebError("链接缺少主机名")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError as exc:
        raise WebError(f"无法解析域名 {parsed.hostname}：{exc}") from exc
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise WebError("拒绝访问本机或内网地址")
    return url


MAX_REDIRECTS = 5


def _next_target(resp: httpx.Response, current: str) -> str | None:
    """取下一跳地址并立刻过内网校验；不是重定向就返回 None。

    httpx 的 follow_redirects 会在校验之后自己跟跳，等于绕过 _assert_public，
    所以这里自己跟：每一跳都重新解析、重新判内网。
    """
    location = resp.headers.get("location")
    if not location or resp.status_code not in (301, 302, 303, 307, 308):
        return None
    return _assert_public(str(httpx.URL(current).join(location)))


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|section|article|main|li|ul|ol|tr|h[1-6]|blockquote|pre)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _title_of(raw: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    return html_lib.unescape(_strip_html(match.group(1))).strip() if match else ""


def _unwrap_ddg(href: str) -> str:
    href = html_lib.unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return href


def _parse_ddg(raw: str, limit: int) -> list[dict[str, str]]:
    anchors = re.finditer(
        r"(?is)<a\b[^>]*class=\"[^\"]*result__a[^\"]*\"[^>]*>(.*?)</a>", raw
    )
    snippets = [
        _strip_html(m.group(1))
        for m in re.finditer(
            r"(?is)<a\b[^>]*class=\"[^\"]*result__snippet[^\"]*\"[^>]*>(.*?)</a>", raw
        )
    ]
    results: list[dict[str, str]] = []
    for idx, match in enumerate(anchors):
        tag = match.group(0)
        href_match = re.search(r"href=\"([^\"]+)\"", tag)
        if not href_match:
            continue
        url = _unwrap_ddg(href_match.group(1))
        if not url.startswith(("http://", "https://")):
            continue
        results.append(
            {
                "title": _strip_html(match.group(1)) or url,
                "url": url,
                "snippet": snippets[idx] if idx < len(snippets) else "",
            }
        )
        if len(results) >= limit:
            break
    return results


def _unwrap_bing(href: str) -> str:
    href = html_lib.unescape(href or "")
    parsed = urlparse(href)
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target.startswith("a1"):
            raw = target[2:] + "=" * (-len(target[2:]) % 4)
            try:
                decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "ignore")
                if decoded.startswith(("http://", "https://")):
                    return decoded
            except Exception:
                pass
    return href


def _parse_bing(raw: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for block in re.split(r'(?i)<li class="b_algo"', raw)[1:]:
        block = block.split("</li>")[0]
        heading = re.search(r"(?is)<h2[^>]*>(.*?)</h2>", block)
        if not heading:
            continue
        href = re.search(r'href="([^"]+)"', heading.group(1))
        if not href:
            continue
        url = _unwrap_bing(href.group(1))
        if not url.startswith(("http://", "https://")):
            continue
        snippet = ""
        para = re.search(r"(?is)<p[^>]*>(.*?)</p>", block)
        if para:
            snippet = _strip_html(para.group(1))
        results.append(
            {"title": _strip_html(heading.group(1)) or url, "url": url, "snippet": snippet}
        )
        if len(results) >= limit:
            break
    return results


async def _search_bing(query: str, limit: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=8), follow_redirects=True, headers=_HEADERS
    ) as client:
        resp = await client.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "zh-Hans", "count": max(limit * 2, 10)},
        )
    resp.raise_for_status()
    return _parse_bing(resp.text, limit)


async def _search_ddg(query: str, limit: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15, connect=8), follow_redirects=True, headers=_HEADERS
    ) as client:
        resp = await client.post(
            "https://html.duckduckgo.com/html/", data={"q": query, "kl": "wt-wt"}
        )
    resp.raise_for_status()
    return _parse_ddg(resp.text, limit)


async def search(query: str, limit: int = 5) -> list[dict[str, str]]:
    """Bing 优先，DuckDuckGo 兜底，都不需要 API Key。"""
    limit = max(1, min(int(limit or 5), 10))
    failures: list[str] = []
    for name, fn in (("Bing", _search_bing), ("DuckDuckGo", _search_ddg)):
        try:
            results = await fn(query, limit)
        except httpx.HTTPError as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            continue
        if results:
            return results
        failures.append(f"{name}: 未解析到结果")
    raise WebError("搜索失败（" + "；".join(failures) + "），可能被限流，请稍后再试")


async def fetch_text(url: str, max_chars: int = MAX_FETCH_CHARS) -> dict[str, Any]:
    url = _assert_public(url.strip())
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(25, connect=8), follow_redirects=False, headers=_HEADERS
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                resp = await client.get(url)
                nxt = _next_target(resp, url)
                if nxt is None:
                    break
                url = nxt
            else:
                raise WebError(f"重定向次数超过 {MAX_REDIRECTS} 次，已中止")
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebError(f"抓取失败：{exc}") from exc

    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype.startswith("image/") or ctype.startswith("audio/") or ctype.startswith("video/"):
        raise WebError(f"这是 {ctype} 二进制内容，请改用 download_file 下载")
    raw = resp.text
    text = _strip_html(raw) if ("html" in ctype or "xml" in ctype or not ctype) else raw
    limit = max(500, min(int(max_chars or MAX_FETCH_CHARS), 30000))
    truncated = len(text) > limit
    return {
        "url": str(resp.url),
        "status": resp.status_code,
        "title": _title_of(raw),
        "content": text[:limit],
        "truncated": truncated,
        "chars": len(text),
    }


def _safe_name(name: str) -> str:
    name = unquote(str(name or "")).strip()
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:120]


def _pick_name(
    filename: str | None, resp: httpx.Response, url: str, ctype: str
) -> str:
    candidate = ""
    if filename:
        candidate = _safe_name(filename)
    if not candidate:
        disposition = resp.headers.get("content-disposition") or ""
        star = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
        plain = re.search(r'filename="?([^";]+)"?', disposition, re.I)
        candidate = _safe_name(unquote(star.group(1)) if star else (plain.group(1) if plain else ""))
    if not candidate:
        candidate = _safe_name(Path(urlparse(url).path).name)
    if not candidate:
        candidate = uuid.uuid4().hex[:12] + _EXT_BY_TYPE.get(ctype, "")
    if not Path(candidate).suffix:
        candidate += _EXT_BY_TYPE.get(ctype, "")
    return candidate


def _unique_path(name: str) -> Path:
    target = DOWNLOAD_DIR / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = DOWNLOAD_DIR / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return DOWNLOAD_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"


async def download(
    url: str,
    filename: str | None = None,
    max_mb: int = 50,
) -> dict[str, Any]:
    url = _assert_public(url.strip())
    limit = max(1, min(int(max_mb or 50), 200)) * 1024 * 1024
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target: Path | None = None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=10), follow_redirects=False, headers=_HEADERS
        ) as client:
            resp = None
            for _ in range(MAX_REDIRECTS + 1):
                probe = await client.send(client.build_request("GET", url), stream=True)
                nxt = _next_target(probe, url)
                if nxt is None:
                    resp = probe
                    break
                await probe.aclose()
                url = nxt
            else:
                raise WebError(f"重定向次数超过 {MAX_REDIRECTS} 次，已中止")
            try:
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                target = _unique_path(_pick_name(filename, resp, str(resp.url), ctype))
                total = 0
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(65536):
                        total += len(chunk)
                        if total > limit:
                            raise WebError(f"文件超过 {limit // (1024 * 1024)}MB 上限，已中止")
                        fh.write(chunk)
            finally:
                # 原来由 async with 负责关闭；自己发的请求必须显式关，否则连接泄漏
                await resp.aclose()
    except httpx.HTTPError as exc:
        if target and target.exists():
            target.unlink(missing_ok=True)
        raise WebError(f"下载失败：{exc}") from exc
    except WebError:
        if target and target.exists():
            target.unlink(missing_ok=True)
        raise
    else:
        if not target or total <= 0:
            if target:
                target.unlink(missing_ok=True)
            raise WebError("下载内容为空")
        return {
            "ok": True,
            "path": str(target),
            "name": target.name,
            "size": total,
            "content_type": ctype,
        }
