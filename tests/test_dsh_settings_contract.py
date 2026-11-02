"""插件设置页依赖的控制台契约：端点存在、密钥不回传、掩码回写不覆盖真值。

不跑网络：只断言路由登记与 config.public/_unmask_secrets 的纯函数行为。
"""
from __future__ import annotations

import copy
from pathlib import Path

import app.main as main
from app import config

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dsh-plugin"

# 规格 §6 的 P1 端点：ops.js 必须登记、控制台必须真实注册
REQUIRED_API_PATHS = (
    "/api/settings",
    "/api/settings/test-ai",
    "/api/personas",
    "/api/personas/sync_wiki",
    "/api/update/check",
)


def test_ops_js_registers_required_console_paths():
    source = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for path in REQUIRED_API_PATHS:
        assert path in source, f"ops.js 缺少 {path}"
    # 删除人格是带 id 的路径，ops.js 里以 encodeURIComponent 前缀写法登记
    assert "/api/personas/" in source, "ops.js 缺少人格删除的带 id 路径"


def test_required_paths_exist_in_console_routes():
    registered = {getattr(route, "path", "") for route in main.app.routes}
    for path in REQUIRED_API_PATHS:
        assert path in registered, f"控制台未注册 {path}"
    assert "/api/personas/{name}" in registered, "控制台未注册人格删除路径"


def test_public_never_returns_real_api_key():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["ai"]["api_key"] = "sk-1234567890abcd"
    out = config.public(cfg)
    api_key = out["ai"]["api_key"]
    assert api_key != "sk-1234567890abcd"
    assert api_key.startswith("•") and api_key.endswith("abcd")
    assert out["ai"]["configured"] is True


def test_masked_secret_roundtrip_restores_true_value():
    """前端把未修改的掩码串原样回传，控制台必须还原真值而不是写入掩码。"""
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["server"]["access_token"] = "secret-token-1234"
    masked = config.public(cfg)["server"]["access_token"]
    assert masked.startswith("•")
    patch = {"server": {"access_token": masked}}
    config._unmask_secrets(patch, cfg)
    assert patch["server"]["access_token"] == "secret-token-1234"
