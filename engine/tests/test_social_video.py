"""Isolated browser-boundary fixtures; no platform accounts or network calls."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from easy_publish.providers.base import ProviderContext, ProviderResult, PublishPayload
from easy_publish.providers import social_video as video


class VideoAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "clip.mp4"
        self.media.write_bytes(b"local test fixture; never uploaded")
        self.events = []
        self.ctx = ProviderContext(self.root / "session.json", self.root, "/test/browser", self.events.append)
        self.payload = PublishPayload("douyin", "test-account", "video", "publish", "测试视频标题", video_path=self.media)
        self.notice_patch = patch.object(video, "_success_notice_present", AsyncMock(return_value=False))
        self.notice_patch.start()
        self.addCleanup(self.notice_patch.stop)

    def browser_fixture(self):
        context = MagicMock()
        context.storage_state = AsyncMock()
        page = MagicMock()
        page.goto = AsyncMock()
        page.is_closed.return_value = False

        @asynccontextmanager
        async def factory(ctx, **kwargs):
            self.assertIs(ctx, self.ctx)
            yield context, page

        return factory, context, page

    async def test_missing_session_does_not_launch_browser(self):
        with patch.object(video, "_browser_page") as browser:
            result = await video.publish(self.payload, self.ctx)
        self.assertEqual(result.status, "login_required")
        browser.assert_not_called()
        self.assertFalse(self.events)

    async def test_douyin_ignores_cover_before_validation_and_publication(self):
        self.payload.cover_path = self.root / "missing-other-platform-cover.png"
        self.assertIsNone(video._validate(self.payload, "douyin"))
        self.ctx.session_path.write_text("{}")
        factory, context, page = self.browser_fixture()
        seen = []

        async def prepare(actual_page, platform, payload, ctx):
            seen.append(payload)
            self.assertIsNone(payload.cover_path)
            ctx.submitting()
            return MagicMock()

        with patch.object(video, "_browser_page", factory), patch.object(video, "_wait_login", AsyncMock(return_value=page)), patch.object(video, "_douyin_upload_page", AsyncMock()), patch.object(video, "_prepare", prepare), patch.object(video, "_submit_once", AsyncMock(return_value=ProviderResult("submitted", "fixture"))):
            result = await video.publish(self.payload, self.ctx)
        self.assertEqual(result.status, "submitted")
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(self.payload.cover_path)

    async def test_unknown_platform_is_unavailable_without_opening_browser(self):
        self.payload.platform_id = "unknown-platform"
        with patch.object(video, "_browser_page") as browser:
            for action in (video.login, video.check, video.publish):
                result = await action(self.payload, self.ctx)
                self.assertEqual(result.status, "needs_attention")
                self.assertIn("未接入", result.message)
        browser.assert_not_called()

    async def test_closed_manual_window_never_clicks_or_claims_success(self):
        button = MagicMock(click=AsyncMock())
        page = MagicMock()
        page.is_closed.return_value = True
        result = await video._submit_once(page, button, "douyin", self.payload, self.ctx)
        self.assertEqual(result.status, "unknown")
        button.click.assert_not_awaited()
        self.assertEqual(self.events[-1]["event"], "awaiting_user")

    async def test_manual_review_has_no_short_timeout_and_is_cancellable(self):
        button = MagicMock(click=AsyncMock())
        page = MagicMock()
        page.is_closed.return_value = False
        with patch.object(video, "CONFIRM_SECONDS", 0.001), patch.object(video, "_manual_receipt", AsyncMock(return_value=None)):
            task = asyncio.create_task(video._submit_once(page, button, "douyin", self.payload, self.ctx))
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            button.click.assert_not_awaited()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_manual_confirmed_receipt_is_submitted_without_a_click(self):
        button = MagicMock(click=AsyncMock())
        page = MagicMock()
        page.is_closed.return_value = False
        expected = ProviderResult("submitted", "platform accepted")
        with patch.object(video, "_manual_receipt", AsyncMock(return_value=expected)):
            result = await video._submit_once(page, button, "douyin", self.payload, self.ctx)
        self.assertIs(result, expected)
        button.click.assert_not_awaited()
        self.assertEqual(self.events[-1]["event"], "awaiting_user")

    async def test_preexisting_success_notice_cannot_confirm_manual_publication(self):
        page = MagicMock()
        page.is_closed.return_value = False
        receipt = AsyncMock(return_value=ProviderResult("submitted", "old success"))
        with patch.object(video, "_manual_success_notice", AsyncMock(return_value=True)), patch.object(video, "_manual_receipt", receipt):
            task = asyncio.create_task(video._wait_for_manual_publish(page, "douyin", self.payload, self.ctx))
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            receipt.assert_not_awaited()
            page.is_closed.return_value = True
            result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.status, "unknown")

    async def test_manual_receipts_require_trusted_success_path_and_notice(self):
        success_paths = {"douyin": "/creator-micro/content/manage", "xiaohongshu": "/publish/success", "channels": "/platform/post/list"}
        for platform, path in success_paths.items():
            page = MagicMock(url=f"https://{video.SITES[platform].host}{path}")
            with patch.object(video, "_manual_success_notice", AsyncMock(return_value=False)):
                self.assertIsNone(await video._manual_receipt(page, platform, self.payload))
            with patch.object(video, "_manual_success_notice", AsyncMock(return_value=True)):
                self.assertEqual((await video._manual_receipt(page, platform, self.payload)).status, "submitted")
                for url in (f"https://{video.SITES[platform].host}.attacker.invalid{path}",
                            f"http://{video.SITES[platform].host}{path}",
                            f"https://{video.SITES[platform].host}:444{path}",
                            f"https://{video.SITES[platform].host}/login"):
                    page.url = url
                    self.assertIsNone(await video._manual_receipt(page, platform, self.payload))

    async def test_upload_fence_precedes_first_media_mutation(self):
        upload = MagicMock()
        upload.first = upload
        upload.wait_for = AsyncMock()

        async def fail_upload(path):
            self.assertEqual(self.events[-1]["event"], "submitting")
            raise RuntimeError("fixture abort after upload fence")

        upload.set_input_files = AsyncMock(side_effect=fail_upload)
        page = MagicMock()
        page.locator.return_value = upload
        with self.assertRaisesRegex(RuntimeError, "fixture abort"):
            await video._prepare(page, "douyin", self.payload, self.ctx)
        upload.set_input_files.assert_awaited_once_with(str(self.media))

    async def test_failure_after_upload_is_unknown_not_retryable(self):
        self.ctx.session_path.write_text("{}")
        factory, context, page = self.browser_fixture()

        async def prepare(page, platform, payload, ctx):
            ctx.submitting()
            raise RuntimeError("fixture: remote upload may already exist")

        with patch.object(video, "_browser_page", factory), patch.object(video, "_wait_login", AsyncMock(return_value=page)), patch.object(video, "_douyin_upload_page", AsyncMock()), patch.object(video, "_prepare", prepare), patch.object(video, "_submit_once", AsyncMock()) as submit, patch.object(video, "_wait_for_manual_publish", AsyncMock(return_value=ProviderResult("unknown", "window closed"))) as manual:
            result = await video.publish(self.payload, self.ctx)
        self.assertEqual(result.status, "unknown")
        submit.assert_not_awaited()
        manual.assert_awaited_once()

    async def test_failure_before_upload_is_retryable(self):
        self.ctx.session_path.write_text("{}")
        factory, context, page = self.browser_fixture()
        page.goto.side_effect = RuntimeError("fixture: network unavailable")
        with patch.object(video, "_browser_page", factory):
            result = await video.publish(self.payload, self.ctx)
        self.assertEqual(result.status, "failed")
        self.assertFalse(self.events)

    async def test_session_write_error_does_not_mask_confirmed_submission(self):
        self.ctx.session_path.write_text("{}")
        factory, context, page = self.browser_fixture()
        context.storage_state.side_effect = OSError("fixture: storage unavailable")

        async def prepare(page, platform, payload, ctx):
            ctx.submitting()
            return MagicMock()

        with patch.object(video, "_browser_page", factory), patch.object(video, "_wait_login", AsyncMock(return_value=page)), patch.object(video, "_douyin_upload_page", AsyncMock()), patch.object(video, "_prepare", prepare), patch.object(video, "_submit_once", AsyncMock(return_value=ProviderResult("submitted", "confirmed"))):
            result = await video.publish(self.payload, self.ctx)
        self.assertEqual(result.status, "submitted")
        context.storage_state.assert_awaited_once_with(path=str(self.ctx.session_path))

    async def test_false_navigation_is_not_a_receipt(self):
        page = MagicMock()
        page.url = "https://channels.weixin.qq.com/login.html"

        async def wait_url(pattern, **kwargs):
            if not pattern.search(page.url):
                raise TimeoutError("fixture: login is not a content receipt")

        page.wait_for_url = AsyncMock(side_effect=wait_url)
        with self.assertRaises(TimeoutError):
            await video._confirm_receipt(page, "channels", self.payload)

    async def test_existing_same_title_post_is_not_current_receipt(self):
        for platform, url in (("douyin", "https://creator.douyin.com/creator-micro/content/manage"), ("channels", "https://channels.weixin.qq.com/platform/post/list")):
            page = MagicMock()
            page.url = url
            page.wait_for_url = AsyncMock()
            page.get_by_text.return_value.count = AsyncMock(return_value=1)
            page.get_by_text.return_value.first.is_visible = AsyncMock(return_value=True)
            result = await video._confirm_receipt(page, platform, self.payload)
            self.assertEqual(result.status, "unknown")

    async def test_channels_fence_precedes_editor_entry_once(self):
        entry = MagicMock()
        entry.first = entry

        async def click():
            self.assertEqual([e["event"] for e in self.events], ["submitting"])
            raise RuntimeError("fixture abort after editor entry")

        entry.click = AsyncMock(side_effect=click)
        page = MagicMock()
        page.locator.return_value.filter.return_value = entry
        with patch.object(video, "_channels_entry", AsyncMock(return_value=entry)), self.assertRaisesRegex(RuntimeError, "fixture abort"):
            await video._prepare(page, "channels", self.payload, self.ctx)
        entry.click.assert_awaited_once()
        self.assertEqual(len(self.events), 1)


if __name__ == "__main__":
    unittest.main()
