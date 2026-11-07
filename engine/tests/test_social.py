"""Offline adapter contract checks; no platform account or network is used."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_publish.providers import social
from easy_publish.providers.base import ProviderContext, PublishPayload


def payload(**changes):
    values = dict(platform_id="zhihu", account_id="account-a", kind="article", mode="publish",
                  title="A test article", body_html="<p>Hello</p>")
    values.update(changes)
    return PublishPayload(**values)


def context(tmp_path):
    events = []
    ctx = ProviderContext(tmp_path / "encrypted-session-input.json", tmp_path / "worker", "/owned/chromium", events.append)
    ctx.workspace.mkdir()
    return ctx, events


def store_session(ctx, identity=None):
    ctx.session_path.write_text(json.dumps({"version": 1, "storage_state": {"cookies": [], "origins": []},
                                           "identity": identity or {"name": "Fixture", "avatar_path": "/avatar.png"}}))


def test_session_identity_roundtrip_is_single_host_file(tmp_path):
    ctx, _ = context(tmp_path)
    store_session(ctx)
    local = social._unpack_session(ctx)
    assert local.is_relative_to(ctx.workspace)
    local.write_text(json.dumps({"cookies": [{"name": "fixture-only", "value": "opaque"}], "origins": []}))
    social._pack_session(ctx, local)
    restored = json.loads(ctx.session_path.read_text())
    assert restored["storage_state"]["cookies"][0]["value"] == "opaque"
    assert restored["identity"]["name"] == "Fixture"
    assert not ctx.session_path.with_suffix(".identity.json").exists()


@pytest.mark.parametrize("src", ["/etc/passwd", "file:///secret.png", "https://example.test/a.png", "data:image/png;base64,a"])
def test_article_cannot_read_unimported_image_paths(tmp_path, src):
    with pytest.raises(ValueError, match="先导入"):
        social._prepare_html(payload(body_html=f'<p>x</p><img src="{src}">'), tmp_path)


def test_article_assets_are_copied_to_owned_workspace(tmp_path):
    source = tmp_path / "outside.png"
    source.write_bytes(b"fixture image")
    work = tmp_path / "work"
    work.mkdir()
    target = social._prepare_html(payload(body_html='<p>before</p><img src="easy-asset://image-1"><p>after</p>', images={"image-1": source}), work)
    text = target.read_text()
    assert str(work / "article-image-0.png") in text
    assert "easy-asset://" not in text
    assert (work / "article-image-0.png").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("platform,options", [("dayu", {}), ("dayu", {"source": "转载"}), ("xueqiu", {})])
def test_factual_declarations_are_not_assumed(platform, options):
    item = payload(platform_id=platform)
    item.options = options
    with pytest.raises(ValueError):
        social._validate_options(item)


def test_zero_exit_and_prepared_editor_are_not_published_receipts():
    for platform in social.ARTICLE_PLATFORMS:
        result = social._map_receipt(platform, {"success": True, "status": "published"}, "publish")
        assert result.status == "unknown"
    assert social._map_receipt("dayu", {"success": True, "status": "prepared"}, "draft").status == "needs_attention"
    assert social._map_receipt("xueqiu", {"success": True, "status": "prepared"}, "draft").status == "draft_created"


def test_pending_review_stays_submitted():
    receipt = social._map_receipt("dayu", {"success": True, "status": "submitted", "content_id": "123"}, "publish")
    assert receipt.status == "submitted"
    assert receipt.remote_id == "123"


def test_xueqiu_requires_management_receipt_and_matching_url():
    data = {"success": True, "status": "published", "content_id": "234", "url": "https://xueqiu.com/123/234"}
    assert social._map_receipt("xueqiu", data, "publish").status == "unknown"
    data["management_verified"] = True
    assert social._map_receipt("xueqiu", data, "publish").status == "published"
    data["url"] = "https://xueqiu.com/123/999"
    assert social._map_receipt("xueqiu", data, "publish").status == "unknown"


def test_dayu_rejects_invalid_image_bytes(tmp_path):
    from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader.article_content import validate_dayu_article_image
    image = tmp_path / "invalid.png"
    image.write_bytes(b"this is not image data")
    with pytest.raises(ValueError, match="无法解码"):
        validate_dayu_article_image(image)


def test_dayu_accepts_decodable_image(tmp_path):
    from PIL import Image
    from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader.article_content import validate_dayu_article_image
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    assert validate_dayu_article_image(image) == image


@pytest.mark.parametrize("confirm", [True, False])
def test_real_zhihu_main_signals_before_editor_and_clicks_once(tmp_path, monkeypatch, confirm):
    """Exercise vendored orchestration with an offline browser fixture."""
    from easy_publish.vendor.social_auto_upload.uploader.zhihu_uploader import article

    ctx, events = context(tmp_path)
    store_session(ctx)
    identity = {"name": "Fixture", "avatar_path": "/avatar.png"}
    clicks = []
    launches = []

    class Locator:
        async def click(self):
            clicks.append("publish")
            page.url = "https://zhuanlan.zhihu.com/p/123"

        def get_by_role(self, *args, **kwargs):
            return self

    class Page:
        url = "about:blank"

        async def goto(self, url, **kwargs):
            assert any(e["event"] == "submitting" for e in events)
            self.url = url
            return SimpleNamespace(status=200)

        def get_by_role(self, *args, **kwargs):
            return Locator()

        async def wait_for_url(self, pattern, **kwargs):
            if not confirm:
                raise TimeoutError("fixture connection lost after one click")
            assert pattern.fullmatch(self.url)

    page = Page()

    class BrowserContext:
        async def new_page(self):
            return page

    class Browser:
        async def new_context(self, **kwargs):
            assert Path(kwargs["storage_state"]).is_relative_to(ctx.workspace)
            return BrowserContext()

        async def close(self):
            pass

    class Chromium:
        async def launch(self, **kwargs):
            launches.append(kwargs)
            return Browser()

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *args):
            pass

    class Expect:
        async def to_be_enabled(self):
            pass

        async def to_be_visible(self):
            pass

    async def read_identity(page):
        return identity

    async def fill_article_form(self, page):
        self.uploaded_images = []

    monkeypatch.setattr(article, "async_playwright", Playwright)
    monkeypatch.setattr(article, "expect", lambda locator: Expect())
    monkeypatch.setattr(article, "read_zhihu_identity", read_identity)
    monkeypatch.setattr(article.ZhihuArticle, "fill_article_form", fill_article_form)
    result = asyncio.run(social.publish(payload(), ctx))
    assert clicks == ["publish"]
    assert [e["event"] for e in events].count("submitting") == 1
    assert launches[0]["executable_path"] == "/owned/chromium"
    assert launches[0]["channel"] == "chromium"
    assert launches[0]["args"] == ["--password-store=basic", "--use-mock-keychain"]
    assert result.status == ("published" if confirm else "unknown")


def test_unsupported_kind_does_not_mutate(tmp_path):
    ctx, events = context(tmp_path)
    result = asyncio.run(social.publish(payload(kind="video"), ctx))
    assert result.status == "needs_attention"
    assert events == []
