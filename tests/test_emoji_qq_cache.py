"""QQ 缓存表情导入：文件头识别、收编入缓存、扫描范围、缓存优先的 fetch、拒收名单、标签归并。"""
import asyncio
import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import db, emoji, main
from app.ai import tagger


def _gif(size: int = 700) -> bytes:
    return b"GIF89a" + b"\x00" * size


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_sniff_image_ext():
    assert emoji.sniff_image_ext(b"GIF89a......") == ".gif"
    assert emoji.sniff_image_ext(b"GIF87a......") == ".gif"
    assert emoji.sniff_image_ext(b"\x89PNG\r\n\x1a\n....") == ".png"
    assert emoji.sniff_image_ext(b"\xff\xd8\xff\xe0....") == ".jpg"
    assert emoji.sniff_image_ext(b"RIFF1234WEBPVP8 ") == ".webp"
    assert emoji.sniff_image_ext(b"RIFF1234WAVEfmt ") == ""
    assert emoji.sniff_image_ext(b"hello world") == ""
    assert emoji.sniff_image_ext(b"") == ""


def test_adopt_emoji_file_copies_by_content_md5(tmp_path, monkeypatch):
    monkeypatch.setattr(emoji, "EMOJI_DIR", tmp_path / "emojis")
    src = tmp_path / "0900896175c31c209bb42ec88ac17b20.jpg"
    data = _gif()
    src.write_bytes(data)

    adopted = emoji.adopt_emoji_file(src)
    assert adopted is not None
    md5, path = adopted
    assert md5 == hashlib.md5(data).hexdigest()
    assert path.read_bytes() == data

    # 再收一次（文件名换了也一样）：内容相同就落到同一个缓存文件
    renamed = tmp_path / "whatever.gif"
    renamed.write_bytes(data)
    assert emoji.adopt_emoji_file(renamed) == (md5, path)


def test_adopt_emoji_file_rejects_non_image_and_tiny(tmp_path, monkeypatch):
    monkeypatch.setattr(emoji, "EMOJI_DIR", tmp_path / "emojis")
    junk = tmp_path / "x.jpg"
    junk.write_bytes(b"hello world, not an image at all")
    assert emoji.adopt_emoji_file(junk) is None

    tiny = tmp_path / "y.gif"
    tiny.write_bytes(b"GIF89a" + b"\x00" * 10)  # 小于体积门槛
    assert emoji.adopt_emoji_file(tiny) is None

    assert emoji.adopt_emoji_file(tmp_path / "missing.gif") is None


def test_scan_qq_cache_files_prefers_ori_and_fills_from_thumb(tmp_path):
    nt = tmp_path / "123456" / "nt_qq" / "nt_data"
    emoji_root = nt / "Emoji"

    personal_ori = emoji_root / "personal_emoji" / "Ori"
    personal_ori.mkdir(parents=True)
    (personal_ori / "b.jpg").write_bytes(_gif(800))
    (personal_ori / "a.jpg").write_bytes(_gif())
    # personal 的 Thumb 与 Ori 同名（仅扩展名不同）：已有 Ori，跳过；z 只有 Thumb，收最大的
    personal_thumb = emoji_root / "personal_emoji" / "Thumb"
    personal_thumb.mkdir()
    (personal_thumb / "B.PNG").write_bytes(_gif(900))  # 同名大小写不敏感
    (personal_thumb / "z_0.png").write_bytes(_gif(100))
    (personal_thumb / "z_720.png").write_bytes(_gif(700))

    recv_ori = emoji_root / "emoji-recv" / "2026-10" / "Ori"
    recv_ori.mkdir(parents=True)
    (recv_ori / "c.png").write_bytes(PNG_1PX)
    recv_thumb = emoji_root / "emoji-recv" / "2026-10" / "Thumb"
    recv_thumb.mkdir()
    (recv_thumb / "c_720.jpg").write_bytes(_gif())  # Ori 里有的：跳过
    (recv_thumb / "d_0.jpg").write_bytes(_gif(50))  # Ori 缺的：两个变体留大的
    (recv_thumb / "d_720.jpg").write_bytes(_gif(600))
    # marketface：私有格式原图收进来（adopt 时会被魔数识别拦掉），_aio 预览可用
    market = emoji_root / "marketface" / "235520"
    market.mkdir(parents=True)
    (market / "e97b307233b9134406574854f329ef82").write_bytes(b"\xb8\xb6\xb9\xc7\xc6\x9e" + b"\x00" * 600)
    (market / "e97b307233b9134406574854f329ef82_aio.png").write_bytes(PNG_1PX * 30)

    # 内置小黄脸：apng（动图）与 png（静图）同 stem 留大的；ThumbTemp 临时目录不碰
    builtin = emoji_root / "BaseEmojiSyastems" / "EmojiSystermResource" / "3"
    (builtin / "apng").mkdir(parents=True)
    (builtin / "apng" / "3.png").write_bytes(_gif(800))
    (builtin / "png").mkdir()
    (builtin / "png" / "3.png").write_bytes(_gif(200))
    # 同 stem 的 lottie JSON 体积更大：图片优先，不能顶掉 png
    (builtin / "lottie").mkdir()
    (builtin / "lottie" / "3.json").write_bytes(b'{"v":"5.9.0"}' + b"x" * 900)
    (emoji_root / "BaseEmojiSyastems" / "ThumbTemp" / "s.png").parent.mkdir(parents=True)
    (emoji_root / "BaseEmojiSyastems" / "ThumbTemp" / "s.png").write_bytes(PNG_1PX)

    # Pic 同规则：Ori 权威，Thumb 补缺
    pic = nt / "Pic" / "2026-10"
    pic_ori = pic / "Ori"
    pic_ori.mkdir(parents=True)
    (pic_ori / "g.jpg").write_bytes(_gif(300))
    pic_thumb = pic / "Thumb"
    pic_thumb.mkdir()
    (pic_thumb / "g_720.jpg").write_bytes(_gif(400))  # Ori 里有的：跳过
    (pic_thumb / "h_720.jpg").write_bytes(_gif(500))  # 只有 Thumb 的：收

    files = emoji.scan_qq_cache_files(tmp_path)
    names = {p.name for p in files}
    # Thumb 同名/去后缀后同名的全部让位 Ori；z 取 _720 大变体；d 取 _720
    assert names == {"a.jpg", "b.jpg", "z_720.png", "c.png", "d_720.jpg",
                     "e97b307233b9134406574854f329ef82", "e97b307233b9134406574854f329ef82_aio.png",
                     "g.jpg", "h_720.jpg", "3.png"}
    best = {p.name: p for p in files}
    assert best["d_720.jpg"].stat().st_size > _gif(50).__len__()  # 留的是大变体
    assert best["z_720.png"].stat().st_size > _gif(100).__len__()
    assert best["3.png"].parent.name == "apng"  # 内置表情留动图
    assert [str(p) for p in files] == sorted(str(p) for p in files)  # 顺序稳定


def test_fetch_emoji_prefers_cache_before_url(tmp_path, monkeypatch):
    monkeypatch.setattr(emoji, "EMOJI_DIR", tmp_path / "emojis")
    md5 = "d" * 32
    cached = tmp_path / "emojis" / f"{md5}.gif"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_gif())

    # url 是 local:// 占位、根本不可下载：md5 命中缓存就直接用，不做 URL 校验
    assert emoji.fetch_emoji(f"local://{md5}", md5) == cached


def test_import_qq_endpoint(api, tmp_path, monkeypatch):
    qq = tmp_path / "123456" / "nt_qq" / "nt_data" / "Emoji"
    ori = qq / "personal_emoji" / "Ori"
    ori.mkdir(parents=True)
    data = _gif()
    (ori / "new1.jpg").write_bytes(data)
    (ori / "dup.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 700)
    (ori / "broken.jpg").write_bytes(b"not an image")

    db.add_emoji("acc_a", "https://cdn/old", md5=hashlib.md5(b"\x89PNG\r\n\x1a\n" + b"\x00" * 700).hexdigest())
    monkeypatch.setattr(emoji, "qq_cache_roots", lambda base=None: [qq])

    resp = api.post("/api/emojis/import-qq")
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert body["skipped"] == 1
    assert body["broken"] == 1

    row = db.list_emojis("acc_a")[0]
    assert row["md5"] == hashlib.md5(data).hexdigest()
    assert row["url"] == f"local://{row['md5']}"

    # 重复点按钮：全部跳过，不产生重复行
    assert api.post("/api/emojis/import-qq").json()["imported"] == 0


def test_emojis_list_limit_and_offset(api):
    rows = [db.add_emoji("acc_a", f"https://cdn/{i}") for i in range(5)]
    assert len(api.get("/api/emojis").json()) == 5
    assert len(api.get("/api/emojis", params={"limit": 2}).json()) == 2
    assert len(api.get("/api/emojis", params={"limit": 99999}).json()) == 5  # 封顶后不报错
    assert api.get("/api/emojis", params={"limit": 0}).status_code == 200
    # 翻页：最新在前，offset 跳过前 3 条
    page2 = api.get("/api/emojis", params={"limit": 2, "offset": 3}).json()
    assert [r["id"] for r in page2] == [rows[1]["id"], rows[0]["id"]]


def test_parse_tag_reply_not_sticker():
    assert tagger.parse_tag_reply('{"not_sticker": true}') == {"not_sticker": True}
    assert tagger.parse_tag_reply('{"is_sticker": false}') == {"not_sticker": True}
    # 拒收判定优先：模型顺手给的标签被忽略
    assert tagger.parse_tag_reply('{"tags": ["风景"], "not_sticker": true}') == {"not_sticker": True}
    assert tagger.parse_tag_reply('{"tags": ["猫"], "not_sticker": false}') == {
        "tags": ["猫"],
        "description": "",
    }


def test_reject_emoji_records_md5(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5="a" * 32)
    assert db.reject_emoji("acc_a", row["id"], "测试") == row
    assert db.get_emoji("acc_a", row["id"]) is None
    assert db.list_reject_md5s("acc_a") == {"a" * 32}
    assert db.any_emoji_with_md5("a" * 32) is False
    # 别的账号还在引用同一 md5 时，排除该账号后不算悬空
    db.add_emoji("acc_b", "https://cdn/b", md5="a" * 32)
    assert db.any_emoji_with_md5("a" * 32, exclude_account="acc_b") is False
    assert db.any_emoji_with_md5("a" * 32, exclude_account="acc_c") is True


def test_retag_rejects_non_sticker(api, tmp_path, monkeypatch):
    from app import emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "EMOJI_DIR", tmp_path / "emojis")
    md5 = "e" * 32
    row = db.add_emoji("acc_a", f"local://{md5}", md5)
    cache = tmp_path / "emojis" / f"{md5}.gif"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(_gif())

    class RejLLM:
        configured = True

        async def stream(self, messages, **kwargs):
            yield {"type": "text", "text": '{"not_sticker": true, "tags": ["风景"]}'}

    monkeypatch.setattr(main, "session_of", lambda request: FakeSession(RejLLM()))
    resp = api.post(f"/api/emojis/{row['id']}/retag")
    assert resp.status_code == 200
    assert resp.json()["rejected"] is True
    assert db.get_emoji("acc_a", row["id"]) is None
    assert db.list_reject_md5s("acc_a") == {md5}
    assert not cache.exists()  # 没别的账号引用，缓存文件一并清掉


def test_import_skips_reject_list(api, tmp_path, monkeypatch):
    md5 = "f" * 32
    row = db.add_emoji("acc_a", f"local://{md5}", md5)
    db.reject_emoji("acc_a", row["id"])

    emoji_root = tmp_path / "1" / "nt_qq" / "nt_data" / "Emoji"
    ori = emoji_root / "personal_emoji" / "Ori"
    ori.mkdir(parents=True)
    (ori / f"{md5}.jpg").write_bytes(_gif())  # 拒收名单里的：跳过且不回库
    (ori / f"{'ab' * 16}.jpg").write_bytes(_gif())  # 新面孔：正常导入
    monkeypatch.setattr(emoji, "qq_cache_roots", lambda base=None: [emoji_root])

    body = api.post("/api/emojis/import-qq").json()
    assert body["rejected"] == 1
    assert body["imported"] == 1
    assert db.get_emoji_by_md5("acc_a", md5) is None


def test_import_qq_endpoint_no_cache_dir(api, monkeypatch):
    monkeypatch.setattr(emoji, "qq_cache_roots", lambda base=None: [])
    assert api.post("/api/emojis/import-qq").status_code == 404


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


class FakeSession:
    def __init__(self, llm=None) -> None:
        self.llm = llm


@pytest.fixture()
def api(temp_db, monkeypatch):
    monkeypatch.setattr(main, "acc_id", lambda request: "acc_a")
    monkeypatch.setattr(main, "session_of", lambda request: FakeSession())
    return TestClient(main.app)  # 不用 with，避免触发 lifespan 启动 Hub


class ReplyLLM:
    configured = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list = []

    async def stream(self, messages, **kwargs):
        self.calls.append(messages)
        yield {"type": "text", "text": self.reply}


def test_tag_frequency_and_mapping(temp_db):
    r1 = db.add_emoji("acc_a", "https://cdn/1")
    db.set_emoji_tags("acc_a", r1["id"], "微笑笑脸,开心", "")
    r2 = db.add_emoji("acc_a", "https://cdn/2")
    db.set_emoji_tags("acc_a", r2["id"], "微笑,开心", "")
    assert dict(db.tag_frequency("acc_a")) == {"开心": 2, "微笑笑脸": 1, "微笑": 1}
    # 映射重写：微笑→微笑笑脸，第二行去重后变为 微笑笑脸,开心
    assert db.apply_tag_mapping("acc_a", {"微笑": "微笑笑脸"}) == 1
    assert db.get_emoji("acc_a", r2["id"])["tags"] == "微笑笑脸,开心"
    assert db.apply_tag_mapping("acc_a", {}) == 0


def test_tag_emoji_injects_vocab(tmp_path):
    img = tmp_path / "e.png"
    img.write_bytes(PNG_1PX)
    fake = ReplyLLM('{"tags": ["无语"]}')
    asyncio.run(tagger.tag_emoji(fake, image_path=str(img), vocab=["无语", "加班"]))
    text = fake.calls[-1][1]["content"][0]["text"]
    assert "词表" in text
    assert "加班" in text


def _make_ref_wiki(tmp_path):
    """最小图集：一个立绘角色，配参考立绘路径与说明。"""
    from app.ai import moe_wiki

    base = tmp_path / "01-立绘人设" / "模型娘立绘"
    base.mkdir(parents=True)
    (base / "大肥鱼.jpg").write_bytes(PNG_1PX)
    (tmp_path / moe_wiki.DOC_NAME).write_text(
        "**1. 大肥鱼**\n\n蓝发鲸尾女仆装。\n", encoding="utf-8"
    )
    return tmp_path


def test_tag_emoji_attaches_ref_images(tmp_path, monkeypatch):
    from app.ai import moe_wiki

    root = _make_ref_wiki(tmp_path)
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: root)
    img = tmp_path / "e.png"
    img.write_bytes(PNG_1PX)
    fake = ReplyLLM('{"tags": ["大肥鱼", "无语"]}')
    asyncio.run(tagger.tag_emoji(fake, image_path=str(img)))
    content = fake.calls[-1][1]["content"]
    images = [c for c in content if c.get("type") == "image_url"]
    assert len(images) == 2  # 待打标表情 + 参考立绘各一张
    assert "第1张参考图=大肥鱼" in content[0]["text"]
    assert "蓝发鲸尾女仆装" in content[0]["text"]  # 参考文案随清单注入


def test_tag_emoji_without_image_skips_refs(tmp_path, monkeypatch):
    """纯上下文打标（无图）不附参考立绘，也不注入清单。"""
    from app.ai import moe_wiki

    root = _make_ref_wiki(tmp_path)
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: root)
    fake = ReplyLLM('{"tags": ["摸鱼"]}')
    out = asyncio.run(tagger.tag_emoji(fake, image_path=None, context="他刚发了张图摸鱼"))
    assert "tags" in out
    user = fake.calls[-1][1]["content"]
    assert isinstance(user, str)  # 没有图就保持纯文本
    assert "参考立绘" not in user


def test_consolidate_tags_validates_mapping():
    freq = [("微笑笑脸", 10), ("熊猫头", 8), ("微笑", 5), ("大笑", 3)]
    llm = ReplyLLM(
        '{"mapping": {"微笑": "微笑笑脸", "大笑": "微笑", "不存在的": "微笑笑脸",'
        ' "熊猫头": "熊猫头", "微笑笑脸": "微笑笑脸"}}'
    )
    out = asyncio.run(tagger.consolidate_tags(llm, freq))
    # 只有键值都在词表里、且确实改写的映射保留；自映射与凭空词被剔除
    assert out == {"微笑": "微笑笑脸", "大笑": "微笑"}


def test_search_emojis_multi_token(temp_db):
    a = db.add_emoji("acc_a", "https://cdn/a")
    db.update_emoji_tags("acc_a", a["id"], "无语,晚安", "鲸鱼说晚安")
    b = db.add_emoji("acc_a", "https://cdn/b")
    db.update_emoji_tags("acc_a", b["id"], "晚安", "")
    # 整句多词（旧实现整串匹配全军覆没的场景）：任一命中即返回，命中词多的在前
    assert [r["id"] for r in db.search_emojis("acc_a", "鲸鱼、无语、晚安")] == [a["id"], b["id"]]
    assert [r["id"] for r in db.search_emojis("acc_a", "鲸鱼 晚安")] == [a["id"], b["id"]]
    # 单个词直接命中
    assert [r["id"] for r in db.search_emojis("acc_a", "鲸鱼")] == [a["id"]]
    assert [r["id"] for r in db.search_emojis("acc_a", "不存在")] == []
    assert len(db.search_emojis("acc_a", "")) == 2


def test_consolidate_tags_endpoint(api, monkeypatch):
    r1 = db.add_emoji("acc_a", "https://cdn/1")
    db.set_emoji_tags("acc_a", r1["id"], "微笑,开心", "")
    r2 = db.add_emoji("acc_a", "https://cdn/2")
    db.set_emoji_tags("acc_a", r2["id"], "微笑笑脸", "")
    monkeypatch.setattr(
        main, "session_of", lambda request: FakeSession(ReplyLLM('{"mapping": {"微笑": "微笑笑脸"}}'))
    )

    body = api.post("/api/emojis/consolidate-tags").json()
    assert body["mapping"] == {"微笑": "微笑笑脸"}
    assert body["changed"] == 1
    assert db.get_emoji("acc_a", r1["id"])["tags"] == "微笑笑脸,开心"
