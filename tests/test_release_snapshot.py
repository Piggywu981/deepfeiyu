"""release_snapshot 的排除清单与扫描闸门。"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_snapshot as rs  # noqa: E402

# 用中性目录名做夹具：测试要验的是「按清单剔除」这个机制，不是某个具体第三方站名
POLICY = {
    "exclude": ("docs/third-party-scraped/", ".internal-notes/"),
    "forbidden_literals": ("scrvendor",),
    "scan_exempt": (),
}


def _make_repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# 变更\n干净内容\n", encoding="utf-8")
    (tmp_path / ".internal-notes").mkdir()
    (tmp_path / ".internal-notes" / "2026-10-21.md").write_text(
        "主号 wxid_aaaaaaaaaa\n", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "third-party-scraped").mkdir()
    (tmp_path / "docs" / "third-party-scraped" / "01.md").write_text(
        "scrvendor 报价\n", encoding="utf-8"
    )
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return tmp_path


def test_excluded_prefixes_are_not_copied(tmp_path):
    repo = _make_repo(tmp_path)
    out = tmp_path / "snapshot"
    rs.build_snapshot(repo, out, POLICY)
    written = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert "app/main.py" in written and "CHANGELOG.md" in written
    assert not any(p.startswith(".internal-notes/") for p in written)
    assert not any(p.startswith("docs/third-party-scraped/") for p in written)


def test_load_policy_falls_back_to_empty(tmp_path):
    pol = rs.load_policy(tmp_path / "missing.json")
    assert pol == {"exclude": (), "forbidden_literals": (), "scan_exempt": ()}


def test_self_exempt_works_without_policy(tmp_path):
    """公开仓的 CI 里没有 release-policy.json，扫描器仍必须豁免自己的定义与自测。

    这两个文件按设计内含「长得像泄露」的合成值；豁免一旦依赖私有策略文件，
    公开仓的快照产出就会在自己的闸门下卡死。
    """
    for rel in ("tools/release_snapshot.py", "tests/test_release_snapshot.py"):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("KEY = 'wxid_zzzzzzzzzz'\n", encoding="utf-8")
    assert rs.scan_text("wxid_zzzzzzzzzz", "tests/test_release_snapshot.py"), "样本应命中规则"
    assert rs.scan_tree(tmp_path, (), ()) == []          # 无策略、无额外豁免也跳过
    other = tmp_path / "app.py"
    other.write_text("KEY = 'wxid_zzzzzzzzzz'\n", encoding="utf-8")
    assert any("REAL_WXID" in h for h in rs.scan_tree(tmp_path, (), ()))  # 别的文件照常命中


def test_scanner_catches_chatroom_inside_filename():
    """群号常被写进落盘文件名（<id>@chatroom_1365.jpg），下划线不是词边界。"""
    hits = rs.scan_text("落盘 data/media/13243546879@chatroom_1365.jpg", "CHANGELOG.md")
    assert any("REAL_CHATROOM" in h for h in hits), hits


def test_scanner_flags_wechat_appid_and_cdn_handle():
    """GitHub 密钥扫描按 AppID 形态报 Public leak，本项目的 wxid_ 规则盖不到它。

    用例里用编造的 hex 串而不是真实值：本文件虽被扫描豁免，但它会随快照发布，
    写真实 AppID 等于把告警重新送回公开仓。
    """
    appid = "wx" + "a1b2c3d4e5f60718"
    cdn = "@cdn_" + "7f0c00080220dc37c3a374ca5deb534f" + "_1"
    hits = rs.scan_text(f'<appmsg appid="{appid}"> <attachid>{cdn}</attachid>', "t.py")
    assert any("WECHAT_APPID" in h for h in hits), hits
    assert any("CDN_ATTACH_ID" in h for h in hits), hits
    # 夹具里的合成值不误报：AppID 占位符刻意不含 hex，扫描器与外部扫描都识别不到
    assert rs.scan_text(
        'appid="wxTESTAPPID000000" @cdn_00000000000000000000000000000000', "t.py"
    ) == []


def test_scan_tracked_applies_exemptions(tmp_path):
    """CI 的 --scan-tracked 同样要跳过扫描器自己的文件，否则公开仓的 CI 会卡死自己。"""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    trip = "KEY = 'wxid_zzzzzzzzzz'\n"
    (tmp_path / "tools" / "release_snapshot.py").write_text(trip, encoding="utf-8")
    (tmp_path / "tests" / "test_release_snapshot.py").write_text(trip, encoding="utf-8")
    (tmp_path / "app.py").write_text(trip, encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    hits = rs.scan_tracked(tmp_path)
    assert hits and all("app.py" in h for h in hits), hits
    assert len(hits) == 1, hits


def test_gitlinks_are_reported(tmp_path):
    """子模块要留 gitlink：内容不复制（许可不允许再分发），但 .gitmodules 不能是空摆设。"""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")
    sha = "a" * 40   # 不能是全 0：git 会拒绝「null sha1」的 gitlink 条目
    git("update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/fake-sub")
    assert rs.gitlinks(tmp_path) == [("vendor/fake-sub", sha)]


def test_scanner_hits_real_identifier_forms():
    hits = rs.scan_text("绑定 wxid_aaaaaaaaaa 与 12345678901@chatroom", "f.md")
    # 12345678901@chatroom 在 PLACEHOLDERS 白名单里，只应命中 wxid
    assert [h.split()[0] for h in hits] == ["REAL_WXID"]
    assert rs.scan_text("群 13243546879@chatroom", "f.md")[0].startswith("REAL_CHATROOM")


def test_scanner_ignores_placeholders():
    assert rs.scan_text("示例 wxid_xxx 与 wxid_self_main", "f.md") == []


def test_scanner_flags_literals_from_policy():
    hits = rs.scan_text("scrvendor 的报价表", "docs/x.md", POLICY["forbidden_literals"])
    assert hits and "FORBIDDEN_PATH" in hits[0]
    # 不带策略时不该误报：站名只存在于私有策略文件里
    assert rs.scan_text("scrvendor 的报价表", "docs/x.md") == []


def test_scanner_flags_private_repo_links():
    hits = rs.scan_text("[`abc1234`](https://gitee.com/o/r/commit/abc1234)", "CHANGELOG.md")
    assert any("PRIVATE_REPO_LINK" in h for h in hits)


def test_build_snapshot_raises_when_dirty(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "app" / "leak.py").write_text("KEY = 'wxid_zzzzzzzzzz'\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "leak"],
        cwd=repo, check=True,
    )
    with pytest.raises(rs.SnapshotBlocked):
        rs.build_snapshot(repo, tmp_path / "out", POLICY)


def test_scan_tree_matches_build_snapshot(tmp_path):
    """CI 用的 --scan 与快照自检必须给出同一结论，所以共用一个扫描器。"""
    repo = _make_repo(tmp_path)
    out = tmp_path / "snapshot"
    rs.build_snapshot(repo, out, POLICY)
    assert rs.scan_tree(out, POLICY["forbidden_literals"]) == []

    (out / "app" / "leak.py").write_text("wxid_zzzzzzzzzz\n", encoding="utf-8")
    hits = rs.scan_tree(out, POLICY["forbidden_literals"])
    assert any("REAL_WXID" in h for h in hits)

    # .git 这类目录必须被跳过：CI 的 checkout 里一定有 .git
    (out / ".git").mkdir()
    (out / ".git" / "leak2.txt").write_text("wxid_zzzzzzzzzz\n", encoding="utf-8")
    hits = rs.scan_tree(out, POLICY["forbidden_literals"])
    assert len([h for h in hits if "REAL_WXID" in h]) == 1
    assert not any(".git" in h for h in hits)

def test_release_workflow_prepends_disclaimer():
    """发行说明必须带免责声明：文件存在，workflow 真的把它拼到 notes.md 顶部。"""
    disclaimer = (ROOT / "RELEASE_DISCLAIMER.md").read_text(encoding="utf-8")
    assert "PolyForm Noncommercial" in disclaimer
    assert "EULA.md" in disclaimer
    assert "与腾讯" in disclaimer
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "RELEASE_DISCLAIMER.md" in workflow
    assert "Set-Content notes.md" in workflow
