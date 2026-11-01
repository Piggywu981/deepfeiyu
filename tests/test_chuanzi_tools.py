"""串子模式工具：首次表情检索先查站、单轮一次、仅 AI 娘豁免。"""
from __future__ import annotations

import asyncio

import pytest

from app import config, db
from app.ai import chuanzi, moe_wiki
from app.ai.tools import build_tools

MD5_LOCAL = "a" * 32
MD5_SITE = "b" * 32

# 串子模式关闭时 find_emoji_online 的描述原文（上线前 / 现在必须完全一致）。
# 只在「：」「），」「。」后面断句，任何空格两侧都不留断点。
OFF_FIND_EMOJI_DESCRIPTION = (
    "联网搜索表情包并自动下载入库、打标签。search_emojis 找不到合适的表情时用它补库："
    "query 写画面/场景关键词（如「熊猫头 加班」），"
    "可带 site:tieba.baidu.com 这类站点限定。"
    "返回的 emoji_id 可直接用 send_wechat_emoji 发送。搜索加下载要十几秒，一次回复别连用好几次。"
)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
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
    account_id = "acc_a"
    name = "A"
    llm = None

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def resolve_chat(self, name: str):
        return {"uid": "u1", "name": "会话一", "chat_id": "c1"}

    async def send_emoji(self, uid, row, source="agent"):
        self.sent.append({"uid": uid, "row": row, "source": source})


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def _seed():
    local = db.add_emoji("acc_a", "https://cdn/local", md5=MD5_LOCAL)
    db.set_emoji_tags("acc_a", local["id"], "无语,熊猫头", "本地无语")
    site, _ = db.import_site_emoji(
        "acc_a", MD5_SITE, ["串子站", "新三国"], "「这是什么」— 新三国"
    )
    return local, site


def _site_on(monkeypatch):
    """显式打开「串子站表情包」独立开关（默认关闭）。"""
    monkeypatch.setattr(chuanzi, "site_enabled", lambda cfg=None: True)


def test_search_emojis_site_first_and_only_once(temp_db, monkeypatch):
    local, site = _seed()
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "tags": site["tags"],
            "description": site["description"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    state = {"used": False, "hint": "对方在吐槽加班"}

    tools = build_tools(
        FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True
    )
    handler = _tool(tools, "search_emojis").handler

    first = asyncio.run(handler({"query": "无语"}))
    assert state["used"] is True
    assert first[0]["id"] == site["id"]
    assert first[0]["source"] == "searchanythingfun"
    assert first[0]["quote"] == "这是什么"
    assert calls == ["对方在吐槽加班"]

    second = asyncio.run(handler({"query": "无语"}))
    assert calls == ["对方在吐槽加班"]
    assert all(r["id"] != site["id"] for r in second)


def test_search_emojis_serious_skips_site(temp_db, monkeypatch):
    """硬闸场景只从本地找安慰图：不查站、不把串子站图排在前面。"""
    _local, site = _seed()
    comfort = db.add_emoji("acc_a", "https://cdn/comfort", md5="c" * 32)
    db.set_emoji_tags("acc_a", comfort["id"], "安慰,抱抱", "抱抱")
    # 历史入库的串子站图也可能命中「安慰」关键词；硬闸里同样不能放行
    db.import_site_emoji(
        "acc_a", "d" * 32, ["串子站", "安慰"], "「别难过」— 串子站"
    )
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "tags": site["tags"],
            "description": site["description"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    state = {"used": False, "hint": "我不想活了", "serious": True}
    tools = build_tools(
        FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True
    )

    rows = asyncio.run(_tool(tools, "search_emojis").handler({"query": "安慰"}))

    assert calls == []
    assert state["used"] is False
    assert rows and all(r.get("source") != chuanzi.SOURCE for r in rows)


def test_find_online_serious_is_rejected(temp_db, monkeypatch):
    _local, site = _seed()
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "tags": site["tags"],
            "description": site["description"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    state = {"used": False, "hint": "别串了", "serious": True}
    tools = build_tools(
        FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True
    )

    result = asyncio.run(
        _tool(tools, "find_emoji_online").handler({"query": "安慰"})
    )

    assert "正经" in result["error"]
    assert calls == []


def test_send_site_emoji_rejected_in_serious_mode(temp_db, monkeypatch):
    _local, site = _seed()
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    session = FakeSession()
    tools = build_tools(
        session,
        allow_send=True,
        chat_uid="c1",
        emoji_tools=True,
        chuanzi_state={"used": False, "hint": "别串了", "serious": True},
    )

    result = asyncio.run(
        _tool(tools, "send_wechat_emoji").handler(
            {"emoji_id": site["id"], "to": "会话一"}
        )
    )

    assert "正经" in result["error"]
    assert session.sent == []


def test_find_online_second_call_is_rejected(temp_db, monkeypatch):
    _local, site = _seed()
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {
            "ok": True,
            "emoji_id": site["id"],
            "tags": site["tags"],
            "description": site["description"],
            "quote": "这是什么",
            "source": "searchanythingfun",
            "imported": False,
        }

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    state = {"used": False, "hint": "对方在吐槽"}
    tools = build_tools(
        FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True
    )
    handler = _tool(tools, "find_emoji_online").handler

    first = asyncio.run(handler({"query": "新三国 吐槽"}))
    assert first["ok"] is True
    assert first["quote"] == "这是什么"
    second = asyncio.run(handler({"query": "再找一张"}))
    assert "error" in second
    assert calls == ["对方在吐槽 新三国 吐槽"]


def test_find_online_registered_without_general_web(temp_db, monkeypatch):
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    state = {"used": False, "hint": "在吗"}
    tools = build_tools(
        FakeSession(), allow_send=False, allow_web=False,
        chuanzi_state=state, emoji_tools=True,
    )
    assert "find_emoji_online" in {t.name for t in tools}

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: False)
    tools_off = build_tools(
        FakeSession(), allow_send=False, allow_web=False, emoji_tools=True
    )
    assert "find_emoji_online" not in {t.name for t in tools_off}


def test_site_emoji_bypasses_wiki_only(temp_db, monkeypatch):
    """串子开着：站内图不受「仅AI娘」角色名限制，普通本地表情照样拦。"""
    local, site = _seed()
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    monkeypatch.setattr(moe_wiki, "character_names", lambda: ["大肥鱼"])
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    session = FakeSession()
    tools = build_tools(
        session,
        allow_send=True,
        emoji_tools=True,
        chuanzi_state={"used": True, "hint": "在吗"},
    )
    send = _tool(tools, "send_wechat_emoji").handler

    ok = asyncio.run(send({"emoji_id": site["id"], "to": "会话一"}))
    assert "error" not in ok
    denied = asyncio.run(send({"emoji_id": local["id"], "to": "会话一"}))
    assert "仅AI娘" in denied["error"]


def test_chuanzi_off_removes_site_privileges(temp_db, monkeypatch):
    """串子关着：历史入库的站内表情按普通本地表情对待，没有任何豁免、也不置顶。"""
    local, site = _seed()
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    monkeypatch.setattr(moe_wiki, "character_names", lambda: ["大肥鱼"])
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: False)
    session = FakeSession()
    tools = build_tools(session, allow_send=True, emoji_tools=True)
    send = _tool(tools, "send_wechat_emoji").handler
    search = _tool(tools, "search_emojis").handler

    denied = asyncio.run(send({"emoji_id": site["id"], "to": "会话一"}))
    assert "仅AI娘" in denied["error"]
    assert session.sent == []
    empty = asyncio.run(search({"query": ""}))
    assert empty["message"] == "表情包库里没有匹配的表情"
    assert "quote" not in empty

    # 仅AI娘也关掉：站内图仍在库里，但顺序就是 db.search_emojis 原样，且不带 quote
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
    tools2 = build_tools(session, allow_send=True, emoji_tools=True)
    out = asyncio.run(_tool(tools2, "search_emojis").handler({"query": ""}))
    assert [r["id"] for r in out] == [local["id"], site["id"]]
    assert all("quote" not in r for r in out)


def _ok_hunt(site_id, source="searchanythingfun", calls=None):
    async def fake_hunt(account_id, query):
        if calls is not None:
            calls.append(query)
        return {
            "ok": True,
            "emoji_id": site_id,
            "tags": "串子站,新三国",
            "description": "「这是什么」— 新三国",
            "quote": "这是什么",
            "source": source,
            "imported": False,
        }

    return fake_hunt


def test_site_hit_on_other_source_row_stays_local(temp_db, monkeypatch):
    """站点命中的是库里别的来源的旧图：不冒充站点图，仍按本地候选走仅AI娘过滤。"""
    local, _site = _seed()
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", _ok_hunt(local["id"], source=""))

    state = {"used": False, "hint": "对方在吐槽"}
    tools = build_tools(FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True)
    search = _tool(tools, "search_emojis").handler

    # 仅AI娘模式开着：这张非站点来源的图照样被角色名过滤掉
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    monkeypatch.setattr(moe_wiki, "character_names", lambda: ["大肥鱼"])
    out = asyncio.run(search({"query": "无语"}))
    assert out["message"] == "表情包库里没有匹配的表情"
    assert state["used"] is True
    assert state.get("emoji_id") is None  # 不写进串子状态，免得冒充站点图

    # 关掉仅AI娘：它作为普通本地候选回到结果里，source 仍是空
    monkeypatch.setattr(config, "load", lambda: {"ai": {}})
    state2 = {"used": False, "hint": "对方在吐槽"}
    tools2 = build_tools(FakeSession(), allow_send=False, chuanzi_state=state2, emoji_tools=True)
    out2 = asyncio.run(_tool(tools2, "search_emojis").handler({"query": "无语"}))
    assert [r["id"] for r in out2] == [local["id"]]
    assert out2[0]["source"] == ""
    assert state2.get("emoji_id") is None
    assert state.get("emoji_id") is None


def test_one_site_request_per_turn_across_tools(temp_db, monkeypatch):
    """search_emojis 查过一次后，find_emoji_online 不再打第二次 /api/search。"""
    _local, site = _seed()
    calls: list[str] = []
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", _ok_hunt(site["id"], calls=calls))

    state = {"used": False, "hint": "在吗"}
    tools = build_tools(FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True)
    first = asyncio.run(_tool(tools, "search_emojis").handler({"query": "无语"}))
    assert first[0]["source"] == "searchanythingfun"
    assert state["emoji_id"] == site["id"]
    assert state["quote"] == "这是什么"

    again = asyncio.run(_tool(tools, "find_emoji_online").handler({"query": "猫"}))
    assert "本轮已经查过串子站" in again["error"]
    assert calls == ["在吗"]


def test_failed_site_query_still_blocks_a_second_request(temp_db, monkeypatch):
    """站点挂了也算掉本轮唯一一次额度，不会连着打第二次。"""
    _seed()
    calls: list[str] = []

    async def boom(account_id, query):
        calls.append(query)
        raise RuntimeError("串子站请求失败")

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    monkeypatch.setattr(chuanzi, "hunt", boom)

    state = {"used": False, "hint": "在吗"}
    tools = build_tools(FakeSession(), allow_send=False, chuanzi_state=state, emoji_tools=True)
    out = asyncio.run(_tool(tools, "search_emojis").handler({"query": "根本没有这个词"}))
    assert out["site_error"] == "串子站请求失败"
    assert "串子站" in out["hint"]

    again = asyncio.run(_tool(tools, "find_emoji_online").handler({"query": "猫"}))
    assert "error" in again
    assert calls == ["在吗"]


def test_site_switch_off_keeps_imported_emoji_and_skips_site(temp_db, monkeypatch):
    """默认关闭：不查站，但已入库的站内图照常检索/发送，站内特权不受影响。"""
    _local, site = _seed()
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {"ok": True, "emoji_id": site["id"], "source": "searchanythingfun"}

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "site_enabled", lambda cfg=None: False)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    state = {"used": False, "hint": "在吗"}
    session = FakeSession()
    tools = build_tools(
        session,
        allow_send=True,
        chat_uid="c1",
        chuanzi_state=state,
        emoji_tools=True,
    )

    out = asyncio.run(_tool(tools, "search_emojis").handler({"query": ""}))
    assert calls == []
    assert state["used"] is False
    assert isinstance(out, list)
    assert out[0]["id"] == site["id"]  # 已入库的站内图仍置顶、可检索

    sent = asyncio.run(
        _tool(tools, "send_wechat_emoji").handler(
            {"emoji_id": site["id"], "to": "会话一"}
        )
    )
    assert "error" not in sent
    assert session.sent and session.sent[0]["row"]["id"] == site["id"]


def test_find_online_site_switch_off_falls_back_to_general_search(
    temp_db, monkeypatch
):
    """关掉串子站表情包：find_emoji_online 回到原来的联网找图，不再打招呼站。"""
    from app.ai import emoji_search

    _seed()
    calls: list[str] = []

    async def fake_hunt(account_id, query):
        calls.append(query)
        return {"ok": True, "emoji_id": 1, "source": "searchanythingfun"}

    async def fake_online(account_id, llm, query, context="", limit=3):
        return {"ok": True, "emoji_id": 1, "description": query, "imported": True}

    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    monkeypatch.setattr(chuanzi, "site_enabled", lambda cfg=None: False)
    monkeypatch.setattr(chuanzi, "hunt", fake_hunt)
    monkeypatch.setattr(emoji_search, "hunt", fake_online)

    tools = build_tools(
        FakeSession(), allow_send=False, allow_web=True,
        chuanzi_state={"used": False, "hint": "在吗"}, emoji_tools=True,
    )
    out = asyncio.run(
        _tool(tools, "find_emoji_online").handler({"query": "熊猫头 加班"})
    )

    assert out["ok"] is True
    assert calls == []
    find = _tool(tools, "find_emoji_online")
    assert "searchanythingfun" not in find.description


def test_off_path_tool_wording_is_unchanged(temp_db, monkeypatch):
    """串子模式关闭时锁死 OFF 文案：空结果提示保留「先换更短的关键词」，
    find_emoji_online 的描述与参数说明不得提到「串子」，且必须与上线前的原文逐字一致。"""
    _seed()
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: False)
    tools = build_tools(FakeSession(), allow_send=False, emoji_tools=True)

    empty = asyncio.run(_tool(tools, "search_emojis").handler({"query": "根本没有这个词xyz"}))
    assert empty["message"] == "表情包库里没有匹配的表情"
    assert "先换更短的关键词" in empty["hint"]
    assert "入库成功后即可 send_wechat_emoji 发送" in empty["hint"]
    assert "串子" not in empty["hint"]

    find = _tool(tools, "find_emoji_online")
    assert "串子" not in find.description
    # 整段逐字钉死：描述由多个隐式拼接的字面量组成，断句时容易吃掉中英文之间的空格
    # （历史上「可直接用 send_wechat_emoji」曾被拼成「可直接用send_wechat_emoji」）。
    assert find.description == OFF_FIND_EMOJI_DESCRIPTION
    for prop in find.parameters["properties"].values():
        assert "串子" not in prop["description"]


def test_console_prompt_announces_chuanzi_mode(temp_db, monkeypatch):
    """控制台智能体也要知道串子模式开着：状态、素材用法与「不自动查站」边界。"""
    from app import main
    from app.wechat.manager import AccountSession

    cfg = {
        "ai": {
            "chuanzi_mode": True,
            "memory_enabled": False,
            "system_prompt": "人设",
            "persona_name": "Deep肥鱼",
        }
    }
    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(moe_wiki, "self_block", lambda: "")
    session = AccountSession(
        {"id": "acc_a", "name": "A", "nickname": "A"}, lambda: cfg, None
    )

    prompt = main._console_prompt(session)
    assert "【串子模式已开启】" in prompt
    assert "串子站" in prompt
    assert "search_emojis" in prompt
    assert "send_wechat_emoji" in prompt
    assert "find_emoji_online 仍走" in prompt
    assert "不要声称自己查过串子站" in prompt

    cfg["ai"]["chuanzi_mode"] = False
    assert "【串子模式已开启】" not in main._console_prompt(session)


def test_console_site_emoji_privileges_without_state(temp_db, monkeypatch):
    """控制台没有 chuanzi_state：不自动查站，但仍认串子模式的站内图特权。"""
    local, site = _seed()
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    monkeypatch.setattr(moe_wiki, "character_names", lambda: ["大肥鱼"])
    monkeypatch.setattr(chuanzi, "enabled", lambda cfg=None: True)
    _site_on(monkeypatch)
    session = FakeSession()
    tools = build_tools(session, allow_send=True, emoji_tools=True, chuanzi_state=None)
    send = _tool(tools, "send_wechat_emoji").handler
    search = _tool(tools, "search_emojis").handler

    # 站内图放行，本地杂图仍被角色名拦下
    ok = asyncio.run(send({"emoji_id": site["id"], "to": "会话一"}))
    assert "error" not in ok
    denied = asyncio.run(send({"emoji_id": local["id"], "to": "会话一"}))
    assert "仅AI娘" in denied["error"]

    # 不查站（没有 state），但本地库里的站内图置顶且不被角色名过滤
    out = asyncio.run(search({"query": ""}))
    assert isinstance(out, list)
    assert out[0]["id"] == site["id"]
    assert all(r["id"] != local["id"] for r in out)

