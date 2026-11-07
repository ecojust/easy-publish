"""Isolated plain-text conversion lifecycle; no external model or publication."""
import asyncio
import json
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from easy_publish.app import create_app
from easy_publish.content import ContentInput
from easy_publish.opencode_runtime import OpenCodeError
from test_core import TOKEN, FakeRuntime, create_content, ready_account


class FakeFormatter:
    def __init__(self):
        self.calls = []
        self.output = "## 整理结果\n\n保留文章内容。"
        self.fail = False
        self.safe_error = None
        self.block = False
        self.release = threading.Event()
        self.started = threading.Event()
        self.cleaned = threading.Event()
        self.closed = False
        self.checks = 0

    def state(self):
        return {"available": True, "version": "fixture", "modelId": "opencode/free-fixture",
                "modelName": "Free fixture", "free": True, "message": "fixture ready"}

    async def check(self):
        self.checks += 1
        if self.safe_error:
            raise OpenCodeError(self.safe_error)
        if self.fail:
            raise RuntimeError("private-credential-and-article-should-not-appear")
        return self.state()

    async def format(self, source):
        self.calls.append(source)
        self.started.set()
        try:
            while self.block and not self.release.is_set():
                await asyncio.sleep(0.01)
            if self.safe_error:
                raise OpenCodeError(self.safe_error)
            if self.fail:
                raise RuntimeError("private-credential-and-article-should-not-appear")
            return self.output
        finally:
            await asyncio.sleep(0.01)
            self.cleaned.set()

    async def close(self):
        self.closed = True


@pytest.fixture
def formatting_api(tmp_path):
    formatter, runtime = FakeFormatter(), FakeRuntime()
    app = create_app(tmp_path / "data", TOKEN, runtime=runtime, formatter=formatter)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client, formatter, app, runtime


def plain_content(client, **changes):
    return create_content(client, **{"inputFormat": "plain", "sourceText": "原文保留文章内容。", **changes})


def editable(content, **changes):
    value = {key: content[key] for key in ContentInput.model_fields if key in content}
    return {**value, **changes}


def start(client, content, request_id=None):
    return client.post(f"/api/contents/{content['id']}/format", json={"revision": content["revision"], "requestId": request_id or str(uuid4())})


def finished(client, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f"/api/format-jobs/{identifier}")
        assert result.status_code == 200, result.text
        if result.json()["status"] != "running":
            return result.json()
        time.sleep(0.01)
    raise AssertionError("Formatting fixture did not finish")


def current(client, content):
    return next(item for item in client.get("/api/state").json()["contents"] if item["id"] == content["id"])


def test_plain_save_preserves_original_and_cannot_inject_generated_markdown(formatting_api):
    client, formatter, _, runtime = formatting_api
    content = plain_content(client, body="Forged prepared body")
    assert content["sourceText"] == "原文保留文章内容。" and content["body"] == ""
    assert not content["formatReady"] and content["formatModel"] is None
    account = ready_account(client)
    response = client.post('/api/preflight', json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"})
    assert not response.json()["ok"] and '请先整理' in str(response.json())
    client.post('/api/preview/article', json={"body": content["body"]})
    assert formatter.calls == [] and runtime.publish_calls == []


def test_conversion_persists_separate_original_and_output_and_preflight_uses_ready(formatting_api):
    client, formatter, app, runtime = formatting_api
    content = plain_content(client)
    job = finished(client, start(client, content).json()["id"])
    result = job["content"]
    assert job["status"] == "succeeded" and result["formatReady"]
    assert result["sourceText"] == content["sourceText"] and result["body"] == formatter.output
    assert result["formatModel"] == "opencode/free-fixture" and result["revision"] == content["revision"] + 1
    assert "formatSourceHash" not in result
    assert json.loads(app.state.service.content_row(content["id"])["data"])["formatSourceHash"]
    account = ready_account(client)
    assert client.post('/api/preflight', json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"}).json()["ok"]
    assert runtime.publish_calls == []
    # Editing metadata retains prepared output but client-supplied Markdown is ignored.
    result = client.put(f"/api/contents/{content['id']}", json=editable(result, title="Metadata update", body="injected")).json()
    assert result["formatReady"] and result["body"] == formatter.output
    result = client.put(f"/api/contents/{content['id']}", json=editable(result, sourceText="新的原文", body=formatter.output)).json()
    assert not result["formatReady"] and result["body"] == ""
    assert result["sourceText"] == "新的原文" and result["formatModel"] is None


def test_same_request_replay_uses_original_job_and_rejects_different_revision(formatting_api):
    client, formatter, _, _ = formatting_api
    formatter.block = True
    content = plain_content(client)
    identifier = str(uuid4())
    first = start(client, content, identifier)
    assert first.status_code == 200
    assert formatter.started.wait(1)
    assert start(client, content, identifier).json()["id"] == identifier
    assert start(client, content).status_code == 409
    changed = {**content, "revision": content["revision"] + 1}
    assert start(client, changed, identifier).status_code == 409
    formatter.release.set()
    job = finished(client, identifier)
    assert start(client, content, identifier).json() == job
    assert formatter.calls == [content["sourceText"]]


@pytest.mark.parametrize('change', ['source', 'metadata', 'delete'])
def test_late_model_output_never_overwrites_modified_or_deleted_draft(formatting_api, change):
    client, formatter, _, _ = formatting_api
    formatter.block = True
    content = plain_content(client)
    job = start(client, content).json()
    assert formatter.started.wait(1)
    if change == 'delete':
        assert client.delete(f"/api/contents/{content['id']}").status_code == 200
    else:
        values = {"sourceText": "后来输入的新原文"} if change == 'source' else {"title": "后来编辑的标题"}
        assert client.put(f"/api/contents/{content['id']}", json=editable(content, **values)).status_code == 200
    formatter.release.set()
    result = finished(client, job["id"])
    assert result["status"] == "failed" and "未写入" in result["message"]
    state = client.get('/api/state').json()
    if change == 'delete':
        assert all(c['id'] != content['id'] for c in state['contents'])
    else:
        saved = current(client, content)
        assert saved['body'] == '' and all(saved[key] == value for key, value in values.items())


def test_cancel_waits_for_cleanup_and_preserves_previous_good_result(formatting_api):
    client, formatter, _, _ = formatting_api
    content = plain_content(client)
    good = finished(client, start(client, content).json()["id"])["content"]
    formatter.block = True
    formatter.cleaned.clear()
    formatter.started.clear()
    job = start(client, good).json()
    assert formatter.started.wait(1)
    response = client.post(f"/api/format-jobs/{job['id']}/cancel")
    assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    assert formatter.cleaned.is_set()
    assert client.post(f"/api/format-jobs/{job['id']}/cancel").json() == response.json()
    assert current(client, good) == good


def test_failure_and_check_errors_never_echo_provider_details(formatting_api, caplog):
    client, formatter, _, _ = formatting_api
    content = plain_content(client)
    good = finished(client, start(client, content).json()["id"])["content"]
    formatter.fail = True
    result = finished(client, start(client, good).json()["id"])
    assert result['status'] == 'failed' and '原文' in result['message']
    response = client.post('/api/formatting/check')
    assert response.status_code == 503
    assert 'private-credential' not in response.text + json.dumps(result) + caplog.text
    assert current(client, good) == good


def test_safe_runtime_errors_remain_actionable(formatting_api):
    client, formatter, _, _ = formatting_api
    content = plain_content(client)
    formatter.safe_error = "免费模型当前请求受限，请稍后重试；原文已保留。"
    result = finished(client, start(client, content).json()['id'])
    assert result['status'] == 'failed' and result['message'] == formatter.safe_error
    response = client.post('/api/formatting/check')
    assert response.status_code == 503 and formatter.safe_error in response.text
    assert current(client, content) == content


def image(client, tmp_path, name):
    path = tmp_path / (name + '.png')
    Image.new('RGB', (3, 3), 'blue').save(path)
    return client.post('/api/assets/import', json={'path': str(path), 'kind': 'image'}).json()['id']


def test_image_positions_map_to_local_assets_and_mapping_changes_invalidate(formatting_api, tmp_path):
    client, formatter, _, _ = formatting_api
    identifiers = [image(client, tmp_path, name) for name in ['first', 'second']]
    source = '第一段\n【图片1】\n第二段\n【图片2】'
    content = plain_content(client, sourceText=source, imageAssetIds=identifiers)
    formatter.output = '## 第一段\n【图片1】\n\n第二段\n【图片2】'
    result = finished(client, start(client, content).json()['id'])['content']
    assert result['sourceText'] == source
    assert result['body'].count('easy-asset://') == 2
    for index, identifier in enumerate(identifiers, 1):
        assert f'![正文图片 {index}](easy-asset://{identifier})' in result['body']
    result = client.put(f"/api/contents/{content['id']}", json=editable(result, imageAssetIds=list(reversed(identifiers)))).json()
    assert not result['formatReady'] and result['body'] == ''


def test_model_cannot_reorder_image_placeholders(formatting_api, tmp_path):
    client, formatter, _, _ = formatting_api
    identifiers = [image(client, tmp_path, name) for name in ['first', 'second']]
    content = plain_content(client, sourceText='第一段【图片1】第二段【图片2】', imageAssetIds=identifiers)
    formatter.output = '第一段【图片2】第二段【图片1】'
    result = finished(client, start(client, content).json()['id'])
    assert result['status'] == 'failed' and '图片位置' in result['message']
    assert current(client, content) == content


@pytest.mark.parametrize('output, message', [('', '为空'), ('原文无图', '图片位置'),
    ('【图片1】\n![bad](https://example.invalid/remote.png)', '未导入'),
    ('【图片1】【图片1】', '图片位置')])
def test_invalid_model_output_is_not_saved(formatting_api, tmp_path, output, message):
    client, formatter, _, _ = formatting_api
    asset = image(client, tmp_path, 'only')
    content = plain_content(client, sourceText='正文【图片1】', imageAssetIds=[asset])
    formatter.output = output
    result = finished(client, start(client, content).json()['id'])
    assert result['status'] == 'failed' and message in result['message']
    assert current(client, content)['body'] == ''


def test_legacy_markdown_remains_editable_without_formatting(formatting_api):
    client, formatter, _, _ = formatting_api
    content = create_content(client, body='## Agent authored Markdown')
    assert content['inputFormat'] == 'markdown' and content['formatReady']
    assert start(client, content).status_code == 400
    saved = client.put(f"/api/contents/{content['id']}", json=editable(content, body='Updated **Markdown**')).json()
    assert saved['body'] == 'Updated **Markdown**' and saved['formatReady']
    assert formatter.calls == []


def test_validation_auth_unknown_jobs_and_stale_revision(formatting_api):
    client, formatter, _, _ = formatting_api
    content = plain_content(client)
    assert start(client, {**content, 'revision': content['revision'] + 1}).status_code == 409
    assert start(client, {**content, 'revision': True}).status_code == 422
    assert client.post(f"/api/contents/{content['id']}/format", json={'revision': 1, 'requestId': str(uuid4())}, headers={'Authorization': ''}).status_code == 401
    assert client.get('/api/format-jobs/missing').status_code == 404
    assert client.post('/api/format-jobs/missing/cancel').status_code == 404
    assert client.post('/api/formatting/check', headers={'Authorization': ''}).status_code == 401
    assert client.post('/api/formatting/check').json()['free'] is True
    assert client.put(f"/api/contents/{content['id']}", json={**editable(content), 'formatSourceHash': 'forged'}).status_code == 422
    assert formatter.calls == []


def test_shutdown_cancels_active_conversion_and_restart_retains_finished_output(tmp_path):
    directory = tmp_path / 'data'
    formatter = FakeFormatter()
    app = create_app(directory, TOKEN, runtime=FakeRuntime(), formatter=formatter)
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        content = plain_content(client)
        good = finished(client, start(client, content).json()['id'])['content']
        formatter.block = True
        formatter.started.clear()
        formatter.cleaned.clear()
        start(client, good)
        assert formatter.started.wait(1)
    assert formatter.closed and formatter.cleaned.is_set()
    replacement = FakeFormatter()
    app = create_app(directory, TOKEN, runtime=FakeRuntime(), formatter=replacement)
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        assert current(client, good) == good
        assert replacement.calls == []
