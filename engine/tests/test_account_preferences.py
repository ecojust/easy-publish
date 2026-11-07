"""Account default selection is durable and never starts or changes a publication."""
from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from easy_publish.app import create_app
from easy_publish.store import Store
from test_core import (TOKEN, FakeRuntime, api, completed_batch, create_content,
                       ready_account, request_batch, wait_state)


def preference_path(identifier):
    return f"/api/accounts/{identifier}/preferences"


def test_new_accounts_default_to_selected_without_starting_a_connection(api):
    client, runtime, application = api
    response = client.post("/api/accounts", json={"platformId": "douyin", "label": "New account"})
    assert response.status_code == 200
    account = response.json()
    assert account["defaultSelected"] is True
    assert account["status"] == "login_required"
    assert runtime.publish_calls == []
    assert application.state.service.jobs == {}


def test_version_two_database_defaults_existing_accounts_without_changing_them(tmp_path):
    directory = tmp_path / "old"
    directory.mkdir()
    db = sqlite3.connect(directory / "library.sqlite3")
    db.executescript("""
        CREATE TABLE accounts(id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, updated TEXT NOT NULL, configured INTEGER NOT NULL);
        INSERT INTO accounts VALUES ('old', 'zhihu', 'Preserved account', 'ready', 'Preserved connection', 'before', 0);
        PRAGMA user_version=2;
    """)
    db.commit()
    db.close()
    store = Store(directory)
    try:
        account = store.account(store.one("SELECT * FROM accounts WHERE id='old'"))
        assert account["defaultSelected"] is True
        assert account["status"] == "ready"
        assert account["message"] == "Preserved connection"
        assert account["updatedAt"] == "before"
        assert store.one("PRAGMA user_version")["user_version"] == 3
        assert store.all("SELECT * FROM account_preferences") == []
    finally:
        store.close()


def test_preference_persists_restart_and_reconnection(tmp_path):
    directory = tmp_path / "data"
    first = create_app(directory, TOKEN, runtime=FakeRuntime())
    with TestClient(first, headers={"Authorization": "Bearer " + TOKEN}) as client:
        identifier = ready_account(client)["id"]
        before = next(a for a in client.get("/api/state").json()["accounts"] if a["id"] == identifier)
        response = client.put(preference_path(identifier), json={"defaultSelected": False})
        assert response.status_code == 200
        assert response.json() == {**before, "defaultSelected": False}
    second = create_app(directory, TOKEN, runtime=FakeRuntime())
    with TestClient(second, headers={"Authorization": "Bearer " + TOKEN}) as client:
        assert client.get("/api/state").json()["accounts"][0]["defaultSelected"] is False
        assert client.post(f"/api/accounts/{identifier}/login").status_code == 200
        state = wait_state(client, lambda state: state["accounts"][0]["status"] == "ready")
        assert state["accounts"][0]["defaultSelected"] is False
        response = client.put(preference_path(identifier), json={"defaultSelected": True})
        assert response.json()["defaultSelected"] is True
        assert len(second.state.service.store.all("SELECT * FROM account_preferences")) == 1


def test_toggle_during_active_task_changes_only_preference(api):
    client, runtime, application = api
    identifier = ready_account(client)["id"]
    store = application.state.service.store
    store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message) VALUES (?,?,?,?,?,?,?)",
                  ("active", "batch", identifier, "Fixture", "zhihu", "running", "Still publishing"))
    account_before = store.one("SELECT * FROM accounts WHERE id=?", (identifier,))
    task_before = store.one("SELECT * FROM tasks WHERE id='active'")
    saved_session = runtime.vault.get(identifier, "session")
    assert client.put(preference_path(identifier), json={"defaultSelected": False}).status_code == 200
    assert store.one("SELECT * FROM accounts WHERE id=?", (identifier,)) == account_before
    assert store.one("SELECT * FROM tasks WHERE id='active'") == task_before
    assert runtime.vault.get(identifier, "session") == saved_session
    assert runtime.publish_calls == []
    assert runtime.cancelled == set()


def test_manual_selection_can_publish_an_account_with_default_disabled(api):
    client, runtime, _ = api
    account = ready_account(client)
    content = create_content(client)
    assert client.put(preference_path(account["id"]), json={"defaultSelected": False}).status_code == 200
    selection = {"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"}
    assert client.post("/api/preflight", json=selection).json()["ok"] is True
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200
    batch = completed_batch(client, response.json()["id"])
    assert batch["tasks"][0]["status"] == "published"
    assert len(runtime.publish_calls) == 1
    assert client.get("/api/state").json()["accounts"][0]["defaultSelected"] is False


def test_delete_account_removes_preference(api):
    client, _, application = api
    account = ready_account(client)
    identifier = account["id"]
    assert client.put(preference_path(identifier), json={"defaultSelected": False}).status_code == 200
    assert client.delete(f"/api/accounts/{identifier}").status_code == 200
    assert application.state.service.store.all("SELECT * FROM account_preferences") == []


@pytest.mark.parametrize("payload", [{}, {"defaultSelected": None}, {"defaultSelected": 0}, {"defaultSelected": 1},
                                     {"defaultSelected": "false"}, {"defaultSelected": "true"},
                                     {"defaultSelected": []}, {"defaultSelected": False, "platformId": "douyin"}])
def test_preferences_require_a_boolean_and_reject_other_fields(api, payload):
    client, _, application = api
    identifier = ready_account(client)["id"]
    response = client.put(preference_path(identifier), json=payload)
    assert response.status_code == 422
    assert application.state.service.store.all("SELECT * FROM account_preferences") == []
    assert client.get("/api/state").json()["accounts"][0]["defaultSelected"] is True


def test_preferences_require_auth_and_an_existing_account(api):
    client, _, application = api
    identifier = ready_account(client)["id"]
    assert client.put(preference_path(identifier), json={"defaultSelected": False},
                      headers={"Authorization": ""}).status_code == 401
    assert client.put(preference_path(str(uuid4())), json={"defaultSelected": False}).status_code == 404
    assert application.state.service.store.all("SELECT * FROM account_preferences") == []
