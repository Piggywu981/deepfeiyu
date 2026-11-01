"""sanitize_reply 的密钥打码：形似凭据的内容不能原样发到聊天里。"""
from app import rules


def test_masks_openai_style_key():
    text = rules.sanitize_reply("我的密钥是 sk-abc123def456ghi789jkl012，别告诉别人")
    assert "sk-abc123" not in text
    assert "＊＊＊" in text


def test_masks_env_var_assignment():
    text = rules.sanitize_reply("配置里 DEEPSEEK_API_KEY=sk1234567890abcdef 记得改")
    assert "sk1234567890abcdef" not in text
    assert "＊＊＊" in text


def test_masks_github_and_slack_tokens():
    text = rules.sanitize_reply("ghp_aaaaaaaaaaaaaaaaaaaa 和 xoxb-bbbbbbbbbbbbbbbbbb 都不能发")
    assert "ghp_aaaa" not in text
    assert "xoxb-bbbb" not in text


def test_keeps_normal_text():
    text = rules.sanitize_reply("https://example.com 上有个 skynet 项目，帮忙看看")
    assert text.startswith("https://example.com")
    assert "＊＊＊" not in text


def test_still_strips_code_fence_and_caps_length():
    assert "```" not in rules.sanitize_reply("```python\nprint('hi')\n```")
    assert rules.sanitize_reply("啊" * 500, max_chars=10).endswith("…")


def test_split_reply_no_separator_returns_single():
    assert rules.split_reply("今天不去了，明天再约") == ["今天不去了，明天再约"]


def test_split_reply_by_dash_line():
    text = "我看了下\n---\n明天下午有空\n---\n要不三点见？"
    assert rules.split_reply(text) == ["我看了下", "明天下午有空", "要不三点见？"]


def test_split_reply_splits_plain_newlines():
    """没写 --- 但用了换行：按行拆成多条，不允许一条消息里带换行。"""
    assert rules.split_reply("今天不去了\n明天再约\n早点睡") == ["今天不去了", "明天再约", "早点睡"]


def test_split_reply_mixed_dash_and_newlines_never_keeps_newline():
    parts = rules.split_reply("第一句\n\n第二句\n---\n第三句\n第四句")
    assert parts == ["第一句", "第二句", "第三句 第四句"]
    assert all("\n" not in p for p in parts)


def test_split_reply_ignores_inline_dashes():
    text = "这个方案——不行吧，得再想想"
    assert rules.split_reply(text) == [text]


def test_split_reply_merges_overflow_into_last_part():
    text = "一\n---\n二\n---\n三\n---\n四\n---\n五"
    parts = rules.split_reply(text, max_parts=3)
    assert len(parts) == 3
    assert parts[2] == "三 四 五"  # 溢出部分合并成一条，内部不留换行
    assert all("\n" not in p for p in parts)


def test_split_reply_drops_empty_segments():
    text = "---\n第一条\n---\n---\n第二条\n---"
    assert rules.split_reply(text) == ["第一条", "第二条"]
