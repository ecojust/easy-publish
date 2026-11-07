"""Account connection cancellation, ownership races and real worker cleanup."""
from __future__ import annotations

import asyncio
import sys

import pytest

from easy_publish.runtime import Runtime
from easy_publish.service import Problem, Service
from test_core import FakeRuntime, api, ready_account


class WaitingRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.allow_cleanup = asyncio.Event()
        self.cleaned = False
        self.cancellation_count = 0

    async def run(self, operation, payload, on_event, job_id):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellation_count += 1
            raise
        finally:
            self.cleaning.set()
            await self.allow_cleanup.wait()
            self.cleaned = True

    async def shutdown(self):
        self.allow_cleanup.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["login", "check"])
async def test_cancel_waits_for_cleanup_keeps_ownership_and_is_idempotent(tmp_path, operation):
    runtime = WaitingRuntime()
    service = Service(tmp_path, runtime)
    await service.start()
    try:
        account = service.add_account("douyin", "Fixture account", None)
        identifier = account["id"]
        previous_session = b'{"cookies":[],"origins":[]}'
        runtime.vault.put(identifier, "session", previous_session)
        await service.connect_account(identifier, operation)
        await asyncio.wait_for(runtime.entered.wait(), 2)
        first = asyncio.create_task(service.cancel_account(identifier))
        await asyncio.wait_for(runtime.cleaning.wait(), 2)
        second = asyncio.create_task(service.cancel_account(identifier))
        assert service.busy_account(identifier)
        for retry_operation in ("login", "check"):
            with pytest.raises(Problem) as error:
                await service.connect_account(identifier, retry_operation)
            assert error.value.code == "account_busy"
        with pytest.raises(Problem) as error:
            service.delete_account(identifier)
        assert error.value.code == "account_busy"
        # A request disconnect does not interrupt the shared cleanup operation.
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert not runtime.cleaned
        assert not second.done()
        runtime.allow_cleanup.set()
        await asyncio.wait_for(second, 2)
        assert runtime.cleaned
        assert runtime.cancellation_count == 1
        assert not service.busy_account(identifier)
        assert not service.jobs and not service.account_cancellations
        assert service.account_row(identifier)["status"] == "login_required"
        assert service.account_row(identifier)["message"] == "连接已取消"
        assert runtime.vault.get(identifier, "session") == previous_session
        assert runtime.cancelled == set()
        # A second cancellation after completion is a harmless no-op.
        unchanged = service.account_row(identifier)
        await service.cancel_account(identifier)
        assert service.account_row(identifier) == unchanged
    finally:
        runtime.allow_cleanup.set()
        await service.close()


@pytest.mark.asyncio
async def test_cancellation_before_connect_task_first_turn_clears_busy_entry(tmp_path):
    runtime = WaitingRuntime()
    service = Service(tmp_path, runtime)
    try:
        identifier = service.add_account("xiaohongshu", "Fixture account", None)["id"]
        await service.connect_account(identifier, "login")
        job = service.jobs[identifier]
        # Run the owner inline before the scheduled worker receives a loop turn.
        await service._cancel_account_job(identifier, job)
        assert not runtime.entered.is_set()
        assert job.cancelled()
        assert not service.busy_account(identifier)
        assert service.account_row(identifier)["message"] == "连接已取消"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_already_completed_connection_is_not_downgraded(tmp_path):
    service = Service(tmp_path, FakeRuntime())
    try:
        identifier = service.add_account("douyin", "Fixture account", None)["id"]
        await service.connect_account(identifier, "login")
        job = service.jobs[identifier]
        await job
        ready = service.account_row(identifier)
        assert ready["status"] == "ready"
        # Models completion between accepting cancel and scheduling its cleanup.
        await service._cancel_account_job(identifier, job)
        await service.cancel_account(identifier)
        assert service.account_row(identifier) == ready
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_shutdown_during_cancellation_finishes_existing_cleanup(tmp_path):
    runtime = WaitingRuntime()
    service = Service(tmp_path, runtime)
    await service.start()
    identifier = service.add_account("channels", "Fixture account", None)["id"]
    await service.connect_account(identifier, "login")
    await asyncio.wait_for(runtime.entered.wait(), 2)
    cancellation = asyncio.create_task(service.cancel_account(identifier))
    await asyncio.wait_for(runtime.cleaning.wait(), 2)
    await service.close()
    await cancellation
    assert runtime.cleaned
    assert runtime.cancellation_count == 1
    assert not service.jobs and not service.account_cancellations


def test_cancel_endpoint_requires_auth_and_never_cancels_publication(api):
    client, runtime, application = api
    account = ready_account(client, "douyin")
    identifier = account["id"]
    store = application.state.service.store
    store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message) VALUES (?,?,?,?,?,?,?)",
                  ("publishing", "batch", identifier, "Fixture", "douyin", "running", "Still publishing"))
    previous_account = application.state.service.account_row(identifier)
    response = client.post(f"/api/accounts/{identifier}/cancel", headers={"Authorization": ""})
    assert response.status_code == 401
    assert client.post(f"/api/accounts/{identifier}/cancel").json() == {"ok": True}
    assert application.state.service.account_row(identifier) == previous_account
    assert store.one("SELECT status FROM tasks WHERE id='publishing'")["status"] == "running"
    assert runtime.cancelled == set()
    assert client.post("/api/accounts/missing/cancel").status_code == 404


@pytest.mark.asyncio
async def test_cancel_stops_real_owned_worker_and_allows_next_connection(tmp_path):
    runtime = Runtime(tmp_path)
    runtime.browser_path = "/fixture/browser"
    runtime._worker_command = lambda: [sys.executable, "-c", """
import json, sys, time
json.loads(sys.stdin.readline())
print(json.dumps({'event':'progress','message':'Fixture waiting'}), flush=True)
time.sleep(300)
"""]
    service = Service(tmp_path, runtime)
    await service.start()
    try:
        identifier = service.add_account("douyin", "Fixture account", None)["id"]
        previous_session = b'{"cookies":[],"origins":[]}'
        runtime.vault.put(identifier, "session", previous_session)
        await service.connect_account(identifier, "login")
        async with asyncio.timeout(10):
            while service.account_row(identifier)["message"] != "Fixture waiting":
                await asyncio.sleep(0.01)
        process = runtime._jobs["account-" + identifier]
        await asyncio.wait_for(service.cancel_account(identifier), 15)
        assert process.returncode is not None
        assert not runtime._jobs and not runtime._cancelled
        assert list((tmp_path / "work").iterdir()) == []
        assert runtime.vault.get(identifier, "session") == previous_session
        runtime._worker_command = lambda: [sys.executable, "-c", """
import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({'event':'result','status':'ready','message':'Fixture reconnected'}), flush=True)
"""]
        await service.connect_account(identifier, "login")
        await asyncio.wait_for(service.jobs[identifier], 15)
        assert service.account_row(identifier)["status"] == "ready"
        assert service.account_row(identifier)["message"] == "Fixture reconnected"
    finally:
        await service.close()
