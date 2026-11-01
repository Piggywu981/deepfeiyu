"""表情包库：md5 去重、打标写回、AI 检索、打标输出解析。"""
import asyncio
import base64
import hashlib

import pytest

from app import db
from app.ai import tagger
from app.emoji import content_md5


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


MD5_A = "a" * 32
MD5_B = "b" * 32


def test_valid_md5():
    assert db.valid_md5("A" * 32) == "a" * 32
    assert db.valid_md5("") == ""
    assert db.valid_md5("zz" * 16) == ""
    assert db.valid_md5("abc") == ""


def test_content_md5(tmp_path):
    p = tmp_path / "e.gif"
    p.write_bytes(b"GIF89a")
    assert content_md5(p) == hashlib.md5(b"GIF89a").hexdigest()
    assert content_md5(tmp_path / "missing.gif") == ""


def test_add_emoji_dedupes_by_md5(temp_db):
    first = db.add_emoji("acc_a", "https://cdn/signed?token=1", md5=MD5_A, width=100, height=100)
    second = db.add_emoji("acc_a", "https://cdn/signed?token=2", md5=MD5_A)
    assert second["id"] == first["id"]
    assert second["url"] == "https://cdn/signed?token=2"  # url 刷新为最近一次看到的
    assert second["hit_count"] == 2
    assert len(db.list_emojis("acc_a")) == 1


def test_add_emoji_falls_back_to_url_without_md5(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/x", md5="not-a-md5")
    again = db.add_emoji("acc_a", "https://cdn/x", md5="")
    assert again["id"] == row["id"]
    assert again["hit_count"] == 2


def test_add_emoji_md5_scoped_to_account(temp_db):
    db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.add_emoji("acc_b", "https://cdn/a", md5=MD5_A)
    assert len(db.list_emojis("acc_a")) == 1
    assert len(db.list_emojis("acc_b")) == 1


def test_update_emoji_tags_write_once(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    assert db.update_emoji_tags("acc_a", row["id"], "无语,加班", "熊猫头翻白眼")
    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["tags"] == "无语,加班"
    assert fresh["description"] == "熊猫头翻白眼"
    # 已有标签不覆盖（防旧任务/并发冲掉）
    assert db.update_emoji_tags("acc_a", row["id"], "开心") is False
    assert db.get_emoji("acc_a", row["id"])["tags"] == "无语,加班"
    assert db.update_emoji_tags("acc_a", row["id"], "") is False


def test_search_emojis_by_tag_and_hotness(temp_db):
    a = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.update_emoji_tags("acc_a", a["id"], "无语,加班", "熊猫头")
    b = db.add_emoji("acc_a", "https://cdn/b", md5=MD5_B)
    db.update_emoji_tags("acc_a", b["id"], "开心,猫", "猫咪比心")

    assert [r["id"] for r in db.search_emojis("acc_a", "加班")] == [a["id"]]
    assert [r["id"] for r in db.search_emojis("acc_a", "比心")] == [b["id"]]

    # 英文标签大小写不敏感
    c = db.add_emoji("acc_a", "https://cdn/c", md5="c" * 32)
    db.update_emoji_tags("acc_a", c["id"], "LOL", "")
    assert db.search_emojis("acc_a", "lol")[0]["id"] == c["id"]

    # 空关键词按热度返回；命中次数越多越靠前
    for _ in range(3):
        db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    assert db.search_emojis("acc_a", "")[0]["id"] == a["id"]

    # 账号隔离
    assert db.search_emojis("acc_b", "加班") == []


def test_parse_tag_reply():
    assert tagger.parse_tag_reply("") == {}
    assert tagger.parse_tag_reply("这个表情没什么可打的标签") == {}
    assert tagger.parse_tag_reply("{ broken") == {}
    out = tagger.parse_tag_reply(
        '好的：{"tags": ["无语", "加班", "", "猫"], "description": "熊猫头翻白眼"}'
    )
    assert out == {"tags": ["无语", "加班", "猫"], "description": "熊猫头翻白眼"}
    assert tagger.parse_tag_reply('{"tags": ["a"], "desc": "d"}')["description"] == "d"
    assert tagger.parse_tag_reply('{"tags": []}') == {}


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class FakeLLM:
    configured = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list = []

    async def stream(self, messages, **kwargs):
        self.calls.append(messages)
        yield {"type": "text", "text": self.reply}


def test_tag_emoji_with_fake_llm(tmp_path):
    # 无图无上下文：直接跳过，不浪费调用
    fake = FakeLLM('{"tags": ["无语"]}')
    assert asyncio.run(tagger.tag_emoji(fake)) == {}
    assert fake.calls == []

    # 仅上下文：content 保持字符串
    out = asyncio.run(tagger.tag_emoji(fake, context="我：今天好累\n对方：[表情]"))
    assert out["tags"] == ["无语"]
    assert isinstance(fake.calls[-1][1]["content"], str)

    # 有图：content 变多模态数组，图片放最后
    img = tmp_path / "e.png"
    img.write_bytes(PNG_1PX)
    out = asyncio.run(tagger.tag_emoji(fake, image_path=str(img), context="对方：周末加班吗"))
    assert out["tags"] == ["无语"]
    content = fake.calls[-1][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"


def test_tag_emoji_ignores_unconfigured_llm():
    class NotConfigured:
        configured = False

    assert asyncio.run(tagger.tag_emoji(NotConfigured(), context="x")) == {}


def test_tag_emoji_prompt_carries_meme_knowledge(tmp_path):
    img = tmp_path / "e.png"
    img.write_bytes(PNG_1PX)
    fake = FakeLLM('{"tags": ["滑稽脸"]}')
    asyncio.run(tagger.tag_emoji(fake, image_path=str(img)))
    text = fake.calls[-1][1]["content"][0]["text"]
    assert "梗百科" in text
    assert "滑稽脸" in text
