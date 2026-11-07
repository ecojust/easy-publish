"""Offline Wenyan typography using the application's existing Node runtime.

One owned JSON-lines child serves repeated previews. Article text never enters
command arguments, a shell, logs, account storage, or a temporary source file.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import patchright


RENDER_TIMEOUT = 20
MAX_OUTPUT = 16 * 1024 * 1024
CACHE_BYTES = 32 * 1024 * 1024
CACHE_ENTRIES = 24


class WenyanError(RuntimeError):
    """Only a fixed, safe message; never expose renderer output or article text."""


def _runtime_paths() -> tuple[Path, Path]:
    # Do not use PLAYWRIGHT_NODEJS_PATH, PATH lookup, or a user-installed CLI.
    node = Path(patchright.__file__).resolve().parent / "driver" / ("node.exe" if os.name == "nt" else "node")
    script = Path(__file__).resolve().parent / "vendor" / "wenyan" / "render.cjs"
    if not script.is_file() and not getattr(sys, "frozen", False):
        script = Path(__file__).resolve().parents[2] / "runtime" / "wenyan" / "dist" / "render.cjs"
    if not node.is_file() or not script.is_file() or not (script.parent / "wenyan-cli.mjs").is_file():
        raise WenyanError("未找到内置 Wenyan 模板引擎，请安装完整版本的 Easy Publish。")
    return node, script


def _environment(directory: Path | None = None) -> dict[str, str]:
    # No NODE_OPTIONS, provider credentials, executable override or personal
    # configuration. The renderer has no network/account/publishing features.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["NODE_ENV"] = "production"
    if directory is not None:
        # Official CLI rendering is adapted to avoid config access entirely;
        # give it an empty owned home as an additional isolation boundary.
        environment.update({name: str(directory) for name in (
            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
        )})
    return environment


class WenyanRenderer:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: asyncio.Task | None = None
        self._operations: set[asyncio.Task] = set()
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = 0
        self._closed = False
        self._temporary: tempfile.TemporaryDirectory | None = None

    async def _start(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        await self._stop()
        node, script = _runtime_paths()
        self._temporary = tempfile.TemporaryDirectory(prefix="easy-publish-wenyan-")
        try:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            self._process = await asyncio.create_subprocess_exec(
                str(node), str(script), "--stdio", cwd=str(script.parent), env=_environment(Path(self._temporary.name)),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=MAX_OUTPUT + 1, **options,
            )
        except OSError:
            raise WenyanError("无法启动内置 Wenyan 模板引擎，请检查应用安装。") from None
        process = self._process

        async def drain_errors():
            # Drain without retaining text: JavaScript errors may include the
            # source article. A bounded reader also prevents pipe deadlocks.
            while await process.stderr.read(8192):
                pass

        self._stderr = asyncio.create_task(drain_errors())
        return process

    async def _stop(self):
        process, self._process = self._process, None
        reader, self._stderr = self._stderr, None
        if process is not None:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
            else:
                await process.wait()
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    async def render(self, body: str, theme: str) -> str:
        from .article_templates import WENYAN_TEMPLATE_IDS
        if f"wenyan-{theme}" not in WENYAN_TEMPLATE_IDS:
            raise WenyanError("Wenyan 模板不存在，请重新选择。")
        if not isinstance(body, str) or len(body) > 500_000:
            raise WenyanError("文章超出模板引擎的长度限制。")
        if self._closed:
            raise WenyanError("应用正在退出，请重新打开后预览。")
        if not body.strip():
            return ""
        key = hashlib.sha256((theme + "\0" + body).encode()).hexdigest()
        operation = asyncio.current_task()
        self._operations.add(operation)
        try:
            async with self._lock:
                if self._closed:
                    raise WenyanError("应用正在退出，请重新打开后预览。")
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
                try:
                    async with asyncio.timeout(RENDER_TIMEOUT):
                        process = await self._start()
                        request = json.dumps({"body": body, "theme": theme}, ensure_ascii=False, separators=(",", ":"))
                        process.stdin.write(request.encode() + b"\n")
                        await process.stdin.drain()
                        line = await process.stdout.readline()
                        if len(line) > MAX_OUTPUT:
                            raise WenyanError("文章排版结果过大，请缩短文章后重试。")
                        value = json.loads(line)
                        if not isinstance(value, dict) or not isinstance(value.get("html"), str):
                            raise WenyanError("Wenyan 文章排版未完成，请检查正文后重试。")
                        html = value["html"]
                        if not html.strip():
                            raise WenyanError("Wenyan 文章排版结果为空，请检查正文。")
                except asyncio.CancelledError:
                    await self._stop()
                    raise
                except asyncio.TimeoutError:
                    await self._stop()
                    raise WenyanError("Wenyan 文章排版超时，请重试；原文已保留。") from None
                except WenyanError:
                    await self._stop()
                    raise
                except (OSError, ValueError, UnicodeError, RuntimeError):
                    await self._stop()
                    raise WenyanError("Wenyan 模板引擎未返回有效结果，请重试。") from None
                size = len(html.encode())
                while self._cache and (len(self._cache) >= CACHE_ENTRIES or self._cache_size + size > CACHE_BYTES):
                    _, previous = self._cache.popitem(last=False)
                    self._cache_size -= len(previous.encode())
                if size <= CACHE_BYTES:
                    self._cache[key] = html
                    self._cache_size += size
                return html
        finally:
            self._operations.discard(operation)

    async def close(self):
        self._closed = True
        tasks = [task for task in self._operations if task is not asyncio.current_task() and not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._stop()
        self._cache.clear()
        self._cache_size = 0
