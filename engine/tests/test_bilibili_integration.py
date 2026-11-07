"""Bilibili catalog, account capability persistence and queue integration fixtures."""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import sqlite3
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from easy_publish.app import create_app
from easy_publish.catalog import BY_ID, bilibili_categories
from easy_publish.runtime import Runtime
from easy_publish.store import Store
from test_core import (TOKEN, FakeRuntime, completed_batch, content_input,
                       create_content, ready_account, request_batch, wait_state)


CATEGORIES = [{"value": "171", "label": "知识 / 科学科普"}, {"value": "201", "label": "生活 / 生活记录"}]


class BilibiliRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.categories = copy.deepcopy(CATEGORIES)
        self.connect_status = "ready"

    async def run(self, operation, payload, on_event, job_id):
        if operation in {"login", "check"}:
            return {"status": self.connect_status, "message": "fixture account response", "metadata": {
                "bilibiliCategories": [dict(category, private="fixture-private-value") for category in self.categories],
                "cookies": "fixture-cookie-never-public", "csrf": "fixture-token-never-public"}}
        self.publish_calls.append(copy.deepcopy(payload))
        if self.publish_hook:
            return await self.publish_hook(payload, on_event, job_id)
        await on_event({"event": "submitting", "message": "fixture upload starts"})
        return {"status": "submitted", "message": "fixture awaiting review", "remote_id": "BV1xx411c7mD", "url": "https://www.bilibili.com/video/BV1xx411c7mD"}


@pytest.fixture
def bili_api(tmp_path):
    runtime = BilibiliRuntime()
    application = create_app(tmp_path / "data", TOKEN, runtime=runtime)
    with TestClient(application, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client, runtime, application


def video_content(client, tmp_path, **changes):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42fixture-video")
    response = client.post("/api/assets/import", json={"path": str(video), "kind": "video"})
    assert response.status_code == 200, response.text
    values = dict(kind="video", title="Fixture Bilibili video", body="", description="A fixture video", tags=["知识"],
                  videoAssetId=response.json()["id"], platformOptions={"bilibili": {"copyright": "1", "tid": "171", "source": ""}})
    values.update(changes)
    return create_content(client, **values)


def preflight(client, content, accounts, mode="publish"):
    response = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [a["id"] for a in accounts], "mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def test_catalog_exposes_video_only_with_account_categories():
    platform = BY_ID["bilibili"]
    assert platform["kinds"] == ["video"]
    assert platform["modes"] == ["publish"]
    assert platform["auth"] == "browser"
    assert platform["titleLimit"] == 80
    fields = {field["key"]: field for field in platform["fields"]}
    assert fields["tid"]["dynamicChoices"] == "bilibiliCategories"
    assert not fields["tid"].get("choices")
    assert fields["copyright"]["choices"] == [{"value": "1", "label": "原创"}, {"value": "2", "label": "转载"}]


def test_ready_account_categories_are_public_and_secret_metadata_is_discarded(bili_api):
    client, _, application = bili_api
    account = ready_account(client, "bilibili")
    state = client.get("/api/state").json()
    current = next(a for a in state["accounts"] if a["id"] == account["id"])
    assert current["bilibiliCategories"] == CATEGORIES
    stored = application.state.service.store.all("SELECT * FROM account_metadata")
    assert json.loads(stored[0]["bilibili_categories"]) == CATEGORIES
    for private in ("fixture-private-value", "fixture-cookie-never-public", "fixture-token-never-public"):
        assert private not in json.dumps(state)
        assert private not in json.dumps(stored)


def test_categories_persist_after_restart_and_are_removed_with_account(tmp_path):
    directory = tmp_path / "data"
    with TestClient(create_app(directory, TOKEN, runtime=BilibiliRuntime()), headers={"Authorization": "Bearer " + TOKEN}) as client:
        account = ready_account(client, "bilibili")
    application = create_app(directory, TOKEN, runtime=BilibiliRuntime())
    with TestClient(application, headers={"Authorization": "Bearer " + TOKEN}) as client:
        state = client.get("/api/state").json()
        assert state["accounts"][0]["bilibiliCategories"] == CATEGORIES
        assert client.delete(f"/api/accounts/{account['id']}").status_code == 200
        assert application.state.service.store.all("SELECT * FROM account_metadata") == []


def test_expired_verification_clears_categories_and_success_refreshes_them(bili_api):
    client, runtime, application = bili_api
    account = ready_account(client, "bilibili")
    runtime.connect_status = "login_required"
    assert client.post(f"/api/accounts/{account['id']}/check").status_code == 200
    state = wait_state(client, lambda s: s["accounts"][0]["status"] == "expired")
    assert state["accounts"][0]["bilibiliCategories"] == []
    assert application.state.service.store.all("SELECT * FROM account_metadata") == []
    runtime.connect_status = "ready"
    runtime.categories = [CATEGORIES[1]]
    assert client.post(f"/api/accounts/{account['id']}/login").status_code == 200
    state = wait_state(client, lambda s: s["accounts"][0]["status"] == "ready")
    assert state["accounts"][0]["bilibiliCategories"] == [CATEGORIES[1]]


def test_version_one_database_gets_additive_metadata_table(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    db = sqlite3.connect(directory / "library.sqlite3")
    db.executescript("""
        CREATE TABLE accounts(id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, updated TEXT NOT NULL, configured INTEGER NOT NULL);
        INSERT INTO accounts VALUES ('old', 'zhihu', 'Existing account', 'ready', 'existing session', 'before', 0);
        PRAGMA user_version=1;
    """)
    db.commit()
    db.close()
    store = Store(directory)
    try:
        account = store.account(store.one("SELECT * FROM accounts WHERE id='old'"))
        assert account["label"] == "Existing account" and account["status"] == "ready"
        assert "bilibiliCategories" not in account
        assert store.all("SELECT * FROM account_metadata") == []
        assert store.one("PRAGMA user_version")["user_version"] == 6
        assert account["defaultSelected"] is True
    finally:
        store.close()


def test_preflight_uses_each_selected_accounts_current_categories(bili_api, tmp_path):
    client, runtime, _ = bili_api
    first = ready_account(client, "bilibili")
    runtime.categories = [CATEGORIES[1]]
    second = ready_account(client, "bilibili")
    content = video_content(client, tmp_path)
    result = preflight(client, content, [first, second])
    assert result["ok"] is False
    assert result["targets"][0]["errors"] == []
    assert any("分区" in error for error in result["targets"][1]["errors"])
    response = client.post("/api/batches", json=request_batch(content, [first, second]))
    assert response.status_code == 400
    assert runtime.publish_calls == []


@pytest.mark.parametrize("changes, expected", [
    ({"title": "标" * 81}, "80"),
    ({"description": "介" * 2001}, "2000"),
    ({"tags": []}, "1–12"),
    ({"tags": [str(i) for i in range(13)]}, "1–12"),
    ({"tags": ["话" * 21]}, "20"),
    ({"tags": ["a,b"]}, "逗号"),
    ({"tags": ["a，b"]}, "逗号"),
    ({"tags": ["a\nb"]}, "换行"),
    ({"tags": ["a", " a "]}, "重复"),
    ({"platformOptions": {"bilibili": {"copyright": "3", "tid": "171"}}}, "创作类型"),
    ({"platformOptions": {"bilibili": {"copyright": "1", "tid": "9999"}}}, "分区"),
    ({"platformOptions": {"bilibili": {"copyright": "2", "tid": "171"}}}, "链接"),
    ({"platformOptions": {"bilibili": {"copyright": "2", "tid": "171", "source": "http://[invalid"}}}, "链接"),
    ({"platformOptions": {"bilibili": {"copyright": "2", "tid": "171", "source": "https://name:password@example.test/video"}}}, "链接"),
])
def test_invalid_video_metadata_is_rejected_before_queue(bili_api, tmp_path, changes, expected):
    client, runtime, _ = bili_api
    account = ready_account(client, "bilibili")
    content = video_content(client, tmp_path, **changes)
    result = preflight(client, content, [account])
    assert not result["ok"]
    assert any(expected in error for error in result["targets"][0]["errors"])
    assert runtime.publish_calls == []


def test_article_and_remote_draft_are_not_bilibili_capabilities(bili_api, tmp_path):
    client, _, _ = bili_api
    account = ready_account(client, "bilibili")
    article = create_content(client)
    result = preflight(client, article, [account])
    assert any("内容类型" in e for e in result["targets"][0]["errors"])
    video = video_content(client, tmp_path)
    result = preflight(client, video, [account], mode="draft")
    assert any("草稿" in e for e in result["targets"][0]["errors"])


def test_large_cover_is_rejected_in_preflight(bili_api, tmp_path):
    from PIL import Image
    client, _, _ = bili_api
    account = ready_account(client, "bilibili")
    image = tmp_path / "cover.png"
    Image.new("RGB", (2, 2)).save(image)
    with image.open("ab") as output:
        output.write(b"x" * (5 * 1024**2))
    response = client.post("/api/assets/import", json={"path": str(image), "kind": "image"})
    assert response.status_code == 200, response.text
    video = video_content(client, tmp_path, coverAssetId=response.json()["id"])
    result = preflight(client, video, [account])
    assert any("5 MiB" in e for e in result["targets"][0]["errors"])


def test_valid_reprint_options_are_frozen_and_receipt_is_submitted(bili_api, tmp_path):
    client, runtime, _ = bili_api
    account = ready_account(client, "bilibili")
    options = {"copyright": "2", "tid": "171", "source": "https://example.test/original-video"}
    video = video_content(client, tmp_path, platformOptions={"bilibili": options}, tags=[str(i) for i in range(12)])
    assert preflight(client, video, [account])["ok"]

    async def pause(payload, on_event, job_id):
        runtime.started.set()
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        await on_event({"event": "submitting", "message": "fixture upload"})
        return {"status": "submitted", "message": "fixture awaiting review", "remote_id": "123456"}

    runtime.publish_hook = pause
    response = client.post("/api/batches", json=request_batch(video, [account]))
    assert response.status_code == 200, response.text
    assert runtime.started.wait(2)
    changed = content_input(kind="video", title="Changed", tags=["new"], videoAssetId=video["videoAssetId"], platformOptions={"bilibili": {"copyright": "1", "tid": "201"}})
    assert client.put(f"/api/contents/{video['id']}", json=changed).status_code == 200
    runtime.release.set()
    batch = completed_batch(client, response.json()["id"])
    task = batch["tasks"][0]
    assert task["status"] == "submitted" and not task["canRetry"]
    assert runtime.publish_calls[0]["options"] == options
    assert runtime.publish_calls[0]["tags"] == [str(i) for i in range(12)]


def test_expired_session_during_publish_removes_public_categories(bili_api, tmp_path):
    client, runtime, application = bili_api
    account = ready_account(client, "bilibili")
    video = video_content(client, tmp_path)

    async def expired(payload, on_event, job_id):
        return {"status": "login_required", "message": "fixture session expired"}

    runtime.publish_hook = expired
    response = client.post("/api/batches", json=request_batch(video, [account]))
    assert response.status_code == 200
    completed_batch(client, response.json()["id"])
    current = client.get("/api/state").json()["accounts"][0]
    assert current["status"] == "expired" and current["bilibiliCategories"] == []
    assert application.state.service.store.all("SELECT * FROM account_metadata") == []


def test_category_metadata_shape_is_bounded_and_strips_unknown_fields():
    assert bilibili_categories(None) == []
    assert bilibili_categories([CATEGORIES[0]] * 1001) == []
    assert bilibili_categories([
        {"value": "171", "label": " 科学科普 ", "cookie": "private"},
        {"value": "171", "label": "duplicate"}, {"value": "-1", "label": "bad"},
        {"value": "65536", "label": "bad"}, {"value": "12", "label": "bad\nlabel"},
        {"value": 1, "label": "bad"}, {"value": "1", "label": "x" * 161}, "bad",
    ]) == [{"value": "171", "label": "科学科普"}]


@pytest.mark.parametrize("platform, operation, status, allowed", [
    ("bilibili", "login", "ready", True), ("bilibili", "check", "ready", True),
    ("bilibili", "publish", "submitted", False), ("bilibili", "check", "login_required", False),
    ("douyin", "check", "ready", False),
])
def test_runtime_forwards_only_public_account_capabilities(tmp_path, monkeypatch, platform, operation, status, allowed):
    monkeypatch.setenv("EASY_PUBLISH_TESTING", "1")
    monkeypatch.setenv("EASY_PUBLISH_TEST_KEY", base64.b64encode(b"b" * 32).decode())
    event = {"event": "result", "status": status, "message": "fixture result", "metadata": {
        "bilibiliCategories": [dict(CATEGORIES[0], cookie="private-cookie")], "csrf": "private-csrf"}}
    script = "import sys; sys.stdin.readline(); print(" + repr(json.dumps(event)) + ", flush=True)"

    async def run():
        runtime = Runtime(tmp_path / "runtime")
        async def progress(event):
            pass
        try:
            with patch.object(runtime, "_worker_command", return_value=[sys.executable, "-c", script]):
                return await runtime.run(operation, {"platform_id": platform, "account_id": "fixture"}, progress, "metadata-test")
        finally:
            await runtime.shutdown()
    result = asyncio.run(run())
    if allowed:
        assert result["metadata"] == {"bilibiliCategories": [CATEGORIES[0]]}
    else:
        assert "metadata" not in result
    assert "private-cookie" not in json.dumps(result)
    assert "private-csrf" not in json.dumps(result)


def test_supervised_worker_dispatches_bilibili_without_browser_or_session(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_PUBLISH_TESTING", "1")
    monkeypatch.setenv("EASY_PUBLISH_TEST_KEY", base64.b64encode(b"b" * 32).decode())
    async def run():
        runtime = Runtime(tmp_path / "runtime")
        async def progress(event):
            pass
        try:
            return await runtime.run("check", {"platform_id": "bilibili", "account_id": "fixture", "kind": "video"}, progress, "dispatch-test")
        finally:
            await runtime.shutdown()
    result = asyncio.run(run())
    assert result["status"] == "login_required"
    assert "哔哩哔哩" in result["message"]
