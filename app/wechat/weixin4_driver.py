"""微信 4.x 驱动：解密本地 SQLCipher 数据库，后台轮询接收消息。

能力：后台接收私聊/群聊消息、会话列表、历史消息（数据库读取，不依赖窗口）。

发送走 wechatauto 的 UIA 引擎：**微信左上角搜索栏搜对方昵称/备注 → 打开会话
→ 校验当前会话确实是目标 → 粘贴正文 → 回车**。发送前会回读输入框内容确认
落笔；目标会话校验不过直接报错，绝不再把消息留在当前打开的会话里。
表情/文件把文件放进剪贴板（CF_HDROP）粘贴，由微信按类型自动识别为
动画表情 / 图片消息 / 文件卡片。

依赖：wechatauto-replica（数据库读取 + UIA 发送）、cryptography、colorama。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from . import ui_gate
from .base import (
    DriverError,
    Message,
    WeChatDriver,
    format_pat_notice,
    is_internal_chat,
    parse_pat_notice,
    strip_sender_prefix,
)

log = logging.getLogger("weixin4")

_u = ctypes.windll.user32

# 剪贴板文件粘贴（CF_HDROP）：GlobalAlloc 的句柄按 Win32 互操作约定只取低 32 位，
# 但 GlobalLock 返回的是真实 64 位指针，必须声明原型避免被截断
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002

_k = ctypes.windll.kernel32
_k.GlobalAlloc.restype = w.HGLOBAL
_k.GlobalAlloc.argtypes = (w.UINT, ctypes.c_size_t)
_k.GlobalLock.restype = ctypes.c_void_p
_k.GlobalLock.argtypes = (w.HGLOBAL,)
_k.GlobalUnlock.argtypes = (w.HGLOBAL,)
_k.GlobalFree.restype = w.HGLOBAL
_k.GlobalFree.argtypes = (w.HGLOBAL,)
_u.SetClipboardData.restype = w.HANDLE
_u.SetClipboardData.argtypes = (w.UINT, w.HANDLE)

# 前台窗口恢复：HWND 是 64 位，不声明原型会被 ctypes 默认的 c_int 截断
_u.GetForegroundWindow.restype = w.HWND
_u.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
_u.GetWindowThreadProcessId.restype = w.DWORD
_u.IsWindow.argtypes = (w.HWND,)
_u.IsWindow.restype = w.BOOL
_u.IsWindowVisible.argtypes = (w.HWND,)
_u.IsWindowVisible.restype = w.BOOL
_u.IsIconic.argtypes = (w.HWND,)
_u.IsIconic.restype = w.BOOL
_u.SetForegroundWindow.argtypes = (w.HWND,)
_u.SetForegroundWindow.restype = w.BOOL
_u.BringWindowToTop.argtypes = (w.HWND,)
_u.BringWindowToTop.restype = w.BOOL
_u.AttachThreadInput.argtypes = (w.DWORD, w.DWORD, w.BOOL)
_u.AttachThreadInput.restype = w.BOOL
_k.GetCurrentThreadId.restype = w.DWORD


class _DROPFILES(ctypes.Structure):
    """CF_HDROP 数据头（Win32 DROPFILES 结构）。"""

    _fields_ = [
        ("pFiles", w.DWORD),
        ("pt", w.POINT),
        ("fNC", w.BOOL),
        ("fWide", w.BOOL),
    ]


def hdrop_blob(paths: list[str]) -> bytes:
    """构造 CF_HDROP 剪贴板数据：DROPFILES 头 + UTF-16 双零结尾的路径列表。"""
    header = _DROPFILES(pFiles=ctypes.sizeof(_DROPFILES), fWide=True)
    files = "\0".join(str(p) for p in paths) + "\0\0"
    return bytes(header) + files.encode("utf-16-le")

_uia_cls: Any = None


def _uia_class() -> Any:
    """延迟加载 UIA 发送引擎；搜索词按字面值使用，不去碰库内自动 DB 解析。

    wechatauto 的 ``open_chat`` 默认会自建 ``WeChatDB`` 把 wxid 映射成昵称/备注，
    多开会挑到另一个账号的库（每次多花好几秒、还可能报密钥错误）。本驱动的
    ``_chats`` 已经来自正确的解密库，直接把名字交给搜索框即可。
    """
    global _uia_cls
    if _uia_cls is not None:
        return _uia_cls
    try:
        from wechatauto.uia_driver import MAIN_CLASSES, WeChatUIA
    except Exception as exc:  # noqa: BLE001 - 依赖缺失时给可读报错
        raise DriverError(f"缺少微信 4.x 发送依赖 wechatauto-replica（UIA 引擎）：{exc}") from exc

    class _NamedSearchUIA(WeChatUIA):
        """钉住指定账号主窗口的 UIA 引擎。

        必须把 hwnd 传进库的 ``__init__``：库据此记录 ``_bound_pid``，
        ``_candidate_hwnds()`` 才会认这个窗口、gate 归零后才会去热激活。
        只给 ``self.hwnd`` 赋值是不够的——``_bound_pid`` 为空会让
        ``_candidate_hwnds()`` 直接返回空，``ensure_window()`` 静默返回 False
        （实测微信重启后 gate 归零时 100% 报「控件树不可用」，且没有任何热激活日志）。
        旧版库若不收 hwnd 参数，这里退回手工补 ``_bound_pid``。

        ``_find_main`` 额外只认这个句柄，多开时绝不退回「随便哪个微信窗口」；
        句柄失效（微信重启）时返回 None，由上层重建引擎重定位。
        """

        hwnd = 0

        def __init__(self, hwnd: int = 0, **kwargs: Any) -> None:
            pinned = int(hwnd or 0)
            try:
                super().__init__(hwnd=pinned, **kwargs)
            except TypeError:  # 兼容不收 hwnd 参数的旧版库
                super().__init__(**kwargs)
            self.hwnd = pinned
            if pinned and not getattr(self, "_bound_pid", None):
                self._bound_pid = self._pid_from_hwnd(pinned)

        @staticmethod
        def _resolve_search_keyword(keyword: str) -> list[str]:
            return [str(keyword)]

        def _find_main(self):
            if self.hwnd:
                ctrl = self._anchor(self.hwnd)
                if ctrl is not None and (ctrl.ClassName or "") in MAIN_CLASSES:
                    return ctrl
                return None
            return super()._find_main()

        def _find_search_list(self, timeout: float = 3.0):
            """只在微信自己的窗口子树里找搜索下拉列表，替掉库里的桌面全树遍历。

            库的实现是 ``auto.ListControl(searchDepth=0xFFFFFFFF)`` 再从
            ``auto.GetRootControl()`` 做一轮 max_depth=25 的 DFS——两次都要遍历
            桌面上**所有进程**的 UIA 树。本机实测单轮 6.6s（4282 节点，最慢一个
            节点 524ms）+ 8.8s 且必然落空，而这条路径每条消息都要付一遍；被扫到
            的应用（含微信自己）都得在自己的 UI 线程上应答这些跨进程查询，表现就是
            「机器人一发送，微信点不动」。
            没命中就照实返回 None（发送会以「找不到搜索列表」失败），绝不因为
            「这次没找到」退回桌面全扫；只有库内部名字漂移、限定范围这条路走不通时
            才退回上游实现，宁可贵，也不能发不出去。
            """
            hit = _search_list_scoped(self, timeout)
            if hit is _SCOPE_UNAVAILABLE:
                return super()._find_search_list(timeout)
            return hit

    _uia_cls = _NamedSearchUIA
    return _uia_cls


_SCOPE_UNAVAILABLE = object()


def _wechat_top_hwnds(pid: int) -> list[int]:
    """该微信进程的全部可见顶层窗口句柄。

    搜索下拉在部分版本里是独立弹窗而不是主窗口的子控件，只认主窗口会漏；
    按 pid 收顶层窗口是本地 Win32 调用，不涉及跨进程 UIA 遍历。
    """
    out: list[int] = []
    pid = int(pid or 0)
    if not pid:
        return out
    WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(hwnd, _lparam):
        got = w.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value == pid and _u.IsWindowVisible(hwnd):
            out.append(int(hwnd))
        return True

    try:
        _u.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception as exc:  # noqa: BLE001 - 拿不到弹窗就只用主窗口，别把发送搞挂
        log.debug("枚举微信顶层窗口失败（忽略，只用主窗口）：%s", exc)
        return []
    return out


def _scoped_roots(uia: Any) -> list[Any]:
    """限定 UIA 遍历的起点：微信主窗口 + 同进程的其它顶层窗口，绝不含桌面根节点。"""
    roots: list[Any] = []
    win = getattr(uia, "_win", None)
    main_hwnd = int(getattr(uia, "hwnd", 0) or 0)
    if win is None:
        find_main = getattr(uia, "_find_main", None)
        try:
            win = find_main() if callable(find_main) else None
        except Exception:  # noqa: BLE001 - 主窗口定位失败还能靠同进程弹窗继续找
            win = None
    if win is not None:
        roots.append(win)
    pid = int(getattr(uia, "_bound_pid", 0) or 0)
    anchor = getattr(uia, "_anchor", None)
    if pid and callable(anchor):
        for extra in _wechat_top_hwnds(pid):
            if win is not None and extra == main_hwnd:
                continue  # 同一棵树已经走过一遍，不再重复（一次就是几秒）
            try:
                ctrl = anchor(extra)
            except Exception:  # noqa: BLE001 - 单个弹窗拿不到不影响其它根节点
                ctrl = None
            if ctrl is not None:
                roots.append(ctrl)
    return roots


def _search_list_in_roots(
    roots: list[Any], find_by: Any, aid_hit: Any, aids: tuple[str, ...]
) -> Any:
    """在给定根节点（都是微信自己的窗口）里找搜索下拉列表，找不到返回 None。

    遍历与属性判定用库里的 ``_find_by``/``_aid_hit``，保证和上游语义一致；
    差别只在**根节点从哪来**——上游取桌面根，这里只取微信窗口。
    """

    def pred(ctrl: Any) -> bool:
        return bool(
            getattr(ctrl, "ControlTypeName", "") == "ListControl"
            and aid_hit(getattr(ctrl, "AutomationId", ""), aids)
        )

    for root in roots:
        if root is None:
            continue
        hit = find_by(root, pred, max_depth=25)
        if hit is not None:
            return hit
    return None


def _search_list_scoped(uia: Any, timeout: float = 3.0) -> Any:
    """轮询限定范围的搜索列表；返回 ``_SCOPE_UNAVAILABLE`` 表示库内部名字变了。"""
    try:
        from wechatauto.uia_driver import SEARCH_LIST_AIDS, _aid_hit, _find_by
    except Exception as exc:  # noqa: BLE001 - 库升级改名：交回上游实现，调用方会显式记录
        log.debug("未能加载库内搜索辅助函数，本次退回上游实现：%s", exc)
        return _SCOPE_UNAVAILABLE
    deadline = time.monotonic() + max(0.2, float(timeout or 0))
    while True:
        # 每轮重新取根节点：搜索下拉是打字之后才出现的独立弹窗
        hit = _search_list_in_roots(_scoped_roots(uia), _find_by, _aid_hit, SEARCH_LIST_AIDS)
        if hit is not None or time.monotonic() >= deadline:
            return hit
        time.sleep(0.2)


RHYTHM_PROFILE = "fast"


def _apply_rhythm_profile() -> str:
    """把 wechatauto 自带的「拟人节奏」压到 fast 档，返回实际生效的档位名（空串=没设上）。

    这一层在库内独立生效，项目配置看不见它：natural 档要求任意两次界面写入之间再隔
    2.5–6 秒，120 秒内写满 6 次直接睡 30–75 秒，跟本项目 `send.chat_gap_sec` 叠成
    两重节流，状态还落在 ~/.wechatauto/rhythm.json 跨进程共享。发送节奏由本项目统一
    拿主意，这里只保留鼠标轨迹与键击抖动。显式设了 WECHATAUTO_RHYTHM 就尊重它，不覆盖。
    """
    env = (os.environ.get("WECHATAUTO_RHYTHM") or "").strip()
    if env:
        return env
    try:
        from wechatauto import rhythm
    except Exception as exc:  # noqa: BLE001 - 依赖缺失时发送链路自己会给可读报错
        log.warning("未能加载 wechatauto 节奏模块（发送仍按项目自身节流）：%s", exc)
        return ""
    try:
        rhythm.set_profile(RHYTHM_PROFILE)
    except Exception as exc:  # noqa: BLE001
        log.warning("设置拟人节奏档位失败：%s", exc)
        return ""
    log.info("wechatauto 拟人节奏档位已压到 %s（发送间隔由 send.* 决定）", RHYTHM_PROFILE)
    return RHYTHM_PROFILE

TYPE_MAP = {
    "文本": "text",
    "图片": "image",
    "语音": "voice",
    "视频": "video",
    "动画表情": "emoji",
    "位置": "location",
    "文件/链接/卡片": "link",
    "系统消息": "system",
    "拍一拍": "pat",
    "音视频通话": "system",
    "红包": "system",
}
CODE_MAP = {
    1: "text", 3: "image", 34: "voice", 43: "video",
    47: "emoji", 48: "location", 49: "link", 10000: "system",
}


class Weixin4Driver(WeChatDriver):
    name = "weixin4"
    display_name = "微信 4.x（数据库读取）"
    supports_send_file = True
    supports_contacts = True

    def __init__(
        self,
        on_message=None,
        account_dir: str = "",
        interval: float = 1.0,
        warmup_on_start: bool = False,
    ) -> None:
        super().__init__(on_message)
        self.account_dir = account_dir
        self.interval = float(interval)
        # 启动时就预热 UIA 会去强开微信无障碍 gate、物化控件树；默认留到首次发送再做
        self.warmup_on_start = bool(warmup_on_start)
        self.db = None
        self.listener = None
        self._connected = False
        self._self_wxid = ""
        self._self_name = ""
        self._pid = 0
        self._chats: dict[str, str] = {}
        self._senders: dict[int, str] = {}
        self._senders_at = 0.0
        self._media = None
        self._media_lock = threading.Lock()
        # 发送侧：所有 UIA 操作收敛到单一线程（uiautomation 的 COM 对象有线程亲和性，
        # 同时也能避免后台自动回复和控制台发送互相抢窗口）
        self._uia: Any = None
        self._rpa_thread: threading.Thread | None = None
        self._rpa_q: queue.Queue | None = None
        self._rpa_lock = threading.Lock()

    def start(self) -> None:
        from .diagnose import (  # 绕过 wechatauto 包 __init__，直接加载 db.py
            _wx4_db_lock,
            resolve_wx4_account,
            resolve_wx4_db_dir,
            wx4_db_module,
        )

        mod = wx4_db_module()
        if mod is None:
            raise DriverError(
                "未安装微信 4.x 读取依赖。请执行：pip install wechatauto-replica cryptography pyperclip"
                "（wechatauto-replica 需 --no-deps 安装）"
            )
        WeChatDB = mod.WeChatDB
        Listener = mod.Listener
        self._dbmod = mod

        # 微信 4.x 数据目录不一定是 %USERPROFILE%\Documents：开启 OneDrive 文档备份
        # 后「文档」会被重定向到 OneDrive\文档，而旧目录还可能留着过期副本。
        # wechatauto 的自动探测会选错副本（库 salt 已变 → 密钥全部验不过），
        # 这里显式解析微信真正在写的目录，账号优先选有可见窗口的那个。
        db_dir = resolve_wx4_db_dir(prefer_account=self.account_dir)
        account = self.account_dir
        if account and db_dir and not Path(db_dir, account, "db_storage").is_dir():
            raise DriverError(
                f"配置的微信账号目录 {account} 不在 {db_dir} 里。"
                "微信可能升级、迁移或被 OneDrive 重定向；请在控制台重新选择账号目录。"
                "为避免发错号，这里不会自动改绑其他账号。"
            )
        if not account:
            account = resolve_wx4_account(db_dir, prefer="")

        with _wx4_db_lock:  # 与账号枚举串行，避免并发解密同一缓存文件（WinError 5）
            try:
                # 1.2.7 起自动提取/自愈已可用，不再传占位 master_key（会干扰账号选择）；
                # 若仍有库缺密钥，由 _fill_keys 逐进程补扫兜底
                self.db = WeChatDB(db_dir=db_dir or None, account=account or None)
                if self.db.unkeyed:
                    self._fill_keys()
                if self.db.unkeyed:
                    raise DriverError(
                        "以下数据库未能取得密钥：%s。数据目录：%s。"
                        "请确认微信已登录，并用管理员权限运行。"
                        % (", ".join(self.db.unkeyed), self.db.db_dir)
                    )
                info = self.db.get_self_info()
            except DriverError:
                raise
            except Exception as exc:
                raise DriverError(
                    f"读取微信 4.x 数据库失败（数据目录：{db_dir or '自动探测'}）：{exc}"
                ) from exc

        self._self_wxid = info.get("username") or ""
        self._self_name = info.get("nick_name") or ""
        self._locate_pid()
        self._refresh_chats()
        self.listener = Listener(self.db, interval=self.interval)
        self.listener.add_all(self._on_db_message)
        self.listener.start()
        self._connected = True
        log.info(
            "微信 4.x 已连接：%s（账号 %s，数据目录 %s）",
            self._self_name,
            self.db.account,
            self.db.db_dir,
        )
        # 库内那层拟人节奏压到 fast：不然它会与 send.chat_gap_sec 叠加，
        # 发送前再等 2.5–6s、连发几轮直接睡 30–75s
        _apply_rhythm_profile()
        # 预热 UIA 发送引擎：首次要扫描 Weixin.dll 打开无障碍 gate、物化整棵控件树，
        # 约数秒到数十秒。默认不在启动时做——那一刻号主往往正在用微信，而这两件事都会
        # 让微信的 UI 线程去服务跨进程查询，实测就是「一开项目微信点不动」。
        # 代价：第一条回复多等几秒。要恢复旧行为：账号配置 weixin4.warmup_on_start = true。
        if self.warmup_on_start:
            threading.Thread(target=self._warmup, name="weixin4-uia-warmup", daemon=True).start()
        else:
            log.info("微信 UIA 发送通道改为首次发送时预热（启动不预热，避免打开项目就去动微信界面）")

    def stop(self) -> None:
        self._connected = False
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        self._stop_rpa_thread()

    def _fill_keys(self) -> None:
        """逐个微信进程独立扫描 Config.Cipher，补齐库密钥。

        微信 4.x 多开时有多个 Weixin.exe（含渲染子进程），按键表分别扫描，
        共享 tested 集合会漏掉后续进程的候选，因此每个进程独立扫描。
        """
        pids = self.db._find_weixin_pids()
        try:
            import psutil

            pids.sort(key=lambda p: psutil.Process(p).memory_info().rss, reverse=True)
        except Exception:
            pass
        for pid in pids:
            if not self.db.unkeyed:
                break
            try:
                found = self.db._extract_keys_pid(pid, set())
            except Exception as exc:
                log.warning("扫描进程 %s 密钥失败：%s", pid, exc)
                continue
            if found:
                self.db._keys.update(found)
        self.db._save_keys()
        self.db.unkeyed = [
            rel for rel, _, _ in self.db._db_files if not self.db._key_works(rel)
        ]

    def _locate_pid(self) -> None:
        """找到当前账号对应的 Weixin.exe 进程，用于定位窗口。"""
        mod = getattr(self, "_dbmod", None)
        extract = getattr(mod, "extract_master_key_from_cfg", None)
        if extract is None:
            return
        for pid in self.db._find_weixin_pids():
            try:
                got = extract(pid)
            except Exception:
                continue
            if got and got[2] == self._self_wxid:
                self._pid = pid
                return

    def status(self) -> dict[str, Any]:
        db = getattr(self, "db", None)
        return {
            "driver": self.name,
            "display_name": self.display_name,
            "connected": self._connected,
            "account": self._self_name or self._self_wxid,
            "detail": (
                f"数据库：{getattr(db, 'account', '')}（{getattr(db, 'db_dir', '')}）"
                if db
                else ""
            ),
            "error": "" if self._connected else self.last_error,
            "mode": "db-read",
        }

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {"key": "recv", "label": "接收消息", "ok": True},
            {"key": "send_text", "label": "发送文本", "ok": True},
            {"key": "send_file", "label": "发送文件/表情", "ok": True},
            {"key": "contacts", "label": "通讯录", "ok": True},
            {"key": "history", "label": "历史记录检索", "ok": True},
        ]

    # ---------------------------------------------------------------- 发送（UIA 搜索定位）
    _RPA_TIMEOUT = 180.0

    def _chat_name(self, chat_id: str) -> str:
        """会话 id → 微信搜索框能命中的名字（备注/昵称）；查不到原样返回 id。"""
        name = str(self._chats.get(chat_id) or "").strip()
        if not name:
            try:
                name = str(self.resolve_name(chat_id) or "").strip()
            except Exception:  # noqa: BLE001 - 名字解析失败就退回 id，让发送侧报未命中
                name = ""
        return name or str(chat_id)

    def _main_hwnd(self) -> int:
        """当前账号的主窗口句柄；pid 失效（微信重启）时重新定位一次。"""
        if not getattr(self, "_pid", 0) and self.db is not None:
            self._locate_pid()
        hwnd = self._find_window()
        if not hwnd and self.db is not None:
            self._locate_pid()
            hwnd = self._find_window()
        return int(hwnd or 0)

    def _ensure_uia(self) -> Any:
        """拿到可用的 UIA 引擎（只在发送线程里调用）。

        微信重启/升级后无障碍 gate 会归零、旧引擎的窗口绑定也会失效：先重建引擎，
        ``ensure_window()`` 失败再显式补一轮热激活重试；微信窗口刚重启还没起来时
        等几秒再找。全部失败才抛错（绝不静默继续，避免发到错误会话）。
        """
        engine = self._uia
        if engine is not None:
            try:
                if engine.ensure_window():
                    return engine
            except Exception:  # noqa: BLE001 - 引擎绑定失效，重建
                pass
            self._uia = None
        last_error = ""
        for attempt in range(1, 4):
            hwnd = self._main_hwnd()
            if not hwnd:
                if attempt < 3:
                    time.sleep(1.5)  # 微信刚重启时窗口可能还没建出来
                    continue
                raise DriverError(
                    "未找到当前账号的微信主窗口（微信可能正在重启或还没登录），请稍后重试"
                )
            engine = None
            try:
                engine = _uia_class()(hwnd=hwnd)
                ok = engine.ensure_window()
                if not ok:
                    # ensure_window 内部会热激活；这里再显式补一轮并等树物化，
                    # 兜住「微信刚重启、gate 归零、窗口还没渲染完」的瞬时状态
                    ok = bool(engine.ensure_materialized(force=False, timeout=20)) and bool(
                        engine.ensure_window()
                    )
            except DriverError:
                raise
            except Exception as exc:  # noqa: BLE001
                ok = False
                last_error = f"{type(exc).__name__}: {exc}"
            if ok:
                self._uia = engine
                return engine
            log.warning(
                "微信 UIA 引擎不可用（第 %d/3 次）：hwnd=%s pid=%s bound_pid=%s err=%s",
                attempt,
                hwnd,
                getattr(self, "_pid", 0),
                getattr(engine, "_bound_pid", None) if engine is not None else None,
                last_error or "ensure_window=False",
            )
            if attempt < 3:
                time.sleep(1.5)
        raise DriverError(
            "微信控件树不可用（无障碍 gate 激活失败，已重试 3 次）。为避免把消息发到"
            "错误会话，本次已取消发送；请确认微信窗口可见/已登录，稍后重试或重启微信"
        )

    def _open_target(self, uia: Any, name: str) -> None:
        """在微信搜索栏搜索对方昵称/备注并打开会话，确认切到目标后才允许发送。"""
        if not uia.ensure_window():
            raise DriverError("微信窗口不可用（可能已关闭或最小化）")
        try:
            current = str(uia.current_chat() or "")
        except Exception:  # noqa: BLE001 - 读不到当前会话就先走打开流程
            current = ""
        if current == name:
            return
        if not uia.open_chat(name):
            raise DriverError(f"在搜索栏里没找到会话「{name}」，已取消发送")
        try:
            current = str(uia.current_chat() or "")
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"无法确认当前会话（{exc}），已取消发送") from exc
        if current != name:
            raise DriverError(
                f"会话校验失败：目标是「{name}」，实际打开的是「{current}」，已取消发送"
            )

    def _send_text_rpa(self, name: str, text: str) -> None:
        uia = self._ensure_uia()
        self._open_target(uia, name)
        if not uia.send_text(text):
            raise DriverError(
                f"发送失败：内容没有确认写进「{name}」的输入框（未回车发送）"
            )

    def _send_file_rpa(self, name: str, path: str) -> None:
        uia = self._ensure_uia()
        self._open_target(uia, name)
        try:
            ctrl = uia._chat_input()
        except Exception:  # noqa: BLE001
            ctrl = None
        if ctrl is None:
            raise DriverError("找不到聊天输入框，已取消发送")
        focused = False
        try:
            focused = bool(uia._click_ctrl(ctrl))
        except Exception:  # noqa: BLE001 - UIA 点击失败时退回坐标点击
            focused = False
        if not focused:
            try:
                rect = ctrl.BoundingRectangle
                self._click(
                    (int(rect.left) + int(rect.right)) // 2,
                    (int(rect.top) + int(rect.bottom)) // 2,
                )
            except Exception as exc:  # noqa: BLE001
                raise DriverError(f"无法聚焦聊天输入框：{exc}") from exc
        time.sleep(0.15)
        hwnd = int(getattr(uia, "hwnd", 0) or 0)
        if hwnd and _u.GetForegroundWindow() != hwnd and not uia.ensure_window():
            raise DriverError("微信没有抢到前台，已取消发送")
        self._copy_hdrop([path])
        time.sleep(0.05)
        self._ctrl_v()
        time.sleep(0.9)     # 等微信构建文件/表情预览
        self._key(0x0D)     # 回车发送
        time.sleep(0.35)

    def _warmup(self) -> None:
        # 应用启动与微信重启经常并发：UIA gate 要等窗口就绪才能激活，失败就隔几秒再试，
        # 别把「微信还没起来」当成永久不可用
        for attempt in range(1, 4):
            try:
                self._rpa_call(self._warmup_uia, timeout=300)
                return
            except Exception as exc:  # noqa: BLE001 - 预热失败不影响收消息
                if attempt < 3:
                    log.warning(
                        "微信 UIA 发送通道预热失败（第 %d/3 次，5s 后重试）：%s", attempt, exc
                    )
                    time.sleep(5)
                else:
                    log.warning("微信 UIA 发送通道预热失败（首次发送时会再试）：%s", exc)

    def _warmup_uia(self) -> None:
        uia = self._ensure_uia()
        log.info("微信 UIA 发送通道就绪：当前会话 %s", uia.current_chat())

    def _ensure_rpa_thread(self) -> None:
        with self._rpa_lock:
            if self._rpa_thread is not None and self._rpa_thread.is_alive():
                return
            self._rpa_q = queue.Queue()
            self._rpa_thread = threading.Thread(
                target=self._rpa_loop,
                name=f"weixin4-rpa-{self.account_dir or self._self_wxid or 'acc'}",
                daemon=True,
            )
            self._rpa_thread.start()

    def _rpa_loop(self) -> None:
        """所有 UIA 操作串行在这一条线程上执行（COM 线程亲和 + 不抢窗口）。

        串行只解决了「发送自己不打自己」：朋友圈点赞/评论跑在另一条线程上，
        照样能在滚动中途点掉导航栏。所以每个操作还要占住 ui_gate.UI_LOCK，
        与朋友圈互动共用同一把界面锁。
        """
        q = self._rpa_q
        assert q is not None
        while True:
            item = q.get()
            if item is None:
                break
            fn, args, box = item
            try:
                with ui_gate.hold():
                    # 发送会把微信顶到前台（UIA 写入/点击都会 activateWindow）；
                    # 任务结束把原来的前台窗口还回去，用户本来就在微信里则不动。
                    prev = self._foreground_hwnd()
                    try:
                        box["value"] = fn(*args)
                    finally:
                        self._restore_foreground(prev)
            except BaseException as exc:  # noqa: BLE001 - 原样抛回调用线程
                box["error"] = exc
            finally:
                box["done"].set()

    def _rpa_call(self, fn: Any, *args: Any, timeout: float | None = None) -> Any:
        self._ensure_rpa_thread()
        q = self._rpa_q
        if q is None:  # 已被 stop() 拆掉
            raise DriverError("微信发送线程未启动")
        box: dict[str, Any] = {"done": threading.Event(), "value": None, "error": None}
        q.put((fn, args, box))
        if not box["done"].wait(timeout or self._RPA_TIMEOUT):
            raise DriverError("微信发送线程超时，请人工确认消息是否已发出")
        if box["error"] is not None:
            raise box["error"]
        return box["value"]

    def _drop_uia(self) -> None:
        """在发送线程里释放 UIA 引擎（COM 对象在创建它的线程里释放更稳）。"""
        self._uia = None

    def _stop_rpa_thread(self) -> None:
        thread, q = self._rpa_thread, self._rpa_q
        if thread is not None and thread.is_alive() and q is not None:
            try:
                self._rpa_call(self._drop_uia, timeout=10)
            except Exception:  # noqa: BLE001 - 停止阶段的异常不阻塞退出
                pass
        with self._rpa_lock:
            thread, q = self._rpa_thread, self._rpa_q
            self._rpa_thread = None
            self._rpa_q = None
        if q is not None:
            try:
                q.put(None)
            except Exception:  # noqa: BLE001
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._uia = None

    def send_text(self, chat_id: str, text: str) -> None:
        """发送文本：搜索栏搜会话名 → 校验目标 → 输入 → 回车（发送前回读校验）。"""
        if not self._connected:
            raise DriverError("微信 4.x 通道未就绪")
        name = self._chat_name(chat_id)
        self._rpa_call(self._send_text_rpa, name, text)

    def send_emoji(self, chat_id: str, path: str) -> None:
        """发送本地动画表情（gif）：剪贴板粘贴文件，微信自动按动画表情发出。"""
        self._send_clipboard_file(chat_id, path)

    def send_file(self, chat_id: str, path: str) -> None:
        """发送本地文件：剪贴板粘贴，微信按类型识别为图片/视频/文件。"""
        self._send_clipboard_file(chat_id, path)

    def _send_clipboard_file(self, chat_id: str, path: str) -> None:
        """把本地文件粘贴进目标会话的输入框发送，send_emoji / send_file 共用。

        微信对粘贴的文件自动识别类型：GIF 发动画表情、jpg/png 发图片消息、
        其余发文件卡片，无需按类型分支。
        """
        if not self._connected:
            raise DriverError("微信 4.x 通道未就绪")
        path = str(Path(path).resolve())
        if not Path(path).is_file():
            raise DriverError(f"文件不存在：{path}")
        name = self._chat_name(chat_id)
        self._rpa_call(self._send_file_rpa, name, path, timeout=240)

    def _find_window(self) -> int:
        target_pid = getattr(self, "_pid", 0)
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def cb(h, _):
            p = w.DWORD()
            _u.GetWindowThreadProcessId(h, ctypes.byref(p))
            if target_pid and p.value != target_pid:
                return True
            if not _u.IsWindowVisible(h):
                return True
            n = _u.GetWindowTextLengthW(h)
            title = ctypes.create_unicode_buffer(n + 1)
            _u.GetWindowTextW(h, title, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            _u.GetClassNameW(h, cls, 256)
            if title.value == "微信" and cls.value.startswith("Qt5"):
                found.append(int(h))
            return True

        _u.EnumWindows(WNDENUMPROC(cb), 0)
        return found[0] if found else 0

    @staticmethod
    def _foreground_hwnd() -> int:
        try:
            return int(_u.GetForegroundWindow() or 0)
        except Exception:  # noqa: BLE001 - 取不到就当没有前台
            return 0

    def _window_pid(self, hwnd: int) -> int:
        try:
            pid = w.DWORD()
            _u.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except Exception:  # noqa: BLE001
            return 0

    def _belongs_to_wechat(self, hwnd: int) -> bool:
        """该窗口是不是本账号的微信（按 PID；PID 未知时退回主窗句柄）。"""
        if not hwnd:
            return False
        if self._pid:
            return self._window_pid(hwnd) == self._pid
        main = self._find_window()
        return bool(main) and int(hwnd) == int(main)

    def _restore_foreground(self, prev: int) -> None:
        """把发送期间被微信顶掉的前台窗口还回去。

        只处理「发送前不是微信、发送后变成微信」这一种：原本就在微信里、
        或中途用户自己切了别的窗口，都不抢他的选择。恢复失败只记 debug，
        不影响发送结果。
        """
        if not prev:
            return
        try:
            fg = self._foreground_hwnd()
            if not fg or fg == prev:
                return
            if not self._belongs_to_wechat(fg):
                return
            if self._belongs_to_wechat(prev):
                return
            if not _u.IsWindow(prev) or not _u.IsWindowVisible(prev) or _u.IsIconic(prev):
                return
            cur_tid = int(_k.GetCurrentThreadId())
            fg_tid = int(_u.GetWindowThreadProcessId(fg, None) or 0)
            attached = False
            try:
                if fg_tid:
                    attached = bool(_u.AttachThreadInput(cur_tid, fg_tid, True))
                _u.BringWindowToTop(prev)
                if not _u.SetForegroundWindow(prev):
                    log.debug("恢复前台窗口被系统拒绝：hwnd=%s", prev)
            finally:
                if attached:
                    _u.AttachThreadInput(cur_tid, fg_tid, False)
        except Exception as exc:  # noqa: BLE001 - 恢复前台只是体验优化，绝不打断发送
            log.debug("恢复前台窗口失败：%s", exc)

    @staticmethod
    def _click(x: int, y: int) -> None:
        _u.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        _u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        _u.mouse_event(0x0004, 0, 0, 0, 0)

    @staticmethod
    def _key(vk: int) -> None:
        _u.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
        _u.keybd_event(vk, 0, 2, 0)

    @staticmethod
    def _ctrl_v() -> None:
        _u.keybd_event(0x11, 0, 0, 0)
        _u.keybd_event(0x56, 0, 0, 0)
        time.sleep(0.03)
        _u.keybd_event(0x56, 0, 2, 0)
        _u.keybd_event(0x11, 0, 2, 0)

    @staticmethod
    def _copy_hdrop(paths: list[str]) -> None:
        """把本地文件放进剪贴板（CF_HDROP），微信输入框 Ctrl+V 即可粘贴发送。"""
        blob = hdrop_blob([str(Path(p).resolve()) for p in paths])
        h = _k.GlobalAlloc(GMEM_MOVEABLE, len(blob))
        if not h:
            raise DriverError("分配剪贴板内存失败")
        ptr = _k.GlobalLock(h)
        if not ptr:
            _k.GlobalFree(h)
            raise DriverError("锁定剪贴板内存失败")
        ctypes.memmove(ptr, blob, len(blob))
        _k.GlobalUnlock(h)

        opened = False
        for _ in range(5):  # 剪贴板可能被其他程序短暂占用
            if _u.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.1)
        if not opened:
            _k.GlobalFree(h)
            raise DriverError("打开剪贴板失败（可能被其他程序占用）")
        try:
            _u.EmptyClipboard()
            if not _u.SetClipboardData(CF_HDROP, w.HANDLE(h)):
                _k.GlobalFree(h)  # 失败时归我们还，成功后归系统所有
                raise DriverError("写入剪贴板失败")
        finally:
            _u.CloseClipboard()

    def list_chats(self, keyword: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not self.db:
            return []
        self._refresh_chats()
        items = []
        for chat_id, name in self._chats.items():
            if is_internal_chat(chat_id):
                continue
            if keyword and keyword.lower() not in (name or chat_id).lower():
                continue
            items.append({"chat_id": chat_id, "name": name})
            if len(items) >= limit:
                break
        return items

    def resolve_name(self, username: str) -> str:
        """wxid → 备注/昵称；查不到原样返回。

        不能叫 display_name：那是 base 驱动约定的展示名字符串属性，
        被本方法覆盖后 status()/事件日志会拿到 bound method，导致 JSON 序列化 500。
        """
        username = str(username or "")
        if not username or not self.db:
            return username
        try:
            return self.db.nickname_map().get(username) or username
        except Exception:
            return username

    @staticmethod
    def _split_msg_id(local_id: str, shard: str = "") -> tuple[str, str]:
        """从复合格式的消息 id 里还原 (shard, local_id)。

        兼容「会话#分片#local_id」（现行）与「分片#local_id」（旧入库记录）两种格式；
        传入的 shard 优先于 id 里带的。裸 local_id（无 #）原样返回。
        """
        parts = str(local_id or "").rsplit("#", 2)
        if len(parts) == 3:
            return shard or parts[1], parts[2]
        if len(parts) == 2:
            return shard or parts[0], parts[1]
        return shard, parts[0]

    def get_image(
        self, chat_id: str, local_id: str, shard: str = "", tier: str | None = None
    ) -> str | None:
        """按消息 local_id 解密本地图片，返回图片文件路径；本机没有则 None。"""
        if not self.db:
            return None
        # 消息 id 可能是「会话#分片#local_id」（现行）或「分片#local_id」（旧入库
        # 记录）的复合格式，回读媒体时还原成纯 local_id；调用方没带 shard 时从 id 里补
        shard, text = self._split_msg_id(local_id, shard)
        try:
            from .diagnose import wx4_media_module

            mod = wx4_media_module()
            if mod is None:
                return None
            with self._media_lock:
                if self._media is None:
                    from ..config import DATA_DIR

                    save_dir = DATA_DIR / "media" / (self.db.account or "wechat")
                    self._media = mod.MediaDownloader(self.db, save_dir=str(save_dir))
                return self._media.download_image(
                    chat_id,
                    int(text),
                    tier=tier or "best",
                    shard=shard or None,
                )
        except Exception as exc:
            log.warning("读取图片失败 %s/%s：%s", chat_id, local_id, exc)
            return None

    def get_image_by_server_id(self, chat_id: str, server_id: str) -> str | None:
        """按消息 server_id 反查并解密图片；引用消息（refermsg.svrid）取原图用。

        本地消息表只存分片+local_id，被引用的原消息只有 refermsg 里的 svrid，
        这里先在近期图片行里把 svrid 换算回 local_id+分片，再走 get_image。
        """
        if not self.db:
            return None
        target = str(server_id or "").strip()
        if not target:
            return None
        try:
            rows = self.db.get_image_rows(chat_id, limit=300)
        except Exception as exc:
            log.warning("按 server_id 查图片失败 %s：%s", chat_id, exc)
            return None
        row = next((r for r in rows if str(r.get("server_id") or "") == target), None)
        if not row:
            return None
        return self.get_image(chat_id, str(row.get("local_id") or ""), str(row.get("shard") or ""))

    def _refresh_chats(self) -> None:
        if not self.db:
            return
        try:
            nicks = self.db.nickname_map()
        except Exception:
            nicks = {}
        try:
            for s in self.db.get_sessions(limit=500):
                username = s.get("username")
                if username and not is_internal_chat(username):
                    self._chats[username] = nicks.get(username) or username
        except Exception as exc:
            log.warning("刷新会话列表失败：%s", exc)

    def _sender_names(self) -> dict[int, str]:
        if time.time() - self._senders_at < 60 and self._senders:
            return self._senders
        try:
            # 1.1.x 有 _sender_id_index；1.2.7 移除（改用回调内的 sender_username）
            if self.db and hasattr(self.db, "_sender_id_index"):
                idx = self.db._sender_id_index()
                nicks = self.db.nickname_map()
                self._senders = {rid: nicks.get(u, u) for rid, u in idx.items()}
                self._senders_at = time.time()
        except Exception:
            pass
        return self._senders

    @staticmethod
    def _msg_id(chat_id: str, shard: str, local_id: str) -> str:
        """消息 id：「会话#分片#local_id」。

        微信 4.x 每个会话一张独立的 Msg_<hash> 表，local_id 每表从 1 起，
        只用「分片#local_id」的话不同会话必然撞 id：实时去重会吞消息、
        INSERT OR REPLACE 会跨会话覆盖，所以必须把 chat_id 编进 id。
        """
        return f"{chat_id}#{shard}#{local_id}"

    def _pat_hit(
        self, content: str, msg_type: str
    ) -> tuple[str, dict[str, Any]] | None:
        """识别拍一拍并按 wxid 解析人名；非拍一拍返回 None。

        4.x 的系统消息正文是 ``<sysmsg type="pat">`` XML；个别版本会把它标成
        别的类型，所以只要正文带 pat XML 也认。普通文本里的「拍了拍」不算，
        避免把聊天内容误判成系统通知。
        """
        if msg_type not in ("system", "pat") and "<pat" not in content and 'type="pat"' not in content:
            return None
        pat = parse_pat_notice(content)
        if not pat:
            return None
        return format_pat_notice(pat, self.resolve_name, self._self_wxid)

    def _row_to_message(self, chat_id: str, msg: dict) -> Message | None:
        """把解密库的一行消息转成 Message；自己发的/内部会话返回 None。"""
        sender_name_raw = str(msg.get("sender_username") or "")
        is_group = chat_id.endswith("@chatroom")
        sender_id = msg.get("sender_id")
        sender_wxid = str(msg.get("sender_username") or "")
        raw_type = msg.get("type")
        msg_type = TYPE_MAP.get(raw_type) if isinstance(raw_type, str) else CODE_MAP.get(raw_type, "text")
        msg_type = msg_type or "text"
        content = strip_sender_prefix(str(msg.get("content") or ""), sender_wxid)
        shard = str(msg.get("shard") or "")
        local_id = str(msg.get("local_id") or "")
        raw: dict[str, Any] = {"shard": shard, "local_id": local_id}

        pat_hit = self._pat_hit(content, msg_type)
        if pat_hit:
            content, pat_info = pat_hit
            raw["pat"] = pat_info
            # pat XML 的 fromusername 才是拍的人；system 行的 real_sender_id
            # 不一定可靠（1.2.7 里 rowid 2 可能是任意群成员），不能拿它判自己
            is_self = bool(pat_info.get("from_self")) or (
                not pat_info.get("from_id")
                and (
                    msg.get("sender_id") == 2
                    or (sender_name_raw and sender_name_raw == self._self_wxid)
                )
            )
            if is_self:
                return None
            actor = str(pat_info.get("from_id") or sender_wxid)
            sender_name = str(pat_info.get("from_name") or "")
            if not sender_name or sender_name == "有人":
                sender_name = self.resolve_name(actor) if actor else self._chats.get(chat_id, chat_id)
            return Message(
                chat_id=chat_id,
                chat_name=self._chats.get(chat_id, chat_id),
                sender_id=actor or str(sender_id or ""),
                sender_name=sender_name,
                content=content,
                is_group=is_group,
                is_self=False,
                msg_type="pat",
                ts=float(msg.get("create_time") or time.time()),
                msg_id=self._msg_id(chat_id, shard, local_id),
                raw=raw,
            )

        # 自己发出的消息：1.1.x 是 sender_id==2；1.2.7 起按分片 Name2Id 解析为自己的 wxid
        if msg.get("sender_id") == 2 or (
            sender_name_raw and sender_name_raw == self._self_wxid
        ):
            return None
        if sender_wxid:
            sender_name = self.resolve_name(sender_wxid)
        elif is_group:
            sender_name = self._sender_names().get(sender_id, "") or self._chats.get(chat_id, chat_id)
        else:
            sender_name = self._chats.get(chat_id, chat_id)

        return Message(
            chat_id=chat_id,
            chat_name=self._chats.get(chat_id, chat_id),
            # sender_id 统一存 wxid（拿不到才退数值 id）：昵称会重名，习惯统计与
            # AI 上下文里的同名群友区分都靠它
            sender_id=sender_wxid or str(sender_id or ""),
            sender_name=sender_name,
            content=content,
            is_group=is_group,
            is_self=False,
            msg_type=msg_type or "text",
            ts=float(msg.get("create_time") or time.time()),
            msg_id=self._msg_id(chat_id, shard, local_id),
            raw=raw,
        )

    def _on_db_message(self, msg: dict, _listener=None) -> None:
        try:
            chat_id = str(msg.get("username") or "")
            if not chat_id or is_internal_chat(chat_id):
                return
            sender_name_raw = str(msg.get("sender_username") or "")
            is_group = chat_id.endswith("@chatroom")
            sender_id = msg.get("sender_id")
            sender_wxid = str(msg.get("sender_username") or "")
            raw_type = msg.get("type")
            msg_type = TYPE_MAP.get(raw_type) if isinstance(raw_type, str) else CODE_MAP.get(raw_type, "text")
            msg_type = msg_type or "text"
            content = strip_sender_prefix(str(msg.get("content") or ""), sender_wxid)
            shard = str(msg.get("shard") or "")
            local_id = str(msg.get("local_id") or "")
            raw: dict[str, Any] = {"shard": shard, "local_id": local_id}

            pat_hit = self._pat_hit(content, msg_type)
            if pat_hit:
                content, pat_info = pat_hit
                raw["pat"] = pat_info
                is_self = bool(pat_info.get("from_self")) or (
                    not pat_info.get("from_id")
                    and (
                        (sender_name_raw and sender_name_raw == self._self_wxid)
                        or (not sender_name_raw and msg.get("sender_id") == 2)
                    )
                )
                actor = str(pat_info.get("from_id") or sender_wxid)
                if is_self:
                    sender_wxid = actor or self._self_wxid
                    sender_name = "我"
                else:
                    sender_wxid = actor
                    sender_name = str(pat_info.get("from_name") or "")
                    if not sender_name or sender_name == "有人":
                        sender_name = (
                            self.resolve_name(actor)
                            if actor
                            else (self._chats.get(chat_id) or chat_id)
                        )
                msg_type = "pat"
            else:
                # 自己发出的消息：1.2.7 起按分片 Name2Id 解析为自己的 wxid，最可靠；
                # sender_id==2 只是 1.1.x 的约定（1.2.7 里 rowid 2 可能是任意群成员，
                # 只在解析不出 wxid 时才回退该判断）。不在这里丢弃：人工在手机/桌面端
                # 直接发的消息只有这条通道能看到，由上层按出站留底区分回声与人工接管
                is_self = bool(
                    (sender_name_raw and sender_name_raw == self._self_wxid)
                    or (not sender_name_raw and msg.get("sender_id") == 2)
                )
                if is_self:
                    sender_wxid = sender_wxid or self._self_wxid
                    sender_name = "我"
                elif sender_wxid:
                    sender_name = self.resolve_name(sender_wxid)
                elif is_group:
                    sender_name = self._sender_names().get(sender_id, "") or self._chats.get(chat_id, chat_id)
                else:
                    sender_name = self._chats.get(chat_id, chat_id)

            item = Message(
                chat_id=chat_id,
                chat_name=self._chats.get(chat_id, chat_id),
                sender_id=sender_wxid or str(sender_id or ""),
                sender_name=sender_name,
                content=content,
                is_group=is_group,
                is_self=is_self,
                msg_type=msg_type,
                ts=float(msg.get("create_time") or time.time()),
                msg_id=self._msg_id(chat_id, shard, local_id),
                raw=raw,
            )
            if self.on_message:
                self.on_message(item)
        except Exception as exc:  # 单条消息异常不打断监听
            log.warning("处理数据库消息失败：%s", exc)

    def fetch_history(self, chat_id: str, limit: int = 50) -> list[Message]:
        """从解密库按会话回读最近的历史消息（启动回填用），不包含自己发的消息。"""
        if not self.db or limit <= 0 or not chat_id:
            return []
        try:
            rows = self.db.get_messages(chat_id, int(limit))
        except Exception as exc:
            log.warning("读取历史消息失败 %s：%s", chat_id, exc)
            return []
        out: list[Message] = []
        for row in rows:
            try:
                item = self._row_to_message(chat_id, row)
            except Exception as exc:
                log.warning("解析历史消息失败 %s/%s：%s", chat_id, row.get("local_id"), exc)
                continue
            if item:
                out.append(item)
        return out
