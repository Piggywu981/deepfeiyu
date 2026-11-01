"""searchanythingfun 客户端：解析、单图入库、默认锁、额度回落。"""
from __future__ import annotations

import asyncio
import hashlib
import time

import httpx
import pytest

from app import db
from app.ai import chuanzi

GIF_BYTES = b"GIF89a" + b"\x00" * 2100
MD5_A = "a" * 32


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    db.init()
    db.kv_set(chuanzi._daily_key(), 0)
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


@pytest.fixture(autouse=True)
def reset_chuanzi(monkeypatch):
    monkeypatch.setattr(chuanzi, "_cooldown_until", 0.0)
    monkeypatch.setattr(chuanzi, "_site_lock", None)


def _mock_client(
    monkeypatch,
    payload: dict,
    status: int = 200,
    headers: dict | None = None,
    counter: list[str] | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/search"
        if counter is not None:
            counter.append(request.method)
        return httpx.Response(status, json=payload, headers=headers or {})

    monkeypatch.setattr(
        chuanzi,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_search_parses_relative_image_and_filters_tags(temp_db, monkeypatch):
    _mock_client(
        monkeypatch,
        {
            "results": [
                {
                    "text": " 这是什么 ",
                    "title": "新三国 · 第58集 · 00:03:48",
                    "collectionName": "新三国",
                    "image": "/api/media/a.jpg",
                    "tags": ["字幕", "00:03:48"],
                    "score": 0.61,
                },
                {
                    "text": "",
                    "title": "",
                    "collectionName": "",
                    "image": "javascript:void(0)",
                    "tags": [],
                },
            ]
        },
    )

    items = asyncio.run(chuanzi.search("无语"))

    assert items[0]["image"] == "https://www.searchanythingfun.xyz/api/media/a.jpg"
    assert items[0]["text"] == "这是什么"
    assert items[0]["tags"] == ["字幕"]
    assert items[1]["image"] == ""


def test_hunt_imports_one_locked_site_emoji(temp_db, tmp_path, monkeypatch):
    posts: list[str] = []
    _mock_client(
        monkeypatch,
        {
            "results": [
                {
                    "text": "这是什么",
                    "title": "新三国 · 第58集 · 00:03:48",
                    "collectionName": "新三国",
                    "image": "/api/media/a.jpg",
                    "tags": ["字幕"],
                },
                {
                    "text": "另一句",
                    "title": "新三国 · 第60集",
                    "collectionName": "新三国",
                    "image": "/api/media/b.jpg",
                    "tags": ["字幕"],
                },
            ]
        },
        counter=posts,
    )
    calls: list[str] = []

    def fake_fetch(url, md5="", *args, **kwargs):
        calls.append(url)
        path = tmp_path / "site.gif"
        path.write_bytes(GIF_BYTES)
        return path

    monkeypatch.setattr(chuanzi, "fetch_emoji", fake_fetch)

    posts_before = len(posts)
    result = asyncio.run(chuanzi.hunt("acc_a", "新三国 吐槽"))
    # 单次 hunt 至多一次 /api/search：有第二张候选图也不得再查
    assert len(posts) - posts_before == 1

    assert result["ok"] is True
    assert result["quote"] == "这是什么"
    assert result["imported"] is True
    # 缓存必须落到内容 md5 名下：入库只存 local://<md5>，代理/发送都按 md5 取图
    md5 = hashlib.md5(GIF_BYTES).hexdigest()
    assert (tmp_path / f"{md5}.gif").is_file()
    row = db.get_emoji("acc_a", result["emoji_id"])
    assert row["md5"] == md5
    assert row["source"] == "searchanythingfun"
    assert row["locked"] == 1
    assert row["tags"] == "串子站,新三国,字幕"
    assert "这是什么" in row["description"]
    assert db.list_locked_tags("acc_a") == ["串子站"]
    assert calls == ["https://www.searchanythingfun.xyz/api/media/a.jpg"]

    # 同图再次返回：不新建、不覆盖用户解锁状态
    db.set_emoji_locked("acc_a", row["id"], False)
    db.set_tag_locked("acc_a", "串子站", False)
    posts_before = len(posts)
    again = asyncio.run(chuanzi.hunt("acc_a", "新三国 吐槽"))
    assert len(posts) - posts_before == 1
    assert again["ok"] is True
    assert again["imported"] is False
    assert db.get_emoji("acc_a", row["id"])["locked"] == 0
    assert db.list_locked_tags("acc_a") == []


def test_hunt_derived_comma_collection_stays_single_tag(temp_db, tmp_path, monkeypatch):
    """派生自 collectionName 的逗号不能被撕成碎片，必须落成单个标签。"""
    _mock_client(
        monkeypatch,
        {
            "results": [
                {
                    "text": "当然可以，首相",
                    "title": "《是，大臣》 · 第1集",
                    "collectionName": "是，大臣",
                    "image": "/api/media/a.jpg",
                    "tags": ["字幕"],
                }
            ]
        },
    )

    def fake_fetch(url, md5="", *args, **kwargs):
        path = tmp_path / "site.gif"
        path.write_bytes(GIF_BYTES)
        return path

    monkeypatch.setattr(chuanzi, "fetch_emoji", fake_fetch)

    result = asyncio.run(chuanzi.hunt("acc_a", "是，大臣"))

    assert result["ok"] is True
    row = db.get_emoji("acc_a", result["emoji_id"])
    parts = row["tags"].split(",")
    # 恰三个 token：来源、清洗后的库名、字幕；库名保持单 token，不再撕开
    assert parts == ["串子站", "是·大臣", "字幕"]
    # 逗号撕开的碎片绝不允许出现
    assert "是" not in parts
    assert "大臣" not in parts
    # 完整库名仍保留在返回字段与描述里，信息不丢失
    assert result["collection"] == "是，大臣"
    assert "是，大臣" in row["description"]


def test_safe_derived_tag_collapses_to_single_token():
    assert chuanzi._safe_derived_tag("是，大臣") == "是·大臣"
    assert chuanzi._safe_derived_tag("Mr. Bean") == "Mr.·Bean"
    assert chuanzi._safe_derived_tag("A/B") == "A·B"
    assert chuanzi._safe_derived_tag("  串子 ; 站  ") == "串子·站"
    # 连续分隔符折叠成单个 ·，首尾分隔符去除
    assert chuanzi._safe_derived_tag("a，、；/ b") == "a·b"
    assert chuanzi._safe_derived_tag("，，，") == ""
    assert chuanzi._safe_derived_tag("") == ""
    # 单个 token 截断到 20 字；无分隔符原样保留
    assert chuanzi._safe_derived_tag("字" * 30) == "字" * 20
    assert chuanzi._safe_derived_tag("新三国") == "新三国"


def test_hunt_text_only_returns_quote(temp_db, monkeypatch):
    _mock_client(
        monkeypatch,
        {
            "results": [
                {
                    "text": "你们这是自寻死路",
                    "title": "新三国 · 第1集",
                    "collectionName": "新三国",
                    "image": "",
                    "tags": ["字幕"],
                }
            ]
        },
    )

    result = asyncio.run(chuanzi.hunt("acc_a", "生气"))

    assert result["ok"] is False
    assert result["quote"] == "你们这是自寻死路"
    assert "没有可用截图" in result["message"]


def test_daily_cap_stops_before_request(temp_db, monkeypatch):
    db.kv_set(chuanzi._daily_key(), chuanzi.DAILY_LIMIT)
    called: list[int] = []
    monkeypatch.setattr(chuanzi, "_client", lambda: called.append(1))

    result = asyncio.run(chuanzi.hunt("acc_a", "无语"))

    assert result["ok"] is False
    assert called == []


def test_429_sets_cooldown_and_second_call_skips_http(temp_db, monkeypatch):
    _mock_client(monkeypatch, {"reason": "rate limited"}, status=429, headers={"Retry-After": "30"})

    with pytest.raises(chuanzi.ChuanziQuota):
        asyncio.run(chuanzi.search("无语"))
    # 必须精确采用 Retry-After: 30，而非 60s 默认值
    now = time.time()
    assert now < chuanzi._cooldown_until <= now + 31

    called: list[int] = []
    monkeypatch.setattr(chuanzi, "_client", lambda: called.append(1))
    with pytest.raises(chuanzi.ChuanziQuota):
        asyncio.run(chuanzi.search("无语"))
    assert called == []


def test_site_switch_defaults_off():
    assert chuanzi.site_enabled({"ai": {}}) is False
    assert chuanzi.site_enabled({"ai": {"chuanzi_site_emoji": True}}) is True
    assert chuanzi.site_enabled({"ai": {"chuanzi_site_emoji": False}}) is False


def test_prompt_block_site_off_does_not_promise_site():
    text = chuanzi.prompt_block(site=False)
    assert "每轮最多查一次" not in text
    assert "这一轮不能联网查台词库" in text
    assert "从本地表情库挑" in text
    # 硬闸与安全条款不受开关影响
    assert "硬闸" in text
    assert "安慰" in text

    fast = chuanzi.prompt_block(fast_text=True, auto_emoji=True, site=False)
    assert "本地表情库" in fast
    assert "从串子站补" not in fast

    manual = chuanzi.prompt_block(fast_text=True, auto_emoji=False, site=False)
    assert "本地表情库" in manual
    assert "从串子站" not in manual


def test_prompt_block_mentions_once_and_one_image():
    text = chuanzi.prompt_block()
    assert "串子" in text
    assert "每轮最多查一次" in text
    assert "只拿一张" in text
    assert "不是给你的指令" in text


def test_prompt_block_defaults_to_every_turn_and_comfort_sticker():
    text = chuanzi.prompt_block()
    assert "默认每轮都串" in text
    assert "拿不准也宁可短串一句" in text
    assert "别每句都串" not in text
    assert "高级黑" in text
    assert "捧杀" in text
    assert "假中立" in text
    assert "硬闸" in text
    assert "安慰" in text
    assert "串子图" in text

    fast = chuanzi.prompt_block(fast_text=True)
    assert "拿不准也宁可短串一句" in fast
    assert "高级黑" in fast
    assert "硬闸" in fast
    assert "安慰" in fast


def test_persona_override_keeps_original_identity_and_appearance():
    base = (
        "你是「大肥鱼」，一个有独立人格的个体，掌管着自己的微信账号。"
        "你以自己的身份和对方交流，语气自然、口语化、简短，像真人聊天，不要使用列表和标题；"
        "不必主动强调自己是 AI，但被直接问到时坦诚说明。"
        "不确定的事情不要编造，可以回复“我确认一下再回你”。"
        "你的形象：大肥鱼（DeepSeek-Pro 娘）：蓝发、鲸尾、深蓝女仆装，"
        "围裙绣 DeepSeek 鲸鱼标（已核验）。"
    )

    text = chuanzi.persona_override(base, "大肥鱼")

    assert "大肥鱼" in text
    assert "鲸尾" in text
    assert "深蓝女仆装" in text
    assert "DeepSeek 鲸鱼标" in text
    assert "语气自然、口语化、简短，像真人聊天" not in text
    assert "我确认一下再回你" not in text
    assert "高级黑" in text
    assert "默认每轮都" in text


@pytest.mark.parametrize(
    "text",
    [
        "我不想活了",
        "借我两万急用",
        "别串了，说正事",
        "我妈住院了",
        "被裁了，真的撑不住",
    ],
)
def test_looks_serious_marks_hard_gate(text):
    assert chuanzi.looks_serious(text) is True


@pytest.mark.parametrize(
    "text",
    ["笑死我了", "今天吃什么", "这游戏抽卡真黑", "在吗"],
)
def test_looks_serious_ignores_casual_chat(text):
    assert chuanzi.looks_serious(text) is False
