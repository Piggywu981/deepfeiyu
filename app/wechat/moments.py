"""朋友圈服务：sns.db 轮询收动态/互动通知；点赞与评论走 UIA（需要微信窗口）。

能力边界（受底层 wechatauto 限制）：

- 收动态、收「谁赞/评了我」：纯数据库读取（sns.db），不碰窗口，稳定；
- 点赞、评论、回复评论：UIA 控件操作，会激活微信窗口、滚动定位，
  单次可能耗时几十秒，且要求桌面已解锁；
- 发朋友圈：微信 4.x 发表页为自绘界面，底层库明确不支持，不做。

所有 wechatauto 导入都延迟到方法内部：普通聊天通道不需要这串重依赖。
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .. import config, db
from ..bus import bus
from . import ui_gate

log = logging.getLogger("moments")

# 点赞/评论要独占微信前台窗口：与驱动的 UIA 发送共用 ui_gate.UI_LOCK，
# 全局串行（多账号也不会互相把界面切走）。等锁上限：发送侧单个操作秒级，
# 超过这个时间说明前面卡住了，这次不做，别把 AI 的工具调用挂死。
_UI_WAIT_SECONDS = 45.0


def _import_wechatauto():
    """完整导入 wechatauto；旧进程里诊断代码注入的空壳 stub 会挡道，清掉重试。

    驱动启动时 wx4_db_module 曾把 sys.modules["wechatauto"] 换成只带 __path__
    的 stub（绕过当年装不全的 UIA 依赖）。stub 存在时 `from wechatauto import
    WeChat` 会报 cannot import name (unknown location)；识别到 stub 就整体
    逐出，让真实包重新导入。db.py 走独立模块名（wechatauto_db），不受影响。
    """
    import sys

    try:
        import wechatauto  # noqa: F401
    except ImportError:
        raise
    pkg = sys.modules.get("wechatauto")
    if pkg is not None and not hasattr(pkg, "WeChat"):
        for name in [n for n in sys.modules if n == "wechatauto" or n.startswith("wechatauto.")]:
            del sys.modules[name]
        import wechatauto  # noqa: F401
    return sys.modules["wechatauto"]


def poll_interval() -> float:
    raw = config.load().get("moments") or {}
    try:
        return max(30.0, float(raw.get("poll_interval", 600) or 600))
    except (TypeError, ValueError):
        return 600.0


def moments_enabled() -> bool:
    raw = config.load().get("moments") or {}
    return bool(raw.get("enabled", True))


def _com_init() -> None:
    """UIA 是 COM 组件，每个线程都得先初始化；asyncio 工作线程默认没初始化。

    uiautomation 只在「导入它的那个线程」上自动 CoInitializeEx；服务进程里
    点赞/评论跑在 to_thread 工作线程上，不补就会全部静默失败（日志表现：
    「尚未调用 CoInitialize」→「未找到朋友圈窗口」→ 定位失败）。
    CoInitializeEx 幂等，重复调用无害。
    """
    try:
        from comtypes import CoInitializeEx

        CoInitializeEx()
    except Exception:  # noqa: BLE001
        pass


def _restore_wechat_window(wx: Any) -> None:
    """微信被遮挡/最小化时 UIA 点击与滚轮会落到别的窗口：互动前强抢前台。

    SetForegroundWindow 有系统级前台锁：后台服务进程直接调用多半被静默
    拒绝（无报错，前台不变），于是被遮挡的微信连「滚轮落点校验」这关都
    过不去，定位必然失败。用 AttachThreadInput 借当前前台线程的输入队列
    再切焦点，与 weixin4 驱动 send_text 的 _activate 同一套已验证做法。
    """
    import ctypes
    import ctypes.wintypes as _w

    try:
        hwnd = int(wx._gui.main_hwnd or 0)
    except Exception:
        return
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    hwnd = _w.HWND(hwnd)
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.3)
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(_w.HWND(fg), None) if fg else 0
        tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = False
        if tid_fg and tid_fg != tid_me:
            attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(tid_me, tid_fg, False)
        time.sleep(0.3)
    except Exception:
        pass


_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_IMAGE_EXT_BY_FORMAT = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}


def _cached_image(base: Path, index: int) -> Path | None:
    for suffix in _IMAGE_SUFFIXES:
        p = base / f"image_{index}{suffix}"
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _store_image(src: Path, base: Path, index: int) -> Path | None:
    """把下载到的图片存成固定名 ``image_<index>.<真实后缀>``。

    微信 Sns 缓存会把非 JPEG 图片落成 ``<md5>.mp4``，后缀不可信：按 Pillow
    识别出的真实格式重新起名，下次直接用这份副本，不再依赖微信缓存。
    识别不了格式就返回原文件（识图侧会按内容解码，扩展名不影响读取）。
    """
    try:
        from PIL import Image

        with Image.open(src) as im:
            suffix = _IMAGE_EXT_BY_FORMAT.get(str(im.format or "").lower())
        if not suffix:
            return src if src.is_file() else None
        base.mkdir(parents=True, exist_ok=True)
        dst = base / f"image_{index}{suffix}"
        if dst.resolve() != src.resolve():
            shutil.copyfile(src, dst)
        return dst
    except Exception:  # noqa: BLE001
        return src if src.is_file() else None


class MomentsService:
    """一个微信账号的朋友圈：后台轮询入库 + 按需 UIA 互动。"""

    def __init__(self, session: Any, driver: Any) -> None:
        self.session = session
        self.driver = driver
        self.account_id = session.account_id
        self.last_sync = 0.0
        self.last_error = ""
        self._wx: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 数据库读取侧
    def _mdb(self) -> Any:
        try:
            _import_wechatauto()
            from wechatauto.moment import MomentDB
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"朋友圈依赖加载失败（{exc}）。如果是刚装完依赖，重启一次服务即可。"
            ) from exc
        return MomentDB(self.driver.db)

    def _owner_key(self) -> str:
        return f"moments:{self.account_id}:owner"

    def _tid_key(self) -> str:
        return f"moments:{self.account_id}:tid"

    def current_owner(self) -> str:
        """当前解密账号（xwechat_files 下的目录名）；驱动未就绪时为空串。"""
        return str(getattr(getattr(self.driver, "db", None), "account", "") or "")

    def _expected_owner(self) -> str:
        """配置里指定的账号目录名（weixin4.account_dir）；没配返回空串。"""
        acc = getattr(self.session, "account", None) or {}
        try:
            return str(((acc.get("weixin4") or {}).get("account_dir")) or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _ensure_owner(self) -> str:
        """缓存必须属于当前解密的微信号；返回 ok / rebuilt，账号错配直接抛错。

        多开机器上 ``resolve_wx4_account`` 的自动选择可能落到另一个微信号上
        （号主反馈「朋友圈读取的是我的微信」就是缓存里全是号主个人号的好友动态，
        而当前驱动解密的却是机器人号）。按当前账号重建一次，比让两个微信号的
        数据混在一起更安全：旧「动态/互动」清掉、水位归零后重新同步。

        若驱动的解密账号和配置里指定的账号不一致（说明驱动本身选错号了），
        既不重建也不继续同步——否则会把另一个微信号的动态写进这个号的缓存。
        """
        owner = self.current_owner()
        if not owner:
            return "ok"
        expected = self._expected_owner()
        if expected and owner != expected:
            raise RuntimeError(
                f"朋友圈账号不一致：驱动解密的是 {owner}，配置要求 {expected}。"
                "为避免把另一个微信号的动态同步进来，本轮跳过；请检查微信多开与"
                "账号目录配置（weixin4.account_dir）"
            )
        stored = db.kv_get(self._owner_key())
        if stored == owner:
            return "ok"
        cleared = db.clear_moments(self.account_id)
        db.kv_set(self._owner_key(), owner)
        db.kv_set(self._tid_key(), None)
        log.warning(
            "[%s] 朋友圈缓存账号不符（缓存=%s 当前=%s），已清空重建：动态 %d 条 / 互动 %d 条",
            self.account_id, stored or "<未记录>", owner,
            cleared["feeds"], cleared["interactions"],
        )
        db.add_event(
            self.account_id,
            "moments",
            f"朋友圈缓存已按当前微信重建（{owner}）",
            f"清掉旧缓存：动态 {cleared['feeds']} 条、互动 {cleared['interactions']} 条",
        )
        return "rebuilt"

    def _repair_watermark(self, mdb: Any) -> None:
        """水位必须落在这个账号的时间线里：比当前最新 tid 还新说明是别的号留下的。"""
        try:
            latest = mdb.latest_tid()
        except Exception as exc:  # noqa: BLE001 - 读不到就当没有水位可修
            log.debug("[%s] 读取朋友圈最新 tid 失败：%s", self.account_id, exc)
            return
        if latest is None:
            return
        since = db.kv_get(self._tid_key())
        try:
            since_i = int(since) if since is not None else None
        except (TypeError, ValueError):
            since_i = None
        if since_i is not None and since_i > int(latest):
            db.kv_set(self._tid_key(), None)
            log.warning(
                "[%s] 朋友圈同步水位(%s)比当前账号最新 tid(%s)还新，判定来自别的微信号，已重置",
                self.account_id, since_i, latest,
            )

    def sync_once(self, ensure_owner: bool = True) -> dict[str, Any]:
        """拉一轮时间线与互动通知入库，返回统计。"""
        rebuilt = False
        if ensure_owner:
            rebuilt = self._ensure_owner() == "rebuilt"
        mdb = self._mdb()
        self._repair_watermark(mdb)
        wm_key = self._tid_key()
        feeds, latest = mdb.get_moments_since(since_tid=db.kv_get(wm_key), limit=200)
        new_count = 0
        for feed in feeds:
            if db.upsert_moment(self.account_id, feed):
                new_count += 1
        # 时间线里的点赞/评论会随服务端更新：每轮刷新最近一屏的互动快照
        for feed in mdb.get_moments(limit=50):
            db.upsert_moment(self.account_id, feed)
        new_inter = 0
        for item in mdb.get_interactions(limit=100):
            if db.add_moment_interaction(self.account_id, item):
                new_inter += 1
                self._notify_interaction(item)
        if latest:
            db.kv_set(wm_key, int(latest))
        self.last_sync = time.time()
        db.kv_set(f"moments:{self.account_id}:sync", self.last_sync)
        if new_count:
            db.add_event(
                self.account_id, "moments", f"朋友圈新增 {new_count} 条", "已同步到控制台"
            )
            bus.publish(
                {"type": "moments_dirty", "data": {"account_id": self.account_id, "new": new_count}}
            )
        if new_inter:
            bus.publish(
                {
                    "type": "moment_interactions",
                    "data": {"account_id": self.account_id, "new": new_inter},
                }
            )
        stat: dict[str, Any] = {"new_feeds": new_count, "new_interactions": new_inter}
        if rebuilt:
            stat["rebuilt"] = True
        return stat

    # ---------------------------------------------------------------- 深度刷新
    def deep_sync(self) -> dict[str, Any]:
        """打开微信朋友圈页面，让客户端自己拉一次最新时间线，再读库。

        纯读 ``sns.db`` 只能看到微信本地已经缓存的那部分：机器人号很久没在
        客户端里点开过朋友圈，本地库就一直停在上次打开的时间（实测停在 10-24），
        号主点「立即同步」自然什么都刷不出来。这里用 UIA 点开朋友圈页触发拉取，
        等几秒写库，再切回聊天页；失败不致命，调用方仍会做一次纯库同步。
        """
        result: dict[str, Any] = {"ui_opened": False, "ui_error": ""}
        if not moments_enabled():
            result["ui_error"] = "朋友圈功能未启用"
            return result
        try:
            with ui_gate.hold(timeout=_UI_WAIT_SECONDS):
                _com_init()
                wx = self._wechat()
                _restore_wechat_window(wx)
                result["ui_opened"] = bool(wx.SwitchToMoments())
                if result["ui_opened"]:
                    # 等客户端拉取→解析→写入 sns.db；3s 实测够首屏几十条
                    time.sleep(3.0)
                _back_to_chat(wx)
        except ui_gate.UIBusy as exc:
            result["ui_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - 打开界面失败还有纯库同步兜底
            result["ui_error"] = f"打开微信朋友圈失败：{exc}"
            log.warning("[%s] 深度刷新朋友圈失败：%s", self.account_id, exc)
        return result

    def refresh(self, deep: bool = True) -> dict[str, Any]:
        """控制台「立即同步」：默认先让微信客户端拉一次最新朋友圈，再读库入库。"""
        ui: dict[str, Any] = {"ui_opened": False, "ui_error": ""}
        if deep:
            ui = self.deep_sync()
        stat = self.sync_once()
        # 客户端拉取/写库有延迟：第一次没读到新数据就再等两秒补读一次
        if deep and ui.get("ui_opened") and not stat.get("new_feeds"):
            time.sleep(2.0)
            again = self.sync_once(ensure_owner=False)
            stat["new_feeds"] += again["new_feeds"]
            stat["new_interactions"] += again["new_interactions"]
        return {**stat, "deep": bool(deep), **ui}

    def _notify_interaction(self, item: dict[str, Any]) -> None:
        kind = "赞了" if int(item.get("type") or 0) == 1 else "评论了"
        who = str(item.get("from_nickname") or item.get("from_username") or "有人")
        content = str(item.get("content") or "")
        title = f"{who} {kind}你的朋友圈" if kind == "赞了" else f"{who} 评论：{content[:50]}"
        db.add_event(self.account_id, "moments", title, content[:200] if kind != "赞了" else "")
        bus.publish(
            {
                "type": "moment_interaction",
                "data": {"account_id": self.account_id, "item": dict(item)},
            }
        )

    # ---------------------------------------------------------------- 后台轮询
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"moments-{self.account_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_once()
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.warning("[%s] 朋友圈同步失败: %s", self.account_id, exc)
            self._stop.wait(poll_interval())

    # ---------------------------------------------------------------- UIA 互动侧
    def _bind_hwnd(self) -> int:
        """当前驱动账号的主窗口句柄：多开时必须绑自己的窗口，不能挑「最大的那个」。"""
        find = getattr(self.driver, "_find_window", None)
        if not callable(find):
            return 0
        try:
            return int(find() or 0)
        except Exception:  # noqa: BLE001 - 找不到就退回库的自动定位
            return 0

    def _wechat(self) -> Any:
        if self._wx is None:
            _com_init()
            try:
                _import_wechatauto()
                from wechatauto import WeChat
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"朋友圈互动依赖加载失败（{exc}）。"
                    "如果是刚装完依赖，重启一次服务即可；"
                    "首次安装请参考 requirements.txt 里朋友圈一段。"
                ) from exc
            gui = None
            hwnd = self._bind_hwnd()
            if hwnd:
                try:
                    from wechatauto.guia import WeChatGUI

                    gui = WeChatGUI(hwnd=hwnd)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "[%s] 朋友圈绑定微信窗口 %s 失败（改回自动定位）：%s",
                        self.account_id, hwnd, exc,
                    )
                    gui = None
            # 复用驱动已解密的 WeChatDB，避免再走一遍密钥扫描；
            # gui 显式绑驱动账号窗口，多开时才不会点到号主个人微信的窗口上
            self._wx = WeChat(db=self.driver.db, gui=gui)
        return self._wx

    @staticmethod
    def _locate(feed: dict[str, Any]) -> tuple[str, str | None]:
        """把库里的动态转成 UIA 定位参数 (publisher, keyword)。

        纯图/视频动态没有正文，只能按发布者定位：命中的是该作者最新一条，
        若作者后来又发了新动态就会定位错条，调用方需接受这个限制。
        """
        publisher = str(feed.get("nickname") or feed.get("username") or "").strip()
        if not publisher:
            raise ValueError("缺少发布者信息，无法在界面上定位这条朋友圈")
        text = str(feed.get("text") or "").strip()
        return publisher, text[:10] or None

    def _respond(self, resp: Any) -> dict[str, Any]:
        if not isinstance(resp, dict):
            return {"ok": False, "message": str(resp), "data": {}}
        # WxResponse 的状态字面量是中文（成功/失败/错误），优先用库自带的
        # is_success 判断；普通 dict 才退回字面量比对
        ok = bool(getattr(resp, "is_success", False)) or str(resp.get("status") or "") in ("成功", "success")
        return {"ok": ok, "message": str(resp.get("message") or ""), "data": resp.get("data") or {}}

    def like(self, feed: dict[str, Any]) -> dict[str, Any]:
        publisher, keyword = self._locate(feed)
        return self._interact(
            "点赞",
            lambda moment, mdb: moment.LikeMoment(publisher=publisher, keyword=keyword, db=mdb),
        )

    def like_cancel(self, feed: dict[str, Any]) -> dict[str, Any]:
        publisher, keyword = self._locate(feed)

        def _run(moment: Any, mdb: Any) -> Any:
            item = moment.find_moment(publisher=publisher, keyword=keyword, db=mdb)
            if item is None:
                return {"status": "失败", "message": self._LOCATE_FAIL, "data": {}}
            return moment.Like(item, cancel=True)

        return self._interact("取消点赞", _run)

    def comment(self, feed: dict[str, Any], content: str, reply_to: str = "") -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            return {"ok": False, "message": "评论内容不能为空", "data": {}}
        publisher, keyword = self._locate(feed)
        if reply_to:
            call = lambda moment, mdb: moment.ReplyCommentMoment(  # noqa: E731
                publisher=publisher,
                keyword=keyword,
                reply_to=reply_to,
                content=content,
                db=mdb,
            )
        else:
            call = lambda moment, mdb: moment.CommentMoment(  # noqa: E731
                publisher=publisher, keyword=keyword, content=content, db=mdb
            )
        return self._interact("评论", call)

    # ------------------------------------------------------- 界面独占 / 现场判定
    def _interact(self, label: str, call: Any) -> dict[str, Any]:
        """点赞/评论共用：取标尺库 → 独占界面 → 执行 → 交还聊天页。"""
        try:
            mdb = self._mdb()
        except Exception as exc:  # noqa: BLE001 - 依赖没装齐时给一句人话
            return {"ok": False, "message": str(exc), "data": {}}
        try:
            with ui_gate.hold(timeout=_UI_WAIT_SECONDS):
                return self._interact_locked(label, call, mdb)
        except ui_gate.UIBusy as exc:
            return {"ok": False, "message": str(exc), "data": {}}

    def _interact_locked(self, label: str, call: Any, mdb: Any) -> dict[str, Any]:
        _com_init()
        try:
            wx = self._wechat()
        except RuntimeError as exc:  # 依赖没装齐 / 加载失败
            return {"ok": False, "message": str(exc), "data": {}}
        _restore_wechat_window(wx)
        err = self._ensure_moments_page(wx)
        if err:
            return {"ok": False, "message": err, "data": {}}
        page_ok = False
        try:
            try:
                resp = call(wx.Moment, mdb)
            finally:
                # 回聊天页之前先记下时间线当时还在不在：这是区分「页面被切走」
                # 和「真找不到这条动态」唯一的现场证据
                page_ok = self._wx_page_visible(wx)
                _back_to_chat(wx)
        except Exception as exc:  # noqa: BLE001 - UIA 抛什么都有可能
            log.warning("[%s] 朋友圈%s异常：%s", self.account_id, label, exc)
            return {"ok": False, "message": f"{label}没做成：{exc}", "data": {}}
        result = self._respond(resp)
        if not result["ok"]:
            result["message"] = self._explain(result["message"], page_ok)
            log.warning("[%s] 朋友圈%s失败：%s", self.account_id, label, result["message"])
        return result

    @staticmethod
    def _timeline_rect(moment: Any) -> tuple | None:
        try:
            return moment._time_line_rect()
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _page_visible(cls, moment: Any) -> bool:
        """时间线控件是否真的在屏幕上（rect 有面积）。"""
        rect = cls._timeline_rect(moment)
        return bool(rect) and (rect[2] - rect[0]) > 0 and (rect[3] - rect[1]) > 0

    @classmethod
    def _wx_page_visible(cls, wx: Any) -> bool:
        """同上，但容忍 Moment 属性本身取不到。"""
        return cls._page_visible(getattr(wx, "Moment", None))

    def _ensure_moments_page(self, wx: Any) -> str:
        """把主窗切到朋友圈页并确认时间线可见；返回失败原因（空串=可以开干）。

        必须先清 Moment 的 _list/_api 缓存：页面被切走后，缓存的列表控件仍
        在 UIA 树里、Exists 照样为真，_ensure_list 便不再 SwitchToMoments，
        于是每次重试都对着一个 rect 全零的隐形控件空转（实测连撞 5 次）。
        """
        moment = getattr(wx, "Moment", None)
        if moment is None:
            return "当前通道不支持朋友圈（UIA 树不可用）"
        moment._list = None
        moment._api = None
        if not self._page_visible(moment):
            return (
                "微信界面没能停在朋友圈页（时间线不可见，多半是消息收发或你手动"
                "切走了页面），本次未执行，等界面空出来再试"
            )
        return ""

    _LOCATE_FAIL = "未能定位到目标朋友圈"

    @classmethod
    def _explain(cls, message: str, page_ok: bool) -> str:
        """把库里笼统的「未能定位到目标朋友圈」按现场情况说清楚，避免模型盲目重试。"""
        if cls._LOCATE_FAIL not in (message or ""):
            return message
        if not page_ok:
            return (
                "朋友圈页面在执行中途被切走了（界面被消息收发抢走），这次没做成，"
                "等一会儿再试"
            )
        return (
            "朋友圈界面里翻遍了也没找到这条动态：可能太旧刷不出来，"
            "或界面上的昵称与库里不一致，换 from_id 或换一条动态"
        )


    # ---------------------------------------------------------------- 媒体
    def download_media(self, feed: dict[str, Any], index: int, kind: str = "image") -> str | None:
        """取一条动态的第 index 张图/视频的本地路径；拿不到返回 None。"""
        return self.download_media_detail(feed, index, kind).get("path")

    def download_media_detail(
        self, feed: dict[str, Any], index: int, kind: str = "image"
    ) -> dict[str, Any]:
        """取一条动态的第 index 张图/视频，返回结构化结果（含失败原因）。

        图片优先读固定副本 ``image_<index>.<ext>``；没有才回落到 wechatauto 的
        「本地 Sns 缓存 → CDN」两段式下载。固定副本让二次读图不再依赖随时会被
        轮换/清理的微信缓存，也顺带修正缓存里错误的后缀（png 常被落成 .mp4）。
        """
        if kind not in ("image", "video"):
            return {"status": "bad-kind", "path": None, "reason": f"unsupported kind: {kind}"}
        items = (feed.get("images") if kind == "image" else feed.get("videos")) or []
        if index < 0 or index >= len(items):
            return {
                "status": "no-such-media",
                "path": None,
                "reason": f"index {index} out of range ({len(items)})",
            }
        save_dir = config.DATA_DIR / "moments_media" / self.account_id / str(
            feed.get("feed_id") or feed.get("id") or "x"
        )
        if kind == "image":
            cached = _cached_image(save_dir, index)
            if cached:
                return {"status": "ok", "path": str(cached), "reason": None}
        try:
            from wechatauto.moment import MomentDB

            res = MomentDB(self.driver.db).download_media_detailed(
                items[index], save_dir=str(save_dir), kind=kind
            )
        except Exception as exc:  # noqa: BLE001 - 依赖缺失/库内部异常都转成可读结果
            return {"status": "error", "path": None, "reason": str(exc)}
        path = res.get("path")
        if path and kind == "image":
            stored = _store_image(Path(path), save_dir, index)
            if stored:
                res = {**res, "path": str(stored)}
        return res

    def status(self) -> dict[str, Any]:
        return {
            "enabled": moments_enabled(),
            "last_sync": self.last_sync or (db.kv_get(f"moments:{self.account_id}:sync") or 0),
            "error": self.last_error,
            "unread_interactions": db.count_unread_interactions(self.account_id),
            "account": self.current_owner(),
            "account_name": str(
                getattr(self.session, "nickname", "")
                or getattr(self.session, "name", "")
                or ""
            ),
            "cache_owner": db.kv_get(self._owner_key()) or "",
        }


def _back_to_chat(wx: Any) -> None:
    """操作完把主窗带回聊天页：别把用户的微信一直停在朋友圈，也省得下次
    发送先被 back_to_chat_tab 切一次页。失败无所谓，发送侧自己会补。"""
    try:
        uia = wx._gui._get_uia()
        if uia is not None:
            uia.back_to_chat_tab()
    except Exception:  # noqa: BLE001
        pass

