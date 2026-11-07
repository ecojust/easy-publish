"""Local API/queue integration with a controlled worker; never contacts a platform."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from easy_publish.app import create_app


TOKEN = "fixture-token-not-a-real-credential"


class FakeVault:
    ready = True
    message = "fixture vault"

    def __init__(self):
        self.data = {}

    def put(self, account_id, kind, data):
        self.data[account_id, kind] = data

    def get(self, account_id, kind):
        return self.data.get((account_id, kind))

    def delete(self, account_id):
        self.data = {key: value for key, value in self.data.items() if key[0] != account_id}

    def requires_reconnect(self, account_id, kind):
        return False


class FakeRuntime:
    """An injectable deterministic worker, including controlled mutation fences."""
    browser_path = "/fixture/browser"

    def __init__(self):
        self.vault = FakeVault()
        self.publish_calls = []
        self.publish_hook = None
        self.cancelled = set()
        self.release = threading.Event()
        self.started = threading.Event()

    def state(self):
        return dict(browserReady=True, browserInstalling=False, browserMessage="fixture",
                    vaultReady=True, vaultMessage="fixture", version="test")

    async def run(self, operation, payload, on_event, job_id):
        if operation in {"login", "check"}:
            self.vault.put(payload["account_id"], "session", b'{"cookies":[],"origins":[]}')
            return dict(status="ready", message="fixture logged in", remote_id=None, url=None)
        self.publish_calls.append(copy.deepcopy(payload))
        if self.publish_hook:
            return await self.publish_hook(payload, on_event, job_id)
        await on_event({"event": "submitting", "message": "fixture submitting"})
        return dict(status="published", message="fixture receipt", remote_id="fixture-123", url=None)

    async def cancel(self, job_id):
        self.cancelled.add(job_id)
        self.release.set()

    async def shutdown(self):
        self.release.set()

    async def install_browser(self):
        return None


@pytest.fixture
def api(tmp_path):
    runtime = FakeRuntime()
    application = create_app(tmp_path / "data", TOKEN, runtime=runtime)
    with TestClient(application, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client, runtime, application


def content_input(**changes):
    data = dict(kind="article", title="Fixture article", body="First version of the article.", description="",
                tags=[], author="", videoAssetId=None, coverAssetId=None, imageAssetIds=[], platformOptions={})
    data.update(changes)
    return data


def create_content(client, **changes):
    response = client.post("/api/contents", json=content_input(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def wait_state(client, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/api/state")
        assert response.status_code == 200, response.text
        state = response.json()
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError("fixture operation did not reach its expected state")


def ready_account(client, platform="zhihu", **extra):
    response = client.post("/api/accounts", json={"platformId": platform, "label": "Fixture " + platform, **extra})
    assert response.status_code == 200, response.text
    account = response.json()
    assert client.post(f"/api/accounts/{account['id']}/login").status_code == 200
    wait_state(client, lambda state: any(a["id"] == account["id"] and a["status"] == "ready" for a in state["accounts"]))
    return account


def request_batch(content, accounts, **changes):
    return dict(contentId=content["id"], accountIds=[a["id"] for a in accounts], mode="publish", requestId=str(uuid4()), **changes)


def completed_batch(client, batch_id):
    terminal = {"published", "submitted", "draft_created", "failed", "cancelled", "unknown", "needs_attention"}
    state = wait_state(client, lambda s: any(b["id"] == batch_id and all(t["status"] in terminal for t in b["tasks"]) for b in s["batches"]))
    return next(b for b in state["batches"] if b["id"] == batch_id)


def test_api_requires_bearer_for_reads_and_writes(api):
    client, _, _ = api
    for method, path, body in [("GET", "/api/state", None), ("POST", "/api/contents", content_input())]:
        for token in ("", "Bearer incorrect"):
            response = client.request(method, path, headers={"Authorization": token}, json=body)
            assert response.status_code in {401, 403}
    assert client.get("/api/state").status_code == 200


def test_authenticated_browser_origin_is_rejected(api):
    client, _, _ = api
    assert client.get("/api/state", headers={"Origin": "https://untrusted.example"}).status_code == 403


def test_validation_errors_do_not_echo_credential_input(api):
    client, _, _ = api
    secret = "fixture-secret-in-invalid-input"
    response = client.post("/api/accounts", json={"platformId": "wechat", "label": "Fixture", "credentials": [secret]})
    assert response.status_code == 422
    assert secret not in response.text


def test_credentials_do_not_enter_state_or_sqlite(api):
    client, runtime, app = api
    secret = "fixture-secret-never-in-public-state"
    account = ready_account(client, "wechat", credentials={"appId": "fixture-app-id", "appSecret": secret})
    state = client.get("/api/state").json()
    assert secret not in json.dumps(state)
    assert "fixture-app-id" not in json.dumps(state)
    assert account["credentialConfigured"] is True
    rows = app.state.service.store.all("SELECT * FROM accounts")
    assert secret not in json.dumps(rows)
    assert any(secret.encode() in data for data in runtime.vault.data.values())


def test_imported_asset_is_copied_and_public_response_hides_path(api, tmp_path):
    from PIL import Image
    client, _, app = api
    source = tmp_path / "cover.png"
    Image.new("RGB", (3, 3)).save(source)
    original = source.read_bytes()
    response = client.post("/api/assets/import", json={"path": str(source), "kind": "image"})
    assert response.status_code == 200, response.text
    asset = response.json()
    assert "path" not in asset
    row = app.state.service.store.one("SELECT * FROM assets WHERE id=?", (asset["id"],))
    source.write_bytes(b"changed original")
    assert Path(row["path"]).read_bytes() == original
    assert client.get(f"/api/assets/{asset['id']}/preview").json()["dataUrl"].startswith("data:image/")


@pytest.mark.parametrize("body", ['![x](/etc/passwd)', '![x](relative-image.png)', '![x](https://example.test/image.png)', '![x](easy-asset://missing)'])
def test_preflight_rejects_unowned_image_references(api, body):
    client, _, _ = api
    account = ready_account(client)
    content = create_content(client, body=body)
    result = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"})
    assert result.status_code == 200, result.text
    assert result.json()["ok"] is False
    assert result.json()["targets"][0]["errors"]


def test_raw_html_is_literal_text_in_preview_and_publication(api):
    from easy_publish.content import image_sources, render_article
    client, _, _ = api
    source = '<img src="/etc/passwd">visible literal markup'
    rendered = render_article(source)
    assert "&lt;img" in rendered
    assert "<img" not in rendered
    assert image_sources(rendered) == []
    account = ready_account(client)
    content = create_content(client, body=source)
    response = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


def test_preflight_rejects_wrong_content_kind_and_missing_declarations(api):
    client, _, _ = api
    video = ready_account(client, "douyin")
    dayu = ready_account(client, "dayu")
    content = create_content(client)
    result = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [video["id"], dayu["id"]], "mode": "publish"}).json()
    assert result["ok"] is False
    assert all(target["errors"] for target in result["targets"])


def test_malformed_reprint_url_is_a_preflight_error(api):
    client, _, _ = api
    account = ready_account(client, "dayu")
    content = create_content(client, platformOptions={"dayu": {"source": "转载", "source_url": "http://[invalid"}})
    response = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert response.json()["targets"][0]["errors"]


def test_batch_is_idempotent_and_uses_immutable_content_snapshot(api):
    client, runtime, _ = api
    first = ready_account(client)
    second = ready_account(client)
    content = create_content(client)

    async def pause_first(payload, on_event, job_id):
        if len(runtime.publish_calls) == 1:
            runtime.started.set()
            while not runtime.release.is_set():
                await asyncio.sleep(0.01)
        await on_event({"event": "submitting", "message": "fixture"})
        return {"status": "published", "message": "fixture receipt"}

    runtime.publish_hook = pause_first
    request = request_batch(content, [first, second])
    response = client.post("/api/batches", json=request)
    assert response.status_code == 200, response.text
    batch = response.json()
    assert runtime.started.wait(2)
    again = client.post("/api/batches", json=request)
    assert again.status_code == 200
    assert again.json()["id"] == batch["id"]
    conflict = client.post("/api/batches", json={**request, "accountIds": [first["id"]]})
    assert conflict.status_code == 409
    updated = client.put(f"/api/contents/{content['id']}", json=content_input(title="Changed title", body="Changed body"))
    assert updated.status_code == 200
    runtime.release.set()
    completed_batch(client, batch["id"])
    assert len(runtime.publish_calls) == 2
    assert all(p["title"] == content["title"] for p in runtime.publish_calls)
    assert all("First version" in p["body_html"] and "Changed body" not in p["body_html"] for p in runtime.publish_calls)


def test_failure_after_mutation_fence_is_unknown_and_cannot_retry(api):
    client, runtime, app = api
    account = ready_account(client)
    content = create_content(client)

    async def fail_after_fence(payload, on_event, job_id):
        await on_event({"event": "submitting", "message": "fixture mutation starts"})
        row = app.state.service.store.one("SELECT * FROM tasks WHERE id=?", (job_id,))
        assert row["fence"] == 1
        raise RuntimeError("fixture lost acknowledgement")

    runtime.publish_hook = fail_after_fence
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200, response.text
    batch = completed_batch(client, response.json()["id"])
    task = batch["tasks"][0]
    assert task["status"] == "unknown"
    assert task["canRetry"] is False
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code in {400, 409, 422}
    assert len(runtime.publish_calls) == 1


def test_cancellation_after_mutation_fence_stays_unknown(api):
    client, runtime, _ = api
    account = ready_account(client)
    content = create_content(client)

    async def pause_after_fence(payload, on_event, job_id):
        await on_event({"event": "submitting", "message": "fixture mutation starts"})
        runtime.started.set()
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        return {"status": "failed", "message": "fixture interrupted worker"}

    runtime.publish_hook = pause_after_fence
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200, response.text
    assert runtime.started.wait(2)
    assert client.post(f"/api/batches/{response.json()['id']}/cancel").status_code == 200
    task = completed_batch(client, response.json()["id"])["tasks"][0]
    assert task["status"] == "unknown"
    assert task["canRetry"] is False
    assert len(runtime.publish_calls) == 1


def test_buffered_worker_event_after_cancel_does_not_stop_queue(api):
    client, runtime, _ = api
    account = ready_account(client)
    content = create_content(client)

    async def buffered_progress(payload, on_event, job_id):
        if len(runtime.publish_calls) == 1:
            await on_event({"event": "submitting", "message": "fixture mutation starts"})
            runtime.started.set()
            while not runtime.release.is_set():
                await asyncio.sleep(0.01)
            # The real worker can have written this before cancellation; the
            # supervisor receives the buffered pipe line after the DB update.
            await on_event({"event": "progress", "message": "buffered fixture progress"})
            return {"status": "unknown", "message": "fixture interrupted"}
        await on_event({"event": "submitting", "message": "next task"})
        return {"status": "published", "message": "fixture receipt"}

    runtime.publish_hook = buffered_progress
    first = client.post("/api/batches", json=request_batch(content, [account]))
    assert first.status_code == 200, first.text
    assert runtime.started.wait(2)
    second = client.post("/api/batches", json=request_batch(content, [account]))
    assert second.status_code == 200, second.text
    assert client.post(f"/api/batches/{first.json()['id']}/cancel").status_code == 200
    next_batch = completed_batch(client, second.json()["id"])
    assert next_batch["tasks"][0]["status"] == "published"
    assert len(runtime.publish_calls) == 2


def test_clearly_unsubmitted_failure_can_retry_without_republishing_success(api):
    client, runtime, _ = api
    first = ready_account(client)
    second = ready_account(client)
    content = create_content(client)
    failed_once = False

    async def fail_second_once(payload, on_event, job_id):
        nonlocal failed_once
        if payload["account_id"] == second["id"] and not failed_once:
            failed_once = True
            return {"status": "failed", "message": "fixture failure before mutation"}
        await on_event({"event": "submitting", "message": "fixture"})
        return {"status": "published", "message": "fixture receipt"}

    runtime.publish_hook = fail_second_once
    response = client.post("/api/batches", json=request_batch(content, [first, second]))
    assert response.status_code == 200, response.text
    batch = completed_batch(client, response.json()["id"])
    task = next(t for t in batch["tasks"] if t["accountId"] == second["id"])
    assert task["status"] == "failed" and task["canRetry"]
    retry = client.post(f"/api/tasks/{task['id']}/retry")
    assert retry.status_code == 200, retry.text
    completed_batch(client, batch["id"])
    assert [p["account_id"] for p in runtime.publish_calls].count(first["id"]) == 1
    assert [p["account_id"] for p in runtime.publish_calls].count(second["id"]) == 2


def test_crash_recovery_never_requeues_incomplete_work(tmp_path):
    from easy_publish.store import Store
    store = Store(tmp_path / "recovery")
    try:
        for identifier, status, fence in [("mutated", "submitting", 1), ("queued", "queued", 0), ("started", "running", 0)]:
            store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message,fence) VALUES(?,?,?,?,?,?,?,?)",
                          (identifier, "fixture-batch", "fixture-account", "Fixture", "zhihu", status, "fixture", fence))
        store.recover()
        rows = {row["id"]: row for row in store.all("SELECT * FROM tasks")}
        assert rows["mutated"]["status"] == "unknown"
        assert Store.task(rows["mutated"])["canRetry"] is False
        assert rows["queued"]["status"] == rows["started"]["status"] == "cancelled"
        assert all(row["finished"] for row in rows.values())
    finally:
        store.close()


def test_engine_startup_recovers_before_starting_queue(tmp_path):
    from easy_publish.store import Store
    directory = tmp_path / "startup"
    store = Store(directory)
    store.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)",
                  ("batch", str(uuid4()), "content", "Fixture", "article", "publish", "now", "{}", "[]"))
    for identifier, status, fence in [("mutated", "submitting", 1), ("queued", "queued", 0), ("started", "running", 0)]:
        store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message,fence) VALUES(?,?,?,?,?,?,?,?)",
                      (identifier, "batch", "account", "Fixture", "zhihu", status, "fixture", fence))
    store.close()
    runtime = FakeRuntime()
    with TestClient(create_app(directory, TOKEN, runtime=runtime), headers={"Authorization": "Bearer " + TOKEN}) as client:
        state = client.get("/api/state").json()
        statuses = {task["id"]: task["status"] for task in state["batches"][0]["tasks"]}
        assert statuses == {"mutated": "unknown", "queued": "cancelled", "started": "cancelled"}
        assert runtime.publish_calls == []


def test_instance_lock_prevents_concurrent_engine_and_releases_on_close(tmp_path):
    from easy_publish.instance import InstanceLock
    first = InstanceLock(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="另一个"):
            InstanceLock(tmp_path)
    finally:
        first.close()
    # The persistent lock file is not an indication of a running engine; the
    # OS lock must be released so a subsequent normal launch can open it.
    second = InstanceLock(tmp_path)
    second.close()
