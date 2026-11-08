"""Offline article hand-off checks: no fixture automates a publish button."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_publish.providers import social
from easy_publish.providers.base import ProviderContext, PublishPayload
from easy_publish.vendor.social_auto_upload import runtime


class Response:
    def __init__(self, url, data, *, ok=True):
        self.url, self.data, self.ok = url, data, ok
        self.status = 200 if ok else 500
        self.request = SimpleNamespace(method="POST")

    async def json(self):
        return self.data


class ResponseWait:
    def __init__(self, page, predicate, timeout):
        assert timeout == 0, "A person must not lose their editor to a receipt timeout"
        self.page, self.predicate = page, predicate
        self.value = asyncio.get_running_loop().create_future()

    async def __aenter__(self):
        self.page.waiters.append(self)
        return self

    async def __aexit__(self, kind, exc, tb):
        try:
            if kind is None:
                await self.value
        finally:
            self.page.waiters.remove(self)
            if not self.value.done():
                self.value.cancel()


class ObservablePage:
    def __init__(self):
        self.waiters = []
        self.context = object()
        self.closed = False
        self.close_waiters = []

    def expect_response(self, predicate, *, timeout):
        return ResponseWait(self, predicate, timeout)

    def send_response(self, response):
        for waiter in tuple(self.waiters):
            if not waiter.value.done() and waiter.predicate(response):
                waiter.value.set_result(response)

    def is_closed(self):
        return self.closed

    def expect_event(self, event, *, timeout):
        assert event == "close" and timeout == 0
        page = self

        class CloseWait:
            async def __aenter__(self):
                self.value = asyncio.get_running_loop().create_future()
                page.close_waiters.append(self.value)
                return self

            async def __aexit__(self, kind, exc, tb):
                try:
                    if kind is None:
                        await self.value
                finally:
                    page.close_waiters.remove(self.value)

        return CloseWait()

    def close_by_owner(self):
        self.closed = True
        for future in tuple(self.close_waiters):
            if not future.done():
                future.set_result(None)
        for waiter in tuple(self.waiters):
            if not waiter.value.done():
                waiter.value.set_exception(RuntimeError("owner closed the browser"))

    def get_by_role(self, *args, **kwargs):
        raise AssertionError("Publication code must not locate or click a final button")


def test_vendor_manual_hook_is_required_and_cleared_between_operations():
    messages = []
    runtime.configure(None, lambda: None, messages.append)
    runtime.awaiting_user("fixture hand-off")
    assert messages == ["fixture hand-off"]
    runtime.configure(None, lambda: None)
    with pytest.raises(RuntimeError, match="manual publication"):
        runtime.awaiting_user("must not reuse a prior operation")


@pytest.mark.parametrize("outcome", ["confirmed", "fast_confirmation", "closed", "invalid_receipt"])
def test_dayu_observes_both_owner_buttons_without_clicking(tmp_path, monkeypatch, outcome):
    from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader import publication

    async def run():
        page = ObservablePage()
        messages = []
        first_handoff, second_handoff = asyncio.Event(), asyncio.Event()

        def manual(message):
            messages.append(message)
            (first_handoff if len(messages) == 1 else second_handoff).set()

        preview_checked = asyncio.Event()
        release_preview_check = asyncio.Event()

        async def check_schedule(context, draft_id, schedule):
            assert draft_id == "current-draft"
            preview_checked.set()
            await release_preview_check.wait()

        monkeypatch.setattr(publication, "read_dayu_schedule", check_schedule)
        runtime.configure(None, lambda: None, manual)
        result = {"title": "Fixture title", "success": False}
        job = asyncio.create_task(publication.submit_dayu_publication(page, result, tmp_path / "result.json"))
        await asyncio.wait_for(first_handoff.wait(), 1)
        assert len(messages) == 1 and not job.done()
        # Both listeners are installed before the user can perform the first step.
        assert len(page.waiters) == 2
        page.send_response(Response("https://mp.dayu.com/dashboard/save-draft", {"data": {"_id": "current-draft"}}))
        await asyncio.wait_for(preview_checked.wait(), 1)
        submit_data = {"data": {"content_id": "current-content"}} if outcome != "invalid_receipt" else {"data": {}}
        receipt = Response("https://mp.dayu.com/dashboard/submit-article", submit_data)
        if outcome == "fast_confirmation":
            page.send_response(receipt)
        release_preview_check.set()
        await asyncio.wait_for(second_handoff.wait(), 1)
        assert len(messages) == 2
        if outcome != "fast_confirmation":
            assert not job.done() and result["success"] is False
            if outcome == "closed":
                page.close_by_owner()
            else:
                page.send_response(receipt)
        if outcome in {"closed", "invalid_receipt"}:
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(job, 1)
            assert result["success"] is False
        else:
            await asyncio.wait_for(job, 1)
            assert result["success"] and result["status"] == "submitted"
            assert result["content_id"] == "current-content"
        assert page.waiters == []

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["confirmed", "closed", "wrong_identity", "draft"])
def test_xueqiu_main_keeps_editor_for_owner_and_verifies_receipt(tmp_path, monkeypatch, outcome):
    from easy_publish.vendor.social_auto_upload.uploader.xueqiu_uploader import article

    async def run():
        events, closes, preview_closes, checks = [], [], [], []
        handed_off, recovery_handoff = asyncio.Event(), asyncio.Event()

        def emit(event):
            events.append(event)
            if event["event"] == "awaiting_user":
                (recovery_handoff if handed_off.is_set() else handed_off).set()

        session = tmp_path / "session.json"
        session.write_text(json.dumps({"version": 1, "storage_state": {"cookies": [], "origins": []}, "identity": {"uid": "123", "name": "Fixture"}}))
        work = tmp_path / "work"
        work.mkdir()
        ctx = ProviderContext(session, work, "/fixture/chromium", emit)
        item = PublishPayload(platform_id="xueqiu", account_id="fixture", kind="article", mode="draft" if outcome == "draft" else "publish", title="Fixture article", body_html="<p>Fixture body</p>", options={"declaration": "不添加创作声明"})

        class PreviewClose:
            async def click(self):
                preview_closes.append(True)

        class Page(ObservablePage):
            url = "https://mp.xueqiu.com/write"

            async def goto(self, url, **kwargs):
                assert any(event["event"] == "submitting" for event in events)
                self.url = url
                return SimpleNamespace(status=200)

            async def screenshot(self, **kwargs):
                pass

            def locator(self, selector):
                if 'index_close_' in selector:
                    return PreviewClose()
                assert 'index_preview-content_' in selector
                return object()

        class CheckPage:
            async def set_content(self, content):
                assert content == "<p>Fixture body</p>"

            def locator(self, selector):
                assert selector == "body"
                return object()

            async def close(self):
                pass

        page = Page()

        class Context:
            def __init__(self):
                self.count = 0

            async def new_page(self):
                self.count += 1
                return page if self.count == 1 else CheckPage()

            async def storage_state(self, **kwargs):
                pass

        class Browser:
            async def new_context(self, **kwargs):
                return Context()

            async def close(self):
                closes.append(True)

        class Chromium:
            async def launch(self, **kwargs):
                return Browser()

        class Playwright:
            async def __aenter__(self):
                return SimpleNamespace(chromium=Chromium())

            async def __aexit__(self, *args):
                pass

        class Expect:
            async def to_be_hidden(self):
                pass

        async def identity(page):
            return {"uid": "123"}

        async def fill(self, page):
            self.uploaded_images, self.cover_url = [], None

        async def check_content(*args):
            checks.append(True)

        async def query(*args):
            return {"text": "<p>Fixture body</p>"}

        monkeypatch.setattr(article, "async_playwright", Playwright)
        monkeypatch.setattr(article, "expect", lambda locator: Expect())
        monkeypatch.setattr(article, "read_xueqiu_identity", identity)
        monkeypatch.setattr(article.XueqiuArticle, "fill_article_form", fill)
        monkeypatch.setattr(article, "check_xueqiu_article_content", check_content)
        monkeypatch.setattr(article, "query_xueqiu_article", query)
        job = asyncio.create_task(social.publish(item, ctx))
        if outcome == "draft":
            result = await asyncio.wait_for(job, 1)
            assert result.status == "draft_created"
            assert not preview_closes and not any(event["event"] == "awaiting_user" for event in events)
        else:
            await asyncio.wait_for(handed_off.wait(), 1)
            assert not job.done() and not closes
            assert preview_closes == [True]
            assert [event["event"] for event in events].count("submitting") == 1
            assert [event["event"] for event in events].count("awaiting_user") == 1
            if outcome == "closed":
                page.close_by_owner()
            else:
                page.send_response(Response("https://mp.xueqiu.com/xq/statuses/update.json", {"id": 456, "user_id": "999" if outcome == "wrong_identity" else "123", "title": item.title, "text": "<p>Fixture body</p>", "is_private": False}))
            if outcome == "wrong_identity":
                await asyncio.wait_for(recovery_handoff.wait(), 1)
                assert len([event for event in events if event["event"] == "awaiting_user"]) == 2
                assert not job.done() and not closes
                page.close_by_owner()
            result = await asyncio.wait_for(job, 1)
            assert result.status == ("published" if outcome == "confirmed" else "unknown")
            assert len(checks) == (2 if outcome == "confirmed" else 0)
        assert closes == [True]
        assert page.waiters == []

    asyncio.run(run())


def test_dayu_preview_check_failure_keeps_main_window_until_owner_closes(tmp_path, monkeypatch):
    from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader import article, publication

    async def run():
        events, closed = [], []
        recover = asyncio.Event()
        session, work = tmp_path / "session.json", tmp_path / "work"
        work.mkdir()
        identity = {"wmid": "a" * 32, "name": "Fixture"}
        session.write_text(json.dumps({"version": 1, "storage_state": {"cookies": [], "origins": []}, "identity": identity}))

        def emit(event):
            events.append(event)
            if event["event"] == "awaiting_user" and "未能自动核验" in event["message"]:
                recover.set()

        ctx = ProviderContext(session, work, "/fixture/chromium", emit)
        item = PublishPayload(platform_id="dayu", account_id="fixture", kind="article", mode="publish", title="Fixture article title", body_html="<p>Fixture body</p>", options={"source": "无需标注"})

        class Page(ObservablePage):
            async def goto(self, url, **kwargs):
                assert any(event["event"] == "submitting" for event in events)

        page = Page()

        class Context:
            async def new_page(self):
                return page

        class Browser:
            async def new_context(self, **kwargs):
                return Context()

            async def close(self):
                closed.append(True)

        class Chromium:
            async def launch(self, **kwargs):
                return Browser()

        class Playwright:
            async def __aenter__(self):
                return SimpleNamespace(chromium=Chromium())

            async def __aexit__(self, *args):
                pass

        async def read_identity(page):
            return identity

        async def fill(self, page):
            self.style_result, self.publication_html = {}, "<p>Fixture body</p>"

        async def broken_preview(page, result, result_file, article_html):
            runtime.awaiting_user("请手动点击发表")
            # A read-only server preview check can fail after the owner clicked.
            raise TimeoutError("fixture preview page did not load")

        monkeypatch.setattr(article, "async_playwright", Playwright)
        monkeypatch.setattr(article, "read_dayu_identity", read_identity)
        monkeypatch.setattr(article.DayuArticle, "fill_article_form", fill)
        monkeypatch.setattr(publication, "_prepare_dayu_preview", broken_preview)
        job = asyncio.create_task(social.publish(item, ctx))
        await asyncio.wait_for(recover.wait(), 1)
        assert not job.done() and not closed and not page.is_closed()
        assert any("勿重复点击发布" in event.get("message", "") for event in events)
        page.close_by_owner()
        result = await asyncio.wait_for(job, 1)
        assert result.status == "unknown" and closed == [True]
        assert page.waiters == [] and page.close_waiters == []

    asyncio.run(run())
