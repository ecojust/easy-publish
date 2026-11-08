"""Account-isolated video preparation and supervised manual browser publication.

Selectors adapted from dreammis/social-auto-upload, commit
3847fff01f5434a8a32829833163cd6723cabc59 (MIT, Copyright (c) 2023 dreammis).
See VIDEO_SOURCES.md. No upstream retry loops or runtime download code is used.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from ..browser import browser_launch_options
from ..target_projection import project_payload
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
ACCOUNT_VERIFY_SECONDS = 30
LOGIN_SETTLE_SECONDS = 2
SESSION_INVALID_SETTLE_SECONDS = 2
LOGIN_URLS = {
    "douyin": "https://creator.douyin.com/",
    "xiaohongshu": "https://creator.xiaohongshu.com/login",
    "channels": "https://channels.weixin.qq.com/",
}
ACCOUNT_URLS = {
    "douyin": "https://creator.douyin.com/creator-micro/home",
    "xiaohongshu": "https://creator.xiaohongshu.com/",
    "channels": "https://channels.weixin.qq.com/platform",
}


class LoginPageError(Exception):
    """A fixed, non-secret message that the user can act on."""


class SessionStorageError(LoginPageError):
    """A valid login could not be persisted; do not trigger another scan."""


class VideoStageError(Exception):
    """An authored stage-specific message, without platform data or secrets."""


@dataclass(frozen=True)
class ChannelsSubmission:
    frame: object
    # A requested cover must not be silently lost to a later editor reload.
    document: object | None = None


# Fixed upstream selectors, read only inside the current composer frame. The
# upstream waits for an enabled 发表 button after filling basic description;
# requiring it before filling any text can deadlock on a required field.
_CHANNELS_UPLOAD_STATE = """() => {
    const visible = el => !!el && Array.from(el.getClientRects()).some(rect => rect.width > 0 && rect.height > 0) &&
        getComputedStyle(el).visibility !== 'hidden';
    const all = selector => Array.from(document.querySelectorAll(selector)).filter(visible);
    const enabled = el => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
        !el.classList.contains('weui-desktop-btn_disabled');
    const dialogs = all('.weui-desktop-dialog__wrp, .weui-desktop-dialog');
    const media = all('div.media-status-content');
    const statusText = [...media, ...all('div.status-msg')].map(el => el.innerText).join(' ');
    const percent = statusText.match(/(\\d{1,3})(?:\\.\\d+)?\\s*%/);
    const progress = percent ? Math.min(100, Number(percent[1])) : null;
    const body = all('div.input-editor')[0];
    const title = all('input[placeholder*="短标题"]')[0];
    const bodyReady = enabled(body) && (body.isContentEditable || !!body.querySelector('[contenteditable="true"]'));
    const publish = all('div.form-btns button').find(el => el.textContent.trim() === '发表');
    return {
        failed: all('div.status-msg.error').length > 0,
        verification: dialogs.some(el => /实名验证|管理员验证/.test(el.textContent)),
        blocked: dialogs.length > 0,
        busy: (progress !== null && progress < 100) || /上传中|正在上传|处理中|正在处理|转码中|正在转码/.test(statusText),
        percent: progress,
        media: media.length > 0,
        removable: all('div.media-status-content div.tag-inner').some(el => el.textContent.trim() === '删除'),
        ready: enabled(publish),
        body: bodyReady ? body.innerText.replace(/\\r\\n/g, '\\n').trim() : null,
        title_present: !!document.querySelector('input[placeholder*="短标题"]'),
        title: title && enabled(title) ? title.value : null
    };
}"""


def _channels_body(payload: PublishPayload) -> str:
    return "\n".join(part for part in (payload.title, " ".join("#" + tag for tag in payload.tags), payload.description) if part)


def _channels_trusted_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.hostname == SITES["channels"].host and parsed.port in {None, 443}
    except ValueError:
        return False


def _channels_frame(page):
    """Reacquire the composer: its iframe may reload/remount after transcoding."""
    if page.is_closed():
        raise VideoStageError("视频号上传期间浏览器已关闭；尚未点击发表，请先核对平台草稿")
    if not _channels_trusted_url(page.url):
        raise VideoStageError("视频号发布页面已离开创作中心；尚未点击发表，请先核对平台草稿")
    if "login" in urlsplit(page.url).path or any("open.weixin.qq.com/connect/qrconnect" in frame.url for frame in page.frames):
        raise VideoStageError("视频号登录已失效；尚未点击发表，请重新连接账号并核对平台草稿")
    # The top-level /platform/post/create is a shell, including while its
    # iframe remounts. Never use unrelated shell inputs/errors as the editor.
    frames = [frame for frame in page.frames if not frame.is_detached() and _channels_trusted_url(frame.url)
              and urlsplit(frame.url).path.rstrip("/") == "/micro/content/post/create"]
    if len(frames) > 1:
        raise VideoStageError("视频号存在多个发布编辑器，无法确认本次上传；尚未点击发表，请先核对平台草稿")
    if frames:
        return frames[0]
    return None


async def _channels_wait_editor(page, *, seconds: float = 60):
    try:
        async with asyncio.timeout(seconds):
            while True:
                frame = _channels_frame(page)
                if frame:
                    try:
                        if await frame.locator(SITES["channels"].upload).count():
                            return frame
                    except Exception:
                        pass  # The composer can be replaced while it mounts.
                await asyncio.sleep(1)
    except TimeoutError:
        raise VideoStageError("视频号上传编辑器未能加载；尚未选择视频，请先检查平台页面及网络") from None


async def _channels_wait_upload(page, payload: PublishPayload, ctx: ProviderContext, *, seconds: float = UPLOAD_SECONDS):
    """One upload, bounded waits and repeatable text fills across iframe reloads.

    Readiness is the pinned upstream's enabled 发表 control, supplemented by
    matching text and absence of upload/processing/error/verification states.
    No filename or unverified completion API is assumed. A frame reload never
    selects the file again, and no publish click occurs inside this loop.
    """
    expected_body = _channels_body(payload).strip()
    last_message = ""
    verification_since = None
    try:
        async with asyncio.timeout(seconds):
            while True:
                frame = _channels_frame(page)
                state = None
                if frame:
                    try:
                        state = await frame.evaluate(_CHANNELS_UPLOAD_STATE)
                    except Exception:
                        # A document can disappear between frame lookup and
                        # evaluation at the end of transcoding. Reacquire it.
                        pass
                message = "等待视频号上传编辑器恢复，保持窗口打开"
                if state:
                    if state["failed"]:
                        raise VideoStageError("视频号页面显示视频上传失败；尚未点击发表，请检查网络及文件并先核对平台草稿")
                    if state["verification"]:
                        verification_since = verification_since or asyncio.get_running_loop().time()
                        if asyncio.get_running_loop().time() - verification_since >= LOGIN_SECONDS:
                            raise VideoStageError("视频号管理员验证等待超时；尚未点击发表，请完成平台验证并核对草稿")
                        message = "视频号要求管理员实名验证，请在发布窗口中完成验证"
                    elif state["blocked"]:
                        verification_since = None
                        message = "视频号有待处理的弹窗，请在发布窗口中确认后继续"
                    else:
                        verification_since = None
                        # Basic fields can be required to enable 发表. Try only
                        # actionable fields, within this upload budget; a short
                        # action timeout must not close a still-uploading page.
                        if state["body"] is not None and state["body"] != expected_body:
                            try:
                                async with asyncio.timeout(3):
                                    await _fill_editor(page, frame.locator("div.input-editor").first, _channels_body(payload))
                            except Exception:
                                pass
                            state = None  # Re-read actual text after any fill/reload.
                        elif state["title"] is not None and state["title"] != payload.title:
                            try:
                                async with asyncio.timeout(3):
                                    await frame.locator('input[placeholder*="短标题"]').first.fill(payload.title)
                            except Exception:
                                pass
                            state = None
                        if state:
                            title_ready = not state["title_present"] or state["title"] == payload.title
                            if state["ready"] and not state["busy"] and state["body"] == expected_body and title_ready:
                                ctx.progress("视频号上传及处理完成，正在确认发布信息")
                                return frame
                            if state["percent"] is not None and state["busy"]:
                                message = f"视频号视频上传处理中：{state['percent']}%，请保持发布窗口打开"
                            elif state["busy"]:
                                message = "视频号正在上传或处理视频，请保持发布窗口打开"
                            else:
                                message = "等待视频号完成上传处理并准备好发表控件，请保持窗口打开"
                        else:
                            message = "视频号正在上传，等待表单填写及页面刷新完成"
                if message != last_message:
                    ctx.progress(message)
                    last_message = message
                await asyncio.sleep(1)
    except TimeoutError:
        message = ("等待视频号上传、处理或表单就绪超时；尚未点击发表，请检查平台提示并先核对草稿（上传最多等待 20 分钟）"
                   if seconds >= UPLOAD_SECONDS else "等待视频号表单或提交控件就绪超时；尚未点击发表，请先核对平台草稿及页面提示")
        raise VideoStageError(message) from None


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
    markers = re.compile("^(扫码登录|手机号登录|二维码失效|登录验证|安全验证)$")
    if await _login_visible(page.get_by_text(markers, exact=True)):
        return True
    if await _login_visible(page.get_by_role("img", name="二维码", exact=True)):
        return True
    return await _login_visible(page.locator('input[placeholder*="验证码"], input[placeholder*="短信"]'))


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
                    if (response.status === 401) return false;
                    if (!response.ok || new URL(response.url).origin !== location.origin) return null;
                    const text = await response.text();
                    if (text.length > 1024 * 1024) return false;
                    const value = JSON.parse(text);
                    if (value && typeof value.status_code === 'number' && value.status_code !== 0) return false;
                    if (!value || value.status_code !== 0 || !Object.hasOwn(value, 'uid')) return null;
                    const hasUid = (typeof value.uid === 'string' && /^[1-9][0-9]*$/.test(value.uid)) ||
                        (typeof value.uid === 'number' && Number.isInteger(value.uid) && value.uid > 0);
                    return hasUid;
                } catch (_) { return null; }
                finally { clearTimeout(timer); }
            }""", DOUYIN_USER_INFO)
    except Exception:
        confirmed = None
    finally:
        page.remove_listener("framenavigated", on_navigation)
    # A response from a previous navigation/account must not confirm the page
    # that replaced it. A visible second-factor prompt still requires the user.
    if navigated or page.is_closed() or page.url != original_url or not _douyin_creator_page(page) or await _douyin_challenge(page):
        return False
    if confirmed is not None:
        return confirmed is True  # An explicit unauthenticated API result vetoes fallback.
    try:
        # The upstream CLI verifies sessionid after login. Combine that signal
        # with an authenticated creator control when its optional API is down.
        cookies = await page.context.cookies(["https://creator.douyin.com/"])
        session = any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies)
        control = (await _login_visible(page.get_by_role("button", name="发布视频", exact=True))
                   or await _login_visible(page.get_by_role("link", name="发布视频", exact=True)))
        return bool(session and control and page.url == original_url and not page.is_closed()
                    and _douyin_creator_page(page) and not await _douyin_challenge(page))
    except Exception:
        return False


def _platform(payload: PublishPayload) -> str:
    return ALIASES.get(payload.platform_id, payload.platform_id)


def _unavailable() -> ProviderResult:
    return ProviderResult("needs_attention", "此平台未接入浏览器视频发布驱动，请选择受支持的平台")


@asynccontextmanager
async def _browser_page(ctx: ProviderContext, *, load_session: bool = True, headless: bool = False, platform: str | None = None):
    # Import lazily so validation and protocol tests do not require a browser.
    from patchright.async_api import async_playwright
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(**browser_launch_options(ctx.browser_path, headless=headless))
        try:
            options = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai"}
            if load_session and ctx.session_path.is_file():
                if platform == "xiaohongshu":
                    # This is display metadata, not authentication. Require the
                    # new document to populate it from its current account load,
                    # instead of accepting a stale avatar from restored storage.
                    state = json.loads(ctx.session_path.read_text(encoding="utf-8"))
                    for origin in state.get("origins", []):
                        if origin.get("origin") == "https://creator.xiaohongshu.com":
                            origin["localStorage"] = [item for item in origin.get("localStorage", [])
                                                      if item.get("name") not in {"USER_INFO", "USER_INFO_FOR_BIZ"}]
                    options["storage_state"] = state
                else:
                    options["storage_state"] = str(ctx.session_path)
            context = await browser.new_context(**options)
            page = await context.new_page()
            page.set_default_timeout(30_000)
            yield context, page
        finally:
            await browser.close()


async def _visible(locator) -> bool:
    return bool(await locator.count() and await locator.first.is_visible())


async def _login_visible(locator) -> bool:
    # Hidden stale login widgets must not hide a later visible challenge.
    count = await locator.count()
    for index in range(min(count, 30)):
        candidate = locator.first if count == 1 else locator.nth(index)
        if await candidate.is_visible():
            return True
    return False


def _creator_page(page, platform: str) -> bool:
    if page.is_closed():
        return False
    if platform == "douyin":
        return _douyin_creator_page(page)
    try:
        parsed = urlsplit(page.url)
        if (parsed.scheme != "https" or parsed.hostname != SITES[platform].host
                or parsed.port not in {None, 443} or parsed.username or parsed.password
                or any(part in parsed.path.lower() for part in ("login", "signin"))):
            return False
        return platform == "xiaohongshu" or parsed.path == "/platform" or parsed.path.startswith("/platform/")
    except ValueError:
        return False


async def _login_challenge(page, platform: str) -> bool:
    if platform == "douyin":
        return await _douyin_challenge(page)
    markers = re.compile("^(扫码登录|手机号登录|二维码失效|登录验证|安全验证|管理员验证|实名验证)$")
    if await _login_visible(page.get_by_text(markers, exact=True)):
        return True
    if await _login_visible(page.locator('input[placeholder*="验证码"], input[placeholder*="短信"], div[class*="login-box"], .login-qrcode-wrap, .qrcode-wrap')):
        return True
    for frame in page.frames:
        if "open.weixin.qq.com/connect/qrconnect" in frame.url or "login-for-iframe" in frame.url:
            try:
                if await (await frame.frame_element()).is_visible():
                    return True
            except Exception:
                return True
    return False


async def _channels_entry(page):
    buttons = page.get_by_role("button", name="发表视频", exact=True)
    for index in range(min(await buttons.count(), 20)):
        button = buttons.nth(index)
        if await button.is_visible():
            return button
    links = page.get_by_role("link", name="发表视频", exact=True)
    for index in range(min(await links.count(), 20)):
        link = links.nth(index)
        target = urljoin(page.url, await link.get_attribute("href") or "")
        if (await link.is_visible() and _channels_trusted_url(target)
                and urlsplit(target).path.rstrip("/") == "/platform/post/create"):
            return link
    return None


async def _read_login_evidence(page, platform: str) -> bool:
    if platform == "douyin":
        return await _douyin_account_ready(page)
    if not _creator_page(page, platform) or await _login_challenge(page, platform):
        return False
    original_url = page.url
    if platform == "xiaohongshu":
        # First-party creator header + this document's freshly populated account
        # metadata. A URL, upload widget or restored localStorage alone is not
        # evidence of login. Account details never leave the browser context.
        ready = await page.evaluate("""() => {
            const visible = el => !!el && el.getClientRects().length > 0 &&
                getComputedStyle(el).visibility !== 'hidden';
            try {
                const user = JSON.parse(localStorage.getItem('USER_INFO_FOR_BIZ'));
                if (!user || typeof user.userId !== 'string' || !user.userId.trim() ||
                    typeof user.userName !== 'string' || !user.userName.trim()) return false;
                return Array.from(document.querySelectorAll('.user-info')).some(header =>
                    visible(header) && Array.from(header.querySelectorAll('img.user_avatar')).some(img =>
                        visible(img) && !!img.getAttribute('src')) &&
                    Array.from(header.querySelectorAll('span')).some(name =>
                        visible(name) && name.textContent.trim() === user.userName.trim()));
            } catch (_) { return false; }
        }""")
    else:
        ready = await _channels_entry(page) is not None
        if not ready:
            for frame in page.frames:
                if (not frame.is_detached() and _channels_trusted_url(frame.url)
                        and urlsplit(frame.url).path.rstrip("/") == "/micro/content/post/create"):
                    # Authenticated editor variants need not expose the home
                    # button. Merely having an empty editor iframe is not enough.
                    editor = await _login_visible(frame.locator('div.input-editor[contenteditable="true"], div.input-editor [contenteditable="true"]'))
                    actions = await _login_visible(frame.get_by_role("button", name=re.compile("^(发表|保存草稿)$")))
                    ready = editor and actions
                    if ready:
                        break
    return bool(ready and page.url == original_url and _creator_page(page, platform)
                and not await _login_challenge(page, platform))


async def _logged_in(page, platform: str) -> bool:
    if platform == "douyin":
        return await _douyin_account_ready(page)
    if not _creator_page(page, platform):
        return False
    original_url = page.url
    navigated = False

    def on_navigation(frame):
        nonlocal navigated
        if frame == page.main_frame:
            navigated = True

    page.on("framenavigated", on_navigation)
    try:
        ready = await _read_login_evidence(page, platform)
        return bool(ready and not navigated and not page.is_closed() and page.url == original_url)
    finally:
        page.remove_listener("framenavigated", on_navigation)


async def _wait_login(page, platform: str, seconds: float, ctx: ProviderContext | None = None, *, settle_seconds: float = 0, interactive: bool = True):
    unconfirmed_since = None
    last_message = ""
    confirmed_page = None
    confirmed_url = ""
    confirmed_since = None
    invalid_since = None
    async with asyncio.timeout(seconds):
        while True:
            # All platforms can open a new creator tab after scanning. A stale
            # QR tab must not hide the authenticated tab in the same context.
            pages = [candidate for candidate in reversed(page.context.pages) if not candidate.is_closed()]
            if not pages:
                raise LoginPageError("登录浏览器已关闭，请重新连接账号")
            creator_seen = False
            challenge_seen = False
            ready_page = None
            for candidate in pages:
                if not _creator_page(candidate, platform):
                    if not interactive:
                        parsed = urlsplit(candidate.url)
                        if parsed.hostname == SITES[platform].host:
                            challenge_seen = challenge_seen or any(part in parsed.path.lower() for part in ("login", "signin"))
                            if not challenge_seen:
                                challenge_seen = await _login_challenge(candidate, platform)
                    continue
                creator_seen = True
                try:
                    if await _logged_in(candidate, platform):
                        ready_page = candidate
                        break
                    if await _login_challenge(candidate, platform):
                        challenge_seen = True
                except Exception:
                    if candidate.is_closed():
                        continue
                    # Navigation can replace the document during read-only
                    # account checks. Reacquire instead of closing the login UI.
                    continue
            if ready_page:
                invalid_since = None
                if ready_page != confirmed_page or ready_page.url != confirmed_url:
                    confirmed_page, confirmed_url = ready_page, ready_page.url
                    confirmed_since = asyncio.get_running_loop().time()
                if asyncio.get_running_loop().time() - confirmed_since >= settle_seconds:
                    return ready_page
                message = f"已识别{SITES[platform].label}账号，正在确认并保存登录状态"
            else:
                confirmed_page = confirmed_since = None
                if not interactive and challenge_seen:
                    invalid_since = invalid_since or asyncio.get_running_loop().time()
                    if asyncio.get_running_loop().time() - invalid_since >= SESSION_INVALID_SETTLE_SECONDS:
                        raise LoginPageError("应用保存的登录会话已失效，请重新连接账号")
                else:
                    invalid_since = None
                message = f"请在{SITES[platform].label}登录窗口完成扫码确认及平台验证"
                if challenge_seen:
                    unconfirmed_since = None
                elif creator_seen:
                    message = f"已进入{SITES[platform].label}创作者中心，正在确认账号登录状态"
                    unconfirmed_since = unconfirmed_since or asyncio.get_running_loop().time()
                    limit = DOUYIN_VERIFY_SECONDS if platform == "douyin" else ACCOUNT_VERIFY_SECONDS
                    if asyncio.get_running_loop().time() - unconfirmed_since >= limit:
                        raise LoginPageError(f"已进入{SITES[platform].label}创作者中心，但尚未取得账号登录确认；请检查网络及平台验证后重新连接")
                else:
                    unconfirmed_since = None
            if ctx and message != last_message:
                ctx.progress(message)
                last_message = message
            await asyncio.sleep(0.5)


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


async def _saved_session_ready(platform: str, ctx: ProviderContext) -> bool:
    """Check only this app's saved state, quietly, before requesting another scan."""
    async with _browser_page(ctx, headless=True, platform=platform) as (context, page):
        await page.goto(ACCOUNT_URLS[platform], wait_until="domcontentloaded", timeout=60_000)
        try:
            await _wait_login(page, platform, 25, settle_seconds=LOGIN_SETTLE_SECONDS, interactive=False)
        except (TimeoutError, LoginPageError):
            return False
        # Keep this outside the invalid-session catch: a failed write is a
        # storage problem, not a reason to discard a working account login.
        try:
            await context.storage_state(path=str(ctx.session_path))
        except Exception:
            raise SessionStorageError("账号登录有效，但应用会话保存失败；请检查应用数据目录的读写权限") from None
        return True


async def login(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    platform = _platform(payload)
    if platform not in SITES:
        return _unavailable()
    site = SITES[platform]
    try:
        if ctx.session_path.is_file():
            ctx.progress(f"正在检查应用已保存的{site.label}登录状态")
            try:
                restored = await _saved_session_ready(platform, ctx)
            except SessionStorageError:
                raise
            except Exception:
                restored = False
            if restored:
                return ProviderResult("ready", f"已复用应用保存的{site.label}登录会话")
            ctx.progress(f"已保存的{site.label}会话未能确认，正在打开登录窗口")
        ctx.progress(f"请在打开的浏览器中完成{site.label}登录及平台验证")
        async with _browser_page(ctx, load_session=False, platform=platform) as (context, page):
            await page.goto(LOGIN_URLS[platform], wait_until="domcontentloaded", timeout=60_000)
            await _wait_login(page, platform, LOGIN_SECONDS, ctx, settle_seconds=LOGIN_SETTLE_SECONDS)
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
        if await _saved_session_ready(platform, ctx):
            return ProviderResult("ready", f"已确认{SITES[platform].label}账号登录有效")
        return ProviderResult("login_required", "尚未确认有效登录，请重新登录并完成平台验证")
    except SessionStorageError as exc:
        return ProviderResult("needs_attention", str(exc))
    except Exception:
        return ProviderResult("needs_attention", "账号检查未完成，请检查网络或浏览器后重试")


def _validate(payload: PublishPayload, platform: str) -> str | None:
    payload = project_payload(payload, platform)
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


async def _xhs_wait_submit_button(page, ctx: ProviderContext, *, seconds: float = 60):
    """Resolve only the current composer action, never an unrelated publish button.

    Patchright supports the current component's closed shadow root. Scope its
    normal role locator to that component instead of selecting the first global
    button. The legacy editor is supported only if no current component has
    appeared during this wait; a remount cannot redirect the intended action.
    """
    ctx.progress("小红书发布信息已填写，正在确认发布按钮可用")
    saw_component = False
    try:
        async with asyncio.timeout(seconds):
            while True:
                if page.is_closed():
                    raise VideoStageError("小红书提交前浏览器已关闭；尚未点击发布，请先核对平台草稿")
                parsed = urlsplit(page.url)
                if (parsed.scheme != "https" or parsed.hostname != SITES["xiaohongshu"].host
                        or parsed.port not in {None, 443} or parsed.path.rstrip("/") != "/publish/publish"):
                    raise VideoStageError("小红书提交前已离开本次发布页面；尚未点击发布，请先核对平台草稿")
                try:
                    components = page.locator("xhs-publish-btn")
                    if await components.count():
                        saw_component = True
                        current = components.filter(visible=True)
                        count = await current.count()
                        if count > 1:
                            raise VideoStageError("小红书存在多个可见发布区域，无法确认本次按钮；尚未点击发布，请先核对平台页面")
                        if not count:
                            await asyncio.sleep(1)
                            continue
                        buttons = current.get_by_role("button", name="发布", exact=True).filter(visible=True)
                    elif not saw_component:
                        buttons = page.get_by_role("button", name="发布", exact=True).filter(visible=True)
                    else:
                        # Do not switch to an unrelated legacy-looking button
                        # while the current component is being replaced.
                        await asyncio.sleep(1)
                        continue
                    count = await buttons.count()
                    if count > 1:
                        raise VideoStageError("小红书存在多个可见发布按钮，无法确认本次提交；尚未点击发布，请先核对平台页面")
                    if count == 1:
                        button = buttons.first
                        if (await button.is_enabled()
                                and await button.get_attribute("aria-busy") != "true"
                                and "loading" not in (await button.get_attribute("class") or "").split()):
                            handle = await button.element_handle()
                            if handle is not None:
                                return handle
                except VideoStageError:
                    raise
                except Exception:
                    # Vue may replace the component during a read. Reacquire
                    # within this deadline; the actual click is never retried.
                    pass
                await asyncio.sleep(1)
    except TimeoutError:
        raise VideoStageError("小红书发布按钮在 60 秒内未能确认可用；尚未点击发布，请检查页面必填项、验证或弹窗，并先核对平台草稿") from None


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
    await frame.locator('div.horizontal-cover-wrap:visible:has-text("4:3")').first.click()
    # Old hidden cover dialogs remain mounted in this platform's DOM.
    dialog = frame.locator("div.weui-desktop-dialog:visible").filter(has_text="编辑视频号动态").first
    if not await dialog.count():
        dialog = frame.locator("div.weui-desktop-dialog:visible").filter(has_text="封面").first
    await dialog.locator('.single-cover-uploader-wrap input[type="file"]').first.set_input_files(str(cover))
    confirm = dialog.locator('div.weui-desktop-dialog__ft button.weui-desktop-btn_primary:has-text("确认")').first
    await _wait_enabled(confirm, dialog, timeout=120)
    await confirm.click(timeout=15_000)
    await dialog.wait_for(state="hidden", timeout=60_000)


async def _prepare(page, platform: str, payload: PublishPayload, ctx: ProviderContext):
    site = SITES[platform]
    frame = page
    if platform == "channels":
        entry = await _channels_entry(page)
        if entry is None:
            raise VideoStageError("视频号账号已登录，但未找到发表视频入口；尚未选择视频，请检查平台页面")
        # Opening the editor may create an empty platform draft.
        ctx.submitting("正在打开视频号编辑器；中断后请先核对平台草稿和作品")
        await entry.click()
        frame = await _channels_wait_editor(page)
    upload = frame.locator(site.upload).first
    await upload.wait_for(state="attached", timeout=60_000)
    if platform == "xiaohongshu":
        state = await _xhs_upload_state(page, payload.video_path.name)
        if state["state"] not in {"pending", "idle"}:
            raise VideoStageError("小红书编辑器已有视频或上传任务；尚未选择本次文件，请先核对平台草稿后重新打开发布页")
    if platform == "channels":
        state = await frame.evaluate(_CHANNELS_UPLOAD_STATE)
        if state["removable"] or state["busy"] or state["body"]:
            raise VideoStageError("视频号编辑器已有视频、正文或上传任务；尚未选择本次文件，请先核对平台草稿")
    # Uploading creates platform-side media/drafts; persist the fence first.
    if platform != "channels":
        ctx.submitting("正在上传视频；中断后请先核对平台草稿和作品，避免重复上传")
    try:
        await upload.set_input_files(str(payload.video_path))
    except Exception:
        if platform == "xiaohongshu":
            raise VideoStageError("小红书开始上传视频时中断；尚未点击发布，请先核对平台草稿和网络") from None
        if platform == "channels":
            raise VideoStageError("视频号开始上传视频时页面中断；尚未点击发表，请先核对平台草稿和网络") from None
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
            ctx.progress("正在填写小红书标题")
            title = page.locator('input[placeholder*="填写标题"]').first
            await title.wait_for(state="visible", timeout=45_000)
            await title.fill(payload.title)
            stage = "正文"
            ctx.progress("正在填写小红书正文")
            editor = page.locator('p[data-placeholder*="输入正文描述"]').first
            await _fill_editor(page, editor, payload.description)
            stage = "话题"
            if payload.tags:
                ctx.progress("正在设置小红书话题")
            for tag in payload.tags:
                await page.keyboard.insert_text("#" + tag)
                item = page.locator("#creator-editor-topic-container .item").filter(has_text=tag).first
                await item.click(timeout=10_000)
            if payload.cover_path:
                stage = "封面"
                ctx.progress("正在设置小红书封面")
                await _xhs_cover(page, payload.cover_path)
        except Exception:
            raise VideoStageError(f"小红书视频已上传，但填写{stage}未完成；尚未点击发布，请先核对平台草稿及页面提示") from None
        # Resolve the current action after all metadata/cover work has settled.
        return None
    frame = await _channels_wait_upload(page, payload, ctx)
    document = None
    if payload.cover_path:
        try:
            async with asyncio.timeout(180):
                document = await frame.locator("html").element_handle()
                await _channels_cover(frame, payload.cover_path)
        except Exception:
            raise VideoStageError("视频号视频已上传，但封面设置未完成；尚未点击发表，请先核对平台草稿和封面提示") from None
    # Do not retain a locator from a document that may be replaced before submit.
    return ChannelsSubmission(frame, document)


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


async def _manual_success_notice(page, platform: str, payload: PublishPayload) -> bool:
    if platform == "xiaohongshu":
        return await _visible(page.get_by_text(re.compile(r"^(?:发布|提交)成功[！!。]?$")))
    return await _success_notice_present(page, platform, payload)


async def _manual_receipt(page, platform: str, payload: PublishPayload) -> ProviderResult | None:
    """Observe a platform success page; never operate a publishing control."""
    parsed = urlsplit(page.url)
    if (parsed.scheme != "https" or parsed.hostname != SITES[platform].host
            or parsed.port not in {None, 443} or parsed.username or parsed.password):
        return None
    expected = {"xiaohongshu": "/publish/success", "douyin": "/creator-micro/content/manage",
                "channels": "/platform/post/list"}[platform]
    if parsed.path.rstrip("/") != expected:
        return None
    if not await _manual_success_notice(page, platform, payload):
        return None
    if platform == "xiaohongshu":
        return ProviderResult("submitted", "小红书已显示手动提交成功，作品仍可能需要审核", url="https://creator.xiaohongshu.com/manage/notes")
    return ProviderResult("submitted", "平台已显示本次手动提交成功通知；审核与公开状态请在平台查看", url=page.url.split("?")[0])


async def _wait_for_manual_publish(page, platform: str, payload: PublishPayload, ctx: ProviderContext,
                                   message: str | None = None) -> ProviderResult:
    # No submit-selector/readiness dependency: users can complete account-specific
    # requirements and every confirmation in the real platform UI themselves.
    # The supervising host suspends its automatic timeout after this durable event.
    # A success notice already present at handoff cannot confirm a later user
    # action. Require a clear page before accepting a newly visible receipt.
    notice_was_clear = False
    if not page.is_closed():
        try:
            notice_was_clear = not await _manual_success_notice(page, platform, payload)
        except Exception:
            pass
    ctx.awaiting_user(message or f"{SITES[platform].label}视频已上传并填写，请在已打开的网页中核对内容，手动点击发布并完成确认；请保持应用打开")
    while not page.is_closed():
        try:
            if not notice_was_clear:
                notice_was_clear = not await _manual_success_notice(page, platform, payload)
            if notice_was_clear:
                receipt = await _manual_receipt(page, platform, payload)
                if receipt is not None:
                    return receipt
        except Exception:
            # Transient navigation, validation, login and editor changes belong
            # to the user's interaction; never close the window on a read error.
            pass
        await asyncio.sleep(0.25)
    return ProviderResult("unknown", "发布窗口已关闭，尚未取得本次手动发布的明确回执；请先到平台核对草稿和作品", url=SITES[platform].url)


async def _submit_once(page, button, platform: str, payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    if payload.mode == "publish":
        return await _wait_for_manual_publish(page, platform, payload, ctx)
    # Only the explicitly requested Channels draft action remains automated.
    if platform != "channels" or payload.mode != "draft":
        return ProviderResult("needs_attention", "此平台不支持自动保存草稿")
    prepared = button
    try:
        async with asyncio.timeout(60):
            while True:
                frame = await _channels_wait_upload(page, payload, ctx, seconds=60)
                if payload.cover_path:
                    try:
                        cover_preserved = frame is prepared.frame and prepared.document is not None and await prepared.document.evaluate("el => el.isConnected")
                    except Exception:
                        cover_preserved = False
                    if not cover_preserved:
                        raise VideoStageError("视频号设置封面后编辑器已刷新，无法确认封面保留；尚未保存草稿，请先核对平台草稿")
                button = frame.get_by_role("button", name="保存草稿", exact=True).first
                try:
                    classes = await button.get_attribute("class", timeout=1_000) or ""
                    if await button.is_visible() and await button.is_enabled() and "weui-desktop-btn_disabled" not in classes:
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
    except TimeoutError:
        raise VideoStageError("视频号保存草稿控件尚未就绪或编辑器已刷新；请先核对平台草稿及页面提示") from None
    ctx.progress("正在保存草稿一次，等待平台确认")
    try:
        if await _success_notice_present(page, platform, payload):
            return ProviderResult("unknown", "页面已有成功提示，无法区分本次回执；尚未保存草稿，请先核对平台草稿", url=SITES[platform].url)
        await button.click(timeout=15_000)
        return await _confirm_receipt(page, platform, payload)
    except Exception:
        return ProviderResult("unknown", "已尝试保存草稿，尚未获得明确回执；请先在平台核对草稿", url=SITES[platform].url)


async def publish(payload: PublishPayload, ctx: ProviderContext) -> ProviderResult:
    payload = project_payload(payload)
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
        async with _browser_page(ctx, platform=platform) as (context, page):
            await page.goto(SITES[platform].url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page = await _wait_login(page, platform, 25, ctx)
            except TimeoutError:
                return ProviderResult("login_required", "请重新登录并完成平台验证后发布")
            if platform == "douyin":
                await _douyin_upload_page(page, ctx)
            try:
                async with asyncio.timeout(UPLOAD_SECONDS + LOGIN_SECONDS + CONFIRM_SECONDS):
                    button = await _prepare(page, platform, payload, guarded_ctx)
            except Exception as exc:
                if payload.mode != "publish" or not mutated or page.is_closed():
                    raise
                message = str(exc) if isinstance(exc, VideoStageError) else "视频上传或填写未能自动完成"
                result = await _wait_for_manual_publish(page, platform, payload, guarded_ctx,
                    message + "；窗口将保持打开，请自行检查上传状态、补全信息并手动点击发布及确认")
            else:
                # The user's review/click is deliberately outside preparation's
                # bounded timeout. Cancellation still tears down our browser.
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
