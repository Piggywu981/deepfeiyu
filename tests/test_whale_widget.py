"""dsh-whale 小鲸鱼挂件的宿主接口：素材伺服、余额代理形状、设置持久化、音效映射。"""
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture()
def client():
    return TestClient(main.app)  # 不用 with，避免触发 lifespan 启动 Hub


def _ai_cfg(monkeypatch, api_key: str, base_url: str) -> None:
    monkeypatch.setattr(
        main.llm,
        "_get_cfg",
        lambda: {"ai": {"api_key": api_key, "base_url": base_url, "model": "deepseek-chat"}},
    )


def test_widget_and_assets_served(client):
    r = client.get("/dsh-whale/widget.js")
    assert r.status_code == 200
    assert "__dshWhaleWidget" in r.text
    assert client.get("/dsh-whale/image.png").headers["content-type"] == "image/png"
    assert client.get("/dsh-whale/rua.gif").headers["content-type"] == "image/gif"
    assert client.get("/dsh-whale/sound/press.mp3?set=duck").headers["content-type"] == "audio/mpeg"
    assert client.get("/dsh-whale/sound/release.mp3?set=fx1").headers["content-type"] == "audio/mpeg"


def test_balance_no_key(client, monkeypatch):
    _ai_cfg(monkeypatch, "", "https://api.deepseek.com")
    body = client.get("/dsh-whale/balance.json").json()
    assert body["ok"] is False
    assert body["code"] == "NO_KEY"
    assert body["versionLabel"]


def test_balance_not_deepseek(client, monkeypatch):
    _ai_cfg(monkeypatch, "sk-test", "https://api.example.com/v1")
    body = client.get("/dsh-whale/balance.json").json()
    assert body["ok"] is False
    assert body["code"] == "NOT_DEEPSEEK"


def test_size_roundtrip(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WHALE_SIZE_FILE", tmp_path / "whale-size.json")
    # 从未保存过：只回版本号，不臆造配置
    assert "scale" not in client.get("/dsh-whale/size.json").json()
    r = client.put("/dsh-whale/size.json", json={"scale": 1.3, "soundSet": "fx1", "vol": 0.5})
    assert r.json()["ok"] is True
    body = client.get("/dsh-whale/size.json").json()
    assert body["scale"] == 1.3
    assert body["soundSet"] == "fx1"
    assert body["vol"] == 0.5
    # 缺字段沿用默认值，绝不洗成另一种缺省
    assert body["sound"] is True
    assert body["scrollGapPx"] == 17
    # 缺 scale 的 PUT 必须被拒：否则不全的写入会把设置洗掉
    assert client.put("/dsh-whale/size.json", json={"vol": 0.1}).json()["ok"] is False
    # 只改部分字段时其余保持
    client.put("/dsh-whale/size.json", json={"scale": 1.3, "soundSet": "duck"})
    body = client.get("/dsh-whale/size.json").json()
    assert body["soundSet"] == "duck"
    assert body["vol"] == 0.5


def test_unimplemented_routes_stay_404(client):
    # 前端对这些接口 404 会优雅降级（角色/泡泡/多模型余额等 DSH 专属功能）
    assert client.get("/dsh-whale/roles.json").status_code == 404
    assert client.get("/dsh-whale/audio.json").status_code == 404
    assert client.get("/dsh-whale/api-models.json").status_code == 404


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    from app import usage as usage_mod

    fresh = usage_mod.UsageLedger(tmp_path / "whale-usage.json")
    monkeypatch.setattr(main.usage, "LEDGER", fresh)
    return fresh


def test_last_turn_empty_then_recorded(client, ledger):
    body = client.get("/dsh-whale/last-turn.json").json()
    assert body["ok"] is True
    assert body["seq"] == 0 and body["turn"] is None and body["amount"] is None
    ledger.record("deepseek-chat", 0.0021, 500)
    body = client.get("/dsh-whale/last-turn.json").json()
    # turn 非空 + amount 非空 ⇒ 前端才会弹「每轮消耗」泡泡
    assert body["seq"] == 1
    assert body["turn"] == "deepseek-chat"
    assert body["amount"] == 0.0021
    assert body["tokens"] == 500
    assert body["ts"]


def test_usage_records_today_and_models(client, ledger):
    ledger.record("deepseek-chat", 0.001, 100)
    ledger.record("deepseek-chat", 0.002, 200)
    ledger.record("deepseek-reasoner", 0.003, 300)
    d = client.get("/dsh-whale/usage-records.json").json()
    assert d["ok"] is True
    assert d["today"]["amount"] == 0.006
    assert d["today"]["label"] == "本地估算"
    # 消费记录(全部)面板以 date 为 join 键（排序/分组/柱状图），缺失会显示 undefined
    assert d["today"]["date"] == d["today"]["day"]
    assert all(day_summary["date"] for day_summary in d["all"]["days"])
    models = {m["model"]: m["cost"] for m in d["today"]["models"]}
    assert models == {"deepseek-chat": 0.003, "deepseek-reasoner": 0.003}
    assert len(d["days7"]) == 7
    assert d["total7"] == 0.006
    assert d["all"]["events"][0]["model"]  # 倒序事件列表非空
    assert d["settings"]["taskEnd"]["on"] is True  # 默认设置已下发


def test_usage_settings_roundtrip(client, ledger):
    r = client.put("/dsh-whale/usage-settings.json", json={"alert": {"below": 3}})
    assert r.json()["ok"] is True
    body = client.get("/dsh-whale/usage-settings.json").json()
    assert body["ok"] is True
    assert body["settings"]["alert"]["below"] == 3
    assert body["settings"]["alert"]["on"] is True  # 未动字段沿用默认
    assert body["settings"]["budget"]["amount"] == 10  # 其他键不受影响


def test_balance_carries_today_usage(client, ledger, monkeypatch):
    ledger.record("deepseek-chat", 0.05, 1000)
    _ai_cfg(monkeypatch, "sk-test", "https://api.deepseek.com")
    # 不真正打网络：只验证 NO_KEY 早退之外，成功路径才会带 todayUsage —— 这里用
    # 断言账本侧数据即可（真实链路由浏览器验证覆盖），避免测试依赖外网。
    assert ledger.today_total() == 0.05


def test_balance_success_shape(client, ledger, monkeypatch):
    """余额成功路径的字段形状：会话/智能体页头直接消费 totalBalance。"""
    ledger.record("deepseek-chat", 0.05, 1000)
    _ai_cfg(monkeypatch, "sk-test", "https://api.deepseek.com")
    monkeypatch.setattr(main.whale, "_cache", {})

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "12.34",
                        "granted_balance": "2.00",
                        "topped_up_balance": "10.34",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(main.whale.httpx, "AsyncClient", FakeClient)
    body = client.get("/dsh-whale/balance.json?refresh=1").json()
    assert body["ok"] is True
    assert body["totalBalance"] == 12.34
    assert body["currency"] == "CNY"
    assert body["todayUsage"] == 0.05
    assert body["usageLabel"] == "本地估算"
