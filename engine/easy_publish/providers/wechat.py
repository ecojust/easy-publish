"""Deterministic WeChat Official Account API adapter (not WeChat Channels).

Inspired by oDesk's draft/image flow, implemented independently. Credentials and
access tokens never enter progress messages or exception text. No request that
creates media, a draft or a publication is automatically retried.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .base import ProviderContext, ProviderResult, PublishPayload

API_ORIGIN = "https://api.weixin.qq.com"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BODY_IMAGES = 50
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_MESSAGES = {
    40001: "微信授权信息无效，请重新检查公众号凭据。",
    40013: "公众号 AppID 无效，请检查账号配置。",
    40125: "公众号 AppSecret 无效，请更新账号配置。",
    40164: "当前公网 IP 不在公众号白名单中，请在微信公众平台配置 IP 白名单。",
    40014: "微信授权已失效，请重新连接公众号。",
    42001: "微信授权已过期，请重新连接公众号。",
    40006: "图片大小不符合微信要求，请调整图片后重试。",
    40007: "微信素材编号无效，请检查公众号素材。",
    40005: "微信不支持该素材类型，请使用 JPEG 或 PNG 图片。",
    45009: "微信接口调用次数已达限额，请稍后再试。",
    45011: "微信接口调用过于频繁，请稍后再试。",
    48001: "当前公众号没有此接口权限，请检查账号类型、认证状态和接口权限。",
    48002: "当前公众号尚未获得此操作的授权。",
    48004: "微信已暂停此接口权限，请在公众号后台查看原因。",
    50001: "当前公众号没有此接口权限，请检查账号授权。",
}


class _Rejected(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__(_MESSAGES.get(code, f"微信拒绝此请求（错误码 {code}），请在公众号后台核对。"))


class _Uncertain(Exception):
    """No trustworthy platform acknowledgement was received."""


class _Invalid(Exception):
    pass


def _opaque(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 512 and not any(ord(c) < 32 for c in value) else None


def _credentials(payload: PublishPayload) -> tuple[str, str]:
    app_id = payload.credentials.get("appId", "").strip()
    secret = payload.credentials.get("appSecret", "").strip()
    if not app_id or not secret or len(app_id) > 128 or len(secret) > 512:
        raise _Invalid("请配置公众号 AppID 和 AppSecret。")
    return app_id, secret


def _cache_key(app_id: str, secret: str) -> tuple[str, str]:
    return app_id, hashlib.sha256(secret.encode()).hexdigest()


def _image(path: Path | None) -> tuple[bytes, str, str]:
    if path is None:
        raise _Invalid("微信公众号文章需要 JPEG 或 PNG 封面。")
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            raise _Invalid("图片文件不可用或超过 10 MB，请重新导入 JPEG 或 PNG 图片。")
        data = path.read_bytes()
    except OSError:
        raise _Invalid("图片文件无法读取，请重新导入。") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise _Invalid("图片超过 10 MB，请调整后重新导入。")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image.png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, "image.jpg", "image/jpeg"
    raise _Invalid("微信公众号图片请使用 JPEG 或 PNG 格式。")


def _wechat_image_url(value: object) -> str:
    if not isinstance(value, str):
        raise _Uncertain()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        valid_host = host in {"mmbiz.qpic.cn", "mmbiz.qlogo.cn"}
        if parsed.scheme not in {"https", "http"} or not valid_host or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise _Uncertain()
    except ValueError:
        raise _Uncertain() from None
    return value


class _Body(HTMLParser):
    """Parse image attributes structurally and escape every rewritten attribute."""
    def __init__(self, replacements: dict[str, str] | None = None):
        super().__init__(convert_charrefs=False)
        self.replacements = replacements
        self.image_ids: list[str] = []
        self.output: list[str] = []

    def _tag(self, tag: str, attrs: list[tuple[str, str | None]], closed: bool) -> None:
        if tag == "img":
            sources = [value for key, value in attrs if key == "src"]
            if len(sources) != 1 or not sources[0] or not sources[0].startswith("easy-asset://"):
                raise _Invalid("正文图片请先导入本地素材，微信公众号不直接引用外链图片。")
            asset_id = sources[0][len("easy-asset://"):]
            if not asset_id or any(c in asset_id for c in "/?#\\"):
                raise _Invalid("正文图片引用无效，请重新插入素材。")
            if asset_id not in self.image_ids:
                self.image_ids.append(asset_id)
            if self.replacements is not None:
                attrs = [(key, self.replacements[asset_id] if key == "src" else value) for key, value in attrs]
        serialized = "".join(" " + key + ("" if value is None else '="' + html.escape(value, quote=True) + '"') for key, value in attrs)
        self.output.append("<" + tag + serialized + (" />" if closed else ">"))

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, True)

    def handle_endtag(self, tag):
        self.output.append("</" + tag + ">")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append("&" + name + ";")

    def handle_charref(self, name):
        self.output.append("&#" + name + ";")


def _body(source: str, replacements: dict[str, str] | None = None) -> _Body:
    parser = _Body(replacements)
    try:
        parser.feed(source)
        parser.close()
    except (_Invalid, _Uncertain):
        raise
    except Exception:
        raise _Invalid("文章图片引用无法解析，请检查内容。") from None
    if len(parser.image_ids) > MAX_BODY_IMAGES:
        raise _Invalid("单篇公众号文章最多处理 50 张本地图片。")
    return parser


class _API:
    def __init__(self, payload: PublishPayload, client: httpx.AsyncClient):
        self.app_id, self.secret = _credentials(payload)
        self.key = _cache_key(self.app_id, self.secret)
        self.client = client
        self.token = ""

    async def request(self, endpoint: str, *, auth: bool = True, **kwargs) -> dict:
        try:
            params = dict(kwargs.pop("params", {}))
            if auth:
                params["access_token"] = self.token
            response = await self.client.post(API_ORIGIN + endpoint,
                params=params, **kwargs)
            if response.status_code != 200:
                raise _Uncertain()
            result = response.json()
            if not isinstance(result, dict):
                raise _Uncertain()
            code = result.get("errcode", 0)
            if type(code) is not int:
                raise _Uncertain()
            if code:
                if code in {40001, 40014, 42001}:
                    _TOKEN_CACHE.pop(self.key, None)
                raise _Rejected(code)
            return result
        except (_Rejected, _Uncertain):
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            # Never propagate httpx exceptions: their URL includes the access token.
            raise _Uncertain() from None

    async def authorize(self, *, refresh: bool = False) -> None:
        cached = _TOKEN_CACHE.get(self.key)
        if not refresh and cached and cached[1] > time.monotonic():
            self.token = cached[0]
            return
        _TOKEN_CACHE.pop(self.key, None)
        # Stable-token avoids repeatedly invalidating other sessions of this account.
        data = await self.request("/cgi-bin/stable_token", auth=False, json={
            "grant_type": "client_credential", "appid": self.app_id,
            "secret": self.secret, "force_refresh": False,
        })
        token = data.get("access_token")
        lifetime = data.get("expires_in")
        if not isinstance(token, str) or not token or len(token) > 4096 or type(lifetime) is not int or lifetime <= 0:
            raise _Uncertain()
        self.token = token
        _TOKEN_CACHE[self.key] = (token, time.monotonic() + max(0, lifetime - 300))

    async def upload(self, image: tuple[bytes, str, str], *, cover: bool = False) -> str:
        data, name, mime = image
        if cover:
            result = await self.request("/cgi-bin/material/add_material", params={"type": "image"},
                files={"media": (name, data, mime)})
            media_id = _opaque(result.get("media_id"))
            if not media_id:
                raise _Uncertain()
            return media_id
        result = await self.request("/cgi-bin/media/uploadimg", files={"media": (name, data, mime)})
        return _wechat_image_url(result.get("url"))


async def _login(payload: PublishPayload, ctx: ProviderContext, client: httpx.AsyncClient) -> ProviderResult:
    try:
        api = _API(payload, client)
        ctx.progress("正在检查公众号连接")
        await api.authorize(refresh=True)
        return ProviderResult("ready", "公众号凭据验证通过；正式发布仍需具备对应接口权限。")
    except _Invalid as error:
        return ProviderResult("login_required", str(error))
    except _Rejected as error:
        status = "login_required" if error.code in {40001, 40013, 40125, 40014, 42001} else "needs_attention"
        return ProviderResult(status, str(error))
    except _Uncertain:
        return ProviderResult("failed", "无法确认公众号连接，请检查网络后重试。")


async def _publish(payload: PublishPayload, ctx: ProviderContext, client: httpx.AsyncClient,
                   poll_attempts: int, poll_interval: float) -> ProviderResult:
    draft_id: str | None = None
    publish_id: str | None = None
    mutated = False
    try:
        if payload.kind != "article":
            raise _Invalid("此账号是微信公众号，只支持图文文章；视频号请使用独立的视频账号。")
        if payload.mode not in {"draft", "publish"}:
            raise _Invalid("不支持的公众号发布方式。")
        if not payload.title.strip() or len(payload.title) > 64:
            raise _Invalid("公众号文章标题应为 1–64 个字符。")
        if not payload.body_html.strip():
            raise _Invalid("请填写公众号文章正文。")
        if len(payload.description) > 120:
            raise _Invalid("公众号文章摘要不能超过 120 个字符。")
        api = _API(payload, client)
        parsed = _body(payload.body_html)
        cover = _image(payload.cover_path)
        images = {}
        for asset_id in parsed.image_ids:
            if asset_id not in payload.images:
                raise _Invalid("正文包含找不到的图片素材，请重新插入。")
            images[asset_id] = _image(payload.images[asset_id])
        ctx.progress("正在连接微信公众号")
        await api.authorize()
        ctx.submitting("正在向微信公众号上传素材并保存草稿")
        mutated = True
        replacements = {}
        for asset_id, image in images.items():
            replacements[asset_id] = await api.upload(image)
        cover_id = await api.upload(cover, cover=True)
        article = {"title": payload.title, "content": "".join(_body(payload.body_html, replacements).output),
                   "thumb_media_id": cover_id, "author": payload.author, "digest": payload.description,
                   "content_source_url": "", "need_open_comment": 0, "only_fans_can_comment": 0}
        result = await api.request("/cgi-bin/draft/add", json={"articles": [article]})
        draft_id = _opaque(result.get("media_id"))
        if not draft_id:
            raise _Uncertain()
        if payload.mode == "draft":
            return ProviderResult("draft_created", "文章已保存到微信公众号草稿箱，尚未正式发布。", draft_id,
                                  "https://mp.weixin.qq.com/")
        ctx.progress("草稿已保存，正在提交公众号发布请求")
        result = await api.request("/cgi-bin/freepublish/submit", json={"media_id": draft_id})
        publish_id = _opaque(result.get("publish_id"))
        if not publish_id:
            raise _Uncertain()
        for attempt in range(poll_attempts):
            if attempt:
                await asyncio.sleep(poll_interval)
            result = await api.request("/cgi-bin/freepublish/get", json={"publish_id": publish_id})
            status = result.get("publish_status")
            if type(status) is not int:
                break
            if status == 0:
                items = result.get("article_detail", {}).get("item", []) if isinstance(result.get("article_detail"), dict) else []
                url = next((item.get("article_url") for item in items if isinstance(item, dict) and isinstance(item.get("article_url"), str)), None) if isinstance(items, list) else None
                if url:
                    try:
                        parts = urlsplit(url)
                        if parts.scheme != "https" or parts.hostname != "mp.weixin.qq.com" or parts.username or parts.password:
                            url = None
                    except ValueError:
                        url = None
                return ProviderResult("published", f"微信已确认文章发布成功（草稿 {draft_id}）；此操作不包含向关注者群发。", publish_id, url)
            if status in {2, 3, 4, 5, 6}:
                return ProviderResult("failed", f"微信返回发布失败或文章不可用（状态 {status}，草稿 {draft_id}），请在公众号后台核对。", publish_id)
            if status != 1:
                break
        return ProviderResult("submitted", f"微信已受理发布请求，最终状态尚未确认（草稿 {draft_id}），请在公众号后台查看。", publish_id)
    except _Invalid as error:
        return ProviderResult("failed", str(error))
    except _Rejected as error:
        if publish_id:
            return ProviderResult("submitted", f"发布请求已受理，但暂时无法查询结果：{error}（草稿 {draft_id}）", publish_id)
        if draft_id:
            return ProviderResult("needs_attention", f"草稿已保存，但微信拒绝正式发布：{error}", draft_id)
        return ProviderResult("failed", str(error))
    except _Uncertain:
        if publish_id:
            return ProviderResult("submitted", f"发布请求已受理，查询结果暂不可用（草稿 {draft_id}），请在公众号后台核对。", publish_id)
        if draft_id:
            return ProviderResult("unknown", "草稿已保存，但正式发布请求的结果未能确认；请先到公众号后台核对，避免重复发布。", draft_id)
        if mutated:
            return ProviderResult("unknown", "微信操作的结果未能确认，请先在公众号后台核对素材和草稿，避免重复提交。")
        return ProviderResult("failed", "无法连接微信公众号，请检查网络后重试。")


async def _run(function, payload, ctx, client, **kwargs):
    if client is not None:
        return await function(payload, ctx, client, **kwargs)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=False, trust_env=False) as owned:
        return await function(payload, ctx, owned, **kwargs)


async def login(payload: PublishPayload, ctx: ProviderContext, *, client: httpx.AsyncClient | None = None) -> ProviderResult:
    return await _run(_login, payload, ctx, client)


async def check(payload: PublishPayload, ctx: ProviderContext, *, client: httpx.AsyncClient | None = None) -> ProviderResult:
    return await _run(_login, payload, ctx, client)


async def publish(payload: PublishPayload, ctx: ProviderContext, *, client: httpx.AsyncClient | None = None,
                  poll_attempts: int = 6, poll_interval: float = 2.0) -> ProviderResult:
    if not 1 <= poll_attempts <= 30 or not 0 <= poll_interval <= 10:
        return ProviderResult("failed", "发布状态查询配置无效。")
    return await _run(_publish, payload, ctx, client, poll_attempts=poll_attempts, poll_interval=poll_interval)
