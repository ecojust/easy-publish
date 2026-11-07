import { expect, test } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
});

test("plain articles retain one original editor with on-demand account previews and publish to enabled article accounts", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    state.accounts.push({ ...state.accounts[0], id: "second-wechat", label: "生活公众号", wechatTemplateId: "forest" });
    state.formatting.free = false;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章标题" }).fill("自然生活的记录");
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await editor.fill("每一天都有值得记下的细节。慢下来，看看窗外的树和阳光。");
  await page.getByPlaceholder("作者名称").fill("小林");
  await page.evaluate(() => (window as any).__fixture.pick("/fixture/life.png"));
  await page.getByRole("button", { name: "插入正文图片" }).click();
  await expect(editor).toHaveValue(/【图片1】/);
  const original = await editor.inputValue();
  await expect(page.locator('.article-workspace textarea:visible')).toHaveCount(1);
  await expect(page.locator('.article-workspace input[type="checkbox"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => /\/format$/.test(call.path)))).toHaveLength(0);
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "润色已完成" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/preview/article"))).toHaveLength(0);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".markdown-preview")).toContainText("整理后的文章");
  await expect(editor).toHaveValue(original);
  await expect(page.locator(".markdown-preview img")).toHaveAttribute("src", /^data:image\/png;base64,/);
  await page.getByRole("tab", { name: /生活公众号/ }).click();
  await expect(page.locator('.markdown-preview [data-template="forest"]')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => /\/format$/.test(call.path)))).toHaveLength(1);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "test-results/article-preview-modal-1280.png", fullPage: true });
  await page.setViewportSize({ width: 1000, height: 900 });
  await page.screenshot({ path: "test-results/article-preview-modal-1000.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole("tab", { name: /创作笔记/ }).click();
  await expect(page.locator(".markdown-preview [data-template]")).toHaveCount(0);
  await page.getByRole("dialog", { name: "文章最终预览" }).getByRole("button", { name: "关闭", exact: true }).click();
  const bounds = await Promise.all([editor.boundingBox(), page.getByRole('region', { name: '文章发布设置', exact: true }).boundingBox(), page.getByPlaceholder("作者名称").boundingBox()]);
  expect(bounds[1]!.y).toBeGreaterThan(bounds[0]!.y + bounds[0]!.height);
  expect(bounds[2]!.y).toBeLessThan(bounds[0]!.y);
  await page.getByLabel("信息来源").selectOption("无需标注");
  await page.getByRole("button", { name: "检查发布内容" }).click();
  await page.getByRole("button", { name: "发布到已开启账号" }).click();
  const state = await page.evaluate(() => (window as any).__fixture.state);
  const saved = state.contents.find((item: any) => item.title === "自然生活的记录");
  expect(saved.inputFormat).toBe("plain"); expect(saved.sourceText).toBe(original); expect(saved.body).toContain("easy-asset://");
  expect(state.batches[0].tasks.map((task: any) => task.accountId)).toEqual(["a-wechat", "a-zhihu", "a-dayu", "second-wechat"]);
});

test("failed and cancelled formatting preserve the original and never publish", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章标题" }).fill("保留原文");
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await editor.fill("这段原文必须在失败和取消时完整保留。");
  await page.evaluate(() => (window as any).__fixture.failFormatting(true));
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("原文已保留");
  await expect(editor).toHaveValue("这段原文必须在失败和取消时完整保留。");
  await page.evaluate(() => { (window as any).__fixture.failFormatting(false); (window as any).__fixture.deferFormatting(true); });
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await page.getByRole("button", { name: "取消生成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "整理已取消" })).toBeVisible();
  await expect(editor).toHaveValue("这段原文必须在失败和取消时完整保留。");
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("format responses cannot overwrite original edits made while a job runs", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章标题" }).fill("并发编辑");
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await editor.fill("整理请求中的原文。");
  await page.evaluate(() => (window as any).__fixture.deferFormatting(true));
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消生成", exact: true })).toBeVisible();
  await editor.fill("整理期间继续编辑的新原文。");
  await page.evaluate(() => { const f = (window as any).__fixture; f.finishFormatting(Object.keys(f.formatJobs)[0]); });
  await expect(page.getByRole("status").filter({ hasText: "原文有新的修改" })).toBeVisible();
  await expect(editor).toHaveValue("整理期间继续编辑的新原文。");
  await expect(page.locator(".markdown-preview")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
  await page.evaluate(() => (window as any).__fixture.deferFormatting(false));
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "润色已完成" })).toBeVisible();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".markdown-preview")).toContainText("整理期间继续编辑的新原文。");
});

test("format start retries retain the original request identity after response and lookup failures", async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.easyPublish!.apiRequest;
    let failStart = true; let failLookup = true;
    window.easyPublish!.apiRequest = async (method, route, body) => {
      if (method === "POST" && /\/format$/.test(route) && failStart) { failStart = false; await original(method, route, body); throw { error: "测试：启动响应丢失" }; }
      if (method === "GET" && route.startsWith("/api/format-jobs/") && failLookup) { failLookup = false; throw { error: "测试：状态暂不可取" }; }
      return original(method, route, body);
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章标题" }).fill("响应丢失恢复");
  await page.getByRole("textbox", { name: "文章正文" }).fill("同一次整理操作不应重复创建任务。");
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("启动响应丢失");
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "润色已完成" })).toBeVisible();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".markdown-preview")).toContainText("同一次整理操作");
  const calls = await page.evaluate(() => (window as any).__fixture.calls);
  const starts = calls.filter((call: any) => /\/format$/.test(call.path));
  expect(starts).toHaveLength(2); expect(starts[0].body).toEqual(starts[1].body);
  expect(calls.filter((call: any) => call.method === "PUT" && call.path.startsWith("/api/contents/"))).toHaveLength(0);
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
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
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
  const publish = page.getByRole("button", { name: "发布到已开启账号" });
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
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeEnabled();
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
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
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

test("plain article polishing and Wenyan preview do not require a connected article account", async ({ page }) => {
  await page.addInitScript(() => {
    const state = (window as any).__fixture.state;
    state.accounts = state.accounts.filter((account: any) => ["douyin", "bilibili"].includes(account.platformId));
  });
  await page.goto("/");
  await page.getByRole("button", { name: "新建文章", exact: true }).click();
  await page.getByRole("textbox", { name: "文章标题" }).fill("先写完，再连接账号");
  const original = "今天把一段想法写下来，先润色和预览，再决定发到哪里。";
  await page.getByRole("textbox", { name: "文章正文" }).fill(original);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "文章最终预览", exact: true });
  await expect(dialog).toContainText("请先润色生成");
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => /\/format$/.test(call.path) || call.path === "/api/preview/article"))).toHaveLength(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "润色已完成" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "文章正文" })).toHaveValue(original);
  await page.getByRole("button", { name: "预览", exact: true }).click();
  await expect(dialog.locator('.markdown-preview [data-template="wenyan-default"]')).toContainText(original);
  await expect(dialog.getByRole("tab")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "检查发布内容" })).toBeDisabled();
  const calls = await page.evaluate(() => (window as any).__fixture.calls);
  expect(calls.filter((call: any) => /\/format$/.test(call.path))).toHaveLength(1);
  expect(calls.filter((call: any) => call.path === "/api/batches")).toHaveLength(0);
  expect(calls.find((call: any) => call.path === "/api/preview/article").body).toMatchObject({ articleTemplateId: "wenyan-default", platformId: "wechat" });
});

test("Markdown drafts explicitly copy into plain polishing without losing the original or local images", async ({ page }) => {
  await page.goto("/");
  const originalId = "c-article";
  const imageId = "84a7aca4-212c-4ced-974b-3c035e715395";
  const markdown = `## 当前未保存的小标题\n\n**保留事实 2026**，还有[参考地址](https://example.test/source)。\n\n7. 保留编号\n8. 第二个步骤\n\n![正文图片](easy-asset://${imageId})`;
  const editor = page.getByRole("textbox", { name: "文章正文" });
  await editor.fill(markdown);
  await page.getByRole("button", { name: "复制为润色草稿", exact: true }).click();
  await expect(page.getByRole("button", { name: "润色生成", exact: true })).toBeVisible();
  await expect(editor).toHaveValue(/当前未保存的小标题/);
  const source = await editor.inputValue();
  expect(source).not.toContain("##"); expect(source).not.toContain("**");
  expect(source).toContain("保留事实 2026"); expect(source).toContain("https://example.test/source");
  expect(source).toContain("7. 保留编号"); expect(source).toContain("8. 第二个步骤");
  expect(source).toContain("【图片1】"); expect(source).not.toContain("easy-asset://");
  const beforeGeneration = await page.evaluate(() => (window as any).__fixture.state.contents);
  expect(beforeGeneration.find((item: any) => item.id === originalId)).toMatchObject({ inputFormat: "markdown", body: markdown });
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => /\/format$/.test(call.path)))).toHaveLength(0);
  await page.getByRole("button", { name: "润色生成", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "润色已完成" })).toBeVisible();
  const state = await page.evaluate(() => (window as any).__fixture.state);
  expect(state.contents.find((item: any) => item.id === originalId)).toMatchObject({ inputFormat: "markdown", body: markdown });
  const copy = state.contents.find((item: any) => item.inputFormat === "plain");
  expect(copy.id).not.toBe(originalId); expect(copy.sourceText).toBe(source);
  expect(copy.imageAssetIds).toEqual([imageId]); expect(copy.body).toContain(`easy-asset://${imageId}`);
  expect(state.batches).toHaveLength(0);
});
