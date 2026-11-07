"""Select the fields a target consumes without modifying the shared draft."""
from __future__ import annotations

from dataclasses import replace

from .catalog import BY_ID
from .providers.base import PublishPayload


ALIASES = {"tencent": "channels", "wechat_channels": "channels", "xhs": "xiaohongshu",
           "wechat_official": "wechat", "wechat_official_account": "wechat"}
COVER_PLATFORMS = {"wechat", "xueqiu", "kuaishou", "xiaohongshu", "channels", "bilibili"}
AUTHOR_PLATFORMS = {"wechat", "dayu"}


def target_options(platform_id: str, options: dict) -> dict:
    platform_id = ALIASES.get(platform_id, platform_id)
    allowed = {field["key"] for field in BY_ID.get(platform_id, {}).get("fields", [])}
    if platform_id == "dayu":
        allowed.add("cover_indices")  # Used by the article adapter, including worker fixtures.
    selected = {key: value for key, value in options.items() if key in allowed}
    if platform_id == "bilibili" and selected.get("copyright") != "2":
        selected.pop("source", None)
    if platform_id == "dayu" and selected.get("source") != "转载":
        selected.pop("source_url", None)
    return selected


def project_content(content: dict, platform_id: str) -> dict:
    """Apply before asset lookup, validation or conversion for a queue target."""
    platform_id = ALIASES.get(platform_id, platform_id)
    selected = dict(content)
    selected["platformOptions"] = {platform_id: target_options(platform_id, content.get("platformOptions", {}).get(platform_id, {}))}
    if content["kind"] == "video":
        selected.update(body="", imageAssetIds=[], author="")
    else:
        selected["videoAssetId"] = None
        selected["tags"] = []
        if platform_id != "wechat":
            selected["description"] = ""
        if platform_id not in AUTHOR_PLATFORMS:
            selected["author"] = ""
    if platform_id not in COVER_PLATFORMS:
        selected["coverAssetId"] = None
    return selected


def project_payload(payload: PublishPayload, platform_id: str | None = None) -> PublishPayload:
    """Keep direct adapter calls consistent with the service's target payloads."""
    platform_id = ALIASES.get(platform_id or payload.platform_id, platform_id or payload.platform_id)
    changes = {"options": target_options(platform_id, payload.options)}
    if payload.kind == "video":
        changes.update(body_html="", images={}, author="")
    else:
        changes.update(video_path=None, tags=[])
        if platform_id != "wechat":
            changes["description"] = ""
        if platform_id not in AUTHOR_PLATFORMS:
            changes["author"] = ""
    if platform_id not in COVER_PLATFORMS:
        changes["cover_path"] = None
    return replace(payload, **changes)
