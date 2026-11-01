"""微信驱动抽象层：所有驱动实现同一套接口，业务层不关心底层通道。"""
from __future__ import annotations

import html
import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


def _title_of(xml: str) -> str:
    m = re.search(r"<title>(.*?)</title>", xml, re.S)
    if not m:
        return ""
    title = html.unescape(m.group(1)).strip()
    return title[:80] + ("…" if len(title) > 80 else "")


def _clip(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# 4.x 库里的原始 content 可能带 "wxid_xxx:\n"/"群id:\n" 前缀和 XML 声明；
# 系统通知（邀请入群、撤回等）顶层标签是 <sysmsg>，与普通 <msg> 一起识别
_XML_HEAD_RE = re.compile(
    r"^(?:[^\n<]{1,120}:\s*)?(?:<\?xml[^>]*\?>\s*)?<(?:msg|sysmsg)\b"
)


def _quote_summary(body: str) -> str:
    """引用消息（appmsg type 57）的摘要。

    refermsg 里嵌着被引用消息的原文——可能是转义过的完整 XML（引用文件/图片时
    带 <appattach>/<img>），会让整体探测把整条消息误判成 [文件]/[图片]，所以
    summarize_message_content 必须在媒体探测之前先走这里。
    """
    title = _title_of(body)
    prefix = f"[引用] {title}".rstrip()
    m = re.search(r"<refermsg\b[^>]*>(.*?)</refermsg>", body, re.S)
    if not m:
        return prefix
    refer = m.group(1)
    name_m = re.search(r"<displayname>(.*?)</displayname>", refer, re.S)
    name = html.unescape(name_m.group(1)).strip() if name_m else ""
    content_m = re.search(r"<content>(.*?)</content>", refer, re.S)
    raw = html.unescape(content_m.group(1)) if content_m else ""
    referred = _clip(summarize_message_content(raw))
    if not referred:
        return prefix
    who = f"{name}：" if name else ""
    return f"{prefix}（引用 {who}{referred}）"


def strip_sender_prefix(content: str, *names: str) -> str:
    """去掉 4.x 库内容开头的 ``wxid_xxx:\\n`` 发送者前缀。

    群消息正文会和 sender 字段重复；界面已经单独显示发送者，正文不再带前缀。
    只有与已知 sender 完全一致的前缀才剥，普通文本里的 ``Note:`` 不动。
    """
    text = content or ""
    for name in names:
        name = str(name or "").strip()
        if not name:
            continue
        prefix = name + ":"
        if text.startswith(prefix):
            return text[len(prefix):].lstrip("\r\n")
    return text


def parse_sysmsg_template(content: str) -> str | None:
    """把 sysmsgtemplate 系统通知（邀请入群等）还原成模板可读文案。

    4.x 库存的是 <sysmsg type="sysmsgtemplate"> XML：<template> 给出带
    $username$/$names$ 占位符的文案，<link_list> 里每个 <link name=...>
    携带对应成员的 <nickname> 列表，多个成员用 <separator> 连接。
    解析不出返回 None，调用方退回 [系统消息]。
    """
    text = content or ""
    if "<sysmsgtemplate" not in text:
        return None
    template_m = re.search(r"<template>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</template>", text, re.S)
    if not template_m:
        return None
    links: dict[str, str] = {}
    for link_m in re.finditer(r"<link\s[^>]*>.*?</link>", text, re.S):
        block = link_m.group(0)
        name_m = re.search(r'\bname="([^"]*)"', block)
        if not name_m:
            continue
        nicks = [
            " ".join(html.unescape(n).split())
            for n in re.findall(r"<nickname>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</nickname>", block, re.S)
        ]
        if not any(nicks):
            continue
        sep_m = re.search(r"<separator>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</separator>", block, re.S)
        sep = html.unescape(sep_m.group(1)) if sep_m else "、"
        links[name_m.group(1)] = sep.join(n for n in nicks if n)
    rendered = re.sub(r"\$(\w+)\$", lambda m: links.get(m.group(1), m.group(0)), template_m.group(1))
    rendered = " ".join(rendered.split()).strip()
    if rendered:
        return rendered
    plain_m = re.search(r"<plain>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</plain>", text, re.S)
    if plain_m:
        plain = " ".join(html.unescape(plain_m.group(1)).split()).strip()
        if plain:
            return plain
    return None


_REVOKE_RE = re.compile(r"^(.{1,64}?)\s*撤回了?\s*一条消息\s*$")


def parse_revoke_notice(content: str) -> str | None:
    """识别撤回系统消息，返回撤回者名字；自己撤回返回 ""；非撤回返回 None。

    3.x hook 通道是纯文本（「你撤回了一条消息」「张三 撤回了一条消息」）；
    4.x 库可能是 <sysmsg type="revokemsg"> XML，撤回者名在 <replacemsg> 里，
    XML 里虽有 newmsgid 但与本地库的 id 体系不同，只能按名字+时间窗启发式回溯。
    """
    text = (content or "").strip()
    if not text:
        return None
    if "<sysmsg" in text and "revokemsg" in text:
        m = re.search(r"<replacemsg>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</replacemsg>", text, re.S)
        if not m:
            return None
        text = html.unescape(m.group(1)).strip()
    if re.fullmatch(r"你\s*撤回了?\s*一条消息", text):
        return ""
    m = _REVOKE_RE.match(text)
    if m:
        return m.group(1).strip()
    return None


_PAT_XML_RE = re.compile(r"<pat(?:\s|>)", re.I)
_PAT_SPLIT_RE = re.compile(r"\s*拍了拍\s*", re.S)
_PAT_QUOTED_HEAD_RE = re.compile(r'^["“「](.*?)["”」]\s*(.*)$', re.S)
_PAT_SUFFIX_PUNCT = "的，,。.！!？?、；;：:）)了"


def _strip_quotes(text: str) -> str:
    return (text or "").strip().strip('"“”「」').strip()


def _xml_field(block: str, *names: str) -> str:
    for name in names:
        m = re.search(
            rf"<{name}\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>",
            block,
            re.S,
        )
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def parse_pat_notice(content: str) -> dict[str, Any] | None:
    """识别拍一拍通知，返回原始字段；不是拍一拍返回 None。

    3.x PC hook 是纯文本（``"张三" 拍了拍你`` / ``你 拍了拍"李四"``）；
    4.x 本地库是 ``<sysmsg type="pat"><pat>`` XML，from/patted 是 wxid，
    template 里用「我/你」表示本账号。这里只拆字段，名字解析与展示交给
    :func:`format_pat_notice`（驱动手里才有 wxid → 备注的映射）。
    """
    text = (content or "").strip()
    if not text:
        return None
    if "<sysmsg" in text or "<pat" in text:
        start = text.find("<sysmsg")
        if start < 0:
            start = text.find("<pat")
        xml = text[start:] if start >= 0 else text
        is_pat_sysmsg = bool(re.search(r"<sysmsg[^>]*\btype=\"pat\"", xml, re.I))
        # 4.x 群里拍一拍还有一条 appmsg type=62 的卡片形态：结构在 <patinfo> 里，
        # 带双方 wxid 和 patsuffix，字段名与 sysmsg 的 <pat> 一致，复用同一套拆解
        block_m = re.search(r"<pat\b[^>]*>(.*?)</pat>", xml, re.S) or re.search(
            r"<patinfo\b[^>]*>(.*?)</patinfo>", xml, re.S
        )
        # 只认真正的结构：普通的聊天文本可能恰好带「<pat 」字样，不能当通知
        if not is_pat_sysmsg and block_m is None:
            return None
        block = block_m.group(1) if block_m else xml
        from_id = _xml_field(block, "fromusername", "fromuser", "fromusr")
        to_id = _xml_field(block, "pattedusername", "patteduser", "pattedusr")
        suffix = _xml_field(block, "patsuffix", "suffix")
        template = _xml_field(block, "template", "pattemplate", "templete")
        if not any((from_id, to_id, suffix, template)):
            return None
        from_self = to_self = False
        if template:
            parts = _PAT_SPLIT_RE.split(template, maxsplit=1)
            if len(parts) == 2:
                left = _strip_quotes(parts[0])
                right = parts[1].strip()
                if left in ("我", "你"):
                    from_self = True
                if right.startswith(("我", "你")):
                    to_self = True
                    if not suffix:
                        suffix = right[1:].strip()
        return {
            "from_id": from_id,
            "from_name": "",
            "to_id": to_id,
            "to_name": "",
            "suffix": suffix,
            "from_self": from_self,
            "to_self": to_self,
        }

    # 昵称本身可能含「拍了拍」：带引号的发送者名先按完整引号切，再退回普通切分
    m = re.match(r'^(["“「].*?["”」])\s*拍了拍\s*(.*)$', text, re.S)
    if not m:
        m = re.match(r"^(.*?)\s*拍了拍\s*(.*)$", text, re.S)
    if not m:
        return None
    head = _strip_quotes(m.group(1))
    rest = m.group(2).strip()
    if not head and not rest:
        return None
    from_self = head in ("我", "你")
    to_name = ""
    to_self = False
    suffix = ""
    if rest.startswith(("我", "你")):
        to_self = True
        suffix = rest[1:].strip()
    else:
        quoted = _PAT_QUOTED_HEAD_RE.match(rest)
        if quoted:
            to_name = _strip_quotes(quoted.group(1))
            suffix = quoted.group(2).strip()
        else:
            to_name = rest
    return {
        "from_id": "",
        "from_name": "" if from_self else head,
        "to_id": "",
        "to_name": to_name,
        "suffix": suffix,
        "from_self": from_self,
        "to_self": to_self,
    }


def format_pat_notice(
    pat: dict[str, Any],
    resolve: Callable[[str], str] | None = None,
    self_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """把拍一拍字段变成「谁 拍了拍谁」；resolve 用于 wxid → 昵称/备注。

    本账号统一显示为「我」（自己拍的）/「你」（被拍），与微信客户端的视角一致；
    群里拍别人则各自显示名字。返回值第二项是要落进消息 meta 的结构。
    """
    from_id = str(pat.get("from_id") or "").strip()
    to_id = str(pat.get("to_id") or "").strip()
    from_self = bool(pat.get("from_self"))
    to_self = bool(pat.get("to_self"))
    if self_id:
        if from_id:
            from_self = from_id == self_id
        if to_id:
            to_self = to_id == self_id

    def resolve_id(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if self_id and value == self_id:
            return "我"
        if resolve is not None:
            try:
                return str(resolve(value) or value)
            except Exception:
                return value
        return value

    from_name = "我" if from_self else (str(pat.get("from_name") or "") or resolve_id(from_id))
    to_name = resolve_id(to_id) if not to_self else ""
    if from_self and to_self:
        to_name = "自己"
    elif to_self:
        to_name = "你"
    elif not to_name:
        to_name = str(pat.get("to_name") or "")
    suffix = str(pat.get("suffix") or "").strip()
    if suffix and suffix[0] not in _PAT_SUFFIX_PUNCT:
        suffix = "的" + suffix
    text = f"{from_name or '有人'} 拍了拍{to_name or '某人'}{suffix}"
    info = {
        "from_id": from_id,
        "from_name": from_name or "有人",
        "to_id": to_id,
        "to_name": to_name or "某人",
        "suffix": suffix,
        "from_self": from_self,
        "to_self": to_self,
        "text": text,
    }
    return text, info


# 真文件卡片才有的字段：扩展名、CDN 附件 id、非零体积。微信的链接/小程序卡片
# 也带 <appattach>，但里面只有 cdnthumb* 缩略图字段，按「有没有子节点」判文件
# 会把所有链接卡片误标成 [文件]
_APPMSG_FILE_RE = re.compile(r"<fileext>\s*[^<\s]|<attachid>\s*@|<totallen>\s*[1-9]")
# <url /> 这类自闭合空标签不能当成开标签，否则会把后面整段 XML 当 URL 捕获
_APPMSG_URL_RE = re.compile(r"<url(?:\s[^>/]*)?>(.*?)</url>", re.S)


def _appmsg_subtype(body: str) -> int:
    """<appmsg> 自己的子类型：5 链接、6 文件、33/36 小程序、57 引用、62 聊天记录。"""
    m = re.search(r"<type>(\d+)</type>", body)
    return int(m.group(1)) if m else 0


def appmsg_url(body: str) -> str:
    """取卡片里第一个可访问的 http(s) 网址，没有则空串。

    只认 <url>，不认 lowurl/dataurl/thumburl/shareUrlOpen 那些镜像字段；XML 里
    的 &amp; 必须还原，否则微信文章链接带 chksm 等参数会被转义符截断，模型拿着
    打不开（实测：去掉 chksm 只留 __biz/mid/idx/sn 的"精简版"返回空壳页）。
    """
    for m in _APPMSG_URL_RE.finditer(body):
        raw = m.group(1).strip()
        raw = re.sub(r"^<!\[CDATA\[|\]\]>$", "", raw).strip()
        if "<" in raw:
            continue
        url = html.unescape(raw).strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def summarize_message_content(content: str, msg_type: str = "text") -> str:
    """把微信原始消息 XML 压成一行可读摘要。

    图片/语音等消息在微信 4.x 库里存的是整段 XML；直接展示或塞给模型会浪费
    大量 token。已是摘要、普通文本或非 XML 内容原样返回。
    """
    text = content or ""
    probe = text.lstrip()
    if not _XML_HEAD_RE.match(probe):
        return text
    body = probe[re.search(r"<(?:msg|sysmsg)\b", probe).start():]

    # 引用消息（appmsg type=57）必须最先判：内层 refermsg 里嵌着原消息的完整
    # XML（未转义时 <img> 等会被误判成图片消息），外层还有个空的 <appattach />
    # 会把所有引用消息误判成文件
    if "<refermsg" in body:
        refer = parse_refermsg(text)
        if refer:
            name = f"{refer['displayname']}：" if refer["displayname"] else ""
            quote = f"[引用 {name}{refer['summary']}]"
            return f"{refer['reply']}\n{quote}" if refer["reply"] else quote
    if _PAT_XML_RE.search(body):
        # 拍一拍：旧版驱动入库的 system 行也能在读取时还原成可读文案
        pat = parse_pat_notice(body)
        if pat:
            return format_pat_notice(pat)[0]
    if re.search(r"<sysmsg\b", body):
        # 撤回通知还原成可读文本：回填路径要在摘要上再次识别出撤回者
        actor = parse_revoke_notice(body)
        if actor is not None:
            return "你撤回了一条消息" if actor == "" else f"{actor} 撤回了一条消息"
        return parse_sysmsg_template(body) or "[系统消息]"
    if re.search(r"<img\b", body):
        return "[图片]"
    if re.search(r"<voicemsg\b", body):
        return "[语音]"
    if re.search(r"<videomsg\b", body):
        return "[视频]"
    if re.search(r"<emoji\b", body):
        return "[表情]"
    if re.search(r"<location\b", body):
        return "[位置]"
    if re.search(r"<appmsg\b", body):
        title = _title_of(body)
        # 只认文件字段和 appmsg 子类型，不认「appattach 有没有子节点」：链接卡片
        # 的 appattach 里塞的是缩略图，旧判据会把它们全标成 [文件]
        if _APPMSG_FILE_RE.search(body) or _appmsg_subtype(body) == 6:
            return f"[文件] {title}".rstrip()
        url = appmsg_url(body)
        if url:
            return f"[链接] {title}｜{url}".rstrip("｜ ")
        if _appmsg_subtype(body) == 5:
            return f"[链接] {title}".rstrip()
        return f"[卡片] {title}".rstrip()
    return "[非文本消息]"


_REFER_TYPE_LABEL = {1: "[文本]", 3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]"}
_REFER_MSG_RE = re.compile(r"<refermsg\b.*?</refermsg>", re.S)


def parse_refermsg(content: str) -> dict[str, Any] | None:
    """解析引用消息（appmsg type=57），非引用消息返回 None。

    返回 {type, displayname, svrid, reply, summary, emoji}：reply 是回复文本，
    summary 是被引用原消息的一行摘要（内层 XML 递归走 summarize_message_content），
    svrid 是原消息的服务器 id，4.x 驱动靠它反查本地原图（get_image_by_server_id）；
    原消息是动画表情时 emoji 为 {url, md5, width, height}（否则 None）。
    4.x 库的内层 content 是 HTML 转义过的 XML；3.x 库走 CDATA，两种都兼容。
    """
    text = (content or "").lstrip()
    if "<refermsg" not in text:
        return None
    head = re.match(r"^(?:[^\n<]{1,120}:\s*)?(?:<\?xml[^>]*\?>\s*)?<msg\b", text)
    if not head:
        return None
    body = text[text.index("<msg"):]
    m = _REFER_MSG_RE.search(body)
    if not m:
        return None
    block = m.group(0)
    rtype_m = re.search(r"<type>(\d+)</type>", block)
    rtype = int(rtype_m.group(1)) if rtype_m else 0
    name_m = re.search(r"<displayname>(.*?)</displayname>", block, re.S)
    svrid_m = re.search(r"<svrid>(\d+)</svrid>", block)
    content_m = re.search(r"<content>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content>", block, re.S)
    inner = html.unescape(content_m.group(1)).strip() if content_m else ""
    # 4.x 库里引用消息的内嵌副本把冒号混淆成 *#*（http*#*//、media*#*0_0），
    # 先还原再交给摘要与表情解析，否则 cdnurl 不是合法链接
    inner = inner.replace("*#*", ":")
    if _XML_HEAD_RE.match(inner):
        # 4.x 库的内层副本开头还带着 "wxid_xxx:\n" 发送者前缀，只按 startswith("<")
        # 探测会把整段原始 XML 当纯文本塞进摘要（引用链接卡片时尤其明显）
        summary = " ".join(summarize_message_content(inner).split())
    else:
        summary = " ".join(inner.split())
    if not summary:
        summary = _REFER_TYPE_LABEL.get(rtype, "[消息]")
    return {
        "type": rtype,
        "displayname": html.unescape(name_m.group(1)).strip() if name_m else "",
        "svrid": svrid_m.group(1) if svrid_m else "",
        "reply": _title_of(body),
        "summary": summary[:120],
        # 被引用的是动画表情时带出 url/md5/尺寸：控制台直接渲染原图，AI 看图回应
        "emoji": extract_emoji(inner) if "<emoji" in inner else None,
    }


_RECORD_DATAITEM_RE = re.compile(r"<dataitem\b.*?</dataitem>", re.S)
_RECORD_EMOJIITEM_RE = re.compile(r"<emojiitem\b.*?</emojiitem>", re.S)


def parse_chat_record(content: str, max_items: int = 80, max_chars: int = 1500) -> str | None:
    """解析合并转发的聊天记录（appmsg type=19），返回「发送者：内容」的纯文本。

    被转发的消息完整内嵌在 <recorditem> 的 CDATA（<recordinfo><datalist>）里，
    本地就有，不需要下载 CDN；解析不出内容（普通文件/链接卡片、结构残缺）返回 None。
    动画表情在记录里只有占位符，转成「[动画表情 md5=x]」标记，
    由 AccountSession 对照表情包库补全描述（见 _enrich_record_emojis）。
    """
    text = content or ""
    if "<recorditem" not in text and "<recordinfo" not in text:
        return None
    m = re.search(r"<recorditem>\s*<!\[CDATA\[(.*?)\]\]>", text, re.S)
    if m:
        inner = m.group(1)
    else:
        m = re.search(r"<recordinfo>.*?</recordinfo>", text, re.S)
        inner = m.group(0) if m else ""
    if not inner:
        return None
    lines: list[str] = []
    used = 0
    count = 0
    for item in _RECORD_DATAITEM_RE.findall(inner):
        name = re.search(r"<sourcename>(.*?)</sourcename>", item, re.S)
        desc = re.search(r"<datadesc>(.*?)</datadesc>", item, re.S)
        desc_text = html.unescape(desc.group(1)).strip() if desc else ""
        emoji_md5 = ""
        if "<emojiitem" in item:
            em = _RECORD_EMOJIITEM_RE.search(item)
            emd5 = re.search(r"<md5>([0-9a-fA-F]{32})</md5>", em.group(0)) if em else None
            if emd5:
                emoji_md5 = emd5.group(1).lower()
        if emoji_md5:
            desc_text = f"[动画表情 md5={emoji_md5}]"
        elif not desc_text:
            continue
        else:
            desc_text = " ".join(desc_text.split())
        name_text = html.unescape(name.group(1)).strip() if name else ""
        line = f"{name_text}：{desc_text}" if name_text else desc_text
        # 条数与字数分开算：used 是字符数，拿它和 max_items 比会在第 4 条左右
        # 就误触条数上限（80 字），整段记录被提前截成「……（后略）」
        if count >= max_items or used + len(line) > max_chars:
            lines.append("……（后略）")
            break
        lines.append(line)
        used += len(line)
        count += 1
    return "\n".join(lines) if lines else None


def parse_chat_record_senders(content: str, limit: int = 12) -> list[str]:
    """合并转发聊天记录里出现过的发言人名字（去重、保持原顺序）。

    记录条目里没有稳定 wxid，只有 ``<sourcename>`` 展示名；跨对话转告记录里的人时
    只能按名字提示模型，因此这里只取名字，供提示词列出「记录里的发言人」。
    """
    text = content or ""
    if "<recorditem" not in text and "<recordinfo" not in text:
        return []
    out: list[str] = []
    for raw in re.findall(r"<sourcename>(.*?)</sourcename>", text, re.S):
        name = " ".join(html.unescape(raw).split()).strip()
        if not name or name in out:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def extract_record_emojis(content: str) -> list[dict[str, str]]:
    """从合并聊天记录的原始 XML 里取各动画表情的 md5 与 CDN 链接。

    供表情库对照与「偷表情包」收集使用；URL 已还原 HTML 转义。
    """
    text = content or ""
    out: list[dict[str, str]] = []
    for em in _RECORD_EMOJIITEM_RE.findall(text):
        md5 = re.search(r"<md5>([0-9a-fA-F]{32})</md5>", em)
        if not md5:
            continue
        url = re.search(r"<cdnurlstring>(.*?)</cdnurlstring>", em, re.S)
        out.append(
            {
                "md5": md5.group(1).lower(),
                "url": html.unescape(url.group(1)).strip() if url else "",
            }
        )
    return out


def extract_emoji(content: str) -> dict[str, Any] | None:
    """从表情消息 XML 里提取可展示的属性（cdnurl/md5/尺寸）。

    微信动画表情（type 47）正文形如 ``<emoji cdnurl="..." md5="..." len=".."/>``；
    提取结果存进消息 meta，供控制台展示、收藏与转发使用。
    """
    text = content or ""
    probe = text.lstrip()
    m = re.search(r"<emoji\b([^>]*?)/?>", probe, re.S)
    if not m:
        return None
    attrs = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1)))
    url = html.unescape(attrs.get("cdnurl") or attrs.get("encrypturl") or "").strip()
    if not url:
        return None
    info: dict[str, Any] = {"url": url}
    for key in ("md5", "aeskey", "encrypturl"):
        if attrs.get(key):
            info[key] = attrs[key]
    for key in ("len", "width", "height"):
        try:
            if attrs.get(key):
                info[key] = int(attrs[key])
        except ValueError:
            pass
    return info


class DriverError(RuntimeError):
    pass


# 微信会话表里的内部聚合会话：客户端会话列表不展示，这里同样不展示
INTERNAL_CHAT_IDS = frozenset({"brandsessionholder", "brandservicesessionholder"})


def is_internal_chat(chat_id: str | None) -> bool:
    return str(chat_id or "") in INTERNAL_CHAT_IDS


def unique_sender_id(sender_id: str, sender_name: str) -> str:
    """返回能当唯一身份用的发送者标识（wxid）；不可信时返回空串。

    昵称会重名，同名好友/群友要靠 wxid 才分得开。但两类 sender_id 起不到
    这个作用：weixin4 旧数据的数值行 id（太短），wcferry 群消息只从正文
    解析出昵称（与展示名相同）——这些都不如不标。
    """
    sid = str(sender_id or "").strip()
    if len(sid) < 5 or sid == str(sender_name or "").strip():
        return ""
    return sid


def sender_label(sender_name: str, sender_id: str) -> str:
    """群聊发送者展示名：「昵称(wxid)」，同名群友靠 wxid 区分；拿不到唯一 id 只用昵称。"""
    name = str(sender_name or "").strip() or "对方"
    tag = unique_sender_id(sender_id, name)
    return f"{name}({tag})" if tag else name


@dataclass
class Message:
    chat_id: str
    chat_name: str
    sender_id: str = ""
    sender_name: str = ""
    content: str = ""
    is_group: bool = False
    is_self: bool = False
    msg_type: str = "text"
    ts: float = field(default_factory=time.time)
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content = strip_sender_prefix(self.content, self.sender_name, self.sender_id)
        meta = dict(self.raw or {})
        if self.msg_type == "pat":
            # 驱动一般已把 content/meta 归一化；这里兜底处理外部 webhook 直接
            # 塞进来的原始 XML/文本，保证入库、控制台和模型看到的是同一句人话
            pat = dict(meta.get("pat") or {})
            if pat:
                content = str(pat.get("text") or content)
            else:
                parsed = parse_pat_notice(content)
                if parsed:
                    content, pat = format_pat_notice(parsed)
                    meta["pat"] = pat
        if self.msg_type == "emoji":
            info = extract_emoji(content)
            if info:
                meta["emoji"] = info
        refer = None
        if "<refermsg" in content:
            # 引用消息：给控制台和图片反查留结构化信息（原消息类型/发送者/svrid）；
            # 必须在 link 分支把 content 压成摘要之前取，摘要里已经没有原始 XML 了
            refer = parse_refermsg(content)
        if self.msg_type == "link":
            # 合并转发的聊天记录：原始 XML 里带着全部被转发消息，解析成可读转录，
            # 控制台、AI 上下文和检索用的都是这一份；解析不出再退回标题摘要
            record = parse_chat_record(content)
            content = f"[聊天记录]\n{record}" if record else summarize_message_content(content, self.msg_type)
        if refer:
            entry = {
                "type": refer["type"],
                "displayname": refer["displayname"],
                "svrid": refer["svrid"],
                "summary": refer["summary"],
            }
            if refer.get("emoji"):
                entry["emoji"] = refer["emoji"]
            meta["refer"] = entry
        return {
            "id": self.msg_id,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": summarize_message_content(content, self.msg_type),
            "is_group": self.is_group,
            "is_self": self.is_self,
            "msg_type": self.msg_type,
            "ts": self.ts,
            "source": "wechat",
            "meta": meta,
        }


class WeChatDriver(ABC):
    name = "base"
    display_name = "基础驱动"
    supports_send_file = False
    supports_contacts = False

    def __init__(self, on_message: Callable[[Message], None] | None = None) -> None:
        self.on_message = on_message
        self.last_error = ""

    # 生命周期
    @abstractmethod
    def start(self) -> None: ...

    def stop(self) -> None: ...

    # 状态
    def status(self) -> dict[str, Any]:
        return {"driver": self.name, "connected": True, "account": "", "detail": "", "error": self.last_error}

    # 能力
    @abstractmethod
    def send_text(self, chat_id: str, text: str) -> None:
        """发送文本。"""

    def send_file(self, chat_id: str, path: str) -> None:
        raise DriverError(f"{self.display_name} 不支持发送文件")

    def send_emoji(self, chat_id: str, path: str) -> None:
        """发送本地动画表情文件（gif）；默认不支持。"""
        raise DriverError(f"{self.display_name} 不支持发送表情")

    def list_chats(self, keyword: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return []

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {"key": "send_text", "label": "发送文本", "ok": True},
            {"key": "recv", "label": "接收消息", "ok": True},
            {"key": "send_file", "label": "发送文件", "ok": self.supports_send_file},
            {"key": "contacts", "label": "通讯录", "ok": self.supports_contacts},
            {"key": "history", "label": "历史记录检索", "ok": self.supports_contacts},
        ]
