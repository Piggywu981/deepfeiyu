"""DSH 插件 bundle 的静态一致性：清单、patch、locale、icon、文件清单、工具名与 JS 语法。

只校验仓库里的静态文件，不安装插件、不连运行中的 Harness；面板视觉与实机审批
效果不在本文件覆盖范围内。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dsh-plugin"

# 规格 §5.2：Agent 工具名固定为这 8 个，Host 侧工具源码里必须一字不差地出现
TOOL_NAMES = (
    "deepfeiyu_status",
    "deepfeiyu_accounts",
    "deepfeiyu_chats",
    "deepfeiyu_messages",
    "deepfeiyu_send",
    "deepfeiyu_stats",
    "deepfeiyu_capabilities",
    "deepfeiyu_console",
)


def _manifest() -> dict:
    return json.loads((PKG / "package.json").read_text(encoding="utf-8"))


def _js_files() -> list[Path]:
    return [PKG / "index.js", PKG / "client.js", *sorted((PKG / "lib").glob("*.js"))]


def test_manifest_required_fields():
    data = _manifest()
    assert data["name"] == "@local/deepfeiyu-dsh"
    assert data["private"] is True
    assert data["type"] == "module"
    assert data["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert data["dsh"]["client"]["platform"] == "web"
    for key in (".", "./client", "./icon", "./locale/*.json"):
        assert key in data["exports"], f"package.json exports 缺少 {key}"
    assert data["exports"]["."] == "./index.js"
    assert data["exports"]["./client"] == "./client.js"


def test_patch_inserts_bundle_row():
    patch = yaml.safe_load((PKG / "cordis.patch.yml").read_text(encoding="utf-8"))
    rows = patch[0]["insert"]
    row = next(
        (r for r in rows if r.get("id") == "deepfeiyu"),
        None,
    )
    assert row is not None, "cordis.patch.yml 没有 id=deepfeiyu 的 insert 行"
    assert row["name"] == "@local/deepfeiyu-dsh"
    # 安装后必须有默认 config，否则 Host 侧 apply 拿到 undefined
    assert isinstance(row.get("config"), dict)
    assert row["config"].get("mode") in ("auto", "attach")
    assert row["config"].get("port") == 8765


@pytest.mark.parametrize("loc", ["en", "zh"])
def test_locale_meta(loc):
    meta = json.loads((PKG / "locale" / f"{loc}.json").read_text(encoding="utf-8"))["meta"]
    assert isinstance(meta["title"], str) and meta["title"].strip()
    assert isinstance(meta["description"], str) and meta["description"].strip()


def test_icon_exists_and_is_small_svg():
    icon = PKG / "icon.svg"
    assert icon.is_file(), "缺少 dsh-plugin/icon.svg"
    assert 0 < icon.stat().st_size <= 256 * 1024, "icon.svg 必须存在且不超过 256KiB"
    assert icon.read_text(encoding="utf-8").lstrip().startswith("<svg"), "icon.svg 不是 SVG"


def test_files_list_matches_disk():
    data = _manifest()
    assert data["files"], "package.json files 不能为空，否则安装包缺文件"
    missing: list[str] = []
    for rel in data["files"]:
        target = PKG / rel.rstrip("/")
        if rel.endswith("/"):
            if not target.is_dir():
                missing.append(rel)
        elif "*" in rel:
            if not list(PKG.glob(rel)):
                missing.append(rel)
        elif not target.is_file():
            missing.append(rel)
    assert not missing, "package.json files 声明了磁盘上不存在的条目：" + "、".join(missing)
    # 规格 §5.2 要求的 Host/Client 入口无论 files 怎么写都必须在
    for name in ("index.js", "client.js", "cordis.patch.yml"):
        assert (PKG / name).is_file(), f"缺少 bundle 必需文件 dsh-plugin/{name}"


def test_tool_names_match_host_source():
    tools = PKG / "lib" / "tools.js"
    if not tools.is_file():
        pytest.fail(
            "dsh-plugin/lib/tools.js 尚未生成（Host 侧并行开发中），无法校验 8 个工具名；"
            "等 Host 文件落地后重跑本测试"
        )
    source = tools.read_text(encoding="utf-8")
    for name in TOOL_NAMES:
        assert f"'{name}'" in source or f'"{name}"' in source, f"lib/tools.js 缺少工具名 {name}"


def test_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，无法做 JS 语法检查（已在 PR 中如实说明）")
    files = _js_files()
    assert files, "bundle 没有任何 JS 文件"
    for f in files:
        assert f.is_file(), f"缺少 JS 文件 {f.relative_to(ROOT)}"
        proc = subprocess.run(
            [node, "--check", str(f)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.returncode == 0, (
            f"{f.relative_to(ROOT)} 语法检查失败：{(proc.stderr or proc.stdout).strip()}"
        )
