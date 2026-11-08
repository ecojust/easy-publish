"""Live manual browser handoffs are unfinished, bounded and account-exclusive."""
import asyncio
import threading
import sys

import pytest

from easy_publish.content import ContentInput
from easy_publish.runtime import Runtime
from easy_publish.service import Problem, Service
from test_runtime import TEST_ENV
from test_core import FakeRuntime
from easy_publish.store import Store, now, uid
from test_core import api, completed_batch, create_content, ready_account, request_batch, wait_state


def tasks(state):
    return [task for batch in state["batches"] for task in batch["tasks"]]


def test_manual_handoff_is_durable_busy_and_only_receipt_finishes_it(api):
    client, runtime, app = api
    account = ready_account(client)
    content = create_content(client)

    async def manual(payload, on_event, job_id):
        await on_event({"event": "submitting", "message": "fixture upload"})
        await on_event({"event": "awaiting_user", "message": "请在网页中手动发布"})
        row = app.state.service.store.one("SELECT * FROM tasks WHERE id=?", (job_id,))
        assert row["status"] == "awaiting_user" and row["fence"] == 1 and row["finished"] is None
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        return {"status": "submitted", "message": "fixture user submission receipt"}

    runtime.publish_hook = manual
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200
    state = wait_state(client, lambda s: any(t["status"] == "awaiting_user" for t in tasks(s)))
    task = tasks(state)[0]
    assert not task["canRetry"] and task["finishedAt"] is None
    assert state["contents"][0]["platformPublicationStates"] == {}
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
    assert client.post(f"/api/accounts/{account['id']}/login").status_code == 409
    assert client.delete(f"/api/accounts/{account['id']}").status_code == 409
    runtime.release.set()
    finished = completed_batch(client, response.json()["id"])["tasks"][0]
    assert finished["status"] == "submitted" and finished["finishedAt"]


def test_manual_wait_advances_other_accounts_with_three_job_limit_and_no_overlap(api):
    client, runtime, app = api
    accounts = [ready_account(client) for _ in range(4)]
    content = create_content(client)
    releases = {account["id"]: threading.Event() for account in accounts}
    active = set()
    overlap = []

    async def manual(payload, on_event, job_id):
        account_id = payload["account_id"]
        if account_id in active:
            overlap.append(account_id)
        active.add(account_id)
        try:
            await on_event({"event": "submitting", "message": "fixture upload"})
            await on_event({"event": "awaiting_user", "message": "fixture manual handoff"})
            while not releases[account_id].is_set():
                await asyncio.sleep(0.01)
            return {"status": "submitted", "message": "fixture manual receipt"}
        finally:
            active.discard(account_id)

    runtime.publish_hook = manual
    first = client.post("/api/batches", json=request_batch(content, [accounts[0]])).json()
    wait_state(client, lambda s: any(t["status"] == "awaiting_user" for t in tasks(s)))
    # This earlier queued task shares the waiting account. It must not block
    # unrelated accounts or run concurrently with its existing browser.
    same_account = client.post("/api/batches", json=request_batch(content, [accounts[0]])).json()
    others = client.post("/api/batches", json=request_batch(content, accounts[1:])).json()
    state = wait_state(client, lambda s: sum(t["status"] == "awaiting_user" for t in tasks(s)) == 3)
    assert len(runtime.publish_calls) == 3
    assert len(app.state.service.publishing_jobs) == 3
    assert next(b for b in state["batches"] if b["id"] == same_account["id"])["tasks"][0]["status"] == "queued"
    assert sum(t["status"] == "queued" for t in tasks(state)) == 2
    releases[accounts[1]["id"]].set()
    state = wait_state(client, lambda s: any(t["accountId"] == accounts[3]["id"] and t["status"] == "awaiting_user" for t in tasks(s)))
    assert len(runtime.publish_calls) == 4
    assert len(app.state.service.publishing_jobs) <= 3 and not overlap
    for release in releases.values():
        release.set()
    for batch in (first, same_account, others):
        assert all(t["status"] == "submitted" for t in completed_batch(client, batch["id"])["tasks"])
    assert len(runtime.publish_calls) == 5 and not overlap


def test_preparation_stays_serial_until_manual_handoff(api):
    client, runtime, _ = api
    accounts = [ready_account(client) for _ in range(2)]
    content = create_content(client)
    prepared = threading.Event()

    async def manual(payload, on_event, job_id):
        await on_event({"event": "submitting", "message": "fixture uploading"})
        if payload["account_id"] == accounts[0]["id"]:
            runtime.started.set()
            while not prepared.is_set():
                await asyncio.sleep(0.01)
        await on_event({"event": "awaiting_user", "message": "fixture handoff"})
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        return {"status": "submitted", "message": "fixture receipt"}

    runtime.publish_hook = manual
    batch = client.post("/api/batches", json=request_batch(content, accounts)).json()
    assert runtime.started.wait(2)
    state = client.get("/api/state").json()
    assert [t["status"] for t in tasks(state)] == ["submitting", "queued"]
    assert len(runtime.publish_calls) == 1
    prepared.set()
    wait_state(client, lambda s: sum(t["status"] == "awaiting_user" for t in tasks(s)) == 2)
    runtime.release.set()
    completed_batch(client, batch["id"])


def test_cancelling_manual_wait_stays_unknown_and_cannot_retry(api):
    client, runtime, _ = api
    account = ready_account(client)
    content = create_content(client)

    async def manual(payload, on_event, job_id):
        await on_event({"event": "submitting", "message": "fixture upload"})
        await on_event({"event": "awaiting_user", "message": "fixture handoff"})
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        return {"status": "failed", "message": "fixture browser closed"}

    runtime.publish_hook = manual
    batch = client.post("/api/batches", json=request_batch(content, [account])).json()
    wait_state(client, lambda s: any(t["status"] == "awaiting_user" for t in tasks(s)))
    assert client.post(f"/api/batches/{batch['id']}/cancel").status_code == 200
    task = completed_batch(client, batch["id"])["tasks"][0]
    assert task["status"] == "unknown" and not task["canRetry"]
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
    assert not runtime.cancelled
    assert len(runtime.publish_calls) == 1


def test_restart_recovers_manual_wait_as_unknown_without_requeue(tmp_path):
    store = Store(tmp_path)
    identifier = uid()
    store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message,fence,started) VALUES (?,?,?,?,?,'awaiting_user',?,1,?)",
                  (identifier, "batch", "account", "Fixture", "zhihu", "fixture handoff", now()))
    store.close()
    reopened = Store(tmp_path)
    try:
        reopened.recover()
        task = reopened.one("SELECT * FROM tasks WHERE id=?", (identifier,))
        assert task["status"] == "unknown" and task["fence"] == 1
        assert task["finished"] and not reopened.task(task)["canRetry"]
        assert not reopened.all("SELECT * FROM tasks WHERE status='queued'")
    finally:
        reopened.close()


async def wait_for_async(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_cancel_before_first_worker_turn_does_not_poison_first_retry(tmp_path, monkeypatch):
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)
    runtime = Runtime(tmp_path)
    runtime.browser_path = "/fixture/browser"
    marker = tmp_path / "executed"
    script = """
import json, pathlib, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'event':'submitting'}), flush=True)
assert json.loads(sys.stdin.readline()) == {'ack':'submitting'}
pathlib.Path(sys.argv[1]).write_text('one publication')
print(json.dumps({'event':'result','status':'submitted','message':'fixture receipt'}), flush=True)
"""
    monkeypatch.setattr(runtime, "_worker_command", lambda: [sys.executable, "-c", script, str(marker)])
    service = Service(tmp_path, runtime)
    try:
        account = service.add_account("zhihu", "Fixture", None)
        service.store.execute("UPDATE accounts SET status='ready' WHERE id=?", (account["id"],))
        runtime.vault.put(account["id"], "session", b'{"cookies":[],"origins":[]}')
        content = service.save_content(ContentInput(kind="article", title="Fixture article", body="Fixture Markdown"))
        batch = service.create_batch(content["id"], [account["id"]], "publish", uid())
        identifier = batch["tasks"][0]["id"]
        row = service.store.one("SELECT * FROM tasks WHERE id=?", (identifier,))
        service.store.execute("UPDATE tasks SET status='running' WHERE id=?", (identifier,))
        service.publishing_accounts[identifier] = account["id"]
        job = asyncio.create_task(service.run_task(row))
        service.publishing_jobs[identifier] = job
        job.add_done_callback(lambda finished: service._publishing_done(identifier, finished))
        # No event-loop turn between reservation and cancellation.
        await service.cancel_batch(batch["id"])
        assert job.cancelled() and not marker.exists()
        assert not service.publishing_jobs and not service.publishing_accounts
        assert not runtime._cancelled
        cancelled = service.store.one("SELECT * FROM tasks WHERE id=?", (identifier,))
        assert cancelled["status"] == "cancelled" and service.store.task(cancelled)["canRetry"]
        service.retry_task(identifier)
        service.queue_task = asyncio.create_task(service.queue_loop())
        await wait_for_async(lambda: service.store.one("SELECT status FROM tasks WHERE id=?", (identifier,))["status"] == "submitted")
        assert marker.read_text() == "one publication"
        assert not runtime._cancelled
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_retry_cannot_requeue_a_cancelled_task_until_cleanup_finishes(tmp_path):
    runtime = FakeRuntime()
    entered, cleaning, cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    service = Service(tmp_path, runtime)
    await service.start()

    async def delayed_cleanup(payload, on_event, job_id):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await cleanup.wait()

    runtime.publish_hook = delayed_cleanup
    try:
        account = service.add_account("zhihu", "Fixture", None)
        service.store.execute("UPDATE accounts SET status='ready' WHERE id=?", (account["id"],))
        runtime.vault.put(account["id"], "session", b'{"cookies":[],"origins":[]}')
        content = service.save_content(ContentInput(kind="article", title="Fixture article", body="Fixture Markdown"))
        batch = service.create_batch(content["id"], [account["id"]], "publish", uid())
        identifier = batch["tasks"][0]["id"]
        await asyncio.wait_for(entered.wait(), 3)
        cancellation = asyncio.create_task(service.cancel_batch(batch["id"]))
        await asyncio.wait_for(cleaning.wait(), 3)
        row = service.store.one("SELECT * FROM tasks WHERE id=?", (identifier,))
        assert row["status"] == "cancelled" and row["fence"] == 0
        # Even a duplicate cancel request cannot make the old owner disappear.
        await service.cancel_batch(batch["id"])
        with pytest.raises(Problem) as blocked:
            service.retry_task(identifier)
        assert blocked.value.status == 409
        assert service.busy_account(account["id"])
        cancellation.cancel()  # A disconnected API caller cannot abort cleanup.
        await asyncio.gather(cancellation, return_exceptions=True)
        assert not service.publishing_jobs[identifier].done()
        runtime.publish_hook = None
        cleanup.set()
        await wait_for_async(lambda: identifier not in service.publishing_jobs)
        service.retry_task(identifier)
        await wait_for_async(lambda: service.store.one("SELECT status FROM tasks WHERE id=?", (identifier,))["status"] == "published")
        assert len(runtime.publish_calls) == 2
    finally:
        cleanup.set()
        await service.close()
