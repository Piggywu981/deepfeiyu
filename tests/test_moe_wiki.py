"""moe_wiki 模块：说明文档解析、文件对齐、自我/名册块、认图触发与检索、打标注入。"""
from app import config
from app.ai import moe_wiki, tagger

DOC = """\
# 逐图说明

## 第一部分：UP 主原始动态逐图说明

**1. 大肥鱼**

这次带来的是我们的蓝发鲸尾女仆鱼。

> 注：已核验。

**2. 中秋贺图**

大家一起好好吃月饼哦～！

## 第二部分：补充图目

**88. 各模型娘主立绘（2 张）**

- 文件：Claude娘.jpg、Gemini娘.jpg
- 注：各模型娘的单角色全身立绘。

## 第三部分：全库分类索引

- 01-立绘人设/：大肥鱼.jpg（1）、Claude娘.jpg
"""


def make_wiki(tmp_path):
    (tmp_path / "01-立绘人设").mkdir()
    (tmp_path / "01-立绘人设" / "大肥鱼.jpg").write_bytes(b"x")
    (tmp_path / "01-立绘人设" / "小肥鱼.png").write_bytes(b"x")
    (tmp_path / "01-立绘人设" / "Claude娘.jpg").write_bytes(b"x")
    (tmp_path / "03-三视图设定").mkdir()
    (tmp_path / "03-三视图设定" / "DeepSeek女仆装三视图.jpg").write_bytes(b"x")
    (tmp_path / "04-桌面壁纸").mkdir()
    (tmp_path / "04-桌面壁纸" / "大肥鱼电脑壁纸.jpg").write_bytes(b"x")
    (tmp_path / "05-表情包梗图").mkdir()
    (tmp_path / "05-表情包梗图" / "涨价了.jpg").write_bytes(b"x")
    (tmp_path / "07-节日贺图").mkdir()
    (tmp_path / "07-节日贺图" / "中秋贺图.jpg").write_bytes(b"x")
    (tmp_path / moe_wiki.DOC_NAME).write_text(DOC, encoding="utf-8")
    return tmp_path


def test_load_entries_aligns_doc_text(tmp_path):
    root = make_wiki(tmp_path)
    files = {f["name"]: f for f in moe_wiki.load_entries(root)}
    assert len(files) == 7
    assert "蓝发鲸尾女仆鱼" in files["大肥鱼"]["desc"]
    assert "已核验" in files["大肥鱼"]["desc"]
    assert "好好吃月饼" in files["中秋贺图"]["desc"]
    # 第二部分条目只作图集级说明，不对齐到单张图；无条目的图说明留空
    assert files["Claude娘"]["desc"] == ""
    assert files["小肥鱼"]["desc"] == ""
    assert files["大肥鱼"]["category"] == "01-立绘人设"


def test_self_block_uses_hint_and_skips_memes(tmp_path):
    root = make_wiki(tmp_path)
    block = moe_wiki.self_block(root)
    assert "大肥鱼.jpg" in block
    assert "DeepSeek女仆装三视图.jpg" in block
    assert "大肥鱼电脑壁纸.jpg" in block
    assert "涨价了" not in block  # 梗图不是形象图
    assert "小肥鱼" not in block  # 另一个角色，不算自己


def test_roster_block_lists_all_and_caps(tmp_path):
    root = make_wiki(tmp_path)
    block = moe_wiki.roster_block(root)
    assert "涨价了" in block
    assert "小肥鱼" in block
    assert "ZipZipPipe" in block
    short = moe_wiki.roster_block(root, max_chars=30)
    assert short.startswith("【形象图鉴】")
    assert len(short) < len(block)  # 截断后仍保留完整头部说明


def test_mentions_triggers_on_character_names_only(tmp_path):
    root = make_wiki(tmp_path)
    assert moe_wiki.mentions("Claude娘今天好好看", root)
    assert moe_wiki.mentions("大肥鱼最近好吗", root)
    assert not moe_wiki.mentions("听说又涨价了", root)  # 梗图标题不算
    assert not moe_wiki.mentions("", root)


def test_search_and_find(tmp_path):
    root = make_wiki(tmp_path)
    hits = moe_wiki.search(root, "大肥鱼 壁纸", 3)
    assert hits[0]["name"] == "大肥鱼电脑壁纸"
    assert moe_wiki.find_image(root, "中秋")["rel"] == "07-节日贺图/中秋贺图.jpg"
    assert moe_wiki.find_image(root, "不存在的图") is None
    empty = moe_wiki.search(root, "", 2)
    assert len(empty) == 2  # 空关键词返回图集开头


def test_tag_block_instructs_tagging(tmp_path):
    root = make_wiki(tmp_path)
    block = moe_wiki.tag_block(root)
    assert "大肥鱼" in block
    assert "涨价了" in block  # 梗图图名也是可打的标签
    assert "必须" in block  # 图鉴角色必打名，且放第一位
    assert "第一位" in block
    assert "send_moe_wiki_image" not in block  # 打标场景不引导发图


def test_tagger_prompt_adds_mode_note(monkeypatch, tmp_path):
    root = make_wiki(tmp_path)
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: root)
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": True}})
    assert "仅AI娘表情包模式已开启" in tagger.build_tag_prompt()
    monkeypatch.setattr(config, "load", lambda: {"ai": {"wiki_emoji_only": False}})
    assert "仅AI娘表情包模式已开启" not in tagger.build_tag_prompt()


def test_tagger_prompt_injects_wiki(tmp_path, monkeypatch):
    root = make_wiki(tmp_path)
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: root)
    prompt = tagger.build_tag_prompt()
    assert "大肥鱼" in prompt  # 图鉴注入
    assert "滑稽脸" in prompt  # 梗百科仍在
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: None)
    assert "大肥鱼" not in tagger.build_tag_prompt()  # 图集不可用时降级


def test_wiki_root_resolution(tmp_path):
    root = make_wiki(tmp_path)
    assert moe_wiki.wiki_root({"ai": {"moe_wiki_path": str(root)}}) == root.resolve()
    assert moe_wiki.wiki_root({"ai": {"moe_wiki_path": ""}}) is None
    assert moe_wiki.wiki_root({"ai": {"moe_wiki_path": str(tmp_path / "nope")}}) is None
    (tmp_path / "nope").mkdir()
    (tmp_path / "nope" / moe_wiki.DOC_NAME).write_text("x", encoding="utf-8")
    assert moe_wiki.wiki_root({"ai": {"moe_wiki_path": str(tmp_path / "nope")}}) is not None


PERSONA_DOC = """\
# 逐图说明

## 第一部分：UP 主原始动态逐图说明

**1. 悄悄的拿走**

大家一起来看这只大肥鱼吧。

**2. GitHub Copilot**

Copilot 1 已进入空域，准备作战。

## 第二部分：补充图目

**88. 各模型娘主立绘（2 张）**

- 文件：Claude娘.jpg、GPT娘.jpg
- 注：各模型娘的单角色全身立绘。已核验：Claude 娘为橙发、缀菊花饰、手持书卷的复古洋装造型；Muse 娘为白色羊驼主题。其余以文件名为准。

**89. DeepSeek 系立绘（2 张）**

- 文件：大肥鱼.jpg、小肥鱼.png
- 注：大肥鱼（DeepSeek-Pro 娘）：蓝发、鲸尾、深蓝女仆装，围裙绣 DeepSeek 鲸鱼标。小肥鱼（DeepSeek V4.1 Flash）：单张立绘。
"""


def make_persona_wiki(tmp_path):
    base = tmp_path / "01-立绘人设"
    (base / "模型娘立绘").mkdir(parents=True)
    (base / "平台工具拟人").mkdir(parents=True)
    (base / "DeepSeek系立绘").mkdir(parents=True)
    (base / "模型娘立绘" / "Claude娘.jpg").write_bytes(b"x")
    (base / "模型娘立绘" / "GPT娘.jpg").write_bytes(b"x")
    (base / "平台工具拟人" / "GitHub Copilot.png").write_bytes(b"x")
    (base / "DeepSeek系立绘" / "大肥鱼.jpg").write_bytes(b"x")
    (base / "DeepSeek系立绘" / "小肥鱼.png").write_bytes(b"x")
    (tmp_path / "05-表情包梗图").mkdir()
    (tmp_path / "05-表情包梗图" / "涨价了.jpg").write_bytes(b"x")
    (tmp_path / moe_wiki.DOC_NAME).write_text(PERSONA_DOC, encoding="utf-8")
    return tmp_path


def test_character_names_excludes_memes(tmp_path):
    root = make_wiki(tmp_path)
    names = moe_wiki.character_names(root)
    assert "大肥鱼" in names and "Claude娘" in names
    assert "涨价了" not in names  # 梗图目录不进词表


def test_character_names_degrades_without_wiki(monkeypatch):
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: None)
    assert moe_wiki.character_names() == []


def test_roster_tagged_matches_normalized_tags(tmp_path):
    root = make_wiki(tmp_path)
    names = moe_wiki.character_names(root)
    rows = [
        {"id": 1, "tags": "滑稽"},
        {"id": 2, "tags": "大肥鱼,摸鱼"},
        {"id": 3, "tags": "Claude 娘"},  # 空格归一化后命中
        {"id": 4, "tags": ""},
    ]
    keep = {r["id"] for r in moe_wiki.roster_tagged(rows, names)}
    assert keep == {2, 3}
    assert moe_wiki.roster_tagged(rows, []) == []  # 空词表全不放行


def test_build_persona_presets_sources(tmp_path):
    root = make_persona_wiki(tmp_path)
    presets = {p["name"]: p for p in moe_wiki.build_persona_presets(root)}
    assert set(presets) == {"Claude娘", "GPT娘", "GitHub Copilot", "大肥鱼", "小肥鱼"}
    # 同名条目正文优先
    assert "进入空域" in presets["GitHub Copilot"]["system_prompt"]
    # 合集注分句，名字出现最早的胜出（89 优于 1）
    assert "蓝发、鲸尾" in presets["大肥鱼"]["system_prompt"]
    # 合集注里含名字的句子
    assert "橙发、缀菊花饰" in presets["Claude娘"]["system_prompt"]
    # 无任何说明的角色用兜底描述
    assert "ai-moe-wiki" in presets["GPT娘"]["system_prompt"]
    for p in presets.values():
        assert p["persona_name"] == p["name"]
        assert p["source"] == "ai-moe-wiki"
        assert p["system_prompt"].startswith("你是「")
    # 梗图目录不生成预设
    assert "涨价了" not in presets


def test_build_persona_presets_degrades_without_wiki(monkeypatch):
    monkeypatch.setattr(moe_wiki, "wiki_root", lambda cfg=None: None)
    assert moe_wiki.build_persona_presets() == []


def test_character_refs_and_tag_refs_block(tmp_path):
    root = make_persona_wiki(tmp_path)
    refs = moe_wiki.character_refs(root)
    assert len(refs) == 5  # 立绘三子目录各角色一张
    assert all("path" in f and "desc" in f for f in refs)
    assert moe_wiki.character_refs(root, limit=2) == refs[:2]
    block = moe_wiki.tag_refs_block(refs)
    assert "第1张参考图=" in block and "第5张参考图=" in block
    assert "大肥鱼" in block  # 设定文案随清单带出（参考文案）
    assert moe_wiki.tag_refs_block([]) == ""
