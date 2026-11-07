"""Browser storage isolation, without real accounts or external network calls."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import pytest

from easy_publish.browser import browser_launch_options
from easy_publish.providers import bilibili, kuaishou, social_video
from easy_publish.providers.base import ProviderContext


@pytest.mark.parametrize("provider", ["bilibili", "kuaishou", "social_video"])
def test_video_browser_uses_app_session_without_os_password_store(tmp_path, monkeypatch, provider):
    import patchright.async_api

    page = SimpleNamespace(set_default_timeout=lambda value: None)
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    launch = AsyncMock(return_value=browser)

    class Driver:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(patchright.async_api, "async_playwright", Driver)
    session = tmp_path / "session.json"
    session.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    ctx = ProviderContext(session, tmp_path, "/app/browser", lambda event: None)
    saved_state = {"cookies": [], "origins": []}

    async def run():
        manager = {
            "bilibili": lambda: bilibili._browser(ctx),
            "kuaishou": lambda: kuaishou._browser(ctx, saved_state),
            "social_video": lambda: social_video._browser_page(ctx),
        }[provider]
        async with manager() as actual:
            assert actual == (context, page)

    asyncio.run(run())
    launch.assert_awaited_once_with(
        headless=False, executable_path="/app/browser",
        args=["--password-store=basic", "--use-mock-keychain"],
    )
    options = browser.new_context.call_args.kwargs
    if provider == "kuaishou":
        assert options["storage_state"] is saved_state
    elif provider == "social_video":
        assert options["storage_state"] == str(session)
    else:
        assert "storage_state" not in options
    browser.close.assert_awaited_once()


@pytest.mark.skipif(not os.environ.get("EASY_PUBLISH_CHROMIUM"), reason="Optional local Chromium fixture")
def test_real_chromium_uses_private_worker_profile_and_supplied_session(monkeypatch):
    from patchright.async_api import async_playwright

    # Chromium uses a Unix socket below TMPDIR, so keep the fixture root short.
    with TemporaryDirectory(prefix="epb-") as directory:
        workspace = Path(directory) / "work" / "job-browser-storage"
        workspace.mkdir(parents=True, mode=0o700)
        for variable in ("TMPDIR", "TEMP", "TMP"):
            monkeypatch.setenv(variable, str(workspace))

        async def run():
            async with async_playwright() as driver:
                options = browser_launch_options(os.environ["EASY_PUBLISH_CHROMIUM"], headless=True)
                # Sandbox is unavailable in the Linux test container; production
                # launch options continue to use the driver's sandbox policy.
                if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
                    options["args"].append("--no-sandbox")
                browser = await driver.chromium.launch(**options)
                try:
                    state = {"cookies": [{"name": "app_session", "value": "fixture",
                                          "domain": "app-session.invalid", "path": "/",
                                          "expires": -1, "httpOnly": True, "secure": True,
                                          "sameSite": "Lax"}], "origins": []}
                    context = await browser.new_context(storage_state=state)
                    await context.route("**/*", lambda route: route.abort())
                    page = await context.new_page()
                    await page.goto("chrome://version")
                    command = await page.locator("#command_line").inner_text()
                    profile = Path(await page.locator("#profile_path").inner_text()).resolve()
                    assert "--password-store=basic" in command
                    assert "--use-mock-keychain" in command
                    assert profile.is_relative_to(workspace.resolve())
                    cookies = await context.cookies()
                    assert [(cookie["name"], cookie["domain"]) for cookie in cookies] == [
                        ("app_session", "app-session.invalid")]
                    other_context = await browser.new_context()
                    assert await other_context.cookies() == []
                finally:
                    await browser.close()

        asyncio.run(run())
        assert not list(workspace.glob("*profile*"))
