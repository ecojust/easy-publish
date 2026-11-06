"""Bounded, account-isolated video publishing through the platform's browser UI.

Selectors adapted from dreammis/social-auto-upload, commit
3847fff01f5434a8a32829833163cd6723cabc59 (MIT, Copyright (c) 2023 dreammis).
See VIDEO_SOURCES.md. No upstream retry loops or runtime download code is used.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit

from .base import ProviderContext, ProviderResult, PublishPayload


@dataclass(frozen=True)
class Site:
    label: str
    url: str
    host: str
    upload: str
    title_limit: int


SITES = {
    "douyin": Site("抖音", "https://creator.douyin.com/creator-micro/content/upload", "creator.douyin.com", 'input.upload-btn-input, div[class^="container"] input[type="file"]', 30),
    "xiaohongshu": Site("小红书", "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video", "creator.xiaohongshu.com", 'div[class^="upload-content"] input.upload-input, div.cover-container > input.upload-input[accept*=".mp4"]', 20),
    "channels": Site("视频号", "https://channels.weixin.qq.com/platform", "channels.weixin.qq.com", 'input[type="file"]', 15),
}
ALIASES = {"tencent": "channels", "wechat_channels": "channels", "xhs": "xiaohongshu"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
UPLOAD_SECONDS = 20 * 60
LOGIN_SECONDS = 10 * 60
CONFIRM_SECONDS = 60
DOUYIN_USER_INFO = "/aweme/v1/creator/pc/user/info/"
DOUYIN_VERIFY_SECONDS = 30


class LoginPageError(Exception):
    """A fixed, non-secret message that the user can act on."""


class VideoStageError(Exception):
    """An authored stage-specific message, without platform data or secrets."""


# The public XHS video editor renders these mutually exclusive siblings of its
# file input for Uploading/Analyze, Transcoding, Preview, Fail and Cancel. Only
# Preview is ready; a title field, enabled publish button or 100% is not proof.
# See VIDEO_SOURCES.md for the pinned adapter and current public bundle source.
_XHS_UPLOAD_STATE = """({selector, filename}) => {
    const visible = el => !!el && !!el.getClientRects().length &&
        getComputedStyle(el).visibility !== 'hidden';
    const shown = selector => Array.from(document.querySelectorAll(selector)).some(visible);
    if (shown('div[class*="login-box"], input[placeholder*="验证码"]') ||
        Array.from(document.querySelectorAll('[role="dialog"]')).some(el =>
            visible(el) && /安全验证|登录验证|扫码登录/.test(el.textContent))) {
        return {state: 'auth'};
    }
    const roots = Array.from(document.querySelectorAll(selector)).map(el => el.parentElement);
    for (const root of roots) {
        const child = name => Array.from(root.children).find(el => el.matches(name) && visible(el));
        if (child('.fail')) return {state: 'failed'};
        const uploading = child('.uploading');
        if (uploading) {
            const match = uploading.textContent.match(/上传中\\s*(\\d{1,3})%/);
            return {state: 'uploading', percent: match ? Math.min(100, Number(match[1])) : null};
        }
        if (child('.transcoding')) return {state: 'processing'};
        const preview = child('.preview-new');
        if (preview) {
            const name = preview.querySelector('.name');
            return {state: name && name.textContent.trim() === filename ? 'ready' : 'other_video'};
        }
        if (child('.canceled') || Array.from(root.querySelectorAll('.canceled')).some(visible)) {
            return {state: 'idle'};
        }
    }
    return {state: 'pending'};
}"""


async def _xhs_upload_state(page, filename: str) -> dict:
    if page.is_closed():
        raise VideoStageError("小红书视频上传期间浏览器已关闭；尚未点击发布，请先核对平台草稿")
    try:
        parsed = urlsplit(page.url)
        if parsed.scheme != "https" or parsed.hostname != SITES["xiaohongshu"].host or parsed.port not in {None, 443}:
            raise VideoStageError("小红书视频上传页面已离开创作中心；尚未点击发布，请先核对平台草稿")
        if any(part in parsed.path for part in ("login", "signin")):
            raise VideoStageError("小红书视频上传期间登录已失效；尚未点击发布，请重新连接账号并核对平台草稿")
        if parsed.path.rstrip("/") != "/publish/publish":
            raise VideoStageError("小红书视频上传页面已切换；尚未点击发布，请先核对平台草稿")
        state = await page.evaluate(_XHS_UPLOAD_STATE, {"selector": SITES["xiaohongshu"].upload, "filename": filename})
    except VideoStageError:
        raise
    except Exception:
        if page.is_closed():
            raise VideoStageError("小红书视频上传期间浏览器已关闭；尚未点击发布，请先核对平台草稿") from None
        raise VideoStageError("无法读取小红书视频上传进度；尚未点击发布，请先核对平台草稿和网络") from None
    if state.get("state") == "auth":
        raise VideoStageError("小红书视频上传期间需要重新登录或安全验证；尚未点击发布，请完成验证并核对平台草稿")
    return state


async def _xhs_wait_upload(page, filename: str, ctx: ProviderContext, *, seconds: float = UPLOAD_SECONDS) -> None:
    """Keep the browser open through upload and processing, before editing.

    The caller first rejects an existing preview, then selects the file once.
    Matching the preview filename prevents an unrelated video from satisfying
    this wait. The durable upload fence remains in force for every failure.
    """
    last_message = ""
    seen_active = False
    try:
        async with asyncio.timeout(seconds):
            while True:
                state = await _xhs_upload_state(page, filename)
                phase = state["state"]
                if phase == "ready":
                    ctx.progress("小红书视频上传及处理完成，正在填写发布信息")
                    return
                if phase == "failed":
                    raise VideoStageError("小红书页面显示视频上传失败；尚未点击发布，请检查视频格式、大小和网络，并先核对平台草稿")
                if phase == "other_video":
                    raise VideoStageError("小红书页面的视频与本次文件不一致；尚未点击发布，请先核对平台草稿")
                if phase == "idle" and seen_active:
                    raise VideoStageError("小红书视频上传已取消；尚未点击发布，请先核对平台草稿")
                seen_active = seen_active or phase in {"uploading", "processing"}
                if phase == "processing":
                    message = "小红书视频已传输，等待平台转码处理完成"
                elif phase == "uploading" and state.get("percent") is not None:
                    message = f"小红书视频上传中：{state['percent']}%，请保持发布窗口打开"
                else:
                    message = "等待小红书确认视频上传完成，请保持发布窗口打开"
                if message != last_message:
                    ctx.progress(message)
                    last_message = message
                await asyncio.sleep(1)
    except TimeoutError:
        raise VideoStageError("等待小红书视频上传及处理超过 20 分钟；尚未点击发布，请检查网络并先核对平台草稿") from None


def _douyin_creator_page(page) -> bool:
    try:
        parsed = urlsplit(page.url)
        return parsed.scheme == "https" and parsed.hostname == SITES["douyin"].host and parsed.port in {None, 443} and parsed.path.startswith("/creator-micro/")
    except ValueError:
        return False


async def _douyin_challenge(page) -> bool:
    for marker in ("扫码登录", "手机号登录", "二维码失效", "登录验证", "安全验证"):
        if await _visible(page.get_by_text(marker, exact=True)):
            return True
    if await _visible(page.get_by_role("img", name="二维码", exact=True)):
        return True
    return await _visible(page.locator('input[placeholder*="验证码"], input[placeholder*="短信"]'))


async def _douyin_account_ready(page) -> bool:
    """Verify the current browser session, independently of the upload widget.

    The public creator app calls this read-only endpoint and requires a uid
    with status_code 0. QR login can land on /creator-micro/home, where there
    is no video file input. Never infer authentication from the route alone.
    """
    if not _douyin_creator_page(page) or await _douyin_challenge(page):
        return False
    original_url = page.url
    navigated = False

    def on_navigation(frame):
        nonlocal navigated
        if frame == page.main_frame:
            navigated = True

    page.on("framenavigated", on_navigation)
    try:
        # Run inside the actual creator page so the browser retains its normal
        # cookie/security context. Only a boolean crosses back into Python;
        # account details and raw responses never enter progress or logs.
        async with asyncio.timeout(6):
            confirmed = await page.evaluate("""async (path) => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 5000);
                try {
                    const response = await fetch(path, {
                        method: 'GET', credentials: 'include', redirect: 'error',
                        cache: 'no-store', signal: controller.signal
                    });
                    if (!response.ok || new URL(response.url).origin !== location.origin) return false;
                    const text = await response.text();
                    if (text.length > 1024 * 1024) return false;
                    const value = JSON.parse(text);
                    return value.status_code === 0 && (
                        (typeof value.uid === 'string' && /^[1-9][0-9]*$/.test(value.uid)) ||
                        (typeof value.uid === 'number' && Number.isInteger(value.uid) && value.uid > 0)
                    );
                } catch (_) { return false; }
                finally { clearTimeout(timer); }
            }""", DOUYIN_USER_INFO)
    except Exception:
        return False
    finally:
        page.remove_listener("framenavigated", on_navigation)
    # A response from a previous navigation/account must not confirm the page
    # that replaced it. A visible second-factor prompt still requires the user.
    return confirmed is True and not navigated and not page.is_closed() and page.url == original_url and _douyin_creator_page(page) and not await _douyin_challenge(page)


def _platform(payload: PublishPayload) -> str:
    return ALIASES.get(payload.platform_id, payload.platform_id)


def _unavailable() -> ProviderResult:
    return ProviderResult("needs_attention", "此平台未接入浏览器视频发布驱动，请选择受支持的平台")


@asynccontextmanager
async def _browser_page(ctx: ProviderContext, *, load_session: bool = True):
    # Import lazily so validation and protocol tests do not require a browser.
    from patchright.async_api import async_playwright
    async with async_playwright() as driver:
        options = {"headless": False}
        if ctx.browser_path:
            options["executable_path"] = ctx.browser_path
        browser = await driver.chromium.launch(**options)
        try:
            options = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai"}
            if load_session and ctx.session_path.is_file():
                options["storage_state"] = str(ctx.session_path)
            context = await browser.new_context(**options)
            page = await context.new_page()
            page.set_default_timeout(30_000)
            yield context, page
        finally:
            await browser.close()


async def _visible(locator) -> bool:
    return bool(await locator.count() and await locator.first.is_visible())


async def _logged_in(page, platform: str) -> bool:
    if platform == "douyin":
        return await _douyin_account_ready(page)
    site = SITES[platform]
    parsed = urlsplit(page.url)
    if parsed.hostname != site.host or any(part in parsed.path for part in ("login", "signin")):
        return False
    for frame in page.frames:
        if "open.weixin.qq.com/connect/qrconnect" in frame.url:
            return False
    if platform == "xiaohongshu":
        if await _visible(page.locator('div[class*="login-box"]')):
            return False
        return bool(await page.locator(site.upload).count())
    return await _visible(page.locator("button.weui-desktop-btn").filter(has_text="发表视频"))


async def _wait_login(page, platform: str, seconds: float, ctx: ProviderContext | None = None):
    unconfirmed_since = None
    last_message = ""
    async with asyncio.timeout(seconds):
        while True:
            # QR/login flows can open a new creator tab. It belongs to the
            # same isolated account context; unrelated windows are not used.
            pages = list(reversed(page.context.pages)) if platform == "douyin" else [page]
            pages = [candidate for candidate in pages if not candidate.is_closed()]
            if not pages:
                raise LoginPageError("登录浏览器已关闭，请重新连接账号")
            creator_seen = False
            challenge_seen = False
            for candidate in pages:
                if platform == "douyin":
                    if not _douyin_creator_page(candidate):
                        continue
                    creator_seen = True
                    if await _douyin_challenge(candidate):
                        challenge_seen = True
                        continue
                if await _logged_in(candidate, platform):
                    return candidate
            if platform == "douyin":
                message = ""
                if challenge_seen:
                    message = "请在抖音浏览器中完成扫码确认、短信或安全验证"
                    unconfirmed_since = None
                elif creator_seen:
                    message = "已进入抖音创作者中心，正在确认账号登录状态"
                    unconfirmed_since = unconfirmed_since or asyncio.get_running_loop().time()
                    if asyncio.get_running_loop().time() - unconfirmed_since >= DOUYIN_VERIFY_SECONDS:
                        raise LoginPageError("已进入抖音创作者中心，但账号状态接口未能确认登录；请在浏览器中检查网络及平台验证后重新连接")
                else:
                    unconfirmed_since = None
                if ctx and message and message != last_message:
                    ctx.progress(message)
                    last_message = message
            await asyncio.sleep(1)


async def _douyin_upload_page(page, ctx: ProviderContext) -> None:
    """Only publishing needs a ready upload control; account login does not."""
    ctx.progress("已确认抖音登录，正在打开视频上传入口")
    try:
        if urlsplit(page.url).path.rstrip("/") != "/creator-micro/content/upload":
            await page.goto(SITES["douyin"].url, wait_until="domcontentloaded", timeout=60_000)
        await page.locator(SITES["douyin"].upload).first.wait_for(state="attached", timeout=45_000)
        if not _douyin_creator_page(page) or urlsplit(page.url).path.rstrip("/") != "/creator-micro/content/upload" or await _douyin_challenge(page):
            raise LoginPageError("抖音上传入口仍需登录或平台验证，请在创作中心完成后重试")
    except LoginPageError:
        raise
    except Exception:
        raise LoginPageError("抖音账号已登录，但未找到视频上传入口；请检查账号投稿权限或平台页面后重试") from None


async def login(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    platform = _platform(payload)
    if platform not in SITES:
        return _unavailable()
    site = SITES[platform]
    ctx.progress(f"请在打开的浏览器中完成{site.label}登录及平台验证")
    try:
        async with _browser_page(ctx, load_session=False) as (context, page):
            await page.goto(site.url, wait_until="domcontentloaded", timeout=60_000)
            await _wait_login(page, platform, LOGIN_SECONDS, ctx)
            ctx.progress(f"已确认{site.label}登录，正在保存加密会话")
            ctx.session_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(ctx.session_path))
        return ProviderResult("ready", f"{site.label}登录会话已保存")
    except TimeoutError:
        return ProviderResult("needs_attention", "等待登录超时，请重新打开登录浏览器并完成验证")
    except LoginPageError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "无法完成登录，请检查网络、发布浏览器及平台页面后重试")


async def check(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    platform = _platform(payload)
    if platform not in SITES:
        return _unavailable()
    if not ctx.session_path.is_file():
        return ProviderResult("login_required", "请先登录此账号")
    try:
        async with _browser_page(ctx) as (context, page):
            await page.goto(SITES[platform].url, wait_until="domcontentloaded", timeout=60_000)
            try:
                await _wait_login(page, platform, 25, ctx)
            except TimeoutError:
                return ProviderResult("login_required", "尚未确认有效登录，请重新登录并完成平台验证")
            await context.storage_state(path=str(ctx.session_path))
        return ProviderResult("ready", "已确认抖音账号登录有效" if platform == "douyin" else "已确认当前账号可访问发布入口")
    except LoginPageError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "账号检查未完成，请检查网络或浏览器后重试")


def _validate(payload: PublishPayload, platform: str) -> str | None:
    if payload.kind != "video":
        return "该平台当前仅接入视频发布，图片笔记不能作为文章发布"
    if payload.mode == "draft" and platform != "channels":
        return "该平台当前未接入保存草稿，请选择正式提交"
    if not payload.title.strip() or len(payload.title) > SITES[platform].title_limit:
        return f"标题需要 1–{SITES[platform].title_limit} 个字符；不会自动截断标题"
    if platform == "channels" and len(payload.title) < 7:
        return "视频号短标题需要 7–15 个字符，请调整标题后重试"
    if not payload.video_path or not Path(payload.video_path).is_file():
        return "视频文件不存在，请重新导入"
    if Path(payload.video_path).suffix.lower() not in VIDEO_EXTENSIONS:
        return "此视频扩展名暂不支持，请使用 MP4、MOV 或其他支持的视频格式"
    if payload.cover_path and not Path(payload.cover_path).is_file():
        return "封面文件不存在，请重新导入"
    if platform == "douyin" and payload.cover_path:
        return "抖音自定义封面尚未接入；请取消自定义封面，或在平台手工设置"
    if platform == "xiaohongshu" and len(payload.tags) > 10:
        return "小红书最多支持 10 个话题；请减少话题后重试"
    return None


async def _wait_enabled(button, page, timeout: float = UPLOAD_SECONDS) -> None:
    async with asyncio.timeout(timeout):
        while True:
            if await button.count() and await button.is_visible():
                classes = await button.get_attribute("class") or ""
                if await button.is_enabled() and "weui-desktop-btn_disabled" not in classes:
                    return
            if await _visible(page.locator("div.status-msg.error, div.progress-div > div:has-text('上传失败')")):
                raise RuntimeError("upload_failed")
            await asyncio.sleep(1)


async def _fill_editor(page, editor, text: str) -> None:
    await editor.click()
    await page.keyboard.press("ControlOrMeta+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.insert_text(text)


async def _xhs_cover(page, cover: Path) -> None:
    # Exact upstream cover workflow; failure stops submission instead of silently
    # substituting the video's first frame for the requested cover.
    entry = page.locator("div.upload-cover").first
    if not await entry.count():
        entry = page.locator("div.cover-plugin-preview div.default.pointer").first
    await entry.click()
    await page.get_by_text("上传封面", exact=True).first.click()
    file_input = page.locator('div.upload-wrapper input[type="file"][accept*="image"]').first
    await file_input.set_input_files(str(cover))
    await page.locator("div.d-modal-footer").get_by_text("确定", exact=True).first.click()
    await page.locator("div.d-modal").first.wait_for(state="hidden", timeout=30_000)


async def _channels_cover(frame, cover: Path) -> None:
    await frame.locator('div.horizontal-cover-wrap:has-text("4:3")').first.click()
    dialog = frame.locator("div.weui-desktop-dialog").filter(has_text="编辑视频号动态").first
    if not await dialog.count():
        dialog = frame.locator("div.weui-desktop-dialog").filter(has_text="封面").first
    await dialog.locator('.single-cover-uploader-wrap input[type="file"]').first.set_input_files(str(cover))
    await dialog.locator('div.weui-desktop-dialog__ft button.weui-desktop-btn_primary:has-text("确认")').first.click()
    await dialog.wait_for(state="hidden", timeout=30_000)


async def _prepare(page, platform: str, payload: PublishPayload, ctx: ProviderContext):
    site = SITES[platform]
    frame = page
    if platform == "channels":
        # Opening the editor may create an empty platform draft.
        ctx.submitting("正在打开视频号编辑器；中断后请先核对平台草稿和作品")
        await page.locator("button.weui-desktop-btn").filter(has_text="发表视频").first.click()
        # The editor is an asynchronously mounted iframe. Never replace a
        # missing upload control with an unrelated generic input.
        async with asyncio.timeout(60):
            while True:
                found = None
                for candidate in page.frames:
                    candidate_url = urlsplit(candidate.url)
                    if candidate_url.hostname == site.host and await candidate.locator('input[type="file"]').count():
                        found = candidate
                        break
                if found:
                    frame = found
                    break
                await asyncio.sleep(1)
    upload = frame.locator(site.upload).first
    await upload.wait_for(state="attached", timeout=60_000)
    if platform == "xiaohongshu":
        state = await _xhs_upload_state(page, payload.video_path.name)
        if state["state"] not in {"pending", "idle"}:
            raise VideoStageError("小红书编辑器已有视频或上传任务；尚未选择本次文件，请先核对平台草稿后重新打开发布页")
    # Uploading creates platform-side media/drafts; persist the fence first.
    if platform != "channels":
        ctx.submitting("正在上传视频；中断后请先核对平台草稿和作品，避免重复上传")
    try:
        await upload.set_input_files(str(payload.video_path))
    except Exception:
        if platform == "xiaohongshu":
            raise VideoStageError("小红书开始上传视频时中断；尚未点击发布，请先核对平台草稿和网络") from None
        raise
    ctx.progress("视频上传中，等待平台处理并填写信息")
    if platform == "douyin":
        title = page.locator('input[placeholder*="填写作品标题"]').first
        await title.wait_for(state="visible", timeout=UPLOAD_SECONDS * 1000)
        await title.fill(payload.title)
        editor = page.locator('div.zone-container[contenteditable="true"]').first
        await _fill_editor(page, editor, payload.description)
        for tag in payload.tags:
            await page.keyboard.insert_text(" #" + tag)
            await page.keyboard.press("Space")
        await page.keyboard.press("Escape")
        await page.locator('[class^="long-card"] div:has-text("重新上传")').first.wait_for(state="visible", timeout=UPLOAD_SECONDS * 1000)
        return page.get_by_role("button", name="发布", exact=True).first
    if platform == "xiaohongshu":
        await _xhs_wait_upload(page, payload.video_path.name, ctx)
        stage = "标题"
        try:
            title = page.locator('input[placeholder*="填写标题"]').first
            await title.wait_for(state="visible", timeout=45_000)
            await title.fill(payload.title)
            stage = "正文"
            editor = page.locator('p[data-placeholder*="输入正文描述"]').first
            await _fill_editor(page, editor, payload.description)
            stage = "话题"
            for tag in payload.tags:
                await page.keyboard.insert_text("#" + tag)
                item = page.locator("#creator-editor-topic-container .item").filter(has_text=tag).first
                await item.click(timeout=10_000)
            if payload.cover_path:
                stage = "封面"
                await _xhs_cover(page, payload.cover_path)
        except Exception:
            raise VideoStageError(f"小红书视频已上传，但填写{stage}未完成；尚未点击发布，请先核对平台草稿及页面提示") from None
        return page.get_by_role("button", name="发布", exact=True).first
    verification = frame.locator("div.weui-desktop-dialog__wrp:visible").filter(has_text="实名验证").first
    if await _visible(verification):
        ctx.progress("视频号要求管理员验证，请在发布浏览器中扫码完成")
        await verification.wait_for(state="hidden", timeout=LOGIN_SECONDS * 1000)
    body = "\n".join(part for part in (payload.title, " ".join("#" + tag for tag in payload.tags), payload.description) if part)
    await _fill_editor(page, frame.locator("div.input-editor").first, body)
    short_title = frame.locator('input[placeholder*="短标题"]')
    if await short_title.count():
        await short_title.first.fill(payload.title)
    if payload.cover_path:
        await _channels_cover(frame, payload.cover_path)
    name = "保存草稿" if payload.mode == "draft" else "发表"
    return frame.get_by_role("button", name=name, exact=True).first


async def _success_notice_present(page, platform: str, payload: PublishPayload) -> bool:
    # Exact titles can belong to old posts. Only a semantic, visible platform
    # notification can supplement the list transition as submission evidence.
    text = (r"^(?:草稿)?保存(?:草稿)?成功[！!。]?$" if payload.mode == "draft"
            else r"^(?:作品|视频)?(?:发布|发表|提交)成功[！!。]?(?:，.*)?$")
    for frame in page.frames:
        if urlsplit(frame.url).hostname != SITES[platform].host:
            continue
        notice = frame.locator('[role="status"], [role="alert"]').filter(has_text=re.compile(text))
        if await _visible(notice):
            return True
    return False


async def _confirm_receipt(page, platform: str, payload: PublishPayload) -> ProviderResult:
    """A click or disappearing button is never a successful receipt."""
    site = SITES[platform]
    if platform == "xiaohongshu":
        await page.wait_for_url(re.compile(r"^https://creator\.xiaohongshu\.com/publish/success(?:\?|$)"), timeout=CONFIRM_SECONDS * 1000)
        await page.get_by_text(re.compile(r"(?:发布|提交)成功")).first.wait_for(state="visible", timeout=15_000)
        return ProviderResult("submitted", "小红书已显示提交成功，作品仍可能需要审核", url="https://creator.xiaohongshu.com/manage/notes")
    if platform == "douyin":
        pattern = re.compile(r"^https://creator\.douyin\.com/creator-micro/content/manage(?:\?|/|$)")
    else:
        pattern = re.compile(r"^https://channels\.weixin\.qq\.com/platform/post/list(?:\?|/|$)")
    await page.wait_for_url(pattern, timeout=CONFIRM_SECONDS * 1000)
    # The pinned upstream supplies no stable per-submission ID for these
    # adapters. A manager URL and a matching old title are NOT a receipt.
    if not await _success_notice_present(page, platform, payload):
        return ProviderResult("unknown", "已返回平台列表，但未取得本次操作的明确成功通知；请核对原作品，勿重复提交", url=page.url.split("?")[0])
    status = "draft_created" if payload.mode == "draft" else "submitted"
    return ProviderResult(status, "平台已显示本次操作成功通知；审核与公开状态请在平台查看", url=page.url.split("?")[0])


async def _submit_once(page, button, platform: str, payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    await _wait_enabled(button, page)
    ctx.progress("正在提交一次，等待平台确认")
    try:
        if platform != "xiaohongshu" and await _success_notice_present(page, platform, payload):
            return ProviderResult("unknown", "页面已有成功提示，无法区分本次回执；尚未点击发布，请先核对平台草稿", url=SITES[platform].url)
        # A timeout can occur after the click reached the server: never retry.
        await button.click(timeout=15_000)
        return await _confirm_receipt(page, platform, payload)
    except Exception:
        return ProviderResult("unknown", "已尝试提交，尚未获得明确回执；请先在平台核对原作品，勿直接重复发布", url=SITES[platform].url)


async def publish(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    platform = _platform(payload)
    if platform not in SITES:
        return _unavailable()
    problem = _validate(payload, platform)
    if problem:
        return ProviderResult("needs_attention", problem)
    if not ctx.session_path.is_file():
        return ProviderResult("login_required", "请先登录此账号")
    mutated = False
    original_emit = ctx.emit

    def track(event: dict) -> None:
        nonlocal mutated
        if event.get("event") == "submitting":
            mutated = True
        original_emit(event)

    guarded_ctx = ProviderContext(ctx.session_path, ctx.workspace, ctx.browser_path, track)
    try:
        async with _browser_page(ctx) as (context, page):
            await page.goto(SITES[platform].url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page = await _wait_login(page, platform, 25, ctx)
            except TimeoutError:
                return ProviderResult("login_required", "请重新登录并完成平台验证后发布")
            if platform == "douyin":
                await _douyin_upload_page(page, ctx)
            async with asyncio.timeout(UPLOAD_SECONDS + LOGIN_SECONDS + CONFIRM_SECONDS):
                button = await _prepare(page, platform, payload, guarded_ctx)
                result = await _submit_once(page, button, platform, payload, guarded_ctx)
            # Saving refreshed cookies must not turn a confirmed submission
            # into a retryable failure.
            with suppress(Exception):
                await context.storage_state(path=str(ctx.session_path))
            return result
    except asyncio.CancelledError:
        raise
    except VideoStageError as exc:
        return ProviderResult("unknown" if mutated else "needs_attention", str(exc), url=SITES[platform].url)
    except LoginPageError as exc:
        if mutated:
            return ProviderResult("unknown", "上传或编辑已开始，登录状态随后发生变化；请先在平台核对草稿及作品", url=SITES[platform].url)
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        if mutated:
            return ProviderResult("unknown", "上传或编辑已开始，但流程未获得明确结果；请在平台核对草稿及作品后处理", url=SITES[platform].url)
        return ProviderResult("failed", "尚未上传视频；无法打开或识别平台发布页面，请检查浏览器与网络后重试")
