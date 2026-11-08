"""Single-operation worker with a durable parent-acknowledged mutation fence."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading

from .providers.base import BROWSER_PUBLISH_PLATFORMS, ProviderContext, ProviderResult, PublishPayload


def _parent_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        # A dead owner may remain a zombie briefly before its host reaps it.
        if sys.platform.startswith("linux"):
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return stat[0] != "Z"
        return True
    except (ProcessLookupError, PermissionError, FileNotFoundError):
        return False


def _start_parent_watchdog(parent_pid: int, workspace: Path, worker_group_id: int | None = None) -> threading.Event:
    """Do not let a detached publishing worker outlive its desktop engine."""
    # A one-file PyInstaller bootloader sits between the owning engine and the
    # Python worker. Monitor the supplied owner, not os.getppid().
    if parent_pid <= 0 or (os.name != "nt" and not _parent_running(parent_pid)):
        raise RuntimeError("Owning engine is no longer running")
    if os.name != "nt":
        if worker_group_id is not None and worker_group_id != os.getpgrp():
            raise RuntimeError("Worker does not belong to its assigned process group")
        if worker_group_id is None and os.getpgrp() == os.getpid():
            worker_group_id = os.getpid()
    stop = threading.Event()

    def terminate_owned_tree() -> None:
        # Paths originate from Runtime's private temporary directory. Never
        # recurse through a substituted symlink or an arbitrary input path.
        if workspace.name.startswith("job-") and workspace.parent.name == "work" and not workspace.is_symlink():
            shutil.rmtree(workspace, ignore_errors=True)
        if os.name == "nt":
            with suppress(Exception):
                subprocess.run(["taskkill", "/PID", str(os.getpid()), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
        elif worker_group_id is not None and os.getpgrp() == worker_group_id:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(worker_group_id, signal.SIGKILL)
        os._exit(71)

    def watch() -> None:
        handle = None
        kernel = None
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.WaitForSingleObject.restype = wintypes.DWORD
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
                if not handle:
                    terminate_owned_tree()
            while not stop.wait(0.5):
                dead = (kernel.WaitForSingleObject(handle, 0) != 0x102) if kernel else not _parent_running(parent_pid)
                if dead:
                    terminate_owned_tree()
        except Exception:
            # A broken watchdog cannot silently permit unattended publication.
            terminate_owned_tree()
        finally:
            if handle and kernel:
                kernel.CloseHandle(handle)

    threading.Thread(target=watch, name="engine-lifetime", daemon=True).start()
    return stop


def _payload(data: dict) -> PublishPayload:
    return PublishPayload(
        platform_id=str(data["platform_id"]), account_id=str(data["account_id"]),
        kind=data.get("kind", "article"), mode=data.get("mode", "publish"),
        title=str(data.get("title", "")), body_html=str(data.get("body_html", "")),
        description=str(data.get("description", "")), tags=list(data.get("tags", [])),
        author=str(data.get("author", "")),
        video_path=Path(data["video_path"]) if data.get("video_path") else None,
        cover_path=Path(data["cover_path"]) if data.get("cover_path") else None,
        images={key: Path(value) for key, value in data.get("images", {}).items()},
        credentials=dict(data.get("credentials", {})), options=dict(data.get("options", {})),
    )


def worker_main() -> int:
    # A duplicated pipe remains exclusively for protocol output. Redirect the
    # actual stdout/stderr descriptors too, so native and child-process logging
    # cannot contaminate NDJSON or expose upstream login/session diagnostics.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    submitted = False
    secret_values: list[str] = []
    watchdog: threading.Event | None = None

    def clean(message: object) -> str:
        text = str(message)[:4000]
        for secret in secret_values:
            if secret:
                text = text.replace(secret, "[已脱敏]")
        return text

    def write(event: dict) -> None:
        if "message" in event:
            event = {**event, "message": clean(event["message"])}
        protocol.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        protocol.flush()

    def emit(event: dict) -> None:
        nonlocal submitted
        name = event.get("event")
        if name not in {"progress", "submitting", "awaiting_user"}:
            return
        if name == "submitting":
            if submitted:
                return
            write({"event": name, "message": event.get("message", "正在向平台提交")})
            # Parent must durably persist the fence before sending this ACK.
            ack = sys.stdin.buffer.readline(4096).decode("utf-8")
            if not ack or json.loads(ack) != {"ack": "submitting"}:
                raise RuntimeError("Mutation fence not acknowledged")
            submitted = True
        elif name == "awaiting_user":
            if not submitted or operation != "publish" or payload.platform_id not in BROWSER_PUBLISH_PLATFORMS:
                raise RuntimeError("Unexpected manual publication handoff")
            write({"event": name, "message": event.get("message", "请在网页中手动发布")})
            ack = sys.stdin.buffer.readline(4096).decode("utf-8")
            if not ack or json.loads(ack) != {"ack": "awaiting_user"}:
                raise RuntimeError("Manual publication handoff not acknowledged")
        else:
            write({"event": name, "message": event.get("message", "")})

    try:
        raw = sys.stdin.buffer.readline(32 * 1024 * 1024).decode("utf-8")
        request = json.loads(raw)
        operation = request["operation"]
        if operation not in {"login", "check", "publish"}:
            raise ValueError("Unsupported worker operation")
        payload = _payload(request["payload"])
        secret_values = [str(value) for value in payload.credentials.values() if isinstance(value, (str, int))]
        workspace = Path(request["workspace"]).resolve()
        session = Path(request["session_path"]).resolve()
        if session.parent != workspace:
            raise ValueError("Session must belong to worker workspace")
        watchdog = _start_parent_watchdog(int(request["parent_pid"]), workspace, request.get("worker_group_id"))
        if os.name != "nt":
            os.umask(0o077)  # Browser-created session files remain private too.
        context = ProviderContext(session, workspace, request.get("browser_path"), emit)
        if payload.platform_id in {"wechat", "wechat_official", "wechat_official_account"}:
            from .providers import wechat as provider
        elif payload.platform_id == "bilibili":
            from .providers import bilibili as provider
        elif payload.platform_id == "kuaishou":
            from .providers import kuaishou as provider
        else:
            from .providers import social as provider
        result = asyncio.run(getattr(provider, operation)(payload, context))
        if not isinstance(result, ProviderResult):
            raise TypeError("Invalid provider result")
        response = asdict(result)
        response["message"] = clean(response["message"])
        # A mutating operation cannot become a retryable failure merely because
        # a later status check failed. Definitive successes remain unchanged.
        if submitted and response["status"] in {"failed", "login_required", "needs_attention"}:
            response["status"] = "unknown"
        write({"event": "result", **response})
        return 0
    except Exception:
        write({"event": "result", "status": "unknown" if submitted else "failed", "message": "平台操作未获得完整结果；请先核对平台内容" if submitted else "发布引擎未能完成操作，请检查运行环境和账号后重试", "remote_id": None, "url": None})
        return 1
    finally:
        if watchdog:
            watchdog.set()
        protocol.close()
