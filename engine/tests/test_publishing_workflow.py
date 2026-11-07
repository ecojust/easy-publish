"""History, repeated publication and article-preview API integration fixtures."""
import asyncio
import json

import pytest

from easy_publish.store import dump, now, uid
from test_core import api, completed_batch, content_input, create_content, ready_account, request_batch


def test_published_content_can_be_published_again_with_new_request_id(api):
    client, runtime, _ = api
    account = ready_account(client)
    content = create_content(client)
    first_request = request_batch(content, [account])
    first = client.post("/api/batches", json=first_request)
    assert first.status_code == 200
    completed_batch(client, first.json()["id"])
    state = client.get("/api/state").json()
    assert state["contents"][0]["platformPublicationStates"] == {"zhihu": "published"}
    replay = client.post("/api/batches", json=first_request)
    assert replay.json()["id"] == first.json()["id"]
    assert len(runtime.publish_calls) == 1
    preflight = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"}).json()
    assert preflight["ok"] and any("提交过" in warning for warning in preflight["targets"][0]["warnings"])
    second = client.post("/api/batches", json=request_batch(content, [account]))
    assert second.status_code == 200 and second.json()["id"] != first.json()["id"]
    completed_batch(client, second.json()["id"])
    assert len(runtime.publish_calls) == 2


def insert_history(store, content, platform, status):
    batch_id = uid()
    with store.transaction() as db:
        db.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)", (batch_id, uid(), content["id"], content["title"], content["kind"], "publish", now(), dump(content), "[]"))
        db.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message) VALUES (?,?,?,?,?,?,?)", (uid(), batch_id, "removed-account", "Old account", platform, status, "fixture receipt"))


def test_platform_history_uses_all_batches_and_keeps_distinct_receipts(api):
    client, _, app = api
    content = create_content(client)
    store = app.state.service.store
    insert_history(store, content, "zhihu", "published")
    for _ in range(201):
        insert_history(store, content, "xueqiu", "draft_created")
    insert_history(store, content, "dayu", "submitted")
    insert_history(store, content, "wechat", "unknown")
    insert_history(store, content, "zhihu", "failed")
    state = client.get("/api/state").json()
    assert len(state["batches"]) == 200
    assert state["contents"][0]["platformPublicationStates"] == {"zhihu": "published", "dayu": "submitted", "wechat": "unknown"}


def test_latest_qualified_receipt_can_change_to_unknown_without_enabling_retry(api):
    client, _, app = api
    account = ready_account(client)
    content = create_content(client)
    insert_history(app.state.service.store, content, "zhihu", "submitted")
    insert_history(app.state.service.store, content, "zhihu", "unknown")
    state = client.get("/api/state").json()
    assert state["contents"][0]["platformPublicationStates"]["zhihu"] == "unknown"
    check = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [account["id"]], "mode": "publish"}).json()
    assert check["ok"]
    assert any("结果不明" in warning for warning in check["targets"][0]["warnings"])
    unknown = next(t for b in state["batches"] for t in b["tasks"] if t["status"] == "unknown")
    assert unknown["canRetry"] is False


def test_old_content_gets_default_template_without_modifying_source(api):
    client, _, app = api
    content = create_content(client)
    data = content_input(body="# Existing draft")
    assert "articleTemplateId" not in data
    app.state.service.store.execute("UPDATE contents SET data=? WHERE id=?", (dump(data), content["id"]))
    saved = client.get("/api/state").json()["contents"][0]
    assert saved["articleTemplateId"] == "clean"
    assert saved["body"] == "# Existing draft"
    assert saved["platformPublicationStates"] == {}


def test_article_preview_exposes_templates_and_platform_specific_safe_html(api):
    client, _, _ = api
    state = client.get("/api/state").json()
    assert {item["id"] for item in state["articleTemplates"]} == {"clean", "editorial", "journal", "tech", "forest", "artdeco"}
    rendered = []
    for template in state["articleTemplates"]:
        response = client.post("/api/preview/article", json={"body": "## 标题\n\n正文 **重点**\n\n> 引用", "articleTemplateId": template["id"], "platformId": "wechat"})
        assert response.status_code == 200
        value = response.json()
        assert "重点" in value["html"] and "style=" in value["html"]
        assert isinstance(value["notes"], list)
        rendered.append(value["html"])
    assert len(set(rendered)) == 6
    source = "## 标题\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))"
    for platform in ("wechat", "zhihu", "dayu", "xueqiu"):
        response = client.post("/api/preview/article", json={"body": source, "articleTemplateId": "journal", "platformId": platform})
        assert response.status_code == 200
        html = response.json()["html"]
        assert "<script" not in html and 'href="javascript:' not in html


@pytest.mark.parametrize("fields", [{"platformId": "douyin"}, {"platformId": "missing"}, {"articleTemplateId": "untrusted"}])
def test_preview_rejects_video_platforms_and_unknown_templates(api, fields):
    client, _, _ = api
    response = client.post("/api/preview/article", json={"body": "text", **fields})
    assert response.status_code in {400, 422}


@pytest.mark.parametrize("template", ["forest", "artdeco"])
def test_new_templates_round_trip_to_the_wechat_queue_without_changing_preview(api, tmp_path, template):
    from PIL import Image
    client, runtime, app = api
    account = ready_account(client, "wechat", credentials={"appId": "fixture-app", "appSecret": "fixture-secret"})
    image = tmp_path / "cover.png"
    Image.new("RGB", (3, 3), "green").save(image)
    imported = client.post("/api/assets/import", json={"path": str(image), "kind": "image"})
    assert imported.status_code == 200
    source = "## 一个新的章节\n\n正文 **重要信息**\n\n> 引用里的文字\n\n```text\n原样代码\n```"
    content = create_content(client, body=source, articleTemplateId=template, coverAssetId=imported.json()["id"])
    preview = client.post("/api/preview/article", json={"body": source, "articleTemplateId": template, "platformId": "wechat"})
    assert preview.status_code == 200
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200
    completed_batch(client, response.json()["id"])
    assert runtime.publish_calls[0]["body_html"] == preview.json()["html"]
    stored = app.state.service.store.content(app.state.service.content_row(content["id"]))
    assert stored["body"] == source and stored["articleTemplateId"] == template
    snapshot = json.loads(app.state.service.store.one("SELECT snapshot FROM batches WHERE id=?", (response.json()["id"],))["snapshot"])
    assert snapshot["body"] == source and snapshot["articleTemplateId"] == template


def test_template_choice_is_snapshotted_and_target_html_uses_selected_template(api):
    client, runtime, app = api
    account = ready_account(client, "dayu")
    content = create_content(client, title="足够长的标题", body="## 正文\n\n模板测试", articleTemplateId="editorial", platformOptions={"dayu": {"source": "个人观点"}})
    async def pause(payload, on_event, job_id):
        runtime.started.set()
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        await on_event({"event": "submitting", "message": "fixture submission"})
        return {"status": "submitted", "message": "fixture receipt"}
    runtime.publish_hook = pause
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200
    assert runtime.started.wait(2)
    updated = content_input(title="足够长的标题", body="Changed source", articleTemplateId="tech", platformOptions={"dayu": {"source": "个人观点"}})
    assert client.put(f"/api/contents/{content['id']}", json=updated).status_code == 200
    runtime.release.set()
    completed_batch(client, response.json()["id"])
    snapshot = json.loads(app.state.service.store.one("SELECT snapshot FROM batches WHERE id=?", (response.json()["id"],))["snapshot"])
    assert snapshot["articleTemplateId"] == "editorial" and snapshot["body"] == content["body"]
    from easy_publish.content import render_article
    assert runtime.publish_calls[0]["body_html"] == render_article(content["body"], "editorial", "dayu")
