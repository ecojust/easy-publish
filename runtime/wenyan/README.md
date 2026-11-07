# Built-in Wenyan renderer

This directory pins the official `@wenyan-md/core` **3.0.12** renderer used by
Wenyan CLI **2.0.12**. It provides the eight official public-account themes:
`default`, `orangeheart`, `rainbow`, `lapis`, `pie`, `maize`, `purple`, `phycat`.
Their CSS is bundled from the official package, not approximated with local
style dictionaries. The original theme, highlight, pseudo-element, footnote,
and WeChat post-processing implementations are retained.

## Build and package

```sh
npm ci --prefix runtime/wenyan --ignore-scripts
npm run build --prefix runtime/wenyan
npm test --prefix runtime/wenyan
```

Ship the complete `dist/` directory at `easy_publish/vendor/wenyan/` inside the
Python engine. Development uses `runtime/wenyan/dist/`. Run `render.cjs` with
the Node binary already included by Patchright (verified with v24.13.0); no
system Node/npm installation or separate runtime download is needed. JSDOM
requires Node 20.19+, 22.12+, or 24+. The build is a CommonJS bundle plus JSDOM's
disabled worker placeholder, linked legal comments and package license files.
Do not ship `node_modules/` or install packages on the user's machine.
The build selects css-tree's official CommonJS entry to bundle its JSON data,
and replaces JSDOM's file lookup for its default stylesheet with the identical
stylesheet text. These packaging adaptations avoid runtime files outside dist.

## Protocol

Without arguments, stdin contains one JSON object and stdout receives one JSON
response followed by a newline. `--stdio` keeps one process alive and processes
one JSON object per line sequentially until stdin EOF.

Input: `{"body":"## Article\n\nText","theme":"phycat"}`.
Success: `{"html":"<section id=... data-provider=\"WenYan\">...</section>"}`.
Failure: `{"error":"文章渲染失败，请重试。"}`. Errors never contain source text,
paths or dependency diagnostics. Invalid JSON/theme requests do not terminate
the persistent process. The host supplies its own deadline and output bound.
Input is bounded to 4 MiB per JSON request, body to 500,000 characters and output
to 16 MiB including JSON encoding and the terminating newline. There are no
article writes or publishing operations.

## Controlled rendering

The official core API is the same pipeline called by the CLI wrapper:
`renderMarkdown()` then `applyStylesWithTheme()`. The wrapper itself is not
imported because it also contains account, filesystem and network operations.
At build time, an exact-match guard adds a `renderer.html` callback to the
pinned official Marked parser. This escapes raw HTML tokens, preserving code
blocks and inline code. A second guarded adaptation escapes link text/URLs
before the official footnote formatter interpolates them into HTML. Markdown
output is sanitized before theme application and the final result is sanitized
again, retaining official inline styles but removing resource URLs. User style attributes, scripts, unsafe URI schemes,
custom themes and personal configuration are never accepted.
Static SVG is limited to the official themes' `svg`, `ellipse`, `g`, and `path`
elements, with validated numeric geometry/path commands and non-URL colors.
Ordered-list starts and table alignment are retained with integer/enum checks.

JSDOM never enables scripts or resource loading. HTTP(S) image references remain
in output for the application's local-image validation and preview placeholders;
the renderer does not fetch them. Global fetch is disabled. Mermaid and MathJax
conversion are disabled; their source remains ordinary code/text. Optional
Canvas/WebSocket native extensions and Mermaid's browser dependency are excluded
from the bundle. The official templates, code highlighting and footnotes remain.
The outer `#wenyan` section is retained, including its official inline styles.

## Sources and licenses

- Wenyan Core: https://github.com/caol64/wenyan-core, upstream reviewed at
  `b1c9edc7c13b8a5e110d29266aa849dfbc4dc6ef`; npm 3.0.12 is the pinned build
  authority in `package-lock.json`. Apache-2.0; full license copied to dist.
- Wenyan CLI: https://github.com/caol64/wenyan-cli, reviewed at
  `08b5174443cb7dbfbae4592d89a02a6fdad8af31`, npm 2.0.12. Its render command
  invokes the above core; CLI publishing/configuration code is not bundled.
- oDesk reference: https://github.com/ecojust/oDesk at
  `d062c05b3442f45205ff83cc28497d435fccce99`,
  `src/views/skillapps/WechatPublisher.vue`, `WechatPublisherTemplate.js`, and
  `src-tauri/addin/skills/article-writer.zip` (`scripts/wenyan-render.js`). oDesk
  uses these eight built-in themes and extra CSS themes. No oDesk CSS is copied
  into this runtime.

`dist/THIRD_PARTY.json` records bundled package versions and licenses;
`dist/licenses/` contains the corresponding upstream license/notice files.
`render.cjs.LEGAL.txt` preserves bundler-extracted legal comments. Do not remove
these files from distributable packages.

The saxes 6.0.0 npm archive omits its license file; the complete upstream text
is retained in `licenses/saxes-6.0.0-LICENSE`, from its v6.0.0 tag, commit
`211fa0ebec9b628affc09219199639887174bfc3` at
https://github.com/lddubeau/saxes/blob/211fa0ebec9b628affc09219199639887174bfc3/LICENSE.
The build copies this into the distribution alongside the other licenses and
fails if any bundled package lacks license text.
