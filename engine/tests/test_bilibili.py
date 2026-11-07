"""Offline protocol fixtures: no browser account, media upload, or live request."""
from contextlib import asynccontextmanager
from dataclasses import asdict
from io import BytesIO
import base64
import json
from pathlib import Path
import time
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import httpx
from PIL import Image
import pytest

from easy_publish.providers import bilibili as bili
from easy_publish.providers.base import ProviderContext, PublishPayload


class Harness:
    def __init__(self, root: Path):
        self.root = root
        self.events = []
        self.requests = []
        self.override = lambda request: None
        self.ctx = ProviderContext(root / "session.json", root, "/fixture/browser", self.events.append)
        self.video = root / "测试视频.mp4"
        self.video.write_bytes(b"0123456789")
        self.payload = PublishPayload("bilibili", "local-account", "video", "publish", "测试投稿", tags=["测试", "视频"], video_path=self.video, options={"copyright": "1", "tid": "171", "source": ""})
        self.cookies = [{"name": name, "value": value, "domain": ".bilibili.com", "path": "/", "expires": -1} for name, value in [("SESSDATA", "fixture%2Fsession-secret"), ("bili_jct", "fixture-csrf-secret"), ("DedeUserID", "42")]]
        self.session = {"version": 1, "account_id": "local-account", "mid": "42", "cookies": self.cookies}
        self.save()

    def save(self):
        self.ctx.session_path.write_text(json.dumps(self.session), encoding="utf-8")

    def response(self, request, data, code=0):
        return httpx.Response(200, json={"code": code, "data": data}, request=request)

    async def handle(self, request: httpx.Request):
        self.requests.append(request)
        alternate = self.override(request)
        if alternate is not None:
            return alternate
        if str(request.url) == bili.NAV:
            return self.response(request, {"isLogin": True, "mid": 42})
        if str(request.url) == bili.CATEGORIES:
            return self.response(request, {"typelist": [{"id": 160, "name": "生活", "children": [{"id": 171, "name": "日常"}, {"id": 138, "name": "搞笑"}]}]})
        if request.url.path == "/preupload" and request.url.params.get("r") == "probe":
            return httpx.Response(200, json={"OK": 1, "lines": [{"os": "upos", "query": "probe_version=20250923&upcdn=txa&zone=cs", "probe_url": "//ignored.example/OK"}]})
        if request.url.path == "/preupload":
            assert self.events and self.events[0]["event"] == "submitting"
            assert request.url.params["upcdn"] == "txa"
            return httpx.Response(200, json={"chunk_size": 4, "auth": "fixture-upload-secret", "endpoint": "//upos-cs-upcdntxa.bilivideo.com", "biz_id": 123, "upos_uri": "upos://ugc/fixture-object.mp4"})
        if request.url.host.endswith(".bilivideo.com"):
            assert "Cookie" not in request.headers
            assert request.headers["X-Upos-Auth"] == "fixture-upload-secret"
            assert self.events and self.events[0]["event"] == "submitting"
            if "uploads" in request.url.params:
                return httpx.Response(200, json={"upload_id": "fixture-upload-id"})
            if request.method == "PUT":
                return httpx.Response(200, content=b"")
            return httpx.Response(200, json={"OK": 1})
        if str(request.url) == bili.COVER:
            return self.response(request, {"url": "http://i0.hdslb.com/bfs/archive/fixture.jpg"})
        if request.url.path == "/x/vu/web/add/v3":
            return self.response(request, {"aid": 12345, "bvid": "BV1xx411c7mD"})
        raise AssertionError(f"Unexpected offline request: {request.method} {request.url.host}{request.url.path}")

    def submits(self):
        return [request for request in self.requests if request.url.path == "/x/vu/web/add/v3"]

    def mutations(self):
        return [request for request in self.requests if request.method != "GET" or (request.url.path == "/preupload" and request.url.params.get("r") == "upos")]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    value = Harness(tmp_path)
    original = httpx.AsyncClient
    transport = httpx.MockTransport(value.handle)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        kwargs["transport"] = transport
        return original(**kwargs)

    monkeypatch.setattr(bili.httpx, "AsyncClient", client)
    return value


@pytest.mark.asyncio
async def test_complete_upload_preserves_chunk_ranges_and_single_confirmed_receipt(harness):
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "submitted"
    assert result.remote_id == "BV1xx411c7mD"
    assert result.url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert "尚未确认公开发布" in result.message
    assert len(harness.submits()) == 1
    assert [event["event"] for event in harness.events].count("submitting") == 1
    chunks = [request for request in harness.requests if request.method == "PUT"]
    assert b"".join(request.content for request in chunks) == harness.video.read_bytes()
    assert [(int(r.url.params["partNumber"]), int(r.url.params["start"]), int(r.url.params["end"]), int(r.url.params["size"])) for r in chunks] == [(1, 0, 4, 4), (2, 4, 8, 4), (3, 8, 10, 2)]
    merge = next(r for r in harness.requests if r.url.host.endswith(".bilivideo.com") and r.method == "POST" and "biz_id" in r.url.params)
    assert json.loads(merge.content) == {"parts": [{"partNumber": i, "eTag": "etag"} for i in (1, 2, 3)]}
    submitted = json.loads(harness.submits()[0].content)
    assert submitted["videos"] == [{"filename": "fixture-object", "title": "测试投稿", "desc": ""}]
    assert submitted["copyright"] == 1 and submitted["tid"] == 171
    assert submitted["cover"] == "" and submitted["tag"] == "测试,视频"
    assert "dtime" not in submitted and "aid" not in submitted
    assert harness.submits()[0].url.params["csrf"] == "fixture-csrf-secret"
    api_requests = [r for r in harness.requests if r.url.host.endswith(".bilibili.com")]
    assert all("SESSDATA=fixture%2Fsession-secret" in r.headers["Cookie"] for r in api_requests)
    assert all("X-Upos-Auth" not in r.headers for r in api_requests)
    assert "secret" not in json.dumps([asdict(result), harness.events], ensure_ascii=False)


@pytest.mark.asyncio
async def test_png_cover_converted_before_upload_and_cdn_url_canonicalized(harness):
    cover = harness.root / "cover.png"
    Image.new("RGBA", (12, 12), (255, 0, 0, 0)).save(cover)
    harness.payload.cover_path = cover
    harness.payload.options.update(copyright="2", source="https://example.org/original")
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "submitted"
    request = next(r for r in harness.requests if str(r.url) == bili.COVER)
    form = parse_qs(request.content.decode())
    assert form["csrf"] == ["fixture-csrf-secret"]
    assert form["cover"][0].startswith("data:image/jpeg;base64,")
    with Image.open(BytesIO(base64.b64decode(form["cover"][0].split(",")[1]))) as image:
        assert image.format == "JPEG"
        assert image.getpixel((0, 0)) == (255, 255, 255)
    submitted = json.loads(harness.submits()[0].content)
    assert submitted["cover"] == "https://i0.hdslb.com/bfs/archive/fixture.jpg"
    assert submitted["copyright"] == 2 and submitted["source"] == "https://example.org/original"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "server_error", "redirect", "malformed", "no_receipt", "rejected"])
async def test_final_submit_uncertainty_never_retries_or_reports_published(harness, outcome):
    def override(request):
        if request.url.path != "/x/vu/web/add/v3":
            return None
        if outcome == "timeout":
            raise httpx.ReadTimeout("fixture secret must never escape", request=request)
        if outcome == "server_error":
            return httpx.Response(503, text="fixture secret must never escape")
        if outcome == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.invalid/steal"})
        if outcome == "malformed":
            return httpx.Response(200, text="fixture secret must never escape")
        if outcome == "no_receipt":
            return harness.response(request, {"aid": True, "bvid": "https://attacker.invalid"})
        return harness.response(request, {}, code=-352)

    harness.override = override
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "unknown"
    assert len(harness.submits()) == 1
    assert result.url == bili.MANAGE
    assert "secret" not in result.message
    assert all(r.url.host != "attacker.invalid" for r in harness.requests)


@pytest.mark.asyncio
async def test_numeric_aid_is_also_a_confirmed_submission(harness):
    harness.override = lambda request: harness.response(request, {"aid": 12345}) if request.url.path == "/x/vu/web/add/v3" else None
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "submitted" and result.remote_id == "12345"
    assert result.url == "https://www.bilibili.com/video/av12345"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["init", "chunk", "merge", "cover"])
async def test_upload_failures_stop_before_archive_submit(harness, stage):
    if stage == "cover":
        path = harness.root / "cover.jpg"
        Image.new("RGB", (12, 12)).save(path)
        harness.payload.cover_path = path

    def override(request):
        if ((stage == "init" and "uploads" in request.url.params) or
                (stage == "chunk" and request.method == "PUT") or
                (stage == "merge" and "biz_id" in request.url.params) or
                (stage == "cover" and str(request.url) == bili.COVER)):
            return httpx.Response(503, text="fixture failure")

    harness.override = override
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "unknown"
    assert not harness.submits()
    if stage == "chunk":
        assert len([r for r in harness.requests if r.method == "PUT"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["http://upos.bilivideo.com", "//upos.bilivideo.com.evil.test", "//127.0.0.1", "//bilibili.com@evil.test", "//upos.bilivideo.com:8080", "//upos.bilivideo.com/path", "//upos.bilivideo.com?redirect=secret"])
async def test_untrusted_upload_endpoint_never_receives_token_or_video(harness, endpoint):
    def override(request):
        if request.url.path == "/preupload" and request.url.params.get("r") == "upos":
            return httpx.Response(200, json={"chunk_size": 4, "auth": "fixture-upload-secret", "endpoint": endpoint, "biz_id": 123, "upos_uri": "upos://ugc/test.mp4"})

    harness.override = override
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "unknown"
    assert not any(r.method != "GET" for r in harness.requests)


@pytest.mark.asyncio
async def test_stale_or_wrong_account_category_rejected_before_fence(harness):
    harness.payload.options["tid"] = "999"
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not harness.events and not harness.mutations()


@pytest.mark.asyncio
async def test_fence_ack_failure_prevents_even_upload_allocation(harness):
    def failed_ack(event):
        if event["event"] == "submitting":
            raise RuntimeError("fixture ACK rejected")

    harness.ctx.emit = failed_ack
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not harness.mutations()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["missing", "local_binding", "remote_identity", "cookie_identity", "expired", "offsite"])
async def test_missing_expired_or_cross_account_sessions_never_upload(harness, problem):
    if problem == "missing":
        harness.ctx.session_path.unlink()
    elif problem == "local_binding":
        harness.session["account_id"] = "other-local-account"
        harness.save()
    elif problem == "remote_identity":
        harness.override = lambda request: harness.response(request, {"isLogin": True, "mid": 43}) if str(request.url) == bili.NAV else None
    elif problem == "cookie_identity":
        harness.cookies[-1]["value"] = "43"
        harness.save()
    elif problem == "expired":
        harness.cookies[0]["expires"] = time.time() - 10
        harness.save()
    elif problem == "offsite":
        harness.cookies[0]["domain"] = ".bilibili.com.attacker.invalid"
        harness.save()
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "login_required"
    assert not harness.events and not harness.mutations()
    if problem != "remote_identity":
        assert not harness.requests


@pytest.mark.asyncio
async def test_check_returns_only_sanitized_account_categories(harness):
    result = await bili.check(harness.payload, harness.ctx)
    assert result.status == "ready"
    assert result.metadata == {"bilibiliCategories": [{"value": "171", "label": "生活 / 日常"}, {"value": "138", "label": "生活 / 搞笑"}]}
    assert "secret" not in json.dumps(asdict(result), ensure_ascii=False)
    assert not harness.events and not harness.mutations()


@pytest.mark.asyncio
async def test_check_without_session_does_not_open_network_or_browser(harness, monkeypatch):
    harness.ctx.session_path.unlink()
    client = AsyncMock(side_effect=AssertionError("network must not open"))
    monkeypatch.setattr(bili, "_client", client)
    result = await bili.check(harness.payload, harness.ctx)
    assert result.status == "login_required"
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("categories", [None, [], [{"id": 1, "name": "\ninvalid"}], [{"id": "secret", "name": "bad"}]])
async def test_missing_category_permissions_not_silently_ready(harness, categories):
    harness.override = lambda request: harness.response(request, {"typelist": categories}) if str(request.url) == bili.CATEGORIES else None
    result = await bili.check(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not result.metadata
    assert not harness.mutations()


@pytest.mark.asyncio
async def test_browser_login_keeps_only_bilibili_cookies_and_binds_identity(harness, monkeypatch):
    harness.ctx.session_path.unlink()
    context = AsyncMock()
    context.cookies.return_value = harness.cookies + [{"name": "third-party-secret", "value": "must-not-save", "domain": ".other.invalid", "path": "/"}]
    page = AsyncMock()

    @asynccontextmanager
    async def browser(ctx):
        assert ctx is harness.ctx
        yield context, page

    monkeypatch.setattr(bili, "_browser", browser)
    result = await bili.login(harness.payload, harness.ctx)
    assert result.status == "ready"
    saved = json.loads(harness.ctx.session_path.read_text())
    assert saved["mid"] == "42" and saved["account_id"] == "local-account"
    assert saved["cookies"] == harness.cookies
    assert "third-party-secret" not in harness.ctx.session_path.read_text()
    assert not harness.mutations()
    page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_relogin_cannot_replace_an_existing_account_identity(harness, monkeypatch):
    original = harness.ctx.session_path.read_text()
    cookies = [dict(cookie) for cookie in harness.cookies]
    cookies[-1]["value"] = "43"
    harness.override = lambda request: harness.response(request, {"isLogin": True, "mid": 43}) if str(request.url) == bili.NAV else None
    context, page = AsyncMock(), AsyncMock()
    context.cookies.return_value = cookies

    @asynccontextmanager
    async def browser(ctx):
        yield context, page

    monkeypatch.setattr(bili, "_browser", browser)
    result = await bili.login(harness.payload, harness.ctx)
    assert result.status == "login_required"
    assert harness.ctx.session_path.read_text() == original
    assert not harness.mutations()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("title", "x" * 81), ("description", "x" * 2001), ("tags", []), ("tags", ["x"] * 13), ("tags", ["a,b"]), ("tags", ["a，b"]), ("tags", ["x" * 21]), ("tags", ["same", " same"]), ("kind", "article"), ("mode", "draft")])
async def test_invalid_content_never_opens_network(harness, field, value):
    setattr(harness.payload, field, value)
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not harness.requests and not harness.events


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"copyright": "2", "source": ""}, {"copyright": "2", "source": "javascript:alert(1)"}, {"copyright": "2", "source": "https://user:pass@example.org"}, {"copyright": "2", "source": "https://[bad"}, {"copyright": "2", "source": "https://example.org/" + "x" * 200}, {"copyright": "3"}, {"tid": "0"}])
async def test_invalid_platform_options_never_open_network(harness, options):
    harness.payload.options.update(options)
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not harness.requests and not harness.events


@pytest.mark.asyncio
async def test_invalid_cover_stops_before_upload(harness):
    harness.payload.cover_path = harness.video
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not harness.requests and not harness.events


@pytest.mark.asyncio
async def test_unavailable_probe_line_stops_before_fence(harness):
    harness.override = lambda request: httpx.Response(200, json={"lines": [{"os": "upos", "query": "url=https://evil.invalid"}]}) if request.url.params.get("r") == "probe" else None
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "needs_attention"
    assert not harness.events and not harness.mutations()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["https://example.org/other-platform", "not a URL", " " * 201])
async def test_original_submission_ignores_unused_source_and_unknown_options(harness, source):
    harness.payload.options.update(source=source, unrelatedField="other-platform-value")
    result = await bili.publish(harness.payload, harness.ctx)
    assert result.status == "submitted"
    submitted = json.loads(harness.submits()[0].content)
    assert submitted["copyright"] == 1 and submitted["source"] == ""
    assert "unrelatedField" not in submitted
    assert harness.payload.options["source"] == source
    assert harness.payload.options["unrelatedField"] == "other-platform-value"
