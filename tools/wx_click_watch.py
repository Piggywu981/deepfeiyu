"""微信「单击无效」取证看门狗：只记录，不拦截、不点击、不按键、不改焦点。

跑法（另开一个命令行窗口跑，别和服务挤在一起）：

    .venv\\Scripts\\python.exe tools\\wx_click_watch.py            # 一直跑到 Ctrl+C
    .venv\\Scripts\\python.exe tools\\wx_click_watch.py 120        # 只跑 120 秒

产物：``data/wx_click_watch.jsonl``（逐条事件）+ 退出时打印的结论。

要验的假设：机器人发送时在同一条输入流上抢鼠标（驱动里 ``_click`` 是
``SetCursorPos`` + ``mouse_event``，``back_to_chat_tab`` 还会去点导航栏），
真人的「按下」与「抬起」之间被插入一次注入的指针移动，mmui 的列表项就把这次
单击判成拖拽/取消 —— 表现正好是「主窗口单击全废，但双击能弹出会话窗口」
（双击由系统直接投递 WM_LBUTTONDBLCLK，走的是另一条处理路径）。

低级钩子给出的 ``LLMHF_INJECTED`` 是区分「你的手」和「机器人的手」的唯一硬证据，
所以这里把每个鼠标/键盘事件连同注入标志一起记下来，再和微信消息循环的应答探测、
内存水位放进同一条时间轴。

副作用：WH_MOUSE_LL / WH_KEYBOARD_LL 是全局钩子，回调在系统输入路径上同步执行，
跑着它会给每次输入加几微秒到几毫秒；回调里只做一次 append，其余都事后算。
落盘的是本机鼠标坐标与「有无注入按键」的计数，**不记键码**：全局键盘钩子把 vkCode
写进文件，等于在号主机器上存一份能还原密码的按键流水。文件在 ``data/``（已 gitignore）。

本机实测的两条边界（决定这份数据怎么读）：
1. 钩子能看到 ``mouse_event`` 的按下/抬起与按键，但**只改坐标的 ``SetCursorPos``
   不会产生钩子事件**。驱动的点击是 ``SetCursorPos`` 移位 + ``mouse_event`` 按放
   （见 ``weixin4_driver._click``），所以机器人每次真点都会现形；它单纯挪光标不出现。
   判定函数按「按下与抬起之间的偏离」算，注入的按下/抬起本身带坐标，够用。
2. 钩子里的坐标与 DPI-unaware 进程读到的矩形不一定同一坐标系（本机微信窗口 DPI=144，
   系统 DPI 探测报 96，两次测量互相矛盾，未定论）。所以 ``deviation`` 只当「有没有
   被甩开」的相对量看，别拿它对号入座具体像素位置。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path


def _utf8_console() -> None:
    """默认 cmd 是 GBK，直接 print 中文会变成乱码；跑之前先切 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 重定向到文件等场景没有 reconfigure
            pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_WHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002
PM_REMOVE = 0x0001

MOUSE_MSGS = {
    WM_MOUSEMOVE: "move",
    WM_LBUTTONDOWN: "left_down",
    WM_LBUTTONUP: "left_up",
    WM_LBUTTONDBLCLK: "left_dblclk",
    WM_RBUTTONDOWN: "right_down",
    WM_RBUTTONUP: "right_up",
    WM_WHEEL: "wheel",
}
# 会把一次按下搅成「拖拽」的注入事件（纯滚轮不改光标位置，不算）
MOVING_MSGS = ("move", "left_down", "left_up", "right_down", "right_up")


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("extraInfo", ctypes.c_void_p),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("extraInfo", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", POINT),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)

# CallNextHookEx 的 lParam 是 64 位指针：不声明 argtypes 时 ctypes 按 c_int 传，
# 实测直接抛 OverflowError: int too long to convert ——回调因此没把事件放行，
# 低级钩子不往下传就等于**吞掉号主的这次输入**。必须显式声明原型。
user32.CallNextHookEx.argtypes = (wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_long
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.UnhookWindowsHookEx.argtypes = (wt.HHOOK,)
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, wt.INT)
user32.SendMessageTimeoutW.argtypes = (
    wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
    ctypes.POINTER(ctypes.c_size_t),
)
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t

# 回调对象必须一直活着：被 GC 掉之后系统还在往已释放的桩里调，直接崩
_callbacks: list = []
_hooks: list = []
_events: deque = deque(maxlen=200000)
_samples: list = []
_mems: list = []
_errors: list = []
_shared: dict = {"hwnd": 0}
_stop = threading.Event()


# --------------------------------------------------------------------- 采集
def _mouse_proc(n_code, w_param, l_param):
    try:
        if n_code >= 0:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            flags = int(info.flags)
            _events.append({
                "t": time.time(),
                "kind": "mouse",
                "msg": MOUSE_MSGS.get(int(w_param), f"msg{int(w_param):#x}"),
                "x": int(info.pt.x),
                "y": int(info.pt.y),
                "injected": bool(flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)),
            })
    except Exception as exc:  # noqa: BLE001 - 解析失败要计数，但绝不能挡住放行
        _errors.append(f"mouse: {type(exc).__name__}: {exc}")
    # 放行必须在 try 之外：异常一旦从这里冒出，这次输入就被钩子吞掉了
    return user32.CallNextHookEx(0, n_code, w_param, l_param)


def _key_proc(n_code, w_param, l_param):
    try:
        if n_code >= 0:
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            # 故意不记 vkCode：这是全局键盘钩子，把键码落盘就等于在号主机器上
            # 存了一份能还原密码的按键流水。本工具只需要知道「有按键注入、大致何时」。
            _events.append({
                "t": time.time(),
                "kind": "key",
                "msg": "key_down" if int(w_param) in (WM_KEYDOWN, WM_SYSKEYDOWN) else "key_up",
                "injected": bool(int(info.flags) & LLKHF_INJECTED),
            })
    except Exception as exc:  # noqa: BLE001
        _errors.append(f"key: {type(exc).__name__}: {exc}")
    return user32.CallNextHookEx(0, n_code, w_param, l_param)


def _wechat_pids() -> set[int]:
    try:
        import psutil
    except Exception:  # noqa: BLE001 - 没装 psutil 就退化成「只看有标题的可见窗口」
        return set()
    return {p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() in ("weixin.exe", "wechat.exe")}


def find_wechat_hwnd() -> int:
    """当前有标题、可见的微信主窗口；多开时取面积最大的那个。"""
    pids = _wechat_pids()
    best, best_area = 0, 0
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lparam):
        nonlocal best, best_area
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pids and pid.value not in pids:
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if not title.value:
            return True  # 渲染子窗口/隐藏窗口没有标题，跳过
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        if area > best_area:
            best, best_area = int(hwnd), area
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return best


def record_mem() -> None:
    """内存水位 + 微信/服务的常驻内存（另一种解释：整机在页文件上抖）。"""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return
    vm = psutil.virtual_memory()
    row = {"t": time.time(),
           "avail_gb": round(vm.available / 1024 ** 3, 2),
           "percent": vm.percent}
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info["cmdline"] or [])
            rss = round(proc.memory_info().rss / 1024 ** 2)
            if name in ("weixin.exe", "wechat.exe"):
                row["wechat_mb"] = max(row.get("wechat_mb", 0), rss)
            elif "app.main" in cmd:
                row["agent_mb"] = max(row.get("agent_mb", 0), rss)
        except Exception:  # noqa: BLE001 - 进程随时可能退出
            continue
    _mems.append(row)


def sampler(interval: float = 0.2) -> None:
    """每 0.2s 探一次微信消息循环是否在应答。

    这里只做 SendMessageTimeout 一件事：找句柄要遍历全部进程（psutil 一次几百毫秒），
    放进来会把探测拖成半秒级，卡住的短尖峰就抓不到——所以句柄由 hwnd_refresher 备好。
    """
    while not _stop.is_set():
        hwnd = _shared["hwnd"]
        if hwnd:
            res = ctypes.c_size_t(0)
            ok = bool(user32.SendMessageTimeoutW(
                hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(res)))
            _samples.append({"t": time.time(), "ok": ok, "hwnd": hwnd})
        time.sleep(interval)


def hwnd_refresher(interval: float = 2.0) -> None:
    """微信会被号主重启，句柄每 2s 重找一次；找不到就保留旧值下轮再试。"""
    while not _stop.is_set():
        found = find_wechat_hwnd()
        if found:
            _shared["hwnd"] = found
        _stop.wait(interval)


def mem_sampler(interval: float = 5.0) -> None:
    """内存水位单独一条线程：psutil 遍历全部进程要几百毫秒到几秒，
    混在探测线程里会把 0.2s 的应答探测拖成半秒级，卡住的短尖峰就抓不到了。"""
    while not _stop.is_set():
        record_mem()
        _stop.wait(interval)


# --------------------------------------------------------------------- 判定（纯函数）
def find_swallowed_clicks(events, move_threshold: int = 6, up_window: float = 1.5):
    """找出「硬件按下与硬件抬起之间，被注入事件把光标挪走」的单击。

    mmui 列表项按 Qt 的常规判定：press 之后指针跑出容差再 release，就当成拖拽，
    不触发点击。所以真人明明只动了一点点，只要中间有一次注入的 ``SetCursorPos``
    把光标甩到别处，这次单击就整条被吃掉。``followed_by_dblclick`` 对应号主描述的
    那个细节：单击全无反应，双击照样能弹出独立会话窗口。
    """
    out = []
    pending = None  # (下标, 按下事件)
    evs = [e for e in events if e.get("kind") == "mouse"]
    for i, ev in enumerate(evs):
        msg = ev.get("msg")
        if msg == "left_down" and not ev.get("injected"):
            pending = (i, ev)
            continue
        if msg != "left_up" or pending is None or ev.get("injected"):
            continue
        down_i, down = pending
        pending = None
        if ev["t"] - down["t"] > up_window:
            continue
        worst, worst_dev = None, 0
        for other in evs[down_i + 1:i]:
            if not other.get("injected") or other.get("msg") not in MOVING_MSGS:
                continue
            dev = abs(other["x"] - down["x"]) + abs(other["y"] - down["y"])
            if dev > worst_dev:
                worst_dev, worst = dev, other
        if worst is None or worst_dev <= move_threshold:
            continue
        out.append({
            "down": down,
            "up": ev,
            "injected": worst,
            "deviation": worst_dev,
            "followed_by_dblclick": any(
                e.get("msg") == "left_dblclk"
                and down["t"] < e["t"] <= ev["t"] + 0.6
                and not e.get("injected")
                for e in evs
            ),
        })
    return out


def unresponsive_spans(samples, gap_limit: float = 1.0):
    """把连续「微信没应答」的探测合并成区间；探测间隔 0.2s，允许 1s 的空档。"""
    spans, cur, last_t = [], None, 0.0
    for s in samples:
        if s["ok"]:
            if cur:
                spans.append(cur)
                cur = None
            last_t = s["t"]
            continue
        if cur and s["t"] - last_t > gap_limit:
            spans.append(cur)
            cur = None
        cur = cur or {"start": s["t"], "end": s["t"], "misses": 0}
        cur["end"] = s["t"]
        cur["misses"] += 1
        last_t = s["t"]
    if cur:
        spans.append(cur)
    return spans


def summarize(events, samples, mems, swallowed, spans) -> str:
    """把三条线索汇成一段能直接读的结论（也方便贴给别人看）。"""
    hard = [e for e in events if e.get("kind") == "mouse" and not e.get("injected")]
    inj = [e for e in events if e.get("injected")]
    lines = [
        f"采样 {len(samples)} 次微信应答探测，失败 {sum(1 for s in samples if not s['ok'])} 次",
        f"输入事件 {len(events)} 条：硬件鼠标 {len(hard)} 条，注入（机器人/软件）{len(inj)} 条",
        f"被吞掉的单击：{len(swallowed)} 次"
        + (f"，其中 {sum(1 for s in swallowed if s['followed_by_dblclick'])} 次紧跟着一次双击"
           if swallowed else ""),
    ]
    if spans:
        longest = max(s["end"] - s["start"] for s in spans)
        lines.append(f"微信消息循环卡住：{len(spans)} 段，最长 {longest:.1f}s")
    if mems:
        lines.append(
            f"内存最低点：可用 {min(m['avail_gb'] for m in mems):.2f} GB，"
            f"占用最高 {max(m['percent'] for m in mems):.0f}%"
        )
    for s in swallowed[:10]:
        at = time.strftime("%H:%M:%S", time.localtime(s["down"]["t"]))
        lines.append(
            f"  {at} 单击 ({s['down']['x']},{s['down']['y']}) 之间被注入事件甩到 "
            f"({s['injected']['x']},{s['injected']['y']})，偏离 {s['deviation']}px"
            + ("，随后补了一次双击" if s["followed_by_dblclick"] else "")
        )
    if swallowed and not spans:
        lines.append("判定：不是微信卡死，是输入被注入事件抢走 —— 该限定驱动的界面操作。")
    elif spans and not swallowed:
        lines.append("判定：微信消息循环真的停过，且与被吞的单击无关 —— 往 UIA/内存方向查。")
    elif swallowed and spans:
        lines.append("判定：两件事同时存在，按上面的时间戳看谁先出现。")
    else:
        lines.append("判定：这一轮没抓到异常，请在复现的那一刻继续跑。")
    return "\n".join(lines)


# --------------------------------------------------------------------- 主流程
def install_hooks() -> None:
    _callbacks.append(HOOKPROC(_mouse_proc))
    _callbacks.append(HOOKPROC(_key_proc))
    hh_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, _callbacks[0], 0, 0)
    hh_key = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _callbacks[1], 0, 0)
    if not hh_mouse or not hh_key:
        raise SystemExit(
            f"低级钩子装不上（GetLastError={kernel32.GetLastError()}）；"
            "低级钩子要有交互桌面会话，别在服务/计划任务里跑"
        )
    _hooks.extend([hh_mouse, hh_key])


def remove_hooks() -> None:
    for hh in _hooks:
        try:
            user32.UnhookWindowsHookEx(hh)
        except Exception:  # noqa: BLE001
            pass
    _hooks.clear()


def dump(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = ([{"type": "event", **e} for e in _events]
            + [{"type": "probe", **s} for s in _samples]
            + [{"type": "mem", **m} for m in _mems])
    out_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    _utf8_console()
    seconds = float(argv[1]) if len(argv) > 1 else 0.0
    out_path = Path(__file__).resolve().parent.parent / "data" / "wx_click_watch.jsonl"
    install_hooks()
    threading.Thread(target=hwnd_refresher, daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=mem_sampler, daemon=True).start()
    print(f"正在监听（Ctrl+C 结束并出结论）。事件流写到：{out_path}")
    msg = MSG()
    started = time.time()
    try:
        while not _stop.is_set():
            # 低级钩子靠消息泵维持：不泵消息，系统会认定钩子超时并把它踢掉
            while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if seconds and time.time() - started >= seconds:
                break
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        remove_hooks()
    swallowed = find_swallowed_clicks(list(_events))
    spans = unresponsive_spans(_samples)
    dump(out_path)
    if _errors:
        print(f"⚠ 回调解析失败 {len(_errors)} 次，事件可能没记全；首个错误：{_errors[0]}")
    print(summarize(list(_events), _samples, _mems, swallowed, spans))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
