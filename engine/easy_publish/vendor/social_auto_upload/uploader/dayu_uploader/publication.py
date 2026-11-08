"""Observe the owner's preview and publication clicks without submitting for them."""
import json
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

from patchright.async_api import Page, expect
from easy_publish.vendor.social_auto_upload import runtime
from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader.verification import check_dayu_article_content, wait_dayu_publication
from easy_publish.vendor.social_auto_upload.uploader.dayu_uploader.schedule import read_dayu_schedule


async def _prepare_dayu_preview(page: Page, result: dict, result_file: Path,
                                article_html: str | None) -> None:
    result["status"] = "awaiting_user"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # 主表单“发表”仅生成预览；用精确按钮名区分后续的“确认发表”。
    save_url = "https://mp.dayu.com/dashboard/save-draft"
    async def preserve_article_html(route):
        # 仅替换本次文章正文，保留表单实填的标题、来源、封面及会话字段；不改变平台过滤规则。
        fields = parse_qsl(route.request.post_data or "", keep_blank_values=True)
        if route.request.method != "POST" or sum(key == "content" for key, _ in fields) != 1:
            await route.abort()
            raise RuntimeError("文章预览请求的正文结构变化，拒绝提交")
        if result.get("origin_id") and dict(fields).get("draft_id") != result["origin_id"]:
            await route.abort()
            raise RuntimeError("预览请求与待恢复的草稿 ID 不一致")
        await route.fallback(post_data=urlencode([
            (key, article_html if key == "content" else value) for key, value in fields
        ]))
    if article_html is not None:
        await page.route(save_url, preserve_article_html)
    try:
        async with page.expect_response(
            lambda r: r.url == "https://mp.dayu.com/dashboard/save-draft" and r.request.method == "POST",
            timeout=0,
        ) as draft_event:
            runtime.awaiting_user("大鱼号文章已填写，请在网页检查并亲自点击发表，再自行确认发表")
    finally:
        if article_html is not None:
            await page.unroute(save_url, preserve_article_html)
    draft_response = await draft_event.value
    if not draft_response.ok:
        raise RuntimeError(f"发表预览保存失败: HTTP {draft_response.status}")
    draft = (await draft_response.json()).get("data", {})
    if not draft.get("_id"):
        raise RuntimeError("发表预览未返回草稿 ID，拒绝确认发表")
    if result.get("origin_id") and draft["_id"] != result["origin_id"]:
        raise RuntimeError("预览响应返回了其他草稿 ID，拒绝确认发表")
    result.update(origin_id=draft["_id"], status="preview_ready")
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # 在不可逆的确认发表前读服务端设置，防止 UI 显示正确但原稿时间未保存。
    await read_dayu_schedule(page.context, result["origin_id"], result.get("schedule"))
    if article_html is not None:
        preview_url = draft.get("previewUrl", "")
        parsed = urlsplit(preview_url)
        if (parsed.scheme != "https" or parsed.hostname != "mparticle.uc.cn"
                or parsed.path != "/preview.html" or parse_qs(parsed.query).get("wm_aid") != [draft["_id"]]):
            raise RuntimeError("文章预览地址与本次草稿不符，拒绝确认发表")
        preview = await page.context.new_page()
        try:
            await preview.goto(preview_url, wait_until="domcontentloaded", timeout=45000)
            # 服务端预览的 article-content 是正文区域，排除页头标题、作者及页面其他图片。
            body = preview.locator(".article-content")
            await expect(body).to_have_count(1, timeout=30000)
            await check_dayu_article_content(body, result["manifest"])
            # 预览图使用懒加载；逐张滚动并核对解码，不能把尚未加载的截图作为效果证据。
            images = body.locator("img")
            for index in range(await images.count()):
                image = images.nth(index)
                await image.scroll_into_view_if_needed()
                await expect(image).to_have_js_property("complete", True, timeout=30000)
                if await image.evaluate("image => image.naturalWidth") <= 0:
                    raise RuntimeError(f"文章预览第 {index + 1} 张图片无法加载")
            result["style_check"]["preview_checked"] = True
            result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            await preview.close()


async def submit_dayu_publication(page: Page, result: dict, result_file: Path,
                                  article_html: str | None = None) -> None:
    """用户点击主表单发表与最终确认；本地只记录并核对本次作品回执。"""
    # Install the final observer before handing over the first button: an owner
    # may confirm while the read-only preview verification is still finishing.
    async with page.expect_response(
        lambda r: r.url == "https://mp.dayu.com/dashboard/submit-article" and r.request.method == "POST",
        timeout=0,
    ) as submit_event:
        await _prepare_dayu_preview(page, result, result_file, article_html)
        result["status"] = "awaiting_user"
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        runtime.awaiting_user("大鱼号预览已生成，请检查后亲自点击网页中的确认发表")
    response = await submit_event.value
    if not response.ok:
        raise RuntimeError(f"发表结果未确认: HTTP {response.status}，请核查原作品")
    submitted = (await response.json()).get("data", {})
    if not submitted.get("content_id"):
        raise RuntimeError("发表响应未返回 content_id，结果未知，请核查原作品")
    # 最终发表响应返回本次作品 ID 即成功；审核和公开访问不属于发布命令的完成条件。
    result.update(content_id=str(submitted["content_id"]), article_id=submitted.get("_id"))
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if result.get("schedule"):
        # content_id 确认已提交，再从作品列表复核原作品及安排；草稿不能算定时提交成功。
        record = await wait_dayu_publication(page.context, content_id=result["content_id"],
                                             origin_id=result["origin_id"], title=result.get("title"),
                                             schedule=result["schedule"])
        result.update(schedule_verified=True, platform_status=record["status"])
        result.update(success=True, status="scheduled")
    else:
        result.update(success=True, status="submitted")
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
