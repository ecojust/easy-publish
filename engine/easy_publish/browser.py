"""Browser process options for app-owned, temporary publishing sessions."""
from __future__ import annotations


def browser_launch_options(
    executable_path: str | None, *, headless: bool = False, channel: str | None = None,
) -> dict:
    # Chromium must not unlock the user's OS password store, even when launching
    # an installed Chrome/Edge executable. Patchright creates a fresh profile in
    # the worker's private temporary directory; only our saved storage_state is
    # restored into its isolated context. Keep these switches explicit instead
    # of depending on the automation driver's current defaults.
    options = {
        "headless": headless,
        "args": ["--password-store=basic", "--use-mock-keychain"],
    }
    if executable_path:
        options["executable_path"] = executable_path
    if channel:
        options["channel"] = channel
    return options
