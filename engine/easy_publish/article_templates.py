"""Built-in article typography and deterministic platform adaptation.

Only this module supplies CSS. Markdown HTML stays disabled; no user-supplied
style, script, external font or template code is evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from typing import Literal

import bleach
from markdown_it import MarkdownIt


ArticleTemplateId = Literal["clean", "editorial", "journal", "tech"]
ARTICLE_TEMPLATES = [
    {"id": "clean", "name": "清爽蓝", "description": "蓝色标题与留白，适合知识分享和日常文章", "accent": "#2563eb"},
    {"id": "editorial", "name": "墨色杂志", "description": "衬线标题与细分隔线，适合观点和深度阅读", "accent": "#9a3412"},
    {"id": "journal", "name": "暖色手记", "description": "暖色小标题与柔和引用，适合故事和生活记录", "accent": "#b45309"},
    {"id": "tech", "name": "理性科技", "description": "青绿色层次与清晰代码块，适合技术和产品内容", "accent": "#0f766e"},
]
TEMPLATE_IDS = frozenset(item["id"] for item in ARTICLE_TEMPLATES)
ARTICLE_PLATFORMS = frozenset({"wechat", "zhihu", "dayu", "xueqiu"})

_SANS = '-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif'
_SERIF = 'Georgia,"Songti SC","SimSun",serif'
_MONO = 'Menlo,Consolas,"Liberation Mono",monospace'
_VOID = frozenset({"br", "hr", "img"})
_TAGS = frozenset({"p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code",
                   "strong", "em", "s", "ul", "ol", "li", "a", "img", "hr", "table", "thead", "tbody", "tr", "td", "th"})


def article_format_notes(platform_id: str | None = None) -> list[str]:
    if platform_id is None:
        return ["此处展示模板效果；发布时会按所选平台的编辑器能力转换格式。"]
    if platform_id not in ARTICLE_PLATFORMS:
        raise ValueError("此平台不支持文章格式预览")
    return {
        "wechat": ["公众号使用内联排版样式；平台可能调整字体、间距或外部链接显示。"],
        "zhihu": ["知乎使用平台原生字体和颜色，保留标题、强调、列表、引用与代码结构。",
                  "表格按行转换为段落，分隔线转换为文字分隔线。"],
        "dayu": ["大鱼号会将模板颜色和背景转换成平台可保存的格式，字号、边框和间距可能有所调整。",
                 "代码、表格转为可读段落；链接保留文字和地址，斜体改为加粗强调。"],
        "xueqiu": ["雪球使用平台原生样式，标题统一为小标题，列表保留序号或圆点。",
                   "引用、代码和表格转为段落；模板字体及颜色不会保留。"],
    }[platform_id]


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node | str"] = field(default_factory=list)


class _Document(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def _text(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    if node.tag == "br":
        return "\n"
    return "".join(_text(child) for child in node.children)


def _theme(template_id: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    accent = next(item["accent"] for item in ARTICLE_TEMPLATES if item["id"] == template_id)
    serif = template_id in {"editorial", "journal"}
    soft = {"clean": "#eff6ff", "editorial": "#faf5f1", "journal": "#fffbeb", "tech": "#f0fdfa"}[template_id]
    body = {"font-family": _SERIF if serif else _SANS, "font-size": "16px", "line-height": "1.85",
            "color": "#334155", "letter-spacing": "0.2px", "max-width": "680px", "margin": "0 auto",
            "padding": "8px 4px", "box-sizing": "border-box", "overflow-wrap": "break-word"}
    if template_id == "editorial":
        body.update({"font-size": "17px", "line-height": "1.95", "color": "#292524"})
    headings = {"font-family": _SERIF if serif else _SANS, "font-weight": "700", "line-height": "1.45",
                "color": "#172033", "margin": "32px 0 16px", "word-break": "break-word"}
    styles = {
        "p": {"margin": "0 0 18px", "line-height": body["line-height"]},
        "h1": {**headings, "font-size": "28px", "margin": "28px 0 20px"},
        "h2": {**headings, "font-size": "22px", "color": accent, "border-left": f"4px solid {accent}", "padding-left": "12px"},
        "h3": {**headings, "font-size": "19px", "color": accent},
        "h4": {**headings, "font-size": "17px"},
        "h5": {**headings, "font-size": "16px"},
        "h6": {**headings, "font-size": "16px", "color": "#64748b"},
        "blockquote": {"margin": "24px 0", "padding": "16px 18px", "border-left": f"3px solid {accent}",
                       "background-color": soft, "color": "#475569", "border-radius": "0 8px 8px 0"},
        "strong": {"font-weight": "700", "color": "#172033"},
        "em": {"font-style": "italic"}, "s": {"text-decoration": "line-through"},
        "ul": {"padding-left": "24px", "margin": "16px 0 22px", "list-style-type": "disc"},
        "ol": {"padding-left": "24px", "margin": "16px 0 22px", "list-style-type": "decimal"},
        "li": {"margin": "8px 0", "line-height": body["line-height"]},
        "a": {"color": accent, "text-decoration": "underline", "word-break": "break-all"},
        "img": {"display": "block", "max-width": "100%", "height": "auto", "margin": "22px auto", "border-radius": "8px"},
        "hr": {"border": "0", "border-top": "1px solid #cbd5e1", "margin": "32px 0"},
        "pre": {"font-family": _MONO, "font-size": "14px", "line-height": "1.7", "color": "#0f172a",
                "background-color": "#f1f5f9", "padding": "18px", "margin": "24px 0", "border-radius": "10px",
                "white-space": "pre-wrap", "word-break": "break-word"},
        "code": {"font-family": _MONO, "font-size": "14px", "background-color": soft, "color": accent},
        "table": {"width": "100%", "table-layout": "fixed", "border-collapse": "collapse", "margin": "24px 0", "font-size": "14px"},
        "th": {"border": "1px solid #cbd5e1", "padding": "10px 12px", "text-align": "left", "background-color": soft,
               "font-weight": "700", "color": "#172033", "word-break": "break-word"},
        "td": {"border": "1px solid #cbd5e1", "padding": "10px 12px", "text-align": "left", "word-break": "break-word"},
    }
    if template_id == "editorial":
        styles["h2"] = {**headings, "font-size": "24px", "color": "#1c1917", "padding-bottom": "12px", "border-bottom": "1px solid #a8a29e"}
        styles["blockquote"].update({"font-style": "italic", "background-color": "#fafaf9", "border-left": "2px solid #a8a29e"})
    elif template_id == "journal":
        styles["h2"].update({"border-left": "0", "padding": "10px 14px", "background-color": "#fef3c7", "border-radius": "8px"})
        styles["blockquote"].update({"border-left": "0", "border-radius": "12px", "color": "#78350f"})
    elif template_id == "tech":
        styles["h2"] = {**headings, "font-size": "21px", "color": accent, "padding-bottom": "10px", "border-bottom": "2px solid #99f6e4"}
        styles["pre"].update({"background-color": "#f0fdfa", "border": "1px solid #99f6e4"})
    return body, styles


def _style(values: dict[str, str]) -> str:
    return ";".join(f"{key}:{value}" for key, value in values.items())


class _Renderer:
    def __init__(self, template_id: str, platform_id: str | None):
        self.platform_id = platform_id
        self.styled = platform_id in {None, "wechat", "dayu"}
        self.body_style, self.styles = _theme(template_id)

    def element(self, tag: str, content: str, attrs: dict[str, str] | None = None, style_tag: str | None = None) -> str:
        attrs = dict(attrs or {})
        if self.styled and self.styles.get(style_tag or tag):
            attrs["style"] = _style(self.styles[style_tag or tag])
        attr_html = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in attrs.items())
        return f"<{tag}{attr_html}>" if tag in _VOID else f"<{tag}{attr_html}>{content}</{tag}>"

    def children(self, node: _Node, depth: int = 0) -> str:
        return "".join(self.render(child, depth) for child in node.children)

    def table_rows(self, node: _Node):
        for child in node.children:
            if not isinstance(child, _Node):
                continue
            if child.tag == "tr":
                yield child
            else:
                yield from self.table_rows(child)

    def native_list(self, node: _Node, depth: int) -> str:
        result = []
        number = int(node.attrs.get("start", "1"))
        for item in node.children:
            if not isinstance(item, _Node) or item.tag != "li":
                continue
            prefix = "　" * depth + (f"{number}. " if node.tag == "ol" else "• ")
            number += 1
            contents = list(item.children)
            while contents and isinstance(contents[0], str) and not contents[0].strip():
                contents.pop(0)
            while contents and isinstance(contents[-1], str) and not contents[-1].strip():
                contents.pop()
            # Markdown loose lists wrap their first paragraph; put the visible
            # number inside it, preserving nested lists and image positions.
            if contents and isinstance(contents[0], _Node) and contents[0].tag == "p":
                first = contents.pop(0)
                result.append(self.element("p", escape(prefix) + self.children(first, depth + 1)))
            else:
                inline = []
                while contents and not (isinstance(contents[0], _Node) and contents[0].tag in {"ul", "ol", "p", "blockquote", "pre", "table"}):
                    inline.append(contents.pop(0))
                result.append(self.element("p", escape(prefix) + "".join(self.render(part, depth + 1) for part in inline)))
            result.extend(self.render(part, depth + 1) for part in contents)
        return "".join(result)

    def render(self, node: _Node | str, depth: int = 0) -> str:
        if isinstance(node, str):
            return escape(node)
        tag, attrs = node.tag, dict(node.attrs)
        if tag == "root":
            return self.children(node, depth)
        if tag == "table" and self.platform_id in {"zhihu", "dayu", "xueqiu"}:
            rows = []
            for row in self.table_rows(node):
                cells = [cell for cell in row.children if isinstance(cell, _Node) and cell.tag in {"td", "th"}]
                rendered = [self.element("strong", self.children(cell, depth)) if cell.tag == "th" else self.children(cell, depth) for cell in cells]
                rows.append(self.element("p", "　｜　".join(rendered)))
            return "".join(rows)
        if tag == "pre" and self.platform_id in {"dayu", "xueqiu"}:
            lines = _text(node).rstrip("\n").split("\n")
            code = "<br>".join(escape(line).replace(" ", "&#160;") for line in lines)
            return self.element("blockquote" if self.platform_id == "dayu" else "p", code, style_tag="pre")
        if self.platform_id == "dayu":
            if tag == "a":
                href = attrs.get("href", "")
                body = self.children(node, depth)
                if href and href != _text(node):
                    body += "（" + escape(href) + "）"
                return self.element("span", body, style_tag="a")
            if tag == "code":
                return self.element("span", self.children(node, depth), style_tag="code")
            if tag == "em":
                tag = "strong"
        if self.platform_id == "xueqiu":
            if tag in {"ul", "ol"}:
                return self.native_list(node, depth)
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                tag = "h4"
            elif tag == "blockquote":
                tag = "div"
            elif tag == "code":
                tag = "span"
        if tag == "s" and self.platform_id in {"zhihu", "dayu", "xueqiu"}:
            return self.element("span", "~~" + self.children(node, depth) + "~~")
        if tag == "hr" and self.platform_id in {"zhihu", "xueqiu"}:
            return self.element("p", "────────")
        # Native editors control typography; use supported heading levels so
        # source headings remain headings instead of styled generic paragraphs.
        if self.platform_id == "zhihu" and tag in {"h1", "h4", "h5", "h6"}:
            tag = "h2" if tag == "h1" else "h3"
        return self.element(tag, self.children(node, depth), attrs)


def render_article(body: str, template_id: str = "clean", platform_id: str | None = None) -> str:
    if template_id not in TEMPLATE_IDS:
        raise ValueError("文章模板不存在，请重新选择")
    if platform_id is not None and platform_id not in ARTICLE_PLATFORMS:
        raise ValueError("此平台不支持文章格式转换")
    markdown = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
    raw = markdown.render(body)
    safe = bleach.clean(raw, tags=_TAGS,
                        attributes={"a": ["href", "title"], "img": ["src", "alt", "title"], "ol": ["start"]},
                        protocols={"https", "http", "mailto", "easy-asset"}, strip=True)
    document = _Document(safe)
    renderer = _Renderer(template_id, platform_id)
    content = renderer.render(document.root)
    if not content.strip():
        return ""
    if renderer.styled:
        return '<section style="' + escape(_style(renderer.body_style), quote=True) + '">' + content + "</section>"
    return content
