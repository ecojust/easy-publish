"""Article template catalog, legacy typography and native-platform adaptation.

Official Wenyan templates render asynchronously through wenyan_runtime. The old
six template IDs keep their original output for saved choices and batch snapshots.
Markdown HTML stays disabled; user CSS and template code are never evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from typing import Literal

import bleach
from markdown_it import MarkdownIt


ArticleTemplateId = Literal["clean", "editorial", "journal", "tech", "forest", "artdeco",
                            "wenyan-default", "wenyan-orangeheart", "wenyan-rainbow", "wenyan-lapis",
                            "wenyan-pie", "wenyan-maize", "wenyan-purple", "wenyan-phycat"]
LEGACY_ARTICLE_TEMPLATES = [
    {"id": "clean", "name": "清爽蓝", "description": "蓝色章节条、清晰列表与留白，适合知识分享", "accent": "#2563eb"},
    {"id": "editorial", "name": "墨色杂志", "description": "米白纸面、衬线标题与双线装饰，适合深度阅读", "accent": "#9a3412"},
    {"id": "journal", "name": "暖色手记", "description": "暖色标题卡、圆角引用与照片边框，适合生活记录", "accent": "#b45309"},
    {"id": "tech", "name": "理性科技", "description": "深青标题、终端代码块与规整表格，适合技术文章", "accent": "#0f766e"},
    {"id": "forest", "name": "森林来信", "description": "浅绿纸面、圆角边框与柔和强调，适合自然和人文", "accent": "#2f6b4f"},
    {"id": "artdeco", "name": "黑金画报", "description": "黑金标题带、几何边框与高对比引用，适合专题策划", "accent": "#b7791f"},
]
WENYAN_ARTICLE_TEMPLATES = [
    {"id": "wenyan-default", "name": "默认经典", "description": "Wenyan 官方默认主题，清晰简洁的文章排版", "accent": "#000000"},
    {"id": "wenyan-orangeheart", "name": "橙心", "description": "Wenyan Orange Heart，温暖醒目的橙色主题", "accent": "#ef7060"},
    {"id": "wenyan-rainbow", "name": "彩虹", "description": "Wenyan Rainbow，丰富的章节色彩", "accent": "#e26d6d"},
    {"id": "wenyan-lapis", "name": "青金石", "description": "Wenyan Lapis，沉静的蓝色阅读主题", "accent": "#4870ac"},
    {"id": "wenyan-pie", "name": "思源", "description": "Wenyan Pie，简约有序的内容排版", "accent": "#3b5368"},
    {"id": "wenyan-maize", "name": "玉米", "description": "Wenyan Maize，明快柔和的暖色主题", "accent": "#f2c94c"},
    {"id": "wenyan-purple", "name": "紫色", "description": "Wenyan Purple，鲜明的紫色层次", "accent": "#7c4dff"},
    {"id": "wenyan-phycat", "name": "物理猫", "description": "Wenyan Phycat，精致的文章装饰与排版", "accent": "#00b2ff"},
]
WENYAN_TEMPLATE_IDS = frozenset(item["id"] for item in WENYAN_ARTICLE_TEMPLATES)
ARTICLE_TEMPLATES = WENYAN_ARTICLE_TEMPLATES + LEGACY_ARTICLE_TEMPLATES
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
    # Fixed palettes and layout decisions, not user CSS or remotely loaded skins.
    palette = {
        "clean": ("#ffffff", "#334155", "#172554", "#2563eb", "#eff6ff", "#dbeafe", "#f8faff"),
        "editorial": ("#fbfaf7", "#44403c", "#292524", "#9a3412", "#f2ede6", "#d6cfc4", "#f5f1ea"),
        "journal": ("#fffaf0", "#575045", "#78350f", "#b45309", "#fff0cf", "#ead6b3", "#fff7e7"),
        "tech": ("#ffffff", "#334155", "#123b3a", "#0f766e", "#eaf8f5", "#c3e4dc", "#f2faf8"),
        "forest": ("#f1f7f2", "#374b40", "#214735", "#2f6b4f", "#e0eee3", "#b8d1bd", "#eaf3ec"),
        "artdeco": ("#fffcf4", "#403a2d", "#241f16", "#b7791f", "#fff0bd", "#d9be76", "#fff6da"),
    }[template_id]
    paper, ink, heading, accent, soft, border, stripe = palette
    serif = template_id in {"editorial", "journal"}
    body = {"font-family": _SERIF if serif else _SANS, "font-size": "16px", "line-height": "1.9",
            "color": ink, "background-color": paper, "letter-spacing": "0.3px", "max-width": "680px",
            "margin": "0 auto", "padding": "24px 20px", "box-sizing": "border-box", "overflow-wrap": "break-word"}
    headings = {"font-family": _SERIF if serif else _SANS, "font-weight": "700", "line-height": "1.5",
                "color": heading, "margin": "32px 0 16px", "word-break": "break-word"}
    styles = {
        "p": {"margin": "0 0 20px", "line-height": "1.9", "text-align": "justify"},
        "h1": {**headings, "font-size": "29px", "margin": "12px 0 28px", "padding-bottom": "18px", "border-bottom": f"2px solid {accent}"},
        "h2": {**headings, "font-size": "22px", "color": accent, "background-color": soft,
               "border-left": f"4px solid {accent}", "padding": "10px 14px", "border-radius": "0 6px 6px 0"},
        "h3": {**headings, "font-size": "19px", "color": heading, "padding-bottom": "7px", "border-bottom": f"1px solid {border}"},
        "h4": {**headings, "font-size": "17px", "color": accent},
        "h5": {**headings, "font-size": "16px"},
        "h6": {**headings, "font-size": "15px", "color": "#596579"},
        "blockquote": {"margin": "26px 0", "padding": "18px 20px", "border-left": f"3px solid {accent}",
                       "background-color": soft, "color": ink, "border-radius": "0 10px 10px 0"},
        "quote_p": {"margin": "0 0 10px", "line-height": "1.85"},
        "quote_emphasis": {"color": heading},
        "quote_code": {"color": heading, "background-color": paper, "border": f"1px solid {border}"},
        "strong": {"font-weight": "700", "color": heading, "background-color": soft, "padding": "0 2px"},
        "em": {"font-style": "italic"}, "s": {"text-decoration": "line-through"},
        "ul": {"padding-left": "25px", "margin": "18px 0 24px", "list-style-type": "disc"},
        "ol": {"padding-left": "28px", "margin": "18px 0 24px", "list-style-type": "decimal"},
        "li": {"margin": "9px 0", "padding-left": "4px", "line-height": "1.85"},
        "a": {"color": accent, "text-decoration": "underline", "word-break": "break-all"},
        "img": {"display": "block", "max-width": "100%", "height": "auto", "margin": "24px auto 12px", "border-radius": "8px", "box-sizing": "border-box"},
        "caption": {"display": "block", "font-size": "13px", "line-height": "1.65", "text-align": "center",
                    "color": "#68717d", "margin": "0 4px 22px", "letter-spacing": "0.2px"},
        "hr": {"border": "0", "border-top": f"1px solid {border}", "margin": "36px 0"},
        "pre": {"font-family": _MONO, "font-size": "13px", "line-height": "1.75", "color": heading,
                "background-color": soft, "border": f"1px solid {border}", "border-top": f"3px solid {accent}",
                "padding": "18px 16px", "margin": "26px 0", "border-radius": "8px",
                "white-space": "pre-wrap", "word-break": "break-word", "overflow-wrap": "anywhere"},
        "code": {"font-family": _MONO, "font-size": "14px", "background-color": soft, "color": accent,
                 "padding": "2px 5px", "border": f"1px solid {border}", "border-radius": "4px", "word-break": "break-word"},
        "pre_code": {"font-family": _MONO, "font-size": "13px", "line-height": "1.75", "color": heading,
                     "background-color": soft, "padding": "0", "margin": "0", "border": "0", "border-radius": "0",
                     "white-space": "pre-wrap", "word-break": "break-word", "overflow-wrap": "anywhere"},
        "table": {"width": "100%", "table-layout": "fixed", "border-collapse": "collapse", "margin": "26px 0", "font-size": "14px"},
        "th": {"border": f"1px solid {border}", "padding": "11px 10px", "text-align": "left", "background-color": accent,
               "font-weight": "700", "color": "#ffffff", "word-break": "break-word", "line-height": "1.6"},
        "td": {"border": f"1px solid {border}", "padding": "10px", "text-align": "left", "color": ink,
               "background-color": paper, "word-break": "break-word", "line-height": "1.7"},
        "stripe": {"background-color": stripe},
    }
    if template_id == "editorial":
        body.update({"font-size": "17px", "line-height": "2", "padding": "28px 22px"})
        styles["p"].update({"line-height": "2", "letter-spacing": "0.5px"})
        styles["h1"].update({"font-size": "28px", "font-weight": "600", "text-align": "center", "letter-spacing": "3px",
                              "padding": "24px 8px", "border-top": f"3px double {heading}", "border-bottom": f"3px double {heading}"})
        styles["h2"] = {**headings, "font-size": "23px", "font-weight": "600", "letter-spacing": "2px",
                        "padding": "10px 0", "border-top": f"1px solid {border}", "border-bottom": f"1px solid {border}"}
        styles["blockquote"].update({"background-color": "#f2ede6", "border-left": f"2px solid {accent}", "border-radius": "0", "font-style": "italic"})
        styles["strong"].update({"color": accent, "background-color": "transparent", "padding": "0", "border-bottom": f"1px dotted {accent}"})
        styles["img"].update({"border-radius": "0", "border": "1px solid #e7e0d6", "padding": "5px", "background-color": "#ffffff"})
        styles["th"].update({"background-color": heading})
        styles["pre"].update({"border-radius": "0", "border-top": f"1px solid {border}"})
    elif template_id == "journal":
        styles["h1"].update({"font-size": "27px", "padding": "20px 18px", "background-color": "#fff0cf", "border": f"1px solid {border}", "border-radius": "14px"})
        styles["h2"] = {**headings, "font-size": "21px", "display": "inline-block", "background-color": "#f9e3ae", "color": heading,
                        "padding": "7px 16px", "border-radius": "18px"}
        styles["h3"].update({"border-bottom": f"1px dashed {border}"})
        styles["blockquote"].update({"border": f"1px dashed {accent}", "border-left": f"1px dashed {accent}", "border-radius": "14px", "padding": "20px"})
        styles["li"].update({"padding": "6px 8px", "background-color": "#fff5df", "border-radius": "6px"})
        styles["img"].update({"border": "5px solid #ffffff", "box-shadow": "0 2px 7px #e6d8bf", "border-radius": "6px"})
        styles["pre"].update({"border-top": f"1px solid {border}", "border-radius": "12px"})
        styles["code"]["color"] = "#92400e"
    elif template_id == "tech":
        styles["h1"].update({"font-size": "27px", "color": "#ffffff", "background-color": heading, "padding": "22px 18px",
                              "border-bottom": "4px solid #78cbb7", "border-radius": "4px"})
        styles["h2"] = {**headings, "font-size": "21px", "color": accent, "padding": "8px 0 10px",
                        "border-bottom": "2px solid #8cd5c3", "font-family": _SANS}
        styles["h3"].update({"border-bottom": "0", "border-left": f"3px solid {accent}", "padding": "0 0 0 10px"})
        styles["blockquote"].update({"border": "1px solid #b8ded4", "border-left": f"4px solid {accent}", "border-radius": "4px"})
        styles["ul"]["list-style-type"] = "square"
        styles["pre"].update({"background-color": "#142f35", "color": "#e0f2ed", "border": "1px solid #244b51", "border-top": "4px solid #0f766e"})
        styles["pre_code"].update({"background-color": "#142f35", "color": "#e0f2ed"})
        styles["th"].update({"background-color": heading})
    elif template_id == "forest":
        body["padding"] = "24px 20px"
        styles["h1"].update({"font-size": "27px", "text-align": "center", "padding": "22px 16px", "background-color": "#ffffff",
                              "border": f"2px solid {accent}", "border-radius": "12px", "box-shadow": "3px 3px 0 #c4dac9"})
        styles["h2"].update({"color": heading, "border-left": f"5px solid {accent}", "background-color": "#ffffff", "border-radius": "0 12px 12px 0"})
        styles["blockquote"].update({"border": f"1px solid {border}", "border-left": f"1px solid {border}", "border-radius": "14px"})
        styles["strong"].update({"background-color": "#e6dcb8", "color": heading})
        styles["img"].update({"border": "1px solid #aec7b4", "border-radius": "12px", "padding": "4px", "background-color": "#ffffff"})
        styles["pre"].update({"background-color": "#213d30", "color": "#e5f0e8", "border": "1px solid #37604b"})
        styles["pre_code"].update({"background-color": "#213d30", "color": "#e5f0e8"})
        styles["hr"].update({"border-top": f"3px double {accent}"})
    elif template_id == "artdeco":
        body.update({"padding": "24px 20px", "letter-spacing": "0.5px"})
        styles["h1"].update({"font-size": "28px", "font-weight": "800", "text-align": "center", "letter-spacing": "2px",
                              "background-color": "#f4cf65", "padding": "22px 14px", "border": f"3px solid {heading}",
                              "box-shadow": "4px 4px 0 #241f16"})
        styles["h2"] = {**headings, "font-size": "22px", "font-weight": "800", "color": "#f8df93", "background-color": heading,
                        "padding": "11px 15px", "border-bottom": "3px solid #e8bc42"}
        styles["h3"].update({"border-bottom": "2px solid #d9b64c"})
        styles["blockquote"].update({"background-color": "#302b20", "color": "#fff6d9", "border": "1px solid #d6ad40",
                                     "border-left": "4px solid #e8bc42", "border-radius": "0"})
        styles["quote_emphasis"]["color"] = "#ffe195"
        styles["quote_code"].update({"color": "#241f16", "background-color": "#ffe8a7", "border": "1px solid #d6ad40"})
        styles["strong"].update({"color": heading, "background-color": "#f6dc8c", "padding": "1px 4px"})
        styles["img"].update({"border": f"2px solid {heading}", "border-radius": "0", "box-shadow": "3px 3px 0 #e5bf55"})
        styles["pre"].update({"background-color": "#29251c", "color": "#fff0bb", "border": "2px solid #29251c", "border-top": "4px solid #e6bd50", "border-radius": "0"})
        styles["pre_code"].update({"background-color": "#29251c", "color": "#fff0bb"})
        styles["th"].update({"background-color": heading, "color": "#ffe5a0", "border": f"1px solid {heading}"})
        styles["hr"].update({"border-top": "4px double #b7791f"})
        for tag in ("a", "code", "h4"):
            styles[tag]["color"] = "#80540b"
    return body, styles

def _style(values: dict[str, str]) -> str:
    return ";".join(f"{key}:{value}" for key, value in values.items())


class _Renderer:
    def __init__(self, template_id: str, platform_id: str | None):
        self.platform_id = platform_id
        self.styled = platform_id in {None, "wechat", "dayu"}
        self.rich = platform_id in {None, "wechat"}
        self.ancestors: list[str] = []
        self.body_style, self.styles = _theme(template_id)

    def element(self, tag: str, content: str, attrs: dict[str, str] | None = None, style_tag: str | None = None,
                extra_style: dict[str, str] | None = None) -> str:
        attrs = dict(attrs or {})
        if self.styled and self.styles.get(style_tag or tag):
            style_key = style_tag or tag
            values = dict(self.styles[style_key])
            # Quote text inherits its card color; inline emphasis must not bring
            # a dark foreground or pale highlight onto a dark quote card.
            regions = {"blockquote", "table", "pre"} if self.rich else {"blockquote", "pre"}
            region = next((ancestor for ancestor in reversed(self.ancestors) if ancestor in regions), None)
            if region == "blockquote":
                if tag == "p":
                    values.update(self.styles["quote_p"])
                elif style_key in {"strong", "a"}:
                    values.update(self.styles["quote_emphasis"])
                    values["background-color"] = "transparent"
                elif style_key == "code":
                    values.update(self.styles["quote_code"])
                elif tag.startswith("h") and tag[1:] in {"1", "2", "3", "4", "5", "6"}:
                    values.update({"color": self.styles["blockquote"]["color"], "background-color": "transparent"})
                elif style_tag == "caption":
                    values["color"] = self.styles["blockquote"]["color"]
            heading = next((ancestor for ancestor in reversed(self.ancestors) if ancestor in {"h1", "h2", "h3", "h4", "h5", "h6"}), None)
            if (heading or "th" in self.ancestors) and style_key in {"a", "strong", "em", "s"}:
                # Dayu translates colors into font attributes instead of letting
                # the browser cascade them, so resolve its inherited ink here.
                color = "inherit" if self.rich else self.styles["blockquote" if region == "blockquote" else heading]["color"]
                values.update({"color": color, "background-color": "transparent"})
            if "th" in self.ancestors and (tag == "code" or style_tag == "caption"):
                values.update({"color": "inherit", "background-color": "transparent"})
                if tag == "code":
                    values["border"] = "1px solid " + self.styles["th"]["color"]
            values.update(extra_style or {})
            attrs["style"] = _style(values)
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
        self.ancestors.append(node.tag)
        try:
            return self.render_node(node, depth)
        finally:
            self.ancestors.pop()

    def styled_table(self, node: _Node, depth: int) -> str:
        headers, rows = [], []
        row_number = 0
        for row in self.table_rows(node):
            cells = [cell for cell in row.children if isinstance(cell, _Node) and cell.tag in {"th", "td"}]
            is_header = bool(cells) and all(cell.tag == "th" for cell in cells)
            stripe = None if is_header or row_number % 2 == 0 else self.styles["stripe"]
            rendered_cells = []
            for cell in cells:
                self.ancestors.append(cell.tag)
                try:
                    rendered_cells.append(self.element(cell.tag, self.children(cell, depth), cell.attrs, extra_style=stripe))
                finally:
                    self.ancestors.pop()
            line = "".join(rendered_cells)
            (headers if is_header else rows).append(self.element("tr", line))
            if not is_header:
                row_number += 1
        content = (self.element("thead", "".join(headers)) if headers else "") + self.element("tbody", "".join(rows))
        return self.element("table", content)

    def render_node(self, node: _Node, depth: int = 0) -> str:
        tag, attrs = node.tag, dict(node.attrs)
        if tag == "root":
            return self.children(node, depth)
        if tag == "table" and self.rich:
            return self.styled_table(node, depth)
        if tag == "img" and self.rich:
            image = self.element("img", "", attrs)
            # A Markdown image title is explicit author-provided caption text.
            # Keep it inline-safe even when an image occurs inside a paragraph,
            # table cell or link; never invent a caption from alt text/filenames.
            caption = attrs.get("title", "").strip()
            return image + (self.element("span", escape(caption), style_tag="caption") if caption else "")
        if tag == "code" and "pre" in self.ancestors and self.styled:
            return self.element("code", self.children(node, depth), attrs, style_tag="pre_code")
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


def markdown_html(body: str) -> str:
    """Parse safe Markdown structure without starting a typography runtime."""
    markdown = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
    raw = markdown.render(body)
    return bleach.clean(raw, tags=_TAGS,
                        attributes={"a": ["href", "title"], "img": ["src", "alt", "title"], "ol": ["start"]},
                        protocols={"https", "http", "mailto", "easy-asset"}, strip=True)


def render_article(body: str, template_id: str = "clean", platform_id: str | None = None) -> str:
    """Legacy typography and native-platform adaptation; Wenyan is asynchronous."""
    if template_id not in TEMPLATE_IDS:
        raise ValueError("文章模板不存在，请重新选择")
    if platform_id is not None and platform_id not in ARTICLE_PLATFORMS:
        raise ValueError("此平台不支持文章格式转换")
    if template_id in WENYAN_TEMPLATE_IDS:
        if platform_id in {None, "wechat"}:
            raise ValueError("Wenyan 模板需要通过文章预览服务渲染")
        # Native editors control their own typography; retain the established
        # safe platform structure instead of carrying WeChat theme CSS across.
        template_id = "clean"
    document = _Document(markdown_html(body))
    renderer = _Renderer(template_id, platform_id)
    content = renderer.render(document.root)
    if not content.strip():
        return ""
    if renderer.styled:
        return '<section style="' + escape(_style(renderer.body_style), quote=True) + '">' + content + "</section>"
    return content
