import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const target = (page: Page, label: string) => page.locator(".publish-panel:visible .target-card").filter({ hasText: label }).getByRole("checkbox");
const articleTarget = (page: Page, label: string) => page.locator(".article-preview-tabs:visible").getByRole("tab").filter({ hasText: label });
const repeatTarget = (page: Page, label: string) => page.locator(".article-history > div").filter({ hasText: label }).getByRole("button", { name: "加入本次再次发布", exact: true });
const navigate = (page: Page, label: string) => page.getByRole("navigation").getByRole("button", { name: label, exact: true }).click();

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
});

test("fresh drafts use saved account defaults, content kinds and supported publish modes", async ({ page }) => {
  await page.addInitScript(() => {
    const accounts = (window as any).__fixture.state.accounts;
    accounts.find((account: any) => account.id === "a-zhihu").defaultSelected = false;
    accounts.find((account: any) => account.id === "a-xhs").defaultSelected = false;
    // Older state responses have the same true default as migrated accounts.
    delete accounts.find((account: any) => account.id === "a-wechat").defaultSelected;
  });
  await page.goto("/");
  await expect(articleTarget(page, "品牌公众号")).toBeVisible();
  await expect(articleTarget(page, "创作工作室")).toBeVisible();
  await expect(articleTarget(page, "创作笔记")).toHaveCount(0);
  await expect(articleTarget(page, "品牌抖音")).toHaveCount(0);
  await expect(page.locator(".article-workspace").getByRole("checkbox")).toHaveCount(0);
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await expect(articleTarget(page, "品牌公众号")).toBeVisible();
  await expect(articleTarget(page, "创作笔记")).toHaveCount(0);
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(articleTarget(page, "品牌公众号")).toBeVisible();
  await expect(articleTarget(page, "创作工作室")).toHaveCount(0);
  await navigate(page, "视频");
  await expect(target(page, "品牌抖音")).toBeChecked();
  await expect(target(page, "品牌快手")).toBeChecked();
  await expect(target(page, "品牌哔哩哔哩")).toBeChecked();
  await expect(target(page, "品牌小红书")).not.toBeChecked();
  await expect(target(page, "品牌视频号")).not.toBeChecked();
  await expect(target(page, "品牌视频号")).toBeDisabled();
  await expect(target(page, "品牌公众号")).toHaveCount(0);
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(page.locator(".publish-panel:visible .target-card input:checked")).toHaveCount(0);
  await page.evaluate(() => {
    const account = (window as any).__fixture.state.accounts.find((item: any) => item.id === "a-channels");
    account.status = "ready";
  });
  await expect(target(page, "品牌视频号")).toBeChecked();
  await expect(target(page, "品牌抖音")).not.toBeChecked();
  await page.getByRole("button", { name: "发布内容", exact: true }).click();
  await expect(target(page, "品牌抖音")).toBeChecked();
  await expect(target(page, "品牌小红书")).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("account switches update article audience without losing drafts or manual video choices on polls", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "文章标题" }).fill("尚未保存的文章修改");
  await expect(articleTarget(page, "创作笔记")).toBeVisible();
  await navigate(page, "视频");
  await page.getByRole("textbox", { name: "视频标题" }).fill("尚未保存的视频修改");
  await target(page, "品牌快手").uncheck();
  await navigate(page, "账号管理");
  const articleSwitch = page.getByRole("switch", { name: "品牌公众号 发布时默认选中", exact: true });
  const videoSwitch = page.getByRole("switch", { name: "品牌抖音 发布时默认选中", exact: true });
  await expect(articleSwitch).toBeChecked();
  await articleSwitch.uncheck();
  await expect(articleSwitch).not.toBeChecked();
  await videoSwitch.uncheck();
  await expect(videoSwitch).not.toBeChecked();
  await navigate(page, "文章");
  await expect(page.getByRole("textbox", { name: "文章标题" })).toHaveValue("尚未保存的文章修改");
  await expect(articleTarget(page, "品牌公众号")).toHaveCount(0);
  await expect(articleTarget(page, "创作笔记")).toBeVisible();
  await expect(articleTarget(page, "创作工作室")).toBeVisible();
  const polls = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/state").length);
  await expect.poll(() => page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/state").length)).toBeGreaterThan(polls);
  await expect(articleTarget(page, "品牌公众号")).toHaveCount(0);
  await expect(articleTarget(page, "创作笔记")).toBeVisible();
  await navigate(page, "视频");
  await expect(page.getByRole("textbox", { name: "视频标题" })).toHaveValue("尚未保存的视频修改");
  await expect(target(page, "品牌抖音")).not.toBeChecked();
  await expect(target(page, "品牌快手")).not.toBeChecked();
  await expect(target(page, "品牌小红书")).toBeChecked();
  await navigate(page, "账号管理");
  await expect(articleSwitch).not.toBeChecked();
  await expect(videoSwitch).not.toBeChecked();
  await articleSwitch.check();
  await expect(articleSwitch).toBeChecked();
  await videoSwitch.check();
  await expect(videoSwitch).toBeChecked();
  await navigate(page, "文章");
  await expect(articleTarget(page, "品牌公众号")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "文章标题" })).toHaveValue("尚未保存的文章修改");
  await navigate(page, "视频");
  await expect(target(page, "品牌抖音")).toBeChecked();
  await expect(target(page, "品牌快手")).not.toBeChecked();
  const mutations = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.method !== "GET" && !(call.method === "POST" && call.path === "/api/preview/article")));
  expect(mutations.map((call: any) => [call.method, call.path, call.body])).toEqual([
    ["PUT", "/api/accounts/a-wechat/preferences", { defaultSelected: false }],
    ["PUT", "/api/accounts/a-douyin/preferences", { defaultSelected: false }],
    ["PUT", "/api/accounts/a-wechat/preferences", { defaultSelected: true }],
    ["PUT", "/api/accounts/a-douyin/preferences", { defaultSelected: true }],
  ]);
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("history requires explicit article repeats and only relevant account changes invalidate preflight", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    state.batches.push({ id: "prior", contentId: "c-article", title: "之前的文章发布", kind: "article", mode: "publish", createdAt: "2026-11-05T10:00:00Z", tasks: [
      { id: "done", accountId: "a-wechat", accountLabel: "品牌公众号", platformId: "wechat", status: "published", canRetry: false },
      { id: "unclear", accountId: "a-zhihu", accountLabel: "创作笔记", platformId: "zhihu", status: "unknown", canRetry: false },
    ] });
  });
  await page.goto("/");
  await expect(articleTarget(page, "品牌公众号")).toHaveCount(0);
  await expect(articleTarget(page, "创作笔记")).toHaveCount(0);
  await expect(articleTarget(page, "创作工作室")).toBeVisible();
  await expect(repeatTarget(page, "品牌公众号")).toBeVisible();
  await expect(repeatTarget(page, "创作笔记")).toBeVisible();
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  const publish = page.getByRole("button", { name: "发布到已开启账号", exact: true });
  await expect(publish).toBeEnabled();
  await navigate(page, "账号管理");
  await page.getByRole("switch", { name: "品牌抖音 发布时默认选中", exact: true }).uncheck();
  await navigate(page, "文章");
  await expect(publish).toBeEnabled();
  await expect(articleTarget(page, "品牌公众号")).toHaveCount(0);
  await repeatTarget(page, "品牌公众号").click();
  await expect(articleTarget(page, "品牌公众号")).toBeVisible();
  await expect(publish).toBeDisabled();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(publish).toBeEnabled();
  // A template change alters the final output and must require a fresh check.
  await navigate(page, "账号管理");
  await page.getByRole("button", { name: "设置 品牌公众号 文章模板", exact: true }).click();
  const templateDialog = page.getByRole("dialog", { name: "品牌公众号 · 文章模板", exact: true });
  await templateDialog.getByRole("button", { name: /墨色杂志/ }).click();
  await templateDialog.getByRole("button", { name: "保存模板", exact: true }).click();
  await expect(templateDialog).not.toBeVisible();
  await navigate(page, "文章");
  await expect(publish).toBeDisabled();
  await articleTarget(page, "品牌公众号").click();
  await expect(page.locator('.article-final-preview [data-template="editorial"]')).toBeVisible();
  await navigate(page, "账号管理");
  const preference = page.getByRole("switch", { name: "品牌公众号 发布时默认选中", exact: true });
  await preference.uncheck();
  await expect(preference).not.toBeChecked();
  await preference.check();
  await expect(preference).toBeChecked();
  await navigate(page, "文章");
  // Re-enabling a previously published platform does not silently opt into a repeat.
  await expect(articleTarget(page, "品牌公众号")).toHaveCount(0);
  await repeatTarget(page, "品牌公众号").click();
  await expect(articleTarget(page, "品牌公众号")).toBeVisible();
  await expect(articleTarget(page, "创作笔记")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(1);
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(publish).toBeEnabled();
  await publish.click();
  const requests = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/batches"));
  expect(requests).toHaveLength(1);
  expect(requests[0].body).toMatchObject({ contentId: "c-article", accountIds: ["a-wechat", "a-dayu"], mode: "publish" });
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(2);
});
