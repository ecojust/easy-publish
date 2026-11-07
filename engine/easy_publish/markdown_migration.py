"""One-time preservation of drafts from the retired plain-text article editor."""
from __future__ import annotations

import hashlib
import html
import json
import re


_IMAGE = re.compile(r"【图片([1-9][0-9]*)】")
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+.!|~=-])")


MAX_MARKDOWN = 500_000
_MARKDOWN_CHARACTERS = set(r"\`*_{}[]()#+.!|~=-")


def _escaped_length(text: str, remaining: int) -> int | None:
    """Measure before escaping: a long tab run must not allocate huge entities."""
    size, leading, column = 0, True, 0
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\n":
            size += 3
            leading, column = True, 0
        elif leading and char in " \t":
            width = 1 if char == " " else 4 - column % 4
            size += 5 * width
            column += width
        else:
            leading = False
            size += {"&": 5, "<": 4, ">": 4}.get(char, 2 if char in _MARKDOWN_CHARACTERS else 1)
        if size > remaining:
            return None
    return size


def _source_parts(source: str, image_ids: list[str]):
    offset = 0
    for match in _IMAGE.finditer(source):
        if len(match[1]) > len(str(len(image_ids))):
            continue
        index = int(match[1]) - 1
        identifier = image_ids[index] if index < len(image_ids) else None
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            continue
        yield source[offset:match.start()], False
        yield f"![正文图片 {index + 1}](easy-asset://{identifier})", True
        offset = match.end()
    yield source[offset:], False


def _unescaped_fallback(source: str, image_ids: list[str]) -> str:
    # Some legal old drafts expand beyond the current Markdown limit. Preserve
    # all text as Markdown instead of truncating it or blocking the library.
    # Keep image mappings only if the complete mapped text also fits. HTML is
    # still sanitized by the shared preview/publication renderer.
    parts, size = [], 0
    for text, _ in _source_parts(source, image_ids):
        size += len(text)
        if size > MAX_MARKDOWN:
            return source
        parts.append(text)
    return "".join(parts)


def literal_markdown(source: str, image_ids: list[str]) -> str:
    """Prefer literal prose; preserve raw Markdown if escaping exceeds 500k."""
    def escaped(text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = html.escape(text, quote=False)
        text = _MARKDOWN.sub(r"\\\1", text)
        text = re.sub(r"(?m)^[ \t]+", lambda match: "&#32;" * len(match[0].expandtabs(4)), text)
        return text.replace("\n", "  \n")

    result, size = [], 0
    for text, image in _source_parts(source, image_ids):
        length = len(text) if image else _escaped_length(text, MAX_MARKDOWN - size)
        if length is None or size + length > MAX_MARKDOWN:
            return _unescaped_fallback(source, image_ids)
        size += length
        result.append(text if image else escaped(text))
    return "".join(result)


def migrate_article(previous: dict) -> dict:
    if previous.get("kind") != "article" or previous.get("inputFormat") != "plain":
        return previous
    value = dict(previous)
    source = previous.get("sourceText", "")
    images = previous.get("imageAssetIds", [])
    fingerprint = hashlib.sha256(json.dumps([source, images], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    generated = previous.get("body", "")
    valid = isinstance(generated, str) and bool(generated.strip()) and previous.get("formatSourceHash") == fingerprint
    value["legacyFormatting"] = {key: previous[key] for key in ("inputFormat", "sourceText", "body", "imageAssetIds", "formatSourceHash", "formatModel") if key in previous}
    value["inputFormat"] = "markdown"
    value["body"] = generated if valid else literal_markdown(source, images)
    for key in ("formatReady", "formatSourceHash", "formatModel"):
        value.pop(key, None)
    return value
