"""Webhook 驱动：把任意外部微信网关（协议服务 / 官方号 / 自建 Hook 服务）接进来。

入站：POST /api/webhook/inbound  （带 token）
出站：POST 到 settings.webhook.callback_url，JSON:
      {"action":"send_text","to":"<chat_id>","content":"...","token":"..."}
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from .base import (
    DriverError,
    Message,
    WeChatDriver,
    format_pat_notice,
    parse_pat_notice,
    strip_sender_prefix,
)


class WebhookDriver(WeChatDriver):
    name = "webhook"
    display_name = "Webhook 网关"
    supports_send_file = False
    supports_contacts = True

    def __init__(
        self,
        on_message=None,
        callback_url: str = "",
        token: str = "",
        account_id: str = "acc_default",
    ) -> None:
        super().__init__(on_message)
        self.callback_url = callback_url
        self.token = token
        self.account_id = account_id
        self._connected = False
        self._client: httpx.Client | None = None

    def start(self) -> None:
        self._client = httpx.Client(timeout=15)
        self._connected = True
        if not self.callback_url:
            self.last_error = "未配置出站回调地址，仅能接收消息"

    def stop(self) -> None:
        self._connected = False
        if self._client:
            self._client.close()
            self._client = None

    def status(self) -> dict[str, Any]:
        return {
            "driver": self.name,
            "display_name": self.display_name,
            "connected": self._connected,
            "account": "外部网关",
            "detail": f"出站 → {self.callback_url or '未配置'}",
            "error": self.last_error,
            "mode": "gateway",
        }

    def send_text(self, chat_id: str, text: str) -> None:
        if not self.callback_url:
            raise DriverError("未配置 webhook.callback_url，无法发送")
        payload = {"action": "send_text", "to": chat_id, "content": text, "token": self.token, "ts": time.time()}
        try:
            resp = (self._client or httpx).post(self.callback_url, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            raise DriverError(f"回调发送失败：{exc}") from exc

    def inject(self, data: dict[str, Any]) -> Message:
        chat_id = str(data.get("chat_id") or data.get("from") or "")
        if not chat_id:
            raise DriverError("缺少 chat_id")
        msg_type = str(data.get("msg_type") or "text")
        content = str(data.get("content") or "")
        sender_id = str(data.get("sender_id") or chat_id)
        provided_name = str(data.get("sender_name") or "").strip()
        sender_name = provided_name or str(data.get("chat_name") or chat_id)
        is_self = bool(data.get("is_self"))
        raw: dict[str, Any] = {}
        if msg_type in ("system", "pat") or "<pat" in content or 'type="pat"' in content:
            # 个别网关会把发送者前缀带两层，多剥一次
            for _ in range(2):
                stripped = strip_sender_prefix(content, sender_id, sender_name, chat_id)
                if stripped == content:
                    break
                content = stripped
            pat = parse_pat_notice(content)
            if pat:
                if not pat.get("from_name") and not pat.get("from_self") and provided_name:
                    # 网关单独给的发送者名比原始 wxid 更适合展示；没给就别拿群名硬凑
                    pat["from_name"] = provided_name
                content, pat_info = format_pat_notice(pat)
                msg_type = "pat"
                raw["pat"] = pat_info
                is_self = is_self or bool(pat_info.get("from_self"))
                pat_from = str(pat_info.get("from_name") or "")
                if not is_self:
                    if provided_name:
                        sender_name = provided_name
                    elif pat_from and pat_from != "有人":
                        # 没给显示名时宁可显示 wxid，也别把群名当发送者
                        sender_name = pat_from
                if pat_info.get("from_id"):
                    sender_id = str(pat_info["from_id"])
        msg = Message(
            chat_id=chat_id,
            chat_name=str(data.get("chat_name") or chat_id),
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            is_group=bool(data.get("is_group")),
            is_self=is_self,
            msg_type=msg_type,
            ts=float(data.get("ts") or time.time()),
            msg_id=str(data.get("msg_id") or uuid.uuid4().hex),
            raw=raw,
        )
        if self.on_message:
            self.on_message(msg)
        return msg
