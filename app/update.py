"""更新检查：本地 git 提交与 Gitee 远端 master 对比，不做自动更新。

远端地址全部是本文件内的硬编码常量，不接收任何用户输入，无 SSRF 风险。
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from . import __version__, config

# 允许的更新源主机。仓库地址由本机 origin 推导，不接受配置或远端 URL 之外的指定，
# 这样公开仓（GitHub）与私有镜像（Gitee）都用同一条链路，而不会被人改指到第三方。
ALLOWED_HOSTS = frozenset({"gitee.com", "github.com"})
BRANCH_FALLBACK = "master"
NET_TIMEOUT = 6.0
CACHE_TTL = 600.0

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_at = 0.0


class GitUnavailable(RuntimeError):
    """本机没有可用的 git 命令。"""


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(config.ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise GitUnavailable("本机没有 git 命令") from exc
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "").strip() or "git 命令失败")
    return out.stdout.strip()


def local_commit() -> str:
    """本地 HEAD 提交；目录不是 git 仓库（或 git 不可用）时返回空串。"""
    if not (config.ROOT / ".git").exists():
        return ""
    try:
        if _git("rev-parse", "--is-inside-work-tree") != "true":
            return ""
        return _git("rev-parse", "HEAD")
    except GitUnavailable:
        return ""


def parse_origin(url: str) -> tuple[str, str]:
    """从 origin URL 解析 (host, owner/repo)；主机不在白名单则拒绝。"""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("没有配置 origin 远端")
    ssh = re.match(r"^(?:ssh://)?[^@/]+@([^:]+):(.+?)(?:\.git)?/?$", raw)
    if ssh:
        host, path = ssh.group(1), ssh.group(2)
    else:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = re.sub(r"\.git$", "", (parsed.path or "").strip("/"))
    host = host.lower().removeprefix("www.")
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"不支持的更新源主机：{host or raw}")
    parts = [seg for seg in path.split("/") if seg]
    if len(parts) < 2:
        raise ValueError(f"远端地址缺少 owner/repo：{raw}")
    return host, f"{parts[0]}/{parts[1]}"


def origin_remote() -> tuple[str, str]:
    return parse_origin(_git("remote", "get-url", "origin"))


def upstream_branch() -> str:
    """要比对的远端分支。

    顺序：当前分支的上游 → 远端默认分支（origin/HEAD）→ master。
    不能用「当前分支名」兜底：本地分支和 checkout 出来的 tag 在远端不存在，
    那样更新检查会一直报「远端没有 xxx 分支」。
    """
    for args in (
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
        ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
    ):
        try:
            ref = _git(*args)
        except Exception:  # noqa: BLE001 - 游离 HEAD、没抓过 origin 都会走到这里
            continue
        branch = ref.rsplit("/", 1)[-1]
        if branch and branch != "HEAD":
            return branch
    return BRANCH_FALLBACK


def source_label() -> str:
    """给用户看的比对对象：origin 推导出的仓库主页；解析不出来就空，前端会省略链接。"""
    try:
        host, repo = origin_remote()
    except Exception:  # noqa: BLE001 - 没有 origin 或非受支持主机
        return ""
    return f"https://{host}/{repo}"


def remote_commit() -> str:
    """远端默认分支最新提交：先查平台 API，再用 git ls-remote 兜底。"""
    host, repo = origin_remote()
    branch = upstream_branch()
    api = (
        f"https://gitee.com/api/v5/repos/{repo}/branches/{branch}"
        if host == "gitee.com"
        else f"https://api.github.com/repos/{repo}/commits/{branch}"
    )
    try:
        # 地址只由白名单主机 + origin 推导；禁止重定向，避免被拐去内网
        resp = httpx.get(api, timeout=NET_TIMEOUT, follow_redirects=False)
        if resp.status_code == 200:
            payload = resp.json()
            sha = (payload.get("commit") or {}).get("sha") or payload.get("sha") or ""
            if sha:
                return str(sha)
        raise RuntimeError(f"{host} 接口返回 {resp.status_code}")
    except (httpx.HTTPError, RuntimeError, ValueError):
        page = f"https://{host}/{repo}"
        line = _git("ls-remote", page + ".git", "refs/heads/" + branch)
        parts = line.split()
        if not parts:
            raise RuntimeError(f"远端没有 {branch} 分支") from None
        return parts[0]


def _do_check() -> dict[str, Any]:
    out: dict[str, Any] = {
        "git_repo": False,
        "local": "",
        "local_short": "",
        "remote": "",
        "remote_short": "",
        "version": __version__,
        "branch": upstream_branch(),
        "has_update": False,
        "error": "",
        "checked_at": time.time(),
        "repo_url": source_label(),
    }
    try:
        out["local"] = local_commit()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        return out
    if not out["local"]:
        return out
    out["git_repo"] = True
    out["local_short"] = out["local"][:7]
    try:
        out["remote"] = remote_commit()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"获取远端版本失败：{exc}"
        return out
    out["remote_short"] = out["remote"][:7]
    out["has_update"] = out["remote"] != out["local"]
    return out


def check(force: bool = False) -> dict[str, Any]:
    """检查更新，结果缓存 10 分钟；force=True 跳过缓存（WebUI 手动按钮用）。"""
    global _cache, _cache_at
    with _lock:
        if not force and _cache and time.time() - _cache_at < CACHE_TTL:
            return dict(_cache)
    result = _do_check()
    with _lock:
        _cache = dict(result)
        _cache_at = time.time()
    return result


def startup_check(timeout: float = 6.0) -> dict[str, Any] | None:
    """启动前检查更新：限时等待，超时/失败只打印提示，不阻塞启动。"""
    holder: dict[str, Any] = {}

    def _run() -> None:
        try:
            holder["result"] = check(force=True)
        except Exception as exc:  # noqa: BLE001
            holder["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    _print_result(holder.get("result"), holder.get("error") or ("超时" if t.is_alive() else ""))
    return holder.get("result")


def _print_result(result: dict[str, Any] | None, error: str) -> None:
    if result is None:
        print(f"[更新检查] 暂时不可用（{error}），已跳过，不影响启动")
        return
    if not result["git_repo"]:
        print("[更新检查] 目录不是 git 仓库，跳过检查")
        return
    if result["error"]:
        print(f"[更新检查] 暂时不可用（{result['error']}），已跳过，不影响启动")
        return
    if result["has_update"]:
        print(f"[更新检查] 发现新版本 {result['remote_short']}（当前 {result['local_short']}）")
        print("           更新方法：在项目目录运行 git pull 后重启服务；或到控制台「设置 → 软件更新」查看")
    else:
        print(f"[更新检查] 当前版本 {result['local_short']} 已是最新")
