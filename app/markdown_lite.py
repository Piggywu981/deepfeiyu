"""极简 Markdown 渲染：标题、段落、引用、列表、表格、分隔线、行内样式。

只覆盖本项目文档实际用到的语法；先转义 HTML 再做行内替换，链接只放行
http(s)，避免文档里的尖括号被当成标签执行。
"""
from __future__ import annotations

import html
import re

_TABLE_SEP = re.compile(r"^:?-{3,}:?$")
_ORDERED_ITEM = re.compile(r"^\d+\.\s+")
_NESTED_ITEM = re.compile(r"^([ \t]{2,})[-*]\s+(.+)$")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _join_lines(lines: list[str]) -> str:
    """段落软换行：中文与中文之间不插空格，其余按一个空格合并。"""
    out = ""
    for part in lines:
        if not out:
            out = part
        elif _CJK.search(out[-1]) and _CJK.match(part[:1] or ""):
            out += part
        else:
            out += " " + part
    return out


def _inline(text: str) -> str:
    """转义 HTML 后处理行内 Markdown。"""
    text = html.escape(text, quote=True)

    # 行内代码先占位，避免其中的 ** 或链接语法被二次解释
    slots: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        slots.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(slots) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash_code, text)
    text = re.sub(
        r"&lt;(https?://.+?)&gt;",
        lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )
    # 危险协议直接降级成文字，别留下半截链接
    text = re.sub(
        r"\[([^\]]+)\]\(\s*(?:javascript|data|vbscript):(?:[^()]|\([^)]*\))*\)",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    # 相对链接（README/CHANGELOG/specs）在控制台里没有可点目标：只留文字，别显示成
    # 「[README](../README.md)」这种半截 Markdown。
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

    for index, snippet in enumerate(slots):
        text = text.replace(f"\x00{index}\x00", snippet)
    return text


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def _is_table_sep(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_TABLE_SEP.match(cell) for cell in cells)


def render_markdown(markdown: str) -> str:
    """渲染文档；表格/有序列表/一级缩进子列表，段落内软换行按语言合并。"""
    out: list[str] = []
    para: list[str] = []
    quote: list[str] = []
    items: list[str] = []
    nested: list[str] = []
    item_tag = "ul"

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(_join_lines(para))}</p>")
            para.clear()

    def flush_quote() -> None:
        if quote:
            out.append(f"<blockquote>{_inline(_join_lines(quote))}</blockquote>")
            quote.clear()

    def flush_nested() -> None:
        if nested and items:
            # 顶层 <li> 当前以 </li> 结尾，去掉尾标签再把子列表塞进去
            items[-1] = items[-1][:-5] + "<ul>" + "".join(nested) + "</ul></li>"
            nested.clear()

    def flush_list() -> None:
        nonlocal item_tag
        flush_nested()
        if items:
            out.append(f"<{item_tag}>" + "".join(items) + f"</{item_tag}>")
            items.clear()
        item_tag = "ul"

    def flush_all() -> None:
        flush_para()
        flush_quote()
        flush_list()

    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            flush_all()
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and _is_table_sep(lines[index + 1]):
            flush_all()
            header = _cells(line)
            rows: list[list[str]] = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append(_cells(lines[cursor]))
                cursor += 1
            parts = ["<table><thead><tr>"]
            parts.extend(f"<th>{_inline(cell)}</th>" for cell in header)
            parts.append("</tr></thead><tbody>")
            for row in rows:
                normalized = (row + [""] * len(header))[: len(header)]
                parts.append(
                    "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in normalized) + "</tr>"
                )
            parts.append("</tbody></table>")
            out.append("".join(parts))
            index = cursor
            continue

        nested_match = _NESTED_ITEM.match(line)
        if nested_match:
            flush_para()
            flush_quote()
            text = nested_match.group(2).strip()
            if items:
                nested.append(f"<li>{_inline(text)}</li>")
            else:
                para.append(text)
            index += 1
            continue

        if line.startswith("### "):
            flush_all()
            out.append(f"<h3>{_inline(line[4:].strip())}</h3>")
        elif line.startswith("## "):
            flush_all()
            out.append(f"<h2>{_inline(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            flush_all()
            out.append(f"<h1>{_inline(line[2:].strip())}</h1>")
        elif stripped == "---":
            flush_all()
            out.append("<hr>")
        elif line.startswith("> "):
            flush_para()
            flush_list()
            quote.append(line[2:].strip())
        elif line.startswith("- "):
            flush_para()
            flush_quote()
            flush_nested()
            if items and item_tag != "ul":
                flush_list()
            item_tag = "ul"
            items.append(f"<li>{_inline(line[2:].strip())}</li>")
        elif _ORDERED_ITEM.match(line):
            flush_para()
            flush_quote()
            flush_nested()
            if items and item_tag != "ol":
                flush_list()
            item_tag = "ol"
            items.append(f"<li>{_inline(_ORDERED_ITEM.sub('', line, count=1).strip())}</li>")
        else:
            flush_quote()
            flush_list()
            para.append(stripped)
        index += 1

    flush_all()
    return "\n".join(out)
