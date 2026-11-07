"""Bilibili browser login and bounded UPOS/Web publishing.

Protocol reference: biliup/biliup f23b7614b250caacba8a1475412515264dc30558
(MIT, Copyright (c) 2019 ForgQi). See BILIBILI_SOURCES.md.
No executable downloads, automatic final-submit retries, or account secrets in
result metadata. The runtime encrypts this adapter's opaque session JSON.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import parse_qsl, urlsplit

import httpx
from PIL import Image, ImageOps

from ..browser import browser_launch_options
from ..target_projection import project_payload
from .base import ProviderContext, ProviderResult, PublishPayload

NAV = "https://api.bilibili.com/x/web-interface/nav"
CATEGORIES = "https://member.bilibili.com/x/vupre/web/archive/pre"
PREUPLOAD = "https://member.bilibili.com/preupload"
COVER = "https://member.bilibili.com/x/vu/web/cover/up"
SUBMIT = "https://member.bilibili.com/x/vu/web/add/v3"
CREATOR = "https://member.bilibili.com/platform/upload/video/frame"
MANAGE = "https://member.bilibili.com/platform/upload-manager/article"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
LOGIN_SECONDS = 600
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024 * 1024


class BilibiliError(Exception):
    """Only locally authored, safe messages may enter this exception."""


class LoginRequired(BilibiliError):
    pass


def _own_domain(domain: str) -> bool:
    host = domain.lstrip(".").lower()
    return host == "bilibili.com" or host.endswith(".bilibili.com")


def _cookies(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise LoginRequired("登录会话无效，请重新登录哔哩哔哩")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        domain, name, value = item.get("domain"), item.get("name"), item.get("value")
        if not isinstance(domain, str) or not _own_domain(domain):
            continue
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", name):
            continue
        if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            continue
        expires = item.get("expires", -1)
        if not isinstance(expires, (int, float)) or (expires > 0 and expires <= time.time()):
            continue
        path = item.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 1024:
            continue
        result.append({"name": name, "value": value, "domain": domain.lower(), "path": path, "expires": expires})
    return result


def _credential(cookies: list[dict], name: str) -> str:
    values = {item["value"] for item in cookies if item["name"] == name and item["value"]}
    if len(values) != 1:
        raise LoginRequired("登录会话缺少有效凭据，请重新登录哔哩哔哩")
    return values.pop()


def _load_session(payload: PublishPayload, ctx: ProviderContext) -> dict:
    try:
        data = json.loads(ctx.session_path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("account_id") != payload.account_id:
            raise ValueError("session binding")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", str(data.get("mid", ""))):
            raise ValueError("account identity")
        data["cookies"] = _cookies(data.get("cookies"))
        for name in ("SESSDATA", "bili_jct", "DedeUserID"):
            _credential(data["cookies"], name)
        if _credential(data["cookies"], "DedeUserID") != str(data["mid"]):
            raise ValueError("identity mismatch")
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        raise LoginRequired("登录会话无效或不属于当前账号，请重新登录哔哩哔哩") from None


def _client(cookies: list[dict] | None = None) -> httpx.AsyncClient:
    jar = httpx.Cookies()
    for cookie in cookies or []:
        jar.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
    # Upload hosts use a different, cookie-free client. Redirects cannot forward
    # authenticated headers or an upload token to an unvalidated destination.
    headers = {"User-Agent": USER_AGENT}
    if cookies is not None:
        headers.update({"Referer": CREATOR, "Origin": "https://member.bilibili.com"})
    return httpx.AsyncClient(cookies=jar, headers=headers, timeout=60, follow_redirects=False, trust_env=False)


def _json(response: httpx.Response) -> dict:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise BilibiliError("平台返回了无法识别的响应")
    return value


def _data(response: httpx.Response) -> dict:
    value = _json(response)
    code = value.get("code")
    if type(code) is not int:
        raise BilibiliError("平台返回了无法识别的响应")
    if code in {-101, -111}:
        raise LoginRequired("哔哩哔哩登录已失效，请重新登录")
    if code != 0:
        if code in {-352, -412, -509}:
            raise BilibiliError("哔哩哔哩要求平台验证或暂时限制请求，请在创作中心处理后再试")
        raise BilibiliError(f"哔哩哔哩未接受操作（错误码 {code}），请在创作中心检查账号及投稿要求")
    if not isinstance(value.get("data"), dict):
        raise BilibiliError("平台返回了无法识别的响应")
    return value["data"]


async def _identity(client: httpx.AsyncClient, cookies: list[dict], expected: str | None = None) -> str:
    for name in ("SESSDATA", "bili_jct", "DedeUserID"):
        _credential(cookies, name)
    data = _data(await client.get(NAV))
    mid = str(data.get("mid", ""))
    if data.get("isLogin") is not True or not re.fullmatch(r"[1-9][0-9]{0,19}", mid):
        raise LoginRequired("哔哩哔哩尚未登录，请完成浏览器登录")
    if mid != _credential(cookies, "DedeUserID") or (expected is not None and mid != expected):
        raise LoginRequired("当前登录用户与保存的账号不一致，请登录原账号；其他账号请单独添加")
    return mid


def _category_choices(data: dict) -> list[dict[str, str]]:
    tree = data.get("typelist")
    if not isinstance(tree, list):
        raise BilibiliError("未取得当前账号的投稿分区，请在创作中心检查账号权限后重新检查登录")
    choices: dict[str, str] = {}

    def visit(nodes: list, ancestors: list[str], depth: int = 0) -> None:
        if depth > 5 or len(nodes) > 1000:
            raise BilibiliError("平台分区结构已变化，请更新发布器后重试")
        for node in nodes:
            if not isinstance(node, dict):
                continue
            identifier, name = str(node.get("id", "")), node.get("name")
            if not re.fullmatch(r"[1-9][0-9]{0,4}", identifier) or int(identifier) > 65535:
                continue
            if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
                continue
            labels = ancestors + [name.strip()]
            children = node.get("children")
            if isinstance(children, list) and children:
                visit(children, labels, depth + 1)
            else:
                choices[identifier] = " / ".join(labels)

    visit(tree, [])
    if not choices or len(choices) > 1000:
        raise BilibiliError("当前账号没有可识别的投稿分区，请在创作中心检查后重试")
    return [{"value": key, "label": label} for key, label in choices.items()]


async def _categories(client: httpx.AsyncClient) -> list[dict[str, str]]:
    return _category_choices(_data(await client.get(CATEGORIES)))


@asynccontextmanager
async def _browser(ctx: ProviderContext):
    from patchright.async_api import async_playwright
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(**browser_launch_options(ctx.browser_path))
        try:
            context = await browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai", user_agent=USER_AGENT)
            page = await context.new_page()
            yield context, page
        finally:
            await browser.close()


async def login(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    expected = None
    if ctx.session_path.is_file():
        # Preserve identity even when an old session has expired.
        try:
            old = json.loads(ctx.session_path.read_text(encoding="utf-8"))
            if old.get("account_id") != payload.account_id:
                raise ValueError("account binding")
            expected = str(old["mid"])
            if not re.fullmatch(r"[1-9][0-9]{0,19}", expected):
                raise ValueError("identity")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return ProviderResult("needs_attention", "保存的账号身份无法识别，请删除此账号后重新添加")
    ctx.progress("请在打开的哔哩哔哩浏览器中扫码登录，并完成平台验证")
    try:
        async with asyncio.timeout(LOGIN_SECONDS), _browser(ctx) as (context, page):
            await page.goto(CREATOR, wait_until="domcontentloaded", timeout=60_000)
            while True:
                cookies = _cookies(await context.cookies())
                try:
                    async with _client(cookies) as client:
                        mid = await _identity(client, cookies)
                        if expected is not None and mid != expected:
                            return ProviderResult("login_required", "登录的哔哩哔哩用户与原账号不一致，请登录原账号；其他账号请单独添加")
                        choices = await _categories(client)
                    break
                except LoginRequired:
                    await asyncio.sleep(1)
            ctx.session_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.session_path.write_text(json.dumps({"version": 1, "account_id": payload.account_id, "mid": mid, "cookies": cookies}, ensure_ascii=False), encoding="utf-8")
            ctx.session_path.chmod(0o600)
        return ProviderResult("ready", "哔哩哔哩登录及投稿分区已确认", metadata={"bilibiliCategories": choices})
    except TimeoutError:
        return ProviderResult("needs_attention", "等待哔哩哔哩登录超时，请重新登录并完成平台验证")
    except BilibiliError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "哔哩哔哩登录未完成，请检查网络、发布浏览器及创作中心权限后重试")


async def check(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    try:
        session = _load_session(payload, ctx)
        async with _client(session["cookies"]) as client:
            await _identity(client, session["cookies"], str(session["mid"]))
            choices = await _categories(client)
        return ProviderResult("ready", "哔哩哔哩登录有效，已刷新当前账号的投稿分区", metadata={"bilibiliCategories": choices})
    except LoginRequired as exc:
        return ProviderResult("login_required", str(exc))
    except BilibiliError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "哔哩哔哩账号检查未完成，请检查网络后重试")


def _validate(payload: PublishPayload) -> str | None:
    payload = project_payload(payload)
    if payload.kind != "video" or payload.mode != "publish":
        return "哔哩哔哩当前支持视频投稿，请选择视频及正式提交"
    if not payload.title.strip() or len(payload.title) > 80:
        return "哔哩哔哩标题需要 1–80 个字符；不会自动截断标题"
    if len(payload.description) > 2000:
        return "哔哩哔哩视频简介最多 2000 个字符"
    if not 1 <= len(payload.tags) <= 12:
        return "哔哩哔哩投稿需要 1–12 个标签"
    if any(not isinstance(tag, str) or not tag.strip() or len(tag) > 20 or any(c in tag for c in ",，\r\n") for tag in payload.tags):
        return "哔哩哔哩每个标签需要 1–20 个字符，不能包含逗号或换行"
    if len(set(tag.strip() for tag in payload.tags)) != len(payload.tags):
        return "哔哩哔哩标签不能重复"
    copyright = payload.options.get("copyright")
    if copyright not in {"1", "2"}:
        return "请选择哔哩哔哩稿件类型：自制或转载"
    source = payload.options.get("source", "")
    if not isinstance(source, str) or len(source) > 200 or (copyright == "2" and not source.strip()):
        return "转载视频必须填写来源，最多 200 个字符"
    if copyright == "2":
        try:
            origin = urlsplit(source)
            if origin.scheme not in {"http", "https"} or not origin.hostname or origin.username is not None or origin.password is not None or any(c.isspace() for c in source):
                return "转载来源需填写完整的 HTTP 或 HTTPS 链接"
        except ValueError:
            return "转载来源需填写完整的 HTTP 或 HTTPS 链接"
    tid = payload.options.get("tid", "")
    if not isinstance(tid, str) or not re.fullmatch(r"[1-9][0-9]{0,4}", tid) or int(tid) > 65535:
        return "请选择当前哔哩哔哩账号可用的投稿分区"
    if not payload.video_path or not payload.video_path.is_file() or payload.video_path.stat().st_size <= 0:
        return "视频文件不存在或为空，请重新导入"
    if payload.video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return "此视频扩展名暂不支持，请使用 MP4、MOV 或其他支持的视频格式"
    if payload.cover_path:
        try:
            if not 0 < payload.cover_path.stat().st_size <= MAX_COVER_BYTES:
                return "哔哩哔哩封面必须为不超过 5 MiB 的 JPEG 或 PNG 图片"
            with Image.open(payload.cover_path) as cover:
                if cover.format not in {"JPEG", "PNG"}:
                    return "哔哩哔哩封面仅支持 JPEG 或 PNG 图片"
                cover.verify()
        except Exception:
            return "封面图片不存在、损坏或无法读取，请重新导入"
    return None


def _upload_url(bucket: dict) -> str:
    endpoint, uri = bucket.get("endpoint"), bucket.get("upos_uri")
    if not isinstance(endpoint, str) or not isinstance(uri, str) or not uri.startswith("upos://"):
        raise BilibiliError("视频上传地址无效，已停止操作")
    parsed = urlsplit("https:" + endpoint if endpoint.startswith("//") else endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or
            not any(parsed.hostname.endswith("." + domain) for domain in ("bilivideo.com", "bilivideo.cn")) or
            parsed.username is not None or parsed.password is not None or parsed.port not in {None, 443} or
            parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise BilibiliError("平台返回了未受支持的视频上传地址，已停止操作")
    key = uri.removeprefix("upos://")
    if not key or not re.fullmatch(r"[A-Za-z0-9_/\.\-]+", key) or any(part in {"", ".", ".."} for part in key.split("/")):
        raise BilibiliError("视频上传对象无效，已停止操作")
    return f"https://{parsed.netloc}/{key}"


async def _line(client: httpx.AsyncClient) -> dict[str, str]:
    # The platform chooses currently available UPOS lines. This read-only probe
    # does not upload its suggested speed-test body or follow its probe URLs.
    value = _json(await client.get(PREUPLOAD, params={"r": "probe"}))
    lines = value.get("lines")
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, dict) or line.get("os") != "upos" or not isinstance(line.get("query"), str):
                continue
            params = dict(parse_qsl(line["query"]))
            if params and set(params) <= {"probe_version", "upcdn", "zone"} and all(re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", item) for item in params.values()):
                return params
    raise BilibiliError("哔哩哔哩未提供可用的视频上传线路，请稍后重试")


async def _upload(client: httpx.AsyncClient, payload: PublishPayload, ctx: ProviderContext, line: dict[str, str]) -> str:
    path = payload.video_path
    assert path is not None
    size = path.stat().st_size
    # preupload allocates remote upload state, so the caller fences before this GET.
    bucket = _json(await client.get(PREUPLOAD, params={**line, "r": "upos", "profile": "ugcupos/bup", "ssl": 0, "version": "2.14.0", "build": 2140000, "name": path.name, "size": size}))
    url = _upload_url(bucket)
    chunk_size, auth, biz_id = bucket.get("chunk_size"), bucket.get("auth"), bucket.get("biz_id")
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_BYTES or type(biz_id) is not int or biz_id <= 0:
        raise BilibiliError("平台视频上传参数无效，已停止操作")
    if not isinstance(auth, str) or not auth or len(auth) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in auth):
        raise BilibiliError("平台视频上传凭证无效，已停止操作")
    async with _client() as upload:
        headers = {"X-Upos-Auth": auth}
        init = _json(await upload.post(url, params={"uploads": "", "output": "json"}, headers=headers))
        upload_id = init.get("upload_id")
        if not isinstance(upload_id, str) or not upload_id or len(upload_id) > 1024:
            raise BilibiliError("平台未确认视频上传会话")
        count = (size + chunk_size - 1) // chunk_size
        parts = []
        last_progress = -1
        with path.open("rb") as video:
            for chunk in range(count):
                content = video.read(chunk_size)
                start = chunk * chunk_size
                expected = min(chunk_size, size - start)
                if len(content) != expected:
                    raise BilibiliError("视频文件在上传时发生变化，已停止操作")
                response = await upload.put(url, params={"uploadId": upload_id, "chunks": count, "total": size, "chunk": chunk, "size": len(content), "partNumber": chunk + 1, "start": start, "end": start + len(content)}, content=content, headers=headers, timeout=240)
                response.raise_for_status()
                parts.append({"partNumber": chunk + 1, "eTag": "etag"})
                percent = (start + len(content)) * 100 // size
                if percent // 5 != last_progress:
                    ctx.progress(f"哔哩哔哩视频已上传 {percent}%")
                    last_progress = percent // 5
            if video.read(1):
                raise BilibiliError("视频文件在上传时发生变化，已停止操作")
        merged = _json(await upload.post(url, params={"name": path.name, "uploadId": upload_id, "biz_id": biz_id, "output": "json", "profile": "ugcupos/bup"}, json={"parts": parts}, headers=headers))
        if type(merged.get("OK")) is not int or merged["OK"] != 1:
            raise BilibiliError("平台尚未确认视频上传完成")
    return PurePosixPath(bucket["upos_uri"]).stem


def _prepare_cover(path: Path | None) -> bytes | None:
    if path is None:
        return None
    # The verified upload contract uses JPEG. Preserve orientation and composite
    # transparent PNGs onto white; conversion finishes before remote mutation.
    with Image.open(path) as original:
        oriented = ImageOps.exif_transpose(original)
        rgba = oriented.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        output = BytesIO()
        rgb.save(output, format="JPEG", quality=95)
    value = output.getvalue()
    if len(value) > MAX_COVER_BYTES:
        raise BilibiliError("封面转换为投稿 JPEG 后超过 5 MiB，请缩小图片后重试")
    return value


async def _cover(client: httpx.AsyncClient, content: bytes | None, csrf: str) -> str:
    if content is None:
        return ""
    data = _data(await client.post(COVER, data={"cover": "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii"), "csrf": csrf}))
    url = data.get("url")
    if isinstance(url, str) and url.startswith("//"):
        url = "https:" + url
    if not isinstance(url, str):
        raise BilibiliError("平台未确认封面上传")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not any(parsed.hostname.endswith("." + domain) for domain in ("hdslb.com", "biliimg.com", "bilibili.com")) or parsed.username is not None or parsed.password is not None or parsed.port is not None or parsed.fragment:
        raise BilibiliError("平台返回了无法识别的封面地址")
    return parsed._replace(scheme="https").geturl()


async def publish(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    payload = project_payload(payload)
    mutating = False
    try:
        problem = _validate(payload)
        if problem:
            return ProviderResult("needs_attention", problem)
        cover_content = _prepare_cover(payload.cover_path)
        session = _load_session(payload, ctx)
        async with _client(session["cookies"]) as client:
            await _identity(client, session["cookies"], str(session["mid"]))
            categories = await _categories(client)
            if payload.options["tid"] not in {item["value"] for item in categories}:
                return ProviderResult("needs_attention", "所选分区已不适用于当前哔哩哔哩账号，请检查账号以刷新分区后重新选择")
            csrf = _credential(session["cookies"], "bili_jct")
            line = await _line(client)
            ctx.submitting("正在向哔哩哔哩上传视频；中断后请先核对创作中心，避免重复投稿")
            mutating = True
            filename = await _upload(client, payload, ctx, line)
            cover = await _cover(client, cover_content, csrf)
            studio = {"copyright": int(payload.options["copyright"]), "source": payload.options.get("source", "").strip(), "tid": int(payload.options["tid"]), "cover": cover, "title": payload.title, "desc_format_id": 0, "desc": payload.description, "dynamic": "", "subtitle": {"open": 0, "lan": ""}, "tag": ",".join(tag.strip() for tag in payload.tags), "videos": [{"filename": filename, "title": payload.title, "desc": ""}], "open_subtitle": False, "interactive": 0, "dolby": 0, "lossless_music": 0, "no_reprint": 0, "charging_pay": 0}
            ctx.progress("视频上传完成，正在向哔哩哔哩提交一次投稿")
            # Never retry this request, including HTTP failures or missing receipt.
            receipt = _data(await client.post(SUBMIT, params={"t": int(time.time() * 1000), "csrf": csrf}, json=studio))
            aid, bvid = receipt.get("aid"), receipt.get("bvid")
            if isinstance(bvid, str) and re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
                return ProviderResult("submitted", "哔哩哔哩已接收投稿，等待平台审核；尚未确认公开发布", remote_id=bvid, url=f"https://www.bilibili.com/video/{bvid}")
            if type(aid) is int and aid > 0:
                return ProviderResult("submitted", "哔哩哔哩已接收投稿，等待平台审核；尚未确认公开发布", remote_id=str(aid), url=f"https://www.bilibili.com/video/av{aid}")
            raise BilibiliError("投稿响应缺少有效稿件编号，无法确认是否已提交")
    except LoginRequired as exc:
        if not mutating:
            return ProviderResult("login_required", str(exc))
        message = str(exc)
    except BilibiliError as exc:
        if not mutating:
            return ProviderResult("needs_attention", str(exc))
        message = str(exc)
    except Exception:
        if not mutating:
            return ProviderResult("needs_attention", "哔哩哔哩投稿准备未完成，请检查网络、账号及本地素材")
        message = "哔哩哔哩上传或投稿未收到完整确认"
    return ProviderResult("unknown", message + "；可能已产生平台内容，请到创作中心核对后再操作，不要直接重发", url=MANAGE)
