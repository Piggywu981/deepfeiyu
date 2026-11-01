"""桌宠素材装配：manifest 重写、帧校验、禁用 legacy。判据来自上游 animation_model。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pet import assets


MANIFEST_CLIPS = [
    "idle", "thinking", "working", "success", "error", "waiting",
    "working_search", "working_command", "idle_yawn", "head_pat",
    "dragging", "dragging_release",
]


def _manifest() -> dict:
    return {
        "formatVersion": 1,
        "baseSize": 412,
        "clips": {
            name: {
                "frames": [f"{name}/{name}_001.webp", f"{name}/{name}_002.webp"],
                "frameMs": 42,
                "loop": name == "idle",
            }
            for name in MANIFEST_CLIPS
        },
        "stateMap": {
            "IDLE": "idle", "THINKING": "thinking", "WORKING": "working",
            "WAITING": "waiting", "SUCCESS": "success", "ERROR": "error",
            "DISCONNECTED": "idle",
        },
        "workingActivityMap": {
            "searching": "working_search", "commanding": "working_command",
            "editing": "working", "testing": "working_command", "using-tool": "working",
        },
        "idleMicroClips": ["idle_yawn", "head_pat"],
        "idleMicroIntervalsMs": {
            "quiet": [45000, 90000], "normal": [25000, 45000], "lively": [12000, 22000],
        },
        "interactionCooldownMs": 1200,
    }


def _upstream(tmp_path: Path) -> Path:
    root = tmp_path / "upstream"
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    for name in assets.RUNTIME_FILES:
        (root / "runtime" / name).write_text("# stub\n", encoding="utf-8")
    man = _manifest()
    for clip, spec in man["clips"].items():
        for frame in spec["frames"]:
            p = root / "assets" / "pet" / frame
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"WEBP" + clip.encode())
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "pet-manifest.json").write_text(
        json.dumps(man, ensure_ascii=False), "utf-8"
    )
    return root


def test_trim_manifest_collapses_activity_map():
    out = assets.trim_manifest(_manifest(), minimal=True)
    assert set(out["clips"]) == set(assets.ASSET_STATES)
    # 上游 _clip_for 返回映射值后直接 self.clips[...] 索引：映射表指向被裁 clip 就是 KeyError
    assert set(out["workingActivityMap"].values()) <= set(out["clips"])
    assert out["workingActivityMap"] == {k: "working" for k in out["workingActivityMap"]}
    # idleMicroClips 置空是安全的（上游 play_idle_micro 直接返回 False）
    assert out["idleMicroClips"] == []
    assert out["stateMap"] == _manifest()["stateMap"]


def test_trim_manifest_full_keeps_everything():
    out = assets.trim_manifest(_manifest(), minimal=False)
    assert out["clips"].keys() == _manifest()["clips"].keys()
    assert out["workingActivityMap"] == _manifest()["workingActivityMap"]


def test_assemble_writes_bundle_and_assembly(tmp_path):
    source = _upstream(tmp_path)
    dest = tmp_path / "bundle"
    assembly = assets.assemble(source, dest, minimal=True)
    assert assembly["minimal"] is True
    assert assembly["frameCount"] == 12  # 六个状态 clip × 2 帧
    assert assembly["upstreamCommit"] == assets.UPSTREAM_COMMIT
    assert (dest / "runtime" / "helper.py").is_file()
    assert (dest / "assets" / "pet-manifest.json").is_file()
    assert (dest / "assembly.json").is_file()
    # 上游按 runtime/ 的父目录找 assets/，装配产物必须正好是这个形状
    assert assets.helper_script(dest) == dest / "runtime" / "helper.py"


def test_assemble_never_carries_legacy(tmp_path):
    """legacy/dafeiyu 帧不受 MIT 覆盖、禁止仓库外分发：既不能进来，也不能留在原地。"""
    source = _upstream(tmp_path)
    legacy = source / "legacy" / "dafeiyu"
    legacy.mkdir(parents=True)
    (legacy / "dragging_238_01.png").write_bytes(b"restricted")
    dest = tmp_path / "bundle"
    stale = dest / "legacy"
    stale.mkdir(parents=True)
    (stale / "old.png").write_bytes(b"stale")
    assets.assemble(source, dest, minimal=True)
    assert not (dest / "legacy").exists()


def test_zip_slip_is_rejected(tmp_path):
    """素材 zip 里带 ../ 的成员不能在装配时写到目标目录之外。"""
    import zipfile

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", b"x")
    target = tmp_path / "out"
    target.mkdir()
    with zipfile.ZipFile(archive) as handle:
        with pytest.raises(ValueError, match="非法路径"):
            assets._safe_extract_zip(handle, target)   # noqa: SLF001
    assert not (tmp_path / "escaped.txt").exists()


def test_sha1_of_matches_published_vectors(tmp_path):
    # RFC 3174 / FIPS 示例向量，刻意写死十六进制而不是拿 hashlib 现算，否则测不出实现跑偏
    cases = {b"": "da39a3ee5e6b4b0d3255bfef95601890afd80709",
             b"abc": "a9993e364706816aba3e25717850c26c9cd0d89d"}
    for index, (payload, expected) in enumerate(cases.items()):
        p = tmp_path / f"v{index}.bin"
        p.write_bytes(payload)
        assert assets.sha1_of(p) == expected, payload


def test_sha1_of_handles_multi_chunk_files(tmp_path):
    p = tmp_path / "big.bin"
    payload = bytes(range(251)) * 6000          # 1.5MB，跨过 1MB 分块边界，能抓到漏尾块
    p.write_bytes(payload)
    import hashlib

    assert assets.sha1_of(p) == hashlib.sha1(payload).hexdigest()


def test_verify_artifact_rejects_tampered_file(tmp_path):
    p = tmp_path / assets.UPSTREAM_ARTIFACT_NAME
    p.write_bytes(b"tampered")
    ok, reason = assets.verify_artifact(p)
    assert ok is False and reason.startswith("[artifact]")
    assert assets.UPSTREAM_ARTIFACT_NAME in reason or p.name in reason


def test_verify_bundle_rejects_unassembled(tmp_path):
    ok, reason = assets.verify_bundle(tmp_path)
    assert ok is False and reason.startswith("[missing]")


def test_verify_bundle_accepts_fresh_assembly(tmp_path, upstream_bundle):
    assert assets.verify_bundle(upstream_bundle) == (True, "")


def test_verify_bundle_rejects_legacy(tmp_path, upstream_bundle):
    (upstream_bundle / "legacy").mkdir()
    (upstream_bundle / "legacy" / "x.png").write_bytes(b"nope")
    ok, reason = assets.verify_bundle(upstream_bundle)
    assert ok is False and reason.startswith("[legacy]")


def test_verify_bundle_rejects_extra_frames(tmp_path, upstream_bundle):
    stray = upstream_bundle / "assets" / "pet" / "idle" / "idle_999.webp"
    stray.write_bytes(b"WEBPstray")
    ok, reason = assets.verify_bundle(upstream_bundle)
    assert ok is False and reason.startswith("[digest]")


def test_verify_bundle_rejects_tampered_frame(tmp_path, upstream_bundle):
    victim = upstream_bundle / "assets" / "pet" / "idle" / "idle_001.webp"
    victim.write_bytes(b"WEBPtampered")
    ok, reason = assets.verify_bundle(upstream_bundle)
    assert ok is False and reason.startswith("[digest]")


def test_verify_bundle_rejects_other_commit(tmp_path, upstream_bundle):
    path = upstream_bundle / "assembly.json"
    data = json.loads(path.read_text("utf-8"))
    data["upstreamCommit"] = "0" * 40
    path.write_text(json.dumps(data), "utf-8")
    ok, reason = assets.verify_bundle(upstream_bundle)
    assert ok is False and reason.startswith("[commit]")


@pytest.fixture()
def upstream_bundle(tmp_path):
    source = _upstream(tmp_path)
    dest = tmp_path / "bundle"
    assets.assemble(source, dest, minimal=True)
    return dest

