"""README 与使用说明不能否认自己提供的通道。"""
from pathlib import Path

import pytest

README = Path("README.md").read_text(encoding="utf-8")
USAGE = Path("docs/使用说明.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("text,where", [(README, "README.md"), (USAGE, "docs/使用说明.md")])
def test_weixin4_is_not_denied(text, where):
    # :63 曾写「不能作为本应用的驱动」，而通道表里 weixin4 是一等通道，
    # 4.x 用户照那段读会直接放弃主用驱动
    assert "不能作为本应用的驱动" not in text
    assert "暂无通用 Hook，需走 Webhook 网关" not in text


def test_channel_table_lists_all_drivers():
    for driver in ("wcferry", "weixin4", "webhook"):
        assert f"`{driver}`" in README


def test_send_gate_is_documented():
    assert "forbidden_wxids" in USAGE
    assert "写操作只允许本机" in USAGE
