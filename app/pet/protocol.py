"""上游 Helper 的 stdin 协议信封。

上游 parse_message 对每条消息都要求 protocolVersion == 1，缺了就整条拒收
（实测报 "unsupported protocol version"）。所以版本号只在写 stdin 的那一个点
统一盖章：reducer / bubble / settings 产出的是领域消息，不该各自记着信封字段，
否则漏一处的后果是「单元测试全绿、桌宠一动不动」。
"""
from __future__ import annotations

from typing import Any

PROTOCOL_VERSION = 1
STATES = ("IDLE", "THINKING", "WORKING", "WAITING", "SUCCESS", "ERROR", "DISCONNECTED")
MESSAGE_KINDS = ("state", "pulse", "task", "tasks", "config", "balance", "alert", "ping", "shutdown")


def wrap(message: dict[str, Any]) -> dict[str, Any]:
    """盖上游信封；已有 protocolVersion 的消息保持原值（便于回放人工构造的消息）。"""
    return {"protocolVersion": PROTOCOL_VERSION, **message}


def encode(message: dict[str, Any]) -> str:
    """一行 JSONL 文本。给 text=True 的 subprocess.run 与测试用。"""
    import json

    return json.dumps(wrap(message), ensure_ascii=False) + "\n"


def encode_bytes(message: dict[str, Any]) -> bytes:
    """写 asyncio 子进程 stdin 必须用 bytes：那是二进制流，write(str) 直接 TypeError。

    线上就是这么坏的：writer 任务在第一条 config 上静默死掉，界面却显示「运行中」，
    只有点预览时才以 500 的形式暴露。
    """
    return encode(message).encode("utf-8")
