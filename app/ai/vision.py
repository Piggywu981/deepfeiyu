"""图片输入：把本地图片编码成 OpenAI 兼容的 content parts。"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..config import DATA_DIR

MAX_IMAGE_BYTES = 8 * 1024 * 1024
UPLOAD_PREFIX = "/api/agent/uploads/"
_THUMB_CACHE_MAX = 64
_thumb_cache: dict[tuple[str, float, int, int], str | None] = {}

SELF_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# 形象图每轮都要随请求上传，原图（实测 5.9MB → base64 7.9MB）会白拖慢每次首字：
# 同机同档实测带原图首字 7.56s、纯文本 3.11s。记住长相不需要像素级细节，缩到这里就够。
SELF_IMAGE_MAX_PX = 512
SELF_IMAGE_QUALITY = 78
SELF_IMAGE_NOTE = (
    "（系统附注：下面这张图是你的微信头像/形象图，帮你记住自己长什么样。"
    "看一眼记住就好，这不是对方发来的消息，不用回应它。）"
)


def image_to_data_url(path: str | Path) -> str | None:
    try:
        p = Path(path)
        data = p.read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    mime = mimetypes.guess_type(p.name)[0] or ""
    if not mime.startswith("image/"):
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def thumb_data_url(path: str | Path, max_px: int = 320, quality: int = 70) -> str | None:
    """缩图版 data URL，供批量附带参考图时控制请求体与 token 体积。

    缩图依赖 Pillow；PIL 不可用或图打不开时返回 None（宁缺勿滥，不回退原图）。
    GIF 取首帧，带透明通道的转白底 RGB。结果按 (路径, mtime, 参数) 缓存复用。

    不看扩展名：微信 Sns 缓存会把非 JPEG 图片落成 ``<md5>.mp4``，按后缀猜
    MIME 会把真图误杀；Pillow 打不开的文件自然返回 None，不影响非图片调用方。
    """
    try:
        p = Path(path)
        data = p.read_bytes()
    except OSError:
        return None
    key = (str(p), p.stat().st_mtime, max_px, quality)
    hit = _thumb_cache.get(key)
    if hit is not None:
        return hit
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            im.thumbnail((max_px, max_px))
            if im.mode != "RGB":
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.convert("RGBA").split()[-1])
                im = bg
            buf = BytesIO()
            im.save(buf, "JPEG", quality=quality)
        out = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        out = None
    _thumb_cache[key] = out
    if len(_thumb_cache) > _THUMB_CACHE_MAX:
        _thumb_cache.pop(next(iter(_thumb_cache)))
    return out


# 朋友圈配图一次性读取：不低于 1024px，截图里的字才看得清；再大只是白花 token
VISION_MAX_PX = 1024
VISION_QUALITY = 82
VISION_DEFAULT_QUESTION = (
    "逐张客观描述这些图片：主体、场景、画面里出现的文字（截图/聊天记录请转写关键文字）。"
    "有多张图时按顺序分开说明，不要猜测看不到的内容。"
)


def vision_image_data_url(
    path: str | Path, max_px: int = VISION_MAX_PX, quality: int = VISION_QUALITY
) -> str | None:
    """识图用 data URL：优先按内容缩图；只有没装 Pillow 时才回退原图。

    缩图交给 Pillow 按内容解码，能绕过朋友圈缓存里错误的后缀（如 png 存成
    ``.mp4``）。装了 Pillow 还打不开的文件就是坏图/非图，直接返回 None，
    不拿它去浪费一次模型调用；没装 Pillow 时才退回按扩展名编码原图。
    """
    url = thumb_data_url(path, max_px=max_px, quality=quality)
    if url:
        return url
    try:
        import PIL  # noqa: F401
    except ImportError:
        return image_to_data_url(path)
    return None


async def describe_images(
    llm: Any,
    image_paths: list[str | Path],
    question: str = "",
    max_px: int = VISION_MAX_PX,
    quality: int = VISION_QUALITY,
) -> str:
    """让视觉模型看一组本地图片并返回文字描述；一张都编码不了时抛 ValueError。"""
    urls = [
        url for url in (vision_image_data_url(p, max_px, quality) for p in image_paths) if url
    ]
    if not urls:
        raise ValueError("图片读不出来（文件缺失或解码失败）")
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": (question or "").strip() or VISION_DEFAULT_QUESTION}
    ]
    parts.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
    from .agent import Agent

    agent = Agent(llm, [], max_rounds=1)
    result = await agent.run(
        [{"role": "user", "content": parts}],
        temperature=0.3,
        max_tokens=1024,
        effort="off",
    )
    if result.get("error"):
        raise RuntimeError(result["error"])
    return (result.get("text") or "").strip()


def build_user_content(text: str, image_paths: list[str] | None = None) -> str | list[dict[str, Any]]:
    """没有图片时保持字符串；有图片时返回 OpenAI 多模态 content 数组。"""
    urls = [u for u in (image_to_data_url(p) for p in (image_paths or [])) if u]
    if not urls:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    parts.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
    return parts


# 空白判定只看缩到这么小的副本：全图同色这件事不需要像素级证据
BLANK_PROBE_PX = 96
_BLANK_MAXCOLORS = 2


def is_blank_image(path: str | Path) -> bool:
    """这张图是不是整幅纯色——本机解密/转码出事故时的兜底探针。

    判不出来一律 False：没装 Pillow、文件不是图、解码报错，都宁可把图发给模型，
    也别把能看的图误杀成"没图"。

    只认白/黑/中灰三种"转码残骸色"：微信静图转码取错帧交出来的是空白底，解码器
    没拿到数据时常见纯黑和 128 灰。别的纯色（对方真发一张全红）当正常内容放行，
    代价是一张全白玩梗的图会被跳过——那种本来也没有可识别的内容。
    """
    try:
        from PIL import Image

        with Image.open(path) as im:
            # draft 让 JPEG 直接按 DCT 缩放解码，省掉整幅解码
            im.draft("RGB", (BLANK_PROBE_PX, BLANK_PROBE_PX))
            im.thumbnail((BLANK_PROBE_PX, BLANK_PROBE_PX))
            colors = im.convert("RGB").getcolors(maxcolors=_BLANK_MAXCOLORS)
    except Exception:  # noqa: BLE001
        return False
    if not colors or len(colors) > 1:
        return False
    r, g, b = colors[0][1]
    return max(r, g, b) - min(r, g, b) <= 8 and (r <= 16 or g >= 240 or 120 <= r <= 136)


def split_blank_images(paths: list[str]) -> tuple[list[str], list[str]]:
    """把待附图拆成（能发的，纯色空白的）。"""
    kept: list[str] = []
    blank: list[str] = []
    for p in paths or []:
        (blank if is_blank_image(p) else kept).append(p)
    return kept, blank


def resolve_upload_ref(ref: str, root: str | Path) -> Path | None:
    """把前端回传的 /api/agent/uploads/<name> 限制到上传目录内。"""
    text = str(ref or "")
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(UPLOAD_PREFIX):
        return None
    name = unquote(parsed.path[len(UPLOAD_PREFIX):])
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    return Path(root) / name


def self_image_path(account_id: str = "") -> Path | None:
    """形象图约定路径：优先 data/self/<账号id>/avatar.<ext>，回退共享的 data/self/avatar.<ext>。"""
    dirs = [DATA_DIR / "self" / account_id] if account_id.strip() else []
    dirs.append(DATA_DIR / "self")
    for d in dirs:
        for ext in SELF_IMAGE_EXTS:
            p = d / f"avatar{ext}"
            if p.is_file():
                return p
    return None


def self_image_messages(account_id: str = "") -> list[dict[str, Any]]:
    """形象注入消息：user 附图说明 + assistant 确认，保持角色轮替。图不可用时返回空。

    附图走缩略图（按 路径+mtime 缓存复用）：这张图每轮都发，原图体积直接压在首字延迟上。
    缩不了（没装 Pillow、文件损坏）就干脆这轮不带图，绝不回退成几 MB 的原图。
    """
    p = self_image_path(account_id)
    if not p:
        return []
    thumb = thumb_data_url(p, max_px=SELF_IMAGE_MAX_PX, quality=SELF_IMAGE_QUALITY)
    if not thumb:
        return []
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": SELF_IMAGE_NOTE},
                {"type": "image_url", "image_url": {"url": thumb}},
            ],
        },
        {"role": "assistant", "content": "（嗯，我记得自己长什么样啦）"},
    ]


async def describe_self(llm: Any, question: str = "", account_id: str = "") -> str:
    """让视觉模型看一眼形象图并返回文字描述；没配形象图返回空串。"""
    p = self_image_path(account_id)
    if not p:
        return ""
    text = question.strip() or (
        "客观描述这张图里的形象：外观特征（发型发色、瞳色、服装、配饰、"
        "体态与非人特征）、姿态动作、主色调和构图。"
    )
    from .agent import Agent

    agent = Agent(llm, [], max_rounds=1)
    result = await agent.run(
        [{"role": "user", "content": build_user_content(text, [str(p)])}],
        temperature=0.3,
        max_tokens=1024,
        effort="off",
    )
    if result.get("error"):
        raise RuntimeError(result["error"])
    return (result.get("text") or "").strip()
