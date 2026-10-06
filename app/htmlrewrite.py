"""字节级 ``<a href>`` 改写工具：只动 href 值，其余字节原样保留。

页面搬迁（:mod:`app.relocate`）与站点读取输出（:mod:`app.site`）共用同一套
机制：用 HTMLParser 定位每个 ``<a>`` 开始标签的精确字节范围，再在标签原文内
按属性语法扫描出真正的 ``href`` 属性值并替换——其他属性值（如
``title="hint href='/x'"``）中出现的 ``href=`` 不会被误认成 href 属性，
正文、注释、其他标签、属性排版与 HTML 实体也都不受影响。
"""

from __future__ import annotations

from html.parser import HTMLParser

# 无引号属性值在空白（值结束，其后可能是下一个属性或自闭合 '/'）或 '>'
# 处结束。'/' 不是终止符：href=/docs/a 这类值本身以 '/' 开头；HTML 词法中
# 自闭合 '/' 只能出现在空白之后，由扫描循环另行识别。
_UNQUOTED_STOPS = frozenset(" \t\n\r\f\v>")


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


def _find_href_value_span(tag_text: str) -> tuple[int, int] | None:
    """在单个开始标签原文内定位**真正的** href 属性值的字符范围 (起, 止)。

    按 HTML 属性词法逐 token 扫描标签：跳过标签名与空白，读取属性名，再视
    ``=`` 后的引号读取整个带引号（或无引号）的值。带引号的值（连同其中
    任何形如 ``href=`` 的文本，例如 ``title="hint href='/x'"``）被整体
    跳过，绝不会把属性值里的伪 ``href=`` 误认成 href 属性；
    ``data-href`` 之类属性名不做匹配。同名属性重复时与链接校验的
    ``dict(attrs)["href"]`` 口径一致，取**最后一个** href。
    标记畸形无法可靠定位时返回 None（此时该锚点保守地不做改写）。
    """
    n = len(tag_text)
    i = 1  # 跳过起始 '<'
    while i < n and not tag_text[i].isspace() and tag_text[i] not in "/>":
        i += 1  # 跳过标签名

    found: tuple[int, int] | None = None  # 记录最后一个 href（与校验 dict 口径一致）
    while i < n:
        while i < n and tag_text[i].isspace():
            i += 1
        if i >= n or tag_text[i] in "/>":
            return found
        if tag_text[i] in "'\"":
            # 引号只在 "= 之后" 作为值的起始被消费；名称起始位置出现引号，
            # 说明标记畸形（如属性值缺少等号、引号未成对），无法可靠区分
            # 后续属性，保守地停止扫描。
            return found
        # 属性名（在空白、'='、'/' 或 '>' 处结束）
        name_start = i
        while i < n and not tag_text[i].isspace() and tag_text[i] not in "=/>":
            i += 1
        name = tag_text[name_start:i].lower()
        # 名称与 '=' 之间允许空白
        while i < n and tag_text[i].isspace():
            i += 1
        if i >= n or tag_text[i] != "=":
            continue  # 布尔属性（无值）：i 已在下一个 token 前
        i += 1  # 跳过 '='
        while i < n and tag_text[i].isspace():
            i += 1
        if i >= n:
            if name == "href":  # 形如 href=（值缺失，位于标签末尾）
                found = (i, i)
            return found
        if tag_text[i] == ">":
            if name == "href":
                found = (i, i)
            return found
        if tag_text[i] == "/" and (i + 1 >= n or tag_text[i + 1] in " \t\n\r\f\v>"):
            # 自闭合 '/'（其后是 '>' 或空白），不是值：本属性无值。
            if name == "href":
                found = (i, i)
            continue
        quote = tag_text[i]
        if quote in ("'", '"'):
            value_start = i + 1
            j = tag_text.find(quote, value_start)
            if j == -1:  # 值引号未闭合：畸形 HTML，停止扫描
                return found
            value_end = j
            i = j + 1  # 下一轮循环从闭引号之后的空白/下一个属性开始
        else:
            value_start = i
            while i < n and tag_text[i] not in _UNQUOTED_STOPS:
                i += 1
            value_end = i  # i 停在空白或 '>'，由下一轮循环处理
        if name == "href":
            found = (value_start, value_end)
    return found


def _raw_href(tag_text: str) -> str | None:
    """从 <a> 标签原文提取 href 的原始文本（不去除值内首尾空白），无 href 返回 None。"""
    span = _find_href_value_span(tag_text)
    return None if span is None else tag_text[span[0]:span[1]]


def _replace_href_in_tag(tag_text: str, new_value: str) -> str:
    """在单个 <a> 标签原文内替换 href 的值，保留引号与值内首尾空白。"""
    start, end = _find_href_value_span(tag_text)
    if tag_text[start - 1:start] in ("'", '"'):  # 值带引号（start 位于开引号之后）
        quote = tag_text[start - 1]
        inner = tag_text[start:end]
        leading = inner[: len(inner) - len(inner.lstrip())]
        trailing = inner[len(inner.rstrip()) :]
        new_value = f"{leading}{new_value}{trailing}"
        end += 1  # 闭引号也纳入替换范围，统一重建 token
        start -= 1
        new_token = quote + new_value + quote
    else:
        new_token = new_value
    return tag_text[:start] + new_token + tag_text[end:]


def rewrite_anchor_hrefs(html: str, rewrite) -> str:
    """逐个改写页面内的 ``<a href>``。

    ``rewrite(raw_href)`` 收到保留 HTML 实体与空白的原始值，返回新的
    href 文本（去除首尾空白后的形式）或 ``None``（不改）。
    除 <a href> 之外的任何字节（正文、注释、其他标签、属性排版、HTML 实体、
    其他属性值里形似 ``href=`` 的文本）都原样保留；无任何 href 变化时返回原字符串。
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
