"""Offline regression for QR login landing on creator home, without an upload input."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easy_publish.providers import social_video as video
from easy_publish.providers.base import ProviderContext, ProviderResult, PublishPayload


HOME = "https://creator.douyin.com/creator-micro/home"
UPLOAD = video.SITES["douyin"].url


class Locator:
    def __init__(self, visible=False):
        self.visible = visible
        self.first = self

    async def count(self):
        return int(self.visible)

    async def is_visible(self):
        return self.visible

    async def wait_for(self, **kwargs):
        if not self.visible:
            raise TimeoutError("fixture has no upload input")


class Page:
    def __init__(self, url=HOME, authenticated=True):
        self.url = url
        self.authenticated = authenticated
        self.closed = False
        self.login_marker = None
        self.qrcode = False
        self.sms = False
        self.has_upload = False
        self.evaluations = []
        self.navigation = []
        self.on_evaluate = None
        self.on_goto = None
        self.listeners = []
        self.main_frame = object()
        self.context = SimpleNamespace(pages=[self], storage_state=AsyncMock())

    def is_closed(self):
        return self.closed

    def on(self, event, listener):
        self.listeners.append(listener)

    def remove_listener(self, event, listener):
        self.listeners.remove(listener)

    def get_by_text(self, text, **kwargs):
        return Locator(bool(text.search(self.login_marker or "")) if hasattr(text, "search") else text == self.login_marker)

    def get_by_role(self, role, **kwargs):
        return Locator(self.qrcode)

    def locator(self, selector):
        return Locator(self.has_upload if selector == video.SITES["douyin"].upload else self.sms)

    async def evaluate(self, script, path):
        self.evaluations.append((script, path))
        if self.on_evaluate:
            self.on_evaluate()
        return self.authenticated

    async def goto(self, url, **kwargs):
        self.navigation.append(url)
        self.url = self.on_goto(url) if self.on_goto else url


@pytest.fixture
def account(tmp_path):
    events = []
    context = ProviderContext(tmp_path / "session.json", tmp_path, "/fixture/browser", events.append)
    payload = PublishPayload("douyin", "fixture-account", "video", "publish", "测试视频")
    return context, payload, events


def browser_for(page):
    @asynccontextmanager
    async def browser(ctx, **kwargs):
        yield page.context, page
    return browser


@pytest.mark.asyncio
async def test_authenticated_home_is_ready_without_upload_input():
    page = Page()
    assert not page.has_upload
    assert await video._logged_in(page, "douyin")
    assert page.evaluations[0][1] == "/aweme/v1/creator/pc/user/info/"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://creator.douyin.com/", "https://creator.douyin.com/login",
    "https://creator.douyin.com.attacker.invalid/creator-micro/home",
    "http://creator.douyin.com/creator-micro/home",
    "https://creator.douyin.com/creator-micro-lookalike/home",
    "https://creator.douyin.com:1234/creator-micro/home",
])
async def test_untrusted_or_login_routes_do_not_probe_or_confirm(url):
    page = Page(url)
    assert not await video._logged_in(page, "douyin")
    assert page.evaluations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge", ["扫码登录", "手机号登录", "二维码失效", "安全验证", "二维码", "短信"])
async def test_visible_login_or_second_factor_is_never_ready(challenge):
    page = Page()
    page.login_marker = challenge
    page.qrcode = challenge == "二维码"
    page.sms = challenge == "短信"
    assert not await video._logged_in(page, "douyin")
    assert page.evaluations == []


@pytest.mark.asyncio
async def test_creator_route_or_widget_alone_is_not_authenticated():
    page = Page(authenticated=False)
    page.has_upload = True
    assert not await video._logged_in(page, "douyin")


@pytest.mark.asyncio
async def test_navigation_during_account_probe_discards_old_result():
    page = Page()
    page.on_evaluate = lambda: setattr(page, "url", UPLOAD)
    assert not await video._logged_in(page, "douyin")


@pytest.mark.asyncio
async def test_navigation_away_and_back_to_same_url_discards_old_result():
    page = Page()
    page.on_evaluate = lambda: [listener(page.main_frame) for listener in page.listeners]
    assert not await video._logged_in(page, "douyin")
    assert page.listeners == []


@pytest.mark.asyncio
async def test_second_factor_appearing_during_probe_discards_result():
    page = Page()
    page.on_evaluate = lambda: setattr(page, "sms", True)
    assert not await video._logged_in(page, "douyin")


@pytest.mark.asyncio
async def test_new_creator_tab_after_scan_is_used(account):
    ctx, _, _ = account
    original = Page("https://creator.douyin.com/")
    original.login_marker = "扫码登录"
    target = Page()
    original.context.pages.append(target)
    target.context = original.context
    assert await video._wait_login(original, "douyin", 1, ctx) is target
    assert not original.evaluations


@pytest.mark.asyncio
async def test_closed_login_browser_exits_without_ten_minute_wait(account):
    ctx, _, _ = account
    page = Page()
    page.context.pages.clear()
    with pytest.raises(video.LoginPageError, match="已关闭"):
        await video._wait_login(page, "douyin", 600, ctx)


@pytest.mark.asyncio
async def test_unconfirmed_home_has_bounded_actionable_error(account, monkeypatch):
    ctx, _, _ = account
    monkeypatch.setattr(video, "DOUYIN_VERIFY_SECONDS", 0)
    with pytest.raises(video.LoginPageError, match="尚未取得账号登录确认"):
        await video._wait_login(Page(authenticated=False), "douyin", 600, ctx)


@pytest.mark.asyncio
async def test_login_saves_session_after_home_confirmation(account, monkeypatch):
    ctx, payload, events = account
    page = Page()
    page.on_goto = lambda _: HOME
    monkeypatch.setattr(video, "_browser_page", browser_for(page))
    result = await video.login(payload, ctx)
    assert result.status == "ready"
    page.context.storage_state.assert_awaited_once_with(path=str(ctx.session_path))
    assert any("正在保存加密会话" in event["message"] for event in events)
    assert not any(event["event"] == "submitting" for event in events)
    assert not page.has_upload


@pytest.mark.asyncio
async def test_account_check_accepts_home_without_claiming_upload_permission(account, monkeypatch):
    ctx, payload, _ = account
    ctx.session_path.write_text("{}")
    page = Page()
    page.on_goto = lambda _: HOME
    monkeypatch.setattr(video, "_browser_page", browser_for(page))
    result = await video.check(payload, ctx)
    assert result.status == "ready" and "登录有效" in result.message
    assert not page.has_upload


@pytest.mark.asyncio
async def test_publish_returns_to_upload_page_after_confirming_home(account, monkeypatch):
    ctx, payload, events = account
    ctx.session_path.write_text("{}")
    payload.video_path = ctx.workspace / "video.mp4"
    payload.video_path.write_bytes(b"fixture only")
    page = Page()
    page.has_upload = True
    page.on_goto = lambda url: HOME if len(page.navigation) == 1 else url
    monkeypatch.setattr(video, "_browser_page", browser_for(page))

    async def prepare(actual, platform, payload, ctx):
        assert actual is page and page.url == UPLOAD
        ctx.submitting()
        return object()

    monkeypatch.setattr(video, "_prepare", prepare)
    monkeypatch.setattr(video, "_submit_once", AsyncMock(return_value=ProviderResult("submitted", "fixture receipt")))
    result = await video.publish(payload, ctx)
    assert result.status == "submitted"
    assert page.navigation == [UPLOAD, UPLOAD]
    assert [event["event"] for event in events].count("submitting") == 1


@pytest.mark.asyncio
async def test_upload_widget_failure_does_not_reclassify_connected_account(account):
    ctx, _, events = account
    page = Page()
    with pytest.raises(video.LoginPageError, match="账号已登录，但未找到"):
        await video._douyin_upload_page(page, ctx)
    assert page.navigation == [UPLOAD]
    assert not any(event["event"] == "submitting" for event in events)


@pytest.mark.asyncio
async def test_late_login_error_hands_off_until_user_closes_then_stays_unknown(account, monkeypatch):
    ctx, payload, events = account
    ctx.session_path.write_text("{}")
    payload.video_path = ctx.workspace / "video.mp4"
    payload.video_path.write_bytes(b"fixture only")
    page = Page()
    page.has_upload = True
    monkeypatch.setattr(video, "_browser_page", browser_for(page))

    async def prepare(actual, platform, payload, ctx):
        ctx.submitting()
        raise video.LoginPageError("fixture expired after upload")

    monkeypatch.setattr(video, "_prepare", prepare)
    handoff = asyncio.Event()

    def observe(event):
        events.append(event)
        if event["event"] == "awaiting_user":
            handoff.set()

    ctx.emit = observe
    task = asyncio.create_task(video.publish(payload, ctx))
    try:
        await asyncio.wait_for(handoff.wait(), 2)
        assert not task.done() and not page.closed
        assert [event["event"] for event in events].count("submitting") == 1
        assert "手动点击发布" in events[-1]["message"]
        assert "fixture expired after upload" not in events[-1]["message"]
        # The adapter keeps the uploaded page alive for manual recovery. The
        # fixture must now model the user's close, rather than await an auto-exit.
        page.closed = True
        assert (await asyncio.wait_for(task, 2)).status == "unknown"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("body, expected", [
    ({"status_code": 0, "uid": "1234567890123456789"}, True),
    ({"status_code": 0, "uid": 123}, True),
    ({"status_code": 8, "status_msg": "用户未登录", "uid": "123"}, False),
    ({"status_code": 4, "uid": "123"}, False),
    ({"status_code": "0", "uid": "123"}, None),
    ({"status_code": 0}, None),
    ({"status_code": 0, "uid": "0"}, False),
    ({"status_code": 0, "uid": True}, False),
    ({"status_code": 0, "uid": 1.5}, False),
    ({"status_code": 0, "uid": "not-an-identity"}, False),
    ({"status_code": 0, "uid": [123]}, False),
    (None, None),
])
async def test_browser_probe_executes_verified_status_and_uid_contract(body, expected):
    # Execute the exact browser function against a fake fetch in Patchright's
    # bundled Node. No browser download, real account, or HTTP call is involved;
    # this also catches JS schema/timeout/redirect mistakes on all CI systems.
    from patchright._impl._driver import compute_driver_executable
    page = Page()
    await video._douyin_account_ready(page)
    script, path = page.evaluations[0]
    fixture = json.dumps({"body": body, "path": path}, ensure_ascii=False)
    code = """const fs = require('fs');
        const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
        global.location = {origin:'https://creator.douyin.com'};
        global.fetch = async (path, options) => {
            if (path !== fixture.path || options.redirect !== 'error' ||
                options.credentials !== 'include' || options.method !== 'GET' ||
                options.cache !== 'no-store' || !options.signal) throw Error('unsafe probe');
            return {ok:true, url:location.origin + path, text:async()=>JSON.stringify(fixture.body)};
        };
    """ + f"({script})(fixture.path).then(value=>process.stdout.write(JSON.stringify(value)));"
    node, _ = compute_driver_executable()
    result = subprocess.run([str(node), "-e", code], input=fixture, text=True, encoding="utf-8", capture_output=True, timeout=10, check=True)
    assert json.loads(result.stdout) is expected


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional routed Chromium fixture requires an explicit browser path")
async def test_real_browser_home_session_and_new_tab_use_only_routed_fixture(account):
    from patchright.async_api import async_playwright
    ctx, _, _ = account
    seen = []

    async def route(request_route):
        request = request_route.request
        seen.append(request.url)
        if request.url == "https://creator.douyin.com" + video.DOUYIN_USER_INFO:
            logged_in = "fixture-login=yes" in (await request.all_headers()).get("cookie", "")
            await request_route.fulfill(json={"status_code": 0, "uid": "12345"} if logged_in else {"status_code": 8})
        elif request.url == HOME:
            await request_route.fulfill(content_type="text/html", body="<!doctype html><h1>创作者中心首页 fixture</h1>")
        else:
            await request_route.abort()

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=os.environ["EASY_PUBLISH_CHROMIUM"], headless=True, args=["--no-sandbox"])
        try:
            context = await browser.new_context()
            await context.route("**/*", route)
            original = await context.new_page()
            await original.goto(HOME)
            assert not await video._logged_in(original, "douyin")
            await context.add_cookies([{"name": "fixture-login", "value": "yes", "url": "https://creator.douyin.com"}])
            target = await context.new_page()
            await target.goto(HOME)
            await original.close()
            assert await video._wait_login(original, "douyin", 5, ctx) is target
            assert not await target.locator('input[type="file"]').count()
            assert set(seen) <= {HOME, "https://creator.douyin.com" + video.DOUYIN_USER_INFO}
        finally:
            await browser.close()
