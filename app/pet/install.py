"""桌宠一键准备：装 PySide6-Essentials、装配素材、启用并拉起。

形态照 app/bili/setup.py：真实命令与输出写进日志文件，前端按 offset 增量读取，
刷新页面不丢输出，也不依赖 WebSocket。包名写死在代码里——即使是本机端点，
也不开一个"任意 pip install"的口子。
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import assets, settings
from .supervisor import supervisor

PIP_PACKAGE = "PySide6-Essentials"
STATE_PATH = assets.bundle_dir() / "install.json"
LOG_PATH = assets.bundle_dir() / "install.log"
MAX_LOG_BYTES = 200_000

_lock = asyncio.Lock()
_task: asyncio.Task | None = None
_running = False   # 同步标志：create_task 到任务真正抢到锁之间有空档，不能只靠 lock 判重


def _now() -> float:
    return time.time()


def _write_state(phase: str, error: str = "", step: str = "") -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"phase": phase, "error": error, "step": step, "updatedAt": _now()},
                   ensure_ascii=False),
        "utf-8",
    )


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def bundle_ready() -> bool:
    """轻量判定：只看装配记录在不在。完整校验留给 supervisor 启进程前做。"""
    return (assets.bundle_dir() / "assembly.json").is_file()


def _pyside_available() -> bool:
    importlib.invalidate_caches()   # 刚装完的包要让 finder 重新扫
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


def _log_size() -> int:
    try:
        return LOG_PATH.stat().st_size
    except OSError:
        return 0


def _run(cmd: list[str]) -> int:
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
        if "pip" in cmd and mirror:
            fh.write(f"# 使用镜像源：{mirror}\n")
        fh.flush()
        proc = subprocess.Popen(
            [str(c) for c in cmd],
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
                # pip 下载大 wheel 时可能几分钟没有任何输出，定期落一条心跳，避免日志看起来像卡死
                emit(f"…仍在运行（已 {time.time() - started:.0f}s，等待子进程输出）")
                continue
            if item is None:
                break
            emit(item, throttle=("%" in item or item.startswith("Downloading")))
        code = proc.wait()
        fh.write(f"[exit {code}] 用时 {time.time() - started:.1f}s\n")
        fh.flush()
    return code


def _assemble() -> None:
    """从子模块（或已放好的官方包）装配素材；耗时约 50 秒，在线程里跑。"""
    source = assets.resolve_source()
    assets.assemble(source, assets.bundle_dir(), minimal=False)


async def _pipeline() -> None:
    async with _lock:
        try:
            if not _pyside_available():
                _write_state("deps", step=f"pip install {PIP_PACKAGE}")
                cmd = [sys.executable, "-m", "pip", "install"]
                mirror = os.environ.get("PIP_INDEX_URL") or os.environ.get("PIP_INDEX")
                if mirror:
                    # --isolated：忽略用户全局 pip 配置里的 extra-index，
                    # 否则会从 pypi.org 拉 82MB 的 wheel，国内经常卡死
                    cmd.insert(3, "--isolated")
                    cmd += ["-i", mirror]
                cmd.append(PIP_PACKAGE)
                # 放线程里跑：pip 下载动辄几分钟，不能堵死事件循环（否则日志接口也没响应）
                if await asyncio.to_thread(_run, cmd) != 0:
                    raise RuntimeError(
                        "安装 PySide6-Essentials 失败，见 data/pet/install.log；"
                        "网络受限时可用 run.bat 启动（会带清华源）或先手动 pip install"
                    )
                if not _pyside_available():
                    raise RuntimeError("pip 已结束但仍导入不到 PySide6，检查是否装进了另一个解释器")
            if not bundle_ready():
                _write_state("assets", step="装配角色素材")
                await asyncio.to_thread(_assemble)
                if not bundle_ready():
                    raise RuntimeError("素材装配后仍缺 assembly.json，见 data/pet/install.log")
            _write_state("restart", step="启用并拉起桌宠")
            settings.save({"enabled": True})
            await supervisor.restart()
            _write_state("ok", step="就绪")
        except Exception as exc:  # noqa: BLE001 - 失败只落状态，交给前端显示
            _write_state("error", str(exc))


async def _guarded_pipeline() -> None:
    global _running
    try:
        await _pipeline()
    finally:
        _running = False


async def start_install() -> dict[str, Any]:
    global _task, _running
    offset = _log_size()
    if _running or _lock.locked():
        return {**status(), "ok": False, "error": "正在准备中，请稍候", "log_offset": offset}
    _running = True
    _task = asyncio.create_task(_guarded_pipeline())
    return {**status(), "ok": True, "started": True, "log_offset": offset}


def status() -> dict[str, Any]:
    data = _read_state()
    return {
        "phase": str(data.get("phase") or "idle"),
        "error": str(data.get("error") or ""),
        "step": str(data.get("step") or ""),
        "updatedAt": float(data.get("updatedAt") or 0),
        "depsReady": _pyside_available(),
        "assetsReady": bundle_ready(),
        "logOffset": _log_size(),
    }


async def log_since(offset: int = 0, max_bytes: int = MAX_LOG_BYTES) -> dict[str, Any]:
    data = _read_state()
    phase = str(data.get("phase") or "idle")
    size = _log_size()
    reset = offset < 0 or offset > size
    start = 0 if reset else offset
    text = ""
    if size:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            if reset:
                fh.seek(max(0, size - max_bytes))
                start = fh.tell()
            else:
                fh.seek(start)
            text = fh.read(max_bytes)
            start = fh.tell()
    return {
        "ok": True,
        "offset": start,
        "size": size,
        "reset": reset,
        "text": text,
        "phase": phase,
        "error": str(data.get("error") or ""),
        "done": phase in {"ok", "error", "idle"},
        "depsReady": _pyside_available(),
        "assetsReady": bundle_ready(),
    }


async def wait_idle() -> None:
    """给测试与关停用：等当前任务结束。"""
    task = _task
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
