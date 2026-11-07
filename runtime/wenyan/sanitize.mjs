import createDOMPurify from "dompurify";
import { JSDOM, VirtualConsole } from "jsdom";

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


export function sanitizeParsed(parsed) {
  const clean = purify.sanitize(parsed, {
    ALLOWED_TAGS: tags,
    ALLOWED_ATTR: ["href", "src", "alt", "title", "start", "align", "class"],
    ADD_URI_SAFE_ATTR: ["start", "align"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|easy-asset):|#)/i,
  });
  return clean;
}

export function sanitizeRoot(root) {
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
}

export function sanitizeStyled(styled) {
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
    if (!result) throw new Error("Article rendering did not produce a Wenyan document");
    result.setAttribute("data-provider", "WenYan");
    return result.outerHTML;
}
