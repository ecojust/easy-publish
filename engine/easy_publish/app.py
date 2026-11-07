from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from easy_publish.content import ContentInput, import_asset, preview_asset
from easy_publish.article_templates import ArticleTemplateId
from easy_publish.catalog import BY_ID
from easy_publish.service import Problem, Service


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInput(Input):
    platformId: str = Field(max_length=40)
    label: str = Field(max_length=80)
    credentials: dict[str, str] | None = None


class AccountPreferencesInput(Input):
    # The route applies only explicitly supplied fields, preserving the others.
    defaultSelected: StrictBool = True
    wechatTemplateId: ArticleTemplateId = "clean"

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("请提供需要修改的账号设置")
        return self


class AssetInput(Input):
    path: str = Field(max_length=4096)
    kind: Literal["video", "image", "article"]


class Selection(Input):
    contentId: str = Field(max_length=64)
    accountIds: list[str] = Field(min_length=1, max_length=50)
    mode: Literal["publish", "draft"]


class BatchInput(Selection):
    requestId: UUID


class ArticlePreviewInput(Input):
    body: str = Field(default="", max_length=500_000)
    articleTemplateId: ArticleTemplateId = "clean"
    platformId: str | None = Field(default=None, max_length=40)
    accountId: str | None = Field(default=None, min_length=1, max_length=128)


class ArticleFormatInput(Input):
    revision: int = Field(strict=True, ge=1)
    requestId: UUID


class McpCallInput(Input):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


def create_app(data_dir: Path, token: str, runtime=None, *, formatter=None, mcp_token: str | None = None, instance_id: str | None = None):
    if runtime is None:
        from easy_publish.runtime import Runtime
        runtime = Runtime(data_dir)
    service = Service(data_dir, runtime, formatter=formatter)

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.exit_callback = None
    app.state.mcp_instance_id = instance_id

    @app.middleware("http")
    async def local_auth(request: Request, call_next):
        selected_token = mcp_token if request.url.path == "/api/mcp/call" else token
        if not selected_token:
            return JSONResponse({"error": "MCP 本地连接不可用", "code": "unauthorized"}, status_code=401)
        expected = "Bearer " + selected_token
        if not secrets.compare_digest(request.headers.get("authorization", "").encode("utf-8"), expected.encode("ascii")):
            return JSONResponse({"error": "无效的本地会话", "code": "unauthorized"}, status_code=401)
        if request.url.path == "/api/mcp/call" and instance_id and not secrets.compare_digest(request.headers.get("x-easy-publish-instance", "").encode("utf-8"), instance_id.encode("utf-8")):
            return JSONResponse({"error": "MCP 连接已失效，请重新连接", "code": "unauthorized"}, status_code=401)
        if request.headers.get("origin"):
            return JSONResponse({"error": "请通过桌面应用访问", "code": "origin_denied"}, status_code=403)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            length = 5_000_000
        if length > 4_000_000:
            return JSONResponse({"error": "请求内容过大", "code": "request_too_large"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(Problem)
    async def problem_handler(_, error):
        return JSONResponse({"error": error.message, "code": error.code}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_, error):
        # Pydantic's default response echoes input, which can contain credentials.
        return JSONResponse({"error": "请求字段不完整或格式不正确", "code": "validation_error"}, status_code=422)

    @app.exception_handler(Exception)
    async def failure_handler(_, error):
        return JSONResponse({"error": "本地操作失败，请检查文件权限与运行环境", "code": "internal_error"}, status_code=500)

    @app.get("/api/state")
    async def state():
        return service.state()

    @app.post("/api/preview/article")
    async def preview_article(value: ArticlePreviewInput):
        return await service.preview_article(value.body, value.articleTemplateId, value.platformId, value.accountId)

    @app.post("/api/mcp/call")
    async def mcp_call(value: McpCallInput):
        from .mcp_server import execute_tool
        return await execute_tool(service, data_dir, value.name, value.arguments)

    @app.post("/api/contents")
    async def add_content(content: ContentInput):
        return service.save_content(content)

    @app.put("/api/contents/{identifier}")
    async def update_content(identifier: str, content: ContentInput):
        return service.save_content(content, identifier)

    @app.delete("/api/contents/{identifier}")
    async def delete_content(identifier: str):
        service.content_row(identifier)
        service.store.execute("DELETE FROM contents WHERE id=?", (identifier,))
        return {"ok": True}

    @app.post("/api/contents/{identifier}/format")
    async def format_article(identifier: str, value: ArticleFormatInput):
        return service.start_article_format(identifier, value.revision, str(value.requestId))

    @app.get("/api/format-jobs/{identifier}")
    async def format_job(identifier: str):
        return service.format_job(identifier)

    @app.post("/api/format-jobs/{identifier}/cancel")
    async def cancel_format_job(identifier: str):
        return await service.cancel_article_format(identifier)

    @app.post("/api/formatting/check")
    async def check_formatting():
        return await service.check_formatting()

    @app.post("/api/assets/import")
    async def add_asset(value: AssetInput):
        try:
            row = await asyncio.to_thread(import_asset, data_dir, value.path, value.kind)
        except (ValueError, OSError):
            raise Problem("素材导入失败，请检查文件格式、大小及读取权限。图片支持 JPG/PNG，文章支持 UTF-8 Markdown/TXT，视频支持 MP4/MOV/M4V/WebM") from None
        service.store.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?)", tuple(row[k] for k in ("id", "name", "kind", "size", "mime", "path", "text")))
        return service.store.asset(row)

    @app.get("/api/assets/{identifier}/preview")
    async def preview(identifier: str):
        row = service.asset_row(identifier)
        try:
            return {"dataUrl": await asyncio.to_thread(preview_asset, row)}
        except (ValueError, OSError):
            raise Problem("无法预览此图片") from None

    @app.get("/api/assets/{identifier}")
    async def asset(identifier: str):
        return service.store.asset(service.asset_row(identifier))

    @app.post("/api/accounts")
    async def account(value: AccountInput):
        if value.credentials and (set(value.credentials) - {"appId", "appSecret"} or any(len(v) > 512 for v in value.credentials.values())):
            raise Problem("应用凭据格式不正确")
        return service.add_account(value.platformId, value.label, value.credentials)

    @app.put("/api/accounts/{identifier}/preferences")
    async def account_preferences(identifier: str, value: AccountPreferencesInput):
        changes = value.model_dump(exclude_unset=True)
        names = {"defaultSelected": "default_selected", "wechatTemplateId": "wechat_template_id"}
        return service.set_account_preferences(identifier, **{names[key]: item for key, item in changes.items()})

    @app.delete("/api/accounts/{identifier}")
    async def delete_account(identifier: str):
        service.delete_account(identifier)
        return {"ok": True}

    @app.post("/api/accounts/{identifier}/login")
    async def login(identifier: str):
        await service.connect_account(identifier, "login")
        return {"ok": True}

    @app.post("/api/accounts/{identifier}/check")
    async def check(identifier: str):
        await service.connect_account(identifier, "check")
        return {"ok": True}

    @app.post("/api/accounts/{identifier}/cancel")
    async def cancel_account(identifier: str):
        await service.cancel_account(identifier)
        return {"ok": True}

    @app.post("/api/preflight")
    async def preflight(value: Selection):
        return service.preflight(service.store.content(service.content_row(value.contentId)), value.accountIds, value.mode)

    @app.post("/api/batches")
    async def batch(value: BatchInput):
        return service.create_batch(value.contentId, value.accountIds, value.mode, str(value.requestId))

    @app.post("/api/batches/{identifier}/cancel")
    async def cancel(identifier: str):
        await service.cancel_batch(identifier)
        return {"ok": True}

    @app.post("/api/tasks/{identifier}/retry")
    async def retry(identifier: str):
        service.retry_task(identifier)
        return {"ok": True}

    @app.post("/api/runtime/browser/install")
    async def browser_install():
        await runtime.install_browser()
        return {"ok": True}

    @app.post("/api/shutdown")
    async def shutdown():
        if app.state.exit_callback:
            asyncio.get_running_loop().call_later(0.1, app.state.exit_callback)
        return {"ok": True}

    return app
