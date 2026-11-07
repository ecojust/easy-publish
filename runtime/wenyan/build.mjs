import { build } from "esbuild";
import { readFile, writeFile, mkdir, readdir, copyFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";

const directory = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const output = path.join(directory, "dist");
const coreDirectory = path.join(directory, "node_modules/@wenyan-md/core");
const corePackage = JSON.parse(await readFile(path.join(coreDirectory, "package.json"), "utf8"));
const cliDirectory = path.join(directory, "node_modules/@wenyan-md/cli");
const cliPackage = JSON.parse(await readFile(path.join(cliDirectory, "package.json"), "utf8"));
if (cliPackage.version !== "2.0.12") throw new Error("Wenyan CLI version does not match the pinned runtime");
if (corePackage.version !== "3.0.12") throw new Error("Wenyan version does not match the pinned runtime");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
const common = {
  absWorkingDir: directory,
  bundle: true,
  platform: "node",
  target: "node22",
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
      context.onResolve({ filter: /^easy-publish:cli$/ }, () => ({ path: path.join(cliDirectory, "dist/cli.js"), namespace: "controlled-cli" }));
      context.onResolve({ filter: /^@wenyan-md\/core\/wrapper$/ }, () => ({ path: path.join(coreDirectory, "dist/wrapper.js"), namespace: "controlled-wrapper" }));
      context.onLoad({ filter: /.*/, namespace: "controlled-cli" }, async ({ path: filename }) => {
        let source = await readFile(filename, "utf8");
        if (createHash("sha256").update(source).digest("hex") !== "38b4a460f23b0d1acc1fa90f380abe1a4e45af01abe4e8ada25b3b89479b6a4c") throw new Error("Review the pinned official CLI before adapting its command surface");
        // Keep the official createProgram/common options/render command and
        // action. The desktop renderer must not expose publishing or credentials.
        source = source.slice(0, source.indexOf("    const pubCmd = program")) + source.slice(source.indexOf("    const renderCmd = program"));
        const afterRender = source.indexOf('    program\n        .command("theme")');
        source = source.slice(0, afterRender) + source.slice(source.indexOf("    return program;", afterRender));
        source = source.replace(/^import (?:input |password |\{ loadEnvFile \}|\{ resolveApiKey \}).*;\n/gm, "");
        source = source.replace(/async function setupProxy\(proxyUrl\) \{[\s\S]*?\n\}\n(?=export const program)/, "");
        source = source.replace(/            \.option\("-c, --custom-theme[^\n]+\n/, "");
        source = source.replace(/            \.option\("-f, --file[^\n]+\n/, "");
        source = source.replace(/async function runCommandWrapper\(action\) \{[\s\S]*?\n\}\n/, "async function runCommandWrapper(action) { await action(); }\n");
        return { contents: source, resolveDir: path.dirname(filename), loader: "js" };
      });
      context.onLoad({ filter: /.*/, namespace: "controlled-wrapper" }, async ({ path: filename }) => {
        let source = await readFile(filename, "utf8");
        if (createHash("sha256").update(source).digest("hex") !== "542c7b43f65e73e84efa844d9e960fed63f3f75444de01c17f675cde7195bcce") throw new Error("Review the pinned official CLI rendering wrapper before adapting it");
        // The remaining official render path only needs path, JSDOM and Core.
        // Drop unused publishing imports rather than allowing their module
        // initializers to construct credential/token/network stores.
        source = source.replace(/^import .+;\n/gm, (line) => /from "(?:node:path|jsdom|\.\/core\.js)"/.test(line) ? line : "");
        source = source.replace(/const configDir = \(\(\) => \{[\s\S]*?\n\}\)\(\);/, "");
        source = source.replace(/^const (?:tokenPath|cachePath|credentialPath|configPath) = path\.join\(configDir,.*;\n/gm, "");
        source = 'import { sanitizeParsed, sanitizeRoot, sanitizeStyled } from "easy-publish:sanitize";\n' + source;
        source = source.replace('from "./core.js";', 'from "easy-publish:safe-wenyan";');
        source = source.replace(/const wechatPublisher = new WechatPublisher\([\s\S]*?\n\);/, 'const wechatPublisher = null;');
        source = source.replace('const credentialStore = new CredentialStore(new NodeCredentialStorageAdapter());', 'const credentialStore = null;');
        source = source.replace('const configStore = new ConfigStore();', 'const configStore = null;');
        source = source.replace('const nodeMermaidRenderer = createNodeMermaidRenderer();', 'const nodeMermaidRenderer = null;');
        source = source.replace(/const wenyanCoreInstance = await createWenyanCore\([\s\S]*?\n\}\);/, 'const wenyanCoreInstance = await createWenyanCore({ isWechat: true, isConvertMathJax: false, mermaid: false });');
        source = source.replace('const wenyanCoreInstanceWithoutMermaid = await createWenyanCore();', 'const wenyanCoreInstanceWithoutMermaid = wenyanCoreInstance;');
        source = source.replace('    const normalizePath2 = getNormalizeFilePath(customTheme);\n    handledCustomTheme = await readFileContent(normalizePath2);', '    throw new Error("Custom themes are not enabled in this renderer");');
        source = source.replace('handledCustomTheme = await configStore.getThemeById(theme);', 'handledCustomTheme = undefined;');
        source = source.replace('  const html = await coreInstance.renderMarkdown(content);', '  const html = sanitizeParsed(await coreInstance.renderMarkdown(content));');
        source = source.replace('  const result = await coreInstance.applyStylesWithTheme(wenyan, options);\n  return result;', '  try { sanitizeRoot(wenyan); return sanitizeStyled(await coreInstance.applyStylesWithTheme(wenyan, options)); } finally { dom.window.close(); }');
        source = source.replace('  const preHandlerContent = await wenyanCoreInstance.handleFrontMatter(content);', '  const preHandlerContent = await wenyanCoreInstance.handleFrontMatter(content);\n  if (preHandlerContent.type === "image" || preHandlerContent.image_list?.length) throw new Error("Image posts are not supported by the article renderer");');
        source = source.slice(0, source.lastIndexOf("export {")) + 'export { prepareRenderContext, getNormalizeFilePath };\n';
        return { contents: source, resolveDir: path.dirname(filename), loader: "js" };
      });
      context.onResolve({ filter: /^easy-publish:sanitize$/ }, () => ({ path: path.join(directory, "sanitize.mjs") }));
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
};
const result = await build({
  ...common,
  stdin: { contents: 'export { createProgram, program } from "easy-publish:cli";', resolveDir: directory, sourcefile: "cli-entry.mjs" },
  outfile: path.join(output, "wenyan-cli.mjs"),
  format: "esm",
  banner: { js: 'import { createRequire as __ep_createRequire } from "node:module"; import { fileURLToPath as __ep_fileURLToPath } from "node:url"; import { dirname as __ep_dirname } from "node:path"; var require = __ep_createRequire(import.meta.url); var __filename = __ep_fileURLToPath(import.meta.url); var __dirname = __ep_dirname(__filename);' },
});
await build({
  absWorkingDir: directory, entryPoints: ["render.mjs"], outfile: path.join(output, "render.cjs"),
  bundle: true, platform: "node", format: "cjs", target: "node22", minify: true,
  define: { "import.meta.url": "__ep_import_meta_url" },
  banner: { js: 'var __ep_import_meta_url = require("node:url").pathToFileURL(__filename).href;' },
});

// JSDOM resolves this worker at module load. Resource loading and XHR are never
// enabled by the renderer; retain a fail-closed worker for its path lookup.
await writeFile(path.join(output, "xhr-sync-worker.js"), 'throw new Error("Network access is disabled in the article renderer");\n');

const packages = new Map();
for (const filename of Object.keys(result.metafile.inputs)) {
  if (!filename.includes("node_modules/") || /^(?:safe-wenyan|controlled-cli|controlled-wrapper):/.test(filename)) continue;
  const relative = filename.slice(filename.lastIndexOf("node_modules/") + 13);
  const parts = relative.split("/");
  const name = parts[0].startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
  const base = filename.slice(0, filename.lastIndexOf("node_modules/") + 13);
  const packageDirectory = path.resolve(directory, base, name);
  const info = JSON.parse(await readFile(path.join(packageDirectory, "package.json"), "utf8"));
  packages.set(`${name}@${info.version}`, { directory: packageDirectory, info });
}
packages.set(`${corePackage.name}@${corePackage.version}`, { directory: coreDirectory, info: corePackage });
packages.set(`${cliPackage.name}@${cliPackage.version}`, { directory: cliDirectory, info: cliPackage });
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
console.log(`Built official Wenyan CLI ${cliPackage.version} / Core ${corePackage.version}; bundled licenses for ${records.length} packages.`);
