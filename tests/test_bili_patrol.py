"""B 站巡逻：DB、输出解析、调度、报告与投递。"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app import db
from app.ai.agent import Tool
from app.bili import patrol
from app.bili import setup as bili_setup
from app.bili import trace as patrol_trace


@pytest.fixture(autouse=True)
def patrol_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bili_setup, "PATROL_DIR", tmp_path / "patrol")
    # 默认不真的入队心情判断，避免测试结束时留下未完成任务。
    monkeypatch.setattr(patrol.mood_service, "observe", lambda *a, **k: "skipped")


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


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        yield {"type": "text", "text": self.text}
        yield {"type": "finish", "reason": "stop"}


class FakeMcp:
    def tools_for(self, auto=False):
        async def handler(args):
            return {"ok": True, "content": "{}"}
        return [Tool("mcp__bilibili__bili_search", "搜索", {"type": "object", "properties": {}}, handler)]


class FakeRegistry:
    def __init__(self):
        self.mcp = FakeMcp()


class FakeSession:
    def __init__(self):
        self.sent = []

    async def send_text(self, uid, text, **kwargs):
        self.sent.append((uid, text))


class FakeHub:
    def __init__(self):
        self.session = FakeSession()

    def get(self, account_id=None):
        return self.session


def test_db_patrol_run_roundtrip(temp_db):
    run = db.add_bili_patrol_run("acc_a", "manual", "AI")
    assert run["status"] == "running"
    db.update_bili_patrol_run(run["id"], status="ok", report_path="x.md")
    got = db.list_bili_patrol_runs("acc_a")[0]
    assert got["status"] == "ok"
    assert got["report_path"] == "x.md"
    assert db.count_bili_patrol_runs_today("acc_a") == 1
    assert db.last_bili_patrol_run("acc_a")["id"] == run["id"]


def test_patrol_trace_renders_and_persists(tmp_path):
    trace = patrol_trace.PatrolTrace(7, "acc_a", tmp_path, meta="触发 manual")
    asyncio.run(trace.on_event({"type": "reasoning", "text": "先搜一下\n再读字幕"}))
    asyncio.run(
        trace.on_event(
            {"type": "tool_start", "name": "bili_search", "args": {"keyword": "鱼"}, "id": "c1"}
        )
    )
    asyncio.run(
        trace.on_event(
            {"type": "tool_end", "name": "bili_search", "ok": True, "result": {"items": [1]}, "id": "c1"}
        )
    )
    asyncio.run(trace.on_event({"type": "delta", "text": "## 摘要\n今天有鱼"}))
    trace.close("ok")
    snap = trace.snapshot(0)
    text = "\n".join(item["text"] for item in snap["items"])
    assert "思考：先搜一下" in text
    assert "调用 bili_search" in text
    assert "✓ bili_search" in text
    assert "输出：## 摘要" in text
    assert snap["done"] is True
    saved = (tmp_path / "7.log").read_text("utf-8")
    assert "巡逻完成" in saved


def test_summarize_result_hides_raw_json():
    subtitle = {
        "ok": True,
        "content": json.dumps(
            {
                "bvid": "BV1",
                "title": "标题",
                "language": "中文",
                "segments": 455,
                "text": "第一句\n第二句",
            },
            ensure_ascii=False,
        ),
    }
    out = patrol_trace._summarize_result("mcp__bilibili__bili_subtitle", subtitle)
    assert "《标题》" in out and "455 段" in out and "开头：第一句" in out
    assert '"title"' not in out

    comments = {
        "ok": True,
        "content": json.dumps(
            {
                "count": 2,
                "comments": [
                    {"user": "甲", "content": "热评内容", "like": 9},
                    {"user": "乙", "content": "第二条", "like": 1},
                ],
            },
            ensure_ascii=False,
        ),
    }
    out = patrol_trace._summarize_result("bili_comments", comments)
    assert "2 条评论" in out and "甲：热评内容" in out and "9 赞" in out

    search = {
        "ok": True,
        "content": json.dumps(
            {"count": 2, "videos": [{"title": "A"}, {"title": "B"}]}, ensure_ascii=False
        ),
    }
    out = patrol_trace._summarize_result("bili_search", search)
    assert out == "2 条 · 《A》 / 《B》"

    err = {"ok": False, "content": json.dumps({"error": "boom"})}
    assert patrol_trace._summarize_result("x", err).startswith("错误：boom")


def test_per_scan_allows_up_to_ten():
    cfg = {**patrol.PATROL_DEFAULTS, "keywords": "大肥鱼", "per_scan": 10}
    assert patrol._per_scan(cfg) == 10
    assert "最多挑 10 条" in patrol._prompt("鱼", cfg)
    assert patrol._per_scan({**cfg, "per_scan": 99}) == 10


def test_run_once_feeds_memory_and_mood(temp_db, tmp_path, monkeypatch):
    calls = {}

    async def fake_extract(llm, account_id, chat_uid, chat_name, transcript):
        calls["memory"] = (account_id, chat_uid, chat_name, transcript)
        return ["B站最近流行大肥鱼"]

    def fake_observe(account_id, chat_uid, user_text, reply, source="user", llm=None):
        calls["mood"] = (account_id, chat_uid, source, reply)
        return "queued"

    monkeypatch.setattr(patrol.memory, "extract_and_store", fake_extract)
    monkeypatch.setattr(patrol.mood_service, "observe", fake_observe)
    llm = FakeLLM("## 摘要\n今天刷到一条好玩的。\n\n## 报告\n- 标题 / BV1 / 值得看")
    scheduler = patrol.BiliPatrolScheduler(llm, FakeHub(), FakeRegistry())
    out = asyncio.run(scheduler.run_now("acc_default", "manual"))
    assert out["ok"] is True
    assert "记忆：更新 1 条" in out["effects"]
    assert "成长：已留给下次夜间反思" in out["effects"]
    assert "心情：已提交判断" in out["effects"]
    assert calls["memory"][1] == ""
    assert "后台B站巡逻见闻" in calls["memory"][3]
    assert calls["mood"][0] == "acc_default"
    assert calls["mood"][2] == "bili-patrol"
    assert calls["mood"][3] == "今天刷到一条好玩的。"


def test_parse_patrol_output():
    text = "## 摘要\n今天看了两条。\n\n## 报告\n- 标题 / BV1 / 值得看"
    summary, report = patrol.parse_patrol_output(text)
    assert summary == "今天看了两条。"
    assert "BV1" in report
    assert patrol.parse_patrol_output("只有正文") == ("只有正文", "只有正文")


def test_run_once_saves_report(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(bili_setup, "PATROL_DIR", tmp_path / "patrol")
    llm = FakeLLM("## 摘要\n今天刷到一条好玩的。\n\n## 报告\n- 标题 / BV1 / 值得看")
    scheduler = patrol.BiliPatrolScheduler(llm, FakeHub(), FakeRegistry())
    out = asyncio.run(scheduler.run_now("acc_default", "manual"))
    assert out["ok"] is True
    assert out["summary"].startswith("今天")
    runs = db.list_bili_patrol_runs("acc_default")
    assert runs[0]["status"] == "ok"
    path = tmp_path / "patrol" / f"{time.strftime('%Y-%m-%d')}.md"
    assert path.is_file()
    assert "BV1" in path.read_text("utf-8")
    logs = list((tmp_path / "patrol" / "logs").glob("*.log"))
    assert logs and "巡逻完成" in logs[0].read_text("utf-8")
    snap = scheduler.active_snapshot("acc_default", 0)
    assert snap["run_id"] == out["run_id"]
    assert snap["done"] is True
    assert any("巡逻完成" in item["text"] for item in snap["items"])


def test_run_once_without_bili_tools_marks_error(temp_db):
    class EmptyMcp:
        def tools_for(self, auto=False):
            return []

    class EmptyRegistry:
        def __init__(self):
            self.mcp = EmptyMcp()

    scheduler = patrol.BiliPatrolScheduler(FakeLLM("x"), FakeHub(), EmptyRegistry())
    out = asyncio.run(scheduler.run_now("acc_default", "manual"))
    assert out["ok"] is False
    assert db.list_bili_patrol_runs("acc_default")[0]["status"] == "error"


def test_delivery_wechat_sends_summary(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(bili_setup, "PATROL_DIR", tmp_path / "patrol")
    monkeypatch.setattr(
        patrol,
        "cfg_for",
        lambda aid: {
            **patrol.PATROL_DEFAULTS,
            "delivery": "both",
            "target_chat_uid": "acc_default:c1",
        },
    )
    hub = FakeHub()
    scheduler = patrol.BiliPatrolScheduler(FakeLLM("## 摘要\n发给微信。\n\n## 报告\n正文"), hub, FakeRegistry())
    out = asyncio.run(scheduler.run_now("acc_default", "manual"))
    assert out["ok"] is True
    assert hub.session.sent == [("acc_default:c1", "发给微信。")]


def test_due_respects_daily_max(temp_db, monkeypatch):
    monkeypatch.setattr(
        patrol,
        "cfg_for",
        lambda aid: {**patrol.PATROL_DEFAULTS, "enabled": True, "daily_max": 0},
    )
    scheduler = patrol.BiliPatrolScheduler(FakeLLM("x"), FakeHub(), FakeRegistry())
    assert scheduler._due("acc_default") is False
