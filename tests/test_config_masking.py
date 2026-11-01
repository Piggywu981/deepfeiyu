"""回显脱敏：前端能看见「有没有填」，但拿不到可用凭据；保存回来也不能把真值冲掉。"""
import json

import pytest

from app import config


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """把配置文件指到临时目录：这些用例会真写盘，绝不能碰本机 data/config.json。"""
    cfg_path = tmp_path / "config.json"
    base = {
        "active_account": "a1",
        "accounts": [{
            "id": "a1", "name": "测试号", "driver": "webhook",
            "webhook": {"callback_url": "http://example.com/cb", "token": "WH-TOKEN-9f3c"},
        }],
        "ai": {"api_key": "", "base_url": "", "model": "", "profiles": [], "active_profile": ""},
        "server": {"host": "127.0.0.1", "port": 8765, "access_token": "S3CRET-abcd"},
    }
    cfg_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config, "_cache", None)
    yield base
    monkeypatch.setattr(config, "_cache", None)


def _masked(cfg_dict):
    out = json.loads(json.dumps(cfg_dict))  # 深拷贝，避免改到 fixture 的 base
    return config.public(out)


def test_public_masks_webhook_token(isolated_cfg):
    pub = _masked(isolated_cfg)
    blob = json.dumps(pub, ensure_ascii=False)
    assert "WH-TOKEN-9f3c" not in blob
    acc = pub["accounts"][0]
    assert acc["webhook"]["configured"] is True
    assert acc["webhook"]["token"].startswith("•") and acc["webhook"]["token"].endswith("9f3c")


def test_public_masks_access_token(isolated_cfg):
    pub = _masked(isolated_cfg)
    assert "S3CRET-abcd" not in json.dumps(pub, ensure_ascii=False)
    assert pub["server"]["configured"] is True


def test_saving_masked_values_keeps_real_ones(isolated_cfg, tmp_path, monkeypatch):
    """控制台会把整份配置原样存回来，脱敏串不能被当成新凭据写进去。"""
    real_wh = isolated_cfg["accounts"][0]["webhook"]["token"]
    real_srv = isolated_cfg["server"]["access_token"]
    saved = config.save(_masked(isolated_cfg))
    assert saved["accounts"][0]["webhook"]["token"] == real_wh
    assert saved["server"]["access_token"] == real_srv
    # 真值仍在磁盘上，落盘的不是 • 串
    on_disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert on_disk["accounts"][0]["webhook"]["token"] == real_wh


def test_genuine_edit_still_applies(isolated_cfg):
    saved = config.save({"accounts": [{
        "id": "a1", "name": "改名",
        "webhook": {"callback_url": "http://example.com/cb", "token": "NEW-TOKEN"},
    }]})
    assert saved["accounts"][0]["webhook"]["token"] == "NEW-TOKEN"


def test_mcp_headers_masked_and_restored():
    from app.main import _mask_mcp_secrets, _restore_mcp_secrets

    text = json.dumps({"mcpServers": {"svc": {
        "url": "http://127.0.0.1:9/", "headers": {"Authorization": "Bearer supersecret"}}}})
    masked = _mask_mcp_secrets(text)
    assert "supersecret" not in masked
    assert json.loads(masked)["mcpServers"]["svc"]["url"] == "http://127.0.0.1:9/"
    restored = _restore_mcp_secrets(json.loads(masked), text)
    assert restored["mcpServers"]["svc"]["headers"]["Authorization"] == "Bearer supersecret"


def test_non_secret_mcp_fields_untouched():
    from app.main import _mask_mcp_secrets

    text = json.dumps({"mcpServers": {"svc": {
        "command": "python", "args": ["-m", "x"], "headers": {"X-Trace": "keep-me"}}}})
    assert "keep-me" in _mask_mcp_secrets(text)
