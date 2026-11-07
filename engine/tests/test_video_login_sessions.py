"""App session reuse and authenticated video-login pages; no live accounts."""
from contextlib import asynccontextmanager
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from easy_publish.providers import social_video as video
from easy_publish.providers.base import ProviderContext, PublishPayload


@pytest.fixture
def account(tmp_path):
    events = []
    ctx = ProviderContext(tmp_path / "session.json", tmp_path, os.environ.get("EASY_PUBLISH_CHROMIUM"), events.append)
    return ctx, events


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", list(video.SITES))
async def test_saved_login_uses_one_quiet_browser_and_refreshes_app_state(account, monkeypatch, platform):
    ctx, events = account
    ctx.session_path.write_text('{"cookies": [], "origins": []}')
    page = SimpleNamespace(goto=AsyncMock())
    context = SimpleNamespace(storage_state=AsyncMock())
    launches = []

    @asynccontextmanager
    async def browser(actual, **options):
        assert actual is ctx
        launches.append(options)
        yield context, page

    wait = AsyncMock(return_value=page)
    monkeypatch.setattr(video, "_browser_page", browser)
    monkeypatch.setattr(video, "_wait_login", wait)
    result = await video.login(PublishPayload(platform, "app-account", "video", "publish", "title"), ctx)
    assert result.status == "ready" and "复用" in result.message
    assert launches == [{"headless": True, "platform": platform}]
    page.goto.assert_awaited_once_with(video.ACCOUNT_URLS[platform], wait_until="domcontentloaded", timeout=60_000)
    context.storage_state.assert_awaited_once_with(path=str(ctx.session_path))
    assert wait.call_args.kwargs["settle_seconds"] == 2
    assert all(event["event"] == "progress" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", list(video.SITES))
async def test_expired_or_timed_out_session_opens_interactive_login_with_full_budget(account, monkeypatch, platform):
    ctx, _ = account
    ctx.session_path.write_text('{"cookies": [], "origins": []}')
    contexts = [SimpleNamespace(storage_state=AsyncMock()), SimpleNamespace(storage_state=AsyncMock())]
    pages = [SimpleNamespace(goto=AsyncMock()), SimpleNamespace(goto=AsyncMock())]
    launches = []
    closed = []

    @asynccontextmanager
    async def browser(actual, **options):
        index = len(launches)
        launches.append(options)
        try:
            yield contexts[index], pages[index]
        finally:
            closed.append(index)

    async def wait(page, actual_platform, seconds, *args, **kwargs):
        if page is pages[0]:
            assert seconds == 25
            raise TimeoutError("fixture expired check")
        assert seconds == video.LOGIN_SECONDS
        assert closed == [0]  # quiet check finished before visible login opened
        return page

    monkeypatch.setattr(video, "_browser_page", browser)
    monkeypatch.setattr(video, "_wait_login", wait)
    result = await video.login(PublishPayload(platform, "app-account", "video", "publish", "title"), ctx)
    assert result.status == "ready"
    assert launches == [{"headless": True, "platform": platform}, {"load_session": False, "platform": platform}]
    contexts[0].storage_state.assert_not_awaited()
    contexts[1].storage_state.assert_awaited_once_with(path=str(ctx.session_path))
    pages[1].goto.assert_awaited_once_with(video.LOGIN_URLS[platform], wait_until="domcontentloaded", timeout=60_000)
    assert closed == [0, 1]


@pytest.mark.asyncio
async def test_valid_session_save_failure_does_not_force_another_scan(account, monkeypatch):
    ctx, _ = account
    original = '{"cookies": [], "origins": []}'
    ctx.session_path.write_text(original)
    launches = []

    @asynccontextmanager
    async def browser(actual, **options):
        launches.append(options)
        yield SimpleNamespace(storage_state=AsyncMock(side_effect=OSError())), SimpleNamespace(goto=AsyncMock())

    monkeypatch.setattr(video, "_browser_page", browser)
    monkeypatch.setattr(video, "_wait_login", AsyncMock())
    result = await video.login(PublishPayload("xiaohongshu", "app-account", "video", "publish", "title"), ctx)
    assert result.status == "needs_attention" and "保存失败" in result.message
    assert len(launches) == 1 and launches[0]["headless"]
    assert ctx.session_path.read_text() == original


AUTH_HEADER = '''<div class="user-info"><img class="user_avatar" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="><span>fixture creator</span></div>'''
FRESH_IDENTITY = "<script>localStorage.setItem('USER_INFO_FOR_BIZ', JSON.stringify({userId:'fixture-id',userName:'fixture creator'}));</script>"


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional intercepted Chromium fixture")
@pytest.mark.parametrize("platform", ["xiaohongshu", "channels", "douyin"])
async def test_creator_home_new_tab_without_upload_input_and_challenges(account, platform):
    ctx, events = account
    root = "https://" + video.SITES[platform].host
    home = video.ACCOUNT_URLS[platform]
    seen = []
    api_body = {"status_code": 8}
    api_unavailable = True

    async def route(r):
        seen.append(r.request.url)
        if r.request.url == root + video.DOUYIN_USER_INFO and platform == "douyin":
            if api_unavailable:
                await r.fulfill(status=503, json={"fixture": "temporarily unavailable"})
            else:
                await r.fulfill(json=api_body)
        elif r.request.is_navigation_request():
            await r.fulfill(content_type="text/html", body='<!doctype html><meta charset="utf-8"><div id="fixture"></div>')
        else:
            await r.abort()

    async with video._browser_page(ctx, load_session=False, headless=True, platform=platform) as (context, original):
        await context.route("**/*", route)
        await original.goto(video.LOGIN_URLS[platform])
        await original.set_content('<div class="login-box">扫码登录</div>')
        target = await context.new_page()
        await target.goto(home)
        assert not await video._logged_in(target, platform)  # empty authenticated-looking route
        if platform == "xiaohongshu":
            await target.set_content(AUTH_HEADER)
            assert not await video._logged_in(target, platform)
            await target.evaluate("localStorage.setItem('USER_INFO_FOR_BIZ', '{}')")
            assert not await video._logged_in(target, platform)
            await target.evaluate("localStorage.setItem('USER_INFO_FOR_BIZ', JSON.stringify({userId:'fixture-id',userName:'wrong creator'}))")
            assert not await video._logged_in(target, platform)
            await target.set_content(AUTH_HEADER + FRESH_IDENTITY)
        elif platform == "channels":
            await target.set_content('<h1>发表视频</h1>')
            assert not await video._logged_in(target, platform)
            await target.set_content('<div role="button" tabindex="0">发表视频</div>')
            assert await video._logged_in(target, platform)
            await target.set_content('<a href="/platform/post/create">发表视频</a>')
            assert await video._logged_in(target, platform)
            await target.set_content('<a href="https://attacker.invalid/platform/post/create">发表视频</a>')
            assert not await video._logged_in(target, platform)
            await target.set_content('<button>发表视频</button>')
        else:
            await target.set_content('<button>发布视频</button>')
            assert not await video._logged_in(target, platform)  # control without session
            await context.add_cookies([{"name": "sessionid", "value": "fixture-app-only", "url": root}])
            api_unavailable = False
            assert not await video._logged_in(target, platform)  # explicit unauthenticated API vetoes fallback
            api_body = {"status_code": 0, "uid": "0"}
            assert not await video._logged_in(target, platform)
            api_body = {"status_code": 0, "new_identity": {"id": "123"}}
            assert await video._logged_in(target, platform)  # unknown API schema uses independent evidence
            api_unavailable = True
        assert await video._logged_in(target, platform)
        assert await video._wait_login(original, platform, 5, ctx, settle_seconds=0.1) is target
        # A visible second factor always overrides the authenticated UI/cookie.
        await target.evaluate("document.body.insertAdjacentHTML('beforeend', '<input hidden placeholder=短信验证码><input placeholder=短信验证码>')")
        assert not await video._logged_in(target, platform)
        await target.locator('input').evaluate_all('(els) => els.forEach(el => el.remove())')
        await original.close()  # original tab closed, current authenticated tab still works
        assert await video._wait_login(original, platform, 5, ctx) is target
        assert not await target.locator('input[type=file]').count()
        await target.close()
        with pytest.raises(video.LoginPageError, match="已关闭"):
            await video._wait_login(original, platform, 600, ctx)
    assert set(seen) <= {video.LOGIN_URLS[platform], home, root + video.DOUYIN_USER_INFO}
    assert all(event["event"] == "progress" for event in events)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional intercepted Chromium fixture")
async def test_xhs_restored_display_cache_cannot_confirm_stale_header(account):
    ctx, _ = account
    cache = {"userId": "fixture-id", "userName": "fixture creator"}
    state = {"cookies": [{"name": "app_cookie", "value": "fixture", "domain": "creator.xiaohongshu.com", "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}],
             "origins": [{"origin": "https://creator.xiaohongshu.com", "localStorage": [
                 {"name": "USER_INFO_FOR_BIZ", "value": json.dumps(cache)},
                 {"name": "USER_INFO", "value": "old display cache"},
                 {"name": "app_auth", "value": "fixture auth remains"}]}]}
    original = json.dumps(state)
    ctx.session_path.write_text(original)
    async with video._browser_page(ctx, headless=True, platform="xiaohongshu") as (context, page):
        await context.route("**/*", lambda r: r.fulfill(content_type="text/html", body=AUTH_HEADER))
        await page.goto(video.ACCOUNT_URLS["xiaohongshu"])
        assert not await video._logged_in(page, "xiaohongshu")
        assert await page.evaluate("localStorage.getItem('USER_INFO')") is None
        assert await page.evaluate("localStorage.getItem('app_auth')") == "fixture auth remains"
        assert [c["name"] for c in await context.cookies()] == ["app_cookie"]
        assert ctx.session_path.read_text() == original  # no invalid state overwrites saved account
        await page.set_content(AUTH_HEADER + FRESH_IDENTITY)
        assert await video._logged_in(page, "xiaohongshu")
        await page.goto("https://creator.xiaohongshu.com.attacker.invalid/")
        await page.set_content(AUTH_HEADER + FRESH_IDENTITY)
        assert not await video._logged_in(page, "xiaohongshu")
        await page.goto("https://creator.xiaohongshu.com/login")
        await page.set_content(AUTH_HEADER + FRESH_IDENTITY)
        assert not await video._logged_in(page, "xiaohongshu")


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", list(video.SITES))
async def test_quiet_check_exits_on_visible_login_without_using_interactive_budget(account, monkeypatch, platform):
    ctx, _ = account
    monkeypatch.setattr(video, "SESSION_INVALID_SETTLE_SECONDS", 0)
    page = SimpleNamespace(url=video.ACCOUNT_URLS[platform], is_closed=lambda: False)
    page.context = SimpleNamespace(pages=[page])
    monkeypatch.setattr(video, "_logged_in", AsyncMock(return_value=False))
    monkeypatch.setattr(video, "_login_challenge", AsyncMock(return_value=True))
    with pytest.raises(video.LoginPageError, match="已失效"):
        await video._wait_login(page, platform, 0.2, ctx, interactive=False)
    with pytest.raises(TimeoutError):
        await video._wait_login(page, platform, 0.02, ctx, interactive=True)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional intercepted Chromium fixture")
async def test_channels_editor_frame_and_hidden_login_iframe(account):
    ctx, _ = account
    root = "https://channels.weixin.qq.com"
    editor = '<div class="input-editor" contenteditable="true">fixture</div><button>发表</button>'

    async def route(r):
        await r.fulfill(content_type="text/html; charset=utf-8", body=editor if '/micro/content/post/create' in r.request.url else '<!doctype html>')

    async with video._browser_page(ctx, load_session=False, headless=True, platform="channels") as (context, page):
        await context.route("**/*", route)
        await page.goto(root + '/platform')
        await page.set_content('<iframe src="https://channels.weixin.qq.com.attacker.invalid/micro/content/post/create"></iframe>')
        assert not await video._logged_in(page, "channels")
        await page.set_content('<iframe src="/micro/content/post/create"></iframe>')
        assert await video._logged_in(page, "channels")
        await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<iframe id=qr style=display:none src=https://open.weixin.qq.com/connect/qrconnect></iframe>')")
        await page.locator('#qr').wait_for(state='attached')
        assert await video._logged_in(page, "channels")
        await page.locator('#qr').evaluate("el => el.style.display='block'")
        assert not await video._logged_in(page, "channels")
        await page.goto(root + '/unrelated')
        await page.set_content('<iframe src="/micro/content/post/create"></iframe>')
        assert not await video._logged_in(page, "channels")


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["xiaohongshu", "channels"])
async def test_unconfirmed_creator_has_short_deadline_but_challenge_keeps_scan_budget(account, monkeypatch, platform):
    ctx, _ = account
    page = SimpleNamespace(url=video.ACCOUNT_URLS[platform], is_closed=lambda: False)
    page.context = SimpleNamespace(pages=[page])
    monkeypatch.setattr(video, "ACCOUNT_VERIFY_SECONDS", 0)
    monkeypatch.setattr(video, "_logged_in", AsyncMock(return_value=False))
    challenge = AsyncMock(return_value=False)
    monkeypatch.setattr(video, "_login_challenge", challenge)
    with pytest.raises(video.LoginPageError, match="尚未取得账号登录确认"):
        await video._wait_login(page, platform, 600, ctx)
    challenge.return_value = True
    with pytest.raises(TimeoutError):
        await video._wait_login(page, platform, 0.02, ctx)


@pytest.mark.asyncio
async def test_quiet_check_allows_transient_login_shell_to_finish_restoring_session(account, monkeypatch):
    ctx, _ = account
    page = SimpleNamespace(url=video.ACCOUNT_URLS["xiaohongshu"], is_closed=lambda: False)
    page.context = SimpleNamespace(pages=[page])
    proof = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(video, "_logged_in", proof)
    monkeypatch.setattr(video, "_login_challenge", AsyncMock(return_value=True))
    assert await video._wait_login(page, "xiaohongshu", 2, ctx, interactive=False) is page
    assert proof.await_count == 2
