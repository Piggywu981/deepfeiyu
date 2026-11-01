"""plugin.json 发现与加载。

插件有两种形态：
1. 声明式 HTTP 工具（推荐）：plugin.json 里写 tools[]，url/body 用 {arg} 占位；
2. 受信任的 Python 插件：manifest 里 trusted=true 且 entry 指向 plugin.py，
   plugin.py 暴露 register(add_tool) 或 TOOLS。

插件默认只在控制台可用；自动回复要显式在 manifest 里写 "auto_reply": true，
并且全局 capabilities.auto_reply_enabled 打开。
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

import httpx

from ..ai.agent import Tool
from .common import as_bool, compose_tool_name

log = logging.getLogger("capabilities.plugins")

MAX_PLUGINS = 100
_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _clean_tool_name(value: str, fallback: str = "tool") -> str:
    name = _NAME_RE.sub("_", (value or "").strip()).strip("_")
    return (name or fallback)[:48]


def _render(value: Any, args: dict[str, Any], *, url: bool = False) -> Any:
    if isinstance(value, str):
        full = re.fullmatch(r"\{([a-zA-Z0-9_]+)\}", value.strip())
        if full and full.group(1) in args:
            return args[full.group(1)]

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                return match.group(0)
            raw = args[key]
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            return quote(text, safe="") if url else text

        return re.sub(r"\{([a-zA-Z0-9_]+)\}", repl, value)
    if isinstance(value, dict):
        return {k: _render(v, args, url=url) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, args, url=url) for v in value]
    return value


def _truncate(value: Any, limit: int = 4000) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    if len(text) <= limit:
        return value
    return text[:limit] + "…(截断)"


async def call_http_tool(spec: dict[str, Any], args: dict[str, Any]) -> Any:
    method = str(spec.get("method") or "GET").upper()
    url = str(spec.get("url") or "").strip()
    if not url:
        return {"error": "插件工具没有配置 url"}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": f"插件工具只支持 http/https：{url}"}
    headers = _render(spec.get("headers") or {}, args)
    timeout = float(spec.get("timeout") or 15)
    kwargs: dict[str, Any] = {"headers": {str(k): str(v) for k, v in headers.items()}}
    if method in ("GET", "HEAD", "DELETE"):
        if spec.get("params"):
            kwargs["params"] = _render(spec["params"], args)
    else:
        if spec.get("body") is not None:
            kwargs["json"] = _render(spec["body"], args)
        elif args:
            kwargs["json"] = args
    try:
        follow = as_bool(spec.get("follow_redirects", False), False)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow) as client:
            resp = await client.request(method, _render(url, args, url=True), **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"插件请求失败：{exc}"}
    content_type = resp.headers.get("content-type", "")
    if "json" in content_type:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = resp.text
    else:
        data = resp.text
    out = {"status": resp.status_code, "data": _truncate(data)}
    if resp.status_code >= 400:
        out["error"] = f"插件接口返回 HTTP {resp.status_code}"
    return out


class _Builder:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, dict[str, Any], Callable[[dict[str, Any]], Any]]] = []

    def add_tool(
        self,
        name: str,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        handler: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if handler is None:
            raise ValueError("add_tool 需要 handler")
        self.items.append(
            (
                str(name or "tool"),
                str(description or ""),
                parameters or {"type": "object", "properties": {}},
                handler,
            )
        )


def _wrap_handler(handler: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    async def run(args: dict[str, Any]) -> Any:
        result = handler(args or {})
        if inspect.isawaitable(result):
            result = await result
        return result

    return run


class PluginRegistry:
    def __init__(self, dirs: Iterable[str | Path] | None = None) -> None:
        self.dirs = [Path(p) for p in (dirs or [])]
        self.plugins: list[dict[str, Any]] = []
        self.tools: list[Tool] = []
        self.tool_auto: dict[str, bool] = {}
        self.errors: list[str] = []
        self.last_scan = 0.0

    def refresh(self) -> None:
        plugins: list[dict[str, Any]] = []
        tools: list[Tool] = []
        tool_auto: dict[str, bool] = {}
        errors: list[str] = []
        seen_paths: set[str] = set()
        used_names: set[str] = set()
        for root in self.dirs:
            if not root.exists() or not root.is_dir():
                continue
            paths: list[Path] = []
            for pattern in ("plugin.json", "manifest.json"):
                paths.extend(root.glob(f"**/{pattern}"))
            for path in sorted(paths)[:MAX_PLUGINS]:
                key = str(path.resolve())
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                try:
                    manifest = json.loads(path.read_text("utf-8"))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{path}: {exc}")
                    continue
                if not isinstance(manifest, dict):
                    errors.append(f"{path}: manifest 必须是对象")
                    continue
                plugin_name = _clean_tool_name(
                    str(manifest.get("name") or path.parent.name), fallback="plugin"
                )
                base_name = plugin_name
                idx = 2
                while plugin_name in used_names:
                    plugin_name = f"{base_name}_{idx}"
                    idx += 1
                used_names.add(plugin_name)
                entry = {
                    "name": plugin_name,
                    "version": str(manifest.get("version") or ""),
                    "description": str(manifest.get("description") or ""),
                    "dir": str(path.parent),
                    "path": str(path),
                    "trusted": as_bool(manifest.get("trusted"), False),
                    "auto_reply": as_bool(manifest.get("auto_reply"), False),
                    "tools": [],
                    "error": "",
                }
                try:
                    for spec in manifest.get("tools") or []:
                        if not isinstance(spec, dict):
                            continue
                        tool = self._http_tool(plugin_name, spec)
                        if tool:
                            tools.append(tool)
                            entry["tools"].append(tool.name)
                    if manifest.get("entry"):
                        if not entry["trusted"]:
                            entry["error"] = "Python 插件需要 manifest 里 trusted=true"
                        else:
                            py_tools = self._python_tools(plugin_name, path.parent, str(manifest["entry"]))
                            tools.extend(py_tools)
                            entry["tools"].extend(t.name for t in py_tools)
                except Exception as exc:  # noqa: BLE001
                    entry["error"] = str(exc)
                    errors.append(f"{plugin_name}: {exc}")
                plugins.append(entry)
                for tool_name in entry["tools"]:
                    tool_auto[tool_name] = bool(entry["auto_reply"])
        self.plugins = plugins
        self.tools = tools
        self.tool_auto = tool_auto
        self.errors = errors
        self.last_scan = time.time()

    def _http_tool(self, plugin_name: str, spec: dict[str, Any]) -> Tool | None:
        raw_name = str(spec.get("name") or "")
        if not raw_name:
            return None
        tool_name = compose_tool_name("plugin", plugin_name, raw_name)
        description = f"[插件 {plugin_name}] {str(spec.get('description') or raw_name)}"
        parameters = spec.get("parameters") or {"type": "object", "properties": {}}

        async def handler(args: dict[str, Any], _spec: dict[str, Any] = spec) -> Any:
            return await call_http_tool(_spec, args or {})

        return Tool(
            name=tool_name,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    def _python_tools(self, plugin_name: str, folder: Path, entry: str) -> list[Tool]:
        path = (folder / entry).resolve()
        if folder.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"插件入口不在插件目录内：{entry}")
        module_name = f"deepfeiyu_plugin_{plugin_name}_{int(time.time())}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if not spec or not spec.loader:
            raise ValueError(f"无法加载 Python 插件：{entry}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        builder = _Builder()
        register = getattr(module, "register", None)
        if callable(register):
            register(builder.add_tool)
        else:
            for item in getattr(module, "TOOLS", []) or []:
                if isinstance(item, dict):
                    builder.add_tool(
                        item.get("name") or "",
                        item.get("description") or "",
                        item.get("parameters"),
                        item.get("handler"),
                    )
        out: list[Tool] = []
        for name, description, parameters, handler in builder.items:
            tool_name = compose_tool_name("plugin", plugin_name, name)
            out.append(
                Tool(
                    name=tool_name,
                    description=f"[插件 {plugin_name}] {description or name}",
                    parameters=parameters,
                    handler=_wrap_handler(handler),
                )
            )
        return out

    def tools_for(self, auto: bool = False) -> list[Tool]:
        if not auto:
            return list(self.tools)
        return [t for t in self.tools if self.tool_auto.get(t.name, False)]

    def status(self) -> dict[str, Any]:
        return {
            "dirs": [str(p) for p in self.dirs],
            "count": len(self.plugins),
            "tools": len(self.tools),
            "last_scan": self.last_scan,
            "errors": self.errors,
            "items": self.plugins,
        }
