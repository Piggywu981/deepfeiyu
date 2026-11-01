"""桌宠素材装配 CLI：官方发布包或子模块 → data/pet/。

上游素材有两条合法来源，本工具都支持，优先用可信度可验证的那条：
1. npm 官方包 dsh-dafeiyu-0.1.23.tgz（结构上不含 legacy/，可按 sha1/sha512 校验）
2. Git 子模块 vendor/dsh-feiyu-pet（含 legacy/，装配时永不复制）
"""
from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:      # python tools/pet_assets.py 直跑时 sys.path[0] 是 tools/
    sys.path.insert(0, str(ROOT))

from app.pet import assets  # noqa: E402

UPSTREAM_VERSION = assets.UPSTREAM_TAG.lstrip("v")


def resolve_source(explicit: str | None) -> Path:
    """从子模块或显式路径（tgz/zip/目录）定位上游素材根。"""
    try:
        return assets.resolve_source(explicit, root=ROOT)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="装配 Deep肥鱼 桌宠素材")
    parser.add_argument("--from", dest="source", help="上游 tgz/zip 或仓库目录")
    parser.add_argument("--minimal", action="store_true", help="只装配六个状态 clip")
    parser.add_argument("--check", action="store_true", help="只校验现有装配产物")
    args = parser.parse_args()

    if args.check:
        ok, reason = assets.verify_bundle()
        print("素材就绪" if ok else f"素材不可用：{reason}")
        return 0 if ok else 1

    source = resolve_source(args.source)
    assembly = assets.assemble(source, assets.bundle_dir(), minimal=args.minimal)
    ok, reason = assets.verify_bundle()
    if not ok:
        print(f"装配后校验未通过：{reason}")
        return 1
    size_mb = sum(f.stat().st_size for f in (assets.bundle_dir() / "assets").rglob("*.webp")) / 1048576
    print(f"已装配 {assembly['frameCount']} 帧 / {len(assembly['clips'])} 组 "
          f"({size_mb:.1f} MB) -> {assets.bundle_dir()}")
    print(f"来源 {assembly['source']}")
    print(f"上游 {assembly['upstreamTag']} @ {assembly['upstreamCommit'][:8]} minimal={assembly['minimal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
