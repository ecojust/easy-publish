"""MCP stdio bridge and narrowly scoped desktop tool dispatch.

No second engine/database is started by --mcp. The stdio process discovers the
already-running desktop through its private per-user endpoint and can call only
the MCP-specific API. Platform writes happen only through the publish tool and
the application's existing durable, idempotent publishing queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import __version__
from .article_templates import ArticleTemplateId
from .content import ContentInput
from .mcp_connection import ConnectionUnavailable, read_endpoint


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", PROTOCOL_VERSION}
MAX_MESSAGE_BYTES = 4_000_000
MAX_RESPONSE_BYTES = 16_000_000


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KindFilter(Arguments):
    kind: Literal["article", "video"] | None = None


class AccountFilter(Arguments):
    kind: Literal["article", "video"]
    connectedOnly: bool = True


class ContentFilter(KindFilter):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ContentId(Arguments):
    contentId: str = Field(min_length=1, max_length=64)


class SaveContent(Arguments):
    content: ContentInput
    contentId: str | None = Field(default=None, min_length=1, max_length=64)


class ImportAsset(Arguments):
    path: str = Field(min_length=1, max_length=4096)
    kind: Literal["article", "video", "image"]


class PreviewArticle(Arguments):
    body: str = Field(max_length=500_000)
    articleTemplateId: ArticleTemplateId = "clean"
    platformId: Literal["wechat", "zhihu", "dayu", "xueqiu"] | None = None
    accountId: str | None = Field(default=None, min_length=1, max_length=128)


class Selection(ContentId):
    accountIds: list[str] | None = Field(default=None, min_length=1, max_length=50)
    mode: Literal["publish", "draft"] = "publish"


class Publish(Selection):
    requestId: UUID


class BatchFilter(Arguments):
    contentId: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class BatchId(Arguments):
    batchId: str = Field(min_length=1, max_length=64)


TOOL_DEFINITIONS = [
    ("list_platforms", "List article/video platform capabilities including publishMethod (browser requires manual final publication; api submits directly), required platform options, and article templates. No credentials are returned.", KindFilter, True),
    ("list_accounts", "List accounts compatible with the requested content kind; connected accounts only by default. Includes defaultSelected and each video account's videoOptions (null when unconfigured). Accounts disabled for default selection remain listed. Article accounts cannot publish videos and video accounts cannot publish articles.", AccountFilter, True),
    ("list_contents", "List local content summaries and platform publication states. Already submitted, published or uncertain platforms are excluded from automatic publish selection.", ContentFilter, True),
    ("get_content", "Read a saved local article/video and its current platform publication states.", ContentId, True),
    ("save_content", "Create or update a LOCAL draft. Supply content.kind explicitly, Markdown for articles, and imported asset IDs for media. This does not publish or invoke AI. Article inputFormat is markdown; no model or generation step is needed. WeChat uses each account's configured template; articleTemplateId applies to other platforms and legacy data. For videos, videoOptionsSource=account uses only each account's settings; the compatibility default content uses that platform's nonempty supported platformOptions as a whole, otherwise account settings. Options are not merged.", SaveContent, False),
    ("import_asset", "Import a user-authorized absolute local media or UTF-8 article path into Easy Publish. Returns an asset ID; article imports also return text. No platform upload occurs.", ImportAsset, False),
    ("preview_article", "Render sanitized Markdown without publishing. Supply accountId for a WeChat account to preview its configured template, which overrides articleTemplateId. Without accountId, use articleTemplateId and optional platformId for legacy or non-WeChat previews. Returns HTML and formatting notes.", PreviewArticle, True),
    ("preflight", "Check saved content for selected accounts without publishing. Omit accountIds to use connected, compatible accounts with defaultSelected enabled on platforms without a previous submitted/published/unknown result. Explicit accountIds override the default-selection preference and allow intentional republication, but never cross article/video types.", Selection, True),
    ("publish", "PUBLISH saved content (or create platform drafts when mode=draft) through connected compatible accounts. This uploads content to external platforms. Call only when publication is requested. requestId must be a UUID: reuse it after uncertainty in this tool call to retrieve the SAME batch with its original selection, even after account preferences change; use a NEW UUID for an intentional new publication. Omitted accountIds use defaultSelected accounts and exclude previous submitted/published/unknown platforms. Explicit accountIds override the default-selection preference and allow republication but never cross article/video types. The batch freezes each WeChat account's configured template and each video account's resolved options. Browser platforms upload and fill the editor, then remain awaiting_user until the user clicks the final publish/confirmation controls in the opened browser. Do not automate those final clicks or treat awaiting_user as success. WeChat and Bilibili API submission is unchanged. Task results may be submitted, published, or unknown; never automatically resend unknown results.", Publish, False),
    ("list_batches", "List publishing batches and per-account results. A submitted receipt is not proof that content is public. Pagination is available with offset/limit.", BatchFilter, True),
    ("get_batch", "Read one publishing batch and all per-account results, including older batches. Poll this after publish; do not publish again to poll. awaiting_user is active work requiring the user to complete publication in the opened browser.", BatchId, True),
    ("cancel_batch", "Cancel pending work in a batch. This cannot retract content already uploaded or submitted to a platform; uncertain submitted tasks remain unknown.", BatchId, False),
]
MODELS = {name: model for name, _, model, _ in TOOL_DEFINITIONS}


def tools_list() -> list[dict]:
    return [{"name": name, "description": description, "inputSchema": model.model_json_schema(),
             "annotations": {"readOnlyHint": read_only,
                             "destructiveHint": name in {"publish", "cancel_batch"},
                             "idempotentHint": read_only or name in {"publish", "cancel_batch"},
                             "openWorldHint": name in {"publish", "cancel_batch"}}}
            for name, description, model, read_only in TOOL_DEFINITIONS]


def tool_result(value: dict, *, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "structuredContent": value, "isError": error}


def tool_error(message: str, code: str = "tool_error") -> dict:
    return tool_result({"error": message, "code": code}, error=True)


def _content(service, identifier: str) -> dict:
    return service.store.content(service.content_row(identifier))


def _select_accounts(service, content: dict, identifiers: list[str] | None, mode: str) -> list[str]:
    from .catalog import BY_ID
    from .service import Problem

    if identifiers is not None:
        selection = list(dict.fromkeys(identifiers))
        for identifier in selection:
            account = service.account_row(identifier)
            if content["kind"] not in BY_ID[account["platform"]]["kinds"]:
                raise Problem("文章只能选择文章账号，视频只能选择视频账号", "content_kind_mismatch")
        return selection
    previous = content.get("platformPublicationStates", {})
    selection = [row["id"] for row in service.store.all("SELECT * FROM accounts WHERE status='ready' ORDER BY rowid")
                 if row["platform"] in BY_ID
                 and service.store.account_default_selected(row["id"])
                 and content["kind"] in BY_ID[row["platform"]]["kinds"]
                 and mode in BY_ID[row["platform"]]["modes"]
                 and row["platform"] not in previous]
    if not selection:
        raise Problem("没有默认选中、已连接且尚未发布的兼容账号；请在账号管理开启默认选中，或明确提供对应类型的 accountIds", "no_eligible_accounts")
    return selection


async def execute_tool(service, data_dir: Path, name: str, arguments: dict) -> dict:
    """Called inside the desktop engine after scoped MCP authentication."""
    from .catalog import BY_ID, PLATFORMS
    from .content import import_asset
    from .service import Problem, require

    model = MODELS.get(name)
    if model is None:
        return tool_error("未知的 Easy Publish MCP 工具", "unknown_tool")
    try:
        values = model.model_validate(arguments)
    except ValidationError:
        return tool_error("工具参数不完整或格式不正确，请检查 inputSchema", "invalid_arguments")
    try:
        if name == "list_platforms":
            from .article_templates import ARTICLE_TEMPLATES
            platforms = [item for item in PLATFORMS if values.kind is None or values.kind in item["kinds"]]
            value = {"platforms": platforms, "articleTemplates": ARTICLE_TEMPLATES}
        elif name == "list_accounts":
            rows = service.store.all("SELECT * FROM accounts ORDER BY rowid")
            value = {"kind": values.kind, "accounts": [service.store.account(row) for row in rows
                     if row["platform"] in BY_ID and values.kind in BY_ID[row["platform"]]["kinds"]
                     and (not values.connectedOnly or row["status"] == "ready")]}
        elif name == "list_contents":
            rows = service.store.all("SELECT * FROM contents ORDER BY updated DESC")
            if values.kind:
                rows = [row for row in rows if json.loads(row["data"])["kind"] == values.kind]
            items = [service.store.content(row) for row in rows[values.offset:values.offset + values.limit]]
            keys = ("id", "kind", "title", "createdAt", "updatedAt", "revision", "articleTemplateId", "platformPublicationStates")
            value = {"contents": [{key: item[key] for key in keys if key in item} for item in items], "total": len(rows),
                     "nextOffset": values.offset + len(items) if values.offset + len(items) < len(rows) else None}
        elif name == "get_content":
            value = {"content": _content(service, values.contentId)}
        elif name == "save_content":
            value = {"content": service.save_content(values.content, values.contentId)}
        elif name == "import_asset":
            if not Path(values.path).expanduser().is_absolute():
                raise Problem("素材导入必须使用用户授权的本地绝对路径", "invalid_path")
            try:
                row = await asyncio.to_thread(import_asset, data_dir, values.path, values.kind)
            except (ValueError, OSError):
                raise Problem("素材导入失败，请检查文件格式、大小及读取权限", "asset_import_failed") from None
            service.store.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?)", tuple(row[key] for key in ("id", "name", "kind", "size", "mime", "path", "text")))
            value = {"asset": service.store.asset(row)}
        elif name == "preview_article":
            value = await service.preview_article(values.body, values.articleTemplateId, values.platformId, values.accountId)
        elif name in {"preflight", "publish"}:
            # Resolve replay before default filtering: after a successful first
            # attempt its platforms are intentionally no longer auto-selected,
            # and later preference changes must not alter the original targets.
            previous = service.store.one("SELECT * FROM batches WHERE request_id=?", (str(values.requestId),)) if name == "publish" else None
            if previous:
                selection = values.accountIds if values.accountIds is not None else json.loads(previous["selection"])
                value = {"batch": service.create_batch(values.contentId, selection, values.mode, str(values.requestId)), "reused": True}
            else:
                content = _content(service, values.contentId)
                selection = _select_accounts(service, content, values.accountIds, values.mode)
                if name == "preflight":
                    value = {**service.preflight(content, selection, values.mode), "contentId": values.contentId,
                             "accountIds": selection, "mode": values.mode}
                else:
                    value = {"batch": service.create_batch(values.contentId, selection, values.mode, str(values.requestId)), "reused": False}
        elif name == "list_batches":
            where, parameters = (" WHERE content_id=?", (values.contentId,)) if values.contentId else ("", ())
            total = service.store.one("SELECT count(*) AS total FROM batches" + where, parameters)["total"]
            rows = service.store.all("SELECT * FROM batches" + where + " ORDER BY created DESC LIMIT ? OFFSET ?", parameters + (values.limit, values.offset))
            value = {"batches": [service.store.batch(row) for row in rows], "total": total,
                     "nextOffset": values.offset + len(rows) if values.offset + len(rows) < total else None}
        elif name == "get_batch":
            value = {"batch": service.store.batch(require(service.store.one("SELECT * FROM batches WHERE id=?", (values.batchId,)), "发布批次不存在"))}
        elif name == "cancel_batch":
            require(service.store.one("SELECT * FROM batches WHERE id=?", (values.batchId,)), "发布批次不存在")
            await service.cancel_batch(values.batchId)
            value = {"ok": True, "batchId": values.batchId}
        else:
            return tool_error("未知的 Easy Publish MCP 工具", "unknown_tool")
        return tool_result(value)
    except Problem as error:
        return tool_error(error.message, error.code)
    except Exception:
        return tool_error("本地操作未完成，请检查桌面应用和输入内容", "operation_failed")


class DesktopBridge:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    async def call(self, name: str, arguments: dict) -> dict:
        try:
            endpoint = read_endpoint(self.data_dir)
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=60) as client:
                async with client.stream("POST", f"http://127.0.0.1:{endpoint['port']}/api/mcp/call",
                                         headers={"Authorization": "Bearer " + endpoint["token"], "X-Easy-Publish-Instance": endpoint["instanceId"]},
                                         json={"name": name, "arguments": arguments}) as response:
                    if response.status_code in {401, 403}:
                        return tool_error("桌面 MCP 会话已变化，请重新打开应用或重试连接", "desktop_session_changed")
                    if response.status_code != 200:
                        return tool_error("桌面应用未接受 MCP 请求，请检查应用版本与运行状态", "desktop_unavailable")
                    chunks, length = [], 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > MAX_RESPONSE_BYTES:
                            return tool_error("桌面返回内容过大，请缩小查询范围", "response_too_large")
                        chunks.append(chunk)
                    value = json.loads(b"".join(chunks))
            if not isinstance(value, dict) or not isinstance(value.get("content"), list) or type(value.get("isError")) is not bool:
                raise ValueError("Invalid tool result")
            return value
        except ConnectionUnavailable as error:
            return tool_error(str(error), "desktop_unavailable")
        except Exception:
            return tool_error("无法连接 Easy Publish 桌面应用；如 publish 调用中断，请使用相同 requestId 查询原任务，勿生成新请求重发", "desktop_unavailable")


class StdioServer:
    def __init__(self, bridge: DesktopBridge):
        self.bridge = bridge
        self.initialized = False

    @staticmethod
    def error(identifier, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}

    async def handle(self, request) -> dict | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return self.error(None, -32600, "Invalid JSON-RPC request")
        identifier = request.get("id")
        if "id" not in request:
            # Notifications never produce a response, including initialization
            # acknowledgement and optional cancellation notifications.
            return None
        if identifier is not None and (isinstance(identifier, bool) or not isinstance(identifier, (str, int))):
            return self.error(None, -32600, "Invalid JSON-RPC request id")
        method, params = request["method"], request.get("params", {})
        if not isinstance(params, dict):
            return self.error(identifier, -32602, "Parameters must be an object")
        if method == "initialize":
            version = params.get("protocolVersion")
            info = params.get("clientInfo")
            if self.initialized:
                return self.error(identifier, -32600, "Server already initialized")
            if not isinstance(version, str) or not isinstance(params.get("capabilities"), dict) or not isinstance(info, dict) or not isinstance(info.get("name"), str) or not isinstance(info.get("version"), str):
                return self.error(identifier, -32602, "Invalid initialize parameters")
            self.initialized = True
            result = {"protocolVersion": version if version in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "easy-publish", "version": __version__},
                      "instructions": "Keep Easy Publish desktop running. Save content locally, inspect compatible connected accounts, then call preflight. Omitted accountIds respect defaultSelected; explicit IDs can override this preference within the matching article/video type. Call publish only when publication is requested, with a stable UUID requestId. Reusing a requestId retrieves the original batch even after preferences change. Poll get_batch for results; never automatically republish an unknown result."}
        elif method == "ping":
            result = {}
        elif not self.initialized:
            return self.error(identifier, -32002, "Initialize the MCP connection first")
        elif method == "tools/list":
            if params.get("cursor") is not None:
                return self.error(identifier, -32602, "Invalid tools cursor")
            result = {"tools": tools_list()}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return self.error(identifier, -32602, "Invalid tools/call parameters")
            if name not in MODELS:
                result = tool_error("未知的 Easy Publish MCP 工具", "unknown_tool")
            else:
                try:
                    MODELS[name].model_validate(arguments)
                except ValidationError:
                    result = tool_error("工具参数不完整或格式不正确，请检查 inputSchema", "invalid_arguments")
                else:
                    result = await self.bridge.call(name, arguments)
        else:
            return self.error(identifier, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": identifier, "result": result}


def mcp_main(data_dir: Path) -> int:
    logging.disable(logging.CRITICAL)
    server = StdioServer(DesktopBridge(data_dir.expanduser().absolute()))
    # UTF-8 NDJSON is the MCP stdio transport. The engine ready handshake and
    # any third-party logging are deliberately absent from this entry point.
    with asyncio.Runner() as runner:
        while line := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
            if len(line) > MAX_MESSAGE_BYTES:
                while line and not line.endswith(b"\n"):
                    line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
                response = server.error(None, -32600, "MCP request exceeds size limit")
            else:
                try:
                    value = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    response = server.error(None, -32700, "Parse error")
                else:
                    try:
                        response = runner.run(server.handle(value))
                    except Exception:
                        response = server.error(value.get("id") if isinstance(value, dict) else None, -32603, "Internal MCP error")
            if response is not None:
                sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                sys.stdout.buffer.flush()
    return 0
