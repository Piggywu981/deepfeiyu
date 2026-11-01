# 技能目录

每个技能一个子目录，里面放 `SKILL.md`：

```text
skills/
  my-skill/
    SKILL.md
    （可选）scripts/、assets/…
```

`SKILL.md` 可以带 YAML frontmatter：

```markdown
---
name: my-skill
description: 一句话说明这个技能解决什么问题
---
正文：步骤、注意事项、输出格式。
```

系统提示只列名字和描述；模型需要时调用 `load_skill` 读正文。技能本身不执行脚本。
也支持放到 `data/skills/`（运行数据目录，不随仓库提交）。
