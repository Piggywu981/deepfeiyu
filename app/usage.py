"""LLM 用量账本：每次模型调用记一条 token/估算金额，喂给 dsh-whale 小鲸鱼挂件。

只按事件记账（「本地估算」口径，统计 Deep肥鱼 自己的调用量）；余额本身仍由
balance.json 实时查询，账户里其他程序的消耗不会计入这里。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import config

MAX_EVENTS = 20000
PAYLOAD_EVENTS = 500
PAYLOAD_DAYS = 90


def _wait_lines(kind: str) -> list[dict[str, Any]]:
    text = "正在等待老大授权" if kind == "approval" else "正在等待老大回答"
    return [
        {"type": "session", "size": 10, "bold": True, "tpl": "[ {session} ]", "len": 5, "rgb": "champagne", "color": ""},
        {"type": "text", "text": text, "size": 7, "bold": True, "bgRgb": "", "bg": "", "rgb": "indigo", "color": ""},
    ]


# 用量面板默认设置：逐字段照抄上游 usageSettingsDefaults / soundEventsDefaults
# （vendor/dsh-whale-widget/lib/index.js）。前端编辑器的「恢复默认」以此为基准，
# 上游升级时若默认内容变了，这里要跟着同步。
USAGE_SETTINGS_DEFAULTS: dict[str, Any] = {
    "taskEnd": {"on": True, "sel": "frag:cost_a"},
    "subagent": {"mute": True},
    "wait": {"charClose": False},
    "events": {
        "press": {"vol": 1},
        "turnCost": {"vol": 1, "volSet": False, "bubbleOn": True},
        "question": {
            "on": True, "soundOn": False, "sel": "frag:ask_a", "vol": 1,
            "autoClose": True, "ttlSec": 180, "bubbleOn": True, "lines": _wait_lines("question"),
        },
        "approval": {
            "on": True, "soundOn": False, "sel": "frag:approve_a", "vol": 1,
            "autoClose": True, "ttlSec": 180, "bubbleOn": True, "lines": _wait_lines("approval"),
        },
    },
    "alert": {
        "on": True,
        "below": 5,
        "msg": "余额预警:当前余额已低于设定值 {below}",
        "lines": [
            {"type": "text", "text": "老大~你的DS余额", "size": 5, "bold": True},
            {"type": "text", "text": "已经不足", "size": 5, "bold": True, "row": 2},
            {"type": "text", "text": "¥{below}", "size": 5, "bold": True, "rgb": "rouge", "color": "", "bgRgb": "", "bg": "", "row": 2},
            {"type": "text", "text": "啦~", "size": 5, "bold": True, "row": 2},
            {"type": "image", "imgId": "bimg_money1", "size": 6, "imgScale": 0.4},
            {"type": "link", "text": ">> 喂 点 米 <<", "url": "https://platform.deepseek.com/top_up", "size": 1, "color": "#ffffff", "rgb": "", "bgRgb": "indigo", "bg": "", "bold": True, "ul": False},
        ],
        "autoClose": False,
        "ttlSec": 6,
    },
    "budget": {
        "on": True,
        "amount": 10,
        "msg": "今日已用已达预算 ¥{amount}",
        "lines": [
            {"type": "text", "text": "老大，今天花销已经超过", "size": 6, "bold": True, "row": 1},
            {"type": "text", "text": "¥{amount}", "size": 7, "bold": True, "row": 1, "rgb": "rouge", "color": "", "italic": False, "bgRgb": "", "bg": ""},
            {"type": "text", "text": "啦，再花要变成穷光蛋啦...", "size": 6, "bold": True, "row": 1},
        ],
        "autoClose": False,
        "ttlSec": 6,
    },
    "turnCost": {
        "lines": [
            {"type": "text", "text": "上一轮对话消耗:", "size": 8, "bold": True},
            {"type": "text", "text": "¥ {cost}", "size": 24, "bold": True, "color": "#e0433f"},
            {"type": "today", "size": 2, "tpl": "今日已用 {expense_ds}", "bold": False, "rgb": "", "color": "#ffffff", "bgRgb": "indigo", "bg": ""},
        ],
    },
}

# 这些键在读写设置时都按「一层合并」处理（与上游 writeUsageSettings/readUsageSettings 一致）
_SETTINGS_MERGE_KEYS = ("taskEnd", "alert", "budget", "turnCost", "wait", "subagent", "events")


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def turn_usage_meta(usage: dict[str, Any]) -> dict[str, Any]:
    """把 Agent 汇总的一轮 usage 转成可落库的 meta 字段。

    只有真的收到过服务商 usage 才写（measured）；没有真实用量时不伪造 0 成本。
    """
    if not isinstance(usage, dict) or not usage.get("measured"):
        return {}
    meta: dict[str, Any] = {
        "tokens": {
            "input": int(usage.get("prompt_tokens") or 0),
            "output": int(usage.get("completion_tokens") or 0),
        }
    }
    cost = usage.get("cost")
    if cost is not None:
        meta["cost"] = round(max(float(cost), 0.0), 6)
    return meta


class UsageLedger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                parsed = json.loads(self._path.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                parsed = {}
            self._data = {
                "seq": int(parsed.get("seq") or 0),
                "events": [e for e in (parsed.get("events") or []) if isinstance(e, dict)],
                "settings": parsed.get("settings") if isinstance(parsed.get("settings"), dict) else {},
            }
        return self._data

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, ensure_ascii=False), "utf-8")
        except OSError:
            pass  # 记账失败不该影响对话本身；下次记录会再尝试

    def record(self, model: str, cost: float, tokens: int) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            ts = int(time.time() * 1000)
            data["seq"] = int(data.get("seq") or 0) + 1
            event = {
                "ts": ts,
                "day": _today(),
                "model": model or "未知",
                "cost": round(max(float(cost), 0.0), 6),
                "tokens": int(tokens or 0),
            }
            data["events"].append(event)
            if len(data["events"]) > MAX_EVENTS:
                del data["events"][: len(data["events"]) - MAX_EVENTS]
            self._save()
            return {**event, "seq": data["seq"]}

    def last_turn(self) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            events = data["events"]
            if not events:
                return {"seq": int(data["seq"] or 0), "turn": None, "amount": None, "tokens": None, "ts": None}
            last = events[-1]
            return {
                "seq": int(data["seq"] or 0),
                "turn": last.get("model"),
                "amount": last.get("cost"),
                "tokens": last.get("tokens"),
                "ts": last.get("ts"),
            }

    def today_total(self) -> float:
        with self._lock:
            today = _today()
            return round(sum(float(e.get("cost") or 0) for e in self._load()["events"] if e.get("day") == today), 6)

    def settings(self) -> dict[str, Any]:
        with self._lock:
            saved = self._load()["settings"]
            merged = json.loads(json.dumps(USAGE_SETTINGS_DEFAULTS))  # 深拷贝
            for key in _SETTINGS_MERGE_KEYS:
                if isinstance(saved.get(key), dict):
                    merged[key] = {**(merged.get(key) or {}), **saved[key]}
            return merged

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            saved = data["settings"]
            for key in _SETTINGS_MERGE_KEYS:
                if isinstance(patch.get(key), dict):
                    saved[key] = {**(saved.get(key) or {}), **patch[key]}
            self._save()
            return self.settings()

    def _day_summary(self, day: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        of_day = [e for e in events if e.get("day") == day]
        amount = round(sum(float(e.get("cost") or 0) for e in of_day), 6)
        by_model: dict[str, float] = {}
        for e in of_day:
            name = str(e.get("model") or "未知")
            by_model[name] = round(by_model.get(name, 0.0) + float(e.get("cost") or 0), 6)
        models = [
            {"model": name, "cost": cost, "source": "events", "currency": "CNY"}
            for name, cost in sorted(by_model.items(), key=lambda kv: -kv[1])
        ]
        return {
            "day": day,
            # 消费记录(全部)面板的排序/分组/柱状图都以 date 为键（上游 forDay 双键并存），缺了会显示 undefined
            "date": day,
            "amount": amount,
            "currency": "CNY",
            "source": "events",
            "label": "本地估算",
            "eventEstimate": amount,
            "eventCurrency": "CNY",
            "bookBreakdown": [],
            "bookCount": 0,
            "total": amount,
            "models": models,
            "modelTotal": amount,
            "modelCurrency": "CNY",
        }

    def records_payload(self, version: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            events = data["events"]
            base = date.today()
            days = [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
            days7 = [self._day_summary(day, events) for day in days]
            total7 = round(sum(d["amount"] for d in days7), 6)
            known_days = sorted({e.get("day") for e in events if isinstance(e.get("day"), str)}, reverse=True)
            all_days = known_days[:PAYLOAD_DAYS] or days[:1]
            return {
                "ok": True,
                **(version or {}),
                "today": days7[0],
                "days7": days7,
                "total7": total7,
                "total7Currency": "CNY",
                "total7ByCurrency": {"CNY": total7},
                "all": {
                    "days": [self._day_summary(day, events) for day in all_days],
                    "events": sorted(events, key=lambda e: int(e.get("ts") or 0), reverse=True)[:PAYLOAD_EVENTS],
                },
                "settings": self.settings(),
            }


LEDGER = UsageLedger(config.DATA_DIR / "whale-usage.json")
