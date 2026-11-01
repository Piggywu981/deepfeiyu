"""表情图片下载与本地缓存：控制台代理展示和经通道发送共用。

微信表情 cdnurl 一般是公网 CDN 直链；服务端代取一份缓存在本地，
展示走代理接口（可下载），发送时直接用缓存文件。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import db
from .config import DATA_DIR

log = logging.getLogger("emoji")

EMOJI_DIR = DATA_DIR / "media" / "emojis"

_download_lock = threading.Lock()

MANIFEST_NAME = "manifest.json"
EXPORT_FORMAT = "deepfeiyu-emojis"
EXPORT_VERSION = 1
# 导入限额：单文件 32MB（库内现有最大表情约 16MB）、单次 5000 个、解压总量 1GB
IMPORT_MAX_FILE_BYTES = 32 * 1024 * 1024
IMPORT_MAX_FILES = 5000
IMPORT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_IMPORT_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class EmojiURLError(ValueError):
    pass


class EmojiImportError(ValueError):
    """导入压缩包整体不可用（超限/损坏）；单张坏图只跳过、不抛这个。"""


_EXT_BY_TYPE = {
    "image/gif": ".gif",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def _host_is_public(host: str) -> bool:
    """IP 直接判断；域名能解析时全部地址必须公网，解析失败交给请求阶段报错。"""
    host = (host or "").strip().lower().strip("[]")
    if not host or host in ("localhost", "0.0.0.0") or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # 域名暂时解析不了不算内网地址，请求阶段会给出明确错误
    for info in infos:
        addr = info[4][0]
        try:
            if not ipaddress.ip_address(addr).is_global:
                return False
        except ValueError:
            continue
    return True


def sanitize_emoji_url(url: str) -> str:
    """只允许公网 http/https 直链，防止代理被用来访问内网。"""
    url = str(url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise EmojiURLError("表情链接必须是 http/https 直链")
    if not _host_is_public(parsed.hostname):
        raise EmojiURLError("表情链接不可指向内网地址")
    return url


def cache_path(url: str, md5: str = "") -> Path:
    """缓存文件路径：md5 可信时用 md5，否则用 URL 哈希；兼容已有任意图片后缀。"""
    key = (md5 or "").strip().lower() if _is_md5(md5) else ""
    if not key:
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:32]
    stem = EMOJI_DIR / key
    for ext in (".gif", ".png", ".jpg", ".webp"):
        if stem.with_suffix(ext).is_file():
            return stem.with_suffix(ext)
    return stem.with_suffix(".gif")


def _is_md5(s: str) -> bool:
    s = (s or "").strip().lower()
    return len(s) == 32 and all(c in "0123456789abcdef" for c in s)


def content_md5(path: str | Path) -> str:
    """按文件内容算 md5：微信表情的身份指纹就是内容 md5（与消息 XML 的 md5 属性同源），
    去重要靠它和库里的 md5 对上，不能换用其他哈希算法。消息缺该属性时用作兜底。"""
    try:
        return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def fetch_emoji(url: str, md5: str = "", timeout: float = 15.0, referer: str | None = None) -> Path:
    """下载表情到本地缓存并返回文件路径；已有缓存直接复用。

    md5 命中本地缓存时直接返回（先于 URL 校验）：QQ 缓存导入的表情 url 只是
    local:// 占位，没有可下载的远端，展示和发送全靠缓存文件本身。
    加锁串行化：自动收藏和 AI 回复可能同时抓同一张表情，避免并发写坏缓存文件。
    referer=None 用微信表情 CDN 的默认 Referer；联网找图按图床传对应站点防盗链，
    传空串表示不带 Referer。
    """
    if _is_md5(md5):
        cached = cache_path("", md5)
        if cached.is_file() and cached.stat().st_size > 0:
            return cached
    url = sanitize_emoji_url(url)
    with _download_lock:
        target = cache_path(url, md5)
        if target.is_file() and target.stat().st_size > 0:
            return target

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeepFeiyu/1.0"}
        if referer is None:
            headers["Referer"] = "https://support.weixin.qq.com/"
        elif referer:
            headers["Referer"] = referer
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.content
        if not data:
            raise EmojiURLError("表情下载内容为空")

        ext = _EXT_BY_TYPE.get(resp.headers.get("content-type", "").split(";")[0].strip().lower())
        if ext and target.suffix != ext:
            target = target.with_suffix(ext)
        EMOJI_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target


# ------------------------------------------------------------- QQ 本地缓存导入
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
)

_MIN_EMOJI_BYTES = 512  # 更小的基本是占位图/坏块，不值得入库

_THUMB_SIZE = re.compile(r"_[0-9]+$")  # Thumb 文件名的 _0/_720 尺寸后缀


def sniff_image_ext(data: bytes) -> str:
    """按文件头识别图片类型，返回标准后缀；识别不了返回空串。"""
    for magic, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def qq_cache_roots(base: Path | None = None) -> list[Path]:
    """找到本机所有 QQ 号的 Emoji 缓存根目录（QQNT 结构）。"""
    root = (base or Path.home() / "Documents" / "Tencent Files").expanduser()
    if not root.is_dir():
        return []
    return [
        entry / "nt_qq" / "nt_data" / "Emoji"
        for entry in sorted(root.iterdir())
        if (entry / "nt_qq" / "nt_data" / "Emoji").is_dir()
    ]


def scan_qq_cache_files(base: Path | None = None) -> list[Path]:
    """收集各 QQ 号的候选表情文件。

    Ori 目录的原图是权威来源；Thumb 是压缩副本，只补 Ori 缺的那部分：
    Thumb 文件名带 _0/_720 尺寸后缀（personal_emoji 的不带），去掉后缀与
    Ori 同名的跳过，同一张图的多个 Thumb 变体只留体积最大的。marketface
    原图是腾讯私有格式识别不了，能用的只有 _aio.png 预览图。内置小黄脸
    （BaseEmojiSyastems）的 apng（动图）与 png（静图）同 stem 各一份，留
    体积大的动图；lottie/surprise 是 JSON 动画、根目录是 id 配置，靠魔数
    识别在收编时拦掉。OriTemp/ThumbTemp 临时目录不碰。
    """
    picked: dict[str, Path] = {}

    def take(path: Path, key: str | None = None) -> None:
        key = key or path.stem.lower()
        cur = picked.get(key)
        if cur is None:
            picked[key] = path
            return
        try:
            # 图片优先于非图片：同 stem 的 lottie JSON 体积再大也不能顶掉 png/gif
            with path.open("rb") as f:
                rank = bool(sniff_image_ext(f.read(16)))
            with cur.open("rb") as f:
                cur_rank = bool(sniff_image_ext(f.read(16)))
            if (rank, path.stat().st_size) > (cur_rank, cur.stat().st_size):
                picked[key] = path
        except OSError:
            pass

    def list_files(d: Path) -> list[Path]:
        return [p for p in sorted(d.iterdir()) if p.is_file()] if d.is_dir() else []

    for emoji_root in qq_cache_roots(base):
        pic_root = emoji_root.parent / "Pic"
        has_pic = pic_root.is_dir()
        ori_keys: set[str] = set()
        ori_dirs = [
            emoji_root / "personal_emoji" / "Ori",
            *emoji_root.glob("emoji-recv/*/Ori"),
            *(list(pic_root.glob("*/Ori")) if has_pic else []),
        ]
        for d in ori_dirs:
            for p in list_files(d):
                ori_keys.add(p.stem.lower())
                take(p)
        thumb_dirs = [
            emoji_root / "personal_emoji" / "Thumb",
            *emoji_root.glob("emoji-recv/*/Thumb"),
            *(list(pic_root.glob("*/Thumb")) if has_pic else []),
        ]
        for d in thumb_dirs:
            for p in list_files(d):
                key = _THUMB_SIZE.sub("", p.stem).lower()
                if key not in ori_keys:
                    take(p, key)
        market = emoji_root / "marketface"
        if market.is_dir():
            for p in sorted(market.rglob("*")):
                if p.is_file():
                    take(p)
        builtin = emoji_root / "BaseEmojiSyastems" / "EmojiSystermResource"
        if builtin.is_dir():
            for p in sorted(builtin.rglob("*")):
                if p.is_file():
                    take(p)
    return sorted(picked.values(), key=lambda p: str(p))


def save_emoji_bytes(
    data: bytes, min_bytes: int = _MIN_EMOJI_BYTES
) -> tuple[str, Path] | None:
    """把图片字节按内容 md5 存进表情缓存；非图片/过小返回 None。

    文件名只用内容 md5，不采信调用方给的文件名，压缩包导入因此没有路径穿越面。
    QQ 缓存导入用默认下限滤占位图；用户自己导入的档案传 min_bytes=0，不吞小表情。
    """
    if len(data) < max(0, min_bytes):
        return None
    ext = sniff_image_ext(data)
    if not ext:
        return None
    md5 = hashlib.md5(data).hexdigest()
    target = EMOJI_DIR / f"{md5}{ext}"
    with _download_lock:
        if not target.is_file() or target.stat().st_size == 0:
            EMOJI_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return md5, target


def adopt_emoji_file(src: str | Path) -> tuple[str, Path] | None:
    """把 QQ 缓存里的原图收编进表情缓存：返回 (内容 md5, 缓存路径)。

    QQNT 的 Ori 文件名本身就是内容 md5，但仍按内容重算，防重命名过的文件
    鱼目混珠；非图片或读不出来返回 None。
    """
    try:
        data = Path(src).read_bytes()
    except OSError:
        return None
    return save_emoji_bytes(data)


def discard_emoji_cache(md5: str) -> None:
    """删除某 md5 的本地缓存文件：表情被判定为非表情包剔除后清磁盘用。"""
    if not _is_md5(md5):
        return
    for p in EMOJI_DIR.glob(f"{md5}.*"):
        try:
            p.unlink()
        except OSError:
            log.warning("表情缓存文件删除失败 %s", p)


# --------------------------------------------------- 表情包 zip 导入导出（带标签）
def build_export_zip(rows: list[dict], dest: str | Path) -> dict[str, int]:
    """把表情行打包成带 manifest.json 的 zip；图片取不到的行跳过。

    manifest 只记录标签/描述/尺寸等可迁移数据，不导出账号与 URL（CDN 签名会过期）。
    """
    items: list[dict] = []
    skipped = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
        for row in rows:
            try:
                path = fetch_emoji(str(row.get("url") or ""), str(row.get("md5") or ""))
            except Exception:
                skipped += 1
                continue
            stem = str(row.get("md5") or "") or f"emoji-{row['id']}"
            name = f"{row['id']}_{stem[:24]}{path.suffix}"
            try:
                zf.write(path, name)
            except OSError:
                skipped += 1
                continue
            items.append(
                {
                    "file": name,
                    "md5": str(row.get("md5") or ""),
                    "tags": db.normalize_tags(row.get("tags") or ""),
                    "description": str(row.get("description") or "")[:200],
                    "width": int(row.get("width") or 0),
                    "height": int(row.get("height") or 0),
                }
            )
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(
                {
                    "format": EXPORT_FORMAT,
                    "version": EXPORT_VERSION,
                    "exported_at": time.time(),
                    "count": len(items),
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return {"packed": len(items), "skipped": skipped}


def _new_import_stats() -> dict[str, int]:
    return {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}


def new_import_budget() -> dict[str, int]:
    """一次导入请求共享的文件数/解压字节预算（跨多个上传文件累计）。"""
    return {"files": 0, "bytes": 0}


def _check_budget(
    budget: dict[str, int], files: int = 0, declared_bytes: int = 0
) -> None:
    if budget["files"] > IMPORT_MAX_FILES or (
        files and budget["files"] + files > IMPORT_MAX_FILES
    ):
        raise EmojiImportError(f"单次导入超过 {IMPORT_MAX_FILES} 个文件，请分批导入")
    if budget["bytes"] > IMPORT_MAX_TOTAL_BYTES or (
        declared_bytes and budget["bytes"] + declared_bytes > IMPORT_MAX_TOTAL_BYTES
    ):
        raise EmojiImportError("单次导入解压后超过 1GB 上限，请分批导入")


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, dict]:
    """读取导出清单；缺失/损坏/非本工具格式都按「没有清单」处理，图片照常导入。"""
    try:
        info = zf.getinfo(MANIFEST_NAME)
        if info.file_size > _IMPORT_MAX_MANIFEST_BYTES:
            return {}
        with zf.open(info) as fh:
            raw = fh.read(_IMPORT_MAX_MANIFEST_BYTES + 1)
        if len(raw) > _IMPORT_MAX_MANIFEST_BYTES:
            return {}
        data = json.loads(raw.decode("utf-8"))
    except (KeyError, ValueError, OSError, RuntimeError, zipfile.BadZipFile):
        return {}
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        return {}
    items = data.get("items")
    if not isinstance(items, list):
        return {}
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or "")
        if not name:
            continue
        out[name] = {
            "tags": item.get("tags") or "",
            "description": str(item.get("description") or "")[:200],
            "width": db.clamp_emoji_dimension(item.get("width")),
            "height": db.clamp_emoji_dimension(item.get("height")),
        }
    return out


def _import_image(
    data: bytes, account_id: str, tags: object = "", description: str = "",
    width: int = 0, height: int = 0,
) -> str:
    """单张图片落缓存并入库，返回 db 状态；非图片返回 skipped。"""
    saved = save_emoji_bytes(data, min_bytes=0)
    if saved is None:
        return "skipped"
    md5, _ = saved
    return db.import_emoji(account_id, md5, tags, description, width, height)


def _import_zip(
    fp,
    account_id: str,
    common_tags: str,
    budget: dict[str, int],
    stats: dict[str, int],
) -> dict[str, int]:
    with zipfile.ZipFile(fp) as zf:
        infos = [
            info for info in zf.infolist()
            if not info.is_dir()
            and not info.filename.startswith("__MACOSX/")
            and info.filename != MANIFEST_NAME
        ]
        declared = sum(max(0, info.file_size) for info in infos)
        _check_budget(budget, files=len(infos), declared_bytes=declared)
        budget["files"] += len(infos)

        manifest = _read_manifest(zf)
        for info in infos:
            if info.file_size > IMPORT_MAX_FILE_BYTES:
                stats["skipped"] += 1
                continue
            try:
                with zf.open(info) as fh:
                    data = fh.read(IMPORT_MAX_FILE_BYTES + 1)
            except Exception:
                stats["skipped"] += 1
                continue
            budget["bytes"] += len(data)
            _check_budget(budget)
            if len(data) > IMPORT_MAX_FILE_BYTES:
                stats["skipped"] += 1
                continue

            meta = manifest.get(info.filename)
            if meta is None:
                status = _import_image(data, account_id, common_tags)
            else:
                status = _import_image(
                    data,
                    account_id,
                    meta["tags"],
                    meta["description"],
                    meta["width"],
                    meta["height"],
                )
            stats[status] += 1
    return stats


def import_upload(
    fp,
    filename: str,
    account_id: str,
    common_tags: str = "",
    budget: dict[str, int] | None = None,
    stats: dict[str, int] | None = None,
) -> dict[str, int]:
    """导入一个上传文件：zip 按清单还原标签，散图用 common_tags 统一打标。

    budget/stats 由调用方跨多个上传文件传入，保证单次请求的总限额与部分成功统计。
    """
    budget = budget if budget is not None else new_import_budget()
    stats = stats if stats is not None else _new_import_stats()
    try:
        fp.seek(0)
        is_zip = zipfile.is_zipfile(fp)
        fp.seek(0)
    except (OSError, ValueError):
        is_zip = False
    if is_zip:
        try:
            return _import_zip(fp, account_id, common_tags, budget, stats)
        except zipfile.BadZipFile as exc:
            raise EmojiImportError("压缩包损坏，无法读取") from exc

    _check_budget(budget, files=1)
    budget["files"] += 1
    data = fp.read(IMPORT_MAX_FILE_BYTES + 1)
    budget["bytes"] += len(data)
    _check_budget(budget)
    if len(data) > IMPORT_MAX_FILE_BYTES:
        stats["skipped"] += 1
        return stats
    stats[_import_image(data, account_id, common_tags)] += 1
    return stats


def history_content(account_id: str, row: dict, cache: dict[str, str] | None = None) -> str:
    """表情消息给模型看的内容：有库内标签时渲染为 [表情包:标签1、标签2]，否则保持原摘要。

    只影响喂给模型/工具的文本，不改库；cache 按 md5 复用查询结果（一次回复内跨历史行）。
    """
    content = row.get("content") or ""
    if (row.get("msg_type") or "") != "emoji":
        return content
    md5 = str(((row.get("meta") or {}).get("emoji") or {}).get("md5") or "")
    if not md5:
        return content
    if cache is None:
        cache = {}
    if md5 not in cache:
        found = db.find_emoji_by_md5(account_id, md5)
        cache[md5] = "、".join(db.normalize_tags((found or {}).get("tags") or "")[:4])
    tags = cache[md5]
    return f"[表情包:{tags}]" if tags else content
