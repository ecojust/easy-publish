import { expect, test } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
});

test("plain articles retain one original editor above per-account previews and publish to enabled article accounts", async ({ page }) => {
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
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
  await expect(page.locator(".markdown-preview")).toContainText("整理后的文章");
  await expect(editor).toHaveValue(original);
  await expect(page.locator(".markdown-preview img")).toHaveAttribute("src", /^data:image\/png;base64,/);
  await page.getByRole("tab", { name: /生活公众号/ }).click();
  await expect(page.locator('.markdown-preview [data-template="forest"]')).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "test-results/article-workspace-1280.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.getByRole("tab", { name: /创作笔记/ }).click();
  await expect(page.locator(".markdown-preview [data-template]")).toHaveCount(0);
  const bounds = await Promise.all([editor.boundingBox(), page.locator('.article-publishing').boundingBox(), page.getByPlaceholder("作者名称").boundingBox()]);
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
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("原文已保留");
  await expect(editor).toHaveValue("这段原文必须在失败和取消时完整保留。");
  await page.evaluate(() => { (window as any).__fixture.failFormatting(false); (window as any).__fixture.deferFormatting(true); });
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
  await page.getByRole("button", { name: "取消整理", exact: true }).click();
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
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消整理", exact: true })).toBeVisible();
  await editor.fill("整理期间继续编辑的新原文。");
  await page.evaluate(() => { const f = (window as any).__fixture; f.finishFormatting(Object.keys(f.formatJobs)[0]); });
  await expect(page.getByRole("status").filter({ hasText: "原文有新的修改" })).toBeVisible();
  await expect(editor).toHaveValue("整理期间继续编辑的新原文。");
  await expect(page.locator(".markdown-preview")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发布到已开启账号" })).toBeDisabled();
  await page.evaluate(() => (window as any).__fixture.deferFormatting(false));
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
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
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("启动响应丢失");
  await page.getByRole("button", { name: "整理并预览", exact: true }).click();
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
  await expect(page.locator('.markdown-preview [data-template="artdeco"]')).toBeVisible();
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
