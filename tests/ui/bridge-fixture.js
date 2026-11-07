// Explicit browser test fixture. This file is never imported by application code.
(() => {
  const clone = (value) => structuredClone(value);
  const date = "2026-11-06T10:00:00Z";
  const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==";
  const platform = (id, name, color, kinds, modes, extra = {}) => ({ id, name, color, kinds, modes, auth: "browser", titleLimit: 100, description: `${name}浏览器发布连接（测试替身）`, homeUrl: `https://${id}.example.test/creator`, fields: [], ...extra });
  const platforms = [
    platform("wechat", "微信公众号", "#21b36b", ["article"], ["publish", "draft"], { auth: "wechat_api", description: "通过官方 API 创建文章草稿或提交发布（测试替身）" }),
    platform("zhihu", "知乎", "#2680e9", ["article"], ["publish"]),
    platform("dayu", "大鱼号", "#ed8b3c", ["article"], ["publish"], { fields: [{ key: "source", label: "信息来源", type: "select", required: true, kinds: ["article"], choices: [{ value: "无需标注", label: "无需标注" }, { value: "AI生成", label: "AI生成" }] }] }),
    platform("xueqiu", "雪球", "#488ecb", ["article"], ["publish"]),
    platform("douyin", "抖音", "#32374c", ["video"], ["publish"]),
    platform("kuaishou", "快手", "#ff5000", ["video"], ["publish"], { titleLimit: 200, description: "浏览器登录；标题、描述与话题合计最多 500 字符（测试替身）" }),
    platform("xiaohongshu", "小红书", "#e8576f", ["video"], ["publish"]),
    platform("channels", "视频号", "#de9e4d", ["video"], ["publish", "draft"]),
    platform("bilibili", "哔哩哔哩", "#00a1d6", ["video"], ["publish"], { titleLimit: 80, fields: [
      { key: "copyright", label: "创作类型", type: "select", required: true, kinds: ["video"], choices: [{ value: "1", label: "原创" }, { value: "2", label: "转载" }] },
      { key: "tid", label: "视频分区", type: "select", required: true, kinds: ["video"], dynamicChoices: "bilibiliCategories" },
      { key: "source", label: "转载来源", type: "text", required: false, kinds: ["video"], hint: "转载必填，填写原始视频链接；原创请留空" },
    ] }),
  ];
  const bilibiliCategories = [{ value: "101", label: "测试分区 / 科普" }, { value: "102", label: "测试分区 / 创作" }];
  const articleTemplates = [
    { id: "clean", name: "清爽蓝", description: "蓝色标题与留白，适合知识分享和日常文章", accent: "#2563eb" },
    { id: "editorial", name: "墨色杂志", description: "衬线标题与细分隔线，适合观点和深度阅读", accent: "#9a3412" },
    { id: "journal", name: "暖色手记", description: "暖色小标题与柔和引用，适合故事和生活记录", accent: "#b45309" },
    { id: "tech", name: "理性科技", description: "青绿色层次与清晰代码块，适合技术和产品内容", accent: "#0f766e" },
  ];
  const account = (id, platformId, label, status = "ready") => ({ id, platformId, label, status, message: status === "ready" ? "已连接（浏览器测试替身）" : "请连接测试账号", updatedAt: date, credentialConfigured: platformId === "wechat", ...(platformId === "bilibili" ? { bilibiliCategories: status === "ready" ? clone(bilibiliCategories) : [] } : {}) });
  const draft = (id, kind, title, body) => ({ id, kind, title, body, description: "", tags: [], author: "", videoAssetId: null, coverAssetId: null, imageAssetIds: [], platformOptions: {}, articleTemplateId: "clean", platformPublicationStates: {}, createdAt: date, updatedAt: date, revision: 1 });
  const state = {
    platforms, articleTemplates,
    accounts: [account("a-wechat", "wechat", "品牌公众号"), account("a-zhihu", "zhihu", "创作笔记"), account("a-dayu", "dayu", "创作工作室"), account("a-douyin", "douyin", "品牌抖音"), account("a-kuaishou", "kuaishou", "品牌快手"), account("a-xhs", "xiaohongshu", "品牌小红书"), account("a-channels", "channels", "品牌视频号", "login_required"), account("a-bilibili", "bilibili", "品牌哔哩哔哩")],
    contents: [draft("c-article", "article", "把创作的时间，还给创作", "## 少一点重复，多一点表达\n\n每一篇好内容，都值得被更多人看见。\n\n当文章写完、视频剪好，接下来的分享应该简单一些。\n\n### 这一次，专注内容本身\n\n- 在一个工作空间整理素材\n- 为不同平台选择合适的账号\n- 清楚了解每个目标的发布进度\n\n**一份创作，更多可能。**"), draft("c-article-2", "article", "从灵感到第一篇文章", "写下你的第一个想法。")],
    batches: [],
    runtime: { browserReady: true, browserInstalling: false, browserMessage: "测试浏览器已就绪", vaultReady: true, vaultMessage: "测试凭据库已就绪", version: "0.1.5" },
  };
  const assets = {};
  const calls = [];
  let failNextBatch = false;
  let preflightError = false;
  let nextFile = null;
  const deferredLogins = new Set();
  const finishLogin = (entry) => {
    entry.status = "ready"; entry.message = "测试会话已连接";
    if (entry.platformId === "bilibili") entry.bilibiliCategories = clone(bilibiliCategories);
  };
  window.__fixture = {
    state, calls,
    failBatchOnce: () => { failNextBatch = true; },
    blockPreflight: (value) => { preflightError = value; },
    pick: (value) => { nextFile = value; },
    deferLogin: (id) => { deferredLogins.add(id); },
    loginProgress: (id, message) => {
      const entry = state.accounts.find((item) => item.id === id);
      if (entry?.status !== "logging_in") throw new Error("Fixture account must be logging in before progress updates.");
      entry.message = message;
    },
    completeLogin: (id) => {
      const entry = state.accounts.find((item) => item.id === id);
      if (entry?.status !== "logging_in") throw new Error("Fixture account must be logging in before completion.");
      deferredLogins.delete(id);
      finishLogin(entry);
    },
    seedTasks: () => {
      const statuses = ["queued", "running", "submitting", "draft_created", "submitted", "published", "needs_attention", "failed", "cancelled", "unknown"];
      state.batches = [{ id: "b-statuses", contentId: "c-article", title: "各目标回执状态测试", kind: "article", mode: "publish", createdAt: date, tasks: statuses.map((status) => ({ id: `t-${status}`, accountId: "a-zhihu", accountLabel: `测试 ${status}`, platformId: "zhihu", status, message: "页面状态验证替身", remoteId: null, url: null, canRetry: status === "failed" || status === "cancelled", startedAt: null, finishedAt: null })) }];
    },
  };
  const bridge = {
    async apiRequest(method, path, body) {
      // Keep method and payload for assertions; never print credential payloads.
      calls.push({ method, path, body: clone(body) });
      if (method === "GET" && path === "/api/state") {
        for (const content of state.contents) {
          content.platformPublicationStates = {};
          for (const batch of state.batches) if (batch.contentId === content.id && batch.mode === "publish") {
            for (const task of batch.tasks) if (["submitted", "published", "unknown"].includes(task.status)) content.platformPublicationStates[task.platformId] = task.status;
          }
        }
        return clone(state);
      }
      if (method === "POST" && path === "/api/preview/article") {
        // UI protocol fixture only. Production uses the Python article renderer.
        const template = articleTemplates.find((item) => item.id === body.articleTemplateId);
        const html = await window.__renderMarkdownFixture(body.body);
        const native = ["zhihu", "xueqiu"].includes(body.platformId);
        return {
          html: native ? html : `<section data-template="${template.id}" style="color:#334155;font-size:16px;line-height:1.85;border-top:3px solid ${template.accent};padding:18px 4px">${html}</section>`,
          notes: [native ? "该平台使用原生字体和颜色，保留文章结构（测试回执）。" : "发布时会按所选平台的编辑器能力转换格式（测试回执）。"],
        };
      }
      if (method === "POST" && path === "/api/contents") {
        const content = { ...clone(body), id: crypto.randomUUID(), createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), revision: 1 };
        state.contents.push(content);
        return clone(content);
      }
      if (method === "PUT" && path.startsWith("/api/contents/")) {
        const index = state.contents.findIndex((content) => content.id === path.split("/").pop());
        const old = state.contents[index];
        state.contents[index] = { ...old, ...clone(body), updatedAt: new Date().toISOString(), revision: old.revision + 1 };
        return clone(state.contents[index]);
      }
      if (method === "DELETE" && path.startsWith("/api/contents/")) {
        state.contents = state.contents.filter((content) => content.id !== path.split("/").pop());
        return { ok: true };
      }
      if (method === "POST" && path === "/api/assets/import") {
        const id = crypto.randomUUID();
        assets[id] = { id, name: body.path.split("/").pop(), kind: body.kind, size: body.kind === "video" ? 1024 * 1024 * 9 : 2048, mime: body.kind === "video" ? "video/mp4" : body.kind === "image" ? "image/png" : "text/markdown", ...(body.kind === "article" ? { text: "# 导入文章\n\n这是一篇导入的 Markdown 稿件。" } : {}) };
        return clone(assets[id]);
      }
      if (method === "GET" && path.startsWith("/api/assets/")) return path.endsWith("/preview") ? { dataUrl: png } : clone(assets[path.split("/").pop()]);
      if (method === "POST" && path === "/api/preflight") return { ok: !preflightError, targets: body.accountIds.map((accountId) => ({ accountId, errors: preflightError ? ["测试：平台要求添加封面。"] : [], warnings: [] })) };
      if (method === "POST" && path === "/api/batches") {
        if (failNextBatch) { failNextBatch = false; throw { error: "测试：网络暂时中断，请重试。", code: "fixture_network" }; }
        const existing = state.batches.find((batch) => batch.requestId === body.requestId);
        if (existing) return clone(existing);
        const content = state.contents.find((entry) => entry.id === body.contentId);
        const batch = { id: crypto.randomUUID(), requestId: body.requestId, contentId: content.id, title: content.title, kind: content.kind, mode: body.mode, createdAt: new Date().toISOString(), tasks: body.accountIds.map((id) => { const account = state.accounts.find((item) => item.id === id); return { id: crypto.randomUUID(), accountId: id, accountLabel: account.label, platformId: account.platformId, status: "queued", message: "等待处理（浏览器测试替身）", remoteId: null, url: null, canRetry: false, startedAt: null, finishedAt: null }; }) };
        state.batches.push(batch);
        return clone(batch);
      }
      if (method === "POST" && path.startsWith("/api/batches/") && path.endsWith("/cancel")) {
        const batch = state.batches.find((entry) => entry.id === path.split("/")[3]);
        for (const task of batch.tasks) if (["queued", "running", "submitting"].includes(task.status)) { task.status = task.status === "submitting" ? "unknown" : "cancelled"; task.canRetry = task.status === "cancelled"; }
        return { ok: true };
      }
      if (method === "POST" && path.endsWith("/retry")) {
        const task = state.batches.flatMap((batch) => batch.tasks).find((item) => item.id === path.split("/")[3]);
        if (!task.canRetry) throw { error: "该任务不可重试。" };
        task.status = "queued"; task.canRetry = false;
        return clone(state.batches[0]);
      }
      if (method === "POST" && path === "/api/accounts") {
        const item = account(crypto.randomUUID(), body.platformId, body.label, "login_required");
        state.accounts.push(item);
        return clone(item);
      }
      if (method === "POST" && path.startsWith("/api/accounts/") && path.endsWith("/cancel")) {
        const account = state.accounts.find((entry) => entry.id === path.split("/")[3]);
        if (account.status === "logging_in") {
          deferredLogins.delete(account.id);
          account.status = "login_required"; account.message = "连接已取消";
          if (account.platformId === "bilibili") account.bilibiliCategories = [];
        }
        return { ok: true };
      }
      if (method === "POST" && (path.endsWith("/login") || path.endsWith("/check"))) {
        const account = state.accounts.find((entry) => entry.id === path.split("/")[3]);
        if (deferredLogins.has(account.id)) {
          account.status = "logging_in"; account.message = "等待扫码登录（测试进度）";
        } else finishLogin(account);
        return { ok: true };
      }
      if (method === "DELETE" && path.startsWith("/api/accounts/")) { state.accounts = state.accounts.filter((item) => item.id !== path.split("/").pop()); return { ok: true }; }
      if (method === "POST" && path === "/api/runtime/browser/install") { state.runtime.browserReady = true; state.runtime.browserMessage = "测试浏览器已准备"; return { ok: true }; }
      throw { error: `未实现的测试路由 ${method} ${path}`, code: "fixture_route" };
    },
    async pickFile(kind) { calls.push({ method: "PICK", path: kind }); const value = nextFile; nextFile = null; return value; },
    async openExternal(url) { calls.push({ method: "OPEN", path: url }); },
    async mcpConfiguration() { return { command: "/fixture/Easy Publish.app/Contents/MacOS/easy-publish-engine", args: ["--mcp", "--data-dir", "/fixture/Application Support/easy-publish"] }; },
  };
  window.easyPublish = bridge;
})();
