"""Upgrade account state without opening the old OS-protected credential store."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from easy_publish.app import create_app
from easy_publish.store import Store, dump
from easy_publish.vault import Vault
from test_core import TOKEN, FakeRuntime, content_input, ready_account, wait_state


class LocalVaultRuntime(FakeRuntime):
    """Actual on-disk storage with a network-free account worker."""

    def __init__(self, directory):
        super().__init__()
        self.vault = Vault(directory)
        self.connect_calls = []

    def state(self):
        return {**super().state(), "vaultReady": self.vault.ready, "vaultMessage": self.vault.message}

    async def run(self, operation, payload, on_event, job_id):
        if operation in {"login", "check"}:
            self.connect_calls.append(operation)
        return await super().run(operation, payload, on_event, job_id)


@pytest.fixture
def directory(tmp_path, monkeypatch):
    monkeypatch.delenv("EASY_PUBLISH_TEST_KEY", raising=False)
    monkeypatch.delenv("EASY_PUBLISH_TESTING", raising=False)
    return tmp_path / "data"


def legacy_account(directory, platform="bilibili"):
    identifier = str(uuid4())
    kind = "credentials" if platform == "wechat" else "session"
    store = Store(directory)
    store.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
                  (identifier, platform, "Preserved account", "ready", "Original account message", "before", int(platform == "wechat")))
    if platform == "bilibili":
        store.execute("INSERT INTO account_metadata VALUES (?,?)",
                      (identifier, dump([{"value": "171", "label": "知识"}])))
    store.close()
    legacy = directory / "vault"
    legacy.mkdir(exist_ok=True)
    filename = hashlib.sha256((identifier + "\0" + kind).encode()).hexdigest() + ".enc"
    location = legacy / filename
    location.write_bytes(b"EPV1 unreadable legacy ciphertext fixture")
    return identifier, location


def client_for(directory, runtime):
    return TestClient(create_app(directory, TOKEN, runtime=runtime), headers={"Authorization": "Bearer " + TOKEN})


def test_upgrade_preserves_library_history_and_legacy_data_without_resuming_work(directory):
    browser, old_session = legacy_account(directory)
    wechat, old_credentials = legacy_account(directory, "wechat")
    old_bytes = {path: path.read_bytes() for path in (old_session, old_credentials)}
    store = Store(directory)
    store.execute("INSERT INTO contents VALUES (?,?,?,?,?)", ("content", dump(content_input()), "before", "before", 3))
    store.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)",
                  ("batch", str(uuid4()), "content", "Preserved article", "article", "publish", "before", "{}", "[]"))
    for identifier, status, fence in [("done", "published", 1), ("pending", "queued", 0), ("mutated", "submitting", 1)]:
        store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message,fence) VALUES (?,?,?,?,?,?,?,?)",
                      (identifier, "batch", browser, "Preserved account", "bilibili", status, "Original task", fence))
    store.close()
    runtime = LocalVaultRuntime(directory)
    with client_for(directory, runtime) as client:
        state = client.get("/api/state").json()
        accounts = {account["id"]: account for account in state["accounts"]}
        assert set(accounts) == {browser, wechat}
        assert all(account["status"] == "login_required" for account in accounts.values())
        assert "重新登录" in accounts[browser]["message"]
        assert accounts[browser]["bilibiliCategories"] == []
        assert accounts[wechat]["credentialConfigured"] is False
        assert "重新添加 AppID 和 AppSecret" in accounts[wechat]["message"]
        assert state["contents"][0]["id"] == "content"
        assert state["contents"][0]["revision"] == 3
        tasks = {task["id"]: task["status"] for task in state["batches"][0]["tasks"]}
        assert tasks == {"done": "published", "pending": "cancelled", "mutated": "unknown"}
        assert runtime.connect_calls == runtime.publish_calls == []
        assert "unreadable legacy ciphertext" not in json.dumps(state)
    assert {path: path.read_bytes() for path in old_bytes} == old_bytes
    store = Store(directory)
    assert store.all("SELECT * FROM account_metadata") == []
    store.close()


def test_repeated_startup_keeps_migration_message_and_timestamp(directory):
    identifier, _ = legacy_account(directory)
    with client_for(directory, LocalVaultRuntime(directory)) as client:
        first = client.get("/api/state").json()["accounts"][0]
    with client_for(directory, LocalVaultRuntime(directory)) as client:
        second = client.get("/api/state").json()["accounts"][0]
    assert first == second
    assert second["id"] == identifier


def test_browser_reconnect_is_saved_locally_and_survives_restart(directory):
    identifier, old_session = legacy_account(directory, "douyin")
    original = old_session.read_bytes()
    runtime = LocalVaultRuntime(directory)
    with client_for(directory, runtime) as client:
        assert client.post(f"/api/accounts/{identifier}/login").status_code == 200
        state = wait_state(client, lambda state: state["accounts"][0]["status"] == "ready")
        account = state["accounts"][0]
        assert runtime.connect_calls == ["login"]
        assert runtime.vault.get(identifier, "session") is not None
    with client_for(directory, LocalVaultRuntime(directory)) as client:
        assert client.get("/api/state").json()["accounts"][0] == account
    assert old_session.read_bytes() == original


@pytest.mark.parametrize("operation", ["login", "check"])
def test_legacy_wechat_requires_reentry_and_new_credentials_work(directory, operation):
    identifier, old_credentials = legacy_account(directory, "wechat")
    original = old_credentials.read_bytes()
    runtime = LocalVaultRuntime(directory)
    with client_for(directory, runtime) as client:
        response = client.post(f"/api/accounts/{identifier}/{operation}")
        assert response.status_code == 400
        assert response.json()["code"] == "credentials_required"
        assert "重新添加 AppID 和 AppSecret" in response.json()["error"]
        assert runtime.connect_calls == []
        assert client.delete(f"/api/accounts/{identifier}").status_code == 200
        added = ready_account(client, "wechat", credentials={"appId": "fixture-id", "appSecret": "fixture-secret"})
        assert added["credentialConfigured"] is True
        assert "fixture-secret" not in client.get("/api/state").text
    with client_for(directory, LocalVaultRuntime(directory)) as client:
        account = client.get("/api/state").json()["accounts"][0]
        assert account["id"] == added["id"]
        assert account["status"] == "ready"
        assert account["credentialConfigured"] is True
    assert old_credentials.read_bytes() == original


def test_existing_local_credentials_override_legacy_migration(directory):
    identifier, old_credentials = legacy_account(directory, "wechat")
    runtime = LocalVaultRuntime(directory)
    runtime.vault.put(identifier, "credentials", b'{"appId":"fixture","appSecret":"private"}')
    with client_for(directory, runtime) as client:
        account = client.get("/api/state").json()["accounts"][0]
        assert account["status"] == "ready"
        assert account["credentialConfigured"] is True
        assert account["updatedAt"] == "before"
        assert old_credentials.is_file()


def test_unavailable_local_vault_does_not_rewrite_accounts(directory):
    identifier, old_session = legacy_account(directory)
    runtime = LocalVaultRuntime(directory)
    # A failed vault initialization is global; do not mislabel existing accounts.
    runtime.vault.ready = False
    runtime.vault.message = "应用内账号存储无法读取"
    with client_for(directory, runtime) as client:
        state = client.get("/api/state").json()
        account = state["accounts"][0]
        assert account["id"] == identifier
        assert account["status"] == "ready"
        assert account["updatedAt"] == "before"
        assert state["runtime"]["vaultReady"] is False
        response = client.post(f"/api/accounts/{identifier}/login")
        assert response.status_code == 503
        assert response.json()["code"] == "vault_unavailable"
        assert runtime.connect_calls == []
    assert old_session.is_file()
