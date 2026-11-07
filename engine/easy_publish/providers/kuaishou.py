"""Kuaishou browser login and single-attempt video publishing.

Browser flows reference dreammis/social-auto-upload at
3847fff01f5434a8a32829833163cd6723cabc59 (MIT). See KUAISHOU_SOURCES.md.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..browser import browser_launch_options
from .base import ProviderContext, ProviderResult, PublishPayload


UPLOAD_URL = "https://cp.kuaishou.com/article/publish/video"
MANAGE_URL = "https://cp.kuaishou.com/article/manage/video"
LOGIN_URL = "https://passport.kuaishou.com/pc/account/login/?sid=kuaishou.web.cp.api&callback=https%3A%2F%2Fcp.kuaishou.com%2Frest%2Finfra%2Fsts%3FfollowUrl%3Dhttps%253A%252F%252Fcp.kuaishou.com%252Farticle%252Fpublish%252Fvideo%26setRootDomain%3Dtrue"
UPLOAD_BUTTON = "button[class^='_upload-btn']"
LOGIN_SECONDS = 600
CHECK_SECONDS = 35
UPLOAD_SECONDS = 20 * 60
CONFIRM_SECONDS = 60
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
CAPTION_LIMIT = 500
TAG_LIMIT = 4  # Current client default; individual account settings can differ.
AUTHORITY_PATHS = {"/rest/v2/creator/pc/authority/account/current",
                   "/rest/cp/works/v3/video/pc/common/current/user"}
SUBMIT_PATHS = {"/rest/cp/works/v2/video/pc/submit", "/rest/cp/works/v3/video/pc/submit",
                "/rest/cp/works/v2/video/pc/isolation/submit"}
UPLOAD_FINISH_PATHS = {"/rest/cp/works/v2/video/pc/upload/finish", "/rest/cp/works/v3/video/pc/upload/finish",
                       "/rest/cp/works/v2/video/pc/upload/isolation/finish"}


def caption_text(title: str, description: str, tags: list[str]) -> str:
    return "\n".join(part for part in (title.strip(), description.strip(),
                                      " ".join("#" + tag.strip() for tag in tags)) if part)


def metadata_errors(title: str, description: str, tags: list[str]) -> list[str]:
    errors = []
    if not title.strip() or len(title) > 200:
        errors.append("快手标题需要 1–200 字")
    if len(tags) > TAG_LIMIT:
        errors.append("当前快手适配最多支持 4 个话题，请减少后重试")
    if any(not tag.strip() or re.search(r"[\s#]", tag) for tag in tags):
        errors.append("快手话题不能为空或包含空白、井号")
    if len({tag.strip() for tag in tags}) != len(tags):
        errors.append("快手话题不能重复")
    # The current publisher's caption limit is 500. Count UTF-16 units as in
    # browser string.length, including separators and the literal hashtag text.
    # Committing the final topic leaves a real trailing separator in the
    # contenteditable; reserve it instead of accepting a 501-unit browser value.
    caption = caption_text(title, description, tags) + (" " if tags else "")
    if len(caption.encode("utf-16-le", errors="replace")) // 2 > CAPTION_LIMIT:
        errors.append("快手标题、描述及话题合计不能超过 500 字符（表情可能占 2 个字符）")
    return errors


def _validate(payload: PublishPayload) -> str | None:
    if payload.kind != "video" or payload.mode != "publish":
        return "快手当前支持视频正式投稿，不支持文章或平台草稿"
    if payload.options:
        return "快手平台选项无效"
    errors = metadata_errors(payload.title, payload.description, payload.tags)
    if errors:
        return errors[0]
    if not payload.video_path or not payload.video_path.is_file() or not payload.video_path.stat().st_size:
        return "视频文件不存在或为空，请重新导入"
    if payload.video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return "快手视频请使用 MP4、MOV、M4V 或 WebM 文件"
    if payload.cover_path:
        from PIL import Image
        try:
            with Image.open(payload.cover_path) as image:
                if image.format not in {"JPEG", "PNG"}:
                    return "快手封面请使用 JPEG 或 PNG 图片"
                image.verify()
        except Exception:
            return "快手封面不存在、损坏或无法读取，请重新导入"
    return None


class KuaishouError(Exception):
    """Contains only safe, locally authored user messages."""


class LoginRequired(KuaishouError):
    pass


def _identity(value: object) -> str | None:
    if not isinstance(value, dict) or type(value.get("result")) is not int or value["result"] != 1:
        return None
    data = value.get("data")
    if not isinstance(data, dict) or data.get("logined") is False or data.get("logined") == 0:
        return None
    identifier = data.get("userId")
    if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
        return None
    text = str(identifier)
    return text if re.fullmatch(r"[1-9][0-9]{0,29}", text) else None


def _normalize_caption(text: str) -> str:
    # Match the current publisher's caption transformation before its request.
    return re.sub(r"\s+", " ", re.sub(r"(\S)#", r"\1 #", text.replace("\u200b", ""))).strip()


class Evidence:
    """Observe only known account and final-submit responses in this browser.

    Capture the initiating Request, not merely the time a response arrived:
    a delayed response from an earlier click cannot confirm our submission.
    """

    def __init__(self):
        self.identity: str | None = None
        self.armed_caption: str | None = None
        self.submit_requests: list = []
        self.all_submissions = 0
        self.ambiguous = False
        self.result: ProviderResult | None = None
        self.upload_armed = False
        self.upload_requests: list = []
        self.upload_file_id: str | None = None
        self.upload_failed = False
        self.owned_page = None
        self._tasks: set[asyncio.Task] = set()

    def request(self, request) -> None:
        if request.method != "POST" or not _creator_url(request.url):
            return
        path = urlsplit(request.url).path
        if path in SUBMIT_PATHS | UPLOAD_FINISH_PATHS and self.owned_page is not None:
            try:
                if request.frame.page is not self.owned_page:
                    return
            except Exception:
                return
        if path in UPLOAD_FINISH_PATHS:
            if self.upload_armed:
                self.upload_requests.append(request)
            return
        if path not in SUBMIT_PATHS:
            return
        self.all_submissions += 1
        try:
            data = request.post_data_json
            valid = (self.armed_caption is not None and isinstance(data, dict)
                     and isinstance(data.get("caption"), str)
                     and _normalize_caption(data["caption"]) == self.armed_caption
                     and isinstance(data.get("fileId"), (str, int)) and not isinstance(data.get("fileId"), bool)
                     and str(data["fileId"]) == self.upload_file_id)
        except Exception:
            valid = False
        if valid and self.all_submissions == 1:
            self.submit_requests.append(request)
        else:
            self.ambiguous = True

    def response(self, response) -> None:
        if not _creator_url(response.url):
            return
        if urlsplit(response.url).path not in AUTHORITY_PATHS | SUBMIT_PATHS | UPLOAD_FINISH_PATHS:
            return
        task = asyncio.create_task(self._read(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read(self, response) -> None:
        path = urlsplit(response.url).path
        if response.request.method != "POST":
            return
        try:
            async with asyncio.timeout(10):
                if response.status != 200:
                    raise ValueError("HTTP response")
                raw = await response.body()
                if len(raw) > 1024 * 1024:
                    raise ValueError("Response size")
                value = json.loads(raw)
            if path in AUTHORITY_PATHS:
                self.identity = _identity(value)
            elif path in UPLOAD_FINISH_PATHS and response.request in self.upload_requests:
                data = value.get("data") if isinstance(value, dict) else None
                identifier = data.get("fileId") if isinstance(data, dict) else None
                if (type(value.get("result")) is not int or value["result"] != 1
                        or isinstance(identifier, bool) or not isinstance(identifier, (int, str))
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(identifier))):
                    raise ValueError("Upload not confirmed")
                self.upload_file_id = str(identifier)
            elif response.request in self.submit_requests:
                if not isinstance(value, dict) or type(value.get("result")) is not int or value["result"] != 1:
                    raise ValueError("Submission not accepted")
                # The current creator UI treats result=1 as accepted; its
                # response does not require an archive ID. Never call it public.
                self.result = ProviderResult("submitted", "快手已接收本次投稿，作品仍可能需要审核；公开状态请在创作中心查看", url=MANAGE_URL)
        except Exception:
            if path in AUTHORITY_PATHS:
                self.identity = None
            elif path in UPLOAD_FINISH_PATHS and response.request in self.upload_requests:
                self.upload_failed = True
            elif response.request in self.submit_requests:
                self.ambiguous = True

    @asynccontextmanager
    async def watch(self, context):
        context.on("request", self.request)
        context.on("response", self.response)
        try:
            yield self
        finally:
            context.remove_listener("request", self.request)
            context.remove_listener("response", self.response)
            for task in list(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)


def _creator_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "cp.kuaishou.com"
                and parsed.port in {None, 443} and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


async def _visible(locator) -> bool:
    return bool(await locator.count() and await locator.first.is_visible())


@asynccontextmanager
async def _browser(ctx: ProviderContext, state: dict | None = None):
    from patchright.async_api import async_playwright
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(**browser_launch_options(ctx.browser_path))
        try:
            config = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai"}
            if state is not None:
                config["storage_state"] = state
            context = await browser.new_context(**config)
            page = await context.new_page()
            page.set_default_timeout(15_000)
            yield context, page
        finally:
            await browser.close()


def _load_session(payload: PublishPayload, ctx: ProviderContext) -> dict:
    try:
        data = json.loads(ctx.session_path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("account_id") != payload.account_id:
            raise ValueError("account binding")
        if not isinstance(data.get("state"), dict):
            raise ValueError("browser session")
        if not isinstance(data.get("identity"), str) or not re.fullmatch(r"[1-9][0-9]{0,29}", data["identity"]):
            raise ValueError("account identity")
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        raise LoginRequired("快手登录会话无效或不属于当前账号，请重新连接") from None


async def _save_session(context, payload: PublishPayload, ctx: ProviderContext, identity: str) -> None:
    state = await context.storage_state()
    ctx.session_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.session_path.write_text(json.dumps({"version": 1, "account_id": payload.account_id,
                                          "identity": identity, "state": state}, ensure_ascii=False), encoding="utf-8")
    ctx.session_path.chmod(0o600)


async def _wait_authenticated(context, evidence: Evidence, ctx: ProviderContext, seconds: float,
                              expected: str | None = None):
    announced = False
    async with asyncio.timeout(seconds):
        while True:
            pages = [page for page in context.pages if not page.is_closed()]
            if not pages:
                raise KuaishouError("快手登录窗口已关闭，请重新连接账号")
            creators = [page for page in pages if _creator_url(page.url)]
            if creators and evidence.identity:
                if expected is not None and evidence.identity != expected:
                    raise LoginRequired("登录的快手用户与原账号不一致，请登录原账号；其他账号请单独添加")
                return creators[-1], evidence.identity
            if creators and not announced:
                ctx.progress("已进入快手创作中心，正在确认账号登录状态")
                announced = True
            await asyncio.sleep(0.5)


async def login(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    expected = None
    if ctx.session_path.is_file():
        try:
            expected = _load_session(payload, ctx)["identity"]
        except LoginRequired:
            return ProviderResult("needs_attention", "保存的快手账号身份无法识别，请移除此本地账号后重新添加")
    ctx.progress("请在打开的快手浏览器中扫码登录，并完成平台验证")
    try:
        async with _browser(ctx) as (context, page), Evidence().watch(context) as evidence:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
            _, identity = await _wait_authenticated(context, evidence, ctx, LOGIN_SECONDS, expected)
            await _save_session(context, payload, ctx, identity)
        return ProviderResult("ready", "快手账号已确认，登录会话已保存")
    except LoginRequired as exc:
        return ProviderResult("login_required", str(exc))
    except TimeoutError:
        return ProviderResult("needs_attention", "等待快手账号验证超时；扫码后仍未连接时，请在创作中心完成验证后重新连接")
    except KuaishouError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "快手连接未完成，请检查发布浏览器、网络及平台验证后重试")


async def check(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    try:
        session = _load_session(payload, ctx)
        async with _browser(ctx, session["state"]) as (context, page), Evidence().watch(context) as evidence:
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            _, identity = await _wait_authenticated(context, evidence, ctx, CHECK_SECONDS, session["identity"])
            await _save_session(context, payload, ctx, identity)
        return ProviderResult("ready", "快手账号登录有效")
    except LoginRequired as exc:
        return ProviderResult("login_required", str(exc))
    except TimeoutError:
        return ProviderResult("login_required", "未能确认快手登录状态，请重新连接并完成平台验证")
    except KuaishouError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "快手账号检查未完成，请检查浏览器与网络后重试")


async def _description_editor(page):
    label = page.get_by_text(re.compile(r"^(?:作品)?描述$"))
    candidates = [
        page.locator('div#work-description-edit[contenteditable="true"]'),
        label.locator("xpath=following-sibling::*[@contenteditable='true']"),
        label.locator("xpath=following-sibling::div//*[@contenteditable='true']"),
        label.locator("xpath=following::*[@contenteditable='true'][1]"),
    ]
    async with asyncio.timeout(60):
        while True:
            for candidate in candidates:
                if await _visible(candidate):
                    return candidate.first
            if page.is_closed():
                raise KuaishouError("快手发布窗口已关闭")
            await asyncio.sleep(0.5)


async def _dismiss_guide(page) -> None:
    # Normal, explicit guide dismissal only. Never remove overlays or bypass a
    # verification/terms prompt with DOM manipulation or force-clicks.
    guide = page.locator("#react-joyride-portal").first
    if await _visible(guide):
        dismiss = guide.get_by_role("button", name=re.compile(r"^(?:跳过|我知道了|关闭)$"))
        if await _visible(dismiss):
            await dismiss.first.click(timeout=10_000)


async def _set_cover(page, path: Path) -> None:
    label = page.locator("span").filter(has_text=re.compile(r"^封面设置$"))
    await label.first.wait_for(state="visible", timeout=30_000)
    await label.first.locator("xpath=../following-sibling::div[1]").locator("div").first.click()
    modal = page.locator('div[role="document"].ant-modal:visible').first
    await modal.wait_for(state="visible", timeout=30_000)
    await modal.get_by_text("上传封面", exact=True).click()
    await modal.locator('input[type="file"]').first.set_input_files(str(path))
    await modal.get_by_role("button", name="确认", exact=True).click()
    await modal.wait_for(state="hidden", timeout=30_000)


async def _wait_upload(page, button, evidence: Evidence) -> None:
    async with asyncio.timeout(UPLOAD_SECONDS):
        while True:
            if page.is_closed():
                raise KuaishouError("快手发布窗口已关闭，请先到平台核对上传结果")
            if evidence.upload_failed or await _visible(page.get_by_text(re.compile(r"上传失败|处理失败|转码失败"))):
                raise KuaishouError("快手未能完成视频上传，请先到平台检查，不会自动重新上传")
            if (evidence.upload_file_id and not await _visible(page.get_by_text(re.compile(r"^上传中(?:\s|$)")))
                    and await _visible(button) and await button.is_enabled()):
                return
            await asyncio.sleep(0.5)


async def _wait_statement(page, ctx: ProviderContext) -> None:
    # Some accounts have a required, server-configured list of author
    # statements. The user must choose the declaration that describes the work;
    # never choose the first option or falsely label it as original/AI-free.
    placeholder = page.get_by_text("请选择作者声明", exact=True)
    if not await _visible(placeholder):
        return
    ctx.progress("快手要求作者声明，请在发布浏览器中选择符合本次内容的声明，选择后将继续投稿")
    try:
        async with asyncio.timeout(LOGIN_SECONDS):
            while await _visible(placeholder):
                if page.is_closed():
                    raise KuaishouError("快手窗口已关闭，作者声明尚未完成；请到平台检查草稿")
                await asyncio.sleep(0.5)
    except TimeoutError:
        raise KuaishouError("快手作者声明未完成，尚未点击发布；请在创作中心检查已上传的草稿") from None


async def _prepare(page, payload: PublishPayload, ctx: ProviderContext, evidence: Evidence):
    evidence.owned_page = page
    existing = page.locator('div#work-description-edit[contenteditable="true"]')
    if await _visible(existing) and (await existing.first.inner_text()).strip():
        raise KuaishouError("快手发布页已有未完成的草稿，请先在创作中心处理；尚未上传本次视频")
    upload = page.locator(UPLOAD_BUTTON).first
    await upload.wait_for(state="visible", timeout=30_000)
    ctx.submitting("正在上传快手视频；中断后请先核对平台草稿及作品，避免重复上传")
    evidence.upload_armed = True
    async with page.expect_file_chooser(timeout=15_000) as event:
        await upload.click()
    chooser = await event.value
    await chooser.set_files(str(payload.video_path))
    ctx.progress("正在上传快手视频并填写作品信息")
    await _dismiss_guide(page)
    editor = await _description_editor(page)
    await editor.click()
    await page.keyboard.press("ControlOrMeta+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.insert_text(caption_text(payload.title, payload.description, []))
    if payload.tags:
        await page.keyboard.press("Enter")
        for tag in payload.tags:
            await page.keyboard.press("Shift+Digit3")
            await page.keyboard.insert_text(tag.strip())
            await page.keyboard.press("Space")
        await page.keyboard.press("Escape")
    button = page.get_by_role("button", name="发布", exact=True).first
    await _wait_upload(page, button, evidence)
    if payload.cover_path:
        await _set_cover(page, payload.cover_path)
    await _wait_statement(page, ctx)
    actual = await editor.inner_text()
    if len(actual.encode("utf-16-le", errors="replace")) // 2 > CAPTION_LIMIT:
        raise KuaishouError("快手编辑器中的完整描述超过 500 字符，尚未点击发布；请检查平台草稿")
    normalize = lambda value: re.sub(r"\s+", " ", value.replace("\u200b", "")).strip()
    if normalize(actual) != normalize(caption_text(payload.title, payload.description, payload.tags)):
        raise KuaishouError("快手编辑器未完整保留标题、描述或话题，已停止投稿；请到平台检查草稿")
    topics = await editor.locator('span[is-tag="true"] span.at-tag-item[data-tag-name]').all_text_contents()
    if sorted(text.replace("\u200b", "").strip().lstrip("#").strip() for text in topics) != sorted(tag.strip() for tag in payload.tags):
        raise KuaishouError("快手未完整识别所选话题，可能超出当前账号的限制；已停止投稿，请检查平台草稿")
    return button


async def _submit_once(page, button, evidence: Evidence, payload: PublishPayload,
                       ctx: ProviderContext) -> ProviderResult:
    if evidence.all_submissions:
        return ProviderResult("unknown", "快手页面已发生投稿请求，尚未再次点击发布；请到创作中心核对", url=MANAGE_URL)
    # An existing confirmation could belong to manual interaction while media
    # uploaded. Do not confirm a dialog whose initiating click we did not own.
    modal_selector = "div.ant-modal-confirm:visible, div.ant-modal-confirm-centered:visible"
    if await _visible(page.locator(modal_selector)):
        return ProviderResult("unknown", "快手页面已有确认弹窗，无法确认来源；请先在创作中心核对", url=MANAGE_URL)
    evidence.armed_caption = _normalize_caption(caption_text(payload.title, payload.description, payload.tags))
    ctx.progress("正在向快手提交一次投稿，等待平台确认")
    confirmed = False
    try:
        # One publish click, at most one matching explicit confirmation. A
        # timeout can occur after a remote write; this method never retries.
        await button.click(timeout=15_000)
        async with asyncio.timeout(CONFIRM_SECONDS):
            while True:
                if evidence.ambiguous or evidence.all_submissions > 1:
                    break
                if evidence.result is not None:
                    return evidence.result
                if page.is_closed():
                    break
                if not confirmed and not evidence.all_submissions:
                    modal = page.locator(modal_selector).filter(has_text=re.compile(r"确认发布|确定发布|是否.{0,30}发布")).first
                    if await _visible(modal):
                        confirmation = modal.get_by_role("button", name=re.compile(r"^(?:确认发布|确认|确定|继续发布)$")).first
                        if await _visible(confirmation):
                            confirmed = True
                            await confirmation.click(timeout=15_000)
                await asyncio.sleep(0.2)
    except Exception:
        pass
    return ProviderResult("unknown", "快手投稿未收到本次请求的明确确认，请到创作中心核对；不要直接重复投稿", url=MANAGE_URL)


async def publish(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    mutated = False
    original_emit = ctx.emit

    def emit(event: dict) -> None:
        nonlocal mutated
        if event.get("event") == "submitting":
            # Set before ACK: a dropped acknowledgement is conservatively
            # treated by the supervising runtime as uncertain too.
            mutated = True
        original_emit(event)

    guarded = ProviderContext(ctx.session_path, ctx.workspace, ctx.browser_path, emit)
    try:
        problem = _validate(payload)
        if problem:
            return ProviderResult("needs_attention", problem)
        session = _load_session(payload, ctx)
        async with _browser(ctx, session["state"]) as (context, initial), Evidence().watch(context) as evidence:
            await initial.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            page, identity = await _wait_authenticated(context, evidence, ctx, CHECK_SECONDS, session["identity"])
            # Account validity is independent of an upload widget. Navigate
            # explicitly only after identity is confirmed, then require the
            # real creator upload control before making a mutating request.
            if urlsplit(page.url).path.rstrip("/") != "/article/publish/video":
                await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            if not _creator_url(page.url):
                raise LoginRequired("快手发布入口要求重新验证，请重新连接账号")
            async with asyncio.timeout(UPLOAD_SECONDS + LOGIN_SECONDS + CONFIRM_SECONDS + 120):
                button = await _prepare(page, payload, guarded, evidence)
                if evidence.identity != identity:
                    raise LoginRequired("快手登录状态在上传期间发生变化，尚未点击发布；请先核对平台草稿")
                result = await _submit_once(page, button, evidence, payload, ctx)
            with suppress(Exception):
                await _save_session(context, payload, ctx, identity)
            return result
    except LoginRequired as exc:
        if not mutated:
            return ProviderResult("login_required", str(exc))
    except KuaishouError as exc:
        if not mutated:
            return ProviderResult("needs_attention", str(exc))
        return ProviderResult("unknown", str(exc), url=MANAGE_URL)
    except Exception:
        if not mutated:
            return ProviderResult("failed", "尚未上传快手视频；请检查账号、网络或发布入口后重试")
    return ProviderResult("unknown", "快手上传或投稿已开始但结果未确认，请在创作中心核对；不会自动重试", url=MANAGE_URL)
