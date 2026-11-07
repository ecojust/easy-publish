import { expect, test } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const fixturePath = path.resolve("tests/ui/bridge-fixture.js");

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: fixturePath });
  // Keep two article accounts enabled; individual video cases configure account defaults.
  // Fresh-account defaults are covered separately in account-defaults.spec.ts.
  await page.addInitScript(() => { for (const account of (window as any).__fixture.state.accounts) account.defaultSelected = ["wechat", "dayu"].includes(account.platformId); });
});

test("article editing preserves unsaved state, imports local images and sanitizes preview", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "文章", exact: false }).first()).toBeVisible();
  await page.getByRole("textbox", { name: "文章标题" }).fill("文章工作流测试");
  await page.getByRole("textbox", { name: "文章正文" }).fill("## 安全预览\n<script>window.__injected = true</script>\n![外部](https://example.test/track.png)\n[坏链接](javascript:alert(1))");
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "文章标题" })).toHaveValue("文章工作流测试");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/article-image.png"));
  await page.getByRole("button", { name: "插入正文图片" }).click();
  await expect(page.getByRole("textbox", { name: "文章正文" })).toHaveValue(/easy-asset:\/\//);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".markdown-preview img")).toHaveAttribute("src", /^data:image\/png;base64,/);
  expect(await page.locator(".markdown-preview script").count()).toBe(0);
  expect(await page.locator('.markdown-preview a[href^="javascript:"]').count()).toBe(0);
  expect(await page.evaluate(() => (window as any).__injected)).toBeUndefined();
  await page.getByRole("dialog", { name: "文章最终预览" }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "稿件已保存" })).toBeVisible();
  const saved = await page.evaluate(() => (window as any).__fixture.state.contents.find((item: any) => item.title === "文章工作流测试"));
  expect(saved.imageAssetIds).toHaveLength(1);
  expect(browserErrors).toEqual([]);
});

test("MCP-style article images are discovered from body references without imageAssetIds", async ({ page }) => {
  const externalRequests: string[] = [];
  page.on("request", (request) => { if (request.url().startsWith("https://example.test/")) externalRequests.push(request.url()); });
  await page.goto("/");
  const importedId = await page.evaluate(async () => {
    const asset = await window.easyPublish!.apiRequest<any>("POST", "/api/assets/import", { path: "/fixture/mcp-body-image.png", kind: "image" });
    await window.easyPublish!.apiRequest("POST", "/api/contents", {
      kind: "article", title: "MCP 正文图片", body: `![正文图片](easy-asset://${asset.id})\n\n![再次引用](easy-asset://${asset.id})\n\n![外部图片](https://example.test/remote.png)\n\n![非法标识](easy-asset://../../private)`,
      description: "", tags: [], author: "", videoAssetId: null, coverAssetId: null, imageAssetIds: [], platformOptions: {}, articleTemplateId: "clean",
    });
    return asset.id;
  });
  await page.locator(".draft-item:visible").filter({ hasText: "MCP 正文图片" }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator('.markdown-preview img[alt="正文图片"]')).toHaveAttribute("src", /^data:image\/png;base64,/);
  await expect(page.locator('.markdown-preview img[alt="再次引用"]')).toHaveAttribute("src", /^data:image\/png;base64,/);
  await expect(page.locator(".markdown-preview img")).toHaveCount(2);
  const result = await page.evaluate(() => ({
    content: (window as any).__fixture.state.contents.find((item: any) => item.title === "MCP 正文图片"),
    previews: (window as any).__fixture.calls.filter((call: any) => call.method === "GET" && call.path.endsWith("/preview")),
  }));
  expect(result.content.imageAssetIds).toEqual([]);
  expect([...new Set(result.previews.map((call: any) => call.path))]).toEqual([`/api/assets/${importedId}/preview`]);
  expect(externalRequests).toEqual([]);
});

test("preflight blocks errors, dynamic fields persist, enqueue retries reuse requestId", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("信息来源").selectOption("AI生成");
  await page.evaluate(() => (window as any).__fixture.blockPreflight(true));
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect(page.getByText("测试：平台要求添加封面。", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
  await page.evaluate(() => (window as any).__fixture.blockPreflight(false));
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect(page.getByText("发布前检查通过")).toBeVisible();
  await page.evaluate(() => (window as any).__fixture.failBatchOnce());
  await page.getByRole("button", { name: "发布到已开启账号" }).click();
  await expect(page.getByRole("alert")).toContainText("网络暂时中断");
  await page.getByRole("button", { name: "发布到已开启账号" }).click();
  await expect(page.getByRole("heading", { name: "发布任务", exact: false })).toBeVisible();
  const result = await page.evaluate(() => ({ batches: (window as any).__fixture.state.batches, calls: (window as any).__fixture.calls.filter((call: any) => call.path === "/api/batches"), content: (window as any).__fixture.state.contents[0] }));
  expect(result.batches).toHaveLength(1);
  expect(result.batches[0].tasks).toHaveLength(2);
  expect(result.calls[0].body.requestId).toBe(result.calls[1].body.requestId);
  expect(result.content.platformOptions.dayu.source).toBe("AI生成");
});

test("previous platform receipts default off and an explicit repeat creates a fresh batch", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    for (const account of state.accounts) account.defaultSelected = true;
    const original = state.accounts.find((item: any) => item.id === "a-zhihu");
    state.accounts.push({ ...original, id: "a-zhihu-second", label: "另一个知乎账号" }, { ...original, id: "a-xueqiu", platformId: "xueqiu", label: "结果待核对的雪球" });
    state.batches.push({ id: "b-past", contentId: "c-article", title: state.contents[0].title, kind: "article", mode: "publish", createdAt: "2026-11-05T10:00:00Z", tasks: [
      { id: "t-wechat", accountId: "a-wechat", accountLabel: "品牌公众号", platformId: "wechat", status: "published", message: "测试历史回执", canRetry: false },
      { id: "t-zhihu", accountId: "a-zhihu", accountLabel: "创作笔记", platformId: "zhihu", status: "submitted", message: "测试历史回执", canRetry: false },
      { id: "t-xueqiu", accountId: "a-xueqiu", accountLabel: "结果待核对的雪球", platformId: "xueqiu", status: "unknown", message: "测试历史回执", canRetry: false },
    ] });
  });
  await page.goto("/");
  const preview = (label: string) => page.locator(".article-target-summary > span").filter({ hasText: label });
  const history = (label: string) => page.locator(".article-history>div").filter({ hasText: label });
  await expect(preview("品牌公众号")).toHaveCount(0);
  await expect(history("品牌公众号")).toContainText("此前已发布");
  await expect(history("创作笔记")).toContainText("此前已提交");
  await expect(preview("另一个知乎账号")).toHaveCount(0);
  await expect(history("结果待核对的雪球")).toContainText("结果待确认");
  await expect(preview("创作工作室")).toBeVisible();
  await history("品牌公众号").getByRole("button").click();
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.getByRole("button", { name: "发布到已开启账号", exact: true }).click();
  await expect(page.getByRole("heading", { name: "发布任务", exact: false })).toBeVisible();
  await page.evaluate(() => { for (const task of (window as any).__fixture.state.batches[1].tasks) task.status = "submitted"; });
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await expect(history("创作工作室")).toContainText("此前已提交");
  await expect(preview("创作工作室")).toHaveCount(0);
  await expect(preview("品牌公众号")).toHaveCount(0);
  await history("品牌公众号").getByRole("button").click();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.getByRole("button", { name: "发布到已开启账号", exact: true }).click();
  const batches = await page.evaluate(() => (window as any).__fixture.state.batches);
  expect(batches).toHaveLength(3);
  expect(batches[1].contentId).toBe("c-article");
  expect(batches[2].contentId).toBe("c-article");
  expect(batches[1].requestId).not.toBe(batches[2].requestId);
  expect(batches[2].tasks.map((task: any) => task.platformId)).toEqual(["wechat"]);
});

test("republishing video selects compatible remaining platforms and ignores platform drafts", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    for (const account of state.accounts) account.defaultSelected = ["douyin", "kuaishou", "xiaohongshu", "channels", "bilibili"].includes(account.platformId);
    state.contents.push({ ...state.contents[0], id: "c-video-repeat", kind: "video", title: "继续分享这支视频", body: "" });
    state.batches.push(
      { id: "b-video-past", contentId: "c-video-repeat", kind: "video", mode: "publish", title: "继续分享这支视频", createdAt: "2026-11-05T10:00:00Z", tasks: [{ id: "t-video-past", accountId: "a-douyin", accountLabel: "品牌抖音", platformId: "douyin", status: "submitted", canRetry: false }] },
      { id: "b-platform-draft", contentId: "c-video-repeat", kind: "video", mode: "draft", title: "继续分享这支视频", createdAt: "2026-11-05T10:00:00Z", tasks: [{ id: "t-platform-draft", accountId: "a-kuaishou", accountLabel: "品牌快手", platformId: "kuaishou", status: "draft_created", canRetry: false }] },
    );
  });
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  const target = (label: string) => page.locator(".video-target-summary:visible").filter({ hasText: label });
  const repeat = page.locator(".video-history > div").filter({ hasText: "品牌抖音" });
  await expect(target("品牌抖音")).toHaveCount(0);
  for (const label of ["品牌快手", "品牌小红书", "品牌哔哩哔哩"]) await expect(target(label)).toBeVisible();
  await expect(target("品牌公众号")).toHaveCount(0);
  await expect(target("品牌视频号")).toHaveCount(0);
  await expect(repeat).toContainText("此前已提交");
  await page.getByRole("button", { name: "新建视频", exact: true }).click();
  await expect(page.locator(".video-target-summary:visible")).toHaveCount(4);
  await page.locator(".draft-item:visible").filter({ hasText: "继续分享这支视频" }).click();
  await expect(target("品牌快手")).toBeVisible();
  await repeat.getByRole("button", { name: "加入本次再次发布" }).click();
  await expect(target("品牌抖音")).toBeVisible();
  await target("品牌抖音").getByRole("button", { name: "取消再次发布" }).click();
  await expect(target("品牌抖音")).toHaveCount(0);
});

test("account templates persist and preview without changing the original article or calling AI", async ({ page }) => {
  await page.addInitScript(() => { (window as any).__fixture.state.accounts.find((item: any) => item.id === "a-zhihu").defaultSelected = true; });
  await page.goto("/");
  await page.getByRole("textbox", { name: "文章标题" }).fill("文章模板工作流");
  await page.getByRole("textbox", { name: "文章正文" }).fill("## 让内容更易读\n\n一份正文，按平台转换格式。");
  await page.getByRole("navigation").getByRole("button", { name: "账号管理", exact: true }).click();
  await page.getByRole("button", { name: "设置 品牌公众号 文章模板" }).click();
  const picker = page.getByRole("group", { name: "公众号文章模板", exact: true });
  await expect(picker.locator(".template-miniature")).toHaveCount(6);
  for (const name of ["清爽蓝", "墨色杂志", "暖色手记", "理性科技", "森林来信", "黑金画报"]) {
    const choice = picker.getByRole("button", { name: new RegExp(name) });
    await choice.click(); await expect(choice).toHaveAttribute("aria-pressed", "true");
  }
  await page.screenshot({ path: "test-results/account-template-modal.png", fullPage: true });
  await page.getByRole("button", { name: "保存模板", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "文章标题" })).toHaveValue("文章模板工作流");
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator('.markdown-preview [data-template="artdeco"]')).toBeVisible();
  await page.getByRole("tab", { name: /创作笔记/ }).click();
  await expect(page.locator(".preview-notes")).toContainText("原生字体和颜色");
  await expect(page.locator(".markdown-preview [data-template]")).toHaveCount(0);
  await page.getByRole("tab", { name: /品牌公众号/ }).click();
  await page.getByRole("dialog", { name: "文章最终预览" }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "稿件已保存" })).toBeVisible();
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.locator(".draft-item:visible").filter({ hasText: "文章模板工作流" }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator('.markdown-preview [data-template="artdeco"]')).toBeVisible();
  await page.screenshot({ path: "test-results/article-templates-1440.png", fullPage: true });
  await page.setViewportSize({ width: 1000, height: 950 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({ path: "test-results/article-templates-1000.png", fullPage: true });
  await page.getByRole("dialog", { name: "文章最终预览" }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.getByRole("button", { name: "发布到已开启账号", exact: true }).click();
  const result = await page.evaluate(() => ({ state: (window as any).__fixture.state, calls: (window as any).__fixture.calls }));
  expect(result.state.accounts.find((item: any) => item.id === "a-wechat").wechatTemplateId).toBe("artdeco");
  expect(result.state.contents.find((item: any) => item.title === "文章模板工作流").body).toContain("让内容更易读");
  expect(result.calls.filter((call: any) => /\/format$/.test(call.path))).toHaveLength(0);
  expect(result.calls.filter((call: any) => call.path === "/api/preview/article" && call.body.body.startsWith("# 灵感的形状"))).toHaveLength(6);
});

test("engine preview failures can retry and preview markup remains constrained", async ({ page }) => {
  await page.addInitScript(() => {
    const apiRequest = window.easyPublish!.apiRequest;
    (window as any).__previewFailure = true;
    window.easyPublish!.apiRequest = async (method, path, body) => {
      if (path !== "/api/preview/article") return apiRequest(method, path, body);
      if ((window as any).__previewFailure) throw { error: "测试：渲染暂时失败" };
      return { html: '<p style="color:rgb(7,8,9);display:inline-block;box-shadow:3px 3px 0 #d6c7a7;border-radius:12px;white-space:pre-wrap;text-decoration:underline;position:fixed;background-image:url(https://example.test/track.png)" onclick="window.__previewInjected=true">安全模板样式</p><script>window.__previewInjected=true</script><img src="https://example.test/track.png"><a href="javascript:alert(1)">不安全链接</a>', notes: [] } as any;
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("渲染暂时失败");
  await page.evaluate(() => { (window as any).__previewFailure = false; });
  await page.getByRole("button", { name: "重新生成预览", exact: true }).click();
  const text = page.locator(".markdown-preview p").filter({ hasText: "安全模板样式" });
  await expect(text).toHaveCSS("color", "rgb(7, 8, 9)");
  await expect(text).toHaveCSS("display", "inline-block");
  await expect(text).toHaveCSS("box-shadow", "rgb(214, 199, 167) 3px 3px 0px 0px");
  await expect(text).toHaveCSS("border-top-left-radius", "12px");
  await expect(text).toHaveCSS("white-space", "pre-wrap");
  await expect(text).toHaveCSS("text-decoration-line", "underline");
  await expect(text).not.toHaveAttribute("onclick");
  expect(await text.evaluate((node: HTMLElement) => [node.style.position, node.style.backgroundImage])).toEqual(["", ""]);
  await expect(page.locator('.markdown-preview script, .markdown-preview img, .markdown-preview a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__previewInjected)).toBeUndefined();
});

test("video import and platform draft submission target only video accounts", async ({ page }) => {
  await page.addInitScript((enabled) => { for (const account of (window as any).__fixture.state.accounts) account.defaultSelected = enabled.includes(account.platformId); }, ["wechat", "dayu", "douyin"]);
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await expect(page.locator('.video-target-summary:visible').filter({ hasText: "品牌公众号" })).toHaveCount(0);
  await page.getByRole("textbox", { name: "视频标题" }).fill("视频工作流测试");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/launch.mp4"));
  await page.getByRole("button", { name: "点击导入视频", exact: false }).click();
  await expect(page.getByRole("button", { name: /launch.mp4/ })).toBeVisible();
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/cover.png"));
  await page.getByRole("button", { name: "上传封面", exact: true }).click();
  await page.getByLabel("视频描述").fill("测试视频描述");
  await page.getByRole("textbox", { name: "话题标签" }).fill("创作");
  await page.getByRole("textbox", { name: "话题标签" }).press("Enter");
  await expect(page.locator('.video-target-summary:visible').filter({ hasText: "品牌抖音" })).toBeVisible();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.getByRole("button", { name: "发布到已开启账号", exact: true }).click();
  await expect(page.locator(".batch-title h2")).toHaveText("视频工作流测试");
  const content = await page.evaluate(() => (window as any).__fixture.state.contents.find((entry: any) => entry.title === "视频工作流测试"));
  expect(content.videoAssetId).toBeTruthy();
  expect(content.coverAssetId).toBeTruthy();
  expect(content.tags).toEqual(["创作"]);
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await page.getByRole("button", { name: "检查草稿内容" }).click();
  await page.getByRole("button", { name: "保存平台草稿" }).click();
  const batches = await page.evaluate(() => (window as any).__fixture.state.batches);
  expect(batches[1].mode).toBe("draft");
  expect(batches[1].tasks).toHaveLength(1);
});

test("accounts connect without echoing secrets and runtime can be prepared", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
  await page.getByRole("button", { name: "添加账号", exact: true }).click();
  const modal = page.getByRole("dialog");
  await modal.getByRole("button", { name: "微信公众号 文章" }).click();
  await modal.getByLabel("账号备注").fill("测试公众号二号");
  await modal.getByLabel("AppID", { exact: true }).fill("wx-test-not-real");
  await modal.getByLabel("AppSecret", { exact: true }).fill("test-secret-not-real");
  await modal.getByRole("button", { name: "添加账号", exact: true }).click();
  await expect(modal).not.toBeVisible();
  expect(await page.locator('input[type="password"]').count()).toBe(0);
  const card = page.locator(".account-card").filter({ hasText: "测试公众号二号" });
  await card.getByRole("button", { name: "检查连接", exact: true }).click();
  await expect(card.getByText("已连接", { exact: true })).toBeVisible();
  await page.evaluate(() => { (window as any).__fixture.state.runtime.browserReady = false; });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("button", { name: "下载并准备浏览器" })).toBeVisible();
  await page.getByRole("button", { name: "下载并准备浏览器" }).click();
  await expect(page.getByRole("button", { name: "浏览器已就绪" })).toBeDisabled();
});

for (const target of [{ id: "a-douyin", label: "品牌抖音", platform: "Douyin" }, { id: "a-kuaishou", label: "品牌快手", platform: "Kuaishou" }]) {
  test(`${target.platform} login progress stays unavailable until the engine reports ready`, async ({ page }) => {
    await page.addInitScript(({ id }) => {
      const fixture = (window as any).__fixture;
      const account = fixture.state.accounts.find((item: any) => item.id === id);
      account.status = "login_required";
      account.defaultSelected = true;
      account.message = "请连接测试账号";
      fixture.deferLogin(id);
    }, target);
    await page.goto("/");
    await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
    const selectedTarget = page.locator(".video-target-summary:visible").filter({ hasText: target.label });
    await expect(selectedTarget).toHaveCount(0);
    await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
    const card = page.locator(".account-card").filter({ hasText: target.label });
    await card.getByRole("button", { name: "登录账号", exact: true }).click();
    await expect(card.getByText("等待登录", { exact: true })).toBeVisible();
    await expect(card.getByRole("button", { name: "取消连接", exact: true })).toBeEnabled();
    await expect(card.getByRole("button", { name: `检查 ${target.label} 连接`, exact: true })).toBeDisabled();
    await expect(card).toContainText("等待扫码登录（测试进度）");
    await page.evaluate(({ id }) => (window as any).__fixture.loginProgress(id, "已扫码，正在验证账号（测试进度）"), target);
    await expect(card).toContainText("已扫码，正在验证账号（测试进度）");
    await expect(card.getByText("已连接", { exact: true })).toHaveCount(0);
    await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
    await expect(selectedTarget).toHaveCount(0);
    await page.evaluate(({ id }) => (window as any).__fixture.completeLogin(id), target);
    await expect(selectedTarget).toBeVisible();
    await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
    await expect(card.getByText("已连接", { exact: true })).toBeVisible();
    await expect(card.getByRole("button", { name: "重新连接", exact: true })).toBeEnabled();
    await expect(card.getByText("等待登录", { exact: true })).toHaveCount(0);
    const requests = await page.evaluate(({ id }) => (window as any).__fixture.calls.filter((call: any) => call.path === `/api/accounts/${id}/login`), target);
    expect(requests).toHaveLength(1);
  });
}

for (const operation of ["login", "check"] as const) {
  test(`account ${operation} can be cancelled without deleting data or reloading the page`, async ({ page }) => {
    await page.addInitScript(({ operation }) => {
      const fixture = (window as any).__fixture;
      const account = fixture.state.accounts.find((item: any) => item.id === "a-douyin");
      account.status = operation === "login" ? "login_required" : "ready";
      fixture.deferLogin(account.id);
      fixture.seedTasks();
    }, { operation });
    await page.goto("/");
    await page.evaluate(() => { (window as any).__connectionPageMarker = "preserve-this-page"; });
    await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
    const filter = page.getByRole("button", { name: "视频平台", exact: true });
    await filter.click();
    const card = page.locator(".account-card").filter({ hasText: "品牌抖音" });
    await expect(card.getByRole("button", { name: "取消连接", exact: true })).toHaveCount(0);
    const before = await page.evaluate(() => {
      const fixture = (window as any).__fixture;
      return { accounts: fixture.state.accounts.length, batches: fixture.state.batches };
    });
    await card.getByRole("button", { name: operation === "login" ? "登录账号" : "检查 品牌抖音 连接", exact: true }).click();
    await expect(card.getByText("等待登录", { exact: true })).toBeVisible();
    await card.getByRole("button", { name: "取消连接", exact: true }).click();
    await expect(card).toContainText("连接已取消");
    await expect(card.getByRole("button", { name: "取消连接", exact: true })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "登录账号", exact: true })).toBeEnabled();
    await expect(filter).toHaveClass("active");
    const after = await page.evaluate(() => {
      const fixture = (window as any).__fixture;
      return { marker: (window as any).__connectionPageMarker, accounts: fixture.state.accounts.length, batches: fixture.state.batches };
    });
    expect(after).toEqual({ marker: "preserve-this-page", ...before });
    await card.getByRole("button", { name: "登录账号", exact: true }).click();
    await expect(card.getByText("已连接", { exact: true })).toBeVisible();
    await expect(card.getByRole("button", { name: "取消连接", exact: true })).toHaveCount(0);
    const calls = await page.evaluate(() => (window as any).__fixture.calls
      .filter((call: any) => call.method !== "GET" && call.path.startsWith("/api/accounts/"))
      .map((call: any) => `${call.method} ${call.path}`));
    expect(calls).toEqual([
      `POST /api/accounts/a-douyin/${operation}`,
      "POST /api/accounts/a-douyin/cancel",
      "POST /api/accounts/a-douyin/login",
    ]);
  });
}

test("Kuaishou is a video-only browser account and joins multi-platform publishing", async ({ page }) => {
  await page.addInitScript((enabled) => { for (const account of (window as any).__fixture.state.accounts) account.defaultSelected = enabled.includes(account.platformId); }, ["wechat", "dayu", "xiaohongshu"]);
  await page.goto("/");
  await expect(page.locator(".article-target-summary").getByText("快手", { exact: true })).toHaveCount(0);
  await expect(page.locator(".video-target-summary:visible").filter({ hasText: "品牌快手" })).toHaveCount(0);
  await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
  await page.getByRole("button", { name: "文章平台", exact: true }).click();
  await expect(page.locator(".account-card").filter({ hasText: "品牌快手" })).toHaveCount(0);
  await page.getByRole("button", { name: "视频平台", exact: true }).click();
  await expect(page.locator(".account-card").filter({ hasText: "品牌快手" }).locator(".capability-tags")).toHaveText("视频");
  await page.getByRole("button", { name: "添加账号", exact: true }).click();
  const modal = page.getByRole("dialog");
  await modal.getByRole("button", { name: "快手 视频", exact: true }).click();
  await expect(modal.getByLabel("AppSecret", { exact: true })).toHaveCount(0);
  await modal.getByLabel("账号备注").fill("快手创作账号");
  await modal.getByRole("button", { name: "添加账号", exact: true }).click();
  await expect(modal).not.toBeVisible();
  const card = page.locator(".account-card").filter({ hasText: "快手创作账号" });
  await expect(card.locator(".platform-mark")).toHaveText("快");
  await card.getByRole("button", { name: "登录账号", exact: true }).click();
  await expect(card.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  const target = page.locator(".video-target-summary:visible").filter({ hasText: "快手创作账号" });
  await expect(target).toBeVisible();
  await expect(target.locator("small")).toContainText("快手");
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(target).toHaveCount(0);
  await page.getByRole("button", { name: "发布内容", exact: true }).click();
  await page.getByRole("textbox", { name: "视频标题" }).fill("快手批量发布测试");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/kuaishou-video.mp4"));
  await page.getByRole("button", { name: "点击导入视频", exact: false }).click();
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/kuaishou-cover.png"));
  await page.getByRole("button", { name: "上传封面", exact: true }).click();
  await page.getByLabel("视频描述").fill("同一支视频发布到多个平台");
  await page.getByRole("textbox", { name: "话题标签" }).fill("创作");
  await page.getByRole("textbox", { name: "话题标签" }).press("Enter");
  await expect(target).toBeVisible();
  await expect(page.locator(".video-target-summary:visible").filter({ hasText: "品牌小红书" })).toBeVisible();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(page.getByText("发布前检查通过")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "test-results/kuaishou-video-1440.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: "test-results/video-auto-accounts-1280.png", fullPage: true });
  await page.setViewportSize({ width: 1000, height: 950 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({ path: "test-results/kuaishou-video-1000.png", fullPage: true });
  await page.getByRole("button", { name: "发布到已开启账号", exact: true }).click();
  await expect(page.locator(".task-row").filter({ hasText: "快手创作账号" }).getByText("等待处理", { exact: true })).toBeVisible();
  const result = await page.evaluate(() => ({
    account: (window as any).__fixture.state.accounts.find((item: any) => item.label === "快手创作账号"),
    content: (window as any).__fixture.state.contents.find((item: any) => item.title === "快手批量发布测试"),
    batch: (window as any).__fixture.state.batches[0],
    preflight: (window as any).__fixture.calls.find((call: any) => call.path === "/api/preflight"),
    enqueue: (window as any).__fixture.calls.find((call: any) => call.path === "/api/batches"),
    create: (window as any).__fixture.calls.find((call: any) => call.path === "/api/accounts" && call.method === "POST"),
  }));
  expect(result.create.body).toEqual({ platformId: "kuaishou", label: "快手创作账号" });
  expect(result.content.videoAssetId).toBeTruthy();
  expect(result.content.coverAssetId).toBeTruthy();
  expect(result.content.tags).toEqual(["创作"]);
  expect(result.preflight.body).toEqual({ contentId: result.content.id, accountIds: ["a-xhs", result.account.id], mode: "publish" });
  expect(result.enqueue.body).toMatchObject(result.preflight.body);
  expect(result.batch.kind).toBe("video");
  expect(result.batch.tasks.map((task: any) => task.platformId)).toEqual(["xiaohongshu", "kuaishou"]);
});

test("Bilibili accounts connect as video-only targets", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".article-target-summary").getByText("哔哩哔哩", { exact: true })).toHaveCount(0);
  await expect(page.locator(".video-target-summary:visible").filter({ hasText: "品牌哔哩哔哩" })).toHaveCount(0);
  await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
  await page.getByRole("button", { name: "文章平台", exact: true }).click();
  await expect(page.locator(".account-card").filter({ hasText: "品牌哔哩哔哩" })).toHaveCount(0);
  await page.getByRole("button", { name: "视频平台", exact: true }).click();
  const existing = page.locator(".account-card").filter({ hasText: "品牌哔哩哔哩" });
  await expect(existing.locator(".capability-tags")).toHaveText("视频");
  await expect(existing.locator(".platform-mark")).toHaveText("哔");
  await page.getByRole("button", { name: "添加账号", exact: true }).click();
  const modal = page.getByRole("dialog");
  await modal.getByRole("button", { name: "哔哩哔哩 视频", exact: true }).click();
  await expect(modal.getByLabel("AppSecret", { exact: true })).toHaveCount(0);
  await modal.getByLabel("账号备注").fill("第二个 B 站账号");
  await modal.getByRole("button", { name: "添加账号", exact: true }).click();
  await expect(modal).not.toBeVisible();
  const added = page.locator(".account-card").filter({ hasText: "第二个 B 站账号" });
  await added.getByRole("button", { name: "登录账号", exact: true }).click();
  await expect(added.getByText("已连接", { exact: true })).toBeVisible();
  const accountCall = await page.evaluate(() => (window as any).__fixture.calls.find((call: any) => call.path === "/api/accounts" && call.method === "POST"));
  expect(accountCall.body).toEqual({ platformId: "bilibili", label: "第二个 B 站账号" });
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  const target = page.locator(".video-target-summary:visible").filter({ hasText: "第二个 B 站账号" });
  await expect(target).toBeVisible();
  await expect(target.locator("small")).toContainText("哔哩哔哩");
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(target).toHaveCount(0);
});

test("Bilibili video options survive reopening and reach a multi-platform batch", async ({ page }) => {
  await page.addInitScript((enabled) => { for (const account of (window as any).__fixture.state.accounts) account.defaultSelected = enabled.includes(account.platformId); }, ["bilibili", "xiaohongshu"]);
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await page.getByRole("textbox", { name: "视频标题" }).fill("B 站批量视频测试");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/bilibili-video.mp4"));
  await page.getByRole("button", { name: "点击导入视频", exact: false }).click();
  await page.getByRole("textbox", { name: "话题标签" }).fill("创作");
  await page.getByRole("textbox", { name: "话题标签" }).press("Enter");
  const target = page.locator(".video-target-summary:visible").filter({ hasText: "品牌哔哩哔哩" });
  await expect(target).toBeVisible();
  await expect(page.getByLabel("视频分区")).toHaveValue("");
  await page.getByLabel("视频分区").selectOption("101");
  await page.getByLabel("创作类型").selectOption("2");
  await page.getByLabel("转载来源").fill("https://example.test/original-video");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "稿件已保存" })).toBeVisible();
  await page.getByRole("button", { name: "新建视频", exact: true }).click();
  await page.locator(".draft-item:visible").filter({ hasText: "B 站批量视频测试" }).click();
  await expect(target).toBeVisible();
  await expect(page.locator(".video-target-summary:visible").filter({ hasText: "品牌小红书" })).toBeVisible();
  await expect(page.getByLabel("视频分区")).toHaveValue("101");
  await expect(page.getByLabel("创作类型")).toHaveValue("2");
  await expect(page.getByLabel("转载来源")).toHaveValue("https://example.test/original-video");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(page.getByText("发布前检查通过")).toBeVisible();
  await page.getByLabel("转载来源").fill("https://example.test/original-video-updated");
  await expect(page.getByRole("button", { name: "发布到已开启账号", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "test-results/bilibili-options-1440.png", fullPage: true });
  await page.setViewportSize({ width: 1000, height: 950 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({ path: "test-results/bilibili-options-1000.png", fullPage: true });
  await page.getByRole("button", { name: "发布到已开启账号", exact: true }).click();
  await expect(page.locator(".task-row").filter({ hasText: "品牌哔哩哔哩" }).getByText("等待处理", { exact: true })).toBeVisible();
  const result = await page.evaluate(() => ({
    batch: (window as any).__fixture.state.batches[0],
    content: (window as any).__fixture.state.contents.find((entry: any) => entry.title === "B 站批量视频测试"),
    preflights: (window as any).__fixture.calls.filter((call: any) => call.path === "/api/preflight"),
    enqueues: (window as any).__fixture.calls.filter((call: any) => call.path === "/api/batches"),
  }));
  expect(result.content.platformOptions.bilibili).toEqual({ tid: "101", copyright: "2", source: "https://example.test/original-video-updated" });
  expect(result.preflights).toHaveLength(2);
  expect(result.preflights[1].body).toEqual({ contentId: result.content.id, accountIds: ["a-xhs", "a-bilibili"], mode: "publish" });
  expect(result.enqueues[0].body).toMatchObject(result.preflights[1].body);
  expect(result.batch.kind).toBe("video");
  expect(result.batch.tasks.map((task: any) => task.platformId)).toEqual(["xiaohongshu", "bilibili"]);
});

test("Bilibili categories require verification and intersect across selected accounts", async ({ page }) => {
  await page.addInitScript((enabled) => { for (const account of (window as any).__fixture.state.accounts) account.defaultSelected = enabled.includes(account.platformId); }, ["bilibili"]);
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    const original = state.accounts.find((entry: any) => entry.id === "a-bilibili");
    state.accounts.push({ ...original, id: "a-bilibili-second", label: "分区不同的 B 站账号", defaultSelected: false, bilibiliCategories: [{ value: "102", label: "测试分区 / 创作" }, { value: "103", label: "测试分区 / 生活" }] });
  });
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await page.getByRole("textbox", { name: "视频标题" }).fill("分区更新测试");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/categories.mp4"));
  await page.getByRole("button", { name: "点击导入视频", exact: false }).click();
  await page.getByRole("textbox", { name: "话题标签" }).fill("创作");
  await page.getByRole("textbox", { name: "话题标签" }).press("Enter");
  await expect(page.locator(".video-target-summary:visible").filter({ hasText: "品牌哔哩哔哩" })).toBeVisible();
  const category = page.getByLabel("视频分区");
  await expect(category).toHaveValue("");
  await category.selectOption("101");
  await page.getByLabel("创作类型").selectOption("1");
  await page.getByRole("navigation").getByRole("button", { name: "账号管理", exact: true }).click();
  await page.getByRole("switch", { name: "分区不同的 B 站账号 发布时默认选中", exact: true }).check();
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await expect(category.locator("option")).toHaveText(["请选择视频分区", "测试分区 / 创作"]);
  await expect(category).toHaveValue("");
  await expect(page.getByText("原选分区不适用于当前账号，请重新选择。")).toBeVisible();
  await category.selectOption("102");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(page.getByRole("button", { name: "发布到已开启账号", exact: true })).toBeEnabled();
  await page.evaluate(() => { (window as any).__fixture.state.accounts.find((entry: any) => entry.id === "a-bilibili").bilibiliCategories = []; });
  await expect(category).toBeDisabled();
  await expect(page.getByText("连接或验证账号以获取分区。")).toBeVisible();
  await expect(page.getByRole("button", { name: "发布到已开启账号", exact: true })).toBeDisabled();
  await page.evaluate(() => { (window as any).__fixture.state.accounts.find((entry: any) => entry.id === "a-bilibili").bilibiliCategories = [{ value: "101", label: "测试分区 / 科普" }]; });
  await expect(page.getByText("已开启账号没有共同可用的视频分区，请在账号管理调整发布账号。")).toBeVisible();
  await expect(category).toBeDisabled();
  await page.getByRole("navigation").getByRole("button", { name: "账号管理" }).click();
  await page.getByRole("button", { name: "检查 品牌哔哩哔哩 连接", exact: true }).click();
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await expect(category).toBeEnabled();
  await expect(category.locator("option")).toHaveText(["请选择视频分区", "测试分区 / 创作"]);
  await expect(category).toHaveValue("102");
  await expect(page.getByRole("button", { name: "发布到已开启账号", exact: true })).toBeDisabled();
});

test("task receipts stay distinct and only known failures can retry", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => (window as any).__fixture.seedTasks());
  await page.getByRole("navigation").getByRole("button", { name: "发布任务" }).click();
  await expect(page.locator(".task-row")).toHaveCount(10);
  for (const label of ["等待处理", "处理中", "正在提交", "已存平台草稿", "已提交，待平台处理", "已发布", "需要处理", "失败", "已取消", "结果待确认"]) await expect(page.locator(".task-row .status-badge").getByText(label, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "重试", exact: true })).toHaveCount(2);
  await page.locator(".task-row").filter({ hasText: "测试 failed" }).getByRole("button", { name: "重试" }).click();
  await expect(page.locator(".task-row").filter({ hasText: "测试 failed" }).getByText("等待处理", { exact: true })).toBeVisible();
  await expect(page.locator(".task-row").filter({ hasText: "测试 unknown" }).getByRole("button", { name: "重试" })).toHaveCount(0);
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(page.locator(".task-row").filter({ hasText: "测试 submitting" }).getByText("结果待确认", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/tasks-desktop.png", fullPage: true });
});

test("article import, responsive workspace and truthful disconnected view", async ({ page, browser }) => {
  await page.goto("/");
  await page.screenshot({ path: "test-results/articles-1440.png", fullPage: true });
  await page.setViewportSize({ width: 1000, height: 950 });
  await page.screenshot({ path: "test-results/articles-1000.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole("button", { name: "新建文章" }).click();
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/imported.md"));
  await page.getByRole("button", { name: "导入文章文件" }).click();
  await expect(page.getByRole("textbox", { name: "文章正文" })).toHaveValue(/这是一篇导入的 Markdown/);
  await expect(page.getByRole("textbox", { name: "文章标题" })).toHaveValue("imported");
  const disconnected = await browser.newPage();
  await disconnected.goto("http://127.0.0.1:1420");
  await expect(disconnected.getByRole("heading", { name: "暂时无法连接发布引擎" })).toBeVisible();
  await expect(disconnected.getByText("请在 Easy Publish 桌面应用中使用。", { exact: false })).toBeVisible();
  await disconnected.close();
});

test("first launch with no accounts or drafts can create the first real local draft", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    state.accounts = [];
    state.contents = [];
    state.batches = [];
    state.runtime.browserReady = false;
  });
  await page.goto("/");
  await expect(page.getByRole("textbox", { name: "文章标题" })).toHaveValue("");
  await expect(page.getByRole("region", { name: "文章发布设置", exact: true }).getByText("暂无本次发布账号")).toBeVisible();
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
  await page.screenshot({ path: "test-results/first-launch-empty.png", fullPage: true });
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("请先填写文章标题");
  await page.getByRole("textbox", { name: "文章标题" }).fill("我的第一篇文章");
  await page.getByRole("textbox", { name: "文章正文" }).fill("从今天开始分享。");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "稿件已保存" })).toBeVisible();
  const state = await page.evaluate(() => (window as any).__fixture.state);
  expect(state.contents).toHaveLength(1);
  expect(state.contents[0].title).toBe("我的第一篇文章");
  expect(state.accounts).toHaveLength(0);
  expect(state.batches).toHaveLength(0);
});
