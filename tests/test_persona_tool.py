"""update_persona 工具：控制台专属、只改当前账号人设、改完刷新会话快照。"""
import asyncio

from app.ai import tools as tools_mod
from app.wechat.manager import AccountSession


def _session() -> AccountSession:
    return AccountSession(
        {
            "id": "acc_x",
            "name": "A",
            "nickname": "B",
            "persona": {"system_prompt": "旧人设", "persona_name": "旧名"},
        },
        lambda: {"ai": {}},
        None,
    )


def test_persona_tool_only_available_on_console():
    session = _session()
    wechat_tools = tools_mod.build_tools(session, allow_send=False)
    assert not any(t.name == "update_persona" for t in wechat_tools)
    console_tools = tools_mod.build_tools(session, allow_send=True, allow_persona=True)
    assert any(t.name == "update_persona" for t in console_tools)


def test_update_persona_rewrites_and_refreshes_session(monkeypatch):
    session = _session()
    captured: dict = {}

    def fake_upsert(payload):
        captured.update(payload)
        return {"id": payload["id"]}

    def fake_account(aid):
        return {"id": aid, "name": "A", "persona": {"system_prompt": "新的人设", "persona_name": "旧名"}}

    monkeypatch.setattr(tools_mod.config, "upsert_account", fake_upsert)
    monkeypatch.setattr(tools_mod.config, "account", fake_account)

    tool = {t.name: t for t in tools_mod.build_tools(session, allow_persona=True)}["update_persona"]
    res = asyncio.run(tool.handler({"system_prompt": "新的人设"}))
    assert res["ok"] is True
    # 只提交 persona 字段，不会动账号的其他配置
    assert captured == {"id": "acc_x", "persona": {"system_prompt": "新的人设"}}
    # 会话快照已刷新：下一次对话（微信/控制台）读到的就是新人设
    assert session.account["persona"]["system_prompt"] == "新的人设"


def test_update_persona_clear_falls_back_to_global(monkeypatch):
    session = _session()
    captured: dict = {}

    def fake_upsert(payload):
        captured.update(payload)
        return {"id": payload["id"]}

    monkeypatch.setattr(tools_mod.config, "upsert_account", fake_upsert)
    monkeypatch.setattr(tools_mod.config, "account", lambda aid: None)

    tool = {t.name: t for t in tools_mod.build_tools(session, allow_persona=True)}["update_persona"]
    res = asyncio.run(tool.handler({"clear": True}))
    assert res["ok"] is True
    assert captured["persona"] == {"system_prompt": "", "persona_name": ""}
    # 拿不到新配置时保留原快照，不能把 session 弄坏
    assert session.account["persona"]["system_prompt"] == "旧人设"


def test_update_persona_rejects_empty_prompt():
    session = _session()
    tool = {t.name: t for t in tools_mod.build_tools(session, allow_persona=True)}["update_persona"]
    assert "error" in asyncio.run(tool.handler({"system_prompt": "  "}))
    assert "error" in asyncio.run(tool.handler({}))
