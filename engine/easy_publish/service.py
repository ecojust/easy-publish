"""Local library, immutable batch snapshots, and a bounded publishing queue with manual browser handoffs."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from easy_publish.catalog import BY_ID, PLATFORMS, bilibili_categories
from easy_publish.article_templates import TEMPLATE_IDS, WENYAN_TEMPLATE_IDS, article_format_notes
from easy_publish.content import ContentInput, image_sources, markdown_html, render_article
from easy_publish.target_projection import project_content, target_options
from easy_publish.store import Store, dump, now, uid
from easy_publish.vault import VaultError
from easy_publish.providers.base import BROWSER_PUBLISH_PLATFORMS
from easy_publish.wenyan_runtime import WenyanError, WenyanRenderer


_UNSET = object()
MAX_PUBLISH_JOBS = 3


class Problem(Exception):
    def __init__(self, message, code="invalid_request", status=400):
        self.message, self.code, self.status = message, code, status


def require(row, message="记录不存在"):
    if row is None:
        raise Problem(message, "not_found", 404)
    return row


class Service:
    def __init__(self, data_dir: Path, runtime):
        self.data_dir, self.runtime = data_dir, runtime
        self.store = Store(data_dir)
        self.wake = asyncio.Event()
        self.jobs = {}
        self.publishing_jobs = {}
        self.publishing_accounts = {}
        self.account_cancellations = {}
        self.queue_task = None
        self.closing = False
        self.wenyan = WenyanRenderer()

    async def start(self):
        self.store.recover()
        self._reconcile_account_storage()
        self.queue_task = asyncio.create_task(self.queue_loop())

    def _reconcile_account_storage(self):
        """Keep legacy accounts visible without trying to unlock their old keys."""
        vault = self.runtime.vault
        if not vault.ready:
            return
        for account in self.store.all("SELECT * FROM accounts"):
            api_account = BY_ID[account["platform"]]["auth"] == "wechat_api"
            kind = "credentials" if api_account else "session"
            if not vault.requires_reconnect(account["id"], kind):
                continue
            message = ("旧版公众号凭据未读取；请移除此本地账号后重新添加 AppID 和 AppSecret"
                       if api_account else "旧版登录会话未读取，请重新登录；以后只使用应用内保存的会话")
            configured = 0 if api_account else account["configured"]
            with self.store.transaction() as db:
                db.execute("DELETE FROM account_metadata WHERE account_id=?", (account["id"],))
                if (account["status"], account["message"], account["configured"]) != ("login_required", message, configured):
                    db.execute("UPDATE accounts SET status='login_required',message=?,configured=?,updated=? WHERE id=?",
                               (message, configured, now(), account["id"]))

    async def close(self):
        self.closing = True
        await self.wenyan.close()
        await self.runtime.shutdown()
        for task in [self.queue_task, *self.jobs.values(), *self.publishing_jobs.values()]:
            if task and not task.cancelling():
                task.cancel()
        await asyncio.gather(*(t for t in [self.queue_task, *self.jobs.values(), *self.publishing_jobs.values()] if t), return_exceptions=True)
        await asyncio.gather(*list(self.account_cancellations.values()), return_exceptions=True)
        self.store.recover()
        self.store.close()

    def state(self):
        from .article_templates import ARTICLE_TEMPLATES
        return {"platforms": PLATFORMS,
                "articleTemplates": ARTICLE_TEMPLATES,
                "contents": [self.store.content(r) for r in self.store.all("SELECT * FROM contents ORDER BY updated DESC")],
                "accounts": [self.store.account(r) for r in self.store.all("SELECT * FROM accounts ORDER BY rowid")],
                "batches": [self.store.batch(r) for r in self.store.all("SELECT * FROM batches ORDER BY created DESC LIMIT 200")],
                "runtime": self.runtime.state()}

    def content_row(self, identifier):
        return require(self.store.one("SELECT * FROM contents WHERE id=?", (identifier,)), "内容不存在")

    def asset_row(self, identifier):
        return require(self.store.one("SELECT * FROM assets WHERE id=?", (identifier,)), "素材不存在")

    def account_row(self, identifier):
        return require(self.store.one("SELECT * FROM accounts WHERE id=?", (identifier,)), "账号不存在")

    def save_content(self, content: ContentInput, identifier=None):
        previous = json.loads(self.content_row(identifier)["data"]) if identifier else None
        value = content.model_dump()
        # The original text from the retired plain-text editor is read-only.
        # Editing Markdown must never erase the archived draft or its source.
        if previous and isinstance(previous.get("legacyFormatting"), dict):
            value["legacyFormatting"] = previous["legacyFormatting"]
            value["sourceText"] = previous.get("sourceText", "")
        else:
            value["sourceText"] = ""
        if any(len(t) > 80 for t in value["tags"]):
            raise Problem("单个话题不能超过 80 字")
        if len(value["platformOptions"]) > len(PLATFORMS) or any(
            len(options) > 10 or any(len(k) > 100 or len(v) > 2000 for k, v in options.items())
            for options in value["platformOptions"].values()
        ):
            raise Problem("平台选项过长")
        for key, kind in (("videoAssetId", "video"), ("coverAssetId", "image")):
            if value[key] and self.asset_row(value[key])["kind"] != kind:
                raise Problem("素材类型与用途不匹配")
        for asset_id in value["imageAssetIds"]:
            if self.asset_row(asset_id)["kind"] != "image":
                raise Problem("正文素材必须是图片")
        timestamp = now()
        if identifier:
            self.content_row(identifier)
            self.store.execute("UPDATE contents SET data=?,updated=?,revision=revision+1 WHERE id=?", (dump(value), timestamp, identifier))
        else:
            identifier = uid()
            self.store.execute("INSERT INTO contents VALUES (?,?,?,?,?)", (identifier, dump(value), timestamp, timestamp, 1))
        return self.store.content(self.content_row(identifier))

    def busy_account(self, identifier):
        return self.store.one("SELECT id FROM tasks WHERE account_id=? AND status IN ('queued','running','submitting','awaiting_user')", (identifier,)) or identifier in self.publishing_accounts.values() or identifier in self.jobs or identifier in self.account_cancellations

    def add_account(self, platform_id, label, credentials):
        platform = BY_ID.get(platform_id)
        if not platform:
            raise Problem("平台尚未接入")
        if not label.strip():
            raise Problem("请填写账号名称")
        if not self.runtime.vault.ready:
            raise Problem(self.runtime.vault.message, "vault_unavailable", 503)
        identifier = uid()
        if platform["auth"] == "wechat_api":
            credentials = credentials or {}
            if not credentials.get("appId", "").strip() or not credentials.get("appSecret", "").strip():
                raise Problem("请填写公众号 AppID 和 AppSecret")
            self.runtime.vault.put(identifier, "credentials", dump(credentials).encode())
        elif credentials:
            raise Problem("浏览器账号不接受应用密钥")
        self.store.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
                           (identifier, platform_id, label.strip(), "login_required", "请连接账号", now(), int(platform["auth"] == "wechat_api")))
        if platform_id == "wechat":
            self.store.execute("INSERT INTO account_preferences(account_id,default_selected,wechat_template_id) VALUES (?,1,?)",
                               (identifier, "wenyan-default"))
        return self.store.account(self.account_row(identifier))

    def set_account_preferences(self, identifier, default_selected=_UNSET, wechat_template_id=_UNSET, video_options=_UNSET):
        account = self.account_row(identifier)
        if default_selected is _UNSET and wechat_template_id is _UNSET and video_options is _UNSET:
            raise Problem("请提供需要修改的账号设置")
        if default_selected is not _UNSET and type(default_selected) is not bool:
            raise Problem("默认选择设置必须为开或关")
        if wechat_template_id is not _UNSET:
            if account["platform"] != "wechat":
                raise Problem("只有微信公众号账号可以设置公众号模板", "template_account_mismatch")
            if not isinstance(wechat_template_id, str) or wechat_template_id not in TEMPLATE_IDS:
                raise Problem("公众号模板不存在，请重新选择")
        if video_options is not _UNSET:
            platform = BY_ID[account["platform"]]
            if "video" not in platform["kinds"]:
                raise Problem("只有视频账号可以设置视频发布选项", "video_options_account_mismatch")
            fields = {field["key"]: field for field in platform["fields"] if "video" in field["kinds"]}
            if not isinstance(video_options, dict) or len(video_options) > 10 or any(
                not isinstance(key, str) or key not in fields or not isinstance(value, str) or len(value) > 2000
                for key, value in video_options.items()
            ):
                raise Problem("视频设置包含此账号不支持的字段或字段过长", "invalid_video_options")
            # Partial preferences are drafts. Required/conditional fields are
            # checked before publication; saving does not assert originality.
            for key, value in video_options.items():
                field = fields[key]
                if value and field.get("choices") and value not in {choice["value"] for choice in field["choices"]}:
                    raise Problem(f"{field['label']}的选项无效", "invalid_video_options")
                if value and field.get("dynamicChoices") == "bilibiliCategories" and value not in {choice["value"] for choice in self.store.account_categories(identifier)}:
                    raise Problem("视频分区不在此账号的可用分区中，请重新验证账号并选择分区", "invalid_video_options")
        with self.store.transaction() as db:
            selected = self.store.account_default_selected(identifier) if default_selected is _UNSET else default_selected
            template = self.store.account_wechat_template(identifier) if wechat_template_id is _UNSET else wechat_template_id
            options = self.store.account_video_options(identifier) if video_options is _UNSET else video_options
            db.execute("INSERT INTO account_preferences(account_id,default_selected,wechat_template_id,video_options) VALUES (?,?,?,?) "
                       "ON CONFLICT(account_id) DO UPDATE SET default_selected=excluded.default_selected,wechat_template_id=excluded.wechat_template_id,video_options=excluded.video_options",
                       (identifier, int(selected), template, dump(options) if options is not None else None))
        return self.store.account(account)

    def _video_options(self, content, identifier, platform_id, *, frozen=False):
        if "accountVideoOptions" in content:
            mapping = content["accountVideoOptions"]
            options = mapping.get(identifier) if isinstance(mapping, dict) else None
            if not isinstance(options, dict) or len(options) > 10 or any(
                not isinstance(key, str) or not isinstance(value, str) or len(key) > 100 or len(value) > 2000
                for key, value in options.items()
            ):
                raise Problem("账号视频设置快照无效，请重新创建发布任务", "invalid_video_options_snapshot")
            return target_options(platform_id, options, "video")
        previous = target_options(platform_id, content.get("platformOptions", {}).get(platform_id, {}), "video")
        # A task created before account settings existed must never consult the
        # live account. This includes retrying a task after preferences changed.
        if frozen:
            return previous
        if content.get("videoOptionsSource", "content") == "content" and previous:
            return previous
        return target_options(platform_id, self.store.account_video_options(identifier) or {}, "video")

    def _account_content(self, content, identifier, platform_id, *, frozen=False):
        selected = project_content(content, platform_id)
        if content["kind"] == "video":
            selected["platformOptions"] = {platform_id: self._video_options(content, identifier, platform_id, frozen=frozen)}
        return selected

    async def _render_article(self, body, template, platform):
        if template in WENYAN_TEMPLATE_IDS and platform in {None, "wechat"}:
            try:
                return await self.wenyan.render(body, template.removeprefix("wenyan-"))
            except WenyanError as error:
                raise Problem(str(error), "article_render_failed", 503) from None
        return render_article(body, template, platform)

    async def preview_article(self, body, template="clean", platform=None, account_id=None):
        if account_id is not None:
            account = self.account_row(account_id)
            if account["platform"] != "wechat" or platform not in {None, "wechat"}:
                raise Problem("公众号模板预览需要选择微信公众号账号", "template_account_mismatch")
            template, platform = self.store.account_wechat_template(account_id), "wechat"
        if platform is not None and (platform not in BY_ID or "article" not in BY_ID[platform]["kinds"]):
            raise Problem("请选择支持文章的平台")
        if template not in TEMPLATE_IDS:
            raise Problem("文章模板不存在，请重新选择")
        return {"html": await self._render_article(body, template, platform), "notes": article_format_notes(platform)}

    def delete_account(self, identifier):
        self.account_row(identifier)
        if self.busy_account(identifier):
            raise Problem("账号仍有登录或发布任务，请等待完成或取消任务", "account_busy", 409)
        self.runtime.vault.delete(identifier)
        with self.store.transaction() as db:
            db.execute("DELETE FROM account_metadata WHERE account_id=?", (identifier,))
            db.execute("DELETE FROM account_preferences WHERE account_id=?", (identifier,))
            db.execute("DELETE FROM accounts WHERE id=?", (identifier,))

    async def connect_account(self, identifier, operation):
        account = self.account_row(identifier)
        if self.busy_account(identifier):
            raise Problem("此账号已有进行中的任务", "account_busy", 409)
        if not self.runtime.vault.ready:
            raise Problem(self.runtime.vault.message, "vault_unavailable", 503)
        if BY_ID[account["platform"]]["auth"] == "wechat_api":
            try:
                configured = bool(self.runtime.vault.get(identifier, "credentials"))
            except VaultError:
                configured = False
            if not configured:
                raise Problem("应用内没有可用的公众号凭据，请移除此本地账号后重新添加 AppID 和 AppSecret", "credentials_required")
        if BY_ID[account["platform"]]["auth"] == "browser" and not self.runtime.state()["browserReady"]:
            raise Problem("请先在设置中安装发布浏览器", "browser_required")
        with self.store.transaction() as db:
            db.execute("DELETE FROM account_metadata WHERE account_id=?", (identifier,))
            db.execute("UPDATE accounts SET status='logging_in',message='正在连接账号',updated=? WHERE id=?", (now(), identifier))
        self.jobs[identifier] = asyncio.create_task(self._connect(account, operation))

    async def cancel_account(self, identifier):
        self.account_row(identifier)
        cancellation = self.account_cancellations.get(identifier)
        if cancellation is None:
            job = self.jobs.get(identifier)
            if job is None or job.done():
                return
            cancellation = asyncio.create_task(self._cancel_account_job(identifier, job))
            self.account_cancellations[identifier] = cancellation
        # A disconnected request or a repeated click must not interrupt worker cleanup.
        await asyncio.shield(cancellation)

    async def _cancel_account_job(self, identifier, job):
        try:
            # The worker may have completed before this coroutine got its first turn.
            if job.done():
                return
            if not job.cancelling():
                job.cancel()
            # Runtime.run owns the worker process group and deletes its workspace
            # in finally. Retain account ownership until that cleanup has finished.
            await asyncio.gather(job, return_exceptions=True)
            with self.store.transaction() as db:
                db.execute("DELETE FROM account_metadata WHERE account_id=?", (identifier,))
                db.execute("UPDATE accounts SET status='login_required',message='连接已取消',updated=? WHERE id=?", (now(), identifier))
        finally:
            # A task cancelled before its first turn never enters _connect's finally.
            if self.jobs.get(identifier) is job:
                self.jobs.pop(identifier, None)
            self.account_cancellations.pop(identifier, None)

    async def _connect(self, account, operation):
        identifier = account["id"]
        async def event(value):
            if value.get("event") == "progress":
                self.store.execute("UPDATE accounts SET message=?,updated=? WHERE id=?", (value.get("message", "正在连接")[:500], now(), identifier))
        try:
            result = await self.runtime.run(operation, {"platform_id": account["platform"], "account_id": identifier,
                                            "kind": BY_ID[account["platform"]]["kinds"][0], "mode": "draft", "title": ""}, event, "account-" + identifier)
            status = "ready" if result["status"] == "ready" else "expired" if operation == "check" else "login_required"
            with self.store.transaction() as db:
                db.execute("DELETE FROM account_metadata WHERE account_id=?", (identifier,))
                if status == "ready" and account["platform"] == "bilibili":
                    metadata = result.get("metadata")
                    categories = metadata.get("bilibiliCategories") if isinstance(metadata, dict) else None
                    db.execute("INSERT INTO account_metadata VALUES (?,?)", (identifier, dump(bilibili_categories(categories))))
                db.execute("UPDATE accounts SET status=?,message=?,updated=? WHERE id=?", (status, result["message"][:500], now(), identifier))
        except asyncio.CancelledError:
            self.store.execute("UPDATE accounts SET status='login_required',message='登录已中断',updated=? WHERE id=?", (now(), identifier))
            raise
        except Exception:
            self.store.execute("UPDATE accounts SET status='error',message='连接失败，请重新连接',updated=? WHERE id=?", (now(), identifier))
        finally:
            self.jobs.pop(identifier, None)

    def preflight(self, content, account_ids, mode, *, frozen=False):
        if not account_ids:
            raise Problem("请选择至少一个发布账号")
        targets = []
        common = []
        if not content["title"].strip():
            common.append("请填写标题")
        if not self.runtime.vault.ready:
            common.append("应用内账号存储不可用，请在设置中检查")
        if content["kind"] == "article" and not content["body"].strip():
            common.append("请填写文章正文")
        if content["kind"] == "video" and not content["videoAssetId"]:
            common.append("请导入视频")
        shared_content = content
        for identifier in account_ids:
            account = self.store.one("SELECT * FROM accounts WHERE id=?", (identifier,))
            errors, warnings = list(dict.fromkeys(common)), []
            if not account:
                errors.append("账号已被移除")
            else:
                platform = BY_ID[account["platform"]]
                content = self._account_content(shared_content, identifier, platform["id"], frozen=frozen)
                account_video_settings = content["kind"] == "video" and not frozen and (
                    shared_content.get("videoOptionsSource", "content") == "account"
                    or not target_options(platform["id"], shared_content.get("platformOptions", {}).get(platform["id"], {}), "video")
                )
                ids = [(content.get("videoAssetId"), "video"), (content.get("coverAssetId"), "image")]
                ids += [(i, "image") for i in content["imageAssetIds"]]
                if content["kind"] == "article":
                    for source in image_sources(markdown_html(content["body"])):
                        if not source.startswith("easy-asset://"):
                            errors.append("正文图片需导入素材库后插入，不支持外部图片地址")
                        else:
                            ids.append((source.removeprefix("easy-asset://"), "image"))
                for asset_id, kind in ids:
                    if asset_id:
                        asset = self.store.one("SELECT * FROM assets WHERE id=?", (asset_id,))
                        if not asset or asset["kind"] != kind or not Path(asset["path"]).is_file():
                            errors.append("引用的素材已失效，请重新导入")
                prior = self.store.publication_states(content.get("id", "")).get(platform["id"])
                if prior == "unknown":
                    warnings.append("此稿件在该平台有结果不明的提交，请先核对平台，避免重复发布")
                elif prior in {"submitted", "published"}:
                    warnings.append("此稿件已向该平台提交过；继续发布将创建一次新的投稿")
                if content["kind"] not in platform["kinds"]:
                    errors.append("此平台不支持当前内容类型")
                if mode not in platform["modes"]:
                    errors.append("此平台暂不支持保存草稿" if mode == "draft" else "此平台暂不支持直接发布")
                if account["status"] != "ready":
                    errors.append("请先连接或重新验证账号")
                if platform["auth"] == "browser" and not self.runtime.state()["browserReady"]:
                    errors.append("请先在设置中安装发布浏览器")
                limit = platform["titleLimit"]
                count = sum(0.5 if ord(c) < 128 else 1 for c in content["title"]) if platform["id"] == "dayu" else len(content["title"])
                if count > limit:
                    errors.append(f"标题超过平台 {limit} 字限制")
                if platform["id"] == "dayu":
                    if count < 5:
                        errors.append("大鱼号标题至少 5 字（英文按半字计算）")
                    if len(content["body"]) > 20_000:
                        errors.append("大鱼号正文不能超过 20000 字")
                    if content["author"] and sum(0.5 if ord(c) < 128 else 1 for c in content["author"]) > 10:
                        errors.append("大鱼号作者不能超过 10 字")
                if platform["id"] == "wechat":
                    if not content["coverAssetId"]:
                        errors.append("公众号文章需要封面图片")
                    warnings.append("公众号需要对应接口权限及 IP 白名单；正式发布不等于向粉丝群发")
                if platform["id"] == "channels" and len(content["title"]) < 7:
                    errors.append("视频号短标题需为 7–15 字")
                if platform["id"] == "xiaohongshu" and len(content["tags"]) > 10:
                    errors.append("小红书最多使用 10 个话题")
                options = content.get("platformOptions", {}).get(platform["id"], {})
                if platform["id"] == "kuaishou":
                    from .providers.kuaishou import metadata_errors
                    errors.extend(metadata_errors(content["title"], content["description"], content["tags"]))
                    warnings.append("快手会将标题、描述与话题合并为作品描述；提交后仍需平台审核")
                for field in platform["fields"]:
                    if content["kind"] not in field["kinds"]:
                        continue
                    value = options.get(field["key"], "")
                    if field["required"] and not value:
                        errors.append(f"请{'在账号管理中' if account_video_settings else ''}填写{field['label']}")
                    elif value and field.get("choices") and value not in {c["value"] for c in field["choices"]}:
                        errors.append(f"{field['label']}的选项无效")
                    elif value and field.get("dynamicChoices") == "bilibiliCategories" and value not in {c["value"] for c in self.store.account_categories(identifier)}:
                        errors.append("视频分区不在此账号的可用分区中，请重新验证账号并选择分区")
                if platform["id"] == "bilibili":
                    if len(content["description"]) > 2000:
                        errors.append("哔哩哔哩简介最多 2000 字")
                    tags = content["tags"]
                    if not 1 <= len(tags) <= 12:
                        errors.append("哔哩哔哩需要 1–12 个标签")
                    if any(not tag.strip() or len(tag) > 20 or re.search(r"[,，\r\n]", tag) for tag in tags):
                        errors.append("哔哩哔哩标签不能为空、超过 20 字或包含逗号及换行")
                    if len({tag.strip() for tag in tags}) != len(tags):
                        errors.append("哔哩哔哩标签不能重复")
                    source = options.get("source", "")
                    try:
                        parsed = urlsplit(source)
                        valid_source = (len(source) <= 200 and parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                                        and not parsed.username and not parsed.password and not re.search(r"\s", source))
                    except ValueError:
                        valid_source = False
                    if options.get("copyright") == "2" and not valid_source:
                        errors.append("转载视频需要填写有效的原始视频链接" + ("，请在账号管理中补充" if account_video_settings else ""))
                    if content["coverAssetId"]:
                        cover = self.store.one("SELECT * FROM assets WHERE id=?", (content["coverAssetId"],))
                        if cover and cover["size"] > 5 * 1024**2:
                            errors.append("哔哩哔哩封面不能超过 5 MiB")
                    warnings.append("哔哩哔哩投稿成功表示已提交审核，不代表已经公开发布")
                if platform["id"] == "dayu":
                    url = options.get("source_url", "")
                    try:
                        parsed = urlsplit(url)
                        valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                    except ValueError:
                        valid_url = False
                    if options.get("source") == "转载" and not valid_url:
                        errors.append("转载文章需要填写有效的原文链接")
                if platform["auth"] == "browser":
                    warnings.append("发布时可能需要在平台浏览器完成验证；平台页面变化可能影响自动化")
            targets.append({"accountId": identifier, "errors": errors, "warnings": warnings})
        return {"ok": all(not t["errors"] for t in targets), "targets": targets}

    def create_batch(self, content_id, account_ids, mode, request_id):
        account_ids = list(dict.fromkeys(account_ids))
        selection = dump(sorted(account_ids))
        previous = self.store.one("SELECT * FROM batches WHERE request_id=?", (request_id,))
        if previous:
            if previous["content_id"] != content_id or previous["mode"] != mode or previous["selection"] != selection:
                raise Problem("此请求编号已用于不同任务", "idempotency_conflict", 409)
            return self.store.batch(previous)
        content = self.store.content(self.content_row(content_id))
        snapshot = dict(content)
        if content["kind"] == "video":
            snapshot["accountVideoOptions"] = {
                identifier: self._video_options(content, identifier, account["platform"])
                for identifier in account_ids
                if (account := self.store.one("SELECT * FROM accounts WHERE id=?", (identifier,)))
            }
        check = self.preflight(snapshot, account_ids, mode)
        if not check["ok"]:
            raise Problem("发布检查未通过：" + "；".join(dict.fromkeys(e for t in check["targets"] for e in t["errors"])), "preflight_failed")
        identifier = uid()
        with self.store.transaction() as db:
            accounts = [self.account_row(account_id) for account_id in account_ids]
            if content["kind"] == "article":
                snapshot["accountTemplateIds"] = {
                    account["id"]: self.store.account_wechat_template(account["id"])
                    for account in accounts if account["platform"] == "wechat"
                }
            db.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)", (identifier, request_id, content_id, content["title"], content["kind"], mode, now(), dump(snapshot), selection))
            for account_id in account_ids:
                account = self.account_row(account_id)
                db.execute("INSERT INTO tasks(id,batch_id,account_id,account_label,platform,status,message) VALUES (?,?,?,?,?,'queued','等待发布')",
                           (uid(), identifier, account_id, account["label"], account["platform"]))
        self.wake.set()
        return self.store.batch(self.store.one("SELECT * FROM batches WHERE id=?", (identifier,)))

    async def payload(self, content, task, mode):
        content = self._account_content(content, task["account_id"], task["platform"], frozen=True)
        template = content.get("articleTemplateId", "clean")
        # New batches freeze each WeChat account's setting. An absent map marks
        # an older batch and must keep its original draft template, even on retry.
        if task["platform"] == "wechat" and "accountTemplateIds" in content:
            templates = content["accountTemplateIds"]
            template = templates.get(task["account_id"]) if isinstance(templates, dict) else None
            if not isinstance(template, str) or template not in TEMPLATE_IDS:
                raise Problem("公众号模板快照无效，请重新创建发布任务")
        def asset_path(identifier):
            return self.asset_row(identifier)["path"] if identifier else None
        images = set(content["imageAssetIds"])
        if content["kind"] == "article":
            images |= {s.removeprefix("easy-asset://") for s in image_sources(markdown_html(content["body"])) if s.startswith("easy-asset://")}
        return {"platform_id": task["platform"], "account_id": task["account_id"], "kind": content["kind"], "mode": mode,
                "title": content["title"].strip(), "body_html": await self._render_article(content["body"], template, task["platform"]) if content["kind"] == "article" else "",
                "description": content["description"], "tags": content["tags"], "author": content["author"],
                "video_path": asset_path(content["videoAssetId"]), "cover_path": asset_path(content["coverAssetId"]),
                "images": {i: asset_path(i) for i in images}, "options": content.get("platformOptions", {}).get(task["platform"], {})}

    async def queue_loop(self):
        while not self.closing:
            self.wake.clear()
            # Only one target prepares/uploads at a time. Waiting for a person
            # releases that preparation slot, but never its account ownership.
            preparing = self.store.one("SELECT id FROM tasks WHERE status IN ('running','submitting') LIMIT 1")
            if not preparing and len(self.publishing_jobs) < MAX_PUBLISH_JOBS:
                owned_accounts = set(self.publishing_accounts.values()) | self.jobs.keys() | self.account_cancellations.keys()
                task = next((row for row in self.store.all("SELECT * FROM tasks WHERE status='queued' ORDER BY rowid")
                             if row["account_id"] not in owned_accounts), None)
                if task:
                    identifier = task["id"]
                    # Reserve before scheduling so a second queue turn cannot
                    # launch this task twice or overlap a preparation phase.
                    self.store.execute("UPDATE tasks SET status='running',message='正在准备',started=?,finished=NULL WHERE id=?", (now(), identifier))
                    self.publishing_accounts[identifier] = task["account_id"]
                    job = asyncio.create_task(self.run_task(task))
                    self.publishing_jobs[identifier] = job
                    job.add_done_callback(lambda finished, identifier=identifier: self._publishing_done(identifier, finished))
                    continue
            await self.wake.wait()

    def _publishing_done(self, identifier, job):
        # A coroutine cancelled before its first turn never enters its finally
        # block. A done callback releases ownership in that case as well.
        if self.publishing_jobs.get(identifier) is job:
            self.publishing_jobs.pop(identifier, None)
            self.publishing_accounts.pop(identifier, None)
        if not job.cancelled():
            job.exception()  # Retrieve an unexpected exception, never log data.
        self.wake.set()

    async def run_task(self, task):
        identifier = task["id"]
        batch = self.store.one("SELECT * FROM batches WHERE id=?", (task["batch_id"],))
        content = json.loads(batch["snapshot"])
        current = self.store.one("SELECT status FROM tasks WHERE id=?", (identifier,))
        if not current or current["status"] != "running":
            return
        async def event(value):
            row = self.store.one("SELECT * FROM tasks WHERE id=?", (identifier,))
            if row["status"] not in {"running", "submitting", "awaiting_user"}:
                # Stop this worker without cancelling the shared queue loop.
                raise RuntimeError("Task no longer active")
            if value.get("event") == "submitting":
                # Runtime acknowledges this only after the durable SQLite commit.
                self.store.execute("UPDATE tasks SET status='submitting',fence=1,message=? WHERE id=?", (value.get("message", "正在提交")[:500], identifier))
            elif value.get("event") == "awaiting_user":
                if not row["fence"] or task["platform"] not in BROWSER_PUBLISH_PLATFORMS:
                    raise RuntimeError("Manual handoff requires a fenced browser task")
                self.store.execute("UPDATE tasks SET status='awaiting_user',message=? WHERE id=?", (value.get("message", "请在网页中手动发布")[:500], identifier))
                self.wake.set()
            elif value.get("event") == "progress":
                self.store.execute("UPDATE tasks SET message=? WHERE id=?", (value.get("message", "正在处理")[:500], identifier))
        try:
            check = self.preflight(content, [task["account_id"]], batch["mode"], frozen=True)
            if not check["ok"]:
                result = {"status": "failed", "message": "；".join(check["targets"][0]["errors"])}
            else:
                result = await self.runtime.run("publish", await self.payload(content, task, batch["mode"]), event, identifier)
        except asyncio.CancelledError:
            self.interrupt_task(identifier)
            raise
        except Problem as error:
            result = {"status": "failed", "message": error.message}
        except Exception:
            result = {"status": "failed", "message": "发布引擎未完成，请检查账号和运行环境"}
        current = self.store.one("SELECT * FROM tasks WHERE id=?", (identifier,))
        if current["status"] not in {"running", "submitting", "awaiting_user"}:
            return
        status = result.get("status", "failed")
        if status == "login_required":
            with self.store.transaction() as db:
                db.execute("DELETE FROM account_metadata WHERE account_id=?", (task["account_id"],))
                db.execute("UPDATE accounts SET status='expired',message='登录已过期，请重新连接',updated=? WHERE id=?", (now(), task["account_id"]))
            status = "failed"
        if status not in {"draft_created", "submitted", "published", "needs_attention", "failed", "cancelled", "unknown"}:
            status = "unknown" if current["fence"] else "failed"
        if current["fence"] and status in {"failed", "cancelled", "needs_attention"}:
            status = "unknown"
        message = result.get("message", "请核对平台结果")[:1000]
        if status == "unknown":
            message += "；请先到平台核对，系统不会自动重发"
        self.store.execute("UPDATE tasks SET status=?,message=?,remote_id=?,url=?,finished=? WHERE id=?",
                           (status, message, result.get("remote_id"), result.get("url"), now(), identifier))

    def interrupt_task(self, identifier):
        self.store.execute("UPDATE tasks SET status=CASE WHEN fence=1 THEN 'unknown' ELSE 'cancelled' END,message=CASE WHEN fence=1 THEN '网页操作或提交已中断，请到平台核对草稿和发布结果，勿直接重发' ELSE '任务已取消' END,finished=? WHERE id=? AND status IN ('queued','running','submitting','awaiting_user')", (now(), identifier))

    async def cancel_batch(self, identifier):
        require(self.store.one("SELECT id FROM batches WHERE id=?", (identifier,)))
        rows = self.store.all("SELECT * FROM tasks WHERE batch_id=? AND status IN ('queued','running','submitting','awaiting_user')", (identifier,))
        # Mark the whole batch before yielding. Otherwise a finishing browser
        # could let the queue start another target while cancellation is pending.
        for row in rows:
            self.interrupt_task(row["id"])
        jobs = [self.publishing_jobs[row["id"]] for row in rows if row["id"] in self.publishing_jobs]
        for job in jobs:
            if not job.done() and not job.cancelling():
                job.cancel()
        if jobs:
            # Runtime.run owns cleanup on task cancellation. Do not call
            # Runtime.cancel before run starts: its marker would poison retry.
            # A disconnected API caller must not interrupt process-tree cleanup.
            await asyncio.shield(asyncio.gather(*jobs, return_exceptions=True))
        self.wake.set()

    def retry_task(self, identifier):
        if identifier in self.publishing_jobs:
            raise Problem("任务仍在运行或清理发布窗口，请稍后再试", "retry_not_allowed", 409)
        task = require(self.store.one("SELECT * FROM tasks WHERE id=?", (identifier,)))
        if not self.store.task(task)["canRetry"]:
            raise Problem("此任务不能直接重试；请先到平台核对结果", "retry_not_allowed", 409)
        batch = self.store.one("SELECT * FROM batches WHERE id=?", (task["batch_id"],))
        check = self.preflight(json.loads(batch["snapshot"]), [task["account_id"]], batch["mode"], frozen=True)
        if not check["ok"]:
            raise Problem("；".join(check["targets"][0]["errors"]), "preflight_failed")
        self.store.execute("UPDATE tasks SET status='queued',message='等待重试',started=NULL,finished=NULL WHERE id=?", (identifier,))
        self.wake.set()
