"""环境自检：判断当前机器能不能跑「全权限」微信 Hook。"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

_CACHE_TTL = 5.0
_running_cache: dict[str, tuple[float, bool]] = {}
_clients_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_windows_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])

SEARCH_DIRS = [
    Path(r"C:\Program Files\Tencent\WeChat"),
    Path(r"C:\Program Files (x86)\Tencent\WeChat"),
    Path(r"C:\Program Files\Tencent\Weixin"),
    Path(r"C:\Program Files (x86)\Tencent\Weixin"),
]


def _file_version(path: Path) -> str:
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            buf, "\\", ctypes.byref(ptr), ctypes.byref(length)
        ):
            return ""
        info = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms, ls = info[2], info[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""


def _running(image: str) -> bool:
    hit = _running_cache.get(image)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=0x08000000,
        ).stdout
        value = image.lower() in out.lower()
    except Exception:
        value = False
    _running_cache[image] = (time.time(), value)
    return value


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _process_path(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def list_windows(force: bool = False) -> list[dict[str, Any]]:
    """枚举可见的微信窗口（双开时会有多个）。"""
    global _windows_cache
    if not force and time.time() - _windows_cache[0] < _CACHE_TTL:
        return _windows_cache[1]
    if not sys.platform.startswith("win"):
        return []
    user32 = ctypes.windll.user32
    result: list[dict[str, Any]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value
        if not (cls.startswith("Qt5") or title in ("微信", "WeChat", "Weixin")):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        path = _process_path(pid.value)
        result.append(
            {
                "pid": pid.value,
                "title": title,
                "class": cls,
                "exe": path,
                "version": _file_version(Path(path)) if path else "",
                "rect": [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top],
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    result = sorted(result, key=lambda x: x["pid"])
    _windows_cache = (time.time(), result)
    return result


def _registry_client_dirs() -> list[Path]:
    """注册表登记的微信安装目录。

    微信 4.x 默认装到 Program Files，但也可装到 D: 等自定义位置
    （本机就是 D:\\ruanjian\\Weixin）；只扫 Program Files 会「检测不到微信」，
    auto 驱动因此永远选不到 weixin4。
    """
    dirs: list[Path] = []
    try:
        import winreg
    except Exception:
        return dirs
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\WeChat"),
    )
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as key:
                for value in ("InstallPath", "InstallDir", "Path"):
                    try:
                        raw, _kind = winreg.QueryValueEx(key, value)
                    except OSError:
                        continue
                    if isinstance(raw, str) and raw.strip():
                        path = Path(raw.strip())
                        if path.is_dir() and path not in dirs:
                            dirs.append(path)
        except OSError:
            continue
    return dirs


def _running_client_exes() -> list[Path]:
    """运行中的微信客户端 exe（注册表缺失时的兜底来源）。"""
    exes: list[Path] = []
    try:
        import psutil
    except Exception:
        return exes
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if str(proc.info.get("name") or "").lower() not in ("weixin.exe", "wechat.exe"):
                continue
            exe = proc.info.get("exe")
            if exe:
                path = Path(exe)
                if path.is_file() and path not in exes:
                    exes.append(path)
        except Exception:
            continue
    return exes


def _client_exe_candidates() -> list[Path]:
    """汇总所有可能的微信客户端 exe：默认目录、注册表安装目录、运行中进程。"""
    found: list[Path] = []
    for base in SEARCH_DIRS:
        if not base.exists():
            continue
        for name in ("WeChat.exe", "Weixin.exe"):
            exe = base / name
            if exe.is_file():
                found.append(exe)
        for pattern in ("*/WeChat.exe", "*/Weixin.exe"):
            found.extend(base.glob(pattern))
    for base in _registry_client_dirs():
        for name in ("WeChat.exe", "Weixin.exe"):
            exe = base / name
            if exe.is_file():
                found.append(exe)
        for pattern in ("*/WeChat.exe", "*/Weixin.exe"):
            found.extend(base.glob(pattern))
    found.extend(_running_client_exes())
    for win in list_windows():
        # list_windows 按「Qt5 类名」筛窗口，别的 Qt 程序（如远程工具）也会命中；
        # 只接受 exe 名确实是微信客户端的窗口，避免把无关程序当成微信
        exe = str(win.get("exe") or "")
        if exe and Path(exe).name.lower() in ("weixin.exe", "wechat.exe"):
            found.append(Path(exe))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        try:
            key = os.path.normcase(os.path.abspath(str(path)))
        except OSError:
            continue
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        unique.append(path)
    return unique


def detect_clients(force: bool = False) -> list[dict[str, Any]]:
    global _clients_cache
    if not force and time.time() - _clients_cache[0] < _CACHE_TTL:
        return _clients_cache[1]
    found: dict[str, dict[str, Any]] = {}
    for path in _client_exe_candidates():
        version = _file_version(path)
        family = "4.x" if path.name.lower() == "weixin.exe" else "3.x"
        found[str(path)] = {
            "path": str(path),
            "version": version,
            "family": family,
            "running": _running(path.name),
        }
    result = sorted(found.values(), key=lambda x: x["version"], reverse=True)
    _clients_cache = (time.time(), result)
    return result


def _wcferry_info() -> dict[str, Any]:
    spec = importlib.util.find_spec("wcferry")
    if spec is None:
        return {"installed": False, "version": ""}
    version = ""
    try:
        from importlib.metadata import version as pkg_version

        version = pkg_version("wcferry")
    except Exception:
        pass
    return {"installed": True, "version": version}


_wx4_db_mod: Any = None
_wx4_media_mod: Any = None


def _load_wx4_module(filename: str, module_name: str) -> Any:
    """按文件加载 wechatauto 的子模块，绕过包 __init__ 的可选依赖。"""
    import sys as _sys
    import types as _types

    pkg = _sys.modules.get("wechatauto")
    paths = list(getattr(pkg, "__path__", None) or []) if pkg is not None else []
    if not paths:
        # 依赖装齐的环境里包 __init__ 能完整导入：优先走正规导入，
        # 绝不往 sys.modules 注入 stub——stub 会把后续 `from wechatauto
        # import WeChat` 之类的完整导入卡死（报 unknown location）。
        try:
            return importlib.import_module(f"wechatauto.{filename[:-3]}")
        except Exception:
            pass
        try:
            spec = importlib.util.find_spec("wechatauto")
        except (ImportError, ValueError):
            return None
        if spec is None or not spec.submodule_search_locations:
            return None
        pkg_dir = Path(list(spec.submodule_search_locations)[0])
        stub = _types.ModuleType("wechatauto")
        stub.__path__ = [str(pkg_dir)]
        _sys.modules["wechatauto"] = stub
    else:
        pkg_dir = Path(paths[0])

    target = pkg_dir / filename
    if not target.exists():
        return None

    mod_spec = importlib.util.spec_from_file_location(
        f"wechatauto.{filename[:-3]}", target
    )
    if mod_spec is None or mod_spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(mod_spec)
    # 必须带上包上下文：库里的子模块有**函数内**延迟相对导入（media.py 第 280 行
    # `from . import db as _dbmod`、第 1168 行 `from .moment import _send_scroll`、
    # 第 2016 行 `from .wx import WeChat`）。spec 用无包名的模块名建出来时
    # ``__spec__.parent`` 是空串，载入这一步毫无异常，直到真去解一张图才在运行时抛
    # 「attempted relative import with no known parent package」——实测一天 104 次
    # 「读取图片失败」，入站图片全部读不出来，机器人只能对着图片盲回。
    # sys.modules["wechatauto"] 在上面两条分支里必定已就位（真包或只带 __path__ 的桩），
    # 相对导入按包名解析到同一份源码。
    try:
        mod_spec.loader.exec_module(mod)
    except Exception:
        return None
    # 执行完再还原成独立模块名（wechatauto_db / wechatauto_media），也绝不把
    # ``wechatauto.media`` 登记进 sys.modules：朋友圈的 `_import_wechatauto()` 一发现
    # sys.modules["wechatauto"] 是 stub，就把所有 ``wechatauto.*`` 整体逐出再导真包；
    # 这份缓存挂的是独立名，不会被它连坐删掉（见 moments.py 该函数的注释）。
    # 名必须在 exec 之后改：SourceFileLoader 会校验模块名，先改就报
    # 「loader for wechatauto.media cannot handle wechatauto_media」。
    mod.__name__ = module_name
    return mod


def wx4_media_module() -> Any:
    """加载 media.py（图片/语音/视频解密），不触发包 __init__。"""
    global _wx4_media_mod
    if _wx4_media_mod is not None:
        return _wx4_media_mod
    mod = _load_wx4_module("media.py", "wechatauto_media")
    if mod is None:
        return None
    _patch_wxgf_to_jpg(mod)
    _wx4_media_mod = mod
    return mod


def wx4_db_module() -> Any:
    """加载 wechatauto 的 db.py，绕过包 __init__。

    包 __init__ 会强制导入 UIA/OCR 等可选组件（新 Python 下常装不全），
    而 4.x 驱动实际只用 db.py。做法：先向 sys.modules 注入只带 __path__
    的 stub 包占位，使 db.py 里的 `from wechatauto.logger import wxlog`
    直接按路径加载 logger 子模块，不触发 __init__。加载失败返回 None。
    """
    global _wx4_db_mod
    if _wx4_db_mod is not None:
        return _wx4_db_mod
    mod = _load_wx4_module("db.py", "wechatauto_db")
    if mod is None:
        return None
    _patch_get_self_info(mod)
    _patch_cfg_dword(mod)
    _wx4_db_mod = mod
    return mod


def _patch_get_self_info(mod: Any) -> None:
    """修复 1.2.7 get_self_info 泄漏 sqlite 连接的缺陷。

    它打开 contact.db 解密缓存后从不 close；Windows 下残留句柄会长期占用
    目标文件，导致后续重建时 os.replace 报 WinError 5（拒绝访问）。
    """
    cls = getattr(mod, "WeChatDB", None)
    if cls is None or getattr(cls, "_wx4_patched_ssi", False):
        return

    def get_self_info(self) -> dict:
        for rel, path, _ in self._db_files:
            if os.path.basename(path) != "contact.db":
                continue
            conn = self._open(rel)
            try:
                row = conn.execute(
                    "SELECT username, nick_name, remark FROM contact WHERE username=? LIMIT 1",
                    (self.wxid,),
                ).fetchone()
                if row:
                    return {"username": row[0], "nick_name": row[1], "remark": row[2]}
            finally:
                conn.close()
        return {"username": self.wxid, "nick_name": "", "remark": ""}

    cls.get_self_info = get_self_info
    cls._wx4_patched_ssi = True


def _patch_cfg_dword(mod: Any) -> None:
    """修复多账号同时登录时 cfgDword 取到**别的账号**的缺陷。

    ``WeChatDB.extract_master_key()`` 按 `_find_weixin_pids()` 的顺序取第一个
    能读出 cfg 的 Weixin.exe。多开（本机实测两个账号同时在线，10 个 Weixin.exe）
    时先命中的往往是另一个账号的进程，于是 `self.cfg_dword` 是账号 A 的，
    `self.wxid` 却是账号 B 的。图片 AES 密钥 = md5(str(cfgDword) + wxid)[:16]，
    两者不同源就永远验不过：每张图都掉进 120 秒内存扫描、每张图都 404。
    （实测同一张 V2 图片：A 的 dword + B 的 wxid → 解不出 JPEG；
    B 自己的 dword + B 的 wxid → 一次命中。）

    改成优先取 cfg 里 wxid 与本账号一致的那个进程，一个都对不上才退回旧行为，
    单账号/老版本完全不受影响。cfg 里的 wxid 与本账号对不上时，连主密钥也是
    另一个账号的，这里一并纠正。
    """
    cls = getattr(mod, "WeChatDB", None)
    extract_one = getattr(mod, "extract_master_key_from_cfg", None)
    if cls is None or extract_one is None:
        return
    if getattr(cls, "_wx4_patched_cfgdword", False):
        return

    def extract_master_key(self) -> Any:
        wanted = str(getattr(self, "wxid", "") or "")
        fallback = None
        try:
            pids = self._find_weixin_pids()
        except Exception:
            pids = []
        for pid in pids:
            try:
                got = extract_one(pid)
            except Exception:
                continue
            if not got:
                continue
            if wanted and got[2] == wanted:
                return got
            if fallback is None:
                fallback = got
        return fallback

    cls.extract_master_key = extract_master_key
    cls._wx4_patched_cfgdword = True


# 一次 wxgf 转码最多解这么多帧：静图只有 1~2 帧，动画表情也就几帧，
# 上限只是防住极端长序列把 30s 超时和临时目录撑爆。
WXGF_MAX_FRAMES = 12


def pick_wxgf_frame(frames: list[tuple[str, int]]) -> str | None:
    """从多帧转码产物里挑一张：字节数最大的那帧（并列取靠前的）。

    微信 4.x 把一张静图编成两帧——先一帧 gray 空白底（IDR 不到 100 字节），
    真图在第二帧（yuvj420p，十几 KB 码率）。空白帧全图同色，JPEG 压不出体积，
    所以"最大的一帧"就是"有内容的那一帧"。并列取靠前是为了动画表情仍交首帧。
    """
    best: tuple[int, str] | None = None
    for path, size in frames:
        if best is None or size > best[0]:
            best = (size, path)
    return None if best is None else best[1]


def _patch_wxgf_to_jpg(mod: Any) -> None:
    """修复 1.2.7 的 wxgf 转码只交第一帧（识图因此看到全白）。

    上游 media.py 把 ffmpeg 写死成 `-frames:v 1`。上面那两帧里第一帧是空白底，
    于是解密落盘的 jpg 整幅 (255,255,255)：管理后台的图预览是个白块，模型只能
    回「The image is blank white」。实测本机 152 张已解密图里 12 张中此雷
    （约 8%，全是走 wxgf 分支的 .dat）。改成解码全部帧后按 :func:`pick_wxgf_frame`
    选帧。认不出帧时照旧返回 None，让上层落 .wxgf 兜底，不拿空白冒充成果。
    """
    cls = getattr(mod, "MediaDownloader", None)
    if cls is None or getattr(cls, "_wx4_patched_wxgf", False):
        return

    def wxgf_to_jpg(self, data: bytes) -> Any:
        exe = self._ffmpeg_exe()
        if exe is None:
            return None
        hevc = self._extract_hevc(data)
        if not hevc:
            return None
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.hevc")
            try:
                with open(src, "wb") as f:
                    f.write(hevc)
                r = subprocess.run(
                    [
                        exe, "-y", "-v", "error",
                        "-f", "hevc", "-i", src,
                        "-frames:v", str(WXGF_MAX_FRAMES),
                        os.path.join(td, "f%03d.jpg"),
                    ],
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                return None
            if r.returncode != 0:
                return None
            frames: list[tuple[str, int]] = []
            for name in sorted(os.listdir(td)):  # 文件名即帧序，排序＝按播放顺序
                if not (name.startswith("f") and name.endswith(".jpg")):
                    continue
                p = os.path.join(td, name)
                try:
                    frames.append((p, os.path.getsize(p)))
                except OSError:
                    continue
            pick = pick_wxgf_frame(frames)
            if not pick:
                return None
            try:
                with open(pick, "rb") as f:
                    out = f.read()
            except OSError:
                return None
            return out if out[:3] == b"\xff\xd8\xff" else None

    cls._wxgf_to_jpg = wxgf_to_jpg
    cls._wx4_patched_wxgf = True


def _wx4_available() -> bool:
    """微信 4.x 数据库读取依赖是否可用。"""
    try:
        return wx4_db_module() is not None
    except Exception:
        return False


# ---------------------------------------------------------------- 微信 4.x 数据目录
#
# 微信 4.x 跟随系统「文档」已知文件夹保存 xwechat_files（客户端配置里写的是
# MyDocument:）。开启 OneDrive 文档备份后该文件夹会被重定向到
# C:\Users\<name>\OneDrive\文档，而 %USERPROFILE%\Documents 下可能还残留一份
# 过期副本。wechatauto 的自动探测只看 %USERPROFILE%\Documents，会选中过期副本：
# 库文件已换成新 salt，缓存密钥一个都验不过，启动时报「以下数据库未能取得密钥」。
#
# 这里统一按「微信真正在写的目录」选：先解析已知文件夹，再比较 .db 最新修改时间。

WX4_DB_SUBDIR = "db_storage"


def _documents_known_folder() -> Path | None:
    """系统「文档」已知文件夹的真实路径（OneDrive 重定向后也正确）。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL=5；SHGetFolderPathW 返回重定向后的真实路径
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            path = Path(buf.value)
            if path.is_dir():
                return path
    except Exception:
        pass
    return None


def _wx4_account_dirs(db_dir: str) -> list[str]:
    """db_dir 下所有含 db_storage 的账号目录（不依赖 wechatauto 内部函数，便于测试）。"""
    try:
        names = os.listdir(db_dir)
    except OSError:
        return []
    return sorted(
        os.path.join(db_dir, name)
        for name in names
        if os.path.isdir(os.path.join(db_dir, name, WX4_DB_SUBDIR))
    )


def _wx4_account_newest(account_dir: str) -> float:
    """账号目录里 .db 文件的最新修改时间（-wal/-shm 不算）。"""
    newest = 0.0
    for root, _dirs, files in os.walk(os.path.join(account_dir, WX4_DB_SUBDIR)):
        for name in files:
            if not name.endswith(".db") or name.endswith(("-wal", "-shm")):
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    return newest


def _normalize_wx4_db_dir(path: str) -> str:
    """把候选路径规整成账号目录的父目录（兼容直接传 xwechat_files 或它的上级）。"""
    raw = str(path or "").strip()
    if not raw or not os.path.isdir(raw):
        return ""
    if _wx4_account_dirs(raw):
        return raw
    sub = os.path.join(raw, "xwechat_files")
    if os.path.isdir(sub) and _wx4_account_dirs(sub):
        return sub
    return ""


def _select_wx4_db_dir(candidates: list[str], prefer_account: str = "") -> str:
    """从候选目录里选出微信真正在用的数据目录。

    优先用户指定的账号所在的那份（换过数据目录后同名账号可能有多份副本），
    否则比较各份 .db 的最新修改时间（微信正在写的目录一定比旧备份新）。
    """
    normalized: list[str] = []
    for cand in candidates:
        db_dir = _normalize_wx4_db_dir(cand)
        if db_dir and db_dir not in normalized:
            normalized.append(db_dir)
    if not normalized:
        return ""
    if prefer_account:
        # 同名账号可能在过期副本和活跃目录里各有一份，取 .db 更新的那份
        pinned = [
            db_dir
            for db_dir in normalized
            if os.path.isdir(os.path.join(db_dir, prefer_account, WX4_DB_SUBDIR))
        ]
        if pinned:
            return max(
                pinned,
                key=lambda d: _wx4_account_newest(os.path.join(d, prefer_account)),
            )
    best = ""
    best_key = (0.0, 0)
    for db_dir in normalized:
        accounts = _wx4_account_dirs(db_dir)
        if not accounts:
            continue
        key = (max(_wx4_account_newest(a) for a in accounts), len(accounts))
        if key > best_key:
            best, best_key = db_dir, key
    return best


def _wx4_candidate_roots() -> list[str]:
    """所有可能的微信 4.x 数据目录来源（含被 OneDrive 重定向的「文档」）。"""
    roots: list[str] = []

    def add(path: Any) -> None:
        text = str(path or "").strip()
        if text and text not in roots:
            roots.append(text)

    mod = wx4_db_module()
    if mod is not None:
        try:
            add(mod.auto_detect_db_dir())
        except Exception:
            pass
    docs = _documents_known_folder()
    if docs is not None:
        add(docs / "xwechat_files")
        add(docs)
    up = os.environ.get("USERPROFILE", "")
    if up:
        add(Path(up) / "Documents" / "xwechat_files")
        add(Path(up) / "Documents")
    onedrive = os.environ.get("OneDrive", "")
    if onedrive:
        add(Path(onedrive) / "xwechat_files")
        for name in ("文档", "Documents"):
            add(Path(onedrive) / name / "xwechat_files")
    return roots


def resolve_wx4_db_dir(prefer_account: str = "") -> str:
    """定位微信 4.x 当前使用的数据目录（db_storage 的父目录）。"""
    try:
        return _select_wx4_db_dir(_wx4_candidate_roots(), prefer_account)
    except Exception:
        return ""


def _select_wx4_account(account_dirs: list[str], wanted_wxids: set[str]) -> str:
    """在账号目录里挑出 wanted_wxids 中的账号（多个时取最近活跃的）。"""
    for account_dir in sorted(account_dirs, key=_wx4_account_newest, reverse=True):
        name = os.path.basename(account_dir)
        if re.sub(r"_\w{4}$", "", name) in wanted_wxids:
            return name
    return ""


def resolve_wx4_account(db_dir: str, prefer: str = "") -> str:
    """自动选择要绑定的微信账号。

    wechatauto 默认选最近写库的账号；双开时后台账号也在写库，但前台 RPA
    找不到它的窗口。这里优先选有可见微信窗口的账号，找不到再交给库自选。
    """
    if not db_dir or prefer:
        return prefer
    mod = wx4_db_module()
    if mod is None:
        return ""
    visible = {int(w["pid"]) for w in list_windows(force=True)}
    wanted: set[str] = set()
    for pid in visible:
        try:
            got = mod.extract_master_key_from_cfg(pid)
        except Exception:
            continue
        if got and got[2]:
            wanted.add(str(got[2]))
    if not wanted:
        return ""
    return _select_wx4_account(_wx4_account_dirs(db_dir), wanted)


def _wx4_running_wxids(mod: Any) -> set[str]:
    """运行中的 Weixin.exe 各自对应的 wxid（在线判定）。"""
    online: set[str] = set()
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return online
    pids: list[int] = []
    for line in (r.stdout or "").strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    for pid in pids:
        try:
            got = mod.extract_master_key_from_cfg(pid)
        except Exception:
            continue
        if got and got[2]:
            online.add(got[2])
    return online


# WeChatDB 构造/解密互斥：同一账号的解密缓存（.tmp→os.replace）并发会
# 因文件句柄占用报 WinError 5，枚举与通道启动必须串行
_wx4_db_lock = threading.Lock()


def list_weixin4_accounts(running: dict[str, str] | None = None) -> dict[str, Any]:
    """枚举本机微信 4.x 可选账号：昵称 + 微信号 + 在线状态。

    running: {account_dir: 昵称}，已连接通道的账号直接复用，不再构造
             WeChatDB（避免与运行中的驱动解密同一缓存冲突）。
    昵称来自解密 contact.db（读取 keys.json 缓存，命中则秒级）；
    缓存都没有的目录只列基本信息，不触发内存扫描。
    """
    running = running or {}
    mod = wx4_db_module()
    if mod is None:
        return {
            "ok": False,
            "error": "未安装读取依赖：pip install --no-deps wechatauto-replica cryptography colorama",
            "accounts": [],
        }
    # 与驱动用同一套目录解析：只在微信真正在写的那份 xwechat_files 里枚举，
    # 避免把 OneDrive 迁移前的过期副本账号也列出来（那副本的密钥不可用）
    db_dir = resolve_wx4_db_dir()
    try:
        dirs = mod.list_accounts(db_dir or None)
    except Exception as exc:
        return {"ok": False, "error": f"枚举账号目录失败：{exc}", "accounts": []}

    online = _wx4_running_wxids(mod)
    accounts: list[dict[str, Any]] = []
    for item in dirs:
        account = item["account"]
        wxid = item["wxid"]
        nickname = running.get(account, "")
        readable = bool(nickname)
        keys_file = os.path.join(tempfile.gettempdir(), "wechatauto_db", account, "keys.json")
        if not readable and os.path.exists(keys_file):
            try:
                with _wx4_db_lock:
                    wdb = mod.WeChatDB(db_dir=db_dir or None, account=account)
                    info = wdb.get_self_info()
                nickname = str(info.get("nick_name") or "")
                readable = True
            except Exception:
                readable = False
        accounts.append(
            {
                "account_dir": account,
                "wxid": wxid,
                "nickname": nickname,
                "online": wxid in online,
                "readable": readable,
                "last_activity": float(item.get("last_activity") or 0),
                "path": str(item.get("path") or ""),
            }
        )
    accounts.sort(key=lambda x: (not x["online"], -x["last_activity"]))
    return {"ok": True, "accounts": accounts, "db_dir": db_dir}


def diagnose(cfg: dict[str, Any], driver_status: dict[str, Any] | None = None) -> dict[str, Any]:
    clients = detect_clients()
    hookable = [c for c in clients if c["version"].startswith("3.9.")]
    latest = clients[0] if clients else None
    wcf = _wcferry_info()

    checks: list[dict[str, Any]] = []

    if not clients:
        checks.append(
            {
                "key": "client",
                "label": "微信客户端",
                "status": "error",
                "value": "未检测到",
                "hint": "安装微信 PC 版后才能接管账号。",
            }
        )
    else:
        v = latest["version"] or "未知"
        has4 = any(c["family"] == "4.x" for c in clients)
        wx4_ok = _wx4_available()
        if hookable:
            status, value, hint = "ok", f"{v}（3.9.x 可 Hook）", ""
        elif has4 and wx4_ok:
            status = "ok"
            value = f"{v}（4.x 数据库读取）"
            hint = "后台可收消息；发送需 Hook 注入，当前驱动只读。"
        elif has4:
            status = "warn"
            value = f"{v}（4.x，缺读取依赖）"
            hint = "pip install wechatauto-replica uiautomation pywin32 pyperclip psutil colorama"
        else:
            status, value, hint = "error", f"{v}（不支持）", "安装 3.9.10.27（wcferry）或 4.x（数据库读取）。"
        checks.append(
            {
                "key": "client",
                "label": "微信客户端",
                "status": status,
                "value": value,
                "hint": hint,
            }
        )

    checks.append(
        {
            "key": "wcferry",
            "label": "wcferry 运行时",
            "status": "ok" if wcf["installed"] else "warn",
            "value": wcf["version"] or ("已安装" if wcf["installed"] else "未安装"),
            "hint": "" if wcf["installed"] else "pip install wcferry（建议 Python 3.10-3.12 环境）",
        }
    )

    checks.append(
        {
            "key": "python",
            "label": "Python 版本",
            "status": "ok" if sys.version_info < (3, 13) else "warn",
            "value": sys.version.split()[0],
            "hint": "" if sys.version_info < (3, 13) else "wcferry 对 3.13 支持有限，建议 3.11/3.12。",
        }
    )

    ai = cfg["ai"]
    _prof = next(
        (p for p in ai.get("profiles") or [] if p.get("id") == ai.get("active_profile")), {}
    )
    _prof_name = (_prof.get("name") or ai.get("active_profile") or "").strip()
    checks.append(
        {
            "key": "ai",
            "label": "AI 模型",
            "status": "ok" if ai.get("api_key") else "warn",
            "value": " · ".join(x for x in (_prof_name, ai.get("model", "")) if x),
            "hint": (
                ""
                if ai.get("api_key")
                else f"在「设置 → 模型」的「{_prof_name or '当前档'}」里填入 API Key。"
            ),
        }
    )

    if driver_status:
        checks.append(
            {
                "key": "driver",
                "label": "当前驱动",
                "status": "ok" if driver_status.get("connected") else "warn",
                "value": driver_status.get("display_name", driver_status.get("driver", "")),
                "hint": driver_status.get("error", ""),
            }
        )

    return {
        "checks": checks,
        "clients": clients,
        "hookable": bool(hookable),
        "recommend": (
            "wcferry"
            if hookable and wcf["installed"]
            else ("weixin4" if any(c["family"] == "4.x" for c in clients) and _wx4_available() else "")
        ),
    }
