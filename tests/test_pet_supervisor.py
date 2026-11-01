"""与上游 Helper 的协议契约，以及 supervisor 的投递与降级。

判据全部来自上游 helper.py 的实测行为，不来自推测。
素材由 tools/pet_assets.py 从校验过 sha1 的官方包装配而来，落在 data/pet/。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.pet import assets, protocol


def _helper() -> Path:
    """优先用装配产物；没有就退回子模块。

    headless 只解析 stdin、不读帧，所以未装配的新检出也该能跑这条契约测试，
    不该被「先执行装配」挡在门外。
    """
    for candidate in (assets.helper_script(),
                      Path("vendor/dsh-feiyu-pet/runtime/helper.py")):
        if candidate.is_file():
            return candidate
    pytest.fail(
        "找不到上游 helper.py：先 git submodule update --init --depth 1 vendor/dsh-feiyu-pet，"
        "或跑 python tools/pet_assets.py 装配到 data/pet/。"
        "契约测试必须真跑上游进程，不允许静默跳过。"
    )


def _pump(messages: list[dict], event_log: Path) -> subprocess.CompletedProcess:
    payload = "".join(protocol.encode(m) for m in messages)
    return subprocess.run(
        [sys.executable, str(_helper()), "--headless", "--event-log", str(event_log)],
        input=payload, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_headless_accepts_protocol_and_exits_cleanly(tmp_path):
    log = tmp_path / "events.jsonl"
    proc = _pump([
        {"kind": "config", "scale": 1.0, "bubbleMode": "always", "bubbleStates": ["ERROR"]},
        {"kind": "state", "state": "THINKING", "message": "在想事情", "detail": "小组"},
        {"kind": "pulse", "state": "SUCCESS", "ttlMs": 2000, "resumeState": "IDLE",
         "message": "这条回出去了"},
        {"kind": "ping"},
        {"kind": "shutdown"},
    ], log)
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert replies[0]["kind"] == "ready"
    assert replies[0]["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert any(r["kind"] == "pong" for r in replies)
    recorded = [json.loads(line)["kind"] for line in log.read_text("utf-8").splitlines()]
    # 实测：headless 先落盘再判 ping/shutdown，所以五条都会留下（写计划时以为只有三条）
    assert recorded == ["config", "state", "pulse", "ping", "shutdown"]


def test_invalid_state_rejected_and_not_recorded(tmp_path):
    log = tmp_path / "events.jsonl"
    proc = _pump([{"kind": "state", "state": "BROWSING"}, {"kind": "shutdown"}], log)
    assert proc.returncode == 0
    errors = [json.loads(line) for line in proc.stdout.splitlines()
              if json.loads(line).get("kind") == "error"]
    assert errors, "非法状态必须被上游拒绝"
    recorded = [json.loads(line) for line in log.read_text("utf-8").splitlines() if line.strip()]
    # 上游 headless 在校验通过后才落盘：被拒那条不进日志，合法的 shutdown 进
    assert [m["kind"] for m in recorded] == ["shutdown"]
    assert all(m.get("state") != "BROWSING" for m in recorded)


def test_missing_protocol_version_is_rejected(tmp_path):
    """这条锁住整个设计前提：没有 protocolVersion 就一条都收不到。"""
    log = tmp_path / "events.jsonl"
    proc = subprocess.run(
        [sys.executable, str(_helper()), "--headless", "--event-log", str(log)],
        input='{"kind":"state","state":"IDLE"}\n{"kind":"shutdown"}\n',
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert "unsupported protocol version" in proc.stdout
    assert not log.exists() or not log.read_text("utf-8").strip()


def test_closed_stdin_ends_process_without_shutdown():
    proc = subprocess.Popen([sys.executable, str(_helper()), "--headless"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8")
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0        # 上游语义：stdin 关闭即退出


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None


class _FakeProc:
    returncode = None

    def __init__(self) -> None:
        self.stdin = _FakeStdin()


def _make_supervisor(monkeypatch):
    from app.pet import supervisor

    sup = supervisor.PetSupervisor()
    proc = _FakeProc()
    sup.proc = proc
    sup.running = True
    monkeypatch.setattr(supervisor.settings, "section",
                        lambda *a, **kw: {"showChatName": False, "showChatDigest": False,
                                          "activityLevel": "auto", "soundStates": ["ERROR"],
                                          "bubbleStates": ["SUCCESS"], "bubbleMode": "always",
                                          "scale": 1.0, "bubbleScale": 1.0,
                                          "reducedMotion": False, "soundEnabled": False,
                                          "enabled": True})
    return sup, proc


def test_send_stamps_protocol_at_the_only_choke_point(monkeypatch):
    sup, proc = _make_supervisor(monkeypatch)
    asyncio.run(sup.send({"kind": "state", "state": "THINKING", "message": "在想事情"}))
    line = proc.stdin.written[-1]
    # 必须是 bytes：asyncio 子进程 stdin 是二进制流，写 str 会 TypeError（线上 500 的根因）
    assert isinstance(line, bytes), type(line)
    text = line.decode("utf-8")
    assert text.endswith("\n") and text.count("\n") == 1
    parsed = json.loads(text)
    assert parsed["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert parsed["state"] == "THINKING"


def test_start_without_bundle_is_graceful(monkeypatch):
    from app.pet import supervisor

    monkeypatch.setattr(supervisor.assets, "verify_bundle",
                        lambda *a, **kw: (False, "[missing] 未装配"))
    monkeypatch.setattr(supervisor.settings, "section", lambda *a, **kw: {"enabled": True})
    sup = supervisor.PetSupervisor()
    asyncio.run(sup.start())
    assert sup.proc is None
    assert sup.disabled_reason.startswith("[missing]")
    assert sup.status()["running"] is False


def test_real_spawn_without_qt_degrades_instead_of_hanging(monkeypatch):
    """真起一次子进程：缺 PySide6 时必须快速判定失败且不卡 lifespan。

    装了 Qt 就跳过——那会真的在桌面上弹窗，测试不该有副作用。
    """
    import importlib.util

    from app.pet import supervisor

    if importlib.util.find_spec("PySide6"):
        pytest.skip("环境里有 Qt，visual 模式会真弹窗，这条只在缺 Qt 时验降级")
    monkeypatch.setattr(supervisor.assets, "verify_bundle", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(supervisor.settings, "section", lambda *a, **kw: {"enabled": True})
    monkeypatch.setattr(supervisor.settings, "env_for_process", lambda *a, **kw: {})
    sup = supervisor.PetSupervisor()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(sup.start(), timeout=60))
    finally:
        loop.run_until_complete(sup.stop())
        loop.close()
    assert sup.running is False
    assert sup.disabled_reason.startswith("[spawn]")


def test_preview_uses_the_same_outlet_as_real_events(monkeypatch):
    """预览不许另开一条投递路径，否则「预览好看」和「真跑起来」会是两件事。"""
    from app.pet import supervisor

    sup, proc = _make_supervisor(monkeypatch)
    asyncio.run(sup.preview("ERROR", "控制台预览"))
    raw = proc.stdin.written[-1]
    assert isinstance(raw, bytes)
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["kind"] == "state" and parsed["state"] == "ERROR"
    assert parsed["message"] and parsed["protocolVersion"] == protocol.PROTOCOL_VERSION


def test_send_reaches_a_real_helper_and_stop_exits_zero(monkeypatch):
    """用真 helper 验投递：假 stdin 接受 str 会掩盖 bytes/str 不匹配（线上就是这么 500 的）。"""
    from app.pet import supervisor

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_helper()), "--headless",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        sup = supervisor.PetSupervisor()
        sup.proc = proc
        sup.running = True
        ready = await asyncio.wait_for(proc.stdout.readline(), 20)
        assert b"ready" in ready
        await sup.send({"kind": "ping"})
        reply = await asyncio.wait_for(proc.stdout.readline(), 20)
        assert b"pong" in reply, reply
        await sup.stop()                                  # 内部写 shutdown + 关 stdin
        assert await asyncio.wait_for(proc.wait(), 20) == 0

    asyncio.run(scenario())


def test_failed_background_task_is_not_silent(monkeypatch):
    """写入线程意外崩掉时，不能还对外说 running=True。"""
    from app.pet import supervisor

    async def boom():
        raise TypeError("data argument must be a bytes-like object")

    async def scenario():
        sup = supervisor.PetSupervisor()
        task = asyncio.create_task(boom())
        sup._watch(task)
        with contextlib.suppress(asyncio.CancelledError, TypeError):
            await task
        return sup

    sup = asyncio.run(scenario())
    assert sup.running is False
    assert sup.disabled_reason.startswith("[task]")


class _PongStdout:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def readline(self) -> bytes:
        return self.payload


class _HandshakeProc:
    returncode = None

    def __init__(self, payload: bytes) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _PongStdout(payload)


def test_handshake_requires_pong(monkeypatch):
    from app.pet import supervisor

    ok = supervisor.PetSupervisor()
    ok.proc = _HandshakeProc(b'{"kind":"pong"}\n')
    asyncio.run(ok._handshake())                     # 不抛即通过
    assert b'"kind": "ping"' in ok.proc.stdin.written[-1]

    bad = supervisor.PetSupervisor()
    bad.proc = _HandshakeProc(b'{"kind":"ready"}\n')
    with pytest.raises(RuntimeError):
        asyncio.run(bad._handshake())
