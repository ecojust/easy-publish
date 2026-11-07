"""MCP protocol, private discovery and scoped publishing integration fixtures."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import socket
import socketserver
import subprocess
import sys
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from easy_publish.app import create_app
from easy_publish.mcp_connection import ConnectionUnavailable, ENDPOINT_FILE, read_endpoint, remove_endpoint, write_endpoint
from easy_publish.mcp_server import DesktopBridge, StdioServer, tool_result
from test_core import TOKEN, FakeRuntime, completed_batch, create_content, ready_account


MCP_TOKEN = "fixture-mcp-scoped-token-not-a-credential-12345678"
INSTANCE = "fixture-mcp-instance-1234567890"


@pytest.fixture
def mcp_api(tmp_path):
    runtime = FakeRuntime()
    app = create_app(tmp_path / "data", TOKEN, runtime=runtime, mcp_token=MCP_TOKEN, instance_id=INSTANCE)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client, runtime, app


def call(client, name, arguments=None):
    response = client.post("/api/mcp/call", headers={"Authorization": "Bearer " + MCP_TOKEN, "X-Easy-Publish-Instance": INSTANCE},
                           json={"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result


def result_value(result):
    assert result["isError"] is False, result
    return result["structuredContent"]


def test_scoped_token_cannot_reach_desktop_or_credentials_routes(mcp_api):
    client, _, _ = mcp_api
    for method, path in [("GET", "/api/state"), ("POST", "/api/shutdown"), ("POST", "/api/accounts"),
                         ("POST", "/api/assets/import"), ("DELETE", "/api/accounts/id"),
                         ("PUT", "/api/accounts/id/preferences")]:
        response = client.request(method, path, headers={"Authorization": "Bearer " + MCP_TOKEN}, json={})
        assert response.status_code == 401
    assert client.post("/api/mcp/call", json={"name": "list_platforms", "arguments": {}}).status_code == 401
    assert client.post("/api/mcp/call", headers={"Authorization": "Bearer " + MCP_TOKEN, "X-Easy-Publish-Instance": INSTANCE, "Origin": "https://example.test"},
                       json={"name": "list_platforms"}).status_code == 403
    assert client.post("/api/mcp/call", headers={"Authorization": "Bearer " + MCP_TOKEN, "X-Easy-Publish-Instance": "wrong-instance"},
                       json={"name": "list_platforms"}).status_code == 401


def test_tool_capabilities_accounts_and_templates_are_kind_scoped(mcp_api):
    client, runtime, _ = mcp_api
    article = ready_account(client, "zhihu")
    video = ready_account(client, "douyin")
    runtime.vault.put(article["id"], "session", b"private-cookie-never-exposed")
    platforms = result_value(call(client, "list_platforms", {"kind": "article"}))
    assert all("article" in p["kinds"] for p in platforms["platforms"])
    from easy_publish.article_templates import TEMPLATE_IDS
    assert {p["id"] for p in platforms["articleTemplates"]} == TEMPLATE_IDS
    accounts = result_value(call(client, "list_accounts", {"kind": "article"}))["accounts"]
    assert [a["id"] for a in accounts] == [article["id"]]
    assert video["id"] not in json.dumps(accounts)
    assert "private-cookie" not in json.dumps(accounts)


@pytest.mark.parametrize("template", ["journal", "forest", "artdeco"])
def test_save_content_and_preview_are_local_and_preserve_template(mcp_api, template):
    client, runtime, _ = mcp_api
    saved = result_value(call(client, "save_content", {"content": {"kind": "article", "title": "MCP 文章",
                              "body": "## 标题\n\n正文与 **重点**", "articleTemplateId": template}}))["content"]
    assert saved["articleTemplateId"] == template
    assert runtime.publish_calls == []
    preview = result_value(call(client, "preview_article", {"body": saved["body"], "articleTemplateId": template, "platformId": "wechat"}))
    assert "<strong" in preview["html"] and "style=" in preview["html"]
    assert preview["notes"]
    listed = result_value(call(client, "list_contents", {"kind": "article"}))["contents"]
    assert listed[0]["id"] == saved["id"] and "body" not in listed[0]
    read = result_value(call(client, "get_content", {"contentId": saved["id"]}))["content"]
    assert read["body"] == saved["body"]
    assert read["articleTemplateId"] == template
    desktop_preview = client.post("/api/preview/article", json={"body": read["body"], "articleTemplateId": template, "platformId": "wechat"})
    assert desktop_preview.status_code == 200
    assert desktop_preview.json() == preview
    invalid = call(client, "preview_article", {"body": "text", "platformId": "douyin"})
    assert invalid["isError"] is True


def test_publish_defaults_match_kind_and_uuid_replay_does_not_repost(mcp_api):
    client, runtime, _ = mcp_api
    article = ready_account(client, "zhihu")
    video = ready_account(client, "douyin")
    content = create_content(client)
    preflight = result_value(call(client, "preflight", {"contentId": content["id"]}))
    assert preflight["accountIds"] == [article["id"]] and preflight["ok"]
    request_id = str(uuid4())
    first = result_value(call(client, "publish", {"contentId": content["id"], "requestId": request_id}))
    batch = completed_batch(client, first["batch"]["id"])
    assert [task["accountId"] for task in batch["tasks"]] == [article["id"]]
    assert video["id"] not in [task["accountId"] for task in batch["tasks"]]
    second = result_value(call(client, "publish", {"contentId": content["id"], "requestId": request_id}))
    assert second["reused"] and second["batch"]["id"] == batch["id"]
    assert len(runtime.publish_calls) == 1
    automatic = call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())})
    assert automatic["isError"] and automatic["structuredContent"]["code"] == "no_eligible_accounts"
    explicit = result_value(call(client, "publish", {"contentId": content["id"], "accountIds": [article["id"]], "requestId": str(uuid4())}))
    assert explicit["batch"]["id"] != batch["id"]
    completed_batch(client, explicit["batch"]["id"])
    assert len(runtime.publish_calls) == 2


@pytest.mark.parametrize("kind,platform,other_platform", [("article", "zhihu", "douyin"), ("video", "douyin", "zhihu")])
def test_account_preferences_limit_automatic_selection_but_not_explicit_targets(mcp_api, tmp_path, kind, platform, other_platform):
    client, runtime, _ = mcp_api
    selected = ready_account(client, platform)
    unchecked = ready_account(client, platform)
    opposite_kind = ready_account(client, other_platform)
    disconnected = client.post("/api/accounts", json={"platformId": platform, "label": "Disconnected default account"}).json()
    response = client.put(f"/api/accounts/{unchecked['id']}/preferences", json={"defaultSelected": False})
    assert response.status_code == 200
    if kind == "video":
        path = tmp_path / "preference-fixture.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42fixture-video")
        asset = result_value(call(client, "import_asset", {"path": str(path), "kind": "video"}))["asset"]
        content = create_content(client, kind="video", body="", videoAssetId=asset["id"])
    else:
        content = create_content(client)

    accounts = result_value(call(client, "list_accounts", {"kind": kind}))["accounts"]
    assert {account["id"]: account["defaultSelected"] for account in accounts} == {
        selected["id"]: True, unchecked["id"]: False,
    }
    all_accounts = result_value(call(client, "list_accounts", {"kind": kind, "connectedOnly": False}))["accounts"]
    assert disconnected["id"] in {account["id"] for account in all_accounts}
    automatic = result_value(call(client, "preflight", {"contentId": content["id"]}))
    assert automatic["ok"] and automatic["accountIds"] == [selected["id"]]
    if kind == "video":
        unsupported = call(client, "preflight", {"contentId": content["id"], "mode": "draft"})
        assert unsupported["isError"] and unsupported["structuredContent"]["code"] == "no_eligible_accounts"
    explicit = result_value(call(client, "preflight", {"contentId": content["id"], "accountIds": [unchecked["id"]]}))
    assert explicit["ok"] and explicit["accountIds"] == [unchecked["id"]]
    mismatch = call(client, "publish", {"contentId": content["id"], "accountIds": [unchecked["id"], opposite_kind["id"]], "requestId": str(uuid4())})
    assert mismatch["isError"] and mismatch["structuredContent"]["code"] == "content_kind_mismatch"
    assert runtime.publish_calls == []

    batch = result_value(call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())}))["batch"]
    completed_batch(client, batch["id"])
    assert [task["accountId"] for task in batch["tasks"]] == [selected["id"]]
    # Explicit choice overrides both the preference and the previous platform receipt.
    explicit_batch = result_value(call(client, "publish", {"contentId": content["id"], "accountIds": [unchecked["id"]], "requestId": str(uuid4())}))["batch"]
    completed_batch(client, explicit_batch["id"])
    assert [task["accountId"] for task in explicit_batch["tasks"]] == [unchecked["id"]]
    assert [payload["account_id"] for payload in runtime.publish_calls] == [selected["id"], unchecked["id"]]


def test_automatic_selection_does_not_fall_back_when_all_compatible_preferences_are_off(mcp_api):
    client, runtime, _ = mcp_api
    article = ready_account(client, "zhihu")
    ready_account(client, "douyin")
    assert client.put(f"/api/accounts/{article['id']}/preferences", json={"defaultSelected": False}).status_code == 200
    content = create_content(client)
    for tool, extra in [("preflight", {}), ("publish", {"requestId": str(uuid4())})]:
        result = call(client, tool, {"contentId": content["id"], **extra})
        assert result["isError"] and result["structuredContent"]["code"] == "no_eligible_accounts"
        assert "默认选中" in result["structuredContent"]["error"]
    assert runtime.publish_calls == []


def test_request_replay_preserves_original_targets_after_preference_changes(mcp_api):
    client, runtime, _ = mcp_api
    first_account = ready_account(client, "zhihu")
    second_account = ready_account(client, "xueqiu")
    assert client.put(f"/api/accounts/{second_account['id']}/preferences", json={"defaultSelected": False}).status_code == 200
    content = create_content(client, platformOptions={"xueqiu": {"declaration": "不添加创作声明"}})
    request_id = str(uuid4())
    first = result_value(call(client, "publish", {"contentId": content["id"], "requestId": request_id}))["batch"]
    completed_batch(client, first["id"])
    assert client.put(f"/api/accounts/{first_account['id']}/preferences", json={"defaultSelected": False}).status_code == 200
    assert client.put(f"/api/accounts/{second_account['id']}/preferences", json={"defaultSelected": True}).status_code == 200
    replay = result_value(call(client, "publish", {"contentId": content["id"], "requestId": request_id}))
    assert replay["reused"] and replay["batch"]["id"] == first["id"]
    assert [task["accountId"] for task in replay["batch"]["tasks"]] == [first_account["id"]]
    assert len(runtime.publish_calls) == 1
    next_batch = result_value(call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())}))["batch"]
    completed_batch(client, next_batch["id"])
    assert next_batch["id"] != first["id"]
    assert [task["accountId"] for task in next_batch["tasks"]] == [second_account["id"]]
    assert [payload["account_id"] for payload in runtime.publish_calls] == [first_account["id"], second_account["id"]]


def test_mixed_kind_or_disconnected_targets_never_publish(mcp_api):
    client, runtime, _ = mcp_api
    article = ready_account(client, "zhihu")
    video = ready_account(client, "douyin")
    content = create_content(client)
    mixed = call(client, "publish", {"contentId": content["id"], "accountIds": [article["id"], video["id"]], "requestId": str(uuid4())})
    assert mixed["isError"] and mixed["structuredContent"]["code"] == "content_kind_mismatch"
    unconnected = client.post("/api/accounts", json={"platformId": "zhihu", "label": "unconnected fixture"}).json()
    stopped = call(client, "publish", {"contentId": content["id"], "accountIds": [unconnected["id"]], "requestId": str(uuid4())})
    assert stopped["isError"] and stopped["structuredContent"]["code"] == "preflight_failed"
    assert runtime.publish_calls == []


def test_video_import_and_publish_cannot_target_article_account(mcp_api, tmp_path):
    client, runtime, _ = mcp_api
    article = ready_account(client, "zhihu")
    video = ready_account(client, "douyin")
    path = tmp_path / "视频.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42fixture-video")
    asset = result_value(call(client, "import_asset", {"path": str(path), "kind": "video"}))["asset"]
    assert "path" not in asset
    content = result_value(call(client, "save_content", {"content": {"kind": "video", "title": "MCP 视频", "videoAssetId": asset["id"]}}))["content"]
    mismatch = call(client, "preflight", {"contentId": content["id"], "accountIds": [article["id"]]})
    assert mismatch["isError"]
    batch = result_value(call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())}))["batch"]
    completed_batch(client, batch["id"])
    assert [task["accountId"] for task in batch["tasks"]] == [video["id"]]
    assert runtime.publish_calls[0]["kind"] == "video"
    result = result_value(call(client, "get_batch", {"batchId": batch["id"]}))["batch"]
    assert result["tasks"][0]["status"] == "published"


def test_unknown_platform_is_excluded_from_automatic_republication(mcp_api):
    client, runtime, _ = mcp_api
    ready_account(client, "zhihu")
    content = create_content(client)
    async def unknown(payload, on_event, job_id):
        await on_event({"event": "submitting", "message": "fixture mutation"})
        return {"status": "unknown", "message": "fixture unknown receipt"}
    runtime.publish_hook = unknown
    first = result_value(call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())}))["batch"]
    completed_batch(client, first["id"])
    second = call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())})
    assert second["isError"] and len(runtime.publish_calls) == 1


def test_request_id_cannot_be_reused_for_other_content_or_targets(mcp_api):
    client, runtime, _ = mcp_api
    first_account = ready_account(client, "zhihu")
    second_account = ready_account(client, "zhihu")
    first_content = create_content(client)
    second_content = create_content(client, title="Another draft")
    request_id = str(uuid4())
    first = result_value(call(client, "publish", {"contentId": first_content["id"], "accountIds": [first_account["id"]], "requestId": request_id}))["batch"]
    completed_batch(client, first["id"])
    for content_id, account_id in [(second_content["id"], first_account["id"]), (first_content["id"], second_account["id"])]:
        error = call(client, "publish", {"contentId": content_id, "accountIds": [account_id], "requestId": request_id})
        assert error["isError"] and error["structuredContent"]["code"] == "idempotency_conflict"
    assert len(runtime.publish_calls) == 1


def test_cancel_and_paginated_batch_queries_use_existing_queue(mcp_api):
    client, runtime, _ = mcp_api
    ready_account(client, "zhihu")
    content = create_content(client)
    async def wait_without_mutation(payload, on_event, job_id):
        runtime.started.set()
        while not runtime.release.is_set():
            await asyncio.sleep(0.01)
        return {"status": "failed", "message": "fixture cancelled before upload"}
    runtime.publish_hook = wait_without_mutation
    batch = result_value(call(client, "publish", {"contentId": content["id"], "requestId": str(uuid4())}))["batch"]
    assert runtime.started.wait(2)
    assert result_value(call(client, "cancel_batch", {"batchId": batch["id"]}))["ok"]
    final = completed_batch(client, batch["id"])
    assert final["tasks"][0]["status"] == "cancelled"
    listed = result_value(call(client, "list_batches", {"contentId": content["id"], "limit": 1}))
    assert listed["total"] == 1 and listed["batches"][0]["id"] == batch["id"] and listed["nextOffset"] is None


@pytest.mark.parametrize("name,arguments", [("execute_shell", {}), ("publish", {"contentId": "x"}),
    ("publish", {"contentId": "x", "requestId": "not-a-uuid"}), ("list_accounts", {"kind": "all"}),
    ("save_content", {"content": {"kind": "article", "credential": "fixture-secret"}})])
def test_invalid_tool_arguments_never_echo_input_or_run(mcp_api, name, arguments):
    client, runtime, _ = mcp_api
    result = call(client, name, arguments)
    assert result["isError"] and "fixture-secret" not in json.dumps(result)
    assert runtime.publish_calls == []


def test_descriptor_roundtrip_and_instance_owned_cleanup(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    write_endpoint(directory, 12345, MCP_TOKEN, INSTANCE)
    value = read_endpoint(directory)
    assert value["port"] == 12345 and value["token"] == MCP_TOKEN
    text = (directory / ENDPOINT_FILE).read_text()
    if os.name == "nt":
        assert MCP_TOKEN not in text and "protectedToken" in text
    else:
        assert (directory / ENDPOINT_FILE).stat().st_mode & 0o777 == 0o600
    remove_endpoint(directory, "different-instance")
    assert (directory / ENDPOINT_FILE).exists()
    remove_endpoint(directory, INSTANCE)
    assert not (directory / ENDPOINT_FILE).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode checks; Windows uses current-user DPAPI")
def test_descriptor_rejects_shared_permissions_and_symlinks(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    write_endpoint(directory, 12345, MCP_TOKEN, INSTANCE)
    path = directory / ENDPOINT_FILE
    path.chmod(0o644)
    with pytest.raises(ConnectionUnavailable):
        read_endpoint(directory)
    path.chmod(0o600)
    moved = directory / "original"
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(ConnectionUnavailable):
        read_endpoint(directory)
    with pytest.raises(ConnectionUnavailable):
        write_endpoint(directory, 12345, MCP_TOKEN, INSTANCE)
    assert moved.exists()


@contextmanager
def http_fixture(handler):
    class LoopbackServer(ThreadingHTTPServer):
        def server_bind(self):
            # These fixtures bind a numeric loopback address. Avoid HTTPServer's
            # reverse DNS lookup, which can stall macOS runners for ~35 seconds.
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            handler(self)
        def log_message(self, *args):
            pass
    server = LoopbackServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_loopback_http_fixture_never_performs_reverse_dns(monkeypatch):
    def unexpected_dns(*args, **kwargs):
        raise AssertionError("A loopback-only fixture must not perform reverse DNS")
    monkeypatch.setattr(socket, "getfqdn", unexpected_dns)
    def handler(request):
        request.send_response(204)
        request.end_headers()
    with http_fixture(handler) as port:
        with httpx.Client(trust_env=False, timeout=3) as client:
            assert client.post(f"http://127.0.0.1:{port}/fixture").status_code == 204


def test_real_stdio_initialize_discovery_and_scoped_tool_call(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    calls = []
    def handler(request):
        assert request.path == "/api/mcp/call"
        assert request.headers["Authorization"] == "Bearer " + MCP_TOKEN
        assert request.headers["X-Easy-Publish-Instance"] == INSTANCE
        data = json.loads(request.rfile.read(int(request.headers["Content-Length"])))
        calls.append(data)
        response = json.dumps(tool_result({"accounts": [], "kind": data["arguments"]["kind"]})).encode()
        request.send_response(200)
        request.send_header("Content-Type", "application/json")
        request.send_header("Content-Length", str(len(response)))
        request.end_headers()
        request.wfile.write(response)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_accounts", "arguments": {"kind": "article"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "publish", "arguments": {"contentId": "x"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
    ]
    with http_fixture(handler) as port:
        write_endpoint(directory, port, MCP_TOKEN, INSTANCE)
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result = subprocess.run([sys.executable, "-m", "easy_publish", "--mcp", "--data-dir", str(directory)],
                                input="".join(json.dumps(item) + "\n" for item in messages).encode(), capture_output=True,
                                env=environment, timeout=15)
    assert result.returncode == 0
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4, 5]
    assert responses[0]["result"]["serverInfo"]["name"] == "easy-publish"
    tools = {tool["name"]: tool for tool in responses[1]["result"]["tools"]}
    assert tools["publish"]["annotations"]["destructiveHint"] is True
    assert "requestId" in tools["publish"]["inputSchema"]["required"]
    from easy_publish.article_templates import TEMPLATE_IDS
    assert set(tools["preview_article"]["inputSchema"]["properties"]["articleTemplateId"]["enum"]) == TEMPLATE_IDS
    assert responses[2]["result"]["structuredContent"] == {"accounts": [], "kind": "article"}
    assert responses[3]["result"]["isError"] is True
    assert len(calls) == 1
    assert MCP_TOKEN.encode() not in result.stdout + result.stderr
    assert not (directory / "library.sqlite3").exists()
    assert not (directory / "engine.lock").exists()


def test_stdio_lifecycle_errors_and_desktop_missing_are_safe(tmp_path):
    server = StdioServer(DesktopBridge(tmp_path / "missing"))
    async def run():
        assert (await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))["error"]["code"] == -32002
        assert await server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
        assert (await server.handle([]))["error"]["code"] == -32600
        result = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {
            "protocolVersion": "unknown-future", "clientInfo": {"name": "fixture", "version": "1"}, "capabilities": {}}})
        assert result["result"]["protocolVersion"] == "2025-06-18"
        response = await server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_accounts", "arguments": {"kind": "video"}}})
        assert response["result"]["isError"] and "请先打开" in response["result"]["content"][0]["text"]
        assert (await server.handle({"jsonrpc": "2.0", "id": 4, "method": "unknown"}))["error"]["code"] == -32601
        assert (await server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {"cursor": "bad"}}))["error"]["code"] == -32602
    asyncio.run(run())


def test_real_stdio_parse_errors_and_utf8_never_open_database(tmp_path):
    directory = tmp_path / "missing"
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    payload = b"{invalid-json}\n" + json.dumps({"jsonrpc": "2.0", "id": "中文编号", "method": "ping"}, ensure_ascii=False).encode("utf-8") + b"\n"
    result = subprocess.run([sys.executable, "-m", "easy_publish", "--mcp", "--data-dir", str(directory)],
                            input=payload, capture_output=True, env=environment, timeout=10)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 0 and responses[0]["error"]["code"] == -32700
    assert responses[1]["id"] == "中文编号" and responses[1]["result"] == {}
    assert not directory.exists()


def test_bridge_does_not_follow_redirect_or_disclose_token(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    received = []
    def handler(request):
        received.append(request.path)
        request.send_response(302)
        request.send_header("Location", "/steal-fixture-token")
        request.end_headers()
    with http_fixture(handler) as port:
        write_endpoint(directory, port, MCP_TOKEN, INSTANCE)
        result = asyncio.run(DesktopBridge(directory).call("list_accounts", {"kind": "article"}))
    assert result["isError"] and received == ["/api/mcp/call"]
    assert MCP_TOKEN not in json.dumps(result)


def test_live_engine_discovery_scoped_calls_and_shutdown_cleanup(tmp_path):
    directory = tmp_path / "desktop"
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    child = subprocess.Popen([sys.executable, "-m", "easy_publish", "--data-dir", str(directory), "--parent-pid", str(os.getpid())],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
    ready = queue.Queue(maxsize=1)
    thread = threading.Thread(target=lambda: ready.put(child.stdout.readline(65_537)), daemon=True)
    thread.start()
    try:
        handshake = json.loads(ready.get(timeout=15))
        assert handshake["event"] == "ready"
        endpoint = read_endpoint(directory)
        assert endpoint["port"] == handshake["port"]
        assert endpoint["token"] != handshake["token"]
        result = asyncio.run(DesktopBridge(directory).call("list_platforms", {"kind": "video"}))
        platforms = result_value(result)["platforms"]
        assert any(p["id"] == "bilibili" for p in platforms)
        assert all("video" in p["kinds"] for p in platforms)
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
            origin = f"http://127.0.0.1:{handshake['port']}"
            assert client.get(origin + "/api/state", headers={"Authorization": "Bearer " + endpoint["token"]}).status_code == 401
            assert client.post(origin + "/api/shutdown", headers={"Authorization": "Bearer " + handshake["token"]}).status_code == 200
        assert child.wait(timeout=10) == 0
        assert not (directory / ENDPOINT_FILE).exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
