"""Current and legacy XHS submit controls, using only isolated fixture pages."""
import asyncio
from contextlib import asynccontextmanager
import os
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit

import pytest

from easy_publish.providers.base import ProviderContext, ProviderResult, PublishPayload
from easy_publish.providers import social_video as video


@pytest.fixture
def account(tmp_path):
    events = []
    ctx = ProviderContext(tmp_path / "session.json", tmp_path, None, events.append)
    payload = PublishPayload("xiaohongshu", "fixture", "video", "publish", "测试标题")
    return ctx, payload, events


@pytest.mark.asyncio
async def test_xhs_manual_publish_does_not_resolve_or_click_a_button(account, monkeypatch):
    ctx, payload, events = account
    button = MagicMock(click=AsyncMock())
    page = MagicMock()
    page.is_closed.return_value = False
    resolve = AsyncMock(side_effect=AssertionError("manual publishing must not resolve a button"))
    receipt = AsyncMock(return_value=ProviderResult("submitted", "manual receipt"))
    monkeypatch.setattr(video, "_xhs_wait_submit_button", resolve)
    monkeypatch.setattr(video, "_manual_receipt", receipt)
    monkeypatch.setattr(video, "_manual_success_notice", AsyncMock(return_value=False))
    result = await video._submit_once(page, button, "xiaohongshu", payload, ctx)
    assert result.status == "submitted"
    resolve.assert_not_awaited()
    button.click.assert_not_awaited()
    assert events[-1]["event"] == "awaiting_user"


@pytest.mark.asyncio
async def test_xhs_closed_manual_window_is_unknown_without_a_click(account):
    ctx, payload, events = account
    button = MagicMock(click=AsyncMock())
    page = MagicMock()
    page.is_closed.return_value = True
    result = await video._submit_once(page, button, "xiaohongshu", payload, ctx)
    assert result.status == "unknown"
    button.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_page_stops_before_resolving_any_button(account):
    ctx, payload, events = account
    page = MagicMock()
    page.is_closed.return_value = True
    with pytest.raises(video.VideoStageError, match="浏览器已关闭"):
        await video._xhs_wait_submit_button(page, ctx)
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_readiness_wait_is_cancellable(account):
    ctx, payload, events = account
    page = MagicMock(url=video.SITES["xiaohongshu"].url)
    page.is_closed.return_value = False
    page.locator.return_value.count = AsyncMock(return_value=0)
    page.get_by_role.return_value.filter.return_value.count = AsyncMock(return_value=0)
    task = asyncio.create_task(video._xhs_wait_submit_button(page, ctx))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


browser_only = pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Requires an explicit isolated fixture browser")
COMPONENT_SCRIPT = """<script>
customElements.define('xhs-publish-btn', class extends HTMLElement {
  constructor() {
    super();
    this._sr = this.attachShadow({mode: 'closed'});
    const button = '<button class="ce-btn bg-red ' + (this.getAttribute('data-class') || '') + '" ' + (this.getAttribute('data-state') || '') + '>' +
      (this.getAttribute('data-label') || '发布') + '</button>';
    this._sr.innerHTML = '<button class="save">暂存离开</button>' + button + (this.hasAttribute('data-duplicate') ? button : '');
    for (const el of this._sr.querySelectorAll('button')) el.onclick = async () => {
      await fetch('/fixture/' + (el.classList.contains('save') ? 'save' : 'submit'), {method:'POST'});
      location.href='/publish/success';
    };
  }
});
</script>"""


@asynccontextmanager
async def fixture_page(markup):
    from patchright.async_api import async_playwright
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=os.environ["EASY_PUBLISH_CHROMIUM"], headless=True, args=["--no-sandbox"])
        try:
            context = await browser.new_context(service_workers="block")
            page = await context.new_page()
            calls = []

            async def route(request_route):
                path = urlsplit(request_route.request.url).path
                if path.startswith("/fixture/"):
                    calls.append(path)
                    await request_route.fulfill(body="{}")
                elif path == "/publish/success":
                    await request_route.fulfill(content_type="text/html; charset=utf-8", body="<meta charset=utf-8><p>发布成功</p>")
                else:
                    await request_route.fulfill(content_type="text/html; charset=utf-8", body="<!doctype html><meta charset=utf-8>" + markup + COMPONENT_SCRIPT)

            await context.route("**/*", route)
            await page.goto(video.SITES["xiaohongshu"].url)
            yield page, calls
        finally:
            await browser.close()


@pytest.mark.asyncio
@browser_only
@pytest.mark.parametrize("outside", ["", '<button disabled>发布</button>', '<button onclick="fetch(\'/fixture/wrong\')">发布</button>'])
async def test_current_closed_component_waits_for_user_ignoring_unrelated_buttons(account, outside):
    ctx, payload, events = account
    async with fixture_page(outside + "<xhs-publish-btn></xhs-publish-btn>") as (page, calls):
        # Faithful main-world component: the root remains closed. Patchright's
        # ordinary scoped role locator supports it without replacing attachShadow.
        assert await page.locator("xhs-publish-btn").evaluate("el => el.shadowRoot === null", isolated_context=False)
        if "disabled" in outside:
            assert not await page.get_by_role("button", name="发布", exact=True).first.is_enabled()
        waiting = asyncio.create_task(video._submit_once(page, None, "xiaohongshu", payload, ctx))
        await asyncio.sleep(0.3)
        assert not waiting.done() and calls == []
        assert events[-1]["event"] == "awaiting_user"
        # Simulate the user's click, including the current closed component.
        await page.locator("xhs-publish-btn").get_by_role("button", name="发布", exact=True).click()
        result = await asyncio.wait_for(waiting, 3)
        assert result.status == "submitted"
        assert calls == ["/fixture/submit"]


@pytest.mark.asyncio
@browser_only
async def test_legacy_composer_waits_for_user_without_current_component(account):
    ctx, payload, events = account
    markup = '<button onclick="fetch(\'/fixture/submit\', {method:\'POST\'}).then(()=>location.href=\'/publish/success\')">发布</button>'
    async with fixture_page(markup) as (page, calls):
        waiting = asyncio.create_task(video._submit_once(page, None, "xiaohongshu", payload, ctx))
        await asyncio.sleep(0.3)
        assert not waiting.done() and calls == []
        await page.get_by_role("button", name="发布", exact=True).click()
        result = await asyncio.wait_for(waiting, 3)
        assert result.status == "submitted"
        assert calls == ["/fixture/submit"]


@pytest.mark.asyncio
@browser_only
@pytest.mark.parametrize("markup, message", [
    ("<xhs-publish-btn></xhs-publish-btn><xhs-publish-btn></xhs-publish-btn>", "多个可见发布区域"),
    ("<xhs-publish-btn data-duplicate></xhs-publish-btn>", "多个可见发布按钮"),
    ("<button>发布</button><button>发布</button>", "多个可见发布按钮"),
    ('<xhs-publish-btn data-state="disabled"></xhs-publish-btn>', "60 秒内"),
    ('<xhs-publish-btn data-state="aria-busy=true"></xhs-publish-btn>', "60 秒内"),
    ('<xhs-publish-btn data-state="aria-disabled=true"></xhs-publish-btn>', "60 秒内"),
    ('<xhs-publish-btn data-class="loading"></xhs-publish-btn>', "60 秒内"),
    ('<button>发布</button><xhs-publish-btn data-label="定时发布"></xhs-publish-btn>', "60 秒内"),
    ('<button>发布</button><xhs-publish-btn style="display:none"></xhs-publish-btn>', "60 秒内"),
])
async def test_unready_or_ambiguous_controls_stop_without_falling_back(account, markup, message):
    ctx, payload, events = account
    async with fixture_page(markup) as (page, calls):
        with pytest.raises(video.VideoStageError, match=message):
            await video._xhs_wait_submit_button(page, ctx, seconds=1.5)
        assert calls == []


@pytest.mark.asyncio
@browser_only
@pytest.mark.parametrize("url", ["https://creator.xiaohongshu.com/login", "https://creator.xiaohongshu.com.attacker.invalid/publish/publish"])
async def test_submit_page_must_still_be_trusted_composer(account, url):
    ctx, payload, events = account
    async with fixture_page("<xhs-publish-btn></xhs-publish-btn>") as (page, calls):
        await page.goto(url)
        with pytest.raises(video.VideoStageError, match="已离开本次发布页面"):
            await video._xhs_wait_submit_button(page, ctx, seconds=0.2)
        assert calls == []


@pytest.mark.asyncio
@browser_only
async def test_current_component_waits_until_button_is_enabled(account):
    ctx, payload, events = account
    async with fixture_page('<xhs-publish-btn data-state="disabled"></xhs-publish-btn>') as (page, calls):
        waiting = asyncio.create_task(video._xhs_wait_submit_button(page, ctx, seconds=3))
        await asyncio.sleep(0.1)
        assert not waiting.done()
        await page.locator("xhs-publish-btn").get_by_role("button", name="发布", exact=True).evaluate("el => el.disabled = false")
        button = await waiting
        await button.dispose()
        assert calls == []


@pytest.mark.asyncio
@browser_only
async def test_transient_composer_read_is_reacquired_before_any_click(account, monkeypatch):
    ctx, payload, events = account
    async with fixture_page("<xhs-publish-btn></xhs-publish-btn>") as (page, calls):
        original = page.locator
        unavailable = MagicMock()
        unavailable.count = AsyncMock(side_effect=RuntimeError("fixture: composer replaced during read"))
        reads = 0

        def locator(selector):
            nonlocal reads
            reads += 1
            return unavailable if reads == 1 else original(selector)

        monkeypatch.setattr(page, "locator", locator)
        button = await video._xhs_wait_submit_button(page, ctx, seconds=3)
        await button.dispose()
        assert reads >= 2
        assert calls == []


@pytest.mark.asyncio
@browser_only
async def test_component_remount_never_falls_back_to_unrelated_legacy_button(account):
    ctx, payload, events = account
    async with fixture_page('<button id="outside">发布</button><xhs-publish-btn data-state="disabled"></xhs-publish-btn>') as (page, calls):
        waiting = asyncio.create_task(video._xhs_wait_submit_button(page, ctx, seconds=6))
        try:
            await asyncio.sleep(1.1)
            assert not waiting.done()
            await page.locator("xhs-publish-btn").evaluate("el => el.remove()")
            await asyncio.sleep(1.1)
            assert not waiting.done()
            await page.evaluate("document.body.appendChild(document.createElement('xhs-publish-btn'))", isolated_context=False)
            button = await waiting
            assert await button.get_attribute("class") == "ce-btn bg-red "
            await button.dispose()
            assert calls == []
        finally:
            if not waiting.done():
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
