"""XHS upload-stage fixtures. No account, actual media upload or publication."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, MagicMock

import pytest

from easy_publish.providers.base import ProviderContext, PublishPayload
from easy_publish.providers import social_video as video


@pytest.fixture
def account(tmp_path):
    events = []
    ctx = ProviderContext(tmp_path / "session.json", tmp_path, None, events.append)
    ctx.session_path.write_text("{}")
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fixture only; never sent to any platform")
    payload = PublishPayload("xiaohongshu", "fixture", "video", "publish", "测试标题", description="测试正文", video_path=media)
    return ctx, payload, events


def fake_page(state="pending"):
    page = MagicMock()
    page.url = video.SITES["xiaohongshu"].url
    page.is_closed.return_value = False
    page.evaluate = AsyncMock(return_value={"state": state})
    return page


@pytest.mark.asyncio
async def test_upload_100_percent_and_transcoding_are_not_ready(account, monkeypatch):
    ctx, payload, events = account
    page = fake_page()
    page.evaluate.side_effect = [
        {"state": "pending"}, {"state": "uploading", "percent": 50},
        {"state": "uploading", "percent": 100}, {"state": "processing"}, {"state": "ready"},
    ]
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    await video._xhs_wait_upload(page, payload.video_path.name, ctx)
    assert page.evaluate.await_count == 5
    messages = [event["message"] for event in events]
    assert any("100%" in message for message in messages)
    assert any("转码" in message for message in messages)
    assert "上传及处理完成" in messages[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(("states", "message"), [
    (["failed"], "上传失败"),
    (["uploading", "idle"], "已取消"),
    (["other_video"], "与本次文件不一致"),
    (["auth"], "安全验证"),
])
async def test_terminal_upload_states_stop_before_metadata(account, monkeypatch, states, message):
    ctx, payload, _ = account
    page = fake_page()
    page.evaluate.side_effect = [{"state": state} for state in states]
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    with pytest.raises(video.VideoStageError, match=message):
        await video._xhs_wait_upload(page, payload.video_path.name, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "closed", "message"), [
    (video.SITES["xiaohongshu"].url, True, "浏览器已关闭"),
    ("https://creator.xiaohongshu.com/login", False, "登录已失效"),
    ("https://example.invalid/publish/publish", False, "已离开创作中心"),
    ("https://creator.xiaohongshu.com/manage/notes", False, "页面已切换"),
])
async def test_closed_or_navigated_upload_page_has_specific_error(url, closed, message):
    page = fake_page()
    page.url = url
    page.is_closed.return_value = closed
    with pytest.raises(video.VideoStageError, match=message):
        await video._xhs_upload_state(page, "clip.mp4")
    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_completion_has_bounded_specific_timeout(account):
    ctx, payload, _ = account
    with pytest.raises(video.VideoStageError, match="超过 20 分钟"):
        await video._xhs_wait_upload(fake_page(), payload.video_path.name, ctx, seconds=0.02)


@pytest.mark.asyncio
async def test_upload_task_cancellation_propagates(account):
    ctx, payload, _ = account
    page = fake_page()
    page.evaluate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await video._xhs_wait_upload(page, payload.video_path.name, ctx)


@pytest.mark.asyncio
async def test_page_read_failure_does_not_expose_raw_error():
    page = fake_page()
    page.evaluate.side_effect = RuntimeError("private-page-text cookie=fixture-secret")
    with pytest.raises(video.VideoStageError) as error:
        await video._xhs_upload_state(page, "clip.mp4")
    assert "无法读取" in str(error.value)
    assert "fixture-secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_fence", [False, True])
async def test_stage_error_preserves_mutation_state_without_resubmit(account, monkeypatch, after_fence):
    ctx, payload, _ = account
    page = fake_page()
    page.goto = AsyncMock()

    @asynccontextmanager
    async def browser(*args, **kwargs):
        yield MagicMock(), page

    async def prepare(page, platform, payload, guarded_ctx):
        if after_fence:
            guarded_ctx.submitting()
        raise video.VideoStageError("小红书页面显示视频上传失败；尚未点击发布")

    submit = AsyncMock()
    monkeypatch.setattr(video, "_browser_page", browser)
    monkeypatch.setattr(video, "_wait_login", AsyncMock(return_value=page))
    monkeypatch.setattr(video, "_prepare", prepare)
    monkeypatch.setattr(video, "_submit_once", submit)
    result = await video.publish(payload, ctx)
    assert result.status == ("unknown" if after_fence else "needs_attention")
    assert "上传失败" in result.message
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_error_identifies_stage_and_does_not_drop_cover(account, monkeypatch):
    ctx, payload, events = account
    payload.cover_path = payload.video_path
    page = fake_page()
    control = MagicMock()
    control.first = control
    control.wait_for = AsyncMock()
    control.fill = AsyncMock()
    control.set_input_files = AsyncMock()
    page.locator.return_value = control
    order = []

    async def uploaded(*args, **kwargs):
        assert events[0]["event"] == "submitting"
        control.fill.assert_not_awaited()
        order.append("upload-complete")

    async def editor(*args):
        assert order == ["upload-complete"]
        order.append("body")

    monkeypatch.setattr(video, "_xhs_wait_upload", uploaded)
    monkeypatch.setattr(video, "_fill_editor", editor)
    monkeypatch.setattr(video, "_xhs_cover", AsyncMock(side_effect=TimeoutError("fixture cover failed")))
    with pytest.raises(video.VideoStageError, match="填写封面未完成"):
        await video._prepare(page, "xiaohongshu", payload, ctx)
    assert order == ["upload-complete", "body"]
    control.set_input_files.assert_awaited_once()
    page.get_by_role.assert_not_called()


# Deliberately show an editable title/body and enabled publish button throughout
# upload. The old adapter filled and submitted them before the upload finished.
UPLOAD_HTML = """<!doctype html><meta charset="utf-8">
<div class="user-info"><img class="user_avatar" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="><span>fixture creator</span></div>
<script>localStorage.setItem('USER_INFO_FOR_BIZ', JSON.stringify({userId:'fixture-id',userName:'fixture creator'}));</script>
<div class="cover-container"><input class="upload-input" type="file" accept=".mp4,.mov">
<div id="status" class="canceled">拖拽或点击上传</div></div>
<input id="title" placeholder="填写标题会有更多赞哦">
<div contenteditable="true"><p data-placeholder="输入正文描述" style="min-height:24px"></p></div>
<button id="submit">发布</button>
<script>
window.started = 0;
window.showState = (state, text) => {
    const el = document.querySelector('#status'); el.className = state;
    el.textContent = text;
};
document.querySelector('input[type=file]').onchange = async e => {
    window.started++;
    window.filename = e.target.files[0].name;
    showState('uploading', '上传中 10%');
    await fetch('/fixture/upload', {method:'POST', body:'fixture-only-no-media'});
    showState('transcoding', '转码中，不影响发布');
};
document.querySelector('#submit').onclick = async () => {
    await fetch('/fixture/submit', {method:'POST', body:JSON.stringify({
        title:document.querySelector('#title').value,
        body:document.querySelector('[contenteditable]').textContent
    })});
    location.href='/publish/success';
};
</script>"""


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional routed Chromium fixture requires an explicit browser path")
async def test_real_browser_slow_upload_keeps_window_open_until_current_preview(account, monkeypatch):
    from patchright.async_api import async_playwright

    ctx, payload, events = account
    payload.cover_path = payload.video_path
    upload_response = asyncio.Event()
    submissions = []
    closed = False
    cover_completed = False

    async def route(request_route):
        request = request_route.request
        parsed = urlsplit(request.url)
        if parsed.hostname != video.SITES["xiaohongshu"].host:
            await request_route.abort()
        elif parsed.path == "/fixture/upload":
            assert any(event["event"] == "submitting" for event in events)
            await upload_response.wait()
            await request_route.fulfill(json={"fixture": True})
        elif parsed.path == "/fixture/submit":
            assert cover_completed
            submissions.append(json.loads(request.post_data))
            await request_route.fulfill(json={"fixture": True})
        elif parsed.path == "/publish/publish":
            await request_route.fulfill(content_type="text/html", body=UPLOAD_HTML)
        elif parsed.path == "/publish/success":
            await request_route.fulfill(content_type="text/html", body="<!doctype html><meta charset=utf-8><p>发布成功</p>")
        else:
            await request_route.abort()

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=os.environ["EASY_PUBLISH_CHROMIUM"], headless=True, args=["--no-sandbox"])
        task = None
        try:
            context = await browser.new_context(service_workers="block")
            await context.route("**/*", route)
            page = await context.new_page()

            @asynccontextmanager
            async def browser_fixture(*args, **kwargs):
                nonlocal closed
                try:
                    yield context, page
                finally:
                    closed = True
                    await context.close()

            async def cover(page, path):
                nonlocal cover_completed
                assert (await video._xhs_upload_state(page, payload.video_path.name))["state"] == "ready"
                cover_completed = True

            monkeypatch.setattr(video, "_browser_page", browser_fixture)
            monkeypatch.setattr(video, "_xhs_cover", cover)
            task = asyncio.create_task(video.publish(payload, ctx))
            await page.wait_for_function("window.started === 1")
            for phase, text in (("uploading", "上传中 10%"), ("uploading", "上传中 100%")):
                await page.evaluate("""([phase, text]) => {
                    const el = document.querySelector('#status');
                    el.className = phase; el.textContent = text;
                }""", [phase, text])
                await asyncio.sleep(1.1)
                assert not task.done() and not closed and not submissions and not cover_completed
                assert await page.locator("#title").input_value() == ""
                assert await page.locator("[contenteditable]").inner_text() == ""
            upload_response.set()
            await page.locator(".transcoding").wait_for(state="visible")
            await asyncio.sleep(1.1)
            assert not task.done() and not closed and not submissions and not cover_completed
            await page.evaluate("""() => {
                const status = document.querySelector('#status');
                status.className = 'preview-new'; status.textContent = '';
                const name = document.createElement('div'); name.className = 'name';
                name.textContent = document.querySelector('input[type=file]').files[0].name;
                status.append(name);
            }""")
            result = await asyncio.wait_for(task, timeout=10)
            assert result.status == "submitted"
            assert closed and cover_completed
            assert submissions == [{"title": payload.title, "body": payload.description}]
            assert sum(event["event"] == "submitting" for event in events) == 1
        finally:
            upload_response.set()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional routed Chromium fixture requires an explicit browser path")
async def test_real_dom_current_and_legacy_upload_states_and_stale_preview(account):
    from patchright.async_api import async_playwright

    ctx, payload, events = account
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=os.environ["EASY_PUBLISH_CHROMIUM"], headless=True, args=["--no-sandbox"])
        try:
            context = await browser.new_context(service_workers="block")
            await context.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<!doctype html>"))
            page = await context.new_page()
            await page.goto(video.SITES["xiaohongshu"].url)
            for root in ("cover-container", "upload-content"):
                for markup, expected in (
                    ('<div class="uploading">上传中 100%</div>', "uploading"),
                    ('<div class="transcoding">转码中，不影响发布</div>', "processing"),
                    ('<div class="fail">上传失败<div>private platform error</div></div>', "failed"),
                    ('<div class="canceled">拖拽或点击上传</div>', "idle"),
                    ('<div class="preview-new"><div class="name">clip.mp4</div></div>', "ready"),
                    ('<div class="preview-new"><div class="name">old-video.mp4</div></div>', "other_video"),
                    ('<div class="preview-new" style="display:none"><div class="name">clip.mp4</div></div>', "pending"),
                ):
                    await page.set_content(f'<div class="{root}"><input class="upload-input" type="file" accept=".mp4">{markup}</div><button>发布</button>')
                    assert (await video._xhs_upload_state(page, "clip.mp4"))["state"] == expected
            await page.set_content('<div class="cover-container"><input class="upload-input" type="file" accept=".mp4"><div class="preview-new"><div class="name">clip.mp4</div></div></div>')
            with pytest.raises(video.VideoStageError, match="已有视频"):
                await video._prepare(page, "xiaohongshu", payload, ctx)
            assert not events  # No fence or file selection for a stale preview.
            await page.set_content('<div role="dialog">安全验证</div>')
            with pytest.raises(video.VideoStageError, match="安全验证"):
                await video._xhs_upload_state(page, "clip.mp4")
        finally:
            await browser.close()
