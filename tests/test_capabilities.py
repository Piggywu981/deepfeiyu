"""能力系统：SKILL.md、plugin.json、MCP stdio、上下文过滤。"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.capabilities.mcp import McpManager
from app.capabilities.plugins import PluginRegistry, _render
from app.capabilities.registry import CapabilityRegistry
from app.capabilities.skills import SkillRegistry


def _skill(root, name="demo", description="演示技能", body="第一步：先读文件。\n第二步：再总结。"):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", "utf-8"
    )
    return path


def test_skill_discovery_prompt_and_load_tool(tmp_path):
    _skill(tmp_path)
    reg = SkillRegistry([tmp_path])
    reg.refresh()
    assert reg.status()["count"] == 1
    assert "demo" in reg.prompt_block()
    tools = {t.name: t for t in reg.tools()}
    assert set(tools) == {"load_skill", "list_skills"}
    result = asyncio.run(tools["load_skill"].handler({"name": "demo"}))
    assert result["ok"] is True
    assert "第一步" in result["body"]
    missing = asyncio.run(tools["load_skill"].handler({"name": "nope"}))
    assert "没有技能" in missing["error"]


def test_skill_without_frontmatter_uses_heading(tmp_path):
    path = tmp_path / "plain" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 整理收件箱\n把未读邮件按主题分组。\n", "utf-8")
    reg = SkillRegistry([tmp_path])
    reg.refresh()
    item = reg.list()[0]
    assert item["name"] == "整理收件箱"
    assert "未读邮件" in item["description"]


def test_plugin_http_tool_and_template_render(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"path": self.path}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        plugin_dir = tmp_path / "demo"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "demo",
                    "description": "演示",
                    "tools": [
                        {
                            "name": "echo",
                            "description": "回显",
                            "parameters": {
                                "type": "object",
                                "properties": {"q": {"type": "string"}},
                                "required": ["q"],
                            },
                            "method": "GET",
                            "url": f"http://127.0.0.1:{port}/echo?q={{q}}",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        reg = PluginRegistry([tmp_path])
        reg.refresh()
        assert reg.status()["count"] == 1
        tools = {t.name: t for t in reg.tools}
        tool = tools["plugin__demo__echo"]
        result = asyncio.run(tool.handler({"q": "你好 世界"}))
        assert result["status"] == 200
        assert "%E4%BD%A0%E5%A5%BD" in result["data"]["path"]
    finally:
        server.shutdown()
        server.server_close()


def test_plugin_python_requires_trusted(tmp_path):
    plugin_dir = tmp_path / "py"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "py", "entry": "plugin.py"}), "utf-8"
    )
    (plugin_dir / "plugin.py").write_text("def register(add_tool):\n    pass\n", "utf-8")
    reg = PluginRegistry([tmp_path])
    reg.refresh()
    item = reg.plugins[0]
    assert item["trusted"] is False
    assert "trusted" in item["error"]
    assert reg.tools == []


def test_render_url_and_body():
    assert _render("https://x/?q={q}", {"q": "a b"}, url=True) == "https://x/?q=a%20b"
    assert _render({"q": "{q}"}, {"q": "你好"}) == {"q": "你好"}
    assert _render("{n}", {"n": 3}) == 3


def _fake_mcp_script(tmp_path):
    path = tmp_path / "fake_mcp.py"
    path.write_text(
        r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{
            "protocolVersion":"2024-11-05","capabilities":{"tools":{}},
            "serverInfo":{"name":"fake","version":"1"}}}), flush=True)
    elif method == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"tools":[{
            "name":"echo","description":"echoes text",
            "inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}
        }]}}), flush=True)
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{
            "content":[{"type":"text","text":"echo:" + str(args.get("text",""))}]}}), flush=True)
''',
        "utf-8",
    )
    return path


def test_mcp_stdio_list_and_call(tmp_path):
    script = _fake_mcp_script(tmp_path)
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(script)],
                        "auto_reply": True,
                    }
                }
            }
        ),
        "utf-8",
    )
    manager = McpManager([config], timeout=10)

    async def scenario():
        await manager.start()
        try:
            status = manager.status()
            assert status["count"] == 1
            assert status["items"][0]["connected"] is True
            assert status["items"][0]["tools"][0]["name"] == "echo"
            tools = {t.name: t for t in manager.tools_for(auto=False)}
            assert "mcp__fake__echo" in tools
            result = await tools["mcp__fake__echo"].handler({"text": "hi"})
            assert result["ok"] is True
            assert "echo:hi" in result["content"]
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_mcp_stdio_accepts_lines_over_64k(tmp_path):
    script = tmp_path / "big_mcp.py"
    script.write_text(
        r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{
            "protocolVersion":"2024-11-05","capabilities":{"tools":{}},
            "serverInfo":{"name":"big","version":"1"}}}), flush=True)
    elif method == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"tools":[{
            "name":"big","description":"big result",
            "inputSchema":{"type":"object","properties":{}}}]}}), flush=True)
    elif method == "tools/call":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{
            "content":[{"type":"text","text":"x" * 100000}]}}), flush=True)
''',
        "utf-8",
    )
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"big": {"command": sys.executable, "args": [str(script)]}}}),
        "utf-8",
    )
    manager = McpManager([config], timeout=10)

    async def scenario():
        await manager.start()
        try:
            out = await manager.clients["big"].call("big", {}, max_text=200_000)
            assert out["ok"] is True
            assert len(out["content"]) == 100_000
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_registry_auto_context_filters_plugins_and_mcp(tmp_path):
    skills = tmp_path / "skills"
    _skill(skills, "s1")
    plugins = tmp_path / "plugins"
    pdir = plugins / "p1"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "p1",
                "auto_reply": True,
                "tools": [
                    {
                        "name": "t1",
                        "description": "t1",
                        "method": "GET",
                        "url": "http://127.0.0.1:1/never",
                    }
                ],
            }
        ),
        "utf-8",
    )
    pdir2 = plugins / "p2"
    pdir2.mkdir(parents=True)
    (pdir2 / "plugin.json").write_text(
        json.dumps(
            {
                "name": "p2",
                "tools": [
                    {
                        "name": "t2",
                        "description": "t2",
                        "method": "GET",
                        "url": "http://127.0.0.1:1/never",
                    }
                ],
            }
        ),
        "utf-8",
    )
    cfg = {
        "capabilities": {
            "enabled": True,
            "skills_dirs": [str(skills)],
            "plugins_dirs": [str(plugins)],
            "mcp_config": str(tmp_path / "missing.json"),
            "auto_reply_enabled": True,
            "console_enabled": True,
            "skills_in_prompt": True,
            "max_tools": 40,
        }
    }
    reg = CapabilityRegistry(lambda: cfg)
    reg.refresh_sync()
    console = {t.name for t in reg.tools_for("console")}
    auto = {t.name for t in reg.tools_for("auto")}
    assert "load_skill" in console and "load_skill" in auto
    assert "plugin__p1__t1" in console and "plugin__p1__t1" in auto
    assert "plugin__p2__t2" in console and "plugin__p2__t2" not in auto
    assert "s1" in reg.prompt_block()
    assert isinstance(reg.status()["tools"][0], dict)


def test_registry_disabled_returns_no_tools(tmp_path):
    _skill(tmp_path / "skills", "s1")
    cfg = {
        "capabilities": {
            "enabled": False,
            "skills_dirs": [str(tmp_path / "skills")],
            "plugins_dirs": [],
        }
    }
    reg = CapabilityRegistry(lambda: cfg)
    reg.refresh_sync()
    assert reg.tools_for("console") == []
    assert reg.prompt_block() == ""


def test_capabilities_api_status_skill_and_mcp_config(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app import config as config_mod

    skills = tmp_path / "skills"
    _skill(skills, "api-skill", body="API 技能正文")
    mcp_path = tmp_path / "mcp.json"
    cfg = {
        "capabilities": {
            "enabled": True,
            "skills_dirs": [str(skills)],
            "plugins_dirs": [],
            "mcp_config": str(mcp_path),
            "auto_reply_enabled": False,
            "console_enabled": True,
            "skills_in_prompt": True,
            "max_tools": 40,
        }
    }
    reg = CapabilityRegistry(lambda: cfg)
    reg.refresh_sync()
    monkeypatch.setattr(main, "capability_registry", reg)
    monkeypatch.setattr(config_mod, "save", lambda patch: patch)
    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    client = TestClient(main.app)

    status = client.get("/api/capabilities").json()
    assert status["skills"]["count"] == 1
    assert status["tools"][0]["name"] in ("load_skill", "list_skills")

    skill = client.get("/api/capabilities/skills/api-skill").json()
    assert skill["body"] == "API 技能正文"

    bad = client.put("/api/capabilities/mcp-config", json={"text": "{ broken"})
    assert bad.status_code == 400

    mcp_path.write_text("{}", "utf-8")
    got = client.get("/api/capabilities/mcp-config").json()
    assert got["path"] == str(mcp_path)
    saved = client.put(
        "/api/capabilities/mcp-config",
        json={"text": json.dumps({"mcpServers": {}})},
    )
    assert saved.status_code == 200
    assert json.loads(mcp_path.read_text("utf-8")) == {"mcpServers": {}}

    unknown = client.post("/api/capabilities/mcp/nope/test")
    assert unknown.status_code == 404


def test_mcp_parse_result_respects_max_text():
    from app.capabilities.mcp import _parse_result

    long = "x" * 50_000
    result = {"content": [{"type": "text", "text": long}]}
    assert len(_parse_result(result)["content"]) == 4000 + len("…(截断)")
    full = _parse_result(result, max_text=200_000)["content"]
    assert len(full) == 50_000


def test_mcp_parse_result_skips_redundant_structured_content():
    from app.capabilities.mcp import _parse_result

    payload = json.dumps({"status": "qrcode_ready"})
    result = {
        "content": [{"type": "text", "text": payload}],
        "structuredContent": {"result": payload},
    }
    assert _parse_result(result)["content"] == payload
    only_structured = {"content": [], "structuredContent": {"a": 1}}
    assert json.loads(_parse_result(only_structured)["content"]) == {"a": 1}


def test_string_booleans_are_not_truthy(tmp_path):
    from app.capabilities.common import as_bool
    from app.capabilities.mcp import load_server_configs

    assert as_bool("false") is False
    assert as_bool("true") is True
    assert as_bool("0") is False
    assert as_bool(None, True) is True

    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "p", "trusted": "false", "entry": "plugin.py"}), "utf-8"
    )
    (plugin_dir / "plugin.py").write_text("def register(add_tool):\n    pass\n", "utf-8")
    reg = PluginRegistry([tmp_path])
    reg.refresh()
    assert reg.plugins[0]["trusted"] is False
    assert reg.plugins[0]["error"]

    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        json.dumps({"mcpServers": {"x": {"command": "x", "auto_reply": "false"}}}),
        "utf-8",
    )
    servers = load_server_configs([cfg])
    assert servers[0].auto_reply is False


def test_compose_tool_name_is_ascii_and_keeps_suffix():
    from app.capabilities.common import compose_tool_name

    name = compose_tool_name("mcp", "天气", "读取文件")
    assert name.startswith("mcp__")
    assert all(ch.isascii() for ch in name)
    long_name = compose_tool_name("mcp", "s" * 100, "abcdefghijklmnop")
    assert len(long_name) <= 64
    assert long_name.endswith("ijklmnop")


def test_long_plugin_name_auto_filter_uses_mapping(tmp_path):
    name = "very_" + "long_plugin_name_" * 3
    pdir = tmp_path / name
    pdir.mkdir()
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "auto_reply": True,
                "tools": [
                    {
                        "name": "inspect",
                        "description": "inspect",
                        "method": "GET",
                        "url": "http://127.0.0.1:1/never",
                    }
                ],
            }
        ),
        "utf-8",
    )
    reg = PluginRegistry([tmp_path])
    reg.refresh()
    auto = [t.name for t in reg.tools_for(auto=True)]
    assert len(auto) == 1
    assert auto[0].startswith("plugin__")


def test_long_duplicate_plugin_names_keep_auto_tools_stable(tmp_path):
    plugins = tmp_path / "plugins"
    prefix = "long_plugin_name_" * 4
    for suffix in ("a", "b"):
        pdir = plugins / (prefix + suffix)
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": prefix + suffix,
                    "auto_reply": True,
                    "tools": [
                        {
                            "name": "inspect",
                            "description": "inspect",
                            "method": "GET",
                            "url": "http://127.0.0.1:1/never",
                        }
                    ],
                }
            ),
            "utf-8",
        )
    cfg = {
        "capabilities": {
            "enabled": True,
            "skills_dirs": [],
            "plugins_dirs": [str(plugins)],
            "mcp_config": str(tmp_path / "missing.json"),
            "auto_reply_enabled": True,
            "console_enabled": True,
            "max_tools": 40,
        }
    }
    reg = CapabilityRegistry(lambda: cfg)
    reg.refresh_sync()
    first = [t.name for t in reg.tools_for("auto") if t.name.startswith("plugin__")]
    second = [t.name for t in reg.tools_for("auto") if t.name.startswith("plugin__")]
    assert len(first) == 2
    assert first == second
    assert len(set(first)) == 2
