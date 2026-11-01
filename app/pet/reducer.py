"""总线事件 → 上游 Helper 协议消息。纯函数式，不碰进程、不碰 Qt。

事件词表取自 app/ 现有埋点：agent_turn{start}（本次新增）、agent_tool{tool_start,done}、
log{level}、status{connected}。一期不发 WAITING：项目里还没有「待确认队列」这种可观测
等待项，ui_gate 只是发送互斥锁且不发布事件，硬凑只会把动画演成假状态。
"""
from __future__ import annotations

import time
from typing import Any, Callable

STATES = ("IDLE", "THINKING", "WORKING", "WAITING", "SUCCESS", "ERROR", "DISCONNECTED")
IDLE_FALLBACK_MS = 8000
PULSE_TTL = 2000
PULSE_TTL_MAX = 2500

_SEARCH_HINTS = ("web", "search", "fetch", "browse", "query")
_COMMAND_HINTS = ("run", "exec", "shell", "command", "python", "powershell")
_MCP_HINTS = ("mcp__", "plugin__")


def time_ms() -> int:
    return int(time.time() * 1000)


def activity_for_tool(name: str) -> str:
    """工具名到上游 workingActivityMap 键的映射。

    内置/MCP/插件三条来源的名字风格不同（内置是函数名、MCP 是 mcp__server__tool、
    插件是 plugin__插件__工具）。语义提示优先于来源前缀：`mcp__bili__search_video`
    名字里写着 search，演「查找」比演「泛用工具」诚实；看不出语义的前缀工具才走 using-tool。
    """
    lowered = (name or "").lower()
    if any(hint in lowered for hint in _SEARCH_HINTS):
        return "searching"
    if any(hint in lowered for hint in _COMMAND_HINTS):
        return "commanding"
    if any(hint in lowered for hint in _MCP_HINTS):
        return "using-tool"
    return "editing"


class PetReducer:
    def __init__(self, account_id: str, *, now_ms: Callable[[], int] = time_ms) -> None:
        self.account_id = account_id
        self._now_ms = now_ms
        self.state = "IDLE"
        self.activity: str | None = None
        self.latched = False          # ERROR / DISCONNECTED：停住直到下一次真实变化
        self.last_chat_uid = ""       # 气泡要显示的会话；无 chat_uid 的事件不覆盖
        self.last_change_ms = now_ms()
        self._sent: dict[str, Any] | None = {"kind": "state", "state": "IDLE"}

    def current(self) -> dict[str, Any]:
        return {"state": self.state, "activity": self.activity, "latched": self.latched}

    def _set(self, state: str, activity: str | None = None) -> list[dict[str, Any]]:
        message: dict[str, Any] = {"kind": "state", "state": state}
        if activity:
            message["activity"] = activity
        if message == self._sent:
            return []
        self.state = state
        self.activity = activity
        self.last_change_ms = self._now_ms()
        self._sent = message
        return [message]

    def on_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        data = event.get("data") or {}
        data_account = data.get("account_id")
        if data_account and data_account != self.account_id:
            return []
        kind = event.get("type")
        if data.get("chat_uid"):
            self.last_chat_uid = str(data["chat_uid"])

        if kind == "agent_turn" and data.get("scope", "auto") == "auto":
            if data.get("type") == "start":
                self.latched = False
                return self._set("THINKING")
            return []

        if kind == "agent_tool" and data.get("scope", "auto") == "auto":
            dtype = data.get("type")
            if dtype == "tool_start":
                return self._set("WORKING", activity_for_tool(str(data.get("name", ""))))
            if dtype == "done":
                return self._finish()
            return []

        if kind == "log" and data.get("level") == "error":
            self.latched = True
            return self._set("ERROR")

        if kind == "status" and "connected" in data:
            if not data["connected"]:
                self.latched = True
                return self._set("DISCONNECTED")
            if self.latched:
                self.latched = False
                return self._set("IDLE")
        return []

    def _finish(self) -> list[dict[str, Any]]:
        """本轮收尾：出错/掉线期间不把失败洗成成功动画。"""
        if self.latched:
            return []
        self.last_change_ms = self._now_ms()
        self._sent = None            # pulse 之后状态回到 IDLE，允许再发一次 IDLE 快照
        return [{
            "kind": "pulse", "state": "SUCCESS", "ttlMs": PULSE_TTL,
            "resumeState": "IDLE", "resumeActivity": None,
        }]

    def tick(self) -> list[dict[str, Any]]:
        """静默超过阈值才回落 IDLE；latched 时由 supervisor 的定时器保持现状。"""
        if self.state == "IDLE" or self.latched:
            return []
        if self._now_ms() - self.last_change_ms < IDLE_FALLBACK_MS:
            return []
        return self._set("IDLE")
