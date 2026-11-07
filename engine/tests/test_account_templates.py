"""Per-account WeChat templates remain private preferences and immutable job inputs."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from easy_publish.app import create_app
from easy_publish.content import render_article
from easy_publish.service import Problem
from easy_publish.store import Store, dump
from test_core import TOKEN, FakeRuntime, api, completed_batch, create_content, ready_account, request_batch
from test_mcp_server import call, mcp_api, result_value


SOURCE = "## 同一篇文章\n\n正文与 **重点**。\n\n> 一段引用"


def wechat_account(client):
    return ready_account(client, "wechat", credentials={"appId": "fixture-app", "appSecret": "fixture-secret"})


def set_template(client, identifier, template):
    response = client.put(f"/api/accounts/{identifier}/preferences", json={"wechatTemplateId": template})
    assert response.status_code == 200, response.text
    return response.json()


def article_with_cover(client, tmp_path, **changes):
    image = tmp_path / "cover.png"
    Image.new("RGB", (3, 3), "green").save(image)
    response = client.post("/api/assets/import", json={"path": str(image), "kind": "image"})
    assert response.status_code == 200, response.text
    return create_content(client, **{"title": "足够长的测试文章", "body": SOURCE, "articleTemplateId": "journal",
                                    "coverAssetId": response.json()["id"], **changes})


def snapshot_text(app, batch_id):
    return app.state.service.store.one("SELECT snapshot FROM batches WHERE id=?", (batch_id,))["snapshot"]


def test_schema_three_migration_preserves_default_selection_and_account_state(tmp_path):
    directory = tmp_path / "old"
    directory.mkdir()
    db = sqlite3.connect(directory / "library.sqlite3")
    db.executescript("""
        CREATE TABLE accounts(id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, updated TEXT NOT NULL, configured INTEGER NOT NULL);
        CREATE TABLE account_preferences(account_id TEXT PRIMARY KEY, default_selected INTEGER NOT NULL DEFAULT 1 CHECK(default_selected IN (0,1)));
        INSERT INTO accounts VALUES ('wechat-old','wechat','Saved WeChat','ready','Saved connection','before',1);
        INSERT INTO accounts VALUES ('wechat-default','wechat','No preference','login_required','Waiting','before',1);
        INSERT INTO accounts VALUES ('zhihu-old','zhihu','Saved Zhihu','ready','Saved connection','before',0);
        INSERT INTO account_preferences VALUES ('wechat-old',0),('zhihu-old',0);
        PRAGMA user_version=3;
    """)
    db.commit()
    db.close()
    # Reopening twice also verifies ALTER is idempotent.
    for _ in range(2):
        store = Store(directory)
        try:
            accounts = {row["id"]: store.account(row) for row in store.all("SELECT * FROM accounts")}
            assert accounts["wechat-old"]["defaultSelected"] is False
            assert accounts["wechat-old"]["wechatTemplateId"] == "wenyan-default"
            assert accounts["wechat-old"]["status"] == "ready"
            assert accounts["wechat-old"]["message"] == "Saved connection"
            assert all(account["updatedAt"] == "before" for account in accounts.values())
            assert accounts["wechat-default"]["wechatTemplateId"] == "wenyan-default"
            assert accounts["wechat-default"]["defaultSelected"] is True
            assert accounts["zhihu-old"]["defaultSelected"] is False
            assert "wechatTemplateId" not in accounts["zhihu-old"]
            assert store.one("PRAGMA user_version")["user_version"] == 5
        finally:
            store.close()


def test_partial_preferences_preserve_omitted_fields_across_restart_and_delete(tmp_path):
    directory = tmp_path / "data"
    runtime = FakeRuntime()
    app = create_app(directory, TOKEN, runtime=runtime)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        account = wechat_account(client)
        identifier = account["id"]
        assert account["wechatTemplateId"] == "wenyan-default" and account["defaultSelected"] is True
        before = app.state.service.account_row(identifier)
        session = runtime.vault.get(identifier, "session")
        path = f"/api/accounts/{identifier}/preferences"
        assert client.put(path, json={"defaultSelected": False}).status_code == 200
        changed = set_template(client, identifier, "forest")
        assert changed["defaultSelected"] is False and changed["wechatTemplateId"] == "forest"
        changed = client.put(path, json={"defaultSelected": True}).json()
        assert changed["defaultSelected"] is True and changed["wechatTemplateId"] == "forest"
        changed = client.put(path, json={"defaultSelected": False, "wechatTemplateId": "artdeco"}).json()
        assert changed["defaultSelected"] is False and changed["wechatTemplateId"] == "artdeco"
        assert app.state.service.account_row(identifier) == before
        assert runtime.vault.get(identifier, "session") == session
        assert runtime.publish_calls == []
    app = create_app(directory, TOKEN, runtime=FakeRuntime())
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        account = client.get("/api/state").json()["accounts"][0]
        assert account["id"] == identifier
        assert account["defaultSelected"] is False and account["wechatTemplateId"] == "artdeco"
        assert client.delete(f"/api/accounts/{identifier}").status_code == 200
        assert app.state.service.store.all("SELECT * FROM account_preferences") == []


@pytest.mark.parametrize("payload", [
    {}, {"wechatTemplateId": None}, {"wechatTemplateId": "missing"}, {"wechatTemplateId": 1},
    {"wechatTemplateId": {"id": "clean"}}, {"wechatTemplateId": "forest", "defaultSelected": None},
])
def test_invalid_partial_preferences_leave_existing_settings_unchanged(api, payload):
    client, _, app = api
    identifier = wechat_account(client)["id"]
    set_template(client, identifier, "journal")
    before = app.state.service.store.all("SELECT * FROM account_preferences")
    response = client.put(f"/api/accounts/{identifier}/preferences", json=payload)
    assert response.status_code == 422
    assert app.state.service.store.all("SELECT * FROM account_preferences") == before


@pytest.mark.parametrize("platform", ["zhihu", "douyin"])
def test_non_wechat_template_change_rejects_the_whole_partial_update(api, platform):
    client, _, app = api
    account = ready_account(client, platform)
    assert "wechatTemplateId" not in account
    response = client.put(f"/api/accounts/{account['id']}/preferences",
                          json={"defaultSelected": False, "wechatTemplateId": "forest"})
    assert response.status_code == 400
    assert response.json()["code"] == "template_account_mismatch"
    assert app.state.service.store.account(app.state.service.account_row(account["id"]))["defaultSelected"] is True
    assert app.state.service.store.all("SELECT * FROM account_preferences") == []


def test_api_and_mcp_preview_use_current_account_preference_without_publishing(mcp_api):
    client, runtime, _ = mcp_api
    first = wechat_account(client)
    second = wechat_account(client)
    set_template(client, first["id"], "forest")
    set_template(client, second["id"], "artdeco")
    for account, template in [(first, "forest"), (second, "artdeco")]:
        request = {"body": SOURCE, "articleTemplateId": "tech", "accountId": account["id"]}
        desktop = client.post("/api/preview/article", json=request)
        assert desktop.status_code == 200
        mcp = result_value(call(client, "preview_article", request))
        assert desktop.json() == mcp
        assert mcp["html"] == render_article(SOURCE, template, "wechat")
        assert mcp["html"] != render_article(SOURCE, "tech", "wechat")
    set_template(client, first["id"], "editorial")
    refreshed = client.post("/api/preview/article", json={"body": SOURCE, "platformId": "wechat", "accountId": first["id"]})
    assert refreshed.json()["html"] == render_article(SOURCE, "editorial", "wechat")
    generic = client.post("/api/preview/article", json={"body": SOURCE, "platformId": "wechat", "articleTemplateId": "tech"})
    assert generic.json()["html"] == render_article(SOURCE, "tech", "wechat")
    accounts = result_value(call(client, "list_accounts", {"kind": "article"}))["accounts"]
    assert {account["id"]: account["wechatTemplateId"] for account in accounts} == {
        first["id"]: "editorial", second["id"]: "artdeco",
    }
    assert runtime.publish_calls == []


def test_account_preview_rejects_wrong_platform_and_missing_account(mcp_api):
    client, runtime, _ = mcp_api
    wechat = wechat_account(client)
    zhihu = ready_account(client, "zhihu")
    for account_id, platform, status in [(wechat["id"], "zhihu", 400), (zhihu["id"], "wechat", 400), (str(uuid4()), "wechat", 404)]:
        request = {"body": SOURCE, "accountId": account_id, "platformId": platform}
        assert client.post("/api/preview/article", json=request).status_code == status
        mcp = call(client, "preview_article", request)
        assert mcp["isError"]
        assert mcp["structuredContent"]["code"] == ("not_found" if status == 404 else "template_account_mismatch")
    assert runtime.publish_calls == []


def test_two_account_templates_are_frozen_for_queued_tasks_and_retry(api, tmp_path):
    client, runtime, app = api
    first, second = wechat_account(client), wechat_account(client)
    other = ready_account(client, "dayu")
    set_template(client, first["id"], "forest")
    set_template(client, second["id"], "artdeco")
    content = article_with_cover(client, tmp_path, platformOptions={"dayu": {"source": "个人观点"}})
    failed_once = False

    async def controlled(payload, on_event, job_id):
        nonlocal failed_once
        if len(runtime.publish_calls) == 1:
            runtime.started.set()
            while not runtime.release.is_set():
                await asyncio.sleep(0.01)
        if payload["account_id"] == second["id"] and not failed_once:
            failed_once = True
            return {"status": "failed", "message": "Fixture failed before upload"}
        await on_event({"event": "submitting", "message": "Fixture submission"})
        return {"status": "published", "message": "Fixture receipt"}

    runtime.publish_hook = controlled
    request = request_batch(content, [first, second, other])
    response = client.post("/api/batches", json=request)
    assert response.status_code == 200, response.text
    batch_id = response.json()["id"]
    assert runtime.started.wait(2)
    before = snapshot_text(app, batch_id)
    assert json.loads(before)["accountTemplateIds"] == {first["id"]: "forest", second["id"]: "artdeco"}
    set_template(client, first["id"], "tech")
    set_template(client, second["id"], "editorial")
    assert client.post("/api/batches", json=request).json()["id"] == batch_id
    assert snapshot_text(app, batch_id) == before
    runtime.release.set()
    batch = completed_batch(client, batch_id)
    assert [payload["body_html"] for payload in runtime.publish_calls] == [
        render_article(SOURCE, "forest", "wechat"), render_article(SOURCE, "artdeco", "wechat"),
        render_article(SOURCE, "journal", "dayu"),
    ]
    failed = next(task for task in batch["tasks"] if task["accountId"] == second["id"])
    assert failed["status"] == "failed" and failed["canRetry"]
    assert client.post(f"/api/tasks/{failed['id']}/retry").status_code == 200
    completed_batch(client, batch_id)
    assert runtime.publish_calls[-1]["body_html"] == render_article(SOURCE, "artdeco", "wechat")
    assert sum(payload["account_id"] == first["id"] for payload in runtime.publish_calls) == 1
    assert snapshot_text(app, batch_id) == before
    assert app.state.service.store.content(app.state.service.content_row(content["id"]))["articleTemplateId"] == "journal"
    newer = client.post("/api/batches", json=request_batch(content, [first, second]))
    assert newer.status_code == 200
    completed_batch(client, newer.json()["id"])
    assert [payload["body_html"] for payload in runtime.publish_calls[-2:]] == [
        render_article(SOURCE, "tech", "wechat"), render_article(SOURCE, "editorial", "wechat"),
    ]


def test_mcp_same_uuid_replay_keeps_frozen_template_after_preference_change(mcp_api, tmp_path):
    client, runtime, app = mcp_api
    account = wechat_account(client)
    set_template(client, account["id"], "forest")
    content = article_with_cover(client, tmp_path)
    request = {"contentId": content["id"], "requestId": str(uuid4())}
    batch = result_value(call(client, "publish", request))["batch"]
    completed_batch(client, batch["id"])
    before = snapshot_text(app, batch["id"])
    set_template(client, account["id"], "artdeco")
    replay = result_value(call(client, "publish", request))
    assert replay["reused"] and replay["batch"]["id"] == batch["id"]
    assert len(runtime.publish_calls) == 1
    assert runtime.publish_calls[0]["body_html"] == render_article(SOURCE, "forest", "wechat")
    assert snapshot_text(app, batch["id"]) == before


@pytest.mark.parametrize("legacy,status", [(True, "failed"), (True, "cancelled"), (False, "cancelled")])
def test_retry_uses_saved_template_without_reading_current_preference(api, tmp_path, legacy, status):
    client, runtime, app = api
    account = wechat_account(client)
    set_template(client, account["id"], "tech")
    content = article_with_cover(client, tmp_path, articleTemplateId="editorial")
    snapshot = {**content}
    if not legacy:
        snapshot["accountTemplateIds"] = {account["id"]: "forest"}
    store = app.state.service.store
    batch_id, task_id = str(uuid4()), str(uuid4())
    store.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)", (batch_id, str(uuid4()), content["id"], content["title"],
                  "article", "publish", "before", dump(snapshot), dump([account["id"]])))
    store.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message,fence) VALUES (?,?,?,?,?,?,?,0)",
                  (task_id, batch_id, account["id"], account["label"], "wechat", status, "Saved unsubmitted task"))
    before = snapshot_text(app, batch_id)
    response = client.post(f"/api/tasks/{task_id}/retry")
    assert response.status_code == 200, response.text
    completed_batch(client, batch_id)
    assert len(runtime.publish_calls) == 1
    assert runtime.publish_calls[0]["body_html"] == render_article(SOURCE, "editorial" if legacy else "forest", "wechat")
    assert snapshot_text(app, batch_id) == before


@pytest.mark.parametrize("templates", [None, {}, {"other-account": "forest"}])
def test_invalid_new_snapshot_does_not_fall_back_to_live_preferences(api, templates):
    client, runtime, app = api
    account = wechat_account(client)
    set_template(client, account["id"], "tech")
    content = create_content(client)
    with pytest.raises(Problem):
        asyncio.run(app.state.service.payload({**content, "accountTemplateIds": templates},
                                  {"account_id": account["id"], "platform": "wechat"}, "publish"))
    assert runtime.publish_calls == []


def test_wenyan_default_migration_is_once_and_preserves_explicit_themes_and_snapshots(tmp_path):
    directory = tmp_path / "migration"
    store = Store(directory)
    for identifier, template in (("default", "clean"), ("chosen", "forest")):
        store.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
                      (identifier, "wechat", identifier, "ready", "connected", "before", 1))
        store.execute("INSERT INTO account_preferences VALUES (?,?,?)", (identifier, 0, template))
    frozen = dump({"body": "Saved body", "accountTemplateIds": {"default": "clean"}})
    store.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)", ("old-batch", "old-request", "content", "title", "article", "publish", "before", frozen, "[]"))
    store.execute("PRAGMA user_version=4")
    store.close()
    store = Store(directory)
    assert store.account_wechat_template("default") == "wenyan-default"
    assert store.account_wechat_template("chosen") == "forest"
    assert store.account_default_selected("default") is False
    assert store.one("SELECT snapshot FROM batches WHERE id='old-batch'")["snapshot"] == frozen
    # An explicit choice of the compatibility template after upgrading survives.
    store.execute("UPDATE account_preferences SET wechat_template_id='clean' WHERE account_id='default'")
    store.close()
    store = Store(directory)
    try:
        assert store.account_wechat_template("default") == "clean"
        assert store.one("SELECT snapshot FROM batches WHERE id='old-batch'")["snapshot"] == frozen
    finally:
        store.close()
