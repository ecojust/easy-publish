import { expect, test } from "@playwright/test";
import path from "node:path";
import MarkdownIt from "markdown-it";

const fixturePath = path.resolve("tests/ui/bridge-fixture.js");

test.beforeEach(async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false, breaks: true }).render(body));
  await page.addInitScript({ path: fixturePath });
});

test("formatting settings check the free model only on request and keep other settings usable", async ({ page }) => {
  await page.addInitScript(() => {
    const fixture = (window as any).__fixture;
    fixture.state.runtime.browserReady = false;
    fixture.state.formatting = {
      available: true, version: "1.2.3-fixture", modelId: "fixture/free", modelName: "测试免费模型",
      free: false, message: "测试：首次使用时检查免费模型。",
    };
    const original = window.easyPublish!.apiRequest;
    window.easyPublish!.apiRequest = async <T,>(method: string, route: string, body?: unknown) => {
      if (route === "/api/formatting/check") {
        await new Promise<void>((resolve) => { (window as any).__finishFormattingCheck = resolve; });
      }
      return original<T>(method, route, body);
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const section = page.getByRole("region", { name: "文章智能整理", exact: true });
  await expect(section).toContainText("待检查免费模型");
  await expect(section).toContainText("内置 OpenCode v1.2.3-fixture");
  await expect(section).toContainText("模型：测试免费模型");
  await expect(section).toContainText("不会切换到收费模型");
  await expect(section).toContainText("检查仅查询服务与免费模型目录");
  await expect(section).toContainText("将原文发送给免费模型服务");
  await expect(section.getByRole("textbox")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/formatting/check"))).toHaveLength(0);
  await section.getByRole("button", { name: "检查免费模型", exact: true }).click();
  await expect(section.getByRole("button", { name: "正在检查免费模型…", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "复制 MCP 配置", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "下载并准备浏览器", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "下载并准备浏览器", exact: true }).click();
  await expect(page.getByRole("button", { name: "浏览器已就绪", exact: true })).toBeDisabled();
  await page.evaluate(() => (window as any).__finishFormattingCheck());
  await expect(section).toContainText("免费模型目录已确认");
  await expect(section.getByRole("button", { name: "检查免费模型", exact: true })).toBeEnabled();
  const calls = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/formatting/check"));
  expect(calls).toHaveLength(1);
  expect(calls[0].method).toBe("POST");
  expect(await page.evaluate(() => (window as any).__fixture.calls.some((call: any) => (call.path.includes("/format-jobs") || call.path.endsWith("/format"))))).toBe(false);
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
});

test("missing bundled formatting engine shows its status without asking for credentials", async ({ page }) => {
  await page.addInitScript(() => {
    const fixture = (window as any).__fixture;
    fixture.state.formatting = {
      available: false, version: null, modelId: null, modelName: null, free: false,
      message: "测试：未找到内置排版引擎，请安装完整版本。",
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const section = page.getByRole("region", { name: "文章智能整理", exact: true });
  await expect(section).toContainText("内置引擎未就绪");
  await expect(section).toContainText("未找到内置排版引擎，请安装完整版本");
  await expect(section).toContainText("模型：尚未确认");
  await expect(section.getByRole("button", { name: "检查免费模型", exact: true })).toBeDisabled();
  await expect(section.getByRole("textbox")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "MCP 配置", exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/formatting/check"))).toHaveLength(0);
});

test("free model check failure stays in its section and can be retried", async ({ page }) => {
  await page.addInitScript(() => {
    const fixture = (window as any).__fixture;
    fixture.state.runtime.browserReady = false;
    fixture.state.formatting.free = false;
    const original = window.easyPublish!.apiRequest;
    (window as any).__formattingCheckAttempts = 0;
    window.easyPublish!.apiRequest = async <T,>(method: string, route: string, body?: unknown) => {
      if (route === "/api/formatting/check") {
        (window as any).__formattingCheckAttempts += 1;
        if ((window as any).__formattingCheckAttempts === 1) throw { error: "测试：免费模型目录暂时不可用，请重试。" };
      }
      return original<T>(method, route, body);
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const section = page.getByRole("region", { name: "文章智能整理", exact: true });
  await section.getByRole("button", { name: "检查免费模型", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("免费模型目录暂时不可用");
  await expect(section.getByRole("button", { name: "检查免费模型", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "复制 MCP 配置", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "下载并准备浏览器", exact: true })).toBeEnabled();
  await section.getByRole("button", { name: "检查免费模型", exact: true }).click();
  await expect(section.getByRole("alert")).toHaveCount(0);
  await expect(section).toContainText("免费模型目录已确认");
  expect(await page.evaluate(() => (window as any).__formattingCheckAttempts)).toBe(2);
  expect(await page.evaluate(() => (window as any).__fixture.calls.some((call: any) => (call.path.includes("/format-jobs") || call.path.endsWith("/format"))))).toBe(false);
});

test("a busy check response stays visible when refreshed global state has a different message", async ({ page }) => {
  await page.addInitScript(() => {
    const fixture = (window as any).__fixture;
    fixture.state.formatting.free = false;
    fixture.state.formatting.message = "测试：尚未查询免费模型目录。";
    const original = window.easyPublish!.apiRequest;
    (window as any).__busyCheckRequested = false;
    (window as any).__stateReadsAfterBusyCheck = 0;
    window.easyPublish!.apiRequest = async <T,>(method: string, route: string, body?: unknown) => {
      if (route === "/api/formatting/check") {
        fixture.calls.push({ method, path: route });
        (window as any).__busyCheckRequested = true;
        // This action result intentionally does not mutate /api/state.
        return { ...fixture.state.formatting, message: "正在整理文章，完成后可检查免费模型。" } as T;
      }
      if (route === "/api/state" && (window as any).__busyCheckRequested) (window as any).__stateReadsAfterBusyCheck += 1;
      return original<T>(method, route, body);
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const section = page.getByRole("region", { name: "文章智能整理", exact: true });
  await section.getByRole("button", { name: "检查免费模型", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__stateReadsAfterBusyCheck)).toBeGreaterThan(0);
  await expect(section.getByRole("status")).toContainText("最近检查：正在整理文章，完成后可检查免费模型。");
  await expect(section).toContainText("测试：尚未查询免费模型目录。");
  await expect(section).toContainText("待检查免费模型");
  await expect(section.getByRole("button", { name: "检查免费模型", exact: true })).toBeEnabled();
  // Later state polling must not erase the transient response either.
  await page.evaluate(() => { (window as any).__fixture.state.formatting.message = "测试：后台状态已刷新。"; });
  await expect(section).toContainText("测试：后台状态已刷新。");
  await expect(section.getByRole("status")).toContainText("正在整理文章，完成后可检查免费模型。");
  const calls = await page.evaluate(() => (window as any).__fixture.calls.filter((call: any) => call.path === "/api/formatting/check"));
  expect(calls).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).__fixture.calls.some((call: any) => call.path.endsWith("/format")))).toBe(false);
});
