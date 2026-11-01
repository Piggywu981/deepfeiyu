"""联网找表情包：来源解析、图片校验、入库去重与打标流水线。"""
import asyncio
import hashlib

import pytest

from app import db
from app.ai import emoji_search
from app.ai import web as ai_web
from app.emoji import EmojiURLError


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


GIF_BYTES = b"GIF89a" + b"\x00" * 2100  # 超过 MIN_IMAGE_BYTES 门槛
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 2100


BING_HTML = """
<div class="imgpt">
<a class="iusc" target="_blank" m="{&quot;murl&quot;:&quot;https://img.example/1.gif&quot;,&quot;purl&quot;:&quot;https://tieba.baidu.com/p/1&quot;,&quot;t&quot;:&quot;熊猫头 加班&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example/2.png&quot;}" mad="x"></a>
<a class="iusc" m="not-json"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;javascript:void(0)&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example/1.gif&quot;}"></a>
<img src="https://thumb.example/t.jpg">
</div>
"""


def test_parse_bing_images():
    out = emoji_search._parse_bing_images(BING_HTML, 10)
    assert [r["image_url"] for r in out] == [
        "https://img.example/1.gif",
        "https://img.example/2.png",
    ]
    assert out[0]["page_url"] == "https://tieba.baidu.com/p/1"
    assert out[0]["title"] == "熊猫头 加班"
    assert out[1]["page_url"] == ""
    assert emoji_search._parse_bing_images("<p>nothing</p>", 10) == []


SITE_HTML = """
<div class="search-result">
<img class="lazy" src="/img/loading.gif" data-original="https://i.doutula.com/p/1.gif!dt-common" alt="熊猫头">
<img src="https://wx1.sinaimg.cn/large/abc123.jpg" alt="开心猫">
<img src="data:image/png;base64,xxxx">
<img src="https://www.doutula.com/img/logo.png">
<img src="//cdn.example/noext">
<img data-original="https://i.doutula.com/p/1.gif" alt="重复图">
<img src="/relative/path.gif">
<img class="lazy" src="/img/loading.gif" data-original="https://i.doutula.com/p/2.webp?x-oss-process=1" alt="带参数">
</div>
"""


def test_parse_img_tags():
    out = emoji_search._parse_img_tags(SITE_HTML, 10)
    assert [r["image_url"] for r in out] == [
        "https://i.doutula.com/p/1.gif",
        "https://wx1.sinaimg.cn/large/abc123.jpg",
    ]
    assert out[0]["title"] == "熊猫头"
    assert out[1]["title"] == "开心猫"
    assert emoji_search._parse_img_tags("<p>nothing</p>", 10) == []


def test_parse_img_tags_limit():
    out = emoji_search._parse_img_tags(SITE_HTML, 1)
    assert len(out) == 1


def test_referer_for():
    assert emoji_search.referer_for("https://tiebapic.baidu.com/forum/a.jpg") == "https://tieba.baidu.com/"
    assert emoji_search.referer_for("https://imgsrc.baidu.com/x.png") == "https://tieba.baidu.com/"
    assert emoji_search.referer_for("https://i.doutula.com/a.gif") == "https://www.doutula.com/"
    assert emoji_search.referer_for("https://wx1.sinaimg.cn/large/a.gif") == "https://www.fabiaoqing.com/"
    assert emoji_search.referer_for("https://i0.hdslb.com/bfs/a.png") == "https://www.bilibili.com/"
    assert emoji_search.referer_for("https://img.example.com/a.gif") == ""


def test_sniff_image_ext():
    assert emoji_search.sniff_image_ext(b"GIF89a" + b"\x00" * 20) == ".gif"
    assert emoji_search.sniff_image_ext(b"GIF87a" + b"\x00" * 20) == ".gif"
    assert emoji_search.sniff_image_ext(PNG_MAGIC) == ".png"
    assert emoji_search.sniff_image_ext(JPG_MAGIC) == ".jpg"
    assert emoji_search.sniff_image_ext(b"RIFF\x00\x00\x00\x00WEBPVPE8") == ".webp"
    assert emoji_search.sniff_image_ext(b"<html>not an image</html>") == ""
    assert emoji_search.sniff_image_ext(b"") == ""


def make_fetch_emoji(tmp_path, table: dict[str, object]):
    """替身 fetch_emoji：按 url 写出指定内容（或抛异常），保留调用参数供断言。"""
    calls: list[dict] = []

    def fake(url, md5="", timeout=15.0, referer=None):
        calls.append({"url": url, "md5": md5, "referer": referer})
        item = table[url]
        if isinstance(item, Exception):
            raise item
        p = tmp_path / (hashlib.sha256(url.encode()).hexdigest()[:12] + ".gif")
        p.write_bytes(item)  # type: ignore[union-attr]
        return p

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


class FakeLLM:
    configured = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list = []

    async def stream(self, messages, **kwargs):
        self.calls.append(messages)
        yield {"type": "text", "text": self.reply}


def patch_search(monkeypatch, results):
    async def fake_search(query, limit=emoji_search.CANDIDATES_PER_SOURCE):
        return results

    monkeypatch.setattr(emoji_search, "search_images", fake_search)


def test_hunt_adds_tagged_emoji(temp_db, tmp_path, monkeypatch):
    table = {"https://img.example/1.gif": GIF_BYTES}
    monkeypatch.setattr(emoji_search, "fetch_emoji", make_fetch_emoji(tmp_path, table))
    patch_search(
        monkeypatch,
        [{"image_url": "https://img.example/1.gif", "page_url": "", "title": "熊猫头", "source": "Bing图片"}],
    )
    llm = FakeLLM('{"tags": ["熊猫头", "无语"], "description": "熊猫头翻白眼"}')

    out = asyncio.run(emoji_search.hunt("acc_a", llm, "熊猫头 加班", context="我：今天好累"))

    assert out["ok"] is True
    assert len(out["found"]) == 1
    assert out["found"][0]["source"] == "Bing图片"
    row = db.get_emoji("acc_a", out["found"][0]["emoji_id"])
    assert row["tags"] == "熊猫头,无语"
    assert row["description"] == "熊猫头翻白眼"
    # 表情身份指纹按设计就是内容 md5（与微信表情 XML 的 md5 属性同源，见 app/emoji.py content_md5）
    assert row["md5"] == hashlib.md5(GIF_BYTES).hexdigest()
    # 打标用上了下载的图和聊天上下文
    assert len(llm.calls) == 1
    content = llm.calls[0][1]["content"]
    assert isinstance(content, list)
    assert "今天好累" in content[0]["text"]
    assert content[1]["type"] == "image_url"


def test_hunt_dedups_by_md5(temp_db, tmp_path, monkeypatch):
    table = {
        "https://img.example/a.gif": GIF_BYTES,
        "https://img.example/b.png": GIF_BYTES,  # 内容相同 → 同一 md5
    }
    monkeypatch.setattr(emoji_search, "fetch_emoji", make_fetch_emoji(tmp_path, table))
    patch_search(
        monkeypatch,
        [
            {"image_url": "https://img.example/a.gif", "page_url": "", "title": "", "source": "Bing图片"},
            {"image_url": "https://img.example/b.png", "page_url": "", "title": "", "source": "Bing图片"},
        ],
    )
    out = asyncio.run(emoji_search.hunt("acc_a", FakeLLM('{"tags": ["x"]}'), "测试"))

    assert out["ok"] is True
    assert len(out["found"]) == 1
    assert out["already_in_library"] == 1
    assert len(db.list_emojis("acc_a")) == 1

    # 同一张图再找一次：不重复入库，明确提示已在库里
    out2 = asyncio.run(emoji_search.hunt("acc_a", FakeLLM('{"tags": ["x"]}'), "测试"))
    assert out2["ok"] is False
    assert "已经在库里" in out2["message"]


def test_hunt_skips_bad_images(temp_db, tmp_path, monkeypatch):
    table = {
        "https://img.example/bad.gif": EmojiURLError("下载失败"),
        "https://img.example/tiny.gif": b"GIF89a\x00",  # 太小
        "https://img.example/html.gif": b"<html>error page</html>" + b"x" * 2100,  # 魔数不对
        "https://img.example/ok.gif": GIF_BYTES,
    }
    monkeypatch.setattr(emoji_search, "fetch_emoji", make_fetch_emoji(tmp_path, table))
    patch_search(
        monkeypatch,
        [{"image_url": u, "page_url": "", "title": "", "source": "Bing图片"} for u in table],
    )
    out = asyncio.run(emoji_search.hunt("acc_a", FakeLLM('{"tags": ["x"]}'), "测试"))

    assert out["ok"] is True
    assert len(out["found"]) == 1
    assert out["download_failed"] == 3
    assert len(db.list_emojis("acc_a")) == 1


def test_hunt_without_llm_still_adds(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        emoji_search, "fetch_emoji", make_fetch_emoji(tmp_path, {"https://img.example/1.gif": GIF_BYTES})
    )
    patch_search(
        monkeypatch,
        [{"image_url": "https://img.example/1.gif", "page_url": "", "title": "", "source": "斗图啦"}],
    )

    class NotConfigured:
        configured = False

    out = asyncio.run(emoji_search.hunt("acc_a", NotConfigured(), "测试"))
    assert out["ok"] is True
    row = db.get_emoji("acc_a", out["found"][0]["emoji_id"])
    assert row["tags"] == ""  # 没模型只入库不打标


def test_hunt_reports_search_failure(temp_db, monkeypatch):
    async def boom(query, limit=12):
        raise ai_web.WebError("搜索失败（Bing图片: 限流）")

    monkeypatch.setattr(emoji_search, "search_images", boom)
    out = asyncio.run(emoji_search.hunt("acc_a", FakeLLM("{}"), "关键词"))
    assert "error" in out and "联网搜索失败" in out["error"]


def test_hunt_requires_query(temp_db):
    out = asyncio.run(emoji_search.hunt("acc_a", None, "  "))
    assert "error" in out


def test_fetch_and_validate_fixes_suffix_and_referer(tmp_path, monkeypatch):
    seen: dict = {}

    def fake(url, md5="", timeout=15.0, referer=None):
        seen["referer"] = referer
        p = tmp_path / "x.png"  # 内容是 gif 但响应头骗成了 png
        p.write_bytes(GIF_BYTES)
        return p

    monkeypatch.setattr(emoji_search, "fetch_emoji", fake)
    out = emoji_search._fetch_and_validate("https://tiebapic.baidu.com/forum/a.gif")
    assert seen["referer"] == "https://tieba.baidu.com/"
    assert out.suffix == ".gif" and out.is_file()
    assert not (tmp_path / "x.png").exists()


def test_fetch_and_validate_rejects_non_image(tmp_path, monkeypatch):
    def fake(url, md5="", timeout=15.0, referer=None):
        p = tmp_path / "x.gif"
        p.write_bytes(b"<html>oops</html>")
        return p

    monkeypatch.setattr(emoji_search, "fetch_emoji", fake)
    with pytest.raises(EmojiURLError):
        emoji_search._fetch_and_validate("https://img.example/x.gif")
    assert not (tmp_path / "x.gif").exists()  # 坏文件不留缓存
