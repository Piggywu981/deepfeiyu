"""更新源由实际 origin 推导：GitHub 与 Gitee 都能用，其他主机一律拒绝。"""
import pytest

from app import update as upd


@pytest.mark.parametrize("url,expect", [
    ("https://github.com/SomeOwner/some-repo.git", ("github.com", "SomeOwner/some-repo")),
    ("https://github.com/SomeOwner/some-repo", ("github.com", "SomeOwner/some-repo")),
    ("git@github.com:SomeOwner/some-repo.git", ("github.com", "SomeOwner/some-repo")),
    ("https://www.gitee.com/SomeOwner/some-repo.git", ("gitee.com", "SomeOwner/some-repo")),
    ("https://gitee.com/SomeOwner/some-repo/", ("gitee.com", "SomeOwner/some-repo")),
])
def test_parse_origin(url, expect):
    assert upd.parse_origin(url) == expect


@pytest.mark.parametrize("url", [
    "https://evil.example/owner/repo.git",
    "ssh://git@internal.corp/x/y.git",
    "",
    "https://gitee.com/onlyowner",
])
def test_untrusted_or_malformed_origin_is_refused(url):
    with pytest.raises(ValueError):
        upd.parse_origin(url)


def test_branch_comes_from_upstream(monkeypatch):
    monkeypatch.setattr(upd, "_git", lambda *a: "refs/remotes/origin/main")
    assert upd.upstream_branch() == "main"


def test_branch_falls_back_to_remote_default(monkeypatch):
    """本地分支与 checkout 的 tag 在远端不存在，必须回落到 origin/HEAD 而不是分支名。"""

    def fake_git(*args):
        if "@{u}" in args:
            raise RuntimeError("no upstream")
        if "symbolic-ref" in args:
            return "origin/main"
        raise AssertionError(f"不该再问别的：{args}")

    monkeypatch.setattr(upd, "_git", fake_git)
    assert upd.upstream_branch() == "main"


def test_branch_last_resort_is_master(monkeypatch):
    def fake_git(*args):
        raise RuntimeError("detached head, no origin/HEAD")

    monkeypatch.setattr(upd, "_git", fake_git)
    assert upd.upstream_branch() == "master"


def test_api_url_matches_host(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(upd, "origin_remote", lambda: ("github.com", "o/r"))
    monkeypatch.setattr(upd, "upstream_branch", lambda: "main")
    monkeypatch.setattr(upd, "_git", lambda *a: "")
    monkeypatch.setattr(upd.httpx, "get", fake_get)
    # API 被 mock 成抛错后走 ls-remote 兜底（fake _git 返回空），最终仍会报错；
    # 这里要验的是它请求了哪个 API 地址
    with pytest.raises(RuntimeError):
        upd.remote_commit()
    assert seen["url"] == "https://api.github.com/repos/o/r/commits/main"
