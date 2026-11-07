#!/usr/bin/env python3
"""Check a frozen engine's startup/authentication/state/shutdown without publishing."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request


def request(port: int, path: str, token: str | None = None, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers, method=method, data=data)
    # Local transport must never follow a redirect or inherit HTTP proxy settings.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=15) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise RuntimeError("State response exceeded size limit")
            return response.status, json.loads(data)
    except urllib.error.HTTPError as error:
        return error.code, {}


def check_worker(binary: Path, environment: dict[str, str], directory: Path,
                 platform_id: str = "__packaging_unsupported__", expected_status: str = "needs_attention") -> None:
    workspace = directory / "work" / f"job-offline-smoke-{platform_id}"
    workspace.mkdir(parents=True)
    payload = {
        "operation": "check", "parent_pid": os.getpid(), "workspace": str(workspace),
        "session_path": str(workspace / "session.json"), "browser_path": None,
        "payload": {"platform_id": platform_id, "account_id": "打包检查",
                    "kind": "video", "mode": "publish", "title": "离线工作进程检查"},
    }
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    worker = subprocess.Popen(
        [str(binary), "--worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=environment, **options,
    )
    payload["worker_group_id"] = worker.pid if os.name != "nt" else None
    try:
        output, _ = worker.communicate((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"), timeout=45)
        try:
            events = [json.loads(line) for line in output.decode("utf-8").splitlines() if line]
        except (ValueError, UnicodeError):
            raise RuntimeError("Frozen worker returned invalid UTF-8 protocol data") from None
        if worker.returncode != 0 or not events or events[-1].get("event") != "result" or events[-1].get("status") != expected_status:
            raise RuntimeError("Frozen offline worker failed its ownership/protocol check")
    finally:
        if worker.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(worker.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            else:
                try:
                    os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            worker.kill()
            worker.wait(timeout=10)


def check_mcp(binary: Path, environment: dict[str, str], directory: Path, port: int, token: str) -> None:
    """Exercise the frozen stdio bridge against this running desktop database."""
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    bridge = subprocess.Popen([str(binary), "--mcp", "--data-dir", str(directory)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=environment, **options)
    responses: queue.Queue[bytes] = queue.Queue()

    def read_lines():
        while True:
            line = bridge.stdout.readline(1_000_001)
            responses.put(line)
            if not line:
                return

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()

    def send(value):
        bridge.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        bridge.stdin.flush()

    def call(identifier, method, params):
        send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        try:
            line = responses.get(timeout=45)
            response = json.loads(line)
        except (queue.Empty, ValueError, UnicodeError):
            raise RuntimeError("Frozen MCP bridge returned no valid protocol response") from None
        if len(line) > 1_000_000 or response.get("id") != identifier or response.get("error") or "result" not in response:
            raise RuntimeError("Frozen MCP bridge failed its protocol check")
        return response["result"]

    def tool(identifier, name, arguments):
        result = call(identifier, "tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") or not isinstance(result.get("structuredContent"), dict):
            raise RuntimeError("Frozen MCP bridge could not call its desktop tool")
        return result["structuredContent"]

    try:
        initialized = call(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "offline-package-smoke", "version": "1.0"}})
        if initialized.get("protocolVersion") != "2025-06-18":
            raise RuntimeError("Frozen MCP protocol negotiation failed")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = call(2, "tools/list", {})
        if not {"save_content", "publish", "list_accounts", "preview_article"} <= {item["name"] for item in listed.get("tools", [])}:
            raise RuntimeError("Frozen MCP tools are missing")
        capabilities = tool(3, "list_platforms", {"kind": "article"})
        if not capabilities.get("platforms") or any("article" not in p["kinds"] for p in capabilities["platforms"]):
            raise RuntimeError("Frozen MCP returned incompatible article platforms")
        saved = tool(4, "save_content", {"content": {"kind": "article", "title": "MCP 本地草稿检查",
                    "body": "## 中文文章\n\n仅保存本地，不发布。", "articleTemplateId": "journal"}})["content"]
        status, state = request(port, "/api/state", token)
        match = next((item for item in state.get("contents", []) if item.get("id") == saved.get("id")), None)
        if status != 200 or not match or match.get("articleTemplateId") != "journal":
            raise RuntimeError("MCP did not use the desktop's shared content library")
        preview = tool(5, "preview_article", {"body": saved["body"], "articleTemplateId": "journal", "platformId": "wechat"})
        if "中文文章" not in preview.get("html", "") or "style=" not in preview["html"]:
            raise RuntimeError("Frozen MCP article formatting failed")
        wenyan = tool(6, "preview_article", {"body": saved["body"], "articleTemplateId": "wenyan-phycat", "platformId": "wechat"})
        if "中文文章" not in wenyan.get("html", "") or 'data-provider="WenYan"' not in wenyan["html"] or "style=" not in wenyan["html"]:
            raise RuntimeError("Frozen MCP Wenyan rendering failed")
        status, deleted = request(port, f"/api/contents/{saved['id']}", token, "DELETE")
        if status != 200 or not deleted.get("ok"):
            raise RuntimeError("MCP smoke draft cleanup failed")
        bridge.stdin.close()
        bridge.wait(timeout=15)
        if bridge.returncode != 0:
            raise RuntimeError("Frozen MCP bridge did not close cleanly")
    finally:
        if bridge.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(bridge.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            else:
                try:
                    os.killpg(bridge.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if bridge.poll() is None:
                bridge.kill()
            bridge.wait(timeout=10)
        if not bridge.stdin.closed:
            bridge.stdin.close()
        reader.join(timeout=2)
        bridge.stdout.close()


def verify(binary: Path, timeout: int, expected_version: str, clean_environment: bool = False) -> None:
    if not binary.is_file():
        raise RuntimeError("Embedded engine binary does not exist")
    # Check the exact sibling shipped alongside this engine, never an executable on PATH.
    opencode = binary.with_name(binary.name.replace("easy-publish-engine", "easy-publish-opencode", 1))
    if opencode == binary or not opencode.is_file() or opencode.is_symlink():
        raise RuntimeError("Bundled OpenCode sibling is missing or invalid")
    spec = importlib.util.spec_from_file_location("prepare_opencode", Path(__file__).with_name("prepare-opencode.py"))
    preparation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preparation)
    manifest, _ = preparation.pinned_target(preparation.native_target())
    preparation.verify_architecture(opencode, preparation.native_target())
    preparation.smoke_version(opencode, manifest["version"])
    preparation.smoke_server(opencode, manifest["version"])
    with tempfile.TemporaryDirectory(prefix="easy-publish-engine-smoke-") as directory:
        environment = os.environ.copy()
        if clean_environment:
            environment = {key: value for key, value in environment.items() if key in {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE"}}
            environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        # Smoke checks run without developer import paths or an installed Python dependency set.
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.pop("EASY_PUBLISH_TESTING", None)
        environment.pop("EASY_PUBLISH_TEST_KEY", None)
        environment["EASY_PUBLISH_OPENCODE_PATH"] = str(opencode)
        child = subprocess.Popen(
            [str(binary), "--data-dir", directory, "--parent-pid", str(os.getpid())], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=environment,
        )
        ready: queue.Queue[bytes] = queue.Queue(maxsize=1)
        thread = threading.Thread(target=lambda: ready.put(child.stdout.readline(65_537)), daemon=True)
        thread.start()
        port = 0
        token = ""
        try:
            try:
                line = ready.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("Embedded engine did not start before the timeout") from None
            if len(line) > 65_536 or not line:
                raise RuntimeError("Embedded engine returned no valid readiness message")
            try:
                handshake = json.loads(line)
                port, token = handshake["port"], handshake["token"]
            except (ValueError, KeyError, TypeError):
                raise RuntimeError("Embedded engine returned an invalid readiness message") from None
            if handshake.get("event") != "ready" or type(port) is not int or not 1 <= port <= 65_535 or not isinstance(token, str) or len(token) < 24:
                raise RuntimeError("Embedded engine readiness fields are invalid")
            status, _ = request(port, "/api/state")
            if status not in (401, 403):
                raise RuntimeError("Engine allowed a request without authentication")
            status, state = request(port, "/api/state", token)
            if status != 200:
                raise RuntimeError("Authenticated state request failed")
            for field in ("platforms", "accounts", "contents", "batches"):
                if not isinstance(state.get(field), list):
                    raise RuntimeError(f"State is missing its {field} list")
            if not state["platforms"] or state.get("runtime", {}).get("version") != expected_version:
                raise RuntimeError("Platform catalog or engine version did not match")
            if state["runtime"].get("vaultReady") is not True:
                raise RuntimeError("App-owned account storage did not start without a system keyring")
            key_path = Path(directory) / "vault-local-v1/master.key"
            if not key_path.is_file() or key_path.stat().st_size != 32:
                raise RuntimeError("App-owned encryption key is missing or invalid")
            # Saving dummy credentials stays offline: do not connect or publish.
            fixture_secret = "offline-package-check-not-a-real-secret"
            status, account = request(port, "/api/accounts", token, "POST", {
                "platformId": "wechat", "label": "离线存储检查",
                "credentials": {"appId": "offline-smoke", "appSecret": fixture_secret},
            })
            if status != 200 or not account.get("credentialConfigured") or not isinstance(account.get("id"), str):
                raise RuntimeError("Frozen engine could not store app-owned account credentials")
            encrypted_files = list(key_path.parent.glob("*.enc"))
            if len(encrypted_files) != 1:
                raise RuntimeError("App-owned encrypted credential file is missing")
            encrypted = encrypted_files[0].read_bytes()
            if not encrypted.startswith(b"EPV1") or fixture_secret.encode() in encrypted or fixture_secret in json.dumps(account):
                raise RuntimeError("Credentials escaped the encrypted app-owned store")
            status, deleted_account = request(port, f"/api/accounts/{account['id']}", token, "DELETE")
            if status != 200 or not deleted_account.get("ok") or encrypted_files[0].exists():
                raise RuntimeError("Offline account cleanup failed")
            for platform_id in ("bilibili", "kuaishou"):
                platform = next((item for item in state["platforms"] if item.get("id") == platform_id), {})
                if platform.get("kinds") != ["video"] or platform.get("modes") != ["publish"]:
                    raise RuntimeError(f"Frozen engine is missing the {platform_id} video capability")
            if token in json.dumps(state):
                raise RuntimeError("Engine exposed its transport secret in public state")
            content = {
                "kind": "article", "title": "打包检查：文章与视频", "body": "# 中文草稿\n\n离线保存：你好，世界。",
                "description": "仅验证本地保存，不发送到任何平台。", "tags": ["测试", "中文"], "author": "验证",
                "videoAssetId": None, "coverAssetId": None, "imageAssetIds": [],
                "platformOptions": {"dayu": {"source": "个人观点"}},
            }
            status, created = request(port, "/api/contents", token, "POST", content)
            if status != 200 or not isinstance(created.get("id"), str):
                raise RuntimeError("Frozen engine could not save a local article")
            content_id = created["id"]
            status, state = request(port, "/api/state", token)
            saved = next((item for item in state.get("contents", []) if item.get("id") == content_id), {})
            if status != 200 or any(saved.get(key) != value for key, value in content.items()):
                raise RuntimeError("Local article UTF-8 fields or platform options did not persist exactly")
            status, deleted = request(port, f"/api/contents/{content_id}", token, "DELETE")
            if status != 200 or deleted.get("ok") is not True:
                raise RuntimeError("Frozen engine could not delete its local smoke article")
            status, state = request(port, "/api/state", token)
            if status != 200 or any(item.get("id") == content_id for item in state.get("contents", [])):
                raise RuntimeError("Deleted smoke article remained in state")
            check_worker(binary, environment, Path(directory))
            # No session means no network request. This exercises the bundled
            # Bilibili adapter and its dependencies through the real worker.
            check_worker(binary, environment, Path(directory), "bilibili", "login_required")
            check_worker(binary, environment, Path(directory), "kuaishou", "login_required")
            check_mcp(binary, environment, Path(directory), port, token)
            status, _ = request(port, "/api/shutdown", token, "POST")
            if status != 200:
                raise RuntimeError("Engine rejected graceful shutdown")
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Engine did not exit after graceful shutdown") from None
            if child.returncode != 0:
                raise RuntimeError("Engine exited unsuccessfully")
            if (Path(directory) / "mcp-endpoint.json").exists():
                raise RuntimeError("Desktop shutdown left an active MCP descriptor")
        finally:
            if child.poll() is None:
                if port and token:
                    try:
                        request(port, "/api/shutdown", token, "POST")
                    except Exception:
                        pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            child.stdout.close()
    print("Embedded engine smoke passed: bundled OpenCode version, startup, app-owned encrypted accounts, authentication, UTF-8 content, Bilibili/Kuaishou workers, stdio MCP and template preview, graceful shutdown.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--expected-version", default="0.1.8")
    parser.add_argument("--clean-environment", action="store_true", help="Use a minimal macOS application environment without developer runtime paths")
    args = parser.parse_args()
    try:
        verify(args.binary.resolve(), args.timeout, args.expected_version, args.clean_environment)
    except Exception as error:
        # Only our own fixed errors may be displayed; never dump a handshake or HTTP headers.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        parser.exit(1, f"Embedded engine smoke failed: {message}\n")


if __name__ == "__main__":
    main()
