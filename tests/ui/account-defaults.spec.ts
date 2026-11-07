import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const target = (page: Page, label: string) => page.locator(".publish-panel:visible .target-card").filter({ hasText: label }).getByRole("checkbox");
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
  await expect(target(page, "品牌公众号")).toBeChecked();
  await expect(target(page, "创作工作室")).toBeChecked();
  await expect(target(page, "创作笔记")).not.toBeChecked();
  await expect(target(page, "品牌抖音")).toHaveCount(0);
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await expect(target(page, "品牌公众号")).toBeChecked();
  await expect(target(page, "创作笔记")).not.toBeChecked();
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(target(page, "品牌公众号")).toBeChecked();
  await expect(target(page, "创作工作室")).not.toBeChecked();
  await expect(target(page, "创作工作室")).toBeDisabled();
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

test("account switches update only their defaults without losing drafts or manual choices on polls", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "文章标题" }).fill("尚未保存的文章修改");
  await target(page, "创作笔记").uncheck();
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
  await expect(target(page, "品牌公众号")).not.toBeChecked();
  await expect(target(page, "创作笔记")).not.toBeChecked();
  await expect(target(page, "创作工作室")).toBeChecked();
  // Explicit selection can override a disabled default for this draft.
  await target(page, "品牌公众号").check();
  const polls = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/state").length);
  await expect.poll(() => page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/state").length)).toBeGreaterThan(polls);
  await expect(target(page, "品牌公众号")).toBeChecked();
  await expect(target(page, "创作笔记")).not.toBeChecked();
  await navigate(page, "视频");
  await expect(page.getByRole("textbox", { name: "视频标题" })).toHaveValue("尚未保存的视频修改");
  await expect(target(page, "品牌抖音")).not.toBeChecked();
  await expect(target(page, "品牌快手")).not.toBeChecked();
  await expect(target(page, "品牌小红书")).toBeChecked();
  await navigate(page, "账号管理");
  await expect(articleSwitch).not.toBeChecked();
  await expect(videoSwitch).not.toBeChecked();
  await videoSwitch.check();
  await expect(videoSwitch).toBeChecked();
  await navigate(page, "视频");
  await expect(target(page, "品牌抖音")).toBeChecked();
  await expect(target(page, "品牌快手")).not.toBeChecked();
  const mutations = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.method !== "GET"));
  expect(mutations.map((call: any) => [call.method, call.path, call.body])).toEqual([
    ["PUT", "/api/accounts/a-wechat/preferences", { defaultSelected: false }],
    ["PUT", "/api/accounts/a-douyin/preferences", { defaultSelected: false }],
    ["PUT", "/api/accounts/a-douyin/preferences", { defaultSelected: true }],
  ]);
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("publication history takes precedence over enabled defaults and preferences do not invalidate unchanged preflight", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    state.batches.push({ id: "prior", contentId: "c-article", kind: "article", mode: "publish", tasks: [
      { id: "done", accountId: "a-wechat", platformId: "wechat", status: "published" },
      { id: "unclear", accountId: "a-zhihu", platformId: "zhihu", status: "unknown" },
    ] });
  });
  await page.goto("/");
  await expect(target(page, "品牌公众号")).not.toBeChecked();
  await expect(target(page, "创作笔记")).not.toBeChecked();
  await expect(target(page, "创作工作室")).toBeChecked();
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  const publish = page.getByRole("button", { name: "发布到所选账号", exact: true });
  await expect(publish).toBeEnabled();
  await navigate(page, "账号管理");
  await page.getByRole("switch", { name: "品牌抖音 发布时默认选中", exact: true }).uncheck();
  await navigate(page, "文章");
  await expect(publish).toBeEnabled();
  await expect(target(page, "品牌公众号")).not.toBeChecked();
  await target(page, "品牌公众号").check();
  await expect(publish).toBeDisabled();
  await navigate(page, "账号管理");
  const preference = page.getByRole("switch", { name: "品牌公众号 发布时默认选中", exact: true });
  await preference.uncheck();
  await expect(preference).not.toBeChecked();
  await preference.check();
  await expect(preference).toBeChecked();
  await navigate(page, "文章");
  await expect(target(page, "品牌公众号")).not.toBeChecked();
  await target(page, "品牌公众号").check();
  await page.getByRole("button", { name: "选择尚未发布的平台", exact: true }).click();
  await expect(target(page, "品牌公众号")).not.toBeChecked();
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(1);
});
