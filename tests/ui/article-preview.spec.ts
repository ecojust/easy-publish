import { expect, test } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
});

test("account changes during preflight invalidate delayed results and refresh preview without AI", async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.easyPublish!.apiRequest;
    window.easyPublish!.apiRequest = async (method, route, body) => {
      if (route === "/api/preflight") await new Promise<void>((resolve) => { (window as any).__releasePreflight = resolve; });
      return original(method, route, body);
    };
  });
  await page.goto("/");
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect.poll(() => page.evaluate(() => !!(window as any).__releasePreflight)).toBe(true);
  await page.getByRole("navigation").getByRole("button", { name: "账号管理", exact: true }).click();
  await page.getByRole("button", { name: "设置 品牌公众号 文章模板" }).click();
  await page.getByRole("group", { name: "公众号文章模板" }).getByRole("button", { name: /黑金画报/ }).click();
  await page.getByRole("button", { name: "保存模板", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator('.markdown-preview [data-template="artdeco"]')).toBeVisible();
  await page.getByRole("dialog", { name: "文章最终预览" }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.evaluate(() => (window as any).__releasePreflight());
  await expect(page.getByRole("alert")).toContainText("内容或发布账号已变化");
  await expect(page.getByRole("button", { name: "创建发布任务" })).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => /\/format$/.test(call.path) || call.path === "/api/batches"))).toHaveLength(0);
});

test("switching to an identical copied article invalidates the prior article preflight", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    const original = state.contents[0];
    state.contents = [original, { ...original, id: "identical-copy" }];
    for (const account of state.accounts) account.defaultSelected = account.platformId === "wechat";
  });
  await page.goto("/");
  await page.getByRole("button", { name: "检查发布内容" }).click();
  const publish = page.getByRole("button", { name: "创建发布任务" });
  await expect(publish).toBeEnabled();
  await page.locator('.article-library .draft-item').nth(1).click();
  await expect(publish).toBeDisabled();
  await expect(page.getByText("发布前检查通过", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await publish.click();
  expect(await page.evaluate(() => (window as any).__fixture.state.batches[0].contentId)).toBe("identical-copy");
});

test("account changes wait for a fresh state after an older poll before returning to article publishing", async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.easyPublish!.apiRequest;
    window.easyPublish!.apiRequest = async (method, route, body) => {
      const response = await original(method, route, body);
      if (route === "/api/state" && (window as any).__deferNextState) {
        (window as any).__deferNextState = false;
        await new Promise<void>((resolve) => { (window as any).__releaseState = resolve; });
      }
      return response;
    };
  });
  await page.goto("/");
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect(page.getByRole("button", { name: "创建发布任务" })).toBeEnabled();
  await page.getByRole("navigation").getByRole("button", { name: "账号管理", exact: true }).click();
  await page.evaluate(() => { (window as any).__deferNextState = true; });
  await expect.poll(() => page.evaluate(() => !!(window as any).__releaseState)).toBe(true);
  const toggle = page.getByRole("switch", { name: "品牌公众号 发布时默认选中", exact: true });
  await toggle.click();
  await expect.poll(() => page.evaluate(() => (window as any).__fixture.state.accounts[0].defaultSelected)).toBe(false);
  await page.evaluate(() => (window as any).__releaseState());
  await expect(toggle).not.toBeChecked();
  await page.getByRole("navigation").getByRole("button", { name: "文章", exact: true }).click();
  await expect(page.getByRole("tab", { name: /品牌公众号/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "创建发布任务" })).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("article preview is on demand, preserves unsaved text and closes with focus restored", async ({ page }) => {
  await page.goto("/");
  const preview = page.getByRole("button", { name: "预览", exact: true });
  const dialog = page.getByRole("dialog", { name: "文章最终预览", exact: true });
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".markdown-preview")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/preview/article"))).toHaveLength(0);
  await editor.fill("## 尚未保存的正文\n\n只预览，不保存也不生成。");
  await preview.click();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".markdown-preview")).toContainText("尚未保存的正文");
  await dialog.getByRole("tab", { name: /创作笔记/ }).click();
  await expect(dialog.locator(".markdown-preview [data-template]")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(preview).toBeFocused();
  await expect(editor).toHaveValue("## 尚未保存的正文\n\n只预览，不保存也不生成。");
  await expect(page.getByText("有未保存修改", { exact: true })).toBeVisible();
  await preview.click();
  await expect(dialog).toBeVisible();
  await page.locator(".modal-backdrop").click({ position: { x: 5, y: 5 } });
  await expect(dialog).toHaveCount(0);
  await expect(preview).toBeFocused();
  const mutations = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.method !== "GET" && call.path !== "/api/preview/article"));
  expect(mutations).toHaveLength(0);
});

test("disabled and previously published article accounts remain available for preview only", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    for (const account of state.accounts) account.defaultSelected = false;
    state.accounts[0].defaultSelected = true;
    state.batches.push({ id: "already-published", contentId: "c-article", kind: "article", title: "历史文章", mode: "publish", createdAt: "2026-11-05T10:00:00Z", tasks: [{ accountId: "a-wechat", platformId: "wechat", status: "published" }] });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "检查发布内容" })).toBeDisabled();
  await page.getByRole("button", { name: "平台草稿", exact: true }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "文章最终预览", exact: true });
  await expect(dialog.locator('.markdown-preview [data-template="clean"]')).toBeVisible();
  await dialog.getByRole("tab", { name: /创作笔记/ }).click();
  await expect(dialog.locator(".markdown-preview")).toContainText("少一点重复，多一点表达");
  await expect(dialog.locator(".markdown-preview [data-template]")).toHaveCount(0);
  await expect(dialog.getByRole("tab", { name: /品牌抖音/ })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "检查草稿内容" })).toBeDisabled();
  const calls = await page.evaluate(() => (window as any).__fixture.calls);
  expect(calls.filter((call: any) => /\/format$/.test(call.path) || call.path === "/api/batches")).toHaveLength(0);
  expect([...new Set(calls.filter((call: any) => call.path === "/api/preview/article").map((call: any) => call.body.platformId))]).toEqual(["wechat", "zhihu"]);
});


test("new Markdown articles preview immediately without saving or generating and target enabled article accounts", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章标题" }).fill("直接编辑 Markdown");
  const markdown = "## 第一节\n\n保留 **重点** 与[参考资料](https://example.test/source)。\n\n7. 第一项\n8. 第二项";
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await editor.fill(markdown);
  await expect(page.locator(".article-workspace textarea")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /润色生成|复制为润色草稿/ })).toHaveCount(0);
  await expect(page.locator(".markdown-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "文章最终预览", exact: true });
  await expect(dialog.locator(".markdown-preview h2")).toHaveText("第一节");
  await expect(dialog.locator(".markdown-preview strong")).toHaveText("重点");
  await expect(dialog.locator(".markdown-preview ol")).toHaveAttribute("start", "7");
  await dialog.getByRole("tab", { name: /创作笔记/ }).click();
  await expect(dialog.locator(".markdown-preview")).toContainText("第一项");
  await page.keyboard.press("Escape");
  await expect(editor).toHaveValue(markdown);
  const before = await page.evaluate(() => (window as any).__fixture.calls);
  expect(before.filter((call: any) => call.method !== "GET" && call.path !== "/api/preview/article")).toHaveLength(0);
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await expect(page.getByRole("button", { name: "创建发布任务" })).toBeEnabled();
  const after = await page.evaluate(() => (window as any).__fixture.calls);
  const saved = after.find((call: any) => call.method === "POST" && call.path === "/api/contents");
  expect(saved.body).toMatchObject({ inputFormat: "markdown", body: markdown, sourceText: "" });
  const preflight = after.find((call: any) => call.path === "/api/preflight");
  expect(preflight.body.accountIds).toEqual(["a-wechat", "a-zhihu", "a-dayu"]);
  expect(after.filter((call: any) => /\/format(?:ting|\b)|\/format-jobs/.test(call.path) || call.path === "/api/batches")).toHaveLength(0);
});

test("Markdown previews use the default Wenyan theme without any connected article account", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    state.accounts = state.accounts.filter((account: any) => ["douyin", "bilibili"].includes(account.platformId));
  });
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章正文" }).fill("## 先写完，再连接账号\n\n无需模型服务即可预览。");
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "文章最终预览", exact: true });
  await expect(dialog.locator('.markdown-preview [data-template="wenyan-default"]')).toContainText("先写完，再连接账号");
  await expect(dialog.getByRole("tab")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "检查发布内容" })).toBeDisabled();
  const calls = await page.evaluate(() => (window as any).__fixture.calls);
  expect(calls.find((call: any) => call.path === "/api/preview/article").body).toMatchObject({ articleTemplateId: "wenyan-default", platformId: "wechat" });
  expect(calls.filter((call: any) => call.method !== "GET" && call.path !== "/api/preview/article")).toHaveLength(0);
});

test("empty Markdown has a preview hint and does not invoke the renderer", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "文章最终预览" })).toContainText("写下 Markdown 正文");
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/preview/article"))).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "检查发布内容" })).toBeDisabled();
});

test("a failed preview can be reopened without losing unsaved Markdown", async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.easyPublish!.apiRequest;
    (window as any).__previewFailure = true;
    window.easyPublish!.apiRequest = async (method, path, body) => {
      if (path === "/api/preview/article" && (window as any).__previewFailure) throw { error: "测试：Wenyan 预览暂不可用，请重试。" };
      return original(method, path, body);
    };
  });
  await page.goto("/");
  const markdown = "## 未保存正文\n\n**保留我的修改**。";
  await page.getByRole("textbox", { name: "文章正文" }).fill(markdown);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "文章最终预览", exact: true });
  await expect(dialog.getByRole("alert").first()).toContainText("Wenyan 预览暂不可用");
  await page.keyboard.press("Escape");
  await page.evaluate(() => { (window as any).__previewFailure = false; });
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(dialog.locator(".markdown-preview")).toContainText("保留我的修改");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("textbox", { name: "文章正文" })).toHaveValue(markdown);
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.method !== "GET" && call.path !== "/api/preview/article"))).toHaveLength(0);
});

test("migrated original text is read-only while its Markdown can be edited and saved", async ({ page }) => {
  const original = "旧版原文，保留【图片1】和原来的措辞。";
  await page.addInitScript((original) => {
    const content = (window as any).__fixture.state.contents[0];
    content.sourceText = original;
    content.body = "## 已迁移的正文\n\n当前 Markdown 内容。";
  }, original);
  await page.goto("/");
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await expect(editor).toHaveValue("## 已迁移的正文\n\n当前 Markdown 内容。");
  await editor.fill("## 编辑后的正文\n\nMarkdown 修改只影响正文。");
  await page.getByRole("button", { name: "查看旧版原文", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "旧版原文", exact: true });
  await expect(dialog.getByRole("textbox", { name: "旧版原文", exact: true })).toHaveValue(original);
  await expect(dialog.getByRole("textbox", { name: "旧版原文", exact: true })).toHaveAttribute("readonly", "");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "稿件已保存" })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__fixture.state.contents[0])).toMatchObject({ inputFormat: "markdown", sourceText: original, body: "## 编辑后的正文\n\nMarkdown 修改只影响正文。" });
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看旧版原文", exact: true })).toHaveCount(0);
});

test("imported Markdown remains editable and local images insert Markdown syntax", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/文章.md"));
  await page.getByRole("button", { name: "导入文章文件", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await expect(editor).toHaveValue("# 导入文章\n\n这是一篇导入的 Markdown 稿件。");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/正文图片.png"));
  await page.getByRole("button", { name: "插入正文图片", exact: true }).click();
  await expect(editor).toHaveValue(/!\[正文图片\.png\]\(easy-asset:\/\//);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".markdown-preview img")).toHaveAttribute("src", /^data:image\/png;base64,/);
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => /\/format(?:ting|\b)|\/format-jobs/.test(call.path)))).toHaveLength(0);
});

test("settings have no model configuration and retain browser setup and MCP", async ({ page }) => {
  await page.addInitScript(() => { (window as any).__fixture.state.runtime.browserReady = false; });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "文章智能润色", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "检查免费模型", exact: true })).toHaveCount(0);
  await expect(page.getByText(/OpenCode|免费模型/)).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "MCP 配置", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "复制 MCP 配置", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "下载并准备浏览器", exact: true }).click();
  await expect(page.getByRole("button", { name: "浏览器已就绪", exact: true })).toBeDisabled();
  const calls = await page.evaluate(() => (window as any).__fixture.calls);
  expect(calls.some((call: any) => call.path === "/api/runtime/browser/install")).toBe(true);
  expect(calls.some((call: any) => /\/format(?:ting|\b)|\/format-jobs/.test(call.path))).toBe(false);
});
