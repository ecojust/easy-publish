"""Template rendering, platform compatibility and official API output fixtures."""
import asyncio
from html import unescape
import re

from PIL import Image
import pytest
from pydantic import ValidationError

from easy_publish.article_templates import ARTICLE_TEMPLATES, article_format_notes, render_article
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
    assert len(set(rendered)) == 4
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
