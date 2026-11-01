"""朋友圈配图读取：工具注册范围、定位/取图/识图链路与媒体缓存。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config, db
from app.ai import tools as tools_mod


FEED = {
    "id": "10001",
    "username": "wxid_mios",
    "nickname": "ＭＩＯＳ",
    "text": "这两张是熊猫图鉴，别误会",
    "create_time": 1761000000,
    "images": [
        {"md5": "a" * 32, "url": "https://cdn/1.jpg", "size": 1, "width": 10, "height": 10},
        {"md5": "b" * 32, "url": "https://cdn/2.jpg", "size": 1, "width": 10, "height": 10},
        {"md5": "c" * 32, "url": "https://cdn/3.jpg", "size": 1, "width": 10, "height": 10},
    ],
    "videos": [],
    "likes": [],
    "comments": [],
}


class FakeLLM:
    cfg = {"api_key": "sk-test", "base_url": "https://api.deepseek.com", "model": "deepseek-flash"}


class StubMoments:
    """只实现工具用到的下载接口；路径由测试提前落盘。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.paths: dict[int, Path] = {}
        self.statuses: dict[int, str] = {}

    def download_media_detail(self, feed, index, kind="image"):
        self.calls.append((str(feed.get("feed_id")), index, kind))
        status = self.statuses.get(index, "ok")
        if status != "ok":
            return {"status": status, "path": None, "reason": "test"}
        return {"status": "ok", "path": str(self.paths[index]), "reason": None}


class FakeSession:
    account_id = "acc_moments"
    name = "测试号"

    def __init__(self, moments) -> None:
        self.llm = FakeLLM()
        self.moments = moments


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    db.init()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


@pytest.fixture()
def cfg_on(monkeypatch):
    monkeypatch.setattr(
        config,
        "load",
        lambda: {"ai": {"vision_enabled": True, "moments_ai_enabled": True}},
    )


@pytest.fixture()
def vision_calls(monkeypatch):
    calls: dict = {}

    async def fake_describe(llm, paths, question=""):
        calls["paths"] = list(paths)
        calls["question"] = question
        return "第 1 张是熊猫图鉴封面，第 2 张是内页目录"

    monkeypatch.setattr("app.ai.vision.describe_images", fake_describe)
    return calls


def _console_tools(session):
    return {
        t.name: t
        for t in tools_mod.build_tools(session, allow_send=True)
    }


def _feed_with_images(tmp_path, stub: StubMoments) -> None:
    db.upsert_moment("acc_moments", FEED)
    for i in range(3):
        p = tmp_path / f"image_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
        stub.paths[i] = p


def test_tool_only_for_console_and_real_moments_channel(cfg_on):
    stub = StubMoments()
    assert "view_wechat_moment_images" in _console_tools(FakeSession(stub))
    auto = {t.name for t in tools_mod.build_tools(FakeSession(stub), allow_send=False)}
    assert "view_wechat_moment_images" not in auto
    no_moments = _console_tools(FakeSession(None))
    assert "view_wechat_moment_images" not in no_moments


def test_reads_selected_image_and_carries_feed_context(
    temp_db, tmp_path, cfg_on, vision_calls
):
    stub = StubMoments()
    _feed_with_images(tmp_path, stub)
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(
        tool.handler(
            {"feed_id": "10001", "index": 2, "question": "第二张是不是熊猫图鉴？"}
        )
    )
    assert result["ok"] is True
    assert [x["index"] for x in result["images"]] == [2]
    assert stub.calls == [("10001", 1, "image")]
    assert "第二张是不是熊猫图鉴" in vision_calls["question"]
    assert "这两张是熊猫图鉴" in vision_calls["question"]
    assert "熊猫图鉴封面" in result["description"]


def test_default_reads_first_images_up_to_limit(temp_db, tmp_path, cfg_on, vision_calls):
    stub = StubMoments()
    _feed_with_images(tmp_path, stub)
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"feed_id": "10001", "limit": 2}))
    assert result["ok"] is True
    assert [x["index"] for x in result["images"]] == [1, 2]
    assert [(c[1], c[2]) for c in stub.calls] == [(0, "image"), (1, "image")]
    # 超过上限也最多读 4 张
    stub.calls.clear()
    asyncio.run(tool.handler({"feed_id": "10001", "limit": 99}))
    assert len(stub.calls) == 3


def test_lookup_by_publisher_when_feed_id_absent(temp_db, tmp_path, cfg_on, vision_calls):
    stub = StubMoments()
    _feed_with_images(tmp_path, stub)
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"publisher": "wxid_mios", "index": 3}))
    assert result["ok"] is True
    assert result["from"] == "ＭＩＯＳ"
    assert stub.calls == [("10001", 2, "image")]


def test_unknown_feed_id_returns_error(temp_db, cfg_on, vision_calls):
    tool = _console_tools(FakeSession(StubMoments()))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"feed_id": "nope"}))
    assert "没在已同步的朋友圈里找到" in result["error"]


def test_feed_without_images_returns_error(temp_db, cfg_on, vision_calls):
    db.upsert_moment("acc_moments", dict(FEED, id="10002", images=[]))
    stub = StubMoments()
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"feed_id": "10002"}))
    assert "没有图片" in result["error"]
    assert stub.calls == []


def test_vision_disabled_blocks_before_download(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        config,
        "load",
        lambda: {"ai": {"vision_enabled": False, "moments_ai_enabled": True}},
    )
    stub = StubMoments()
    _feed_with_images(tmp_path, stub)
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"feed_id": "10001"}))
    assert "识图未开启" in result["error"]
    assert stub.calls == []


def test_all_downloads_fail_reports_readable_reasons(temp_db, tmp_path, cfg_on, vision_calls):
    stub = StubMoments()
    _feed_with_images(tmp_path, stub)
    stub.statuses = {0: "no-cache", 1: "url-dead"}
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"feed_id": "10001", "limit": 2}))
    assert "本机缓存已过期" in result["error"]
    assert "CDN 链接已失效" in result["error"]
    assert vision_calls == {}


def test_index_out_of_range(temp_db, tmp_path, cfg_on, vision_calls):
    stub = StubMoments()
    _feed_with_images(tmp_path, stub)
    tool = _console_tools(FakeSession(stub))["view_wechat_moment_images"]
    result = asyncio.run(tool.handler({"feed_id": "10001", "index": 5}))
    assert "只有 3 张图" in result["error"]


# ---------------------------------------------------------------- 媒体缓存层


def test_store_image_fixes_wrong_extension(tmp_path):
    from PIL import Image

    from app.wechat import moments as moments_mod

    src = tmp_path / "abc.mp4"  # 微信缓存把 PNG 落成 .mp4 的现场
    Image.new("RGB", (16, 16), (255, 0, 0)).save(src, "PNG")
    out = moments_mod._store_image(src, tmp_path, 0)
    assert out is not None
    assert out.name == "image_0.png"
    assert out.read_bytes() == src.read_bytes()


def test_download_media_detail_reuses_cached_copy(tmp_path, monkeypatch):
    from app.wechat import moments as moments_mod

    monkeypatch.setattr(moments_mod.config, "DATA_DIR", tmp_path)
    base = tmp_path / "moments_media" / "acc" / "10001"
    base.mkdir(parents=True)
    cached = base / "image_0.jpg"
    cached.write_bytes(b"cached")
    service = moments_mod.MomentsService(SimpleNamespace(account_id="acc"), None)
    result = service.download_media_detail(FEED, 0)
    assert result["status"] == "ok"
    assert result["path"] == str(cached)
    assert service.download_media(FEED, 0) == str(cached)


def test_download_media_detail_rejects_out_of_range(tmp_path, monkeypatch):
    from app.wechat import moments as moments_mod

    monkeypatch.setattr(moments_mod.config, "DATA_DIR", tmp_path)
    service = moments_mod.MomentsService(SimpleNamespace(account_id="acc"), None)
    result = service.download_media_detail(FEED, 9)
    assert result["status"] == "no-such-media"
    assert result["path"] is None


def test_thumb_data_url_ignores_wrong_extension(tmp_path):
    """朋友圈缓存的 png 可能叫 .mp4：识图缩图必须按内容解码。"""
    from PIL import Image

    from app.ai.vision import thumb_data_url

    p = tmp_path / "abc.mp4"
    Image.new("RGB", (32, 32), (0, 128, 0)).save(p, "PNG")
    url = thumb_data_url(p, max_px=16, quality=80)
    assert url is not None
    assert url.startswith("data:image/jpeg;base64,")


def test_thumb_data_url_returns_none_for_non_image(tmp_path):
    from app.ai.vision import thumb_data_url

    p = tmp_path / "note.txt"
    p.write_text("不是图片", encoding="utf-8")
    assert thumb_data_url(p) is None


# ---------------------------------------------------------------- 识图请求


def test_describe_images_builds_multimodal_request(tmp_path):
    from PIL import Image

    from app.ai import vision

    p = tmp_path / "a.jpg"
    Image.new("RGB", (32, 32), (0, 0, 255)).save(p, "JPEG")
    captured: dict = {}

    class StubLLM:
        async def stream(self, messages, tools=None, **kwargs):
            captured["messages"] = messages
            captured["tools"] = tools
            yield {"type": "text", "text": "一只蓝鲸"}
            yield {"type": "finish", "reason": "stop"}

    result = asyncio.run(vision.describe_images(StubLLM(), [str(p)], "图里是什么"))
    assert result == "一只蓝鲸"
    assert captured["tools"] is None
    parts = captured["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "图里是什么"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_describe_images_rejects_unreadable_files(tmp_path):
    from app.ai import vision

    p = tmp_path / "broken.jpg"
    p.write_bytes(b"not an image")

    class StubLLM:
        async def stream(self, messages, tools=None, **kwargs):
            raise AssertionError("一张图都编码不了时不该调用模型")
            yield  # pragma: no cover

    with pytest.raises(ValueError):
        asyncio.run(vision.describe_images(StubLLM(), [str(p)]))
