"""Template rendering, platform compatibility and official API output fixtures."""
import asyncio
from html import escape, unescape
import re

from PIL import Image
import pytest
from pydantic import ValidationError

from easy_publish.article_templates import LEGACY_ARTICLE_TEMPLATES as ARTICLE_TEMPLATES, article_format_notes, render_article
from easy_publish.content import ContentInput, image_sources
from easy_publish.providers import social, wechat
from easy_publish.providers.base import ProviderContext, PublishPayload
from easy_publish.vendor.social_auto_upload.utils.article_render import load_article
from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader.article_content import load_dayu_article
from easy_publish.vendor.social_auto_upload.uploader.xueqiu_uploader.content import load_xueqiu_article


SOURCE = """# 一级标题

段落里的 **重点**、*斜体*、~~旧说法~~ 和 `inline_code`。

## 第二节

> 保留引用内容。

3. 第一项
4. 第二项
   - 嵌套项目

[参考资料](https://example.org/article?a=1&b=2)

![正文图片](easy-asset://image-a)

```python
if left < right:
    print("原样代码")
```

| 名称 | 内容 |
| --- | --- |
| 第一列 | 表格单元格 |
| 图片 | ![表格图片](easy-asset://image-a) |

---

文章结束。
"""


@pytest.mark.parametrize("template", [item["id"] for item in ARTICLE_TEMPLATES])
def test_templates_are_self_contained_and_preserve_rich_markdown(template):
    html = render_article(SOURCE, template)
    assert "<section style=" in html
    assert "<table style=" in html
    assert "<pre style=" in html and "&lt;" in html
    assert 'start="3"' in html
    assert image_sources(html) == ["easy-asset://image-a", "easy-asset://image-a"]
    assert next(item["accent"] for item in ARTICLE_TEMPLATES if item["id"] == template) in html
    assert "<style" not in html and "<script" not in html
    assert "url(" not in html and "@import" not in html


def test_templates_produce_distinct_typography_without_changing_text():
    rendered = [render_article(SOURCE, item["id"]) for item in ARTICLE_TEMPLATES]
    assert len(set(rendered)) == 6
    text = [unescape(re.sub(r"<[^>]+>", "", html)) for html in rendered]
    assert len(set(text)) == 1


@pytest.mark.parametrize("template", [item["id"] for item in ARTICLE_TEMPLATES])
@pytest.mark.parametrize("platform", ["wechat", "zhihu", "dayu", "xueqiu"])
def test_target_html_passes_real_adapter_loaders_and_keeps_image_order(tmp_path, template, platform):
    image = tmp_path / "image.png"
    Image.new("RGB", (4, 4), "blue").save(image)
    body = render_article(SOURCE, template, platform)
    payload = PublishPayload(platform, "fixture-account", "article", "publish", "Separate title",
                             body_html=body, images={"image-a": image})
    if platform == "wechat":
        parsed = wechat._body(body)
        assert parsed.image_ids == ["image-a"]
        assert "".join(parsed.output) == body
        return
    path = social._prepare_html(payload, tmp_path)
    loader = {"dayu": load_dayu_article, "xueqiu": load_xueqiu_article, "zhihu": load_article}[platform]
    document = loader(path)
    assert len(document.images) == 2
    plain = "".join(document.text)
    for phrase in ("一级标题", "保留引用内容", "第一项", "第二项", "嵌套项目", "原样代码", "表格单元格", "文章结束"):
        assert phrase in plain
    assert document.sequence.count("\x00IMG\x00") == 2
    if platform in {"zhihu", "xueqiu"}:
        assert "style=" not in document.content_html
    if platform == "xueqiu":
        assert "3. 第一项" in plain and "4. 第二项" in plain and "• 嵌套项目" in plain
        assert "h4" in document.tags
    if platform == "dayu":
        assert "https://example.org/article?a=1&b=2" in plain
        assert not {"a", "pre", "code", "table", "em", "s"} & set(document.tags)


def test_raw_html_and_css_are_literal_and_cannot_override_template():
    source = '<script>alert("x")</script>\n\n<img src="https://bad.test/pixel" onerror="bad()">\n\n<style>body { position:fixed; background:url(https://bad.test) }</style>'
    html = render_article(source, "journal", "wechat")
    assert "<script" not in html and "<img src=" not in html and "<style>" not in html
    assert "&lt;script&gt;" in html and "&lt;style&gt;" in html
    assert image_sources(html) == []


@pytest.mark.parametrize("destination", ["javascript:alert(1)", "data:text/html,unsafe", "vbscript:bad"])
def test_active_link_schemes_never_become_attributes(destination):
    html = render_article(f"[link]({destination})", "clean", "wechat")
    assert f'href="{destination}' not in html


def test_plain_source_and_selection_remain_separate_data():
    value = ContentInput(kind="article", body=SOURCE)
    assert value.articleTemplateId == "clean" and value.body == SOURCE
    chosen = ContentInput(kind="article", body=SOURCE, articleTemplateId="tech")
    assert chosen.model_dump()["articleTemplateId"] == "tech"
    with pytest.raises(ValidationError):
        ContentInput(kind="article", articleTemplateId="custom:<script>")
    with pytest.raises(ValueError):
        render_article(SOURCE, "missing")
    with pytest.raises(ValueError):
        render_article(SOURCE, "clean", "douyin")
    assert render_article("", "clean", "wechat") == ""


def test_platform_notes_describe_native_style_limits():
    assert any("字体和颜色" in note for note in article_format_notes("zhihu"))
    assert any("不会保留" in note for note in article_format_notes("xueqiu"))
    assert any("转换" in note for note in article_format_notes("dayu"))


def test_official_api_receives_selected_template_and_replaced_images(tmp_path):
    from test_wechat import OfficialAPI, invoke
    wechat._TOKEN_CACHE.clear()
    image = tmp_path / "cover.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture-only-image")
    events = []
    ctx = ProviderContext(tmp_path / "session", tmp_path / "work", None, events.append)
    payload = PublishPayload("wechat", "template-fixture", "article", "draft", "Title stays separate",
                             body_html=render_article("## 模板标题\n\n![图片](easy-asset://cover)\n\n正文", "tech", "wechat"),
                             images={"cover": image}, cover_path=image,
                             credentials={"appId": "fixture-app", "appSecret": "fixture-secret"})
    api = OfficialAPI(events)
    try:
        result = asyncio.run(invoke(api, payload, ctx))
        assert result.status == "draft_created"
        article = api.json_for("/cgi-bin/draft/add")["articles"][0]
        assert article["title"] == "Title stays separate"
        assert "Title stays separate" not in article["content"]
        assert "#0f766e" in article["content"] and "style=" in article["content"]
        assert "easy-asset://" not in article["content"]
        assert "https://mmbiz.qpic.cn/" in article["content"]
    finally:
        wechat._TOKEN_CACHE.clear()


class _Markup:
    """Small parsed tree for content and inline-style checks, without snapshots."""
    @staticmethod
    def nodes(source):
        from easy_publish.article_templates import _Document, _Node
        def walk(node):
            if isinstance(node, _Node):
                yield node
                for child in node.children:
                    yield from walk(child)
        return list(walk(_Document(source).root))

    @staticmethod
    def css(node):
        return dict(declaration.split(":", 1) for declaration in node.attrs.get("style", "").split(";") if ":" in declaration)


@pytest.mark.parametrize("template", [item["id"] for item in ARTICLE_TEMPLATES])
def test_rich_templates_materialize_stripes_and_separate_inline_from_fenced_code(template):
    source = "内联 `code`。\n\n```text\n  indented\n\tTabbed <tag> & content\n```\n\n| 标题 | 值 |\n| --- | --- |\n| 一 | 1 |\n| 二 | 2 |\n| 三 | 3 |"
    nodes = _Markup.nodes(render_article(source, template, "wechat"))
    inline, block = [node for node in nodes if node.tag == "code"]
    pre = next(node for node in nodes if node.tag == "pre")
    assert _Markup.css(inline)["padding"] == "2px 5px"
    assert _Markup.css(block)["padding"] == "0"
    assert _Markup.css(block)["background-color"] == _Markup.css(pre)["background-color"]
    assert _Markup.css(block)["color"] == _Markup.css(pre)["color"]
    assert "".join(block.children) == "  indented\n\tTabbed <tag> & content\n"
    cells = [node for node in nodes if node.tag == "td"]
    colors = [_Markup.css(node)["background-color"] for node in cells]
    assert colors[0] == colors[1] == colors[4] == colors[5]
    assert colors[2] == colors[3] and colors[0] != colors[2]


@pytest.mark.parametrize("template", [item["id"] for item in ARTICLE_TEMPLATES])
def test_heading_and_table_header_links_keep_their_container_contrast(template):
    source = '# [标题](https://example.org) **强调**\n\n## [章节](https://example.org)\n\n> | [表头](https://example.org) | **强调** | `code` |\n> | --- | --- | --- |\n> | 正文 | 内容 | 数据 |'
    nodes = _Markup.nodes(render_article(source, template, "wechat"))
    for node in nodes:
        if node.tag in {"a", "strong", "code"}:
            assert _Markup.css(node)["color"] == "inherit"
            assert _Markup.css(node)["background-color"] == "transparent"
    assert all(_Markup.css(node).get("text-decoration") == "underline" for node in nodes if node.tag == "a")


@pytest.mark.parametrize("template", [item["id"] for item in ARTICLE_TEMPLATES])
def test_authored_image_title_becomes_escaped_caption_without_inventing_alt_text(template):
    source = '前文 ![替代文字](easy-asset://caption-image "用户提供的说明 & <script>alert(1)</script>") 后文\n\n![没有说明的图片](easy-asset://plain-image)'
    html = render_article(source, template, "wechat")
    nodes = _Markup.nodes(html)
    captions = [node for node in nodes if node.tag == "span" and _Markup.css(node).get("text-align") == "center"]
    assert len(captions) == 1
    assert captions[0].children == ['用户提供的说明 & <script>alert(1)</script>']
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert image_sources(html) == ['easy-asset://caption-image', 'easy-asset://plain-image']
    assert '没有说明的图片' not in ''.join(str(child) for node in nodes if node.tag == 'span' for child in node.children)
    assert '<figure' not in html and '<figcaption' not in html  # safe inside mixed paragraphs/cells
    assert "".join(wechat._body(html).output) == html


def test_dark_quote_emphasis_and_code_are_readable_without_changing_text():
    html = render_article('> 引用 **重点**、[链接](https://example.org) 与 `code`。\n>\n> 另一段。', 'artdeco', 'wechat')
    nodes = _Markup.nodes(html)
    quote = next(node for node in nodes if node.tag == 'blockquote')
    assert _Markup.css(quote)['background-color'] == '#302b20'
    assert all(_Markup.css(node)['color'] == '#ffe195' for node in nodes if node.tag in {'strong', 'a'})
    code = next(node for node in nodes if node.tag == 'code')
    assert _Markup.css(code)['color'] == '#241f16' and _Markup.css(code)['background-color'] == '#ffe8a7'
    assert all('color' not in _Markup.css(node) for node in nodes if node.tag == 'p')
    assert '引用' in html and '另一段' in html


@pytest.mark.parametrize('template', ['forest', 'artdeco'])
def test_new_template_official_api_markup_matches_preview_except_uploaded_image_url(tmp_path, template):
    from test_wechat import OfficialAPI, invoke
    wechat._TOKEN_CACHE.clear()
    image = tmp_path / 'cover.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nfixture-only-image')
    events = []
    ctx = ProviderContext(tmp_path / 'session', tmp_path / 'work', None, events.append)
    source = '# [专题](https://example.org)\n\n> **重点** 与 `code`\n\n![图片](easy-asset://cover "明确图注")\n\n```text\n  original <code>\n```\n\n| 列 | 值 |\n| --- | --- |\n| 一 | 1 |\n| 二 | 2 |'
    preview = render_article(source, template, 'wechat')
    payload = PublishPayload('wechat', 'template-fixture', 'article', 'draft', 'Separate title', body_html=preview,
                             images={'cover': image}, cover_path=image,
                             credentials={'appId': 'fixture-app', 'appSecret': 'fixture-secret'})
    api = OfficialAPI(events)
    try:
        result = asyncio.run(invoke(api, payload, ctx))
        assert result.status == 'draft_created'
        content = api.json_for('/cgi-bin/draft/add')['articles'][0]['content']
        urls = image_sources(content)
        assert len(urls) == 1 and urls[0].startswith('https://mmbiz.qpic.cn/')
        assert content == preview.replace('easy-asset://cover', escape(urls[0], quote=True))
        assert '明确图注' in content and '<code style=' in content and '<table style=' in content
    finally:
        wechat._TOKEN_CACHE.clear()


@pytest.mark.parametrize('template', [item['id'] for item in ARTICLE_TEMPLATES])
def test_dayu_resolves_heading_and_quote_colors_before_platform_conversion(tmp_path, template):
    from easy_publish.article_templates import _theme
    source = '# [标题](https://example.org) **重点**\n\n> [引用链接](https://example.org) 与 `code`\n>\n> ## [引用标题](https://example.org)'
    body = render_article(source, template, 'dayu')
    payload = PublishPayload('dayu', 'fixture-account', 'article', 'draft', 'Separate title', body_html=body)
    document = load_dayu_article(social._prepare_html(payload, tmp_path))
    nodes = _Markup.nodes(document.content_html)
    _, theme = _theme(template)
    links = [node for node in nodes if node.tag == 'span' and _Markup.css(node).get('text-decoration') == 'underline']
    assert [_Markup.css(node)['color'] for node in links] == [theme['h1']['color'], theme['quote_emphasis']['color'], theme['blockquote']['color']]
    strong = next(node for node in nodes if node.tag == 'strong')
    assert _Markup.css(strong)['color'] == theme['h1']['color']
    code = next(node for node in nodes if node.tag == 'span' and 'code' in node.children)
    assert _Markup.css(code)['color'] == theme['quote_code']['color']
    assert _Markup.css(code)['background-color'] == theme['quote_code']['background-color']
    assert 'color:inherit' not in document.content_html
