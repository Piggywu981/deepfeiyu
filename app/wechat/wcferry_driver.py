"""WeChatFerry 本地 Hook 驱动（需微信 PC 3.9.x + wcferry）。

能力：收发消息、通讯录、群聊、发送文件、SQL 检索本地库。
限制：仅 Windows；微信版本必须匹配 wcferry 支持的 3.9.x。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .base import (
    DriverError,
    Message,
    WeChatDriver,
    format_pat_notice,
    parse_pat_notice,
    parse_revoke_notice,
)

log = logging.getLogger("wcferry")

MSG_TYPES = {
    1: "text",
    3: "image",
    34: "voice",
    42: "card",
    43: "video",
    47: "emoji",
    48: "location",
    49: "link",
    10000: "system",
    10002: "system",
}


class WcferryDriver(WeChatDriver):
    name = "wcferry"
    display_name = "WeChatFerry（本地 Hook）"
    supports_send_file = True
    supports_contacts = True

    def __init__(self, on_message=None, host: str = "127.0.0.1", port: int = 10086) -> None:
        super().__init__(on_message)
        self.host = host
        self.port = int(port)
        self.wcf = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._contacts: dict[str, str] = {}
        self._contacts_at = 0.0
        self._self_wxid = ""
        self._lock = threading.Lock()

    def start(self) -> None:
        try:
            try:
                from wcferry import Wcf  # type: ignore
            except ImportError:
                from wcf import Wcf  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise DriverError(
                "未安装 wcferry。请执行：pip install wcferry（需 Python 3.9-3.12 与微信 3.9.x）"
            ) from exc

        try:
            self.wcf = Wcf(host=self.host, port=self.port, debug=False)
        except TypeError:
            try:
                self.wcf = Wcf(self.host, self.port, False)
            except Exception as exc:
                raise DriverError(f"连接微信失败：{exc}") from exc
        except Exception as exc:  # 版本不匹配 / 未登录 / 微信未启动
            raise DriverError(
                f"连接微信失败（{self.host}:{self.port}）：{exc}。常见原因：微信版本不是 3.9.x"
                "（当前 4.x 不支持 Hook）、微信未登录、或 wcferry 与客户端版本不匹配。"
            ) from exc

        try:
            if hasattr(self.wcf, "enable_receiving_msg"):
                self.wcf.enable_receiving_msg()
        except Exception as exc:
            raise DriverError(f"开启消息接收失败：{exc}") from exc

        try:
            self._self_wxid = self.wcf.get_self_wxid()
        except Exception:
            self._self_wxid = ""

        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, name="wcferry-recv", daemon=True)
        self._thread.start()
        self._refresh_contacts()

    def stop(self) -> None:
        self._running = False
        try:
            if self.wcf and hasattr(self.wcf, "disable_receiving_msg"):
                self.wcf.disable_receiving_msg()
        except Exception:
            pass
        self.wcf = None

    def _refresh_contacts(self) -> None:
        if not self.wcf:
            return
        try:
            contacts = self.wcf.get_contacts() or []
            mapping = {}
            for c in contacts:
                wxid = c.get("wxid") or c.get("userName")
                name = c.get("name") or c.get("nickName") or wxid
                if wxid:
                    mapping[wxid] = name
            if mapping:
                with self._lock:
                    self._contacts = mapping
                    self._contacts_at = time.time()
        except Exception as exc:
            log.warning("拉取通讯录失败: %s", exc)

    def _name_of(self, wxid: str) -> str:
        if not wxid:
            return ""
        with self._lock:
            if wxid in self._contacts:
                return self._contacts[wxid]
        if time.time() - self._contacts_at > 600:
            self._refresh_contacts()
            with self._lock:
                return self._contacts.get(wxid, wxid)
        return wxid

    def _recv_loop(self) -> None:
        while self._running and self.wcf:
            try:
                try:
                    msg = self.wcf.get_msg(timeout=1)
                except TypeError:  # 老版本签名不支持 timeout
                    msg = self.wcf.get_msg()
            except Exception as exc:
                name = type(exc).__name__
                if "Empty" in name or "Timeout" in name:
                    continue
                log.warning("接收消息异常: %s", exc)
                time.sleep(1)
                continue
            if not msg:
                continue
            try:
                self._dispatch(msg)
            except Exception as exc:
                log.warning("消息解析失败: %s", exc)

    @staticmethod
    def _flag(msg: Any, name: str) -> bool:
        value = getattr(msg, name, False)
        try:
            return bool(value() if callable(value) else value)
        except Exception:
            return False

    def _dispatch(self, msg: Any) -> None:
        is_group = self._flag(msg, "is_group")
        is_self = self._flag(msg, "is_self")
        mtype = MSG_TYPES.get(getattr(msg, "type", 1), "other")
        content = getattr(msg, "content", "") or ""
        sender = getattr(msg, "sender", "") or ""

        if is_group:
            chat_id = getattr(msg, "roomid", "") or sender
            chat_name = self._name_of(chat_id)
            # 群消息内容形如 "wxid_xxx:\n正文"
            if ":\n" in content:
                head, _, body = content.partition(":\n")
                if head.startswith("wxid_") or len(head) < 40:
                    sender = head.strip()
                    content = body
            sender_name = self._name_of(sender)
        else:
            chat_id = sender
            chat_name = self._name_of(sender)
            sender_name = "我" if is_self else chat_name

        raw: dict[str, Any] = {"type": getattr(msg, "type", 1)}
        if mtype == "system":
            # 红包/入群等系统通知丢弃；撤回与拍一拍放行
            if parse_revoke_notice(content) is None:
                pat = parse_pat_notice(content)
                if pat is None:
                    return
                mtype = "pat"
                content, pat_info = format_pat_notice(pat, self._name_of, self._self_wxid)
                raw["pat"] = pat_info
                if pat_info["from_self"]:
                    is_self = True
                if not is_self and pat_info["from_name"]:
                    sender_name = pat_info["from_name"]

        message = Message(
            chat_id=chat_id,
            chat_name=chat_name or chat_id,
            sender_id=sender,
            sender_name=sender_name,
            content=content,
            is_group=is_group,
            is_self=is_self,
            msg_type=mtype,
            ts=float(getattr(msg, "ts", time.time()) or time.time()),
            msg_id=str(getattr(msg, "id", "") or "") or f"wcf_{int(time.time()*1000)}_{sender}",
            raw=raw,
        )
        if self.on_message:
            self.on_message(message)

    def send_text(self, chat_id: str, text: str) -> None:
        if not self.wcf:
            raise DriverError("微信未连接")
        ret = self.wcf.send_text(text, chat_id)
        if ret not in (0, None):
            raise DriverError(f"发送失败，返回码 {ret}")

    def send_file(self, chat_id: str, path: str) -> None:
        if not self.wcf:
            raise DriverError("微信未连接")
        ret = self.wcf.send_file(path, chat_id)
        if ret not in (0, None):
            raise DriverError(f"文件发送失败，返回码 {ret}")

    def send_emoji(self, chat_id: str, path: str) -> None:
        if not self.wcf:
            raise DriverError("微信未连接")
        sender = getattr(self.wcf, "send_emotion", None)
        if sender is None:
            raise DriverError("当前 wcferry 版本不支持发送表情")
        ret = sender(path, chat_id)
        if ret not in (0, None):
            raise DriverError(f"表情发送失败，返回码 {ret}")

    def list_chats(self, keyword: str = "", limit: int = 100) -> list[dict[str, Any]]:
        self._refresh_contacts()
        with self._lock:
            items = [{"id": k, "name": v, "is_group": False} for k, v in self._contacts.items()]
        if keyword:
            items = [i for i in items if keyword.lower() in i["name"].lower()]
        return items[:limit]

    def status(self) -> dict[str, Any]:
        connected = False
        account = ""
        try:
            connected = bool(self.wcf and self.wcf.is_login())
            if connected:
                info = self.wcf.get_user_info() or {}
                account = info.get("name") or info.get("wxid") or ""
        except Exception:
            connected = False
        return {
            "driver": self.name,
            "display_name": self.display_name,
            "connected": connected,
            "account": account,
            "detail": "已连接微信 PC 客户端" if connected else "未连接",
            "error": self.last_error,
            "mode": "hook",
        }
