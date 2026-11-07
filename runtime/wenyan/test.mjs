import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, cp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { createServer } from "node:http";
import { createWenyanCore } from "@wenyan-md/core";

const directory = path.dirname(fileURLToPath(import.meta.url));
const runtime = path.join(directory, "dist/render.cjs");
const themes = ["default", "orangeheart", "rainbow", "lapis", "pie", "maize", "purple", "phycat"];
const sample = "# 阅读的形状\n\n每份**创作**都值得被认真阅读。\n\n## 写成故事\n\n> 留白，给思考和想象。\n\n- 原文\n- 润色\n\n|阶段|结果|\n|---|---|\n|一|完成|\n\n```js\nconst answer = 42;\n```\n\n[来源](https://example.test/source)";
const svgShape = (node) => ({
  tag: node.localName,
  // This declaration has no references in the eight themes; active SVG
  // references remain forbidden in the application's static shape subset.
  attributes: Object.fromEntries([...node.attributes].filter((attr) => attr.name !== "xmlns:xlink").map((attr) => [attr.name, attr.value]).sort(([a], [b]) => a.localeCompare(b))),
  children: [...node.children].map(svgShape),
});

async function run(input, { entry = runtime, args = [], cwd = directory, chunks = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [entry, ...args], { cwd, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    const timeout = setTimeout(() => { child.kill(); reject(new Error("Renderer timed out")); }, 30_000);
    child.stdout.setEncoding("utf8").on("data", (value) => { stdout += value; });
    child.stderr.setEncoding("utf8").on("data", (value) => { stderr += value; });
    child.on("error", reject);
    child.on("close", (code) => { clearTimeout(timeout); resolve({ code, stdout, stderr }); });
    child.stdin.on("error", () => {});
    if (chunks) {
      for (let i = 0; i < input.length; i += 29) child.stdin.write(input.slice(i, i + 29));
      child.stdin.end();
    } else child.stdin.end(input);
  });
}

function parsed(html) { return new JSDOM(html).window.document; }
function assertSafe(document) {
  assert.equal(document.querySelector("script,style,link,iframe,object,embed,math,form,input,foreignObject,use,image,animate,set"), null);
  for (const node of document.querySelectorAll("*")) {
    for (const attr of node.attributes) {
      assert.ok(!/^on/i.test(attr.name), `Unexpected event attribute ${attr.name}`);
      if (["src", "href"].includes(attr.name)) assert.ok(!/^(?:javascript|data|file):/i.test(attr.value));
      if (["fill", "stroke"].includes(attr.name)) assert.doesNotMatch(attr.value, /url\s*\(|expression|@/i);
      if (attr.name === "style") assert.doesNotMatch(attr.value, /url\s*\(|expression|@import/i);
    }
  }
}

test("eight official themes render with Wenyan markup, highlighting and footnotes", async () => {
  const response = await run(themes.map((theme) => JSON.stringify({ body: sample, theme })).join("\n") + "\n", { args: ["--stdio"] });
  assert.equal(response.code, 0);
  assert.equal(response.stderr, "");
  const outputs = response.stdout.trim().split("\n").map((line) => JSON.parse(line));
  assert.equal(outputs.length, themes.length);
  const styles = new Set();
  const official = await createWenyanCore({ isWechat: true, isConvertMathJax: false, mermaid: false });
  for (const [index, { html }] of outputs.entries()) {
    const doc = parsed(html);
    const root = doc.querySelector('section#wenyan[data-provider="WenYan"]');
    assert.ok(root);
    assert.ok(doc.querySelector("pre code span[style]"), "Official syntax highlighting retained");
    assert.equal(doc.querySelectorAll("svg ellipse").length, 3, "Official static Mac-style code decoration retained");
    const decoration = doc.querySelector("ellipse").closest("svg");
    assert.equal(decoration.getAttribute("width"), "45");
    assert.equal(decoration.getAttribute("height"), "12");
    assert.equal(decoration.getAttribute("viewBox"), "0 0 450 130");
    const ellipses = [...decoration.querySelectorAll("ellipse")];
    assert.deepEqual(ellipses.map((ellipse) => ellipse.getAttribute("cx")), ["65", "225", "385"]);
    for (const ellipse of ellipses) {
      assert.equal(ellipse.getAttribute("cy"), "65");
      assert.equal(ellipse.getAttribute("rx"), "50");
      assert.equal(ellipse.getAttribute("ry"), "52");
      assert.match(ellipse.getAttribute("fill") || "", /^rgb\([\d, ]+\)$/);
      assert.match(ellipse.getAttribute("stroke") || "", /^rgb\([\d, ]+\)$/);
      assert.equal(ellipse.getAttribute("stroke-width"), "2");
    }
    if (themes[index] === "maize") {
      const marker = doc.querySelector("svg g path");
      assert.ok(marker?.getAttribute("d"));
      assert.equal(marker.getAttribute("fill"), "#ffb11b");
      assert.equal(marker.closest("svg").getAttribute("viewBox"), "0 0 100 100");
    }
    if (themes[index] === "phycat") {
      const marker = doc.querySelector("svg path");
      assert.ok(marker?.getAttribute("d"));
      assert.equal(marker.closest("svg").getAttribute("fill"), "rgba(74, 200, 141, 0.5)");
    }
    const originalDom = new JSDOM(`<section id="wenyan">${await official.renderMarkdown(sample)}</section>`);
    try {
      const original = originalDom.window.document.querySelector("#wenyan");
      await official.applyStylesWithTheme(original, { themeId: themes[index], hlThemeId: "solarized-light", isMacStyle: true, isAddFootnote: true });
      assert.deepEqual([...doc.querySelectorAll("svg")].map(svgShape), [...original.querySelectorAll("svg")].map(svgShape), `${themes[index]} preserves official SVG shapes, geometry, path data and colors`);
    } finally { originalDom.window.close(); }
    assert.ok(doc.querySelector("#footnotes"), "Official link footnotes retained");
    assert.ok(doc.querySelector("table"));
    styles.add(html);
    assertSafe(doc);
  }
  assert.equal(styles.size, themes.length);
});

test("raw HTML is text while code and source image placeholders survive", async () => {
  const asset = "easy-asset://12345678-1234-1234-1234-123456789abc";
  const body = `<h2 style="background:url(https://evil.test)">raw heading</h2>\n\n<script>sentinel()</script>\n\n<img src="https://evil.test/x" onerror="sentinel()">\n\n\`<script>code</script>\`\n\n\`\`\`html\n<img onerror="code()">\n\`\`\`\n\n![本地图片](${asset})\n\n[bad](javascript:alert(1))`;
  const response = await run(JSON.stringify({ body, theme: "phycat" }));
  assert.equal(response.code, 0);
  const doc = parsed(JSON.parse(response.stdout).html);
  assertSafe(doc);
  assert.equal(doc.querySelector("h2"), null, "User HTML headings must remain text");
  assert.ok(doc.body.textContent.includes('<h2 style="background:url(https://evil.test)">raw heading</h2>'));
  assert.ok(doc.querySelector("pre code").textContent.replace(/\u00a0/g, " ").includes('<img onerror="code()">'));
  assert.equal(doc.querySelector("img").getAttribute("src"), asset);
});

test("official footnotes preserve escaped link text without creating active elements", async () => {
  const body = "[&lt;script&gt;sentinel&lt;/script&gt;](https://example.test/source)\n\n[&lt;img src=x onerror=sentinel&gt;](https://example.test/other)\n\n[text](https://example.test/<img/src=x/onerror=sentinel>)";
  const response = await run(JSON.stringify({ body, theme: "default" }));
  const doc = parsed(JSON.parse(response.stdout).html);
  assertSafe(doc);
  assert.equal(doc.querySelector("img"), null);
  assert.ok(doc.querySelector("#footnotes").textContent.includes("<script>sentinel</script>"));
  assert.ok(doc.querySelector("#footnotes").textContent.includes("<img src=x onerror=sentinel>"));
});

test("ordered list starts and table alignment survive both sanitization passes", async () => {
  const body = "3. 第三项\n4. 第四项\n\n|左|中|右|\n|:---|:---:|---:|\n|甲|乙|丙|";
  const response = await run(JSON.stringify({ body, theme: "phycat" }));
  const doc = parsed(JSON.parse(response.stdout).html);
  assert.equal(doc.querySelector("ol").getAttribute("start"), "3");
  assert.deepEqual([...doc.querySelectorAll("th")].map((node) => node.getAttribute("align")), ["left", "center", "right"]);
  assert.deepEqual([...doc.querySelectorAll("td")].map((node) => node.getAttribute("align")), ["left", "center", "right"]);
  assertSafe(doc);
});

test("persistent JSONL handles split input, bad requests, and EOF", async () => {
  const lines = [JSON.stringify({ body: "正文", theme: "phycat" }), "invalid json", JSON.stringify({ body: "正文", theme: "../../custom.css" }), JSON.stringify({ body: "末段", theme: "lapis" })];
  const response = await run(lines.join("\n"), { args: ["--stdio"], chunks: true });
  assert.equal(response.code, 0);
  assert.equal(response.stderr, "");
  const result = response.stdout.trim().split("\n").map((line) => JSON.parse(line));
  assert.equal(result.length, 4);
  assert.ok(result[0].html && result[3].html);
  assert.deepEqual(result[1], { error: "文章渲染失败，请重试。" });
  assert.deepEqual(result[2], result[1]);
});

test("dist runs independently of node_modules and includes distribution notices", async () => {
  const temporary = await mkdtemp(path.join(tmpdir(), "easy-publish-wenyan-"));
  try {
    await cp(path.join(directory, "dist"), temporary, { recursive: true });
    const response = await run(JSON.stringify({ body: sample, theme: "rainbow" }), { entry: path.join(temporary, "render.cjs"), cwd: temporary });
    assert.equal(response.code, 0);
    assert.equal(response.stderr, "");
    assert.ok(JSON.parse(response.stdout).html.includes('data-provider="WenYan"'));
    const licenses = JSON.parse(await readFile(path.join(temporary, "THIRD_PARTY.json"), "utf8"));
    assert.ok(licenses.some((entry) => entry.name === "@wenyan-md/core" && entry.version === "3.0.12" && entry.licenseFiles.includes("LICENSE")));
    assert.ok(licenses.every((entry) => entry.licenseFiles.length > 0), "Every bundled package must retain its license text");
  } finally { await rm(temporary, { recursive: true, force: true }); }
});

test("image and link references never fetch resources", async () => {
  let requests = 0;
  const server = createServer((_request, response) => { requests++; response.end("unexpected"); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/resource`;
    const response = await run(JSON.stringify({ body: `![image](${url})\n\n[source](${url})`, theme: "orangeheart" }));
    assert.equal(response.code, 0);
    assert.ok(JSON.parse(response.stdout).html.includes(url));
    assert.equal(requests, 0);
  } finally { await new Promise((resolve) => server.close(resolve)); }
});

test("overlong body and custom styling fields are rejected without echoing content", async () => {
  const lines = [{ body: "x".repeat(500_001), theme: "default" }, { body: "private-source", theme: "default", themeCss: "private-css" }];
  const response = await run(lines.map((line) => JSON.stringify(line)).join("\n"), { args: ["--stdio"] });
  assert.equal(response.code, 0);
  assert.doesNotMatch(response.stdout + response.stderr, /private-source|private-css/);
  for (const line of response.stdout.trim().split("\n")) assert.deepEqual(JSON.parse(line), { error: "文章渲染失败，请重试。" });
});
