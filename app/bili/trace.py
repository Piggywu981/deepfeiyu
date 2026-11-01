"""巡逻过程 trace：Agent 事件 → 终端行 + 每轮日志文件。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("bili")

_THINK_FLUSH = 160
_TEXT_FLUSH = 400
_TOOL_ARGS_LIMIT = 140


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text if len(text) <= limit else text[:limit] + "…"


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _flat(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.replace("\r", " ").replace("\n", " / ").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _tool_name(name: Any) -> str:
    return str(name or "?").rsplit("__", 1)[-1]


def _summarize_result(name: Any, result: Any) -> str:
    """把 MCP 的 JSON 结果压成一行可读摘要；完整结果仍留给模型上下文。"""
    outer = _decode(result)
    payload = _decode(outer.get("content")) if isinstance(outer, dict) and "content" in outer else outer
    if isinstance(outer, dict) and outer.get("error"):
        return "错误：" + _flat(outer["error"], 160)
    if isinstance(payload, dict) and payload.get("error"):
        return "错误：" + _flat(payload["error"], 160)
    if isinstance(payload, str):
        return _flat(payload, 200)
    if not isinstance(payload, dict):
        return _flat(payload, 200)
    if payload.get("message") and not payload.get("title"):
        return _flat(payload["message"], 160)

    title = payload.get("title") or ""
    if payload.get("segments") is not None or (title and payload.get("text")):
        head = f"《{_flat(title, 60)}》" if title else ""
        meta = " · ".join(
            part for part in (
                payload.get("language"),
                f"{payload['segments']} 段" if payload.get("segments") else "",
            ) if part
        )
        excerpt = _flat(payload.get("text") or "", 100)
        line = " · ".join(part for part in (head, meta) if part) or "字幕"
        return line + (f"\n    开头：{excerpt}" if excerpt else "")

    comments = payload.get("comments")
    if isinstance(comments, list):
        first = comments[0] if comments and isinstance(comments[0], dict) else {}
        hot = _flat(first.get("content") or "", 60)
        user = first.get("user") or ""
        like = first.get("like") or 0
        extra = f" · 热评：{user}：{hot}" if hot else ""
        if like:
            extra += f"（{like} 赞）"
        return f"{len(comments)} 条评论{extra}"

    danmakus = payload.get("danmakus")
    if isinstance(danmakus, list):
        head = _flat(" / ".join(str(d.get("text") or "") for d in danmakus[:5]), 100)
        return f"{len(danmakus)} 条弹幕" + (f" · 开头：{head}" if head else "")

    videos = payload.get("videos")
    if isinstance(videos, list):
        titles = [f"《{_flat(v.get('title'), 40)}》" for v in videos[:2] if isinstance(v, dict)]
        return f"{len(videos)} 条" + (f" · {' / '.join(titles)}" if titles else "")

    stat = payload.get("stat")
    if title and isinstance(stat, dict):
        parts = [
            f"播放 {stat.get('view', 0)}",
            f"评论 {stat.get('reply', 0)}",
            f"点赞 {stat.get('like', 0)}",
        ]
        return f"《{_flat(title, 60)}》 · " + " · ".join(parts)

    if payload.get("name") and payload.get("fans") is not None:
        return (
            f"{_flat(payload.get('name'), 40)} · 等级 {payload.get('level', 0)}"
            f" · 粉丝 {payload.get('fans', 0)}"
        )

    return _flat(payload, 200)


class PatrolTrace:
    def __init__(self, run_id: int, account_id: str, log_dir: Path, meta: str = "") -> None:
        self.run_id = int(run_id)
        self.account_id = account_id
        self.started_at = time.time()
        self.done = False
        self.error = ""
        self.usage: dict[str, Any] = {}
        self.seq = 0
        self.items: list[dict[str, Any]] = []
        self._think = ""
        self._answer = ""
        self._tool_at: dict[str, float] = {}
        self.path = Path(log_dir) / f"{self.run_id}.log"
        self._fh = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        except OSError as exc:
            log.warning("B 站巡逻日志文件打不开（#%s）：%s", self.run_id, exc)
        if meta:
            self._write("info", meta)

    def _write(self, kind: str, text: str) -> None:
        self.seq += 1
        stamp = time.strftime("%H:%M:%S")
        self.items.append({"seq": self.seq, "time": stamp, "kind": kind, "text": text})
        if len(self.items) > 2000:
            del self.items[:500]
        if self._fh:
            try:
                self._fh.write(f"[{stamp}] {text}\n")
                self._fh.flush()
            except OSError:
                self._fh = None
        log.info("B站巡逻 #%s %s", self.run_id, text)

    def _flush(self, attr: str, kind: str, prefix: str) -> None:
        text = getattr(self, attr).strip()
        setattr(self, attr, "")
        if not text:
            return
        for line in text.split("\n"):
            line = line.strip()
            if line:
                self._write(kind, f"{prefix}{line}")

    def _flush_all(self) -> None:
        self._flush("_think", "think", "思考：")
        self._flush("_answer", "text", "输出：")

    def add(self, kind: str, text: str) -> None:
        self._write(kind, text)

    async def on_event(self, evt: dict[str, Any]) -> None:
        kind = str(evt.get("type") or "")
        if kind == "reasoning":
            self._think += str(evt.get("text") or "")
            if "\n" in self._think or len(self._think) >= _THINK_FLUSH:
                self._flush("_think", "think", "思考：")
        elif kind == "tool_start":
            self._flush_all()
            name = _tool_name(evt.get("name"))
            args = _flat(evt.get("args") or {}, _TOOL_ARGS_LIMIT)
            self._tool_at[str(evt.get("id") or name)] = time.monotonic()
            self._write("tool", f"调用 {name} {args}")
        elif kind == "tool_end":
            key = str(evt.get("id") or evt.get("name") or "?")
            elapsed = time.monotonic() - self._tool_at.pop(key, time.monotonic())
            name = _tool_name(evt.get("name"))
            summary = _summarize_result(name, evt.get("result"))
            ok = bool(evt.get("ok")) and not summary.startswith("错误：")
            mark = "✓" if ok else "✗"
            self._write("tool_result", f"{mark} {name} {elapsed:.1f}s · {summary}")
        elif kind == "delta":
            self._answer += str(evt.get("text") or "")
            if "\n" in self._answer or len(self._answer) >= _TEXT_FLUSH:
                self._flush("_answer", "text", "输出：")
        elif kind == "note":
            self._flush_all()
            self._write("note", f"提示：{_clip(evt.get('text'), 300)}")
        elif kind == "error":
            self._flush_all()
            self.error = str(evt.get("message") or "未知错误")
            self._write("error", f"错误：{_clip(self.error, 500)}")
        elif kind == "done":
            self.usage = evt.get("usage") or {}

    def close(self, status: str = "ok", error: str = "") -> None:
        if self.done:
            return
        self._flush_all()
        if error:
            self.error = error
        elapsed = time.time() - self.started_at
        tokens = ""
        if self.usage.get("measured"):
            prompt = int(self.usage.get("prompt_tokens") or 0)
            completion = int(self.usage.get("completion_tokens") or 0)
            tokens = f" · tokens {prompt}+{completion}"
        if self.error:
            self._write("done", f"巡逻失败 · {elapsed:.0f}s · {_clip(self.error, 300)}")
        else:
            self._write("done", f"巡逻完成 · {elapsed:.0f}s{tokens}")
        self.done = True
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        after = max(0, int(after or 0))
        return {
            "run_id": self.run_id,
            "items": [item for item in self.items if item["seq"] > after],
            "done": self.done,
            "error": self.error,
            "started_at": self.started_at,
        }
