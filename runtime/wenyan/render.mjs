// Build with build.mjs: the virtual import adds a raw-HTML escaping renderer
// to the pinned official parser, without replacing its Markdown/theme logic.
import { createWenyanCore } from "easy-publish:safe-wenyan";
import createDOMPurify from "dompurify";
import { JSDOM, VirtualConsole } from "jsdom";

const THEMES = new Set(["default", "orangeheart", "rainbow", "lapis", "pie", "maize", "purple", "phycat"]);
const MAX_INPUT = 4 * 1024 * 1024;
const MAX_BODY = 500_000;
const MAX_OUTPUT = 16 * 1024 * 1024;
const ERROR = "文章渲染失败，请重试。";
const MAX_LINE = MAX_INPUT;

// No user configuration, credentials, filesystem URLs, or remote resources.
// The official core entry is used, never its publishing/configuration wrapper.
globalThis.fetch = async () => { throw new Error("Remote resources are disabled"); };
const corePromise = createWenyanCore({ isWechat: true, isConvertMathJax: false, mermaid: false });
const purifierWindow = new JSDOM("", { virtualConsole: new VirtualConsole() }).window;
const purify = createDOMPurify(purifierWindow);
const tags = ["p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "s", "del", "blockquote", "ul", "ol", "li", "pre", "code", "span", "a", "img", "hr", "table", "thead", "tbody", "tr", "th", "td", "sup", "sub"];
const svgAttributes = ["version", "width", "height", "viewBox", "cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width", "d"];

function validateLayoutAttributes(root) {
  for (const element of root.querySelectorAll("[start], [align]")) {
    if (element.hasAttribute("start") && !/^-?\d{1,9}$/.test(element.getAttribute("start"))) element.removeAttribute("start");
    if (element.hasAttribute("align") && !/^(?:left|center|right)$/i.test(element.getAttribute("align"))) element.removeAttribute("align");
  }
  for (const element of root.querySelectorAll("svg, ellipse, g, path")) {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (["fill", "stroke"].includes(name)) {
        if (!/^(?:#[0-9a-f]{3,8}|rgba?\(\s*[\d.%]+(?:\s*,\s*[\d.%]+){2,3}\s*\)|none|currentcolor)$/i.test(value)) element.removeAttribute(attribute.name);
      } else if (name === "viewbox") {
        if (!/^-?\d+(?:\.\d+)?(?:[ ,]+-?\d+(?:\.\d+)?){3}$/.test(value)) element.removeAttribute(attribute.name);
      } else if (name === "d") {
        if (!/^[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]+$/.test(value)) element.removeAttribute(attribute.name);
      } else if (["width", "height", "cx", "cy", "rx", "ry", "stroke-width", "version"].includes(name)) {
        if (!/^\d+(?:\.\d+)?$/.test(value)) element.removeAttribute(attribute.name);
      } else if (["href", "src", "xlink:href"].includes(name)) element.removeAttribute(attribute.name);
    }
  }
}

async function render(value) {
  if (!value || Array.isArray(value) || typeof value !== "object" ||
      Object.keys(value).some((key) => !["body", "theme"].includes(key)) ||
      typeof value.body !== "string" || value.body.length > MAX_BODY || !THEMES.has(value.theme)) throw new Error(ERROR);
  if (!value.body.trim()) return { html: "" };
  const core = await corePromise;
  // The same parser and style application used by Wenyan CLI render.
  const parsed = await core.renderMarkdown(value.body);
  const clean = purify.sanitize(parsed, {
    ALLOWED_TAGS: tags,
    ALLOWED_ATTR: ["href", "src", "alt", "title", "start", "align", "class"],
    ADD_URI_SAFE_ATTR: ["start", "align"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|easy-asset):|#)/i,
  });
  const dom = new JSDOM(`<body><section id="wenyan">${clean}</section></body>`, { virtualConsole: new VirtualConsole() });
  try {
    const root = dom.window.document.getElementById("wenyan");
    validateLayoutAttributes(root);
    // Only syntax-highlighter classes survive into the official CSS engine.
    for (const element of root.querySelectorAll("[class]")) {
      const classes = [...element.classList].filter((name) => /^(?:hljs(?:-[a-z0-9_-]+)?|language-[a-z0-9_-]+)$/.test(name));
      if (classes.length) element.className = classes.join(" ");
      else element.removeAttribute("class");
    }
    for (const element of root.querySelectorAll("[src], [href]")) {
      for (const attribute of ["src", "href"]) {
        if (!element.hasAttribute(attribute)) continue;
        const uri = element.getAttribute(attribute);
        const allowed = attribute === "src" ? /^(?:https?:\/\/|easy-asset:\/\/)/i : /^(?:https?:\/\/|mailto:|#)/i;
        if (!allowed.test(uri)) element.removeAttribute(attribute);
      }
    }
    const styled = await core.applyStylesWithTheme(root, {
      themeId: value.theme,
      hlThemeId: "solarized-light",
      isMacStyle: true,
      isAddFootnote: true,
    });
    // Theme/post-processing can insert additional markup (including footnotes).
    // Sanitize the final result too, while retaining the official theme styles.
    const fragment = purify.sanitize(styled, {
      ALLOWED_TAGS: [...tags, "section", "div", "figure", "figcaption", "svg", "ellipse", "g", "path"],
      ALLOWED_ATTR: ["href", "src", "alt", "title", "start", "align", "class", "style", "id", "xmlns", ...svgAttributes],
      ADD_URI_SAFE_ATTR: ["start", "align", ...svgAttributes],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|easy-asset):|#)/i,
      RETURN_DOM_FRAGMENT: true,
    });
    validateLayoutAttributes(fragment);
    for (const element of fragment.querySelectorAll("[style]")) {
      for (const property of Array.from(element.style)) {
        const value = element.style.getPropertyValue(property);
        if (/url\s*\(|expression|@|\\|\/\*|<|>/i.test(value) || /^(?:behavior|-moz-binding)$/i.test(property)) element.style.removeProperty(property);
      }
    }
    const result = fragment.querySelector("section#wenyan");
    if (!result) throw new Error(ERROR);
    result.setAttribute("data-provider", "WenYan");
    const html = result.outerHTML;
    if (Buffer.byteLength(JSON.stringify({ html }), "utf8") + 1 > MAX_OUTPUT) throw new Error(ERROR);
    return { html };
  } finally {
    dom.window.close();
  }
}

async function respond(line) {
  try {
    if (Buffer.byteLength(line, "utf8") > MAX_INPUT) throw new Error(ERROR);
    return await render(JSON.parse(line));
  } catch {
    return { error: ERROR };
  }
}

async function main() {
  const streaming = process.argv.length === 3 && process.argv[2] === "--stdio";
  if (!streaming && process.argv.length !== 2) { process.stdout.write(JSON.stringify({ error: ERROR }) + "\n"); process.exitCode = 1; return; }
  let pending = "";
  let bytes = 0;
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    bytes += Buffer.byteLength(chunk, "utf8");
    pending += chunk;
    if ((!streaming && bytes > MAX_INPUT) || pending.length > MAX_LINE) throw new Error(ERROR);
    if (streaming) {
      let end;
      while ((end = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, end);
        pending = pending.slice(end + 1);
        process.stdout.write(JSON.stringify(await respond(line)) + "\n");
      }
    }
  }
  if (!streaming || pending) process.stdout.write(JSON.stringify(await respond(pending)) + "\n");
}

main().catch(() => { process.stdout.write(JSON.stringify({ error: ERROR }) + "\n"); process.exitCode = 1; }).finally(() => purifierWindow.close());
