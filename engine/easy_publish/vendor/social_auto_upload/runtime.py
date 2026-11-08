"""Per-worker host hooks; never discover a system browser or global cookie folder."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

from easy_publish.browser import browser_launch_options

# A worker serves one operation. ContextVar still prevents nested test sessions
# from sharing mutation callbacks.
browser_path: str | None = None
_mutation_hook: ContextVar[Callable[[], None] | None] = ContextVar("mutation_hook", default=None)
_manual_hook: ContextVar[Callable[[str], None] | None] = ContextVar("manual_hook", default=None)
_manual_active: ContextVar[bool] = ContextVar("manual_active", default=False)


def configure(path: str | None, hook: Callable[[], None], manual_hook: Callable[[str], None] | None = None) -> None:
    global browser_path
    browser_path = path
    _mutation_hook.set(hook)
    _manual_hook.set(manual_hook)
    _manual_active.set(False)


def before_mutation() -> None:
    hook = _mutation_hook.get()
    if hook is None:
        raise RuntimeError("Publisher worker has not configured its mutation supervisor")
    hook()


def awaiting_user(message: str) -> None:
    """Hand the prepared page to its owner; never substitute a submit click."""
    hook = _manual_hook.get()
    if hook is None:
        raise RuntimeError("Publisher worker has not configured manual publication")
    hook(message)
    _manual_active.set(True)


async def keep_manual_window(page) -> None:
    """A receipt/validation error after handoff must not take away the editor.

    The owner may already have submitted, so this message must never instruct
    them to retry. Closing without a verified receipt stays unknown upstream.
    Cancellation still propagates to the owning worker and closes its browser.
    """
    if _manual_active.get() and page is not None and not page.is_closed():
        async with page.expect_event("close", timeout=0):
            awaiting_user("未能自动核验平台结果，网页会保持打开；请自行处理并核对作品，完成后关闭网页。若已提交，请勿重复点击发布")


def browser_options(*, headless: bool = False) -> dict:
    return browser_launch_options(browser_path, headless=headless, channel="chromium")
