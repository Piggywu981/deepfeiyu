"""可选功能的安装状态机：pip 型固定命令 + 增量日志 + 全局单并发。

形态照 app/pet/install.py：真实命令与输出写进日志文件，前端按 offset 增量读取，
刷新页面不丢输出，也不依赖 WebSocket。命令由 features 注册表决定，本模块不接受
任何前端传入的包名或 pip 参数。

委托型（桌宠 / B 站）不重新实现安装，直接转给既有的一键流程；这里只负责把它们的
状态相映射成统一的 idle / installing / ok / error，并判断它们是否还在进行。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from importlib import invalidate_caches
from typing import Any

from .. import config
from ..bili import setup as bili_setup
from ..pet import install as pet_install
from . import features

DEPS_DIR = config.DATA_DIR / "deps"
STATE_PATH = DEPS_DIR / "install.json"
LOG_PATH = DEPS_DIR / "install.log"
MAX_LOG_BYTES = 200_000

# 委托型"进行中"的 phase：来自 app/pet/install.py 与 app/bili/setup.py 各自的状态机
PET_ACTIVE_PHASES = frozenset({"deps", "assets", "restart"})
BILI_ACTIVE_PHASES = frozenset({"copying", "venv", "deps", "mcp"})
PET_STEPS = {
    "deps": "安装 PySide6 依赖",
    "assets": "装配素材",
    "restart": "启用并拉起桌宠",
    "ok": "就绪",
}
BILI_STEPS = {
    "copying": "复制运行文件",
    "venv": "创建独立环境",
    "deps": "安装依赖",
    "mcp": "写入 MCP 配置",
    "ok": "就绪",
}

_lock = asyncio.Lock()
_task: asyncio.Task[Any] | None = None
_running = False            # 同步标志：create_task 到任务真正抢到锁之间有空档，不能只靠 lock 判重
_current: str | None = None  # 正在跑的 pip 功能 id


def _now() -> float:
    return time.time()


def _write_state(feature_id: str, phase: str, step: str = "", error: str = "") -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "feature": feature_id,
                "phase": phase,
                "step": step,
                "error": error,
                "updatedAt": _now(),
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _log_size() -> int:
    try:
        return LOG_PATH.stat().st_size
    except OSError:
        return 0


def _pip_step(feature: features.Feature) -> str:
    return f"pip install {feature.package or feature.requirements}"


def install_command(feature_id: str) -> list[str]:
    """构造 pip 命令（纯函数，便于测试）。

    包名、requirements 路径和所有 pip 参数都在服务端写死：前端只能传 feature id，
    传 package / pip_args 之类的字段不会进入命令，也不会被当成包名。
    --isolated 必须放在 install 之前；不加的话用户全局 pip 配置里的 extra-index
    仍会生效，国内走镜像时经常从 pypi.org 拉大 wheel 卡死。
    """
    feature = features.get(feature_id)
    if feature is None:
        raise KeyError(f"未知功能：{feature_id}")
    if feature.kind != "pip" or not (feature.package or feature.requirements):
        raise ValueError(f"「{feature.label}」走既有一键流程，不能用 pip 命令安装")
    cmd = [sys.executable, "-m", "pip", "install"]
    mirror = os.environ.get("PIP_INDEX_URL") or os.environ.get("PIP_INDEX")
    if mirror:
        cmd.insert(3, "--isolated")
        cmd += ["-i", mirror]
    if feature.package:
        cmd.append(feature.package)
    else:
        cmd += ["-r", str(config.ROOT / feature.requirements)]
    return cmd


def _run(cmd: list[str]) -> int:
    """跑 pip 子进程，把输出流式追加到日志；实现思路同 app/pet/install.py。"""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    started = time.time()
    last_progress = 0.0
    last_line = ""
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        def emit(text: str, throttle: bool = False) -> None:
            nonlocal last_progress, last_line
            line = text.strip()
            if not line or line == last_line:
                return
            if throttle:
                now = time.time()
                if now - last_progress < 2.0:
                    return
                last_progress = now
            last_line = line
            fh.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
            fh.flush()

        fh.write(f"$ {' '.join(map(str, cmd))}\n")
        mirror = os.environ.get("PIP_INDEX_URL") or os.environ.get("PIP_INDEX")
        if mirror:
            fh.write(f"# 使用镜像源：{mirror}\n")
        fh.flush()
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(config.ROOT),   # requirements 里的相对路径按仓库根解析
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def _pump() -> None:
            try:
                while True:
                    chunk = proc.stdout.read1(4096)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", "replace")
                    for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                        lines.put(part)
            finally:
                lines.put(None)

        threading.Thread(target=_pump, daemon=True).start()
        while True:
            try:
                item = lines.get(timeout=10)
            except queue.Empty:
                # pip 下载大 wheel 时可能几分钟没有输出，定期落一条心跳，避免日志看起来像卡死
                emit(f"…仍在运行（已 {time.time() - started:.0f}s，等待子进程输出）")
                continue
            if item is None:
                break
            emit(item, throttle=("%" in item or item.startswith("Downloading")))
        code = proc.wait()
        fh.write(f"[exit {code}] 用时 {time.time() - started:.1f}s\n")
        fh.flush()
    return code


def _running_feature() -> str | None:
    return _current if _running else None


def _is_running(feature_id: str) -> bool:
    """进程内是否真的在跑这个功能的 pip 安装（判断 state 文件是否陈旧时用）。"""
    return bool(_running and _current == feature_id)


def _delegated_busy() -> str | None:
    """看既有一键流程是否还在进行：它们自己的 status 是唯一真相。"""
    for feature_id, active, module in (
        ("pet", PET_ACTIVE_PHASES, pet_install),
        ("bili", BILI_ACTIVE_PHASES, bili_setup),
    ):
        try:
            phase = str((module.status() or {}).get("phase") or "")
        except Exception:  # noqa: BLE001 - 状态读不到就不算忙
            continue
        if phase in active:
            return feature_id
    return None


def busy_feature() -> str | None:
    """任一功能正在安装时返回其 id；都没有则 None。全局同时只允许一个安装。"""
    return _running_feature() or _delegated_busy()


def installing_feature() -> str | None:
    """状态接口的 installing 字段：正在跑的 pip 功能，或进行中的委托功能。"""
    return _running_feature() or _delegated_busy()


def _busy_reply(feature_id: str) -> dict[str, Any]:
    label = features.label_of(busy_feature() or "")
    return {
        "ok": False,
        "started": False,
        "feature": feature_id,
        "error": f"正在安装 {label}，请稍候",
        "log_offset": 0,
    }


async def _delegate(feature: features.Feature) -> dict[str, Any]:
    """委托型：把既有一键流程的 ok/error/log_offset 透传，补上统一的 started 字段。"""
    if feature.delegate == "pet":
        out = await pet_install.start_install()
    elif feature.delegate == "bili":
        out = await bili_setup.start_setup()
    else:  # pragma: no cover - 注册表写错才会走到
        raise ValueError(f"未知委托流程：{feature.delegate}")
    ok = bool(out.get("ok"))
    return {
        "ok": ok,
        "started": bool(out.get("started", ok)),
        "feature": feature.id,
        "log_offset": int(out.get("log_offset") or 0),
        "error": str(out.get("error") or ""),
    }


async def _pipeline(feature_id: str) -> None:
    feature = features.get(feature_id)
    if feature is None:
        return
    async with _lock:
        step = _pip_step(feature)
        try:
            _write_state(feature.id, "installing", step=step)
            cmd = install_command(feature.id)
            # 放线程里跑：pip 下载动辄几分钟，堵死事件循环的话日志接口也没响应
            code = await asyncio.to_thread(_run, cmd)
            if code != 0:
                raise RuntimeError(
                    f"安装「{feature.label}」失败（退出码 {code}），见 data/deps/install.log"
                )
            invalidate_caches()   # 刚装完的包要让 finder 重新扫，否则复检仍是旧结果
            missing = features.missing_modules(feature)
            if missing:
                raise RuntimeError(
                    "pip 已结束但仍缺少模块："
                    + "、".join(missing)
                    + "，检查是否装进了另一个解释器"
                )
            _write_state(feature.id, "ok", step="就绪")
        except Exception as exc:  # noqa: BLE001 - 失败只落状态，交给前端显示
            _write_state(feature.id, "error", step=step, error=str(exc))


async def _guarded_pipeline(feature_id: str) -> None:
    global _running, _current
    try:
        await _pipeline(feature_id)
    finally:
        _running = False
        _current = None


async def start_install(feature_id: str) -> dict[str, Any]:
    global _task, _running, _current
    feature = features.get(feature_id)
    if feature is None:
        raise KeyError(f"未知功能：{feature_id}")
    busy = busy_feature()
    # 委托型自己也在管并发：同一功能交给它的 start 返回原文，别的功能 busy 才在这里挡
    if busy is not None and (feature.kind == "pip" or busy != feature.id):
        return _busy_reply(feature.id)
    if feature.kind == "delegated":
        return await _delegate(feature)
    offset = _log_size()
    _running = True
    _current = feature.id
    _task = asyncio.create_task(_guarded_pipeline(feature.id))
    return {"ok": True, "started": True, "feature": feature.id, "log_offset": offset, "error": ""}


def _map_delegated(raw: Any, active: frozenset[str], steps: dict[str, str]) -> tuple[str, str]:
    phase = str(raw or "idle")
    if phase in active:
        return "installing", steps.get(phase, "")
    if phase == "ok":
        return "ok", steps.get("ok", "就绪")
    if phase == "error":
        return "error", ""
    return "idle", ""


def phase_of(feature: features.Feature) -> dict[str, Any]:
    """该功能最近一次安装的 phase / step / error（统一为 idle|installing|ok|error）。"""
    if feature.kind == "pip":
        data = _read_state()
        # 只有 state 文件记录的就是这个功能才取；否则是别的功能装过，与它无关
        if str(data.get("feature") or "") != feature.id:
            return {"phase": "idle", "step": "", "error": ""}
        phase = str(data.get("phase") or "idle")
        step = str(data.get("step") or "")
        error = str(data.get("error") or "")
        if phase == "installing" and not _is_running(feature.id):
            # 安装中进程退出（重启/崩溃）会把 installing 永远留在文件里，而内存里的
            # _running 已经没了；不归一化的话前端看到 phase=installing 会一直禁用按钮，
            # 用户只能手动删 state 文件。归一成可重试的失败态，step 保留用于排查。
            return {"phase": "error", "step": step,
                    "error": "上次安装在进程退出时中断，可重新安装"}
        return {"phase": phase, "step": step, "error": error}
    if feature.delegate == "pet":
        try:
            status = pet_install.status()
        except Exception:  # noqa: BLE001
            return {"phase": "idle", "step": "", "error": ""}
        phase, step = _map_delegated(status.get("phase"), PET_ACTIVE_PHASES, PET_STEPS)
        error = ""
        if phase == "error":
            error = str(status.get("error") or "")
            step = str(status.get("step") or "")
        return {"phase": phase, "step": step, "error": error}
    # bili
    try:
        status = bili_setup.status()
    except Exception:  # noqa: BLE001
        return {"phase": "idle", "step": "", "error": ""}
    phase, step = _map_delegated(status.get("phase"), BILI_ACTIVE_PHASES, BILI_STEPS)
    error = str(status.get("error") or "") if phase == "error" else ""
    return {"phase": phase, "step": step, "error": error}


def _default_feature_id() -> str:
    """未指定 feature 时取当前/最近：进行中的功能 > 最近一次安装的功能 > 第一个功能。"""
    running = installing_feature()
    if running and features.get(running):
        return running
    last = str(_read_state().get("feature") or "")
    if features.get(last):
        return last
    return features.FEATURES[0].id


def _pip_log_since(offset: int, max_bytes: int = MAX_LOG_BYTES) -> dict[str, Any]:
    """增量读 install.log：offset 越界或为负数时 reset，从文件尾部截取。"""
    size = _log_size()
    reset = offset < 0 or offset > size
    start = 0 if reset else offset
    text = ""
    if size:
        try:
            with LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
                if reset:
                    fh.seek(max(0, size - max_bytes))
                    start = fh.tell()
                else:
                    fh.seek(start)
                text = fh.read(max_bytes)
                start = fh.tell()
        except OSError:
            return {"offset": 0, "size": size, "reset": True, "text": ""}
    return {"offset": start, "size": size, "reset": reset, "text": text}


async def _delegated_log(feature: features.Feature, offset: int) -> dict[str, Any]:
    """委托型直接把既有流程的日志文本原样返回，只在开头补一行来源说明。"""
    handler = pet_install.log_since if feature.delegate == "pet" else bili_setup.log_since
    try:
        raw = await handler(offset=offset, max_bytes=MAX_LOG_BYTES)
    except Exception as exc:  # noqa: BLE001 - 读日志失败不该把轮询打断成 500
        return {"offset": int(offset or 0), "size": 0, "reset": False,
                "text": f"读取安装日志失败：{exc}\n"}
    text = str(raw.get("text") or "")
    reset = bool(raw.get("reset"))
    start = int(raw.get("offset") or 0)
    size = int(raw.get("size") or 0)
    if feature.log_source and text and (reset or offset <= 0):
        # 来源说明只给人看；不计入 offset/size，否则前端把返回的 offset 传回来会重复计数
        text = f"— 来源：{feature.log_source} —\n{text}"
    return {"offset": start, "size": size, "reset": reset, "text": text}


async def log_since(feature_id: str | None = None, offset: int = 0) -> dict[str, Any]:
    """增量读取某个功能的安装日志；安装没在跑时也能读最近一次。"""
    resolved = feature_id or _default_feature_id()
    feature = features.get(resolved)
    if feature is None:
        raise KeyError(f"未知功能：{resolved}")
    info = phase_of(feature)
    if feature.kind == "pip":
        raw = _pip_log_since(offset)
    else:
        raw = await _delegated_log(feature, offset)
    return {
        "ok": True,
        "feature": feature.id,
        "phase": info["phase"],
        "step": info["step"],
        "error": info["error"],
        "done": info["phase"] in {"idle", "ok", "error"},
        "offset": raw["offset"],
        "size": raw["size"],
        "reset": raw["reset"],
        "text": raw["text"],
    }


async def wait_idle() -> None:
    """给测试与关停用：等当前 pip 任务结束。"""
    task = _task
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
