"""桌宠素材装配与校验。

上游 dsh-dafeiyu 把「素材根」写死为 runtime/ 的父目录（asset_paths.bundle_root()），
所以装配产物必须正好是 <bundle>/runtime/*.py + <bundle>/assets/ 的形状。
本模块只读上游文件、只做复制与 manifest 重写，不改上游一行代码。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config

UPSTREAM_REPO = "https://github.com/QCYTSN/dsh-dafeiyu"
UPSTREAM_TAG = "v0.1.23"
UPSTREAM_COMMIT = "084323845ab82b68d5388d5c638bec09d8544a8a"
# npm 官方包 dsh-dafeiyu-0.1.23.tgz 的 sha1（registry dist.shasum，2026-10-29 实测吻合）
# 用途见 tools/pet_assets.py：本地包必须先对上这个值，否则拒绝装配
UPSTREAM_ARTIFACT_SHA1 = "ff0bae26429fe336d288b07aa89fdc09436576ec"
UPSTREAM_ARTIFACT_NAME = "dsh-dafeiyu-0.1.23.tgz"

# 一期六个状态 clip 就够；DISCONNECTED 在上游 stateMap 里回落到 idle
ASSET_STATES: tuple[str, ...] = ("idle", "thinking", "working", "success", "error", "waiting")
RUNTIME_FILES: tuple[str, ...] = (
    "__init__.py", "helper.py", "animation_model.py",
    "pet_geometry.py", "layout_store.py", "asset_paths.py",
)
# 上游 ASSET_LICENSE：legacy/dafeiyu 帧不受 MIT 覆盖，禁止在仓库外分发
LEGACY_ROOTS: tuple[str, ...] = ("legacy",)
LICENSE_FILES: tuple[str, ...] = (
    "LICENSE", "ASSET_LICENSE.md",
    "assets/dsh-pet-LICENSE.txt", "assets/cursors/LICENSE", "assets/cursors/README.md",
)
EXTRA_ASSETS: tuple[str, ...] = (
    "assets/sounds", "assets/cursor_grab.cur", "assets/cursor_grabbing.cur",
)


def bundle_dir() -> Path:
    return config.DATA_DIR / "pet"


def helper_script(dest: Path | None = None) -> Path:
    return (dest or bundle_dir()) / "runtime" / "helper.py"


def trim_manifest(manifest: dict[str, Any], *, minimal: bool) -> dict[str, Any]:
    """裁剪时必须同步重写引用，否则上游 animation_model 会 KeyError。"""
    out = json.loads(json.dumps(manifest))
    if not minimal:
        return out
    out["clips"] = {name: clip for name, clip in out["clips"].items() if name in ASSET_STATES}
    out["workingActivityMap"] = {key: "working" for key in out.get("workingActivityMap", {})}
    out["idleMicroClips"] = []
    return out


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, expected: str = UPSTREAM_ARTIFACT_SHA1) -> tuple[bool, str]:
    """官方发布包必须对上 registry 公布的 sha1，否则拒绝装配。

    不这么做的后果不是"不安全"这么抽象：拿一份版本不符的包装配，assembly.json 里
    记的 upstreamTag/Commit 就在说谎，而 verify_bundle 的 [commit] 检查恰好看不出来。
    """
    got = sha1_of(path)
    if got != expected:
        return False, f"[artifact] {path.name} sha1={got}，与官方 {expected} 不符，请重新下载"
    return True, ""


def _clip_digest(digests: list[str]) -> str:
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()


def assemble(source: Path, dest: Path, *, minimal: bool = False) -> dict[str, Any]:
    """source 是上游 bundle 根（子模块工作树或解开的 tgz）；dest 是 data/pet。"""
    source = Path(source)
    runtime_src = source / "runtime"
    manifest_path = source / "assets" / "pet-manifest.json"
    missing = [name for name in RUNTIME_FILES if not (runtime_src / name).is_file()]
    if missing or not manifest_path.is_file() or not (source / "assets" / "pet").is_dir():
        raise FileNotFoundError(
            f"上游素材源不完整（缺 {missing or ['assets/pet-manifest.json']}）：{source}\n"
            "先执行 git submodule update --init --depth 1 vendor/dsh-feiyu-pet"
        )

    dest.mkdir(parents=True, exist_ok=True)
    for banned in LEGACY_ROOTS:
        target = dest / banned
        if target.exists():
            shutil.rmtree(target)

    (dest / "runtime").mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:  # 逐字节复制，禁止改写
        shutil.copyfile(runtime_src / name, dest / "runtime" / name)

    manifest = trim_manifest(json.loads(manifest_path.read_text("utf-8")), minimal=minimal)
    assets_dir = dest / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    frame_digests: dict[str, str] = {}
    for clip, spec in manifest["clips"].items():
        for frame in spec["frames"]:
            if frame.split("/")[0] != clip:
                raise ValueError(f"帧路径与 clip 不一致：{clip} -> {frame}")
            src = source / "assets" / "pet" / frame
            if not src.is_file():
                raise FileNotFoundError(f"缺帧文件：{src}")
            dst = assets_dir / "pet" / frame
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            frame_digests[frame] = _digest(dst)
    manifest_path_out = assets_dir / "pet-manifest.json"
    manifest_path_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_path_out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8"
    )
    for rel in LICENSE_FILES:  # 许可与归属必须随素材一起落地
        src = source / rel
        if src.is_file():
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    for extra in EXTRA_ASSETS:
        src = source / extra
        if src.is_dir():
            shutil.copytree(src, dest / extra, dirs_exist_ok=True)
        elif src.is_file():
            (dest / extra).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / extra)

    assembly = {
        "source": str(source),
        "upstreamRepo": UPSTREAM_REPO,
        "upstreamTag": UPSTREAM_TAG,
        "upstreamCommit": UPSTREAM_COMMIT,
        "minimal": minimal,
        "clips": sorted(manifest["clips"]),
        "frameCount": len(frame_digests),
        "clipDigests": {
            clip: _clip_digest([frame_digests[f] for f in spec["frames"]])
            for clip, spec in manifest["clips"].items()
        },
        "assembledAt": datetime.now().isoformat(timespec="seconds"),
    }
    (dest / "assembly.json").write_text(
        json.dumps(assembly, ensure_ascii=False, indent=1), "utf-8"
    )
    return assembly


def _referenced_frames(manifest: dict[str, Any]) -> set[str]:
    return {frame for spec in manifest["clips"].values() for frame in spec["frames"]}


def find_bundle_root(base: Path) -> Path | None:
    """在上游目录里定位真正的 bundle 根（tgz 多一层 package/，zip 多一层 <name>-<sha>/）。"""
    for candidate in (base, base / "package"):
        if (candidate / "runtime" / "helper.py").is_file():
            return candidate
    if base.is_dir():
        for child in base.iterdir():
            if child.is_dir():
                found = find_bundle_root(child)
                if found is not None:
                    return found
    return None


def _safe_extract_zip(handle, target: Path) -> None:
    """逐条校验后解包，拒绝绝对路径与 ..（Zip Slip）。"""
    import shutil

    for info in handle.infolist():
        name = info.filename
        path = Path(name)
        if path.is_absolute() or path.drive or ".." in path.parts:
            raise ValueError(f"压缩包包含非法路径：{name}")
        dest = target / name
        if info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with handle.open(info) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def extract_archive(archive: Path, target: Path) -> Path:
    """解包并返回 bundle 根；tgz 会先校验官方 sha1。"""
    import shutil
    import tarfile
    import zipfile

    if archive.suffix == ".tgz" or archive.name.endswith(".tar.gz"):
        ok, reason = verify_artifact(archive)
        if not ok:
            raise ValueError(reason)
    elif archive.suffix != ".zip":
        raise ValueError(f"只支持 .tgz / .tar.gz / .zip：{archive}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as handle:
            _safe_extract_zip(handle, target)
    else:
        with tarfile.open(archive) as handle:
            # filter="data" 拒绝越界路径、绝对路径与设备文件（Python 3.12+）
            handle.extractall(target, filter="data")
    root = find_bundle_root(target)
    if root is None:
        raise ValueError(f"解压后找不到 runtime/helper.py：{archive}")
    return root


def resolve_source(explicit: str | None = None, *, root: Path | None = None) -> Path:
    """定位上游素材根：显式路径 > 子模块。找不到就抛可执行的错误。

    供 CLI 与「一键准备」共用，避免两条路径各自判断来源。
    """
    base_root = root or config.ROOT
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = base_root / path
        if path.is_dir():
            found = find_bundle_root(path)
            if found is not None:
                return found
            raise FileNotFoundError(f"目录里没有 runtime/helper.py：{path}")
        if not path.is_file():
            raise FileNotFoundError(f"路径不存在：{path}")
        return extract_archive(path, bundle_dir().parent / "pet-source-extracted")
    submodule = base_root / "vendor" / "dsh-feiyu-pet"
    if (submodule / "runtime" / "helper.py").is_file():
        return submodule
    raise FileNotFoundError(
        "找不到上游素材。二选一：\n"
        "  git submodule update --init --depth 1 vendor/dsh-feiyu-pet\n"
        f"  python tools/pet_assets.py --from data/pet-source/{UPSTREAM_ARTIFACT_NAME}"
    )


def verify_bundle(dest: Path | None = None) -> tuple[bool, str]:
    """supervisor 启动前的门槛：素材不全就不拉进程。"""
    dest = dest or bundle_dir()
    assembly_path = dest / "assembly.json"
    if not assembly_path.is_file():
        return False, "[missing] 桌宠素材未装配，运行 tools/pet_assets.py"
    for name in RUNTIME_FILES:
        if not (dest / "runtime" / name).is_file():
            return False, f"[missing] 缺少 runtime/{name}"
    for banned in LEGACY_ROOTS:
        if (dest / banned).exists():
            return False, f"[legacy] 装配产物含禁止分发的 {banned}/ 目录"
    assembly = json.loads(assembly_path.read_text("utf-8"))
    if assembly.get("upstreamCommit") != UPSTREAM_COMMIT:
        return False, "[commit] 素材来自其他上游提交，请重新装配"
    manifest_path = dest / "assets" / "pet-manifest.json"
    if not manifest_path.is_file():
        return False, "[missing] 缺少 assets/pet-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    referenced = _referenced_frames(manifest)
    frames_root = manifest_path.parent / "pet"
    if not frames_root.is_dir():
        return False, "[missing] 缺少 assets/pet/ 帧目录"
    on_disk = {str(p.relative_to(frames_root)).replace("\\", "/") for p in frames_root.rglob("*.webp")}
    if not referenced <= on_disk:
        return False, f"[missing] 清单引用的帧缺失 {len(referenced - on_disk)} 张"
    if on_disk - referenced:
        return False, f"[digest] 目录里有 {len(on_disk - referenced)} 张清单未登记的帧"
    for clip, spec in manifest["clips"].items():
        got = _clip_digest([_digest(frames_root / f) for f in spec["frames"]])
        if assembly["clipDigests"].get(clip) != got:
            return False, f"[digest] clip {clip} 与装配记录不符"
    return True, ""
