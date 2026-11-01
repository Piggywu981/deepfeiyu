"""本地假模型服务：验证流式输出与 function calling 全链路（不消耗真实额度）。"""
from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def chunk(delta: dict, finish: str | None = None) -> bytes:
    return sse(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "model": "deepseek-chat",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def do_GET(self):  # noqa: N802
        data = json.dumps(
            {
                "object": "list",
                "data": [
                    {"id": "deepseek-chat", "object": "model"},
                    {"id": "deepseek-reasoner", "object": "model"},
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        tools = body.get("tools") or []
        model = body.get("model") or "deepseek-chat"
        thinking = (body.get("thinking") or {}).get("type") != "disabled"
        last = messages[-1] if messages else {}
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content") or ""
                break

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def send(data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()
            time.sleep(0.09 if "慢慢" in user_text else 0.02)

        def send_reasoning(text: str) -> None:
            if not thinking:
                return
            for piece in re.findall(r".{1,4}", text):
                send(chunk({"reasoning_content": piece}))

        # 已经执行过工具 → 汇总
        if tool_msgs and last.get("role") == "tool":
            try:
                result = json.loads(last.get("content") or "{}")
            except json.JSONDecodeError:
                result = {}
            send_reasoning("工具已经返回结果，我直接把关键信息转述给用户。")
            text = f"已经处理好了（{model}）：" + json.dumps(result, ensure_ascii=False)[:200]
            for piece in re.findall(r".{1,6}", text):
                send(chunk({"content": piece}))
            send(chunk({}, "stop"))
            send(b"data: [DONE]\n\n")
            return

        # 需要发消息 → 触发工具调用
        if tools and ("发消息" in user_text or "发条" in user_text or "告诉她" in user_text):
            send_reasoning("用户要发微信消息，我先确认收件人名称，再调用发送工具。")
            m = re.search(r"(?:给|向)([\u4e00-\u9fa5A-Za-z0-9·]{1,12}?)(?:发|说)", user_text)
            target = m.group(1) if m else "林晓"
            args = json.dumps({"to": target, "content": "收到，我稍后回复你～"}, ensure_ascii=False)
            send(
                chunk(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_fake_1",
                                "type": "function",
                                "function": {"name": "send_wechat_message", "arguments": ""},
                            }
                        ],
                    }
                )
            )
            for i in range(0, len(args), 12):
                send(
                    chunk(
                        {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": args[i : i + 12]}}
                            ]
                        }
                    )
                )
            send(chunk({}, "tool_calls"))
            send(b"data: [DONE]\n\n")
            return

        send_reasoning(
            "先理解用户的指令，判断这是查询还是操作；"
            "如果是查询就直接整理结果，如果是操作就确认目标后再调用工具。"
        )
        text = f"收到，我在。这条是 {model} 的本地演示回复。"
        if "总结" in user_text:
            text = "产品研发群今天的结论：登录限流已上线，20 并发下 P99 为 180ms，压测通过。"
        if "长回复" in user_text:
            text = "这是一段用于测试停止按钮的长回复。" * 20
        for piece in re.findall(r".{1,5}", text):
            send(chunk({"content": piece}))
        send(chunk({}, "stop"))
        send(
            sse(
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": {"prompt_tokens": 88, "completion_tokens": 24, "total_tokens": 112},
                }
            )
        )
        send(b"data: [DONE]\n\n")


if __name__ == "__main__":
    print("fake llm on http://127.0.0.1:8899")
    ThreadingHTTPServer(("127.0.0.1", 8899), Handler).serve_forever()
