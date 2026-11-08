"""Offline Kuaishou browser/protocol fixtures. No real accounts or uploads."""
from contextlib import asynccontextmanager
from dataclasses import asdict
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from easy_publish.providers import kuaishou as ks
from easy_publish.providers.base import ProviderContext, PublishPayload


class Request:
    def __init__(self, path, body=None, method="POST", host="cp.kuaishou.com"):
        self.url = "https://" + host + path
        self.method = method
        self.post_data_json = body


class Response:
    def __init__(self, request, value, status=200):
        self.url, self.request, self.status = request.url, request, status
        self.body = AsyncMock(return_value=json.dumps(value).encode())


class Locator:
    def __init__(self, exists=False, text=""):
        self.exists, self.text = exists, text
        self.first = self
        self.click = AsyncMock()
        self.wait_for = AsyncMock()
        self.is_enabled = AsyncMock(return_value=True)
        self.all_text_contents = AsyncMock(return_value=[])

    async def count(self): return int(self.exists)
    async def is_visible(self): return self.exists
    async def inner_text(self): return self.text
    def filter(self, **kwargs): return self
    def get_by_role(self, *args, **kwargs): return self
    def locator(self, *args, **kwargs): return self


class Context:
    def __init__(self):
        self.handlers = {}
        self.pages = []
        self.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})

    def on(self, name, callback): self.handlers.setdefault(name, []).append(callback)
    def remove_listener(self, name, callback): self.handlers[name].remove(callback)
    def emit(self, name, value):
        for callback in list(self.handlers.get(name, [])): callback(value)

    async def response(self, path, value, body=None):
        request = Request(path, body)
        self.emit("request", request)
        self.emit("response", Response(request, value))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return request


class Page:
    def __init__(self, context, identity="42", home=False):
        self.context, self.identity, self.home = context, identity, home
        self.url, self.closed = "about:blank", False
        self.keyboard = MagicMock()
        self.keyboard.press = AsyncMock()
        self.keyboard.insert_text = AsyncMock()
        self.modal = Locator()
        context.pages.append(self)

    def is_closed(self): return self.closed
    def locator(self, *args, **kwargs): return self.modal
    def get_by_text(self, *args, **kwargs): return Locator()
    async def goto(self, url, **kwargs):
        self.url = "https://cp.kuaishou.com/profile" if self.home else ks.UPLOAD_URL
        await self.context.response("/rest/v2/creator/pc/authority/account/current",
                                    {"result": 1, "data": {"userId": self.identity, "logined": True}})


@pytest.fixture
def setup(tmp_path):
    media = tmp_path / "中文视频.mp4"
    media.write_bytes(b"offline video fixture")
    ctx = ProviderContext(tmp_path / "session.json", tmp_path, "/fixture/browser", [])
    events = []
    ctx.emit = events.append
    payload = PublishPayload("kuaishou", "local-account", "video", "publish", "视频标题", description="完整描述", tags=["测试"], video_path=media)
    ctx.session_path.write_text(json.dumps({"version": 1, "account_id": payload.account_id, "identity": "42", "state": {"cookies": [], "origins": []}}))
    return payload, ctx, events


def browser(context, page):
    @asynccontextmanager
    async def open_browser(*args):
        yield context, page
    return open_browser


@pytest.mark.parametrize("value", [None, {}, {"result": True, "data": {"userId": 42}},
    {"result": 0, "data": {"userId": 42}}, {"result": 1, "data": {"userId": 42, "logined": False}},
    {"result": 1, "data": {"userId": True}}, {"result": 1, "data": {"userId": "not-an-id"}},
    {"result": 1, "data": {"userName": "Public name"}}])
def test_authentication_requires_explicit_success_and_account_identity(value):
    assert ks._identity(value) is None


def test_identity_allows_verified_numeric_user_id_without_optional_logined():
    assert ks._identity({"result": 1, "data": {"userId": "42"}}) == "42"


@pytest.mark.asyncio
async def test_login_finishes_on_authenticated_home_without_upload_widget(setup):
    payload, ctx, _ = setup
    ctx.session_path.unlink()
    context = Context()
    page = Page(context, home=True)
    with patch.object(ks, "_browser", browser(context, page)):
        result = await ks.login(payload, ctx)
    assert result.status == "ready"
    session = json.loads(ctx.session_path.read_text())
    assert session["account_id"] == payload.account_id and session["identity"] == "42"
    assert session["state"] == {"cookies": [], "origins": []}
    assert not context.handlers["response"] and not context.handlers["request"]


@pytest.mark.asyncio
async def test_login_cannot_silently_replace_bound_account(setup):
    payload, ctx, _ = setup
    original = ctx.session_path.read_bytes()
    context = Context()
    page = Page(context, identity="43")
    with patch.object(ks, "_browser", browser(context, page)):
        result = await ks.login(payload, ctx)
    assert result.status == "login_required"
    assert ctx.session_path.read_bytes() == original


@pytest.mark.asyncio
async def test_closed_login_window_stops_without_waiting_full_timeout(setup):
    _, ctx, _ = setup
    context = Context()
    page = Page(context)
    page.closed = True
    with pytest.raises(ks.KuaishouError, match="窗口已关闭"):
        await ks._wait_authenticated(context, ks.Evidence(), ctx, 600)


@pytest.mark.asyncio
async def test_public_creator_shell_without_authority_response_never_becomes_ready(setup):
    _, ctx, _ = setup
    context = Context()
    page = Page(context)
    page.url = ks.UPLOAD_URL
    with pytest.raises(TimeoutError):
        await ks._wait_authenticated(context, ks.Evidence(), ctx, 0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["check", "publish"])
async def test_missing_session_returns_before_browser_import(setup, action):
    payload, ctx, events = setup
    ctx.session_path.unlink()
    with patch.object(ks, "_browser") as launch:
        result = await getattr(ks, action)(payload, ctx)
    assert result.status == "login_required"
    launch.assert_not_called()
    assert not events


@pytest.mark.asyncio
async def test_session_cannot_cross_local_accounts(setup):
    payload, ctx, _ = setup
    payload.account_id = "other-account"
    with patch.object(ks, "_browser") as launch:
        result = await ks.check(payload, ctx)
    assert result.status == "login_required"
    launch.assert_not_called()


@pytest.mark.parametrize("title, description, tags, message", [
    ("", "", [], "标题"), ("字" * 201, "", [], "标题"),
    ("标题", "", ["1", "2", "3", "4", "5"], "4 个话题"),
    ("标题", "", ["two words"], "空白"), ("标题", "", ["#话题"], "井号"),
    ("标题", "", ["重复", "重复"], "重复"),
    ("字", "字" * 499, [], "500"), ("字", "😀" * 250, [], "500"),
])
def test_caption_validation_is_explicit_without_truncation(title, description, tags, message):
    assert any(message in error for error in ks.metadata_errors(title, description, tags))


def test_caption_preserves_all_fields_and_uses_utf16_boundary():
    assert ks.caption_text("标题", "完整描述", ["第一", "第二", "第三", "第四"]) == "标题\n完整描述\n#第一 #第二 #第三 #第四"
    assert not ks.metadata_errors("字", "字" * 498, [])
    assert not ks.metadata_errors("字", "😀" * 249, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(ks.SUBMIT_PATHS))
async def test_observed_current_request_success_is_submitted_not_published(setup, path):
    payload, _, _ = setup
    evidence = ks.Evidence()
    evidence.upload_file_id = "owned-file"
    evidence.armed_caption = ks._normalize_caption(ks.caption_text(payload.title, payload.description, payload.tags))
    request = Request(path, {"caption": evidence.armed_caption, "fileId": "owned-file"})
    evidence.request(request)
    await evidence._read(Response(request, {"result": 1, "data": {"private": "fixture-cookie-secret"}}))
    assert evidence.result.status == "submitted"
    assert evidence.result.url == ks.MANAGE_URL
    assert "fixture-cookie-secret" not in json.dumps(asdict(evidence.result))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["before_arm", "other_caption", "other_file", "wrong_host", "wrong_path", "get"])
async def test_unrelated_or_stale_response_never_confirms_submission(change):
    evidence = ks.Evidence()
    evidence.upload_file_id = "owned"
    evidence.armed_caption = None if change == "before_arm" else "caption"
    request = Request("/rest/cp/works/v2/video/pc/submit", {"caption": "caption", "fileId": "owned"})
    if change == "other_caption": request.post_data_json["caption"] = "old work"
    if change == "other_file": request.post_data_json["fileId"] = "old-file"
    if change == "wrong_host": request.url = "https://cp.kuaishou.com.attacker.test/rest/cp/works/v2/video/pc/submit"
    if change == "wrong_path": request.url = ks.UPLOAD_URL
    if change == "get": request.method = "GET"
    evidence.request(request)
    if change == "before_arm": evidence.armed_caption = "caption"
    await evidence._read(Response(request, {"result": 1}))
    assert evidence.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [({}, 200), ({"result": True}, 200), ({"result": 0}, 200), ({"result": 1}, 503)])
async def test_failed_final_ack_is_ambiguous(body, status):
    evidence = ks.Evidence()
    evidence.upload_file_id, evidence.armed_caption = "owned", "caption"
    request = Request("/rest/cp/works/v2/video/pc/submit", {"caption": "caption", "fileId": "owned"})
    evidence.request(request)
    await evidence._read(Response(request, body, status))
    assert evidence.ambiguous and evidence.result is None


@pytest.mark.asyncio
async def test_upload_finish_is_media_evidence_not_publication():
    evidence = ks.Evidence()
    evidence.upload_armed = True
    request = Request("/rest/cp/works/v2/video/pc/upload/finish", {"token": "fixture-private"})
    evidence.request(request)
    await evidence._read(Response(request, {"result": 1, "data": {"fileId": "owned-file"}}))
    assert evidence.upload_file_id == "owned-file"
    assert evidence.result is None and evidence.all_submissions == 0


async def wait_for_handoff(events, seconds=3):
    async with asyncio.timeout(seconds):
        while not any(event["event"] == "awaiting_user" for event in events):
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_manual_wait_never_clicks_publish_or_existing_confirmation(setup):
    payload, ctx, events = setup
    page = Page(Context())
    page.modal = Locator(True)
    task = asyncio.create_task(ks._wait_for_manual_publish(page, ks.Evidence(), payload, ctx))
    await wait_for_handoff(events)
    await asyncio.sleep(0.03)
    assert not task.done()
    page.modal.click.assert_not_called()
    page.closed = True
    result = await task
    assert result.status == "unknown"


@pytest.mark.asyncio
async def test_prior_manual_submission_is_not_submitted_again(setup):
    payload, ctx, events = setup
    evidence = ks.Evidence()
    evidence.all_submissions = 1
    evidence.result = ks.ProviderResult("submitted", "Earlier result")
    page = Page(Context())
    task = asyncio.create_task(ks._wait_for_manual_publish(page, evidence, payload, ctx))
    await wait_for_handoff(events)
    assert not task.done()
    page.closed = True
    assert (await task).status == "unknown"


@pytest.mark.asyncio
async def test_manual_receipt_for_owned_video_allows_user_caption_changes(setup):
    payload, ctx, events = setup
    context, evidence = Context(), ks.Evidence()
    page = Page(context)
    evidence.upload_file_id = "owned-file"
    async with evidence.watch(context):
        task = asyncio.create_task(ks._wait_for_manual_publish(page, evidence, payload, ctx))
        await wait_for_handoff(events)
        assert not task.done() and evidence.all_submissions == 0
        # Simulates the human's final click after making a correction on-page.
        await context.response("/rest/cp/works/v3/video/pc/submit", {"result": 1},
                               {"caption": "用户在网页修改了正文", "fileId": "owned-file"})
        result = await asyncio.wait_for(task, 2)
    assert result.status == "submitted"


@pytest.mark.asyncio
async def test_failed_manual_receipt_keeps_window_open_until_user_closes(setup):
    payload, ctx, events = setup
    context, evidence = Context(), ks.Evidence()
    page = Page(context)
    evidence.upload_file_id = "owned-file"
    async with evidence.watch(context):
        task = asyncio.create_task(ks._wait_for_manual_publish(page, evidence, payload, ctx))
        await wait_for_handoff(events)
        await context.response("/rest/cp/works/v3/video/pc/submit", {"result": 0},
                               {"caption": payload.title, "fileId": "owned-file"})
        await asyncio.sleep(0.25)
        assert not task.done()
        assert any("窗口会保持打开" in item["message"] for item in events)
        page.closed = True
        assert (await task).status == "unknown"


@pytest.mark.asyncio
async def test_manual_wait_is_cancellable_without_a_publish_click(setup):
    payload, ctx, events = setup
    page = Page(Context())
    task = asyncio.create_task(ks._wait_for_manual_publish(page, ks.Evidence(), payload, ctx))
    await wait_for_handoff(events)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    page.modal.click.assert_not_called()


@pytest.mark.asyncio
async def test_upload_fence_precedes_file_chooser_mutation(setup):
    payload, ctx, events = setup
    page = Page(Context())
    empty, upload = Locator(), Locator(True)
    page.locator = lambda selector: empty if "work-description-edit" in selector else upload
    chooser = SimpleNamespace(set_files=AsyncMock())

    async def set_files(path):
        assert events[-1]["event"] == "submitting"
        raise RuntimeError("abort fixture after mutation")

    chooser.set_files.side_effect = set_files

    @asynccontextmanager
    async def file_chooser(**kwargs):
        future = asyncio.get_running_loop().create_future()
        future.set_result(chooser)
        yield SimpleNamespace(value=future)

    page.expect_file_chooser = file_chooser
    with pytest.raises(RuntimeError, match="abort fixture"):
        await ks._prepare(page, payload, ctx, ks.Evidence())
    chooser.set_files.assert_awaited_once_with(str(payload.video_path))


@pytest.mark.asyncio
async def test_existing_platform_draft_is_preserved_before_upload(setup):
    payload, ctx, events = setup
    page = Page(Context())
    page.locator = lambda selector: Locator(True, "Existing unsaved draft")
    with pytest.raises(ks.KuaishouError, match="已有未完成"):
        await ks._prepare(page, payload, ctx, ks.Evidence())
    assert not events


@pytest.mark.asyncio
async def test_uploaded_video_with_disabled_publish_does_not_delay_manual_handoff(setup):
    _, _, _ = setup
    page = Page(Context())
    button = Locator(True)
    button.is_enabled.return_value = False
    evidence = ks.Evidence()
    evidence.upload_file_id = "owned-file"
    await asyncio.wait_for(ks._wait_upload(page, button, evidence), 0.2)
    button.click.assert_not_called()


@pytest.mark.asyncio
async def test_statement_requires_user_choice_and_is_never_selected_automatically(setup):
    _, ctx, events = setup
    page = Page(Context())
    placeholder = Locator(True)
    page.get_by_text = lambda *args, **kwargs: placeholder
    await ks._wait_statement(page, ctx)
    placeholder.click.assert_not_called()
    assert any("选择符合本次内容" in event["message"] for event in events)


@pytest.mark.asyncio
async def test_publish_failure_after_upload_keeps_window_for_manual_completion(setup):
    payload, ctx, events = setup
    context = Context()
    page = Page(context)

    async def prepare(page, payload, ctx, evidence):
        ctx.submitting()
        raise RuntimeError("remote upload may exist")

    with patch.object(ks, "_browser", browser(context, page)), patch.object(ks, "_prepare", prepare):
        task = asyncio.create_task(ks.publish(payload, ctx))
        await wait_for_handoff(events)
        assert not task.done()
        page.closed = True
        result = await task
    assert result.status == "unknown"


@pytest.mark.asyncio
async def test_session_save_failure_does_not_erase_accepted_receipt(setup):
    payload, ctx, _ = setup
    context = Context()
    page = Page(context)

    async def prepare(page, payload, ctx, evidence):
        ctx.submitting()
        return Locator(True)

    with patch.object(ks, "_browser", browser(context, page)), patch.object(ks, "_prepare", prepare), \
         patch.object(ks, "_wait_for_manual_publish", AsyncMock(return_value=ks.ProviderResult("submitted", "Accepted"))), \
         patch.object(ks, "_save_session", AsyncMock(side_effect=OSError("fixture disk error"))):
        result = await ks.publish(payload, ctx)
    assert result.status == "submitted"


@pytest.mark.asyncio
async def test_remote_identity_mismatch_stops_before_upload(setup):
    payload, ctx, events = setup
    context = Context()
    page = Page(context, identity="43")
    with patch.object(ks, "_browser", browser(context, page)), patch.object(ks, "_prepare", AsyncMock()) as prepare:
        result = await ks.publish(payload, ctx)
    assert result.status == "login_required"
    prepare.assert_not_called()
    assert not any(event["event"] == "submitting" for event in events)


@pytest.mark.parametrize("change", ["draft", "article", "bad_cover"])
def test_unsupported_content_is_rejected_explicitly(setup, change):
    payload, ctx, _ = setup
    if change == "draft": payload.mode = "draft"
    if change == "article": payload.kind = "article"
    if change == "bad_cover":
        payload.cover_path = ctx.workspace / "bad.png"
        payload.cover_path.write_text("not a picture")
    assert ks._validate(payload)


def test_png_cover_is_supported_without_silent_discard(setup):
    payload, ctx, _ = setup
    payload.cover_path = ctx.workspace / "cover.png"
    Image.new("RGB", (10, 10)).save(payload.cover_path)
    assert ks._validate(payload) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(ks.UPLOAD_FINISH_PATHS))
async def test_all_verified_upload_modes_bind_the_uploaded_file(path):
    evidence = ks.Evidence()
    evidence.upload_armed = True
    request = Request(path, {"token": "fixture"})
    evidence.request(request)
    await evidence._read(Response(request, {"result": 1, "data": {"fileId": "uploaded-file"}}))
    assert evidence.upload_file_id == "uploaded-file"
    assert evidence.result is None


@pytest.mark.asyncio
async def test_upload_and_submit_from_another_page_are_not_task_receipts():
    evidence = ks.Evidence()
    evidence.owned_page = object()
    evidence.upload_armed = True
    evidence.upload_file_id, evidence.armed_caption = "owned", "caption"
    for path in ("/rest/cp/works/v2/video/pc/upload/finish", "/rest/cp/works/v2/video/pc/submit"):
        request = Request(path, {"caption": "caption", "fileId": "owned"})
        request.frame = SimpleNamespace(page=object())
        evidence.request(request)
        await evidence._read(Response(request, {"result": 1, "data": {"fileId": "other"}}))
    assert evidence.result is None
    assert evidence.upload_file_id == "owned"


@pytest.mark.asyncio
async def test_authentication_change_during_upload_stops_before_final_click(setup):
    payload, ctx, _ = setup
    context = Context()
    page = Page(context)

    async def prepare(page, payload, ctx, evidence):
        ctx.submitting()
        evidence.identity = "another-account"
        return Locator(True)

    with patch.object(ks, "_browser", browser(context, page)), patch.object(ks, "_prepare", prepare), \
         patch.object(ks, "_wait_for_manual_publish", AsyncMock()) as submit:
        result = await ks.publish(payload, ctx)
    assert result.status == "unknown"
    submit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("drop_topic", [False, True])
async def test_prepare_preserves_text_commits_topics_and_detects_account_quota(setup, drop_topic):
    payload, ctx, _ = setup
    payload.tags = ["第一", "第二", "第三", "第四"]
    context, evidence = Context(), ks.Evidence()
    page = Page(context)
    upload, empty, button = Locator(True), Locator(), Locator(True)
    editor = Locator(True, ks.caption_text(payload.title, payload.description, payload.tags))
    editor.all_text_contents.return_value = ["#" + tag for tag in (payload.tags[:3] if drop_topic else payload.tags)]
    # On initial entry this is a new upload screen, not an old platform draft.
    page.locator = lambda selector: empty if "work-description-edit" in selector else upload
    page.get_by_role = lambda *args, **kwargs: button

    async def set_files(path):
        request = Request("/rest/cp/works/v2/video/pc/upload/finish", {"token": "fixture"})
        request.frame = SimpleNamespace(page=page)
        evidence.request(request)
        await evidence._read(Response(request, {"result": 1, "data": {"fileId": "owned-file"}}))

    @asynccontextmanager
    async def file_chooser(**kwargs):
        future = asyncio.get_running_loop().create_future()
        future.set_result(SimpleNamespace(set_files=AsyncMock(side_effect=set_files)))
        yield SimpleNamespace(value=future)

    page.expect_file_chooser = file_chooser
    with patch.object(ks, "_description_editor", AsyncMock(return_value=editor)), patch.object(ks, "_dismiss_guide", AsyncMock()):
        if drop_topic:
            with pytest.raises(ks.KuaishouError, match="未完整识别"):
                await ks._prepare(page, payload, ctx, evidence)
        else:
            assert await ks._prepare(page, payload, ctx, evidence) is button
    inserted = [call.args[0] for call in page.keyboard.insert_text.await_args_list]
    assert inserted == ["视频标题\n完整描述", "第一", "第二", "第三", "第四"]
    pressed = [call.args[0] for call in page.keyboard.press.await_args_list]
    assert pressed.count("Shift+Digit3") == 4 and pressed.count("Space") == 4
    assert evidence.upload_file_id == "owned-file"
    button.click.assert_not_called()


def test_final_topic_separator_is_reserved_at_caption_boundary():
    assert not ks.metadata_errors("字", "文" * 494, ["话"])
    assert any("500" in message for message in ks.metadata_errors("字", "文" * 495, ["话"]))


@pytest.mark.asyncio
async def test_routed_browser_upload_topic_cover_and_manual_submit(setup, monkeypatch):
    """Uses only routed local HTML/API fixtures, never a platform account.

    Enable explicitly with EASY_PUBLISH_CHROMIUM=/path/to/chromium. Native
    package jobs can run all protocol fixtures without installing a browser.
    """
    import os
    executable = os.environ.get("EASY_PUBLISH_CHROMIUM")
    if not executable or not Path(executable).is_file():
        pytest.skip("Set EASY_PUBLISH_CHROMIUM for the routed browser fixture")
    from patchright.async_api import async_playwright

    payload, ctx, events = setup
    payload.tags = ["第一", "第二", "第三", "第四"]
    payload.cover_path = ctx.workspace / "cover.png"
    Image.new("RGB", (12, 12)).save(payload.cover_path)
    requests, unexpected, pages = [], [], []
    html = r'''<!doctype html><meta charset="utf-8"><title>Offline Kuaishou fixture</title>
<button class="_upload-btn_fixture">上传视频</button><input id="video-file" type="file" hidden>
<div id="work-description-edit" contenteditable="true" style="display:none;min-height:90px;border:1px solid"></div>
<section><div><span>封面设置</span></div><div><div id="cover-open">选择封面</div></div></section>
<div role="document" class="ant-modal" style="display:none"><button id="cover-tab">上传封面</button><input id="cover-file" type="file" accept="image/*"><button id="cover-confirm">确认</button></div>
<button id="publish">发布</button>
<script>
const editor=document.getElementById('work-description-edit');
const video=document.getElementById('video-file');
let topicMode=false,coverReady=false,uploaded=false;
fetch('/rest/v2/creator/pc/authority/account/current',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
document.querySelector('._upload-btn_fixture').onclick=()=>video.click();
video.onchange=async()=>{
 const response=await fetch('/rest/cp/works/v2/video/pc/upload/isolation/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:'offline-upload'})});
 uploaded=(await response.json()).result===1;editor.style.display='block';
};
editor.addEventListener('keydown',event=>{
 if(event.key==='#')topicMode=true;
 if(event.key===' '&&topicMode){
  const selection=getSelection(),node=selection.anchorNode;
  if(!node||node.nodeType!==Node.TEXT_NODE)return;
  const text=node.textContent,index=text.lastIndexOf('#'),name=text.slice(index+1);
  if(index<0||!name||/\s/.test(name))return;
  event.preventDefault();
  const before=document.createTextNode(text.slice(0,index));
  const outer=document.createElement('span');outer.setAttribute('is-tag','true');outer.contentEditable='false';
  const inner=document.createElement('span');inner.className='at-tag-item';inner.setAttribute('data-tag-name',name);inner.textContent='#'+name;outer.append(inner);
  const after=document.createTextNode('\u00a0');node.replaceWith(before,outer,after);selection.collapse(after,1);topicMode=false;
 }
});
document.getElementById('cover-open').onclick=()=>document.querySelector('.ant-modal').style.display='block';
document.getElementById('cover-file').onchange=()=>coverReady=true;
document.getElementById('cover-confirm').onclick=()=>document.querySelector('.ant-modal').style.display='none';
document.getElementById('publish').onclick=async()=>{
 if(!uploaded)throw Error('fixture upload incomplete');
 const response=await fetch('/rest/cp/works/v2/video/pc/isolation/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fileId:'owned-file',caption:editor.innerText.replace(/(\S)#/g,'$1 #'),coverReady})});
 if((await response.json()).result===1)history.pushState({},'', '/article/manage/video?status=2&from=publish');
};
</script>'''

    async def route(route):
        request = route.request
        requests.append(request)
        path = request.url.split("cp.kuaishou.com", 1)[-1]
        if request.url == ks.UPLOAD_URL:
            await route.fulfill(content_type="text/html; charset=utf-8", body=html)
        elif path == "/rest/v2/creator/pc/authority/account/current":
            await route.fulfill(json={"result": 1, "data": {"userId": "42", "logined": True}})
        elif path == "/rest/cp/works/v2/video/pc/upload/isolation/finish":
            assert any(event["event"] == "submitting" for event in events)
            await route.fulfill(json={"result": 1, "data": {"fileId": "owned-file"}})
        elif path == "/rest/cp/works/v2/video/pc/isolation/submit":
            await route.fulfill(json={"result": 1})
        else:
            unexpected.append(request.url)
            await route.abort()

    @asynccontextmanager
    async def local_browser(context, state=None):
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(executable_path=executable, headless=True)
            try:
                browser_context = await browser.new_context()
                await browser_context.route("**/*", route)
                page = await browser_context.new_page()
                pages.append(page)
                yield browser_context, page
            finally:
                await browser.close()

    monkeypatch.setattr(ks, "_browser", local_browser)
    task = asyncio.create_task(ks.publish(payload, ctx))
    try:
        async with asyncio.timeout(60):
            await wait_for_handoff(events, seconds=45)
            assert not task.done()
            assert not [request for request in requests if request.url.endswith("/isolation/submit")]
            # Fixture driver represents the human, not the publishing adapter.
            await pages[0].get_by_role("button", name="发布", exact=True).click()
            result = await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert result.status == "submitted", result.message
    assert not unexpected
    submissions = [request for request in requests if request.url.endswith("/isolation/submit")]
    assert len(submissions) == 1
    submitted = submissions[0].post_data_json
    assert submitted["fileId"] == "owned-file" and submitted["coverReady"] is True
    assert ks._normalize_caption(submitted["caption"]) == ks._normalize_caption(ks.caption_text(payload.title, payload.description, payload.tags))
    assert [event["event"] for event in events].count("submitting") == 1


def test_unused_options_do_not_override_real_kuaishou_requirements(setup):
    payload, _, _ = setup
    payload.options = {"copyright": "2", "source": "not needed", "unknown": "other-platform"}
    assert ks._validate(payload) is None
    payload.title = "标题" * 101
    assert "标题" in ks._validate(payload)
    assert payload.options["source"] == "not needed"
