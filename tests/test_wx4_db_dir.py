"""微信 4.x 数据目录/账号解析。

背景：微信 4.x 跟随系统「文档」保存 xwechat_files；开启 OneDrive 文档备份后
「文档」被重定向到 OneDrive\\文档，旧 %USERPROFILE%\\Documents 下可能残留一份
过期副本。wechatauto 自带探测会选中副本（库 salt 已变 → 密钥全部验不过），
本模块按「.db 最新修改时间」选微信真正在写的目录。这里用临时目录模拟两种副本。
"""
from pathlib import Path

import os

from app.wechat.diagnose import (
    _normalize_wx4_db_dir,
    _select_wx4_account,
    _select_wx4_db_dir,
    _wx4_account_dirs,
)


def _make_account(root: Path, name: str, newest: float) -> Path:
    """在 root 下造一个含 db_storage 的账号目录，库文件 mtime 设为 newest。"""
    db_dir = root / name / "db_storage"
    (db_dir / "message").mkdir(parents=True)
    db_file = db_dir / "message" / "message_0.db"
    db_file.write_bytes(b"x" * 8)
    for target in (db_file, db_dir / "message", db_dir, root / name):
        try:
            os.utime(target, (newest, newest))
        except OSError:
            pass
    return root / name


def test_select_prefers_live_copy_over_stale(tmp_path: Path):
    stale = tmp_path / "Documents" / "xwechat_files"
    live = tmp_path / "OneDrive" / "文档" / "xwechat_files"
    _make_account(stale, "wxid_same_1111", 1000)
    _make_account(live, "wxid_same_1111", 2000)

    chosen = _select_wx4_db_dir(
        [str(stale), str(live), str(tmp_path / "OneDrive" / "文档"), str(tmp_path)]
    )
    assert Path(chosen) == live


def test_select_prefers_account_copy_when_user_pinned(tmp_path: Path):
    first = tmp_path / "old" / "xwechat_files"
    second = tmp_path / "new" / "xwechat_files"
    _make_account(first, "wxid_x_aaaa", 5000)   # 更新，但不是用户指定的账号
    _make_account(second, "wxid_y_bbbb", 1000)

    chosen = _select_wx4_db_dir(
        [str(first), str(second)], prefer_account="wxid_y_bbbb"
    )
    assert Path(chosen) == second


def test_pinned_same_account_uses_freshest_copy(tmp_path: Path):
    """同名账号在两份目录里都有时（换数据目录前后面），要选活跃的那份。"""
    stale = tmp_path / "Documents" / "xwechat_files"
    live = tmp_path / "OneDrive" / "文档" / "xwechat_files"
    _make_account(stale, "wxid_same_1111", 1000)
    _make_account(live, "wxid_same_1111", 2000)

    chosen = _select_wx4_db_dir(
        [str(stale), str(live)], prefer_account="wxid_same_1111"
    )
    assert Path(chosen) == live


def test_select_ignores_dirs_without_accounts(tmp_path: Path):
    empty = tmp_path / "empty"
    (empty / "xwechat_files").mkdir(parents=True)
    live = tmp_path / "live" / "xwechat_files"
    _make_account(live, "wxid_a_1234", 1000)

    assert Path(_select_wx4_db_dir([str(empty), str(live)])) == live
    assert _select_wx4_db_dir([str(empty)]) == ""


def test_normalize_accepts_parent_of_xwechat_files(tmp_path: Path):
    docs = tmp_path / "OneDrive" / "文档"
    live = docs / "xwechat_files"
    _make_account(live, "wxid_a_1234", 1000)

    assert Path(_normalize_wx4_db_dir(str(docs))) == live
    assert Path(_normalize_wx4_db_dir(str(live))) == live
    assert _normalize_wx4_db_dir(str(tmp_path / "missing")) == ""


def test_account_dirs_only_counts_db_storage(tmp_path: Path):
    root = tmp_path / "xwechat_files"
    _make_account(root, "wxid_a_1234", 1000)
    (root / "not_an_account").mkdir(parents=True)

    names = [Path(p).name for p in _wx4_account_dirs(str(root))]
    assert names == ["wxid_a_1234"]


def test_select_account_matches_visible_wxid(tmp_path: Path):
    root = tmp_path / "xwechat_files"
    _make_account(root, "wxid_a_1234", 2000)
    _make_account(root, "wxid_b_abcd", 1000)

    # 可见窗口对应 wxid_b：即使 wxid_a 库更活跃也要绑有窗口的账号
    assert _select_wx4_account(_wx4_account_dirs(str(root)), {"wxid_b"}) == "wxid_b_abcd"
    assert _select_wx4_account(_wx4_account_dirs(str(root)), {"wxid_c"}) == ""
    assert _select_wx4_account(_wx4_account_dirs(str(root)), set()) == ""
