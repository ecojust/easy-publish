# Built-in Wenyan CLI renderer

This directory pins official `@wenyan-md/cli` **2.0.12** and its
`@wenyan-md/core` **3.0.12** renderer. The bundled official CLI `render` command
produces article previews and the identical HTML used for WeChat publishing.
The eight official themes are `default`, `orangeheart`, `rainbow`, `lapis`,
`pie`, `maize`, `purple`, and `phycat`; their CSS, syntax highlighting, static
SVG decorations, footnotes, and WeChat post-processing come from Wenyan.

## Build and package

```sh
npm ci --prefix runtime/wenyan --ignore-scripts
npm run build --prefix runtime/wenyan
npm test --prefix runtime/wenyan
```

Ship the complete `dist/` at `easy_publish/vendor/wenyan/` inside the Python
engine. Development uses `runtime/wenyan/dist/`. It contains:

- `wenyan-cli.mjs`: the official CLI, restricted to offline article rendering;
- `render.cjs`: the application's bounded JSONL adapter for that CLI;
- JSDOM's disabled worker placeholder, linked legal comments, and all bundled
  package versions/licenses.

Both entries use the Node executable already included by Patchright (verified
with v24.13.0); users need no system Node/npm or separate downloads. JSDOM needs
Node 20.19+, 22.12+, or 24+. Do not ship `node_modules/` or install packages on
users' computers. The build includes css-tree's official CommonJS JSON data and
JSDOM's identical default stylesheet instead of reading those source-tree files
at runtime.

The official CLI also supports standalone stdin rendering for verification:

```sh
node dist/wenyan-cli.mjs --version
printf '## Article\n\nText\n' | node dist/wenyan-cli.mjs render --theme phycat
```

The first command reports `2.0.12`; `--help` exposes only `render`. Publishing,
server, credential, token, custom-theme and theme-management commands are not
included in this application build. File input is also excluded; the adapter
supplies Markdown directly in memory.

## Application protocol

Without arguments, `render.cjs` reads one JSON object from stdin and writes one
JSON response followed by a newline. `--stdio` keeps the process alive and
handles one request per line sequentially until EOF.

Input: `{"body":"## Article\n\nText","theme":"phycat"}`.
Success: `{"html":"<section id=... data-provider=\"WenYan\">...</section>"}`.
Failure: `{"error":"文章渲染失败，请重试。"}`. No article text, file paths or
upstream diagnostics are included in errors. Invalid requests leave the
persistent process usable. Requests are bounded to 4 MiB, bodies to 500,000
characters and JSON output to 16 MiB including its final newline. Python owns a
20-second render deadline, bounded cache, cancellation, process shutdown and an
empty temporary HOME/XDG directory that is removed on shutdown.

For each request, the adapter creates the official CLI program and calls its
`render --theme <selected-theme>` command through Commander `parseAsync()`.
Markdown is an in-memory argument to this API, never an OS process argument,
shell command, temporary source file or model request. The official render
command prints its completed HTML, which the sequential adapter captures and
returns as JSON. It uses `prepareRenderContext()` → `renderWithTheme()` →
`renderStyledContent()` from the pinned official wrapper; this is actual CLI
command execution, not a local approximation of its themes.

## Controlled rendering

The build verifies exact SHA-256 hashes of the official CLI entry and Core
wrapper before adapting their command surface. It retains the official render
action, input handling and rendering pipeline. Unused publishing/configuration
imports and store initializers are removed, so rendering does not read personal
Wenyan profiles or create credential/token/cache files. The selected account's
theme is passed explicitly. Frontmatter uses Wenyan's normal article handling
(e.g. description becomes a quote); frontmatter cannot override theme options.
Image-post frontmatter (`type: image` or nonempty `image_list`) is rejected,
because the application handles articles and videos, not image-note publishing.

The original safe-HTML policy remains: a guarded parser adaptation escapes raw
HTML tokens while preserving inline/fenced code; another escapes link text and
URLs before the official footnote formatter inserts HTML. The shared
`sanitize.mjs` removes unsafe tags and URI schemes before official styling,
validates image/link references, and sanitizes again after styling. It retains
official inline styles and SVG geometry while disallowing CSS resource URLs,
active SVG references and user HTML. Static SVG is limited to `svg`, `ellipse`,
`g`, and `path` with bounded numeric/path/color values. Ordered-list starts and
table alignment survive with integer/enum checks.

JSDOM enables neither scripts nor resource loading. HTTP(S) image references
remain for the application's imported-image validation and preview placeholders;
they are never fetched during rendering. The adapter disables global fetch.
Mermaid and MathJax conversion are disabled; source stays ordinary code/text.
Optional native Canvas/WebSocket modules and Mermaid's browser dependency are
excluded. The outer `#wenyan` section and official theme styles remain intact.

## Sources and licenses

- CLI: https://github.com/caol64/wenyan-cli at
  `08b5174443cb7dbfbae4592d89a02a6fdad8af31`, npm 2.0.12, Apache-2.0.
- Core: https://github.com/caol64/wenyan-core at
  `b1c9edc7c13b8a5e110d29266aa849dfbc4dc6ef`, npm 3.0.12, Apache-2.0.
- npm archive integrity and every transitive dependency version are pinned in
  `package-lock.json`; `build.mjs` additionally pins the two adapted entry files.
- oDesk reference: https://github.com/ecojust/oDesk at
  `d062c05b3442f45205ff83cc28497d435fccce99`,
  `src/views/skillapps/WechatPublisher.vue`, `WechatPublisherTemplate.js`, and
  `src-tauri/addin/skills/article-writer.zip` (`scripts/wenyan-render.js`). No
  oDesk CSS or account configuration is copied into this runtime.

`dist/THIRD_PARTY.json` records bundled versions/licenses; `dist/licenses/`
contains original license/notice texts, including the CLI and Core Apache-2.0
licenses. `wenyan-cli.mjs.LEGAL.txt` preserves extracted legal comments. Keep all
of them in every distribution.

The saxes 6.0.0 npm archive omits its license; its complete upstream text remains
in `licenses/saxes-6.0.0-LICENSE`, taken from v6.0.0 commit
`211fa0ebec9b628affc09219199639887174bfc3` at
https://github.com/lddubeau/saxes/blob/211fa0ebec9b628affc09219199639887174bfc3/LICENSE.
The build copies it alongside other licenses and fails if any bundled package
has no license text.
