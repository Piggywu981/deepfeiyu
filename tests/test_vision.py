"""识图：视觉消息构造与图片消息门控。"""
from __future__ import annotations

import base64
from io import BytesIO

from app.ai.vision import build_user_content, resolve_upload_ref
from app.rules import check
from app.wechat.base import Message

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _cfg(vision: bool) -> dict:
    return {
        "auto": {
            "enabled": True,
            "group_need_at": True,
            "only_whitelist": False,
            "whitelist": [],
            "blacklist": [],
            "min_interval_sec": 0,
            "daily_limit": 0,
            "working_hours": {"enabled": False},
        },
        "ai": {"vision_enabled": vision, "persona_name": "Deep肥鱼"},
    }


def _chat() -> dict:
    return {
        "uid": "acc:wxid_a",
        "chat_id": "wxid_a",
        "name": "朋友",
        "ai_enabled": True,
        "pause_until": 0,
    }


def _image_msg() -> Message:
    return Message(
        chat_id="wxid_a",
        chat_name="朋友",
        sender_name="朋友",
        content='<msg><img md5="2e9ff6066cbe39059111d765bb4c0811" /></msg>',
        msg_type="image",
    )


def test_image_message_is_blocked_when_vision_disabled():
    allowed, reason = check(_cfg(False), {"id": "acc", "nickname": "Deep肥鱼"}, _chat(), _image_msg())
    assert allowed is False
    assert "识图" in reason


def test_image_message_is_allowed_when_vision_enabled():
    allowed, reason = check(_cfg(True), {"id": "acc", "nickname": "Deep肥鱼"}, _chat(), _image_msg())
    assert allowed is True, reason


def test_text_only_content_stays_a_string():
    assert build_user_content("在吗", []) == "在吗"


def test_image_content_uses_openai_image_url_part(tmp_path):
    img = tmp_path / "photo.jpg"
    img.write_bytes(JPEG)
    parts = build_user_content("看看这张图", [str(img)])
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "看看这张图"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_upload_ref_rejects_path_traversal(tmp_path):
    ok = resolve_upload_ref("/api/agent/uploads/a.jpg", tmp_path)
    assert ok == tmp_path / "a.jpg"
    assert resolve_upload_ref("/api/agent/uploads/../../secret.txt", tmp_path) is None
    assert resolve_upload_ref("https://evil.test/a.jpg", tmp_path) is None


def test_auto_reply_prompt_attaches_wechat_image(tmp_path):
    from app.wechat.manager import AccountSession

    img = tmp_path / "wechat.jpg"
    img.write_bytes(JPEG)
    cfg = _cfg(True)
    cfg["ai"]["system_prompt"] = "人设"
    session = AccountSession(
        {"id": "acc", "name": "Deep肥鱼", "nickname": "Deep肥鱼"},
        lambda: cfg,
        None,
    )
    prompt = session._build_prompt(cfg, {"name": "朋友"}, [], _image_msg(), [str(img)])
    content = prompt[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"] == "（对方发来一张图片，见附图）"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_image_prompt_without_attachment_says_no_content(tmp_path):
    from app.wechat.manager import AccountSession

    cfg = _cfg(True)
    cfg["ai"]["system_prompt"] = "人设"
    session = AccountSession(
        {"id": "acc", "name": "Deep肥鱼", "nickname": "Deep肥鱼"},
        lambda: cfg,
        None,
    )
    prompt = session._build_prompt(cfg, {"name": "朋友"}, [], _image_msg(), [])
    assert prompt[-1]["content"] == "（对方发来一张图片，本机没有图片内容）"


def _big_avatar(tmp_path, monkeypatch, size=(2400, 3200)):
    """造一张大尺寸形象图（噪声填充，压不小）并把 DATA_DIR 指到临时目录。"""
    import os

    from PIL import Image

    from app.ai import vision

    d = tmp_path / "self"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "avatar.jpg"
    noisy = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buf = BytesIO()
    noisy.save(buf, "JPEG", quality=95)
    path.write_bytes(buf.getvalue())
    monkeypatch.setattr(vision, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vision, "_thumb_cache", {})
    return vision, path


def test_self_image_is_downscaled_not_original(tmp_path, monkeypatch):
    from PIL import Image

    vision, path = _big_avatar(tmp_path, monkeypatch)
    msgs = vision.self_image_messages("acc")
    assert len(msgs) == 2, "形象注入应是 user 附图 + assistant 确认"
    url = msgs[0]["content"][1]["image_url"]["url"]
    raw = base64.b64decode(url.split(",", 1)[1])
    original = path.stat().st_size
    assert original > 200_000, "测试用的原图得够大才看得出差别"
    assert len(url) < original, "带图 payload 必须比原图小，否则每轮白传几 MB"
    with Image.open(BytesIO(raw)) as im:
        assert max(im.size) <= vision.SELF_IMAGE_MAX_PX


def test_self_image_thumbnail_reused_across_turns(tmp_path, monkeypatch):
    vision, _ = _big_avatar(tmp_path, monkeypatch)
    first = vision.self_image_messages("acc")[0]["content"][1]["image_url"]["url"]
    assert vision._thumb_cache, "缩图要落进缓存，别每轮重新解码编码"
    second = vision.self_image_messages("acc")[0]["content"][1]["image_url"]["url"]
    assert first == second


def test_self_image_skipped_when_thumbnail_unavailable(tmp_path, monkeypatch):
    """Pillow 解码不了（或没装）时宁可不给图，也不能回退成几 MB 原图。"""
    from app.ai import vision

    d = tmp_path / "self"
    d.mkdir(parents=True, exist_ok=True)
    (d / "avatar.jpg").write_bytes(JPEG)  # 假 JPEG：PIL 打不开
    monkeypatch.setattr(vision, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vision, "_thumb_cache", {})
    assert vision.self_image_messages("acc") == []


# ---------------------------------------------------------------- 空白图兜底
#
# 微信 4.x 的 wxgf 转码一旦取错帧，本机落盘的就是一张整幅 (255,255,255) 的 jpg。
# 发给模型只是花一轮钱换一个「图片是白的」，所以识图前先探一次纯色。


def _solid(tmp_path, name: str, color: tuple[int, int, int]) -> str:
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", (200, 400), color).save(p, "JPEG", quality=95)
    return str(p)


def _noisy(tmp_path, name: str = "photo.jpg") -> str:
    import os

    from PIL import Image

    p = tmp_path / name
    Image.frombytes("RGB", (200, 400), os.urandom(200 * 400 * 3)).save(p, "JPEG", quality=90)
    return str(p)


def test_solid_white_image_is_detected_as_blank(tmp_path):
    from app.ai.vision import is_blank_image

    assert is_blank_image(_solid(tmp_path, "blank.jpg", (255, 255, 255)))


def test_transcode_debris_colors_are_blank(tmp_path):
    """纯黑和 128 灰是解码器没拿到数据时的常见残骸，一并算空白。"""
    from app.ai.vision import is_blank_image

    assert is_blank_image(_solid(tmp_path, "black.jpg", (0, 0, 0)))
    assert is_blank_image(_solid(tmp_path, "gray.jpg", (128, 128, 128)))


def test_real_photo_is_not_blank(tmp_path):
    from app.ai.vision import is_blank_image

    assert not is_blank_image(_noisy(tmp_path))


def test_solid_red_is_a_message_not_a_failure(tmp_path):
    """有人真发一张全红是正常内容，只有白/黑/中灰才当转码事故。"""
    from app.ai.vision import is_blank_image

    assert not is_blank_image(_solid(tmp_path, "red.jpg", (255, 0, 0)))


def test_undecodable_file_is_not_blank(tmp_path):
    """打不开的文件不能算空白——那等于把「判不了」当「没内容」，会误杀真图。"""
    from app.ai.vision import is_blank_image

    p = tmp_path / "broken.jpg"
    p.write_bytes(JPEG)  # 假 JPEG：PIL 打不开
    assert is_blank_image(str(p)) is False
    assert is_blank_image(str(tmp_path / "missing.jpg")) is False


def test_split_blank_images_keeps_the_readable_ones(tmp_path):
    from app.ai.vision import split_blank_images

    real = _noisy(tmp_path)
    blank = _solid(tmp_path, "blank.jpg", (255, 255, 255))
    kept, dropped = split_blank_images([real, blank])
    assert kept == [real]
    assert dropped == [blank]


def test_split_blank_images_all_blank_yields_nothing(tmp_path):
    from app.ai.vision import split_blank_images

    kept, dropped = split_blank_images([_solid(tmp_path, "b.jpg", (255, 255, 255))])
    assert kept == []
    assert len(dropped) == 1
