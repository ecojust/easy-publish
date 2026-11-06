"""Shared boundary between the supervised worker and platform adapters."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

ResultStatus = Literal["ready", "login_required", "draft_created", "submitted", "published", "needs_attention", "failed", "unknown"]

@dataclass
class ProviderResult:
    status: ResultStatus
    message: str
    remote_id: str | None = None
    url: str | None = None
    # Public capability data only; credentials remain in the encrypted session.
    metadata: dict = field(default_factory=dict)

@dataclass
class PublishPayload:
    platform_id: str
    account_id: str
    kind: Literal["article", "video"]
    mode: Literal["publish", "draft"]
    title: str
    body_html: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    video_path: Path | None = None
    cover_path: Path | None = None
    # Body references easy-asset://<id>; providers must replace using these local files.
    images: dict[str, Path] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict)
    options: dict = field(default_factory=dict)

@dataclass
class ProviderContext:
    session_path: Path
    workspace: Path
    browser_path: str | None
    emit: Callable[[dict], None]

    def progress(self, message: str) -> None:
        self.emit({"event": "progress", "message": message})

    def submitting(self, message: str = "正在向平台提交") -> None:
        # Must run BEFORE the first content-mutating request/click.
        self.emit({"event": "submitting", "message": message})
