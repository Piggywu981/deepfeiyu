"""多账号同时登录时，cfgDword 不能取到别的账号。

背景：wechatauto 的 `WeChatDB.extract_master_key()` 按 `_find_weixin_pids()` 的
顺序取第一个能读出 cfg 的 Weixin.exe。多开时先命中的往往是另一个账号的进程，
于是 `db.cfg_dword` 属于账号 A、`db.wxid` 属于账号 B。图片 AES 密钥由
`md5(str(cfgDword) + wxid)[:16]` 派生，两者不同源就永远验不过：每张图都掉进
120 秒内存扫描、每张图都 404（2026-11-03 本机真实现象）。

`app/wechat/diagnose.py:_patch_cfg_dword` 在加载 db.py 后把取 cfg 的顺序改成
「优先 wxid 与本账号一致的那个进程」。这里用假模块验补丁，不依赖真装
wechatauto、也不要求微信在线。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.wechat.diagnose import _patch_cfg_dword


def _fake_class():
    """每回造一个新类：补丁会把标记写在类上，复用同一个类会串测试。"""

    class FakeWeChatDB:
        def __init__(self, account: str, pids=(1, 2, 3)):
            self.account = account
            self._pids = list(pids)

        @property
        def wxid(self) -> str:
            return self.account.rsplit("_", 1)[0]

        def _find_weixin_pids(self):
            return self._pids

    return FakeWeChatDB


def _patched(table: dict):
    """table: {pid: (主密钥hex, cfgDword, cfg 里的 wxid)}"""
    mod = SimpleNamespace(
        WeChatDB=_fake_class(),
        extract_master_key_from_cfg=lambda pid: table.get(pid),
    )
    _patch_cfg_dword(mod)
    return mod


# ---------------------------------------------------------------- 多账号取对


def test_picks_the_pid_whose_cfg_belongs_to_this_account():
    # pid 11 是另一个账号（进程顺序在前），pid 22 才是本账号
    mod = _patched(
        {
            11: ("master_a", 0xAAAA, "wxid_a"),
            22: ("master_b", 0xBBBB, "wxid_b"),
        }
    )
    db = mod.WeChatDB("wxid_b_1234", pids=(11, 22))
    assert db.extract_master_key() == ("master_b", 0xBBBB, "wxid_b")


def test_result_is_not_simply_the_first_available_cfg():
    """负控：旧行为（取第一个）会拿到 0xAAAA；断言必须咬住这一点。"""
    mod = _patched(
        {
            11: ("master_a", 0xAAAA, "wxid_a"),
            22: ("master_b", 0xBBBB, "wxid_b"),
        }
    )
    db = mod.WeChatDB("wxid_b_1234", pids=(11, 22))
    assert db.extract_master_key()[1] != 0xAAAA


def test_account_match_wins_even_when_it_is_the_last_pid():
    mod = _patched(
        {
            1: ("m1", 1, "wxid_x"),
            2: ("m2", 2, "wxid_y"),
            3: ("m3", 3, "wxid_z"),
        }
    )
    db = mod.WeChatDB("wxid_z_9999", pids=(1, 2, 3))
    assert db.extract_master_key() == ("m3", 3, "wxid_z")


# ---------------------------------------------------------------- 兜底不变


def test_falls_back_to_first_readable_cfg_when_account_has_no_process():
    """本账号进程还没起来/读不出时退回旧行为，不凭空失败。"""
    mod = _patched({11: ("master_a", 0xAAAA, "wxid_a")})
    db = mod.WeChatDB("wxid_b_1234", pids=(11, 22))
    assert db.extract_master_key() == ("master_a", 0xAAAA, "wxid_a")


def test_single_account_behaviour_unchanged():
    mod = _patched({7: ("m", 0x1234, "wxid_only")})
    db = mod.WeChatDB("wxid_only_abcd", pids=(7,))
    assert db.extract_master_key() == ("m", 0x1234, "wxid_only")


def test_pids_without_cfg_are_skipped():
    mod = _patched({22: ("m", 0xBBBB, "wxid_b")})
    db = mod.WeChatDB("wxid_b_1234", pids=(11, 22))
    assert db.extract_master_key() == ("m", 0xBBBB, "wxid_b")


def test_extraction_error_on_one_pid_does_not_abort_scan():
    def extract(pid):
        if pid == 11:
            raise OSError("read failed")
        return ("m", 0xBBBB, "wxid_b")

    mod = SimpleNamespace(WeChatDB=_fake_class(), extract_master_key_from_cfg=extract)
    _patch_cfg_dword(mod)
    db = mod.WeChatDB("wxid_b_1234", pids=(11, 22))
    assert db.extract_master_key() == ("m", 0xBBBB, "wxid_b")


def test_no_process_yields_none():
    mod = _patched({})
    db = mod.WeChatDB("wxid_b_1234", pids=())
    assert db.extract_master_key() is None


# ---------------------------------------------------------------- 补丁本身


def test_patch_is_idempotent():
    mod = _patched({1: ("m", 1, "wxid_a")})
    first = mod.WeChatDB.extract_master_key
    _patch_cfg_dword(mod)
    assert mod.WeChatDB.extract_master_key is first
    assert getattr(mod.WeChatDB, "_wx4_patched_cfgdword", False)


def test_module_without_wechatdb_is_left_alone():
    _patch_cfg_dword(SimpleNamespace())  # 不该抛
    _patch_cfg_dword(SimpleNamespace(WeChatDB=object))  # 缺 extract 函数，同样跳过
    assert not getattr(object, "_wx4_patched_cfgdword", False)


def test_wx4_db_module_applies_patch():
    """真实加载 db.py 的入口（未安装 wechatauto 时跳过）。"""
    from app.wechat.diagnose import wx4_db_module

    mod = wx4_db_module()
    if mod is None:
        pytest.skip("本机未安装 wechatauto")
    assert getattr(mod.WeChatDB, "_wx4_patched_cfgdword", False) is True
