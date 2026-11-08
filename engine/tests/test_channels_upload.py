"""Video Channels upload/iframe fixtures; every browser request is intercepted."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from urllib.parse import parse_qs, urlsplit
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
    media.write_bytes(b"fixture only; never sent to a platform")
    payload = PublishPayload("channels", "fixture", "video", "publish", "视频号测试短标题", description="测试正文", tags=["测试"], video_path=media)
    return ctx, payload, events


def upload_state(payload, **changes):
    state = {"failed": False, "verification": False, "blocked": False, "busy": False,
             "percent": None, "media": True, "removable": True, "ready": True,
             "body": video._channels_body(payload), "title_present": True, "title": payload.title}
    state.update(changes)
    return state


def fake_frame(state):
    frame = MagicMock()
    frame.url = "https://channels.weixin.qq.com/micro/content/post/create"
    frame.is_detached.return_value = False
    frame.evaluate = AsyncMock(return_value=state)
    return frame


def fake_page(frame):
    page = MagicMock()
    page.url = "https://channels.weixin.qq.com/platform/post/create"
    page.is_closed.return_value = False
    page.frames = [frame]
    return page


@pytest.mark.asyncio
async def test_98_percent_and_processing_wait_despite_enabled_publish(account, monkeypatch):
    ctx, payload, events = account
    frame = fake_frame(None)
    frame.evaluate.side_effect = [upload_state(payload, busy=True, percent=98),
                                  upload_state(payload, busy=True, percent=100), upload_state(payload, media=False)]
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    assert await video._channels_wait_upload(fake_page(frame), payload, ctx) is frame
    assert frame.evaluate.await_count == 3
    assert any("98%" in event["message"] for event in events)
    assert "上传及处理完成" in events[-1]["message"]


@pytest.mark.asyncio
async def test_remounted_iframe_and_transient_context_loss_do_not_reupload(account, monkeypatch):
    ctx, payload, _ = account
    first = fake_frame(upload_state(payload, busy=True, percent=98))
    replacement = fake_frame(None)
    replacement.evaluate.side_effect = [RuntimeError("execution context destroyed"), upload_state(payload)]
    frames = iter([first, None, replacement, replacement])
    monkeypatch.setattr(video, "_channels_frame", lambda page: next(frames))
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    assert await video._channels_wait_upload(MagicMock(), payload, ctx) is replacement
    first.locator.assert_not_called()
    replacement.locator.assert_not_called()


@pytest.mark.asyncio
async def test_text_action_timeout_is_bounded_by_upload_budget_not_early_failure(account, monkeypatch):
    ctx, payload, _ = account
    state = upload_state(payload, body="")
    frame = fake_frame(state)
    calls = 0

    async def fill(page, editor, body):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("fixture: still initializing editor")
        state["body"] = body

    monkeypatch.setattr(video, "_fill_editor", fill)
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    assert await video._channels_wait_upload(fake_page(frame), payload, ctx) is frame
    assert calls == 2


@pytest.mark.asyncio
async def test_failure_is_read_from_editor_frame_and_does_not_expose_text(account):
    ctx, payload, _ = account
    frame = fake_frame(upload_state(payload, failed=True, busy=True, percent=98))
    page = fake_page(frame)
    with pytest.raises(video.VideoStageError, match="页面显示视频上传失败"):
        await video._channels_wait_upload(page, payload, ctx)
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_verification_waits_without_dismissing_dialog(account, monkeypatch):
    ctx, payload, events = account
    frame = fake_frame(None)
    frame.evaluate.side_effect = [upload_state(payload, verification=True, blocked=True), upload_state(payload)]
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    await video._channels_wait_upload(fake_page(frame), payload, ctx)
    assert any("实名验证" in event["message"] for event in events)
    frame.locator.assert_not_called()


@pytest.mark.asyncio
async def test_present_but_unavailable_short_title_must_finish_before_ready(account, monkeypatch):
    ctx, payload, _ = account
    frame = fake_frame(None)
    frame.evaluate.side_effect = [upload_state(payload, title=None), upload_state(payload)]
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    await video._channels_wait_upload(fake_page(frame), payload, ctx)
    assert frame.evaluate.await_count == 2


@pytest.mark.asyncio
async def test_uneditable_description_waits_without_keyboard_actions(account, monkeypatch):
    ctx, payload, _ = account
    frame = fake_frame(None)
    frame.evaluate.side_effect = [upload_state(payload, body=None), upload_state(payload)]
    fill = AsyncMock()
    monkeypatch.setattr(video, "_fill_editor", fill)
    monkeypatch.setattr(video.asyncio, "sleep", AsyncMock())
    await video._channels_wait_upload(fake_page(frame), payload, ctx)
    fill.assert_not_awaited()


@pytest.mark.asyncio
async def test_unfinished_upload_has_bounded_specific_timeout(account):
    ctx, payload, _ = account
    frame = fake_frame(upload_state(payload, busy=True, percent=98))
    with pytest.raises(video.VideoStageError, match="表单或提交控件就绪超时"):
        await video._channels_wait_upload(fake_page(frame), payload, ctx, seconds=0.02)


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed(account):
    ctx, payload, _ = account
    frame = fake_frame(None)
    frame.evaluate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await video._channels_wait_upload(fake_page(frame), payload, ctx)


def test_frame_lookup_restricts_origin_path_and_reacquires_replacement():
    real = fake_frame({})
    wrong_path = fake_frame({})
    wrong_path.url = "https://channels.weixin.qq.com/unrelated"
    wrong_host = fake_frame({})
    wrong_host.url = "https://example.invalid/micro/content/post/create"
    wrong_port = fake_frame({})
    wrong_port.url = "https://channels.weixin.qq.com:444/micro/content/post/create"
    page = fake_page(real)
    page.frames = [wrong_host, wrong_path, wrong_port, real]
    assert video._channels_frame(page) is real
    replacement = fake_frame({})
    real.is_detached.return_value = True
    page.frames.append(replacement)
    assert video._channels_frame(page) is replacement


@pytest.mark.parametrize(("change", "message"), [("closed", "浏览器已关闭"), ("login", "登录已失效"), ("duplicate", "多个发布编辑器")])
def test_page_states_have_specific_errors(change, message):
    page = fake_page(fake_frame({}))
    if change == "closed":
        page.is_closed.return_value = True
    elif change == "login":
        page.url = "https://channels.weixin.qq.com/login.html"
    else:
        page.frames.append(fake_frame({}))
    with pytest.raises(video.VideoStageError, match=message):
        video._channels_frame(page)


@pytest.mark.asyncio
async def test_cover_lost_after_reload_stops_before_draft_save(account, monkeypatch):
    ctx, payload, _ = account
    payload.cover_path = payload.video_path
    payload.mode = "draft"
    frame = fake_frame(upload_state(payload))
    monkeypatch.setattr(video, "_channels_wait_upload", AsyncMock(return_value=frame))
    old_document = MagicMock()
    old_document.evaluate = AsyncMock(return_value=False)
    with pytest.raises(video.VideoStageError, match="无法确认封面保留"):
        await video._submit_once(fake_page(frame), video.ChannelsSubmission(frame, old_document), "channels", payload, ctx)
    frame.get_by_role.assert_not_called()


@pytest.mark.asyncio
async def test_channels_draft_save_timeout_never_clicks_twice(account, monkeypatch):
    ctx, payload, _ = account
    payload.mode = "draft"
    frame = fake_frame(upload_state(payload))
    button = MagicMock()
    button.first = button
    button.get_attribute = AsyncMock(return_value="")
    button.is_visible = AsyncMock(return_value=True)
    button.is_enabled = AsyncMock(return_value=True)
    button.click = AsyncMock(side_effect=TimeoutError("receipt lost"))
    frame.get_by_role.return_value = button
    monkeypatch.setattr(video, "_channels_wait_upload", AsyncMock(return_value=frame))
    monkeypatch.setattr(video, "_success_notice_present", AsyncMock(return_value=False))
    result = await video._submit_once(fake_page(frame), video.ChannelsSubmission(frame), "channels", payload, ctx)
    assert result.status == "unknown"
    button.click.assert_awaited_once()


SHELL_HTML = """<!doctype html><meta charset=utf-8>
<button class="weui-desktop-btn" onclick="history.pushState({},'', '/platform/post/create'); mount(1)">发表视频</button>
<div class="status-msg error">unrelated shell error must not control the editor</div>
<input type=file id=unrelated>
<script>
function mount(generation) {
  const old = document.querySelector('iframe'); if (old) old.remove();
  const frame = document.createElement('iframe'); frame.style='width:900px;height:700px';
  frame.src='/micro/content/post/create?generation='+generation; document.body.append(frame);
}
addEventListener('message', e => {
  if (e.origin === location.origin && e.data === 'remount') {
    document.querySelector('iframe').remove(); setTimeout(() => mount(2), 400);
  }
});
</script>"""


def editor_html(generation):
    status = 'style="display:none"' if generation == "1" else ''
    state_text = "" if generation == "1" else "视频处理中 100%"
    return f"""<!doctype html><meta charset=utf-8>
<input type=file accept="video/mp4" id=video>
<div class="media-status-content" {status}><div class="status-msg">{state_text}</div><div class=tag-inner>删除</div></div>
<div class="input-editor" contenteditable=true style="min-height:60px"></div>
<input placeholder="填写短标题有机会获得更多流量">
<div class="horizontal-cover-wrap" onclick="document.querySelector('#active').style.display='block'">4:3 动态封面</div>
<div class="weui-desktop-dialog" style="display:none"><h2>编辑视频号动态封面</h2>
 <div class=single-cover-uploader-wrap><input type=file id=stale-cover></div></div>
<div class="weui-desktop-dialog" id=active style="display:none"><h2>编辑视频号动态封面</h2>
 <div class=single-cover-uploader-wrap><input type=file accept="image/png" id=cover></div>
 <div class=weui-desktop-dialog__ft><button class=weui-desktop-btn_primary id=confirm disabled>确认</button></div></div>
<div class=form-btns><button id=publish disabled>发表</button><button id=draft disabled>保存草稿</button></div>
<script>
window.coverReady=false;
function syncButtons() {{
 const missing=!document.querySelector('.input-editor').innerText.trim() || !document.querySelector('input[placeholder]').value;
 document.querySelector('#publish').disabled=missing; document.querySelector('#draft').disabled=missing;
}}
document.querySelector('.input-editor').oninput=syncButtons;
document.querySelector('input[placeholder]').oninput=syncButtons;
document.querySelector('#video').onchange=async () => {{
 document.querySelector('.media-status-content').style.display='block';
 document.querySelector('.status-msg').textContent='正在上传 98%';
 await fetch('/fixture/upload', {{method:'POST',body:'fixture-no-media'}});
 parent.postMessage('remount', location.origin);
}};
document.querySelector('#stale-cover').onchange=() => fetch('/fixture/stale-cover');
document.querySelector('#cover').onchange=async () => {{
 await fetch('/fixture/cover', {{method:'POST',body:'fixture-no-image'}});
 document.querySelector('#confirm').disabled=false;
}};
document.querySelector('#confirm').onclick=() => {{window.coverReady=true;document.querySelector('#active').style.display='none';}};
for(const id of ['publish','draft']) document.querySelector('#'+id).onclick=async () => {{
 await fetch('/fixture/submit',{{method:'POST',body:JSON.stringify({{
   kind:id,body:document.querySelector('.input-editor').innerText,
   title:document.querySelector('input[placeholder]').value,cover:window.coverReady,generation:{generation}
 }})}});
 top.location.href='/platform/post/list';
}};
</script>"""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["publish", "draft"])
@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional routed Chromium fixture requires an explicit browser path")
async def test_real_browser_98_percent_remount_refill_cover_and_single_submission(account, monkeypatch, mode):
    from patchright.async_api import async_playwright

    ctx, payload, events = account
    payload.mode = mode
    payload.cover_path = payload.video_path
    upload_requested, upload_response = asyncio.Event(), asyncio.Event()
    cover_requested, cover_response = asyncio.Event(), asyncio.Event()
    uploads, submissions, stale_covers = [], [], []
    closed = False

    async def route(r):
        parsed = urlsplit(r.request.url)
        if parsed.hostname != video.SITES["channels"].host:
            await r.abort()
        elif parsed.path == "/platform":
            await r.fulfill(content_type="text/html", body=SHELL_HTML)
        elif parsed.path == "/micro/content/post/create":
            generation = parse_qs(parsed.query)["generation"][0]
            await r.fulfill(content_type="text/html", body=editor_html(generation))
        elif parsed.path == "/fixture/upload":
            assert events[0]["event"] == "submitting"
            uploads.append(r.request.post_data)
            upload_requested.set()
            await upload_response.wait()
            await r.fulfill(json={"fixture": True})
        elif parsed.path == "/fixture/cover":
            cover_requested.set()
            await cover_response.wait()
            await r.fulfill(json={"fixture": True})
        elif parsed.path == "/fixture/stale-cover":
            stale_covers.append(True)
            await r.abort()
        elif parsed.path == "/fixture/submit":
            submissions.append(json.loads(r.request.post_data))
            await r.fulfill(json={"fixture": True})
        elif parsed.path == "/platform/post/list":
            notice = "保存草稿成功" if mode == "draft" else "发表成功"
            await r.fulfill(content_type="text/html", body=f"<!doctype html><meta charset=utf-8><p role=status>{notice}</p>")
        else:
            await r.abort()

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

            monkeypatch.setattr(video, "_browser_page", browser_fixture)
            task = asyncio.create_task(video.publish(payload, ctx))
            await asyncio.wait_for(upload_requested.wait(), timeout=10)
            await asyncio.sleep(2.2)
            assert not task.done() and not closed and not submissions and not cover_requested.is_set()
            first = video._channels_frame(page)
            upload_response.set()
            async with asyncio.timeout(10):
                while True:
                    current = video._channels_frame(page)
                    if current is not first and current and "generation=2" in current.url:
                        break
                    await asyncio.sleep(0.1)
            assert first.is_detached()
            await current.locator(".media-status-content").wait_for(state="visible")
            await asyncio.sleep(2.2)
            assert not task.done() and not closed and not submissions and not cover_requested.is_set()
            if mode == "draft":
                # Successful render may remove the upload-status container.
                await current.locator(".media-status-content").evaluate("el => el.remove()")
            else:
                # Neither a completed transcode nor a hidden old percentage is busy.
                await current.locator(".status-msg").evaluate("""el => {
                    el.textContent = '转码完成';
                    const old = document.createElement('div'); old.style.display='none';
                    old.className='status-msg'; old.textContent='正在上传 98%'; el.append(old);
                }""")
            await asyncio.wait_for(cover_requested.wait(), timeout=10)
            await asyncio.sleep(1.1)
            assert not task.done() and not closed and not submissions
            cover_response.set()
            if mode == "publish":
                async with asyncio.timeout(10):
                    while not any(event["event"] == "awaiting_user" for event in events):
                        await asyncio.sleep(0.05)
                await asyncio.sleep(0.3)
                assert not task.done() and not closed and not submissions
                # Test driver acts as the user; provider never clicks this.
                await current.get_by_role("button", name="发表", exact=True).click()
            result = await asyncio.wait_for(task, timeout=10)
            assert result.status == ("draft_created" if mode == "draft" else "submitted")
            assert closed and len(uploads) == 1 and not stale_covers
            assert submissions == [{"kind": "draft" if mode == "draft" else "publish", "body": video._channels_body(payload),
                                    "title": payload.title, "cover": True, "generation": 2}]
            assert sum(event["event"] == "submitting" for event in events) == 1
        finally:
            upload_response.set()
            cover_response.set()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await browser.close()
