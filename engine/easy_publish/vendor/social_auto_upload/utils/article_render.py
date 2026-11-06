"""Host HTML loader, replacing the upstream optional Node.js Markdown renderer.

The editor supplies sanitized HTML and local asset paths. Markdown conversion
belongs to the host; platform adapters never download runtimes or execute npm.
"""
from pathlib import Path

from .article_content import ArticleDocument

ARTICLE_MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def load_article(content_file: str | Path, theme: str | None = None) -> ArticleDocument:
    path = Path(content_file).resolve()
    if path.suffix.lower() not in {".html", ".htm"} or theme is not None:
        raise ValueError("发布器接收应用编辑器生成的 HTML 正文")
    return ArticleDocument(path.read_text(encoding="utf-8"), path.parent)
