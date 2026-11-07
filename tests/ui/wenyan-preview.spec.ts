import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import path from "node:path";
import MarkdownIt from "markdown-it";

const themes = ["default", "orangeheart", "rainbow", "lapis", "pie", "maize", "purple", "phycat"];
const article = "# 认真写下每一天\n\n## 把灵感变成文章\n\n每一份创作，都值得被认真阅读。让**好内容**被更多人看见，也为自己的思考留下一份记录。\n\n> 留一点空白，给思考与想象。\n\n### 从一个小想法开始\n\n- 写下身边的新发现\n- 把故事讲清楚\n\n[参考资料](https://example.org/article)\n\n```js\nconst message = '你好，世界';\nconsole.log(message);\n```\n\n| 主题 | 记录 |\n| --- | --- |\n| 今天 | 一个新的开始 |";
let rendered: Record<string, string>;

test.beforeAll(() => {
  // Run the actual bundled official renderer. No model or publishing service.
  const result = spawnSync(process.execPath, [path.resolve("runtime/wenyan/dist/render.cjs"), "--stdio"], {
    input: themes.map((theme) => JSON.stringify({ body: article, theme })).join("\n") + "\n",
    encoding: "utf8", timeout: 30000, maxBuffer: 16 * 1024 * 1024,
  });
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  const outputs = result.stdout.trim().split("\n").map((line) => JSON.parse(line));
  expect(outputs).toHaveLength(themes.length);
  for (const output of outputs) expect(output.html).toContain('data-provider="WenYan"');
  rendered = Object.fromEntries(themes.map((theme, index) => [`wenyan-${theme}`, outputs[index].html]));
});

test("real Wenyan themes retain their inline typography in the final article preview", async ({ page }) => {
  await page.exposeFunction("__renderMarkdownFixture", (body: string) => new MarkdownIt("commonmark", { html: false }).render(body));
  await page.addInitScript({ path: path.resolve("tests/ui/bridge-fixture.js") });
  await page.addInitScript(({ rendered, themes, article }) => {
    const fixture = (window as any).__fixture;
    const account = fixture.state.accounts[0];
    fixture.state.accounts = themes.map((theme: string) => ({ ...account, id: theme, label: theme, wechatTemplateId: `wenyan-${theme}` }));
    fixture.state.contents[0].body = article;
    fixture.renderedPreviews = rendered;
  }, { rendered, themes, article });
  await page.goto("/");
  await page.getByRole("button", { name: "预览", exact: true }).click();
  for (const theme of themes) {
    await page.getByRole("tab", { name: new RegExp(`^${theme} `) }).click();
    const preview = page.locator('.markdown-preview [data-provider="WenYan"]');
    await expect(preview).toContainText("把灵感变成文章");
    await expect.soft.poll(() => preview.evaluate((element, original) => {
      const doc = new DOMParser().parseFromString(original, "text/html");
      const expected = [doc.querySelector('[data-provider="WenYan"]')!, ...doc.querySelectorAll('[data-provider="WenYan"] *')] as HTMLElement[];
      const actual = [element, ...element.querySelectorAll("*")] as HTMLElement[];
      if (actual.length !== expected.length) return [`elements ${actual.length} != ${expected.length}`];
      const lost: string[] = [];
      expected.forEach((before, index) => {
        for (const property of [...before.style]) {
          const after = actual[index].style.getPropertyValue(property);
          if (after !== before.style.getPropertyValue(property)) lost.push(`${before.tagName} ${property}: ${before.style.getPropertyValue(property)} -> ${after}`);
        }
      });
      return lost;
    }, rendered[`wenyan-${theme}`]), { message: theme }).toEqual([]);
    await expect(preview.locator("svg ellipse")).toHaveCount(3);
    await expect(preview.locator("svg ellipse").first()).toHaveAttribute("rx", /[1-9]/);
    await expect(preview.locator("svg ellipse").first()).toHaveAttribute("fill", /.+/);
    if (theme === "phycat") await page.screenshot({ path: "test-results/wenyan-phycat-preview.png", fullPage: true });
  }
  const calls = await page.evaluate(() => (window as any).__fixture.calls);
  expect(calls.some((call: any) => /\/format$/.test(call.path) || call.path === "/api/batches")).toBe(false);
  await page.setViewportSize({ width: 1000, height: 950 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({ path: "test-results/wenyan-phycat-preview-1000.png", fullPage: true });
});
