"""Isolated social-auto-upload article integration and social video dispatcher.

No global cookie storage and no automatic retry of a remote content mutation.
Upstream provenance and MIT notices live in vendor/social_auto_upload.
"""
from __future__ import annotations

import importlib
import json
import re
import shutil
from contextlib import suppress
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from ..target_projection import project_payload
from .base import ProviderContext, ProviderResult, PublishPayload
from ..vendor.social_auto_upload import runtime

ARTICLE_PLATFORMS = {"zhihu", "dayu", "xueqiu"}
DAYU_SOURCES = ("无需标注", "AI生成", "虚构演绎", "营销信息", "转载", "个人观点", "不适宜未成年人")
XUEQIU_DECLARATIONS = ("不添加创作声明", "本文包含AI生成内容")


class _AssetHTML(HTMLParser):
    """Only imported asset references may become filesystem paths for upload."""

    def __init__(self, images: dict[str, Path], workspace: Path):
        super().__init__(convert_charrefs=False)
        self.images = images
        self.workspace = workspace
        self.parts: list[str] = []
        self.copied: dict[str, Path] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            values = dict(attrs)
            source = values.get("src") or ""
            if not source.startswith("easy-asset://"):
                raise ValueError("正文图片需先导入素材库，再插入文章")
            asset_id = source.removeprefix("easy-asset://")
            path = self.images.get(asset_id)
            if path is None:
                # Allow a host map keyed by the complete URI as well.
                path = self.images.get(source)
            if path is None or not Path(path).is_file():
                raise ValueError("正文引用的图片素材不存在，请重新导入")
            if asset_id not in self.copied:
                suffix = Path(path).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                    raise ValueError("正文图片格式不受支持")
                target = self.workspace / f"article-image-{len(self.copied)}{suffix}"
                shutil.copyfile(path, target)
                self.copied[asset_id] = target
            attrs = [(key, str(self.copied[asset_id]) if key == "src" else value) for key, value in attrs]
        rendered = "".join(f' {key}="{escape(value or "", quote=True)}"' for key, value in attrs)
        self.parts.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"img", "br", "hr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")


def _prepare_html(payload: PublishPayload, workspace: Path) -> Path:
    parser = _AssetHTML(payload.images, workspace)
    parser.feed(payload.body_html)
    parser.close()
    target = workspace / "article.html"
    target.write_text("".join(parser.parts), encoding="utf-8")
    return target


def _options(payload: PublishPayload) -> dict:
    value = getattr(payload, "options", {})
    return value if isinstance(value, dict) else {}


def _validate_options(payload: PublishPayload) -> None:
    payload = project_payload(payload)
    options = _options(payload)
    if payload.platform_id == "dayu":
        source = options.get("source")
        if source not in DAYU_SOURCES:
            raise ValueError("请为大鱼号选择真实的信息来源")
        source_url = options.get("source_url", "")
        if source == "转载":
            parsed = urlsplit(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("大鱼号转载内容需要填写原文链接")
        indices = options.get("cover_indices", [])
        if not isinstance(indices, list) or any(type(i) is not int for i in indices):
            raise ValueError("大鱼号封面序号必须是正文图片编号列表")
    elif payload.platform_id == "xueqiu":
        if options.get("declaration") not in XUEQIU_DECLARATIONS:
            raise ValueError("请为雪球选择真实的创作声明")
        if options.get("visibility", "公开") not in {"公开", "私人"}:
            raise ValueError("雪球分享范围只能是公开或私人")


def _module(platform: str, name: str):
    return importlib.import_module(f"easy_publish.vendor.social_auto_upload.uploader.{platform}_uploader.{name}")


def _unpack_session(ctx: ProviderContext) -> Path:
    folder = ctx.workspace / "social-session"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "session.json"
    if ctx.session_path.is_file():
        envelope = json.loads(ctx.session_path.read_text(encoding="utf-8"))
        if envelope.get("version") != 1 or not isinstance(envelope.get("identity"), dict):
            raise ValueError("账号会话缺少身份绑定，请重新登录")
        state = envelope.get("storage_state")
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            raise ValueError("账号会话已损坏，请重新登录")
        path.write_text(json.dumps(state), encoding="utf-8")
        path.with_suffix(".identity.json").write_text(json.dumps(envelope["identity"]), encoding="utf-8")
    return path


def _pack_session(ctx: ProviderContext, path: Path) -> None:
    identity = path.with_suffix(".identity.json")
    if not path.is_file() or not identity.is_file():
        return
    data = {"version": 1, "storage_state": json.loads(path.read_text(encoding="utf-8")),
            "identity": json.loads(identity.read_text(encoding="utf-8"))}
    ctx.session_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.session_path.write_text(json.dumps(data), encoding="utf-8")


async def login(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    if payload.platform_id not in ARTICLE_PLATFORMS:
        from . import social_video
        return await social_video.login(payload, ctx)
    runtime.configure(ctx.browser_path, lambda: None)
    try:
        path = _unpack_session(ctx)
    except (ValueError, OSError):
        return ProviderResult("login_required", "账号会话无法恢复，请移除此账号后重新添加")
    module = _module(payload.platform_id, "session")
    function = getattr(module, f"login_{payload.platform_id}_account")
    ctx.progress("请在打开的发布浏览器中登录，验证码直接在平台网页完成")
    try:
        result = await function(path, timeout=600)
        if not result.get("success"):
            return ProviderResult("login_required", "尚未完成平台登录，请重新连接")
        _pack_session(ctx, path)
        return ProviderResult("ready", "已登录并核对当前发布账号")
    except Exception:
        return ProviderResult("needs_attention", "平台登录未完成或账号身份不一致，请在浏览器中确认后重新连接")


async def check(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    if payload.platform_id not in ARTICLE_PLATFORMS:
        from . import social_video
        return await social_video.check(payload, ctx)
    if not ctx.session_path.is_file():
        return ProviderResult("login_required", "请先连接平台账号")
    runtime.configure(ctx.browser_path, lambda: None)
    try:
        path = _unpack_session(ctx)
        function = getattr(_module(payload.platform_id, "session"), f"check_{payload.platform_id}_account")
        valid = await function(path)
        _pack_session(ctx, path)
        return ProviderResult("ready" if valid else "login_required", "账号登录有效" if valid else "登录已失效，请重新连接")
    except Exception:
        return ProviderResult("needs_attention", "无法确认账号状态，请检查网络或在发布浏览器中重新连接")


def _map_receipt(platform: str, result: dict, mode: str) -> ProviderResult:
    """A process exit or a prepared editor is never a publication receipt."""
    status = result.get("status")
    remote_id = str(result.get("content_id") or "") or None
    url = result.get("url") or result.get("public_url")
    if status == "prepared":
        if platform == "xueqiu" and mode == "draft" and result.get("success"):
            # The Xueqiu pipeline explicitly verifies the remote autosaved-draft label.
            return ProviderResult("draft_created", "平台已确认保存草稿", remote_id=remote_id)
        return ProviderResult("needs_attention", "编辑器已填写，但尚未取得平台草稿保存回执")
    if not result.get("success"):
        return ProviderResult("unknown", "平台尚未确认本次提交，请到平台核对，避免重复发布", remote_id=remote_id)
    if platform == "zhihu" and status == "published" and isinstance(url, str) and re.fullmatch(r"https://zhuanlan\.zhihu\.com/p/[1-9][0-9]*/?(?:\?.*)?", url):
        return ProviderResult("published", "已核对知乎作品详情页和标题", remote_id=urlsplit(url).path.strip("/").split("/")[-1], url=url)
    if platform == "dayu" and status in {"submitted", "published"} and remote_id:
        return ProviderResult(status, "平台已确认作品提交" if status == "submitted" else "平台已确认作品公开", remote_id=remote_id, url=url)
    if platform == "xueqiu" and status == "published" and remote_id and result.get("management_verified") and isinstance(url, str) and re.fullmatch(r"https://xueqiu\.com/[1-9][0-9]*/[1-9][0-9]*", url) and url.endswith("/" + remote_id):
        return ProviderResult("published", "平台已返回作品编号并核对已发表内容", remote_id=remote_id, url=url)
    return ProviderResult("unknown", "尚未取得可靠的作品回执，请到平台核对，避免重复发布", remote_id=remote_id)


async def publish(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    payload = project_payload(payload)
    if payload.platform_id not in ARTICLE_PLATFORMS:
        from . import social_video
        return await social_video.publish(payload, ctx)
    if payload.kind != "article":
        return ProviderResult("needs_attention", "此平台当前接入文章发布，视频驱动尚未启用")
    if payload.mode == "draft" and payload.platform_id != "xueqiu":
        return ProviderResult("needs_attention", "此平台暂未接入可核验的草稿保存，请使用正式发布")
    if not ctx.session_path.is_file():
        return ProviderResult("login_required", "请先连接平台账号")
    mutated = False

    def before_mutation():
        nonlocal mutated
        if not mutated:
            ctx.submitting("正在进入平台编辑器；平台可能自动保存草稿")
            mutated = True

    runtime.configure(ctx.browser_path, before_mutation)
    path = None
    try:
        _validate_options(payload)
        ctx.workspace.mkdir(parents=True, exist_ok=True)
        content_file = _prepare_html(payload, ctx.workspace)
        path = _unpack_session(ctx)
        options = _options(payload)
        module = _module(payload.platform_id, "article")
        common = dict(title=payload.title, content_file=content_file, account_file=path,
                      headless=False, debug=False, prepare_only=payload.mode == "draft")
        if payload.platform_id == "zhihu":
            app = module.ZhihuArticle(**common)
        elif payload.platform_id == "dayu":
            app = module.DayuArticle(**common, source=options["source"], source_url=options.get("source_url", ""),
                                    author=payload.author or None, cover_indices=options.get("cover_indices", []))
        else:
            app = module.XueqiuArticle(**common, declaration=options["declaration"],
                                      visibility=options.get("visibility", "公开"), cover=payload.cover_path)
        ctx.progress("正在核对文章格式、素材和账号身份")
        result = await app.main()
        return _map_receipt(payload.platform_id, result, payload.mode)
    except (ValueError, FileNotFoundError) as exc:
        if mutated:
            return ProviderResult("unknown", "平台编辑过程未完成，可能已保存草稿，请先核对平台内容")
        # Local validation errors contain content paths; only expose known host checks.
        if str(exc) in {"请为大鱼号选择真实的信息来源", "请为雪球选择真实的创作声明", "大鱼号转载内容需要填写原文链接", "大鱼号仅转载内容可以填写原文链接", "正文图片需先导入素材库，再插入文章", "正文引用的图片素材不存在，请重新导入"}:
            return ProviderResult("failed", str(exc))
        return ProviderResult("failed", "文章未通过平台格式、标题、图片或账号校验，请检查发布要求")
    except Exception:
        return ProviderResult("unknown" if mutated else "needs_attention", "平台未确认结果，请先到平台核对后再操作" if mutated else "无法打开平台或核对账号，请检查网络、浏览器和登录状态")
    finally:
        if path is not None:
            # Refreshing local cookies cannot invalidate an already confirmed
            # remote publication or convert it into a retryable exception.
            with suppress(Exception):
                _pack_session(ctx, path)
