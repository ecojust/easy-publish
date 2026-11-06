import { expect, test } from "@playwright/test";
import path from "node:path";

const fixturePath = path.resolve("tests/ui/bridge-fixture.js");

test.beforeEach(async ({ page }) => {
  await page.addInitScript({ path: fixturePath });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "设置", exact: true })).toBeVisible();
});

test("MCP settings expose native configuration and copy it without creating tasks", async ({ page }, testInfo) => {
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: async (value: string) => { (window as any).__copiedMcpConfiguration = value; },
    } });
  });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const text = page.getByRole("textbox", { name: "MCP 配置", exact: true });
  await expect(text).toBeVisible();
  await expect(text).toHaveJSProperty("readOnly", true);
  const value = await text.inputValue();
  const configuration = JSON.parse(value).mcpServers["easy-publish"];
  expect(configuration.command).toBe("/fixture/Easy Publish.app/Contents/MacOS/easy-publish-engine");
  expect(configuration.args).toEqual(["--mcp", "--data-dir", "/fixture/Application Support/easy-publish"]);
  expect(Object.keys(configuration).sort()).toEqual(["args", "command"]);
  await expect(page.getByText("使用时保持 Easy Publish 开启", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "复制 MCP 配置", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "MCP 配置已复制" })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__copiedMcpConfiguration)).toBe(value);
  expect(await page.evaluate(() => (window as any).__fixture.state.batches)).toHaveLength(0);
  await page.screenshot({ path: testInfo.outputPath("mcp-settings.png"), fullPage: true });
});

test("clipboard denial leaves the complete MCP configuration selected for manual copy", async ({ page }) => {
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: async () => { throw new Error("fixture clipboard unavailable"); },
    } });
  });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const text = page.getByRole("textbox", { name: "MCP 配置", exact: true });
  await expect(text).toBeVisible();
  await page.getByRole("button", { name: "复制 MCP 配置", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "自动复制不可用，配置已选中" })).toBeVisible();
  await expect(text).toBeFocused();
  const selection = await text.evaluate((element: HTMLTextAreaElement) => ({ start: element.selectionStart, end: element.selectionEnd, length: element.value.length }));
  expect(selection.start).toBe(0);
  expect(selection.end).toBe(selection.length);
});

test("MCP configuration failure can be retried independently of browser setup", async ({ page }) => {
  await page.evaluate(() => {
    const original = window.easyPublish!.mcpConfiguration;
    (window as any).__mcpFailure = true;
    window.easyPublish!.mcpConfiguration = async () => {
      if ((window as any).__mcpFailure) throw { error: "测试：MCP 配置暂时不可用" };
      return original();
    };
  });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("MCP 配置暂时不可用");
  await expect(page.getByRole("textbox", { name: "MCP 配置", exact: true })).toHaveCount(0);
  await page.evaluate(() => { (window as any).__mcpFailure = false; });
  await page.getByRole("button", { name: "重新生成 MCP 配置", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "MCP 配置", exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "发布浏览器", exact: true })).toBeVisible();
});
