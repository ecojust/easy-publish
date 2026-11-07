"""Kuaishou API/queue fixtures; no real account or platform traffic."""
import asyncio
import base64
import copy

import pytest

from easy_publish.catalog import BY_ID
from easy_publish.runtime import Runtime
from test_core import api, completed_batch, create_content, ready_account, request_batch


def video_content(client, tmp_path, **changes):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42fixture-video")
    response = client.post("/api/assets/import", json={"path": str(video), "kind": "video"})
    assert response.status_code == 200
    values = dict(kind="video", title="视频标题", body="", description="保留这段视频描述", tags=["创作"],
                  videoAssetId=response.json()["id"])
    values.update(changes)
    return create_content(client, **values)


def preflight(client, content, account, mode="publish"):
    response = client.post("/api/preflight", json={"contentId": content["id"], "accountIds": [account["id"]], "mode": mode})
    assert response.status_code == 200
    return response.json()


def test_kuaishou_is_a_video_only_browser_target():
    assert BY_ID["kuaishou"]["kinds"] == ["video"]
    assert BY_ID["kuaishou"]["modes"] == ["publish"]
    assert BY_ID["kuaishou"]["auth"] == "browser"


@pytest.mark.parametrize("changes, expected", [
    ({"title": ""}, "标题"),
    ({"title": "A", "description": "x" * 499, "tags": []}, "500"),
    ({"title": "A", "description": "😀" * 250, "tags": []}, "500"),
    ({"title": "A", "description": "x" * 496, "tags": ["话题"]}, "500"),
    ({"tags": ["1", "2", "3", "4", "5"]}, "话题"),
    ({"tags": ["含 空格"]}, "空白"),
    ({"tags": ["#话题"]}, "井号"),
    ({"tags": ["重复", "重复"]}, "重复"),
])
def test_invalid_metadata_never_reaches_publishing_queue(api, tmp_path, changes, expected):
    client, runtime, _ = api
    account = ready_account(client, "kuaishou")
    content = video_content(client, tmp_path, **changes)
    result = preflight(client, content, account)
    assert not result["ok"]
    assert any(expected in error for error in result["targets"][0]["errors"])
    assert client.post("/api/batches", json=request_batch(content, [account])).status_code == 400
    assert runtime.publish_calls == []


def test_unused_options_are_omitted_from_queue_payload_without_changing_video_metadata(api, tmp_path):
    client, runtime, application = api
    account = ready_account(client, "kuaishou")
    options = {"kuaishou": {"schedule": "tomorrow", "copyright": "2", "source": "unused"}}
    content = video_content(client, tmp_path, platformOptions=options)
    assert preflight(client, content, account)["ok"]
    response = client.post("/api/batches", json=request_batch(content, [account]))
    assert response.status_code == 200, response.text
    assert completed_batch(client, response.json()["id"])["tasks"][0]["status"] == "published"
    assert len(runtime.publish_calls) == 1
    payload = runtime.publish_calls[0]
    assert payload["options"] == {}
    for key in ("title", "description", "tags"):
        assert payload[key] == content[key]
    assert payload["video_path"] == application.state.service.asset_row(content["videoAssetId"])["path"]
    stored = application.state.service.store.content(application.state.service.content_row(content["id"]))
    assert stored["platformOptions"] == options


def test_combined_caption_boundary_and_four_tags_are_supported(api, tmp_path):
    client, _, _ = api
    account = ready_account(client, "kuaishou")
    at_limit = video_content(client, tmp_path, title="A", description="x" * 498, tags=[])
    assert preflight(client, at_limit, account)["ok"]
    with_tags = video_content(client, tmp_path, tags=["一", "二", "三", "四"])
    assert preflight(client, with_tags, account)["ok"]


def test_article_and_remote_draft_are_rejected(api, tmp_path):
    client, _, _ = api
    account = ready_account(client, "kuaishou")
    article = create_content(client)
    assert any("内容类型" in e for e in preflight(client, article, account)["targets"][0]["errors"])
    video = video_content(client, tmp_path)
    assert any("草稿" in e for e in preflight(client, video, account, "draft")["targets"][0]["errors"])


def test_cover_and_metadata_reach_frozen_task_and_receipt_remains_submitted(api, tmp_path):
    from PIL import Image
    client, runtime, _ = api
    account = ready_account(client, "kuaishou")
    cover = tmp_path / "cover.png"
    Image.new("RGB", (4, 4)).save(cover)
    imported = client.post("/api/assets/import", json={"path": str(cover), "kind": "image"})
    assert imported.status_code == 200
    content = video_content(client, tmp_path, coverAssetId=imported.json()["id"])
    captured = []

    async def submit(payload, on_event, job_id):
        captured.append(copy.deepcopy(payload))
        await on_event({"event": "submitting", "message": "fixture upload"})
        return {"status": "submitted", "message": "fixture awaiting review", "remote_id": "fixture-id",
                "url": "https://cp.kuaishou.com/article/manage/video"}

    runtime.publish_hook = submit
    batch = client.post("/api/batches", json=request_batch(content, [account]))
    assert batch.status_code == 200
    task = completed_batch(client, batch.json()["id"])["tasks"][0]
    assert task["status"] == "submitted" and task["canRetry"] is False
    assert captured[0]["platform_id"] == "kuaishou"
    assert captured[0]["title"] == content["title"]
    assert captured[0]["description"] == content["description"]
    assert captured[0]["tags"] == content["tags"]
    assert captured[0]["cover_path"].endswith(".png")


def test_real_supervised_worker_loads_kuaishou_without_session_or_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_PUBLISH_TESTING", "1")
    monkeypatch.setenv("EASY_PUBLISH_TEST_KEY", base64.b64encode(b"k" * 32).decode())

    async def run():
        runtime = Runtime(tmp_path / "runtime")
        async def progress(event):
            pass
        try:
            return await runtime.run("check", {"platform_id": "kuaishou", "account_id": "fixture", "kind": "video"}, progress, "kuaishou-dispatch")
        finally:
            await runtime.shutdown()

    result = asyncio.run(run())
    assert result["status"] == "login_required"
    assert "快手" in result["message"]
