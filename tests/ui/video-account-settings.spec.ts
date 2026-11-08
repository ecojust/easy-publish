import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const navigate = (page: Page, name: string) => page.getByRole("navigation").getByRole("button", { name, exact: true }).click();

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
});

test("old video fields remain saved while account settings control new submissions", async ({ page }) => {
  await page.addInitScript(() => {
    const fixture = (window as any).__fixture;
    for (const account of fixture.state.accounts) account.defaultSelected = account.platformId === "bilibili";
    const account = fixture.state.accounts.find((entry: any) => entry.id === "a-bilibili");
    account.videoOptions = { copyright: "1", tid: "101", source: "https://example.test/account-old-source" };
    fixture.state.contents = [{ ...fixture.state.contents[0], kind: "video", id: "old-video", title: "旧稿参数保留测试", body: "", platformOptions: { bilibili: { copyright: "2", tid: "102", source: "https://example.test/old-video-source" } } }];
  });
  await page.goto("/");
  await navigate(page, "视频");
  await expect(page.getByText("本次使用账号管理中的视频设置，旧稿设置已保留。", { exact: true })).toBeVisible();
  for (const label of ["创作类型", "视频分区", "转载来源"]) await expect(page.getByLabel(label)).toHaveCount(0);
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await page.getByRole("button", { name: "创建发布任务", exact: true }).click();
  const result = await page.evaluate(() => { const fixture = (window as any).__fixture; return { content: fixture.state.contents[0], snapshot: fixture.snapshots[fixture.state.batches[0].id] }; });
  expect(result.content.platformOptions.bilibili).toEqual({ copyright: "2", tid: "102", source: "https://example.test/old-video-source" });
  expect(result.content.videoOptionsSource).toBe("account");
  expect(result.snapshot.accountVideoOptions["a-bilibili"]).toEqual({ copyright: "1", tid: "101" });
  await navigate(page, "账号管理");
  await page.getByRole("button", { name: "设置 品牌哔哩哔哩 视频发布设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "品牌哔哩哔哩 · 视频发布设置", exact: true });
  await expect(dialog.getByLabel("转载来源")).toHaveCount(0);
  await dialog.getByLabel("创作类型").selectOption("2");
  await expect(dialog.getByLabel("转载来源")).toHaveValue("https://example.test/account-old-source");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect(await page.evaluate(() => (window as any).__fixture.state.accounts.find((entry: any) => entry.id === "a-bilibili").videoOptions.copyright)).toBe("1");
});

test("incomplete account preferences block publication and categories can be refreshed in account management", async ({ page }) => {
  await page.addInitScript(() => {
    const fixture = (window as any).__fixture;
    for (const account of fixture.state.accounts) account.defaultSelected = account.platformId === "bilibili";
    const account = fixture.state.accounts.find((entry: any) => entry.id === "a-bilibili");
    account.videoOptions = null;
    account.bilibiliCategories = [];
  });
  await page.goto("/");
  await navigate(page, "视频");
  await page.getByRole("textbox", { name: "视频标题" }).fill("暂时未保存的视频");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(page.getByRole("button", { name: "创建发布任务", exact: true })).toBeDisabled();
  await expect(page.locator(".video-workspace:visible .preflight-result")).toContainText("请到账号管理设置创作类型");
  await navigate(page, "账号管理");
  await expect(page.getByRole("button", { name: "设置 品牌公众号 视频发布设置", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "设置 品牌抖音 视频发布设置", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "设置 品牌哔哩哔哩 视频发布设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "品牌哔哩哔哩 · 视频发布设置", exact: true });
  await expect(dialog.getByLabel("创作类型")).toHaveValue("");
  await expect(dialog.getByLabel("视频分区").locator("option")).toHaveText(["请选择视频分区"]);
  await expect(dialog.getByText("尚未获取此账号的视频分区，请连接或检查此账号。", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "检查账号并刷新分区", exact: true }).click();
  await expect(dialog.getByLabel("视频分区").locator("option")).toHaveText(["请选择视频分区", "测试分区 / 科普", "测试分区 / 创作"]);
  await dialog.getByLabel("视频分区").selectOption("102");
  // Partial preferences are retained, but are not enough to publish.
  await dialog.getByRole("button", { name: "保存视频设置", exact: true }).click();
  await navigate(page, "视频");
  await expect(page.getByRole("textbox", { name: "视频标题" })).toHaveValue("暂时未保存的视频");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(page.getByRole("button", { name: "创建发布任务", exact: true })).toBeDisabled();
  await navigate(page, "账号管理");
  await page.getByRole("button", { name: "设置 品牌哔哩哔哩 视频发布设置", exact: true }).click();
  await expect(dialog.getByLabel("视频分区")).toHaveValue("102");
  await expect(dialog.getByLabel("创作类型")).toHaveValue("");
  await dialog.getByLabel("创作类型").selectOption("1");
  await dialog.getByRole("button", { name: "保存视频设置", exact: true }).click();
  await navigate(page, "视频");
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await expect(page.getByRole("button", { name: "创建发布任务", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});
