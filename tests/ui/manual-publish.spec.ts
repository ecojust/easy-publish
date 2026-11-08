import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const navigate = (page: Page, label: string) => page.getByRole("navigation").getByRole("button", { name: label, exact: true }).click();

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
});

async function seedManualTask(page: Page) {
  await page.goto("/");
  await page.evaluate(() => {
    const fixture = (window as any).__fixture;
    fixture.seedTasks();
    const batch = fixture.state.batches[0];
    batch.title = "等待网页手动发布的文章";
    batch.tasks = batch.tasks.filter((task: any) => task.status === "awaiting_user");
    // An incidental editor URL must not invite opening a different browser.
    batch.tasks[0].url = "https://zhihu.example.test/editor/current";
    batch.tasks[0].message = "文章已填写，请在窗口中手动点击发布";
  });
  await page.getByRole("navigation").getByRole("button", { name: /发布任务/ }).click();
  await expect(page.getByText("等待手动发布", { exact: true })).toBeVisible();
}

test("manual publishing stays active and visible in attention without opening another browser", async ({ page }) => {
  await seedManualTask(page);
  const row = page.locator(".task-row");
  await expect(row.getByText(/请切换到已打开的发布窗口/)).toBeVisible();
  await expect(row.getByRole("button")).toHaveCount(0);
  await expect(page.getByText("0/1 已结束", { exact: true })).toBeVisible();
  await expect(page.locator(".nav-item").filter({ hasText: "发布任务" }).locator(".nav-count")).toHaveText("1");
  await page.getByRole("button", { name: "进行中 1", exact: true }).click();
  await expect(row).toHaveCount(1);
  await page.getByRole("button", { name: "需要处理 1", exact: true }).click();
  await expect(row).toHaveCount(1);
  await page.getByRole("button", { name: "已结束", exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("button", { name: "全部任务 1", exact: true }).click();
  await page.setViewportSize({ width: 1000, height: 950 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/manual-publish-awaiting-user.png", fullPage: true });
});

test("cancelling a manual publish wait warns about closing its window and keeps outcome unknown", async ({ page }) => {
  await seedManualTask(page);
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  const modal = page.getByRole("dialog", { name: "取消未完成的任务" });
  await expect(modal).toContainText("并关闭对应发布窗口");
  await modal.getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(page.locator(".task-row").getByText("结果待确认", { exact: true })).toBeVisible();
  await expect(page.locator(".task-row").getByRole("button", { name: "重试" })).toHaveCount(0);
  await expect(page.getByText("1/1 已结束", { exact: true })).toBeVisible();
  await expect(page.locator(".nav-item").filter({ hasText: "发布任务" }).locator(".nav-count")).toHaveCount(0);
});

test("article and video actions distinguish browser confirmation from direct submission", async ({ page }) => {
  await page.goto("/");
  const notice = page.locator(".publish-method-notice:visible");
  await expect(notice).toContainText("需在网页手动发布：知乎、大鱼号");
  await expect(notice).toContainText("微信公众号由应用直接提交，无需网页确认。");
  await expect(page.getByRole("button", { name: "创建发布任务", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(notice).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存平台草稿", exact: true })).toBeVisible();
  await navigate(page, "视频");
  await expect(notice).toContainText("需在网页手动发布：抖音、快手、小红书");
  await expect(notice).toContainText("哔哩哔哩由应用直接提交，无需网页确认。");
  await expect(notice.locator("strong")).not.toContainText("哔哩哔哩");
  await page.setViewportSize({ width: 1000, height: 950 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("direct-only target list does not request manual webpage publication", async ({ page }) => {
  await page.addInitScript(() => {
    for (const account of (window as any).__fixture.state.accounts) account.defaultSelected = ["wechat", "bilibili"].includes(account.platformId);
  });
  await page.goto("/");
  await expect(page.locator(".publish-method-notice:visible")).toHaveText("微信公众号由应用直接提交，无需网页确认。");
  await navigate(page, "视频");
  await expect(page.locator(".publish-method-notice:visible")).toHaveText("哔哩哔哩由应用直接提交，无需网页确认。");
});
