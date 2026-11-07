"""Per-worker host hooks; never discover a system browser or global cookie folder."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

from easy_publish.browser import browser_launch_options

# A worker serves one operation. ContextVar still prevents nested test sessions
# from sharing mutation callbacks.
browser_path: str | None = None
_mutation_hook: ContextVar[Callable[[], None] | None] = ContextVar("mutation_hook", default=None)


def configure(path: str | None, hook: Callable[[], None]) -> None:
    global browser_path
    browser_path = path
    _mutation_hook.set(hook)


def before_mutation() -> None:
    hook = _mutation_hook.get()
    if hook is None:
        raise RuntimeError("Publisher worker has not configured its mutation supervisor")
    hook()


def browser_options(*, headless: bool = False) -> dict:
    return browser_launch_options(browser_path, headless=headless, channel="chromium")
