"""「添加聊天中的表情包」开关：默认开启、关闭后不再自动收集、手动操作不受影响。

对应配置 ``ai.emoji_auto_collect``（设置 → AI 行为），以及设置页里的开关接线。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app import config
from app.wechat.manager import AccountSession

ROOT = Path(__file__).resolve().parent.parent


def _collect_into(calls: list):
    async def _collect(info, uid, chat_name):
        calls.append((info, uid, chat_name))

    return _collect


def _stub(enabled: bool, calls: list) -> SimpleNamespace:
    return SimpleNamespace(
        get_cfg=lambda: {"ai": {"emoji_auto_collect": enabled}},
        _tasks=set(),
        _collect_emoji=_collect_into(calls),
    )


def _trigger(stub: SimpleNamespace, force: bool = False) -> None:
    async def _go():
        # 按真实调用方式直接走 AccountSession 的方法（stub 提供 self 需要的属性）
        AccountSession.queue_emoji_collect(
            stub, {"url": "http://example.com/a.gif", "md5": "a" * 32}, "uid1", "甲", force=force
        )
        await asyncio.sleep(0)  # 让 create_task 排出的收集任务跑完

    asyncio.run(_go())


def test_default_config_auto_collect_is_on():
    assert config.DEFAULTS["ai"]["emoji_auto_collect"] is True


def test_queue_emoji_collect_skips_when_disabled():
    calls: list = []
    _trigger(_stub(False, calls))
    assert calls == []


def test_queue_emoji_collect_works_when_enabled():
    calls: list = []
    _trigger(_stub(True, calls))
    assert len(calls) == 1
    info, uid, chat_name = calls[0]
    assert info["url"] == "http://example.com/a.gif"
    assert (uid, chat_name) == ("uid1", "甲")


def test_manual_collect_ignores_switch():
    """控制台手动收藏/导入走 force=True，关闭开关也不影响。"""
    calls: list = []
    _trigger(_stub(False, calls), force=True)
    assert len(calls) == 1


def test_settings_toggle_wired_in_ui():
    html = (ROOT / "app" / "static" / "index.html").read_text("utf-8")
    js = (ROOT / "app" / "static" / "app.js").read_text("utf-8")
    assert 'id="fEmojiAutoCollect"' in html
    assert "添加聊天中的表情包" in html
    # 载入设置时回填、保存设置时回写
    assert 'fEmojiAutoCollect").checked = s.ai.emoji_auto_collect !== false' in js
    assert 'emoji_auto_collect: $("#fEmojiAutoCollect").checked' in js
