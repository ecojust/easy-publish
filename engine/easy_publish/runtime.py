"""Browser preparation, encrypted sessions and supervised provider processes."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

from .vault import Vault


RESULT_STATUSES = {"ready", "login_required", "draft_created", "submitted", "published", "needs_attention", "failed", "unknown"}
SUCCESSES = {"ready", "draft_created", "submitted", "published"}
OPERATION_TIMEOUTS = {"login": 12 * 60, "check": 3 * 60, "publish": 35 * 60}
MAX_SESSION_BYTES = 16 * 1024 * 1024


def _result(status: str, message: str) -> dict:
    return {"status": status, "message": message, "remote_id": None, "url": None}


def _process_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate only this worker's independently owned process group/tree."""
    if os.name == "nt":
        if process.returncode is None:
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            with suppress(Exception):
                await asyncio.wait_for(killer.wait(), 10)
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), 2)
        # A browser child can survive after its Python parent exits.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(Exception):
        await asyncio.wait_for(process.wait(), 5)


class Runtime:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # The host holds its per-data-directory InstanceLock before creating
        # Runtime, so only interrupted jobs from this application are removed.
        self._purge_stale_workspaces()
        self.vault = Vault(self.data_dir)
        self.browser_root = self.data_dir / "browser-runtime"
        self.browser_path = self._find_browser()
        self._browser_message = "发布浏览器已准备好" if self.browser_path else "请先准备发布浏览器"
        self._install_task: asyncio.Task | None = None
        self._install_process: asyncio.subprocess.Process | None = None
        self._jobs: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    def _purge_stale_workspaces(self) -> None:
        work_root = self.data_dir / "work"
        if work_root.is_symlink():
            raise RuntimeError("任务工作目录不能是符号链接")
        if not work_root.is_dir():
            return
        for entry in work_root.iterdir():
            if not entry.name.startswith("job-"):
                continue
            if entry.is_symlink():
                entry.unlink()  # Remove the link; never follow its target.
            elif entry.is_dir():
                shutil.rmtree(entry)

    @staticmethod
    def _session_bytes(path: Path) -> bytes:
        if path.is_symlink() or path.stat().st_size > MAX_SESSION_BYTES:
            raise ValueError("Invalid session file")
        data = path.read_bytes()
        if len(data) > MAX_SESSION_BYTES or not isinstance(json.loads(data), dict):
            raise ValueError("Session must be a bounded JSON object")
        return data

    def _find_browser(self) -> str | None:
        candidates: list[Path] = []
        if sys.platform == "darwin":
            for base in (Path("/Applications"), Path.home() / "Applications"):
                candidates.extend((base / "Google Chrome.app/Contents/MacOS/Google Chrome", base / "Chromium.app/Contents/MacOS/Chromium", base / "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"))
        elif os.name == "nt":
            for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                root = os.environ.get(variable)
                if root:
                    candidates.extend((Path(root) / "Google/Chrome/Application/chrome.exe", Path(root) / "Microsoft/Edge/Application/msedge.exe"))
        for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge"):
            found = shutil.which(executable)
            if found:
                candidates.append(Path(found))
        for candidate in candidates:
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return str(candidate)
        if self.browser_root.exists():
            patterns = (
                "chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome",
                "chromium-*/chrome-win/chrome.exe", "chromium-*/chrome-win64/chrome.exe",
                "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                "chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                "chromium-*/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-mac-x64/Chromium.app/Contents/MacOS/Chromium",
            )
            for pattern in patterns:
                for candidate in sorted(self.browser_root.glob(pattern), reverse=True):
                    if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                        return str(candidate)
        return None

    def state(self) -> dict:
        return {"browserReady": self.browser_path is not None, "browserInstalling": self._install_task is not None and not self._install_task.done(), "browserMessage": self._browser_message, "vaultReady": self.vault.ready, "vaultMessage": self.vault.message, "version": "0.1.0"}

    async def install_browser(self) -> None:
        if self._closed or (self._install_task and not self._install_task.done()):
            return
        self.browser_path = self._find_browser()
        if self.browser_path:
            self._browser_message = "已找到可用的本机发布浏览器"
            return
        self._browser_message = "正在下载发布浏览器，请保持网络连接"
        self._install_task = asyncio.create_task(self._install_browser())

    async def _install_browser(self) -> None:
        try:
            from patchright._impl._driver import compute_driver_executable
            node, cli = compute_driver_executable()
            self.browser_root.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ)
            environment["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browser_root)
            environment.pop("EASY_PUBLISH_TEST_KEY", None)
            self._install_process = await asyncio.create_subprocess_exec(str(node), str(cli), "install", "chromium", env=environment, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **_process_options())
            code = await asyncio.wait_for(self._install_process.wait(), 15 * 60)
            self.browser_path = self._find_browser()
            if code or not self.browser_path:
                raise RuntimeError("Browser installation incomplete")
            self._browser_message = "发布浏览器已准备好"
        except asyncio.CancelledError:
            self._browser_message = "浏览器下载已取消，可稍后重新准备"
            raise
        except Exception:
            self._browser_message = "发布浏览器准备失败，请检查网络后重试，或安装 Chrome／Edge"
        finally:
            if self._install_process:
                await _stop_process(self._install_process)
                self._install_process = None

    def _worker_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--worker"]
        return [sys.executable, "-m", "easy_publish", "--worker"]

    async def run(self, operation: str, payload: dict, on_event: Callable[[dict], Awaitable[None]], job_id: str) -> dict:
        if operation not in OPERATION_TIMEOUTS:
            return _result("failed", "发布引擎不支持此操作")
        if self._closed:
            return _result("failed", "发布引擎正在关闭")
        if not self.vault.ready:
            return _result("needs_attention", self.vault.message)
        account_id = payload.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            return _result("failed", "缺少账号标识")
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            if self._closed:
                return _result("failed", "发布引擎正在关闭")
            if job_id in self._cancelled:
                self._cancelled.discard(job_id)
                return _result("failed", "操作已取消，尚未开始平台操作")
            return await self._run_locked(operation, payload, on_event, job_id)

    async def _run_locked(self, operation: str, payload: dict, on_event: Callable[[dict], Awaitable[None]], job_id: str) -> dict:
        submitted = False
        process: asyncio.subprocess.Process | None = None
        temporary: tempfile.TemporaryDirectory | None = None
        result: dict | None = None
        try:
            if job_id in self._jobs:
                return _result("failed", "此任务已在运行")
            account_id = payload["account_id"]
            credentials = self.vault.get(account_id, "credentials")
            session_data = self.vault.get(account_id, "session")
            request_payload = dict(payload)
            request_payload["credentials"] = json.loads(credentials) if credentials else {}
            work_root = self.data_dir / "work"
            work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = tempfile.TemporaryDirectory(prefix="job-", dir=work_root)
            workspace = Path(temporary.name)
            if os.name != "nt":
                workspace.chmod(0o700)
            session = workspace / "session.json"
            if session_data is not None:
                if len(session_data) > MAX_SESSION_BYTES or not isinstance(json.loads(session_data), dict):
                    raise ValueError("Invalid stored session")
                fd = os.open(session, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(session_data)
            environment = dict(os.environ)
            environment.pop("EASY_PUBLISH_TEST_KEY", None)
            environment["PLAYWRIGHT_BROWSERS_PATH"] = str(self.browser_root)
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            process = await asyncio.create_subprocess_exec(*self._worker_command(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=environment, **_process_options())
            self._jobs[job_id] = process
            request = {"operation": operation, "payload": request_payload, "workspace": str(workspace), "session_path": str(session), "browser_path": self.browser_path, "parent_pid": os.getpid(), "worker_group_id": process.pid if os.name != "nt" else None}
            process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()
            async with asyncio.timeout(OPERATION_TIMEOUTS[operation]):
                while line := await process.stdout.readline():
                    event = json.loads(line)
                    name = event.get("event")
                    if name == "submitting":
                        # Once this event arrives, any interruption is treated
                        # conservatively; no ACK is sent before durable commit.
                        submitted = True
                        await on_event({"event": "submitting", "message": str(event.get("message", "正在向平台提交"))[:4000]})
                        process.stdin.write(b'{"ack":"submitting"}\n')
                        await process.stdin.drain()
                    elif name == "progress":
                        await on_event({"event": "progress", "message": str(event.get("message", ""))[:4000]})
                    elif name == "result":
                        if result is not None or event.get("status") not in RESULT_STATUSES:
                            raise ValueError("Invalid worker result")
                        result = {key: event.get(key) for key in ("status", "message", "remote_id", "url")}
                        result["message"] = str(result.get("message", ""))[:4000]
                    else:
                        raise ValueError("Unexpected worker event")
                await process.wait()
            if result is None:
                raise RuntimeError("Worker exited without result")
            if submitted and result["status"] in {"failed", "login_required", "needs_attention"}:
                result["status"] = "unknown"
            if result["status"] in SUCCESSES and session.is_file():
                try:
                    self.vault.put(account_id, "session", self._session_bytes(session))
                except Exception:
                    if operation in {"login", "check"}:
                        return _result("needs_attention", "平台登录已完成，但加密会话保存失败；请检查系统凭据服务后重新连接")
                    # A vault issue cannot invalidate a confirmed publication.
                    await on_event({"event": "progress", "message": "平台结果已确认，但更新登录会话失败；下次使用前请检查账号"})
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            if submitted:
                return _result("unknown", "操作已中断或超时，可能已产生平台内容；请先核对原作品，勿直接重试")
            return _result("failed", "操作未完成且未开始内容提交；请检查浏览器、账号与网络后重试")
        finally:
            if process:
                await _stop_process(process)
            self._jobs.pop(job_id, None)
            self._cancelled.discard(job_id)
            if temporary:
                temporary.cleanup()

    async def cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)
        process = self._jobs.get(job_id)
        if process:
            await _stop_process(process)

    async def shutdown(self) -> None:
        self._closed = True
        await asyncio.gather(*(self.cancel(job_id) for job_id in list(self._jobs)), return_exceptions=True)
        if self._install_task and not self._install_task.done():
            self._install_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._install_task
