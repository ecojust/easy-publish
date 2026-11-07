from __future__ import annotations

import base64
import shutil
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from easy_publish.article_templates import ArticleTemplateId, render_article
from easy_publish.store import uid


class ContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["article", "video"]
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=500_000)
    inputFormat: Literal["plain", "markdown"] = "markdown"
    sourceText: str = Field(default="", max_length=500_000)
    description: str = Field(default="", max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    author: str = Field(default="", max_length=100)
    videoAssetId: str | None = None
    coverAssetId: str | None = None
    imageAssetIds: list[str] = Field(default_factory=list, max_length=100)
    platformOptions: dict[str, dict[str, str]] = Field(default_factory=dict)
    articleTemplateId: ArticleTemplateId = "clean"


def image_sources(rendered: str) -> list[str]:
    from html.parser import HTMLParser
    class Images(HTMLParser):
        sources = None
        def handle_starttag(self, tag, attrs):
            if tag == "img":
                self.sources.append(dict(attrs).get("src", ""))
    parser = Images()
    parser.sources = []
    parser.feed(rendered)
    return parser.sources


def import_asset(data_dir: Path, path: str, kind: str) -> dict:
    source = Path(path).expanduser()
    if not source.is_absolute() or not source.is_file():
        raise ValueError("请选择有效的本地文件")
    # Keep the copied file independent of subsequent edits to the original.
    size = source.stat().st_size
    suffix = source.suffix.lower()
    limit = {"image": 20 * 1024**2, "article": 2 * 1024**2, "video": 4 * 1024**3}[kind]
    if not size or size > limit:
        raise ValueError(f"文件为空或超出大小上限（{limit // 1024**2} MB）")
    text = None
    if kind == "image":
        if suffix not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("封面和正文图片请使用 JPG 或 PNG")
        with Image.open(source) as img:
            if img.format not in {"JPEG", "PNG"} or img.width * img.height > 40_000_000:
                raise ValueError("图片格式不支持或尺寸过大")
            img.verify()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
    elif kind == "article":
        if suffix not in {".md", ".markdown", ".txt"}:
            raise ValueError("文章导入支持 UTF-8 编码的 Markdown 或 TXT 文件")
        try:
            text = source.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("文章文件需要使用 UTF-8 编码") from None
        if len(text) > 500_000:
            raise ValueError("文章内容超出 50 万字上限")
        mime = "text/plain"
    else:
        if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
            raise ValueError("视频支持 MP4、MOV、M4V 或 WebM")
        with source.open("rb") as handle:
            header = handle.read(64)
        if not (b"ftyp" in header or header.startswith(b"\x1aE\xdf\xa3")):
            raise ValueError("文件不是可识别的视频容器")
        mime = "video/webm" if suffix == ".webm" else "video/mp4"
    identifier = uid()
    folder = data_dir / "assets"
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / (identifier + suffix)
    try:
        with source.open("rb") as src, target.open("xb") as dst:
            target.chmod(0o600)
            shutil.copyfileobj(src, dst, 1024 * 1024)
        if target.stat().st_size != size:
            raise ValueError("导入时原文件发生变化，请重新选择")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"id": identifier, "name": source.name, "kind": kind, "size": size, "mime": mime, "path": str(target), "text": text}


def preview_asset(row: dict) -> str:
    if row["kind"] != "image":
        raise ValueError("此素材不是图片")
    # A small preview keeps the IPC response bounded even for large originals.
    import io
    with Image.open(row["path"]) as img:
        img.thumbnail((1400, 1400))
        output = io.BytesIO()
        img.convert("RGB").save(output, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
