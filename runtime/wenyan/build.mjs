import { build } from "esbuild";
import { readFile, writeFile, mkdir, readdir, copyFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const directory = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const output = path.join(directory, "dist");
const coreDirectory = path.join(directory, "node_modules/@wenyan-md/core");
const corePackage = JSON.parse(await readFile(path.join(coreDirectory, "package.json"), "utf8"));
if (corePackage.version !== "3.0.12") throw new Error("Wenyan version does not match the pinned runtime");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
const result = await build({
  absWorkingDir: directory,
  entryPoints: ["render.mjs"],
  outfile: path.join(output, "render.cjs"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  define: { "import.meta.url": "__ep_import_meta_url" },
  banner: { js: 'var __ep_import_meta_url = require("node:url").pathToFileURL(__filename).href;' },
  external: ["./xhr-sync-worker.js"],
  minify: true,
  legalComments: "linked",
  metafile: true,
  plugins: [{
    name: "offline-wenyan",
    setup(context) {
      // css-tree's ESM entry uses createRequire for JSON data. Its official CJS
      // entry exposes the same API while allowing esbuild to include that data.
      context.onResolve({ filter: /^css-tree$/ }, () => ({ path: require.resolve("css-tree") }));
      context.onLoad({ filter: /jsdom[\\/]lib[\\/]jsdom[\\/]living[\\/]helpers[\\/]style-rules\.js$/ }, async ({ path: filename }) => {
        const source = await readFile(filename, "utf8");
        const marker = /fs\.readFileSync\(\s*path\.resolve\(__dirname, "\.\.\/\.\.\/browser\/default-stylesheet\.css"\),\s*\{ encoding: "utf-8" \}\s*\)/;
        if (!marker.test(source)) throw new Error("Review JSDOM stylesheet packaging before building");
        const css = await readFile(path.resolve(path.dirname(filename), "../../browser/default-stylesheet.css"), "utf8");
        return { contents: source.replace(marker, JSON.stringify(css)), resolveDir: path.dirname(filename), loader: "js" };
      });
      context.onResolve({ filter: /^easy-publish:safe-wenyan$/ }, () => ({ path: path.join(coreDirectory, "dist/core.js"), namespace: "safe-wenyan" }));
      context.onLoad({ filter: /.*/, namespace: "safe-wenyan" }, async ({ path: filename }) => {
        let source = await readFile(filename, "utf8");
        const marker = "const md = new Marked();";
        if (source.split(marker).length !== 2) throw new Error("The official Markdown parser changed; review the HTML guard before building");
        const footnoteMarker = "footnotes.push([++footnoteIndex, title, href]);";
        if (source.split(footnoteMarker).length !== 2) throw new Error("The official footnote renderer changed; review its text escaping before building");
        source = `function __ep_escape_html(text) { return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }\n` + source;
        source = source.replace(marker, "const md = new Marked({ renderer: { html({ text }) { return __ep_escape_html(text); } } });");
        source = source.replace(footnoteMarker, "footnotes.push([++footnoteIndex, __ep_escape_html(title), __ep_escape_html(href)]);");
        return { contents: source, resolveDir: path.dirname(filename), loader: "js" };
      });
      // This renderer deliberately disables Mermaid and optional native Canvas.
      // Avoid bundling a diagram browser/runtime or native modules it cannot use.
      context.onResolve({ filter: /^(?:mermaid|canvas|bufferutil|utf-8-validate)$/ }, ({ path: name }) => ({ path: name, namespace: "disabled-runtime" }));
      context.onLoad({ filter: /.*/, namespace: "disabled-runtime" }, () => ({ contents: "module.exports = null;", loader: "js" }));
    },
  }],
});

// JSDOM resolves this worker at module load. Resource loading and XHR are never
// enabled by the renderer; retain a fail-closed worker for its path lookup.
await writeFile(path.join(output, "xhr-sync-worker.js"), 'throw new Error("Network access is disabled in the article renderer");\n');

const packages = new Map();
for (const filename of Object.keys(result.metafile.inputs)) {
  if (!filename.includes("node_modules/") || filename.startsWith("safe-wenyan:")) continue;
  const relative = filename.slice(filename.lastIndexOf("node_modules/") + 13);
  const parts = relative.split("/");
  const name = parts[0].startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
  const base = filename.slice(0, filename.lastIndexOf("node_modules/") + 13);
  const packageDirectory = path.resolve(directory, base, name);
  const info = JSON.parse(await readFile(path.join(packageDirectory, "package.json"), "utf8"));
  packages.set(`${name}@${info.version}`, { directory: packageDirectory, info });
}
packages.set(`${corePackage.name}@${corePackage.version}`, { directory: coreDirectory, info: corePackage });
const licenses = path.join(output, "licenses");
await mkdir(licenses, { recursive: true });
const records = [];
for (const [key, entry] of [...packages].sort(([a], [b]) => a.localeCompare(b))) {
  const folder = path.join(licenses, key.replace(/\//g, "__"));
  await mkdir(folder, { recursive: true });
  const files = (await readdir(entry.directory)).filter((item) => /^(?:licen[cs]e|copying|notice)(?:[.-].*)?$/i.test(item));
  for (const file of files) await copyFile(path.join(entry.directory, file), path.join(folder, file));
  if (entry.info.name === "saxes" && entry.info.version === "6.0.0" && files.length === 0) {
    await copyFile(path.join(directory, "licenses/saxes-6.0.0-LICENSE"), path.join(folder, "LICENSE"));
    files.push("LICENSE");
  }
  if (files.length === 0) throw new Error(`Missing distribution license for ${key}`);
  const metadata = { name: entry.info.name, version: entry.info.version, license: entry.info.license, repository: entry.info.repository, licenseFiles: files };
  await writeFile(path.join(folder, "package.json"), JSON.stringify(metadata, null, 2) + "\n");
  records.push(metadata);
}
await writeFile(path.join(output, "THIRD_PARTY.json"), JSON.stringify(records, null, 2) + "\n");
await copyFile(path.join(directory, "README.md"), path.join(output, "README.md"));
console.log(`Built official Wenyan ${corePackage.version}; bundled licenses for ${records.length} packages.`);
