"""字节级 ``<a href>`` 改写工具：只动 href 值，其余字节原样保留。

页面搬迁（:mod:`app.relocate`）与站点读取输出（:mod:`app.site`）共用同一套
机制：用 HTMLParser 定位每个 ``<a>`` 开始标签的精确字节范围，仅在标签原文内
替换 href 属性值——正文、注释、其他标签、属性排版与 HTML 实体都不受影响。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# 仅匹配 <a> 开始标签内的 href 属性（前面必须是空白，避免命中 data-href 之类）。
_HREF_ATTR_RE = re.compile(
    r"""(?<![^\s])href\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)


class _AnchorCollector(HTMLParser):
    """收集页面内每个 ``<a>`` 开始标签的精确字节范围与标签原文。"""

    def __init__(self, line_starts: list[int]) -> None:
        super().__init__(convert_charrefs=True)
        self._line_starts = line_starts
        # (起始偏移, 结束偏移, 标签原文)
        self.anchors: list[tuple[int, int, str]] = []

    def _abs_offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _record(self, tag_text: str) -> None:
        start = self._abs_offset()
        self.anchors.append((start, start + len(tag_text), tag_text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._record(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._record(self.get_starttag_text())


def _line_starts(html: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(html):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _replace_href_in_tag(tag_text: str, new_value: str) -> str:
    """在单个 <a> 标签原文内替换 href 的值，保留引号与值内首尾空白。"""
    m = _HREF_ATTR_RE.search(tag_text)
    assert m is not None
    token = m.group(1)
    if token[:1] in ("'", '"'):
        quote = token[0]
        inner = token[1:-1]
        leading = inner[: len(inner) - len(inner.lstrip())]
        trailing = inner[len(inner.rstrip()) :]
        new_token = f"{quote}{leading}{new_value}{trailing}{quote}"
    else:
        new_token = new_value
    return tag_text[: m.start(1)] + new_token + tag_text[m.end(1) :]


def _raw_href(tag_text: str) -> str | None:
    """从 <a> 标签原文提取 href 的原始文本（不去除值内首尾空白），无 href 返回 None。"""
    m = _HREF_ATTR_RE.search(tag_text)
    if m is None:
        return None
    token = m.group(1)
    if token[:1] in ("'", '"'):
        return token[1:-1]
    return token


def rewrite_anchor_hrefs(html: str, rewrite) -> str:
    """逐个改写页面内的 ``<a href>``。

    ``rewrite(raw_href)`` 收到保留 HTML 实体与空白的原始值，返回新的
    href 文本（去除首尾空白后的形式）或 ``None``（不改）。
    除 <a href> 之外的任何字节（正文、注释、其他标签、属性排版、HTML 实体）
    都原样保留；无任何 href 变化时返回原字符串。
    """
    collector = _AnchorCollector(_line_starts(html))
    collector.feed(html or "")
    collector.close()

    edits: list[tuple[int, int, str]] = []
    for start, end, tag_text in collector.anchors:
        raw = _raw_href(tag_text)
        if raw is None:
            continue
        new_href = rewrite(raw)
        if new_href is not None and new_href != raw.strip():
            edits.append((start, end, _replace_href_in_tag(tag_text, new_href)))

    if not edits:
        return html

    out = html
    for start, end, new_tag in reversed(edits):
        out = out[:start] + new_tag + out[end:]
    return out
