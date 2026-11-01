"""主动搭话 API：状态、配置、白名单、候选会话、试运行/手动发送与本机限制。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import config, db, main
from app.proactive import api as proactive_api
from app.proactive import store


def _merge(dst: dict, patch: dict) -> dict:
    """模拟 config.save 的深合并，让 settings 读到的始终是最新配置。"""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)
    return dst


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


class FakeService:
    """记录发送参数；返回可控结果，不触碰真实生成/发送链路。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result: dict = {
            "ok": True,
            "status": "sent",
            "text": "在吗",
            "error": "",
        }

    async def send_one(
        self,
        account_id: str,
        chat_uid: str,
        trigger: str = "manual",
        ignore_quota: bool = False,
        dry_run: bool = False,
        preset: dict | None = None,
    ) -> dict:
        self.calls.append(
            {
                "account_id": account_id,
                "chat_uid": chat_uid,
                "trigger": trigger,
                "ignore_quota": ignore_quota,
                "dry_run": dry_run,
                "preset": preset,
            }
        )
        return copy.deepcopy(self.result)


class FakeSession:
    """只实现 /chats 需要的 visible_chats；不启动真实驱动。"""

    def __init__(self, chats: list[dict]) -> None:
        self._chats = chats
        self.last_limit = 0

    def visible_chats(self, limit: int = 200) -> list[dict]:
        self.last_limit = limit
        return list(self._chats)


@pytest.fixture()
def api(temp_db, monkeypatch):
    cfg: dict = {
        "active_account": "acc_a",
        "accounts": [{"id": "acc_a", "name": "测试号"}],
        "server": {"access_token": ""},
        "auto": {"enabled": True, "blacklist": [], "whitelist": []},
        "proactive": {"enabled": False, "accounts": {}},
    }
    saves: list[dict] = []

    def fake_save(patch: dict) -> dict:
        saves.append(copy.deepcopy(patch))
        return _merge(cfg, copy.deepcopy(patch))

    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(config, "save", fake_save)
    monkeypatch.setattr(
        config, "account", lambda aid: {"id": aid} if aid == "acc_a" else None
    )
    monkeypatch.setattr(config, "active_id", lambda: "acc_a")

    service = FakeService()
    monkeypatch.setattr(main.proactive_scheduler, "service", service)

    chats = [
        {
            "uid": "acc_a:c1",
            "chat_id": "c1",
            "name": "阿明",
            "is_group": False,
            "ai_enabled": True,
            "paused": False,
            "last_ts": 100.0,
        },
        {
            "uid": "acc_a:g1",
            "chat_id": "g1",
            "name": "摸鱼群",
            "is_group": True,
            "ai_enabled": False,
            "paused": True,
            "last_ts": 90.0,
        },
    ]
    session = FakeSession(chats)
    monkeypatch.setattr(
        main.hub,
        "get",
        lambda account_id=None: session if account_id == "acc_a" else None,
    )

    # 白名单/状态/事件接口会读 chats 表，先落两条真实会话
    db.upsert_chat("acc_a", "c1", "阿明", False)
    db.upsert_chat("acc_a", "g1", "摸鱼群", True)

    client = TestClient(main.app)
    return SimpleNamespace(
        client=client,
        cfg=cfg,
        saves=saves,
        service=service,
        session=session,
        chats=chats,
    )


@pytest.fixture()
def missing_scheduler():
    """临时摘掉 app.state 上的调度器。

    monkeypatch.setattr 在 Starlette State 上会走 delattr 而不是还原旧值，
    用过的用例会把属性永久删掉，污染后面的测试。
    """
    old = getattr(main.app.state, "proactive_scheduler", main.proactive_scheduler)
    main.app.state.proactive_scheduler = None
    try:
        yield
    finally:
        main.app.state.proactive_scheduler = old


def _entry(uid: str, name: str = "", is_group: bool = False) -> dict:
    return {"chat_uid": uid, "name": name, "is_group": is_group}


def _set_whitelist(api, entries: list[dict], *, enabled: bool = False) -> None:
    api.cfg["proactive"] = {
        "enabled": enabled,
        "accounts": {"acc_a": {"whitelist": copy.deepcopy(entries)}},
    }


# --------------------------------------------------------------------- 状态


def test_status_shape_and_whitelist_rows(api):
    _set_whitelist(
        api,
        [
            _entry("acc_a:c1", "阿明"),
            _entry("acc_a:g1", "摸鱼群", is_group=True),
            _entry("acc_a:ghost", "幽灵"),
        ],
        enabled=True,
    )
    # 让 g1 的最近开口时间晚于 c1；ghost 没有 state，应使用默认值
    store.record_sent("acc_a", "acc_a:c1", 1000.0, "2026-10-29")
    store.record_sent("acc_a", "acc_a:g1", 2000.0, "2026-10-29")

    data = api.client.get("/api/proactive/status").json()
    assert data["ok"] is True
    assert data["account_id"] == "acc_a"
    assert data["enabled"] is True
    assert data["scheduler"] == {"running": False, "interval": 60}
    assert data["settings"]["interval_min"] == 30
    assert [item["chat_uid"] for item in data["whitelist"]] == [
        "acc_a:c1",
        "acc_a:g1",
        "acc_a:ghost",
    ]

    assert [item["chat_uid"] for item in data["states"]] == [
        "acc_a:g1",
        "acc_a:c1",
        "acc_a:ghost",
    ]
    by_uid = {item["chat_uid"]: item for item in data["states"]}
    assert by_uid["acc_a:c1"]["chat_name"] == "阿明"
    assert by_uid["acc_a:g1"]["chat_name"] == "摸鱼群"
    assert by_uid["acc_a:ghost"]["chat_name"] == ""
    assert by_uid["acc_a:ghost"]["last_self_at"] == 0
    assert by_uid["acc_a:ghost"]["misses"] == 0


def test_status_scheduler_missing_defaults(api, missing_scheduler):
    data = api.client.get("/api/proactive/status").json()
    assert data["scheduler"] == {"running": False, "interval": 60}


def test_aid_uses_valid_header_or_active_account(api):
    data = api.client.get(
        "/api/proactive/status", headers={"X-Account-Id": "acc_a"}
    ).json()
    assert data["account_id"] == "acc_a"
    # 非法账号回退当前账号，不能把请求带到任意账号
    data = api.client.get("/api/proactive/status?account=acc_b").json()
    assert data["account_id"] == "acc_a"


# --------------------------------------------------------------------- 配置


def test_config_enable_requires_whitelist(api):
    res = api.client.post("/api/proactive/config", json={"enabled": True})
    assert res.status_code == 400
    assert res.json()["detail"] == "没有白名单不能启用主动搭话"
    assert api.saves == []


@pytest.mark.parametrize(
    "whitelist",
    [
        [_entry("acc_a:c1", "阿明")],  # 合法条目也不能从 /config 改
        [],  # 空列表同样拒绝
        "not-a-list",  # 非法类型不再单独报“必须是列表”
        [_entry("acc_b:c1")],  # 不能借 /config 绕过归属校验
        [_entry("acc_a:missing")],  # 也不能绕过存在性校验
    ],
)
def test_config_rejects_any_whitelist_field(api, whitelist):
    res = api.client.post(
        "/api/proactive/config",
        json={
            "enabled": True,
            "account": {"whitelist": whitelist, "interval_min": 7},
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "请使用白名单接口修改会话名单"
    # 含 whitelist 的请求整单拒绝，其它字段也不能借机落盘
    assert api.saves == []


def test_config_saves_account_fields_without_whitelist_once(api):
    _set_whitelist(api, [_entry("acc_a:c1", "阿明")], enabled=False)
    res = api.client.post(
        "/api/proactive/config",
        json={
            "enabled": True,
            "account": {"interval_min": 7, "unknown_key": "忽略"},
        },
    )
    assert res.status_code == 200
    # 一次保存同时落 enabled 与账号参数
    assert len(api.saves) == 1
    saved = api.saves[0]["proactive"]
    assert saved["enabled"] is True
    account = saved["accounts"]["acc_a"]
    # /config 不再碰名单，已保存的名单原样保留
    assert account["whitelist"] == [
        {"chat_uid": "acc_a:c1", "name": "阿明", "is_group": False}
    ]
    assert account["interval_min"] == 7
    assert "unknown_key" not in account

    data = res.json()
    assert data["enabled"] is True
    assert data["settings"]["interval_min"] == 7
    assert data["whitelist"] == [
        {"chat_uid": "acc_a:c1", "name": "阿明", "is_group": False}
    ]


def test_config_enable_uses_current_saved_whitelist(api):
    # 已保存名单里即使只有 stale 条目也算“有白名单”，/config 不做整体校验
    _set_whitelist(api, [_entry("acc_a:gone", "已删除")], enabled=False)
    res = api.client.post(
        "/api/proactive/config",
        json={"enabled": True, "account": {"interval_min": 9}},
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is True
    assert api.saves[-1]["proactive"]["enabled"] is True

    # 名单为空时，即使同一请求带了账号参数也不能顺手启用
    api.cfg["proactive"]["enabled"] = False
    api.cfg["proactive"]["accounts"]["acc_a"]["whitelist"] = []
    before = len(api.saves)
    res = api.client.post(
        "/api/proactive/config",
        json={"enabled": True, "account": {"interval_min": 11}},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "没有白名单不能启用主动搭话"
    assert len(api.saves) == before


def test_config_disable_allowed_without_whitelist(api):
    api.cfg["proactive"] = {"enabled": True, "accounts": {}}
    res = api.client.post("/api/proactive/config", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["enabled"] is False
    assert api.saves[-1]["proactive"]["enabled"] is False


def test_config_ignores_unknown_fields_and_keeps_other_account_values(api):
    api.cfg["proactive"] = {
        "enabled": False,
        "accounts": {"acc_a": {"interval_min": 12, "custom": "保留"}},
    }
    res = api.client.post(
        "/api/proactive/config",
        json={"account": {"interval_min": 9, "bogus": 1}},
    )
    assert res.status_code == 200
    account = api.saves[-1]["proactive"]["accounts"]["acc_a"]
    assert account["interval_min"] == 9
    assert account["custom"] == "保留"
    assert "bogus" not in account
    assert res.json()["settings"]["interval_min"] == 9


def test_config_without_whitelist_keeps_existing_list(api):
    original = [_entry("acc_a:c1", "旧名")]
    api.cfg["proactive"] = {
        "enabled": False,
        "accounts": {
            "acc_a": {"whitelist": copy.deepcopy(original), "interval_min": 12}
        },
    }
    res = api.client.post(
        "/api/proactive/config", json={"account": {"interval_min": 9}}
    )
    assert res.status_code == 200
    account = api.saves[-1]["proactive"]["accounts"]["acc_a"]
    assert account["whitelist"] == original
    assert account["interval_min"] == 9


def test_save_failure_returns_503(api, monkeypatch):
    def boom(patch: dict) -> dict:
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(config, "save", boom)
    res = api.client.post("/api/proactive/config", json={"enabled": False})
    assert res.status_code == 503
    assert res.json()["detail"] == "保存主动搭话配置失败"

    # 白名单写盘同样不能吞异常后返回成功
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "remove", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "保存主动搭话配置失败"


# ------------------------------------------------------------------ 白名单


def test_whitelist_add_updates_and_removes(api):
    api.cfg["proactive"] = {
        "enabled": False,
        "accounts": {"acc_a": {"interval_min": 9, "custom": "保留"}},
    }
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "  acc_a:c1  "},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == [
        {"chat_uid": "acc_a:c1", "name": "阿明", "is_group": False}
    ]
    account = api.saves[-1]["proactive"]["accounts"]["acc_a"]
    assert account["interval_min"] == 9
    assert account["custom"] == "保留"

    # 会话改名后再次加入：只刷新名称，不产生重复条目
    db.upsert_chat("acc_a", "c1", "阿明改名", False)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:c1"},
    )
    assert res.json()["whitelist"] == [
        {"chat_uid": "acc_a:c1", "name": "阿明改名", "is_group": False}
    ]

    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "remove", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == []
    # 已移除的条目再次移除也算成功
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "remove", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == []


def test_whitelist_add_ignores_stale_history(api):
    # 历史条目对应的会话已删：不参与新条目校验，也不会被清理
    _set_whitelist(api, [_entry("acc_a:gone", "已删除")], enabled=True)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == [
        {"chat_uid": "acc_a:gone", "name": "已删除", "is_group": False},
        {"chat_uid": "acc_a:c1", "name": "阿明", "is_group": False},
    ]
    assert res.json()["enabled"] is True
    # 名单非空，enabled 不因这次保存被改动
    assert "enabled" not in api.saves[-1]["proactive"]


def test_whitelist_add_updates_existing_entry_in_place(api):
    _set_whitelist(
        api,
        [_entry("acc_a:c1", "阿明"), _entry("acc_a:gone", "已删除")],
        enabled=True,
    )
    db.upsert_chat("acc_a", "c1", "阿明改名", False)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 200
    # 更新已有条目时保持原位置，名称以数据库为准
    assert res.json()["whitelist"] == [
        {"chat_uid": "acc_a:c1", "name": "阿明改名", "is_group": False},
        {"chat_uid": "acc_a:gone", "name": "已删除", "is_group": False},
    ]


def test_whitelist_add_uses_database_name_and_flag(api):
    # 私聊被客户端误标成群聊时以数据库为准
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:c1", "is_group": True},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == [
        {"chat_uid": "acc_a:c1", "name": "阿明", "is_group": False}
    ]

    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:g1", "is_group": True},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"][-1] == {
        "chat_uid": "acc_a:g1",
        "name": "摸鱼群",
        "is_group": True,
    }


def test_validated_entry_rejects_invalid_entry(api):
    # 单条校验入口自身也要挡住非字典/空 uid
    for entry in ("bad", {"chat_uid": ""}, {"chat_uid": "   "}):
        with pytest.raises(HTTPException) as exc:
            proactive_api._validated_entry("acc_a", entry)
        assert exc.value.status_code == 400
        assert exc.value.detail == "白名单条目无效"


def test_whitelist_remove_succeeds_when_chat_missing(api, monkeypatch):
    _set_whitelist(api, [_entry("acc_a:gone", "已删除")])

    def boom(uid: str):
        raise AssertionError("remove 不应查询 db.get_chat")

    monkeypatch.setattr(db, "get_chat", boom)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "remove", "chat_uid": "acc_a:gone"},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == []
    assert api.saves[-1]["proactive"]["accounts"]["acc_a"]["whitelist"] == []


def test_whitelist_remove_last_entry_disables(api):
    _set_whitelist(api, [_entry("acc_a:c1", "阿明")], enabled=True)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "remove", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True, "whitelist": [], "enabled": False}
    # 清空名单与关开关在同一次保存里完成
    assert len(api.saves) == 1
    saved = api.saves[0]["proactive"]
    assert saved["enabled"] is False
    assert saved["accounts"]["acc_a"]["whitelist"] == []
    assert api.cfg["proactive"]["enabled"] is False


def test_whitelist_remove_non_last_keeps_enabled(api):
    _set_whitelist(
        api,
        [_entry("acc_a:c1", "阿明"), _entry("acc_a:g1", "摸鱼群", is_group=True)],
        enabled=True,
    )
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "remove", "chat_uid": "acc_a:g1"},
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is True
    # 名单非空时不动全局开关
    assert "enabled" not in api.saves[-1]["proactive"]


def test_whitelist_rejects_other_account_uid(api):
    # 即使该会话真实存在，也不能跨账号写进 acc_a 的名单
    db.upsert_chat("acc_b", "c1", "别人家的会话", False)
    for action in ("add", "remove"):
        res = api.client.post(
            "/api/proactive/whitelist",
            json={"action": action, "chat_uid": "acc_b:c1"},
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "会话不属于当前账号"
    assert api.saves == []


def test_whitelist_unknown_chat_returns_404(api):
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:missing"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "会话不存在"
    # 前缀属于别的账号：直接按归属错误拒绝，不再走存在性查询
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_b:c1"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "会话不属于当前账号"
    assert api.saves == []


def test_whitelist_group_requires_explicit_flag(api):
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:g1"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "群聊必须显式勾选"

    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:g1", "is_group": True},
    )
    assert res.status_code == 200
    assert res.json()["whitelist"] == [
        {"chat_uid": "acc_a:g1", "name": "摸鱼群", "is_group": True}
    ]


def test_whitelist_limit_counts_after_dedupe(api):
    # 49 条 stale + 1 条真实会话 = 去重后正好 50
    stale = [_entry(f"acc_a:w{i}", f"会话{i}") for i in range(49)]
    _set_whitelist(api, [*stale, _entry("acc_a:c1", "阿明")])

    # 重复 add 已有 uid 只更新原条目，不触发上限
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 200
    rows = res.json()["whitelist"]
    assert len(rows) == 50
    assert rows[-1] == {"chat_uid": "acc_a:c1", "name": "阿明", "is_group": False}

    # 再新增第 51 个会话才报上限
    before = len(api.saves)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:g1", "is_group": True},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "白名单最多 50 个会话"
    assert len(api.saves) == before


def test_whitelist_limit_ignores_duplicate_history(api):
    # 历史名单里 60 个重复 uid 去重后只算 1 条，不应挡住新增
    _set_whitelist(api, [_entry("acc_a:c1", "阿明")] * 60)
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "add", "chat_uid": "acc_a:g1", "is_group": True},
    )
    assert res.status_code == 200
    assert [row["chat_uid"] for row in res.json()["whitelist"]] == [
        "acc_a:c1",
        "acc_a:g1",
    ]


def test_whitelist_rejects_unknown_action(api):
    res = api.client.post(
        "/api/proactive/whitelist",
        json={"action": "clear", "chat_uid": "acc_a:c1"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "未知操作"


# ------------------------------------------------------------------ 候选会话


def test_chats_marks_blacklist_pause_ai_selected_and_auto_whitelist(api):
    api.cfg["auto"] = {"blacklist": ["g1"], "whitelist": ["阿明"]}
    _set_whitelist(api, [_entry("acc_a:c1", "阿明")], enabled=True)

    data = api.client.get("/api/proactive/chats").json()
    assert data["ok"] is True
    assert api.session.last_limit == 500
    assert data["chats"] == [
        {
            "uid": "acc_a:c1",
            "chat_id": "c1",
            "name": "阿明",
            "is_group": False,
            "ai_enabled": True,
            "paused": False,
            "blacklisted": False,
            "in_auto_whitelist": True,
            "selected": True,
            "last_ts": 100.0,
        },
        {
            "uid": "acc_a:g1",
            "chat_id": "g1",
            "name": "摸鱼群",
            "is_group": True,
            "ai_enabled": False,
            "paused": True,
            "blacklisted": True,
            "in_auto_whitelist": False,
            "selected": False,
            "last_ts": 90.0,
        },
    ]


def test_chats_tolerates_null_or_broken_policy_lists(api):
    api.cfg["auto"] = {"blacklist": None, "whitelist": "oops"}
    data = api.client.get("/api/proactive/chats").json()
    assert data["ok"] is True
    assert all(item["blacklisted"] is False for item in data["chats"])
    assert all(item["in_auto_whitelist"] is False for item in data["chats"])


def test_chats_excludes_filehelper(api):
    # 驱动会把文件传输助手也放进可见会话，但它不能成为主动搭话候选
    api.chats.append({
        "uid": "acc_a:filehelper",
        "chat_id": "filehelper",
        "name": "文件传输助手",
        "is_group": False,
        "ai_enabled": True,
        "paused": False,
        "last_ts": 80.0,
    })
    data = api.client.get("/api/proactive/chats").json()
    assert data["ok"] is True
    assert [item["chat_id"] for item in data["chats"]] == ["c1", "g1"]


def test_chats_without_session_returns_empty(api, monkeypatch):
    monkeypatch.setattr(main.hub, "get", lambda account_id=None: None)
    assert api.client.get("/api/proactive/chats").json() == {
        "ok": True,
        "chats": [],
    }


# ------------------------------------------------------------- 试运行/手动


def test_test_endpoint_delegates_dry_run_and_live(api):
    _set_whitelist(api, [_entry("acc_a:c1", "阿明")], enabled=True)
    api.service.result = {
        "ok": True,
        "status": "preview",
        "text": "想你了",
        "error": "",
    }
    res = api.client.post(
        "/api/proactive/test", json={"chat_uid": "acc_a:c1"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "preview"
    assert data["text"] == "想你了"
    assert data["chat_name"] == "阿明"
    assert api.service.calls[-1] == {
        "account_id": "acc_a",
        "chat_uid": "acc_a:c1",
        "trigger": "test",
        "ignore_quota": True,
        "dry_run": True,
        "preset": None,
    }

    api.service.result = {
        "ok": True,
        "status": "sent",
        "text": "在吗",
        "error": "",
    }
    res = api.client.post(
        "/api/proactive/test",
        json={"chat_uid": "acc_a:c1", "dry_run": False},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "sent"
    call = api.service.calls[-1]
    assert call["trigger"] == "test"
    assert call["ignore_quota"] is True
    assert call["dry_run"] is False


def test_run_endpoint_uses_manual_and_counts_quota(api):
    _set_whitelist(api, [_entry("acc_a:c1", "阿明")], enabled=True)
    res = api.client.post("/api/proactive/run", json={"chat_uid": "acc_a:c1"})
    assert res.status_code == 200
    assert res.json()["chat_name"] == "阿明"
    assert api.service.calls[-1] == {
        "account_id": "acc_a",
        "chat_uid": "acc_a:c1",
        "trigger": "manual",
        "ignore_quota": False,
        "dry_run": False,
        "preset": None,
    }


def test_send_endpoints_require_scheduler(api, missing_scheduler):
    assert api.client.post(
        "/api/proactive/test", json={"chat_uid": "acc_a:c1"}
    ).status_code == 503
    assert api.client.post(
        "/api/proactive/run", json={"chat_uid": "acc_a:c1"}
    ).status_code == 503
    assert api.service.calls == []


# --------------------------------------------------------------------- 记录


def test_events_limit_before_and_chat_name_fallback(api):
    _set_whitelist(api, [_entry("acc_a:ghost", "幽灵")])
    common = {
        "trigger": "auto",
        "hit_line": 50.0,
        "roll": 10.0,
        "hit": True,
    }
    store.add_event("acc_a", "acc_a:c1", 1000.0, status="miss", **common)
    store.add_event("acc_a", "acc_a:g1", 2000.0, status="sent", **common)
    store.add_event("acc_a", "acc_a:ghost", 3000.0, status="skipped", **common)
    store.add_event("acc_a", "acc_a:absent", 4000.0, status="error", **common)

    data = api.client.get("/api/proactive/events?limit=2").json()
    assert data["ok"] is True
    assert [item["chat_uid"] for item in data["events"]] == [
        "acc_a:absent",
        "acc_a:ghost",
    ]
    # 不在库里的会话：优先用白名单名称快照，再失败空串
    assert data["events"][0]["chat_name"] == ""
    assert data["events"][1]["chat_name"] == "幽灵"

    data = api.client.get("/api/proactive/events?before=3500").json()
    assert [item["chat_uid"] for item in data["events"]] == [
        "acc_a:ghost",
        "acc_a:g1",
        "acc_a:c1",
    ]
    assert data["events"][1]["chat_name"] == "摸鱼群"
    assert data["events"][2]["chat_name"] == "阿明"

    # limit 会交给 store 夹到 1–200：0 按 1 条返回
    data = api.client.get("/api/proactive/events?limit=0").json()
    assert len(data["events"]) == 1


def test_events_survives_chat_lookup_failure(api, monkeypatch):
    _set_whitelist(api, [_entry("acc_a:ghost", "幽灵")])
    common = {
        "trigger": "auto",
        "hit_line": 50.0,
        "roll": 10.0,
        "hit": True,
    }
    store.add_event("acc_a", "acc_a:ghost", 1000.0, status="sent", **common)
    store.add_event("acc_a", "acc_a:absent", 2000.0, status="error", **common)

    def boom(uid: str):
        raise RuntimeError("数据库暂时不可用")

    # 补名字只是展示增强，查询失败必须回退白名单快照/空串而不是整个接口 500
    monkeypatch.setattr(db, "get_chat", boom)
    res = api.client.get("/api/proactive/events")
    assert res.status_code == 200
    by_uid = {item["chat_uid"]: item["chat_name"] for item in res.json()["events"]}
    assert by_uid["acc_a:ghost"] == "幽灵"
    assert by_uid["acc_a:absent"] == ""


# ------------------------------------------------------------------ 安全/接线


def test_require_local_rejects_lan_host():
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.2"))
    with pytest.raises(HTTPException) as exc:
        proactive_api._require_local(request)
    assert exc.value.status_code == 403
    assert "本机" in exc.value.detail


def test_route_requires_local_client(api):
    # 当前 Starlette TestClient 支持 client= 指定来源地址，直接打真实路由
    remote = TestClient(main.app, client=("10.0.0.2", 12345))
    try:
        res = remote.post("/api/proactive/config", json={"enabled": False})
    finally:
        remote.close()
    assert res.status_code == 403
    assert "本机" in res.json()["detail"]
    assert api.saves == []


def _route_paths(routes):
    """当前 FastAPI 把 include_router 保留为惰性节点，需递归展开。"""
    for route in routes:
        path = getattr(route, "path", None)
        if path:
            yield path
        sub = getattr(route, "routes", None)
        if sub:
            yield from _route_paths(sub)
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _route_paths(getattr(original, "routes", []) or [])
        router = getattr(route, "router", None)
        if router is not None:
            yield from _route_paths(getattr(router, "routes", []) or [])


def test_routes_registered_and_hub_wired():
    paths = set(_route_paths(main.app.routes))
    assert {
        "/api/proactive/status",
        "/api/proactive/config",
        "/api/proactive/chats",
        "/api/proactive/whitelist",
        "/api/proactive/test",
        "/api/proactive/run",
        "/api/proactive/events",
    } <= paths
    assert main.app.state.hub is main.hub
    assert main.app.state.proactive_scheduler is main.proactive_scheduler
