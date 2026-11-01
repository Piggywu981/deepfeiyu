"""通用 MCP 客户端：stdio + Streamable HTTP。

只实现工具调用所需的最小协议：initialize / notifications/initialized /
tools/list / tools/call。服务器进程出错不会拖垮主应用，状态在「能力」页可见。
配置兼容 Claude/Cursor 的 mcpServers 结构：

{
  "mcpServers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/data"]},
    "remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}, "auto_reply": false}
  }
}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx

from ..ai.agent import Tool
from .common import as_bool, compose_tool_name

log = logging.getLogger("capabilities.mcp")

PROTOCOL_VERSION = "2024-11-05"
MAX_TOOLS_PER_SERVER = 80
# asyncio 默认单行 64KiB；B 站二维码工具的结果 JSON 约 100KB，超限会让读线程挂掉
MAX_LINE_BYTES = 8 * 1024 * 1024


class McpError(RuntimeError):
    pass


def _tool_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return clean[:48] or "tool"


def _parse_result(result: dict[str, Any], max_text: int = 4000) -> Any:
    if result.get("isError"):
        texts = [
            str(c.get("text") or "")
            for c in (result.get("content") or [])
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return {"error": "\n".join(texts).strip() or "MCP 工具返回错误"}
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False, default=str))
    # 有文本内容时不再追加 structuredContent：FastMCP 会把字符串返回值再包一层
    # {"result": "..."}，拼进同一段文本会让 JSON 解析失败（B 站登录二维码就是这种情况）。
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, default=str))
    text = "\n".join(p for p in parts if p).strip()
    if len(text) > max_text:
        text = text[:max_text] + "…(截断)"
    return {"ok": True, "content": text or "（MCP 工具没有返回文本）"}


@dataclass
class McpServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    auto_reply: bool = False
    timeout: float = 20.0
    error: str = ""


def load_server_configs(
    paths: Iterable[str | Path], default_timeout: float = 20.0
) -> list[McpServerConfig]:
    out: list[McpServerConfig] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("读取 MCP 配置失败 %s: %s", path, exc)
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            continue
        for name, cfg in servers.items():
            if not isinstance(cfg, dict) or name in seen:
                continue
            seen.add(str(name))
            url = str(cfg.get("url") or "").strip()
            command = cfg.get("command") or ""
            transport = str(cfg.get("transport") or ("http" if url else "stdio")).lower()
            if transport in ("streamable_http", "streamable-http", "sse"):
                transport = "http"
            args = cfg.get("args") or []
            if isinstance(command, list) and command:
                args = list(command[1:]) + (args if isinstance(args, list) else [args])
                command = command[0]
            if isinstance(args, str):
                args = [args]
            env = {str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
            headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
            out.append(
                McpServerConfig(
                    name=str(name),
                    transport=transport,
                    command=str(command) if command else "",
                    args=[str(a) for a in args],
                    env=env,
                    cwd=str(cfg.get("cwd") or ""),
                    url=url,
                    headers=headers,
                    enabled=as_bool(cfg.get("enabled", True), True),
                    auto_reply=as_bool(cfg.get("auto_reply", False), False),
                    timeout=float(cfg.get("timeout") or default_timeout),
                )
            )
    return out


class _BaseClient:
    transport = ""

    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self.tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] = {}
        self.error = ""
        self.connected = False
        self.last_error = ""
        self.started_at = 0.0

    async def start(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def call(self, name: str, args: dict[str, Any], max_text: int = 4000) -> Any:  # pragma: no cover
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        return {
            "name": self.cfg.name,
            "transport": self.transport,
            "connected": self.connected,
            "error": self.error or self.last_error,
            "server_info": self.server_info,
            "tools": [
                {"name": t.get("name"), "description": t.get("description") or ""}
                for t in self.tools
            ],
            "started_at": self.started_at,
            "stderr_tail": list(getattr(self, "_stderr", []))[-20:],
        }


class StdioMcpClient(_BaseClient):
    transport = "stdio"

    def __init__(self, cfg: McpServerConfig) -> None:
        super().__init__(cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._reader_task: asyncio.Task[Any] | None = None
        self._stderr_task: asyncio.Task[Any] | None = None
        self._stderr: list[str] = []

    def _resolve_command(self) -> str:
        command = self.cfg.command.strip()
        if not command:
            raise McpError("stdio MCP 缺少 command")
        found = shutil.which(command)
        if found:
            return found
        if os.name == "nt" and not command.lower().endswith((".cmd", ".exe", ".bat")):
            for ext in (".cmd", ".exe", ".bat"):
                found = shutil.which(command + ext)
                if found:
                    return found
        return command

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self.cfg.env)
        cwd = self.cfg.cwd or None
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.proc = await asyncio.create_subprocess_exec(
            self._resolve_command(),
            *self.cfg.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
            creationflags=creationflags,
            limit=MAX_LINE_BYTES,
        )
        self.started_at = time.time()
        self._reader_task = asyncio.create_task(self._reader())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self._initialize()
        await self._list_tools()
        self.connected = True

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise McpError("MCP 进程未启动")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        await self.proc.stdin.drain()

    async def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                line = await self.proc.stdout.readline()
            except ValueError as exc:
                self.last_error = f"MCP 返回内容过大：{exc}"
                break
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and msg.get("id") in self._pending:
                fut = self._pending.pop(int(msg["id"]))
                if not fut.done():
                    if msg.get("error"):
                        fut.set_exception(McpError(str(msg["error"])))
                    else:
                        fut.set_result(msg.get("result") or {})
                continue
            method = msg.get("method")
            if method == "ping":
                try:
                    await self._send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})
                except Exception:  # noqa: BLE001
                    pass
            elif method and "id" in msg:
                # 未实现的服务器请求：明确返回 method not found，避免对面挂死
                try:
                    await self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32601, "message": "method not found"},
                        }
                    )
                except Exception:  # noqa: BLE001
                    pass
        reason = self.last_error or "MCP 进程已退出"
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(McpError(reason))
        self._pending.clear()
        self.connected = False

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                text = text[:500]
                self._stderr.append(text)
                del self._stderr[:-20]
                log.info("[mcp:%s] %s", self.cfg.name, text)

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        await self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        try:
            return await asyncio.wait_for(fut, timeout=self.cfg.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            detail = ("；".join(self._stderr[-3:])) if self._stderr else ""
            raise McpError(f"MCP {method} 超时{('：' + detail) if detail else ''}")

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "deepfeiyu", "version": "0.1.0"},
            },
        )
        self.server_info = result.get("serverInfo") or {}
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def _list_tools(self) -> None:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            tools.extend([t for t in (result.get("tools") or []) if isinstance(t, dict)])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools = tools[:MAX_TOOLS_PER_SERVER]

    async def call(self, name: str, args: dict[str, Any], max_text: int = 4000) -> Any:
        try:
            result = await self._request("tools/call", {"name": name, "arguments": args or {}})
        except McpError as exc:
            self.last_error = str(exc)
            return {"error": str(exc)}
        return _parse_result(result, max_text)

    async def stop(self) -> None:
        self.connected = False
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
        if self.proc:
            pid = self.proc.pid
            try:
                if os.name == "nt" and pid:
                    # npx.cmd 会拉起 node 子进程，只 terminate 父进程会留下孤儿；
                    # taskkill /T 连进程树一起收掉。
                    await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/T", "/F", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self.proc = None


class HttpMcpClient(_BaseClient):
    transport = "http"

    def __init__(self, cfg: McpServerConfig) -> None:
        super().__init__(cfg)
        self.session_id = ""
        self._client: httpx.AsyncClient | None = None
        self._next_id = 1

    async def start(self) -> None:
        if not self.cfg.url:
            raise McpError("HTTP MCP 缺少 url")
        self._client = httpx.AsyncClient(timeout=self.cfg.timeout, follow_redirects=True)
        await self._initialize()
        await self._list_tools()
        self.connected = True
        self.started_at = time.time()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.cfg.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._client:
            raise McpError("HTTP MCP 未启动")
        try:
            resp = await self._client.post(self.cfg.url, json=payload, headers=self._headers())
        except Exception as exc:  # noqa: BLE001
            raise McpError(f"HTTP MCP 请求失败：{exc}") from exc
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        if resp.status_code >= 400:
            raise McpError(f"HTTP MCP {resp.status_code}：{resp.text[:200]}")
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            target_id = payload.get("id")
            for line in resp.text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(msg, dict) and (target_id is None or msg.get("id") == target_id):
                    return msg
            return None
        try:
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            raise McpError(f"HTTP MCP 返回不是 JSON：{resp.text[:120]}") from exc

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        data = await self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        if not data:
            raise McpError(f"HTTP MCP {method} 没有返回")
        if data.get("error"):
            raise McpError(str(data["error"]))
        return data.get("result") or {}

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "deepfeiyu", "version": "0.1.0"},
            },
        )
        self.server_info = result.get("serverInfo") or {}
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def _list_tools(self) -> None:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            result = await self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend([t for t in (result.get("tools") or []) if isinstance(t, dict)])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools = tools[:MAX_TOOLS_PER_SERVER]

    async def call(self, name: str, args: dict[str, Any], max_text: int = 4000) -> Any:
        try:
            result = await self._request("tools/call", {"name": name, "arguments": args or {}})
        except McpError as exc:
            self.last_error = str(exc)
            return {"error": str(exc)}
        return _parse_result(result, max_text)

    async def stop(self) -> None:
        self.connected = False
        if self._client:
            await self._client.aclose()
            self._client = None


class McpManager:
    def __init__(self, config_paths: Iterable[str | Path] | None = None, timeout: float = 20.0) -> None:
        self.config_paths = [Path(p) for p in (config_paths or [])]
        self.timeout = timeout
        self.clients: dict[str, _BaseClient] = {}
        self.configs: dict[str, McpServerConfig] = {}
        self.errors: dict[str, str] = {}
        self.last_start = 0.0

    async def start(self) -> None:
        await self.stop()
        configs = [
            c for c in load_server_configs(self.config_paths, self.timeout) if c.enabled
        ]
        self.configs = {c.name: c for c in configs}
        self.errors = {}
        tasks = [self._start_one(c) for c in configs]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.last_start = time.time()

    async def _start_one(self, cfg: McpServerConfig) -> None:
        client: _BaseClient
        if cfg.transport == "http":
            client = HttpMcpClient(cfg)
        else:
            client = StdioMcpClient(cfg)
        try:
            await asyncio.wait_for(client.start(), timeout=max(5.0, cfg.timeout))
            self.clients[cfg.name] = client
        except Exception as exc:  # noqa: BLE001
            cfg.error = str(exc)
            self.errors[cfg.name] = str(exc)
            try:
                await client.stop()
            except Exception:  # noqa: BLE001
                pass

    async def stop(self) -> None:
        clients = list(self.clients.values())
        self.clients = {}
        for client in clients:
            try:
                await client.stop()
            except Exception:  # noqa: BLE001
                pass

    def tools_for(self, auto: bool = False) -> list[Tool]:
        out: list[Tool] = []
        for name, client in self.clients.items():
            cfg = self.configs.get(name)
            if auto and not (cfg and cfg.auto_reply):
                continue
            for spec in client.tools:
                raw_name = str(spec.get("name") or "")
                if not raw_name:
                    continue
                tool_name = compose_tool_name("mcp", name, raw_name)
                description = (
                    f"[MCP {name}] {str(spec.get('description') or raw_name)}"
                )
                parameters = (
                    spec.get("inputSchema")
                    or spec.get("input_schema")
                    or spec.get("parameters")
                    or {"type": "object", "properties": {}}
                )

                async def handler(
                    args: dict[str, Any],
                    _server: str = name,
                    _tool: str = raw_name,
                ) -> Any:
                    client = self.clients.get(_server)
                    if not client:
                        return {"error": f"MCP {_server} 未连接"}
                    return await client.call(_tool, args or {})

                out.append(
                    Tool(
                        name=tool_name,
                        description=description,
                        parameters=parameters,
                        handler=handler,
                    )
                )
                if len(out) >= MAX_TOOLS_PER_SERVER * max(1, len(self.clients)):
                    return out
        return out

    def status(self) -> dict[str, Any]:
        items = []
        for name, cfg in self.configs.items():
            client = self.clients.get(name)
            if client:
                item = client.status()
            else:
                item = {
                    "name": name,
                    "transport": cfg.transport,
                    "connected": False,
                    "error": cfg.error or self.errors.get(name, "未连接"),
                    "server_info": {},
                    "tools": [],
                    "started_at": 0,
                }
            item["url"] = cfg.url
            item["command"] = cfg.command
            item["auto_reply"] = cfg.auto_reply
            items.append(item)
        return {
            "config_paths": [str(p) for p in self.config_paths],
            "count": len(items),
            "last_start": self.last_start,
            "items": items,
        }
