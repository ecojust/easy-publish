"""Plain-text polishing jobs; original drafts and publication remain independent."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from .content import image_sources, markdown_html
from .opencode_runtime import OpenCodeError
from .store import dump, now


def source_fingerprint(content: dict) -> str:
    # Image placeholders are positional, so changing their mapping invalidates output.
    value = [content.get("sourceText", ""), content.get("imageAssetIds", [])]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def format_ready(content: dict) -> bool:
    if content.get("inputFormat", "markdown") != "plain":
        return bool(content.get("body", "").strip())
    return bool(content.get("body", "").strip()) and content.get("formatSourceHash") == source_fingerprint(content)


def saved_article_input(value: dict, previous: dict | None) -> dict:
    value = dict(value)
    if value["kind"] == "article" and value.get("inputFormat") == "plain":
        if previous and previous.get("inputFormat") == "plain" and format_ready(previous) and source_fingerprint(value) == source_fingerprint(previous):
            value.update(body=previous["body"], formatSourceHash=previous["formatSourceHash"], formatModel=previous.get("formatModel"))
        else:
            value.update(body="", formatSourceHash=None, formatModel=None)
    return value


class InvalidFormattedArticle(Exception):
    """Only fixed local validation messages; never a provider exception."""


def checked_markdown(source: str, markdown: str, image_ids: list[str]) -> str:
    if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > 500_000:
        raise InvalidFormattedArticle("润色结果为空或过长，请重试")
    tokens = r"【图片[1-9][0-9]*】"
    if re.findall(tokens, source) != re.findall(tokens, markdown):
        raise InvalidFormattedArticle("润色结果未保留原文的图片位置，请重试")
    for index, identifier in enumerate(image_ids, 1):
        markdown = markdown.replace(f"【图片{index}】", f"![正文图片 {index}](easy-asset://{identifier})")
    allowed = {"easy-asset://" + identifier for identifier in image_ids}
    if any(source not in allowed for source in image_sources(markdown_html(markdown))):
        raise InvalidFormattedArticle("润色结果包含未导入的图片，请检查原文后重试")
    return markdown.strip()


class ArticleFormattingMixin:
    def _init_formatting(self, formatter=None):
        self._article_formatter = formatter
        self.format_jobs = {}
        self.format_tasks = {}
        self.format_cancellations = {}

    @property
    def formatter(self):
        if self._article_formatter is None:
            from .opencode_runtime import OpenCodeFormatter
            self._article_formatter = OpenCodeFormatter(self.data_dir)
        return self._article_formatter

    @formatter.setter
    def formatter(self, value):
        self._article_formatter = value

    def formatting_state(self):
        return self.formatter.state()

    async def check_formatting(self):
        from .service import Problem
        if self.closing:
            raise Problem("应用正在退出", "closing", 409)
        try:
            return await self.formatter.check()
        except asyncio.CancelledError:
            raise
        except OpenCodeError as error:
            raise Problem(str(error), "formatting_unavailable", 503) from None
        except Exception:
            # Raw provider errors can contain article content, paths or credentials.
            raise Problem("免费润色模型暂时不可用，请稍后重试；不会使用收费模型", "formatting_unavailable", 503) from None

    def format_job(self, identifier):
        from .service import Problem
        job = self.format_jobs.get(identifier)
        if job is None:
            raise Problem("润色任务不存在或应用已重启，请重新润色", "not_found", 404)
        return {key: job[key] for key in ("id", "contentId", "status", "message", "content") if key in job}

    def start_article_format(self, content_id, revision, request_id):
        from .service import Problem
        previous = self.format_jobs.get(request_id)
        if previous:
            if (previous["contentId"], previous["revision"]) != (content_id, revision):
                raise Problem("此请求编号已用于不同润色任务", "idempotency_conflict", 409)
            return self.format_job(request_id)
        if self.closing:
            raise Problem("应用正在退出", "closing", 409)
        row = self.content_row(content_id)
        content = json.loads(row["data"])
        if row["revision"] != revision:
            raise Problem("稿件已更新，请保存当前原文后再润色", "revision_conflict", 409)
        if content["kind"] != "article" or content.get("inputFormat", "markdown") != "plain":
            raise Problem("仅纯文本文章需要润色为 Markdown")
        if not content.get("sourceText", "").strip():
            raise Problem("请先输入文章原文")
        if any(job["contentId"] == content_id and job["status"] == "running" for job in self.format_jobs.values()):
            raise Problem("这篇文章正在润色，请等待完成或取消", "formatting_busy", 409)
        # Retain bounded idempotency receipts for this application session.
        if len(self.format_jobs) >= 100:
            for identifier, old in list(self.format_jobs.items()):
                if old["status"] != "running":
                    del self.format_jobs[identifier]
                    break
            else:
                raise Problem("润色任务过多，请等待当前任务完成", "formatting_busy", 409)
        self.format_jobs[request_id] = {"id": request_id, "contentId": content_id, "revision": revision,
                                        "status": "running", "message": "正在使用免费模型润色文章"}
        self.format_tasks[request_id] = asyncio.create_task(self._format_article(request_id, content))
        return self.format_job(request_id)

    async def _format_article(self, identifier, content):
        job = self.format_jobs[identifier]
        try:
            markdown = await self.formatter.format(content["sourceText"])
            markdown = checked_markdown(content["sourceText"], markdown, content.get("imageAssetIds", []))
            # No await between the revision comparison and atomic update. A stale
            # model result cannot replace a changed/deleted draft or its metadata.
            with self.store.transaction() as db:
                row = db.execute("SELECT * FROM contents WHERE id=?", (job["contentId"],)).fetchone()
                if row is None or row["revision"] != job["revision"]:
                    job.update(status="failed", message="原文在润色期间已更新或删除，结果未写入；请重新润色")
                    return
                value = json.loads(row["data"])
                if value.get("inputFormat") != "plain" or source_fingerprint(value) != source_fingerprint(content):
                    job.update(status="failed", message="原文已更新，润色结果未写入；请重新润色")
                    return
                value.update(body=markdown, formatSourceHash=source_fingerprint(value), formatModel=self.formatter.state().get("modelId"))
                db.execute("UPDATE contents SET data=?,updated=?,revision=revision+1 WHERE id=?",
                           (dump(value), now(), job["contentId"]))
            job.update(status="succeeded", message="润色完成，请检查各平台预览后再发布",
                       content=self.store.content(self.content_row(job["contentId"])))
        except asyncio.CancelledError:
            job.update(status="cancelled", message="润色已取消，原文和之前的结果已保留")
            raise
        except (InvalidFormattedArticle, OpenCodeError) as error:
            job.update(status="failed", message=str(error))
        except Exception:
            job.update(status="failed", message="文章润色未完成，原文和之前的结果已保留；请检查免费模型状态后重试")
        finally:
            self.format_tasks.pop(identifier, None)

    async def cancel_article_format(self, identifier):
        self.format_job(identifier)
        cancellation = self.format_cancellations.get(identifier)
        if cancellation is None:
            task = self.format_tasks.get(identifier)
            if task is None or task.done():
                return self.format_job(identifier)
            cancellation = asyncio.create_task(self._cancel_article_format(identifier, task))
            self.format_cancellations[identifier] = cancellation
        await asyncio.shield(cancellation)
        return self.format_job(identifier)

    async def _cancel_article_format(self, identifier, task):
        try:
            if task.done():
                return
            if not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first turn never executes its finally.
            self.format_tasks.pop(identifier, None)
            if self.format_jobs[identifier]["status"] == "running":
                self.format_jobs[identifier].update(status="cancelled", message="润色已取消，原文和之前的结果已保留")
        finally:
            self.format_cancellations.pop(identifier, None)

    async def close_formatting(self):
        for task in list(self.format_tasks.values()):
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*list(self.format_tasks.values()), return_exceptions=True)
        await asyncio.gather(*list(self.format_cancellations.values()), return_exceptions=True)
        if self._article_formatter is not None:
            await self._article_formatter.close()
