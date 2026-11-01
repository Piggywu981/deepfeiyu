"""梗百科模块：内置速查、独立知识库、用户自定义文件的拼装、模板落盘与截断。"""
import json

from app.ai import memes


def test_builtin_contains_common_memes():
    text = memes.load_meme_knowledge()
    assert "滑稽脸" in text
    assert "奶蛙" in text


def test_user_file_appended_and_comments_skipped(tmp_path, monkeypatch):
    f = tmp_path / "meme_dict.md"
    f.write_text("# 注释不注入\n\n鲸鲸：蓝发女仆小人张嘴傻笑；傻乐\n", encoding="utf-8")
    monkeypatch.setattr(memes, "MEME_FILE", f)
    text = memes.load_meme_knowledge()
    assert "鲸鲸：蓝发女仆小人张嘴傻笑；傻乐" in text
    assert "注释不注入" not in text


def test_missing_file_creates_template_and_keeps_builtin(tmp_path, monkeypatch):
    f = tmp_path / "meme_dict.md"
    monkeypatch.setattr(memes, "MEME_FILE", f)
    text = memes.load_meme_knowledge()
    assert f.exists()
    assert "滑稽脸" in text
    assert "鲸鲸" not in text  # 模板里的示例在注释行，不注入


def test_long_user_file_truncated(tmp_path, monkeypatch):
    f = tmp_path / "meme_dict.md"
    f.write_text("长梗：" + "特" * (memes.MAX_CHARS * 2) + "\n", encoding="utf-8")
    monkeypatch.setattr(memes, "MEME_FILE", f)
    assert len(memes.load_meme_knowledge()) <= memes.MAX_CHARS


def test_prompt_block_mentions_glossary():
    assert "梗百科" in memes.prompt_block()
    assert "滑稽脸" in memes.prompt_block()


def test_kb_file_injected(tmp_path, monkeypatch):
    kb = tmp_path / "meme_kb.json"
    kb.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {"name": "奶蛙", "summary": "大笑奶龙变体\n捧腹大笑", "inject": True},
                    {"name": "牛马三件套", "summary": "房贷车贷下一代", "inject": False},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(memes, "KB_FILE", kb)
    text = memes.load_meme_knowledge()
    assert "- 奶蛙：大笑奶龙变体 捧腹大笑" in text  # 换行压成空格
    assert "牛马三件套" not in text  # inject=False 只查不注入


def test_kb_file_missing_or_broken_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(memes, "KB_FILE", tmp_path / "none.json")
    assert "滑稽脸" in memes.load_meme_knowledge()
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(memes, "KB_FILE", broken)
    assert "滑稽脸" in memes.load_meme_knowledge()
