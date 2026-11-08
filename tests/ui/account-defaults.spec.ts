import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const target = (page: Page, label: string) => page.locator(".video-target-summary:visible").filter({ hasText: label });
const articleTarget = (page: Page, label: string) => page.locator(".article-target-summary:visible > span").filter({ hasText: label });
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
  await expect(target(page, "品牌抖音")).toBeVisible();
  await expect(target(page, "品牌快手")).toBeVisible();
  await expect(target(page, "品牌哔哩哔哩")).toBeVisible();
  await expect(target(page, "品牌小红书")).toHaveCount(0);
  await expect(target(page, "品牌视频号")).toHaveCount(0);
  await expect(target(page, "品牌公众号")).toHaveCount(0);
  await expect(page.locator(".video-workspace:visible").getByRole("checkbox")).toHaveCount(0);
  await expect(page.locator(".video-workspace:visible aside.publish-panel")).toHaveCount(0);
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await expect(page.locator(".video-target-summary:visible")).toHaveCount(0);
  await page.evaluate(() => {
    const account = (window as any).__fixture.state.accounts.find((item: any) => item.id === "a-channels");
    account.status = "ready";
  });
  await expect(target(page, "品牌视频号")).toBeVisible();
  await expect(target(page, "品牌抖音")).toHaveCount(0);
  await page.getByRole("button", { name: "发布内容", exact: true }).click();
  await expect(target(page, "品牌抖音")).toBeVisible();
  await expect(target(page, "品牌小红书")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("account switches update article audience without losing drafts across navigation and polls", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "文章标题" }).fill("尚未保存的文章修改");
  await expect(articleTarget(page, "创作笔记")).toBeVisible();
  await navigate(page, "视频");
  await page.getByRole("textbox", { name: "视频标题" }).fill("尚未保存的视频修改");
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
  await expect(target(page, "品牌抖音")).toHaveCount(0);
  await expect(target(page, "品牌快手")).toBeVisible();
  await expect(target(page, "品牌小红书")).toBeVisible();
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
  await expect(target(page, "品牌抖音")).toBeVisible();
  await expect(target(page, "品牌快手")).toBeVisible();
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
  const publish = page.getByRole("button", { name: "创建发布任务", exact: true });
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
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await page.getByRole("tab", { name: /品牌公众号/ }).click();
  await expect(page.locator('.article-final-preview [data-template="editorial"]')).toBeVisible();
  await page.getByRole("dialog", { name: "文章最终预览" }).getByRole("button", { name: "关闭", exact: true }).click();
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

for (const change of ["account", "categories", "video preferences"] as const) {
  test(`video preflight rejects late results after ${change} changes`, async ({ page }) => {
    await page.addInitScript(() => {
      const original = window.easyPublish!.apiRequest;
      window.easyPublish!.apiRequest = async (method, route, body) => {
        if (route === "/api/preflight") await new Promise<void>((resolve) => { (window as any).__releasePreflight = resolve; });
        return original(method, route, body);
      };
    });
    await page.goto("/");
    await navigate(page, "视频");
    await page.getByRole("textbox", { name: "视频标题" }).fill("视频检查期间更新账号");
    await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
    await expect.poll(() => page.evaluate(() => !!(window as any).__releasePreflight)).toBe(true);
    if (change === "account") {
      await navigate(page, "账号管理");
      await page.getByRole("switch", { name: "品牌抖音 发布时默认选中", exact: true }).uncheck();
      await navigate(page, "视频");
      await expect(target(page, "品牌抖音")).toHaveCount(0);
    } else if (change === "categories") {
      await navigate(page, "账号管理");
      await page.evaluate(() => { (window as any).__fixture.state.accounts.find((account: any) => account.id === "a-bilibili").bilibiliCategories = []; });
      await page.getByRole("button", { name: "设置 品牌哔哩哔哩 视频发布设置", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "品牌哔哩哔哩 · 视频发布设置", exact: true });
      await expect(dialog.getByText("尚未获取此账号的视频分区，请连接或检查此账号。", { exact: true })).toBeVisible();
      await expect(dialog.getByLabel("视频分区").locator("option")).toHaveText(["请选择视频分区", "101（需重新选择）"]);
      await dialog.getByRole("button", { name: "取消", exact: true }).click();
      await navigate(page, "视频");
    } else {
      await navigate(page, "账号管理");
      await page.getByRole("button", { name: "设置 品牌哔哩哔哩 视频发布设置", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "品牌哔哩哔哩 · 视频发布设置", exact: true });
      await dialog.getByLabel("视频分区").selectOption("102");
      await dialog.getByRole("button", { name: "保存视频设置", exact: true }).click();
      await navigate(page, "视频");
    }
    await expect(page.getByRole("textbox", { name: "视频标题" })).toHaveValue("视频检查期间更新账号");
    await expect(page.locator(".video-workspace:visible").getByLabel("视频分区")).toHaveCount(0);
    await page.evaluate(() => (window as any).__releasePreflight());
    await expect(page.getByRole("button", { name: "检查发布内容", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "创建发布任务", exact: true })).toBeDisabled();
    await expect(page.getByText("发布前检查通过", { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
  });
}

test("video repeat consent resets when an account is disabled and an identical copy needs its own check", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    for (const account of state.accounts) account.defaultSelected = account.platformId === "douyin";
    const video = { ...state.contents[0], id: "video-original", kind: "video", title: "相同的视频稿件", body: "" };
    state.contents = [video, { ...video, id: "video-copy" }];
    state.batches.push({ id: "past-video", contentId: "video-original", kind: "video", mode: "publish", title: video.title, createdAt: "2026-11-05T10:00:00Z", tasks: [{ id: "video-done", accountId: "a-douyin", accountLabel: "品牌抖音", platformId: "douyin", status: "unknown", canRetry: false }] });
  });
  await page.goto("/");
  await navigate(page, "视频");
  const repeat = page.locator(".video-history > div").filter({ hasText: "品牌抖音" }).getByRole("button", { name: "加入本次再次发布", exact: true });
  await expect(target(page, "品牌抖音")).toHaveCount(0);
  await repeat.click();
  await expect(target(page, "品牌抖音")).toBeVisible();
  await navigate(page, "账号管理");
  const toggle = page.getByRole("switch", { name: "品牌抖音 发布时默认选中", exact: true });
  await toggle.uncheck();
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await expect(toggle).toBeChecked();
  await navigate(page, "视频");
  await expect(target(page, "品牌抖音")).toHaveCount(0);
  await repeat.click();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  const publish = page.getByRole("button", { name: "创建发布任务", exact: true });
  await expect(publish).toBeEnabled();
  await page.locator(".draft-item:visible").nth(1).click();
  await expect(publish).toBeDisabled();
  await page.getByRole("button", { name: "检查发布内容", exact: true }).click();
  await publish.click();
  const batches = await page.evaluate(() => (window as any).__fixture.state.batches);
  expect(batches).toHaveLength(2);
  expect(batches[1].contentId).toBe("video-copy");
  expect(batches[1].tasks.map((task: any) => task.accountId)).toEqual(["a-douyin"]);
});
