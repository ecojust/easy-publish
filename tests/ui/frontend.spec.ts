import { expect, test } from "@playwright/test";
import path from "node:path";

const fixturePath = path.resolve("tests/ui/bridge-fixture.js");

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: fixturePath });
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
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "稿件已保存" })).toBeVisible();
  const saved = await page.evaluate(() => (window as any).__fixture.state.contents.find((item: any) => item.title === "文章工作流测试"));
  expect(saved.imageAssetIds).toHaveLength(1);
  expect(browserErrors).toEqual([]);
});

test("preflight blocks errors, dynamic fields persist, enqueue retries reuse requestId", async ({ page }) => {
  await page.goto("/");
  await page.locator(".publish-panel:visible .target-card").filter({ hasText: "品牌公众号" }).click();
  await page.locator(".publish-panel:visible .target-card").filter({ hasText: "创作工作室" }).click();
  await page.getByLabel("创作来源").selectOption("AI生成");
  await page.evaluate(() => (window as any).__fixture.blockPreflight(true));
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect(page.getByText("测试：平台要求添加封面。", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "发布到所选账号" })).toBeDisabled();
  await page.evaluate(() => (window as any).__fixture.blockPreflight(false));
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect(page.getByText("发布前检查通过")).toBeVisible();
  await page.evaluate(() => (window as any).__fixture.failBatchOnce());
  await page.getByRole("button", { name: "发布到所选账号" }).click();
  await expect(page.getByRole("alert")).toContainText("网络暂时中断");
  await page.getByRole("button", { name: "发布到所选账号" }).click();
  await expect(page.getByRole("heading", { name: "发布任务", exact: false })).toBeVisible();
  const result = await page.evaluate(() => ({ batches: (window as any).__fixture.state.batches, calls: (window as any).__fixture.calls.filter((call: any) => call.path === "/api/batches"), content: (window as any).__fixture.state.contents[0] }));
  expect(result.batches).toHaveLength(1);
  expect(result.batches[0].tasks).toHaveLength(2);
  expect(result.calls[0].body.requestId).toBe(result.calls[1].body.requestId);
  expect(result.content.platformOptions.dayu.source).toBe("AI生成");
});

test("video import and platform draft submission target only video accounts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "视频", exact: true }).click();
  await expect(page.locator('.publish-panel:visible .target-card').filter({ hasText: "品牌公众号" })).toHaveCount(0);
  await page.getByRole("textbox", { name: "视频标题" }).fill("视频工作流测试");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/launch.mp4"));
  await page.getByRole("button", { name: "点击导入视频", exact: false }).click();
  await expect(page.getByRole("button", { name: /launch.mp4/ })).toBeVisible();
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/cover.png"));
  await page.getByRole("button", { name: "上传封面", exact: true }).click();
  await page.getByLabel("视频描述").fill("测试视频描述");
  await page.getByRole("textbox", { name: "话题标签" }).fill("创作");
  await page.getByRole("textbox", { name: "话题标签" }).press("Enter");
  await page.locator('.publish-panel:visible .target-card').filter({ hasText: "品牌抖音" }).click();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.getByRole("button", { name: "发布到所选账号", exact: true }).click();
  await expect(page.locator(".batch-title h2")).toHaveText("视频工作流测试");
  const content = await page.evaluate(() => (window as any).__fixture.state.contents.find((entry: any) => entry.title === "视频工作流测试"));
  expect(content.videoAssetId).toBeTruthy();
  expect(content.coverAssetId).toBeTruthy();
  expect(content.tags).toEqual(["创作"]);
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await page.locator('.publish-panel:visible .target-card').filter({ hasText: "品牌公众号" }).click();
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
  await page.locator(".publish-panel:visible .target-card").filter({ hasText: "品牌公众号" }).click();
  await page.locator(".publish-panel:visible .target-card").filter({ hasText: "创作笔记" }).click();
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
  await expect(page.locator(".publish-panel:visible").getByText("添加你的第一个账号")).toBeVisible();
  await expect(page.getByRole("button", { name: "发布到所选账号" })).toBeDisabled();
  await page.screenshot({ path: "test-results/first-launch-empty.png", fullPage: true });
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("请先填写内容标题");
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
