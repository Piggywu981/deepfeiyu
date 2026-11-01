"""wxgf（微信 4.x 用 HEVC 存静图）转码取帧。

背景：上游 wechatauto 1.2.7 的 `MediaDownloader._wxgf_to_jpg` 把 ffmpeg 写死成
`-frames:v 1`。而微信把一张静图编成两帧——先一帧 gray 空白底（IDR 不到 100 字节），
真图在第二帧（yuvj420p，十几 KB）。于是解密落盘的 jpg 整幅 (255,255,255)：
管理后台的图预览是个白块，模型只能回「The image is blank white」。
本机实测 152 张已解密图里 12 张中此雷。app/wechat/diagnose.py 负责打补丁，
这里验补丁后的行为，并留一条负控证明测试真的能区分首帧和真图帧。
"""
from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path

import pytest

from app.wechat.diagnose import WXGF_MAX_FRAMES, pick_wxgf_frame

# ---------------------------------------------------------------- 选帧纯函数


def test_picks_the_frame_with_content_not_the_first_one():
    # 实测值：空白底帧 jpg 4009 字节，真图帧 50007 字节
    frames = [("f001.jpg", 4009), ("f002.jpg", 50007)]
    assert pick_wxgf_frame(frames) == "f002.jpg"


def test_pick_is_independent_of_listing_order():
    assert pick_wxgf_frame([("f002.jpg", 50007), ("f001.jpg", 4009)]) == "f002.jpg"


def test_ties_keep_the_earlier_frame():
    # 动画表情各帧差不多大时仍交首帧，不随机挑一帧
    assert pick_wxgf_frame([("f001.jpg", 100), ("f002.jpg", 100)]) == "f001.jpg"


def test_no_frames_yields_nothing():
    assert pick_wxgf_frame([]) is None


# ---------------------------------------------------------------- 端到端转码

def _ffmpeg() -> str | None:
    from app.wechat.diagnose import wx4_media_module

    mod = wx4_media_module()
    if mod is None:
        return None
    return mod.MediaDownloader._ffmpeg_exe()


FFMPEG = _ffmpeg()


def _encode_two_frame_wxgf(tmp_path: Path) -> bytes:
    """造一份「首帧纯白 + 次帧有内容」的 wxgf：两帧都是 IDR，参数集各带一份。"""
    from PIL import Image

    w, h = 64, 96
    Image.new("RGB", (w, h), (255, 255, 255)).save(tmp_path / "f001.png")
    noisy = Image.frombytes("RGB", (w, h), bytes((x * 7 + y * 13) % 256 for y in range(h) for x in range(w * 3)))
    noisy.save(tmp_path / "f002.png")
    dst = tmp_path / "in.hevc"
    r = subprocess.run(
        [
            FFMPEG, "-y", "-v", "error",
            "-framerate", "1", "-i", str(tmp_path / "f%03d.png"),
            "-c:v", "libx265", "-pix_fmt", "yuv420p", "-g", "1", "-x265-params", "log-level=none",
            str(dst),
        ],
        capture_output=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    stream = dst.read_bytes()
    assert stream.count(b"\x00\x00\x00\x01") + stream.count(b"\x00\x00\x01") >= 6, "两帧应各带参数集"
    return b"wxgf" + b"\x82\x10" * 18 + stream  # 40 字节头，真图数据从 Annex-B 起始码起


def _decode_first_frame(data: bytes, tmp_path: Path) -> bytes:
    """上游老行为：只要第一帧。负控用。"""
    src = tmp_path / "old.hevc"
    src.write_bytes(data[data.find(b"\x00\x00\x00\x01"):])
    dst = tmp_path / "old.jpg"
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "hevc", "-i", str(src), "-frames:v", "1", str(dst)],
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return dst.read_bytes()


def test_wxgf_transcode_returns_the_real_frame(tmp_path):
    pytest.importorskip("PIL")
    from app.wechat.diagnose import wx4_media_module

    if not FFMPEG:
        pytest.skip("本机没有 ffmpeg 或 imageio-ffmpeg，转码路径跑不了")
    mod = wx4_media_module()
    downloader = mod.MediaDownloader.__new__(mod.MediaDownloader)
    data = _encode_two_frame_wxgf(tmp_path)

    old = _decode_first_frame(data, tmp_path)
    from PIL import Image

    assert len(Image.open(BytesIO(old)).convert("RGB").getcolors(1000000)) == 1, (
        "负控：首帧就该是纯色空白，否则这份样本没复现出问题"
    )

    out = downloader._wxgf_to_jpg(data)
    assert out is not None, "补丁后应能转出一帧"
    assert out[:3] == b"\xff\xd8\xff"
    assert len(out) > len(old), "真图帧要比空白底帧大"
    colors = len(Image.open(BytesIO(out)).convert("RGB").getcolors(1000000))
    assert colors > 32, f"交出来的应是那张有内容的图，实际只有 {colors} 种颜色"


def test_frame_cap_is_bounded():
    # 静图 1~2 帧，动画表情几帧；上限只防极端长序列
    assert 2 <= WXGF_MAX_FRAMES <= 64


# ---------------------------------------------------------------- 包上下文（回归）

def test_media_module_keeps_package_context_when_package_is_loaded(monkeypatch):
    """复刻服务进程里的真实状态：朋友圈已经 ``import wechatauto``（包在 sys.modules 里），
    此时 `_load_wx4_module` 走「按文件裸加载」那条分支，``__package__`` 会被留空。

    media.py 里有多处**函数内**延迟相对导入（第 280 行 `from . import db as _dbmod`、
    第 1168 行 `from .moment import _send_scroll`、第 2016 行 `from .wx import WeChat`）：
    载入这一步一切正常，真正读图时才在运行时炸
    「attempted relative import with no known parent package」——号主实测一天 104 次
    「读取图片失败」，入站图片全部读不出来，机器人对着图片盲回。
    """
    import importlib.util
    import sys
    import types

    from app.wechat import diagnose

    spec = importlib.util.find_spec("wechatauto")
    stub = types.ModuleType("wechatauto")
    # 只借 __path__：让 _load_wx4_module 认为包已就绪，从而走「按文件裸加载」那条分支
    stub.__path__ = list(spec.submodule_search_locations or [])
    monkeypatch.setitem(sys.modules, "wechatauto", stub)
    monkeypatch.setattr(diagnose, "_wx4_media_mod", None)

    mod = diagnose.wx4_media_module()
    assert mod is not None, "media.py 裸加载失败"
    assert mod.__name__ == "wechatauto_media", "本用例必须走按文件加载那条分支"
    assert mod.__spec__.parent == "wechatauto"  # 延迟相对导入靠包上下文解析
    ns: dict = {}
    exec("from . import db as _dbmod", mod.__dict__, ns)  # 复刻第 280 行那条运行时导入
    assert hasattr(ns["_dbmod"], "_k32") and hasattr(ns["_dbmod"], "_MBI")
