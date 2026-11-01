"""SKILL.md 发现与加载。

兼容 Claude Code / 通用 Agent Skill 的目录约定：
- 递归扫描 ``**/SKILL.md``；
- 文件头可带 YAML frontmatter（name / description / allowed-tools）；
- 正文按需加载，系统提示只暴露名字+描述，避免一次性塞满上下文。
技能只提供指令，不自动执行脚本；脚本执行交给 MCP/插件或用户自己。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..ai.agent import Tool

log = logging.getLogger("capabilities.skills")

MAX_SKILLS = 200
MAX_BODY = 12000


def _clean(value: str) -> str:
    return (value or "").strip().lstrip("|>").strip().strip('"').strip("'")


def parse_skill(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        log.warning("读取技能失败 %s: %s", path, exc)
        return None
    name = ""
    description = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            front = text[3:end].strip("\n")
            body = text[end + 4 :].lstrip("\n")
            lines: list[str] = []
            for raw in front.splitlines():
                if raw.startswith((" ", "\t")) and lines:
                    lines[-1] += " " + raw.strip()
                else:
                    lines.append(raw)
            for line in lines:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = _clean(value)
                if key == "name":
                    name = value
                elif key in ("description", "summary"):
                    description = value
    if not name:
        for line in body.splitlines():
            if line.startswith("#"):
                name = line.lstrip("#").strip()
                break
    if not name:
        name = path.parent.name or path.stem
    if not description:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:200]
                break
    return {
        "name": name[:120],
        "description": (description or "（无描述）")[:400],
        "body": body.strip()[:MAX_BODY],
        "path": str(path),
        "dir": str(path.parent),
    }


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: str
    dir: str
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self, with_body: bool = False) -> dict[str, Any]:
        out = {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "dir": self.dir,
        }
        if with_body:
            out["body"] = self.body
        return out


class SkillRegistry:
    def __init__(self, dirs: Iterable[str | Path] | None = None) -> None:
        self.dirs = [Path(p) for p in (dirs or [])]
        self.skills: dict[str, Skill] = {}
        self.errors: list[str] = []
        self.last_scan = 0.0

    def refresh(self) -> None:
        import time

        found: dict[str, Skill] = {}
        self.errors = []
        count = 0
        for root in self.dirs:
            try:
                if not root.exists() or not root.is_dir():
                    continue
                files = sorted(root.glob("**/SKILL.md"))[:MAX_SKILLS]
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{root}: {exc}")
                continue
            for path in files:
                if count >= MAX_SKILLS:
                    break
                data = parse_skill(path)
                if not data:
                    continue
                base = data["name"]
                name = base
                idx = 2
                while name in found:
                    name = f"{base}-{idx}"
                    idx += 1
                data["name"] = name
                found[name] = Skill(**data)
                count += 1
        self.skills = found
        self.last_scan = time.time()

    def list(self) -> list[dict[str, Any]]:
        return [s.public() for s in sorted(self.skills.values(), key=lambda x: x.name.lower())]

    def get(self, name: str) -> Skill | None:
        return self.skills.get((name or "").strip())

    def prompt_block(self, max_items: int = 40) -> str:
        if not self.skills:
            return ""
        rows = sorted(self.skills.values(), key=lambda x: x.name.lower())[:max_items]
        lines = [
            "【可用技能】下面是本机安装的技能说明。需要按某个技能的步骤做事时，"
            "先调用 load_skill 读取完整正文，再照着做；不要凭空猜测技能内容。"
        ]
        for s in rows:
            lines.append(f"- {s.name}：{s.description}")
        return "\n".join(lines)

    def tools(self) -> list[Tool]:
        if not self.skills:
            return []

        async def load_skill(args: dict[str, Any]) -> Any:
            name = str(args.get("name") or "").strip()
            if not name:
                return {"error": "请提供技能名", "skills": self.list()}
            skill = self.get(name)
            if not skill:
                return {"error": f"没有技能「{name}」", "skills": self.list()}
            return {
                "ok": True,
                "name": skill.name,
                "description": skill.description,
                "path": skill.path,
                "body": skill.body,
            }

        async def list_skills(args: dict[str, Any]) -> Any:
            return {"skills": self.list()}

        return [
            Tool(
                name="load_skill",
                description=(
                    "读取一个已安装技能的完整说明。技能是本地目录里的 SKILL.md，"
                    "包含步骤、注意事项或专用工作流；需要时先读它，再按说明执行。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "技能名，先用 list_skills 查"},
                    },
                    "required": ["name"],
                },
                handler=load_skill,
            ),
            Tool(
                name="list_skills",
                description="列出本机已安装的技能（名字、描述、路径）。",
                parameters={"type": "object", "properties": {}},
                handler=list_skills,
            ),
        ]

    def status(self) -> dict[str, Any]:
        return {
            "dirs": [str(p) for p in self.dirs],
            "count": len(self.skills),
            "last_scan": self.last_scan,
            "errors": self.errors,
            "items": self.list(),
        }
