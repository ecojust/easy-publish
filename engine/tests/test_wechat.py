"""Official-account HTTP fixtures only: no credentials, sockets or real posts."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from easy_publish.providers import wechat
from easy_publish.providers.base import ProviderContext, PublishPayload


@pytest.fixture(autouse=True)
def clear_tokens():
    wechat._TOKEN_CACHE.clear()
    yield
    wechat._TOKEN_CACHE.clear()


@pytest.fixture
def setup(tmp_path):
    image = tmp_path / "cover.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture-only-image")
    events = []
    ctx = ProviderContext(tmp_path / "session", tmp_path / "work", None, events.append)
    payload = PublishPayload(platform_id="wechat", account_id="fixture-account", kind="article",
                             mode="draft", title="Fixture title", body_html="<p>Fixture body</p>",
                             cover_path=image, credentials={"appId": "fixture-app", "appSecret": "fixture-secret"})
    return payload, ctx, events


class OfficialAPI:
    def __init__(self, events, *, overrides=None, statuses=None):
        self.events = events
        self.overrides = overrides or {}
        self.statuses = list(statuses or [0])
        self.requests = []
        self.timeline = []

    def __call__(self, request):
        path = request.url.path
        self.requests.append(request)
        self.timeline.append((path, [event["event"] for event in self.events]))
        assert request.method == "POST"
        assert request.url.host == "api.weixin.qq.com"
        if path in self.overrides:
            value = self.overrides[path]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                value = value(request)
            return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)
        if path == "/cgi-bin/stable_token":
            assert not request.url.query
            data = json.loads(request.content)
            assert data["grant_type"] == "client_credential"
            assert data["force_refresh"] is False
            return httpx.Response(200, json={"access_token": "fixture-access-token", "expires_in": 7200})
        assert request.url.params.get("access_token") == "fixture-access-token"
        if path == "/cgi-bin/media/uploadimg":
            assert request.headers["content-type"].startswith("multipart/form-data;")
            assert b"fixture-only-image" in request.content
            return httpx.Response(200, json={"url": 'https://mmbiz.qpic.cn/fixture?x=1&caption="quoted"'})
        if path == "/cgi-bin/material/add_material":
            assert request.url.params.get("type") == "image"
            return httpx.Response(200, json={"media_id": "fixture-cover-id"})
        if path == "/cgi-bin/draft/add":
            return httpx.Response(200, json={"media_id": "fixture-draft-id"})
        if path == "/cgi-bin/freepublish/submit":
            assert json.loads(request.content) == {"media_id": "fixture-draft-id"}
            return httpx.Response(200, json={"publish_id": "fixture-publish-id"})
        if path == "/cgi-bin/freepublish/get":
            assert json.loads(request.content) == {"publish_id": "fixture-publish-id"}
            return httpx.Response(200, json={"publish_status": self.statuses.pop(0),
                "article_detail": {"item": [{"article_url": "https://mp.weixin.qq.com/s/fixture"}]}})
        raise AssertionError(f"Unexpected endpoint: {path}")

    @property
    def paths(self):
        return [request.url.path for request in self.requests]

    def json_for(self, path):
        return json.loads(next(request.content for request in self.requests if request.url.path == path))


async def invoke(api, payload, ctx, *, action="publish", **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
        result = await getattr(wechat, action)(payload, ctx, client=client, **kwargs)
        assert not client.is_closed  # The caller owns injected clients.
        return result


def test_draft_uploads_referenced_images_once_and_never_submits(setup):
    payload, ctx, events = setup
    payload = replace(payload, body_html='<p>Before</p><img src="easy-asset://image-1" alt="a &quot;quote&quot;"><p>After</p><img src="easy-asset://image-1" />',
                      images={"image-1": payload.cover_path}, author="Fixture author", description="Fixture summary")
    api = OfficialAPI(events)
    result = asyncio.run(invoke(api, payload, ctx))
    assert result.status == "draft_created"
    assert result.remote_id == "fixture-draft-id"
    assert "尚未正式发布" in result.message
    assert api.paths == ["/cgi-bin/stable_token", "/cgi-bin/media/uploadimg", "/cgi-bin/material/add_material", "/cgi-bin/draft/add"]
    assert "submitting" not in api.timeline[0][1]
    assert all("submitting" in names for _, names in api.timeline[1:])
    article = api.json_for("/cgi-bin/draft/add")["articles"][0]
    assert article["thumb_media_id"] == "fixture-cover-id"
    assert article["author"] == "Fixture author"
    assert article["digest"] == "Fixture summary"
    assert "easy-asset://" not in article["content"]
    assert 'alt="a &quot;quote&quot;"' in article["content"]
    assert article["content"].count('src="https://mmbiz.qpic.cn/fixture?x=1&amp;caption=&quot;quoted&quot;"') == 2
    assert "<p>Before</p>" in article["content"]
    assert "<p>After</p>" in article["content"]


@pytest.mark.parametrize("changes", [
    {"kind": "video"}, {"mode": "invalid"}, {"cover_path": None}, {"title": " "},
    {"title": "x" * 65}, {"body_html": " "}, {"description": "x" * 121},
    {"credentials": {}}, {"body_html": '<img src="easy-asset://missing">'},
    {"body_html": '<img src="https://example.test/image.jpg">'},
    {"body_html": '<img src="file:///private/image.jpg">'},
    {"body_html": '<img src="easy-asset://../secret">'},
    {"body_html": '<img src="easy-asset://image-1" src="https://example.test/b.jpg">'},
])
def test_invalid_content_fails_before_any_request(setup, changes):
    payload, ctx, events = setup
    api = OfficialAPI(events)
    result = asyncio.run(invoke(api, replace(payload, **changes), ctx))
    assert result.status == "failed"
    assert not api.requests
    assert not events


def test_invalid_cover_bytes_and_symlinks_are_rejected(setup, tmp_path):
    payload, ctx, events = setup
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    link = tmp_path / "link.png"
    link.symlink_to(payload.cover_path)
    for path in (bad, link, tmp_path / "missing.png"):
        api = OfficialAPI(events)
        result = asyncio.run(invoke(api, replace(payload, cover_path=path), ctx))
        assert result.status == "failed"
        assert not api.requests


def test_connection_check_does_not_claim_publish_rights(setup):
    payload, ctx, events = setup
    api = OfficialAPI(events)
    result = asyncio.run(invoke(api, payload, ctx, action="check"))
    assert result.status == "ready"
    assert "仍需" in result.message
    assert api.paths == ["/cgi-bin/stable_token"]
    assert not any(e["event"] == "submitting" for e in events)


def test_tokens_are_reused_only_for_same_app_and_secret(setup):
    payload, ctx, events = setup
    api = OfficialAPI(events)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            for credentials in [payload.credentials, payload.credentials,
                                {"appId": "second-app", "appSecret": "fixture-secret"},
                                {"appId": "fixture-app", "appSecret": "changed-secret"}]:
                result = await wechat.publish(replace(payload, credentials=credentials), ctx, client=client)
                assert result.status == "draft_created"
    asyncio.run(exercise())
    credentials = [json.loads(r.content) for r in api.requests if r.url.path == "/cgi-bin/stable_token"]
    assert len(credentials) == 3
    assert [(item["appid"], item["secret"]) for item in credentials] == [
        ("fixture-app", "fixture-secret"), ("second-app", "fixture-secret"), ("fixture-app", "changed-secret")]


def test_publish_requires_platform_confirmation_and_preserves_ids(setup):
    payload, ctx, events = setup
    api = OfficialAPI(events, statuses=[1, 0])
    result = asyncio.run(invoke(api, replace(payload, mode="publish"), ctx, poll_attempts=3, poll_interval=0))
    assert result.status == "published"
    assert result.remote_id == "fixture-publish-id"
    assert "fixture-draft-id" in result.message
    assert "不包含向关注者群发" in result.message
    assert result.url == "https://mp.weixin.qq.com/s/fixture"
    assert api.paths.count("/cgi-bin/freepublish/submit") == 1
    assert api.paths.count("/cgi-bin/freepublish/get") == 2
    assert not any("mass" in path for path in api.paths)


@pytest.mark.parametrize("status", [1, 42, True, "0", None])
def test_pending_or_malformed_status_remains_submitted(setup, status):
    payload, ctx, events = setup
    api = OfficialAPI(events, statuses=[status, status])
    result = asyncio.run(invoke(api, replace(payload, mode="publish"), ctx, poll_attempts=2, poll_interval=0))
    assert result.status == "submitted"
    assert result.remote_id == "fixture-publish-id"
    assert api.paths.count("/cgi-bin/freepublish/submit") == 1
    assert api.paths.count("/cgi-bin/freepublish/get") == (2 if type(status) is int and status == 1 else 1)


@pytest.mark.parametrize("status", [2, 3, 4, 5, 6])
def test_platform_confirmed_failed_publication_retains_receipt(setup, status):
    payload, ctx, events = setup
    api = OfficialAPI(events, statuses=[status])
    result = asyncio.run(invoke(api, replace(payload, mode="publish"), ctx, poll_interval=0))
    assert result.status == "failed"
    assert result.remote_id == "fixture-publish-id"
    assert "fixture-draft-id" in result.message


@pytest.mark.parametrize("endpoint,expected,remote_id", [
    ("/cgi-bin/stable_token", "failed", None),
    ("/cgi-bin/material/add_material", "unknown", None),
    ("/cgi-bin/draft/add", "unknown", None),
    ("/cgi-bin/freepublish/submit", "unknown", "fixture-draft-id"),
    ("/cgi-bin/freepublish/get", "submitted", "fixture-publish-id"),
])
def test_transport_ambiguity_never_retries_and_never_leaks_secret(setup, endpoint, expected, remote_id):
    payload, ctx, events = setup
    api = OfficialAPI(events, overrides={endpoint: httpx.ReadTimeout("fixture-secret https://api.weixin.qq.com/?access_token=fixture-access-token")})
    result = asyncio.run(invoke(api, replace(payload, mode="publish"), ctx, poll_interval=0))
    assert result.status == expected
    assert result.remote_id == remote_id
    assert api.paths.count(endpoint) == 1
    public = result.message + json.dumps(events)
    assert "fixture-secret" not in public
    assert "fixture-access-token" not in public
    assert "https://api.weixin.qq.com" not in public


@pytest.mark.parametrize("code,message,status", [(40164, "IP 白名单", "needs_attention"), (48001, "接口权限", "needs_attention"),
                                                (40125, "AppSecret", "login_required"), (40013, "AppID", "login_required")])
def test_authentication_errors_are_actionable_and_sanitized(setup, code, message, status):
    payload, ctx, events = setup
    api = OfficialAPI(events, overrides={"/cgi-bin/stable_token": {"errcode": code, "errmsg": "secret fixture-secret leaked at https://private.test/?token=fixture"}})
    result = asyncio.run(invoke(api, payload, ctx, action="login"))
    assert result.status == status
    assert message in result.message
    assert "fixture-secret" not in result.message
    assert "private.test" not in result.message
    assert api.paths == ["/cgi-bin/stable_token"]


def test_publish_permission_rejection_retains_created_draft(setup):
    payload, ctx, events = setup
    api = OfficialAPI(events, overrides={"/cgi-bin/freepublish/submit": {"errcode": 48001, "errmsg": "ignored remote text"}})
    result = asyncio.run(invoke(api, replace(payload, mode="publish"), ctx))
    assert result.status == "needs_attention"
    assert result.remote_id == "fixture-draft-id"
    assert "草稿已保存" in result.message
    assert "接口权限" in result.message
    assert api.paths.count("/cgi-bin/freepublish/submit") == 1
    assert "/cgi-bin/freepublish/get" not in api.paths


@pytest.mark.parametrize("response", [{}, {"media_id": ""}, {"errcode": "0"}, {"errcode": True},
                                      httpx.Response(502, text="fixture-secret"), httpx.Response(200, text="not JSON fixture-secret"),
                                      httpx.Response(200, json=["not a dictionary"])])
def test_malformed_draft_ack_is_unknown_not_success(setup, response):
    payload, ctx, events = setup
    api = OfficialAPI(events, overrides={"/cgi-bin/draft/add": response})
    result = asyncio.run(invoke(api, payload, ctx))
    assert result.status == "unknown"
    assert result.remote_id is None
    assert "fixture-secret" not in result.message
    assert api.paths.count("/cgi-bin/draft/add") == 1


def test_invalidated_token_is_evicted_without_retrying_post(setup):
    payload, ctx, events = setup
    api = OfficialAPI(events, overrides={"/cgi-bin/draft/add": {"errcode": 42001}})
    result = asyncio.run(invoke(api, payload, ctx))
    assert result.status == "failed"
    assert not wechat._TOKEN_CACHE
    assert api.paths.count("/cgi-bin/stable_token") == 1
    assert api.paths.count("/cgi-bin/draft/add") == 1


@pytest.mark.parametrize("url", ["https://mmbiz.qpic.cn.evil.test/image", "javascript:bad", "https://user:secret@mmbiz.qpic.cn/image", "https://mmbiz.qpic.cn:bad/image"])
def test_untrusted_image_response_never_reaches_draft(setup, url):
    payload, ctx, events = setup
    payload = replace(payload, body_html='<img src="easy-asset://image-1">', images={"image-1": payload.cover_path})
    api = OfficialAPI(events, overrides={"/cgi-bin/media/uploadimg": {"url": url}})
    result = asyncio.run(invoke(api, payload, ctx))
    assert result.status == "unknown"
    assert "/cgi-bin/draft/add" not in api.paths
    assert url not in result.message
