from __future__ import annotations

import json

from app.pet import protocol


def test_wrap_stamps_version():
    assert protocol.wrap({"kind": "state", "state": "IDLE"}) == {
        "protocolVersion": 1, "kind": "state", "state": "IDLE",
    }


def test_wrap_keeps_explicit_version():
    got = protocol.wrap({"protocolVersion": 7, "kind": "ping"})
    assert got["protocolVersion"] == 7


def test_encode_is_one_jsonl_line():
    line = protocol.encode({"kind": "state", "state": "THINKING", "message": "在想事情"})
    assert line.endswith("\n") and line.count("\n") == 1
    parsed = json.loads(line)
    assert parsed["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert parsed["message"] == "在想事情"   # 中文不许被转义成 \\uXXXX


def test_states_and_kinds_match_upstream():
    # 判据抄自上游 helper.py 第 34-35 行的 STATES 与 apply_message 分派
    assert protocol.STATES == (
        "IDLE", "THINKING", "WORKING", "WAITING", "SUCCESS", "ERROR", "DISCONNECTED",
    )
    assert set(protocol.MESSAGE_KINDS) == {
        "state", "pulse", "task", "tasks", "config", "balance", "alert", "ping", "shutdown",
    }


def test_encode_bytes_is_utf8_bytes_with_one_line():
    raw = protocol.encode_bytes({"kind": "state", "state": "IDLE", "message": "待着"})
    assert isinstance(raw, bytes) and raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert json.loads(raw.decode("utf-8"))["message"] == "待着"
