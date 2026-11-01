"""构建可公开的纯净快照：按排除集复制跟踪文件，复制后跑泄露扫描。

用法：
    python tools/release_snapshot.py --out ../deepfeiyu-public
    python tools/release_snapshot.py --scan .        # CI 对 checkout 复用同一套规则
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 排除清单与禁印字面量放在 release-policy.json（仓库根，已 gitignore）：
# 公开仓里不该出现「哪些目录被剔掉、为什么」这类自证性描述。机制留在本文件，数据留在私有仓。
POLICY_PATH = ROOT / "release-policy.json"


def load_policy(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """读排除清单；文件缺失时返回空策略（CI 的 --scan 仍跑正则规则）。"""
    f = path or POLICY_PATH
    if not f.exists():
        return {"exclude": (), "forbidden_literals": (), "scan_exempt": ()}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {
        "exclude": tuple(data.get("exclude_prefixes") or ()),
        "forbidden_literals": tuple(data.get("forbidden_literals") or ()),
        # 本工具自己的测试夹具长得就像泄露，扫到它只会逼人在白名单里消掉检测能力
        "scan_exempt": tuple(data.get("scan_exempt") or ()),
    }


# 真实身份与凭据形态。占位符见 PLACEHOLDERS，规则命中前先抹掉它们避免误报。
SCAN_PATTERNS = (
    ("REAL_WXID", re.compile(r"wxid_[a-z0-9]{8,}")),
    # 微信 AppID 形态（wx + 16 hex）：GitHub 密钥扫描按「Tencent WeChat API App ID」
    # 报 Public leak，本项目的 wxid_ 规则完全覆盖不到，故单列一条
    ("WECHAT_APPID", re.compile(r"\bwx[0-9a-f]{16}\b")),
    ("CDN_ATTACH_ID", re.compile(r"@cdn_[0-9a-f]{24,}")),
    # 文档里常见只写前几位加省略号的截断形式，它同样能定位到真实账号
    ("TRUNCATED_WXID", re.compile(r"wxid_[a-z0-9]{4,7}(?:…|\.\.\.)")),

    ("REAL_CHATROOM", re.compile(r"\b\d{11,}@chatroom")),
    ("API_KEY", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("BILI_CREDENTIAL", re.compile(r"(?:SESSDATA|bili_jct)=[0-9a-zA-Z]{12,}")),
    ("MACHINE_PATH", re.compile(r"[CD]:[\\/]{1,2}Users[\\/]{1,2}[\w.-]+", re.I)),
    ("PRIVATE_REPO_LINK", re.compile(r"gitee\.com/[^/\s]+/[^/\s]+/(commit|tree|commits)/")),
)

# 文档与测试里的合成值：形似真值，实际是占位符
PLACEHOLDERS = (
    "wxid_xxx", "wxid_sample", "wxid_self_main", "wxid_self_robot",
    "wxid_friend_01", "wxid_friend_02", "wxid_friend_03", "wxid_friend_04",
    "wxid_test_self", "wxid_test_ok", "wxid_main_no",
    "10000000001@chatroom", "10000000002@chatroom", "10000000003@chatroom",
    "12345678901@chatroom",
    # 脱敏器与气泡测试里的字母表顺序串（真密钥是随机的）：只白名单这两条确切字面量
    "sk-abc123def456ghi789jkl012", "sk-abcdef0123456789abcd",
    # 合成 CDN 句柄（全 0）。注意：占位符不要写成与真值同形的 hex 串——
    # GitHub 的密钥扫描按 AppID 形态（wx + 16 hex）判 Public leak，同形占位符会
    # 持续触发告警；AppID 的占位符因此改用非 hex 的 wxTESTAPPID000000。
    "@cdn_00000000000000000000000000000000",
)

TEXT_SUFFIXES = {
    ".py", ".md", ".js", ".json", ".txt", ".yaml", ".yml", ".bat", ".html", ".css",
}

# 扫描规则与排除清单的自测文件必然内含「长得像泄露」的合成值；这份豁免内置，
# 不能依赖 release-policy.json——公开仓的 CI 里没有那个文件，缺了就会卡住快照产出。
SELF_EXEMPT = ("tools/release_snapshot.py", "tests/test_release_snapshot.py")

# 扫描时跳过的目录名：CI 的 checkout 里有 .git，元数据会造出假命中
_SKIP_DIRS = {".git", ".venv", ".python", "__pycache__", ".pytest_cache", "node_modules"}


class SnapshotBlocked(RuntimeError):
    """快照里存在不允许公开的内容，已中止产出。"""


def scan_text(text: str, where: str, forbidden: tuple[str, ...] = ()) -> list[str]:
    """返回命中列表，每条形如「规则名 位置 片段」。"""
    hits: list[str] = []
    scrubbed = text
    for ph in PLACEHOLDERS:
        scrubbed = scrubbed.replace(ph, "")
    for name, pattern in SCAN_PATTERNS:
        for m in pattern.finditer(scrubbed):
            hits.append(f"{name} {where}: {m.group(0)[:40]}")
    for literal in forbidden:
        if literal in text:
            hits.append(f"FORBIDDEN_PATH {where}: {literal}")
    return hits


def scan_tree(
    base: Path, forbidden: tuple[str, ...] = (), exempt: tuple[str, ...] = ()
) -> list[str]:
    """扫描目录里所有文本文件。CI 的 --scan 与快照自检共用它，结论必须一致。"""
    hits: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = p.relative_to(base)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if rel.as_posix() in exempt or rel.as_posix() in SELF_EXEMPT:
            continue
        hits += scan_text(
            p.read_text(encoding="utf-8", errors="replace"), rel.as_posix(), forbidden
        )
    return hits


def scan_tracked(
    base: Path, forbidden: tuple[str, ...] = (), exempt: tuple[str, ...] = ()
) -> list[str]:
    r"""只扫「已跟踪且是普通文件」的内容。

    CI 用 submodules: recursive 检出，vendor/ 下会多出上游自己的文件；发行物不含
    它们（gitlink 不是文件，build_snapshot 会跳过），扫描也不该被上游测试夹具里
    的 C:\Users\me 之类占位路径判死。豁免清单与 scan_tree 同源：扫描器自己的定义
    与自测文件必然内含合成值，不豁免就会在公开仓的 CI 里卡死自己。
    """
    hits: list[str] = []
    for rel in _tracked_files(base):
        f = base / rel
        if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if rel in exempt or rel in SELF_EXEMPT:
            continue
        hits += scan_text(f.read_text(encoding="utf-8", errors="replace"), rel, forbidden)
    return hits


def _tracked_files(repo: Path) -> list[str]:
    # -z 保证中文路径不被 core.quotePath 转义，否则排除集会静默不命中
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo, check=True, capture_output=True
    )
    return [p.decode("utf-8") for p in out.stdout.split(b"\0") if p]


def gitlinks(repo: Path) -> list[tuple[str, str]]:
    """子模块在索引里的 (路径, commit) 列表。

    快照不复制子模块内容（上游素材的许可不允许再分发），但必须保留 gitlink：
    否则 .gitmodules 是空摆设，`git clone --recursive` 与 CI 的 submodules: recursive
    都取不到任何东西，依赖上游的测试与桌宠素材会直接失败。
    """
    out = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=repo, check=True, capture_output=True
    )
    links: list[tuple[str, str]] = []
    for rec in out.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.decode("utf-8").partition("\t")
        mode, sha, _stage = meta.split()
        if mode == "160000":
            links.append((path, sha))
    return links


def _excluded(rel: str, prefixes: tuple[str, ...]) -> bool:
    return rel.startswith(prefixes)


def build_snapshot(repo: Path, out: Path, policy: dict | None = None) -> list[str]:
    """按排除清单复制跟踪文件，扫描全部副本，命中即抛。"""
    pol = policy or load_policy()
    files = [
        f for f in _tracked_files(repo)
        if not _excluded(f, pol["exclude"]) and (repo / f).is_file()
    ]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for rel in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, dst)

    hits = scan_tree(out, pol["forbidden_literals"], pol["scan_exempt"])
    if hits:
        shutil.rmtree(out, ignore_errors=True)
        raise SnapshotBlocked("快照被拒绝：\n" + "\n".join(hits))
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="快照输出目录")
    ap.add_argument("--scan", help="只扫描目录，不产出快照")
    ap.add_argument("--scan-tracked", help="只扫该目录里已跟踪的普通文件（CI 用，跳过子模块内容）")
    ap.add_argument("--allow-no-policy", action="store_true",
                    help="明确允许在没有 release-policy.json 的情况下产出（公开仓的 CI 用）")
    ap.add_argument("--print-gitlinks", action="store_true",
                    help="打印子模块的 sha 与路径（发布时用 update-index 写回快照仓）")
    args = ap.parse_args()
    pol = load_policy()
    if args.print_gitlinks:
        for path, sha in gitlinks(ROOT):
            print(f"{sha}\t{path}")
        return 0
    if args.scan_tracked:
        root = Path(args.scan_tracked)
        hits = scan_tracked(root, pol["forbidden_literals"], pol["scan_exempt"])
        if hits:
            print(*hits, sep=chr(10))
            raise SnapshotBlocked(f"扫描命中 {len(hits)} 条：{args.scan_tracked}")
        print(f"[扫描] 通过（仅已跟踪文件）：{args.scan_tracked}")
        return 0
    if args.scan:
        hits = scan_tree(Path(args.scan), pol["forbidden_literals"], pol["scan_exempt"])
        if hits:
            print("\n".join(hits))
            raise SnapshotBlocked(f"扫描命中 {len(hits)} 条：{args.scan}")
        print(f"[扫描] 通过：{args.scan}")
        return 0
    if not args.out:
        ap.error("需要 --out 或 --scan")
    if not POLICY_PATH.exists() and not args.allow_no_policy:
        # 没有排除清单时 --out 等于全量复制已跟踪文件：在私有仓里就会把内部材料打进取件
        raise SystemExit(
            f"缺少 {POLICY_PATH.name}：排除清单不在，产出等于全量复制。"
            "确认这是公开仓的 CI 再加 --allow-no-policy。"
        )
    out = Path(args.out).resolve()
    files = build_snapshot(ROOT, out, pol)
    print(f"[快照] {len(files)} 个文件 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
