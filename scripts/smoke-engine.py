#!/usr/bin/env python3
"""Check a frozen engine's startup/authentication/state/shutdown without publishing."""

from __future__ import annotations

import argparse
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


def check_worker(binary: Path, environment: dict[str, str], directory: Path) -> None:
    workspace = directory / "work" / "job-offline-smoke"
    workspace.mkdir(parents=True)
    payload = {
        "operation": "check", "parent_pid": os.getpid(), "workspace": str(workspace),
        "session_path": str(workspace / "session.json"), "browser_path": None,
        "payload": {"platform_id": "__packaging_unsupported__", "account_id": "打包检查",
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
        if worker.returncode != 0 or not events or events[-1].get("event") != "result" or events[-1].get("status") != "needs_attention":
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


def verify(binary: Path, timeout: int, expected_version: str) -> None:
    if not binary.is_file():
        raise RuntimeError("Embedded engine binary does not exist")
    with tempfile.TemporaryDirectory(prefix="easy-publish-engine-smoke-") as directory:
        environment = os.environ.copy()
        # Smoke checks run without developer import paths or an installed Python dependency set.
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.pop("EASY_PUBLISH_TESTING", None)
        environment.pop("EASY_PUBLISH_TEST_KEY", None)
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
            status, _ = request(port, "/api/shutdown", token, "POST")
            if status != 200:
                raise RuntimeError("Engine rejected graceful shutdown")
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Engine did not exit after graceful shutdown") from None
            if child.returncode != 0:
                raise RuntimeError("Engine exited unsuccessfully")
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
    print("Embedded engine smoke passed: startup, authentication, UTF-8 article save/delete, offline worker, graceful shutdown.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--expected-version", default="0.1.0")
    args = parser.parse_args()
    try:
        verify(args.binary.resolve(), args.timeout, args.expected_version)
    except Exception as error:
        # Only our own fixed errors may be displayed; never dump a handshake or HTTP headers.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        parser.exit(1, f"Embedded engine smoke failed: {message}\n")


if __name__ == "__main__":
    main()
