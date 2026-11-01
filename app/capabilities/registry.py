"""能力注册表：把本地技能、插件、MCP 工具统一挂到智能体上。

默认安全边界：
- 技能只读（SKILL.md 正文按需加载），不执行脚本；
- 插件/MCP 默认只在控制台可用；
- 自动回复要同时满足全局 capabilities.auto_reply_enabled 和条目自己的 auto_reply。
"""
from __future__ import annotations

import logging
from dataclasses import replace
import time
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..ai.agent import Tool
from .common import as_bool
from .mcp import McpManager
from .plugins import PluginRegistry
from .skills import SkillRegistry

log = logging.getLogger("capabilities")


class CapabilityRegistry:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]] | None = None) -> None:
        self._get_cfg = get_cfg or config.load
        self.skills: SkillRegistry | None = None
        self.plugins: PluginRegistry | None = None
        self.mcp: McpManager | None = None
        self.started = False
        self.last_refresh = 0.0
        self.errors: list[str] = []

    def cfg(self) -> dict[str, Any]:
        return self._get_cfg().get("capabilities") or {}

    def enabled(self) -> bool:
        return as_bool(self.cfg().get("enabled", True), True)

    def _paths(self, value: Any, defaults: list[str]) -> list[Path]:
        raw = value if isinstance(value, list) else ([value] if value else defaults)
        out: list[Path] = []
        seen: set[str] = set()
        for item in raw:
            text = str(item or "").strip()
            if not text:
                continue
            path = Path(text)
            if not path.is_absolute():
                path = config.ROOT / path
            key = str(path.resolve()) if path.exists() else str(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
        return out

    def _mcp_paths(self) -> list[Path]:
        raw = self.cfg().get("mcp_config")
        defaults = ["data/mcp.json", ".mcp.json"]
        out = self._paths(raw, defaults)
        # B 站独立配置：不写进用户的 mcp_config，存在才加载
        bili = config.DATA_DIR / "bili" / "mcp.json"
        if bili.is_file() and bili not in out:
            out.append(bili)
        return out

    def mcp_config_path(self) -> Path:
        paths = self._mcp_paths()
        return paths[0] if paths else config.ROOT / "data" / "mcp.json"

    def refresh_sync(self) -> None:
        cfg = self.cfg()
        self.errors = []
        if not as_bool(cfg.get("enabled", True), True):
            self.skills = None
            self.plugins = None
            if self.mcp:
                self.mcp.configs = {}
                self.mcp.clients = {}
            return
        self.skills = SkillRegistry(self._paths(cfg.get("skills_dirs"), ["skills", "data/skills"]))
        self.skills.refresh()
        self.plugins = PluginRegistry(
            self._paths(cfg.get("plugins_dirs"), ["plugins", "data/plugins"])
        )
        self.plugins.refresh()
        if self.mcp is None:
            self.mcp = McpManager(
                self._mcp_paths(),
                timeout=float(cfg.get("timeout_sec") or 20),
            )
        self.errors.extend(self.plugins.errors)
        self.last_refresh = time.time()

    async def start(self) -> None:
        self.refresh_sync()
        if self.mcp and self.enabled():
            try:
                await self.mcp.start()
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"MCP: {exc}")
                log.warning("MCP 启动失败：%s", exc)
        self.started = True

    async def reload(self) -> None:
        if self.mcp:
            try:
                await self.mcp.stop()
            except Exception:  # noqa: BLE001
                pass
        self.mcp = None
        await self.start()

    async def stop(self) -> None:
        if self.mcp:
            try:
                await self.mcp.stop()
            except Exception:  # noqa: BLE001
                pass
        self.started = False

    def tools_for(self, context: str = "console") -> list[Tool]:
        if not self.enabled():
            return []
        cfg = self.cfg()
        auto = context == "auto"
        out: list[Tool] = []
        if self.skills:
            out.extend(self.skills.tools())
        if auto:
            if not as_bool(cfg.get("auto_reply_enabled", False), False):
                return out
        elif not as_bool(cfg.get("console_enabled", True), True):
            return out
        if self.plugins:
            out.extend(self.plugins.tools_for(auto=auto))
        if self.mcp:
            out.extend(self.mcp.tools_for(auto=auto))
        limit = max(1, min(200, int(cfg.get("max_tools") or 40)))
        # 不同来源可能撞名（尤其是 MCP 工具截断后）；Agent 用 dict 存工具，
        # 重名会静默覆盖，这里统一加序号并保持 ASCII 名称。
        unique: list[Tool] = []
        seen: set[str] = set()
        for tool in out:
            name = tool.name
            if name in seen:
                base = name[:56]
                idx = 2
                while f"{base}_{idx}" in seen:
                    idx += 1
                name = f"{base}_{idx}"[:64]
                tool = replace(tool, name=name)
            seen.add(name)
            unique.append(tool)
        return unique[:limit]

    def prompt_block(self) -> str:
        if not self.enabled() or not self.skills:
            return ""
        if not as_bool(self.cfg().get("skills_in_prompt", True), True):
            return ""
        return self.skills.prompt_block()

    def status(self) -> dict[str, Any]:
        cfg = self.cfg()
        return {
            "enabled": as_bool(cfg.get("enabled", True), True),
            "auto_reply_enabled": as_bool(cfg.get("auto_reply_enabled", False), False),
            "console_enabled": as_bool(cfg.get("console_enabled", True), True),
            "skills_in_prompt": as_bool(cfg.get("skills_in_prompt", True), True),
            "started": self.started,
            "last_refresh": self.last_refresh,
            "errors": self.errors,
            "skills": self.skills.status() if self.skills else {"count": 0, "items": [], "dirs": []},
            "plugins": self.plugins.status() if self.plugins else {"count": 0, "items": [], "dirs": []},
            "mcp": self.mcp.status() if self.mcp else {"count": 0, "items": [], "config_paths": []},
            "tools": [
                {"name": t.name, "description": t.description}
                for t in self.tools_for("console")
            ],
        }


registry = CapabilityRegistry()
