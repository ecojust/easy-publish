// Explicit browser test fixture. This file is never imported by application code.
(() => {
  const clone = (value) => structuredClone(value);
  const date = "2026-11-06T10:00:00Z";
  const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==";
  const platform = (id, name, color, kinds, modes, extra = {}) => ({ id, name, color, kinds, modes, auth: "browser", titleLimit: 100, description: `${name}浏览器发布连接（测试替身）`, homeUrl: `https://${id}.example.test/creator`, fields: [], ...extra });
  const platforms = [
    platform("wechat", "微信公众号", "#21b36b", ["article"], ["publish", "draft"], { auth: "wechat_api", description: "通过官方 API 创建文章草稿或提交发布（测试替身）" }),
    platform("zhihu", "知乎", "#2680e9", ["article", "video"], ["publish"]),
    platform("dayu", "大鱼号", "#ed8b3c", ["article", "video"], ["publish"], { fields: [{ key: "source", label: "创作来源", type: "select", required: true, kinds: ["article", "video"], choices: [{ value: "无需标注", label: "无需标注" }, { value: "AI生成", label: "AI生成" }] }] }),
    platform("xueqiu", "雪球", "#488ecb", ["article"], ["publish"]),
    platform("douyin", "抖音", "#32374c", ["video"], ["publish"]),
    platform("xhs", "小红书", "#e8576f", ["video"], ["publish"]),
    platform("channels", "视频号", "#de9e4d", ["video"], ["publish", "draft"]),
  ];
  const account = (id, platformId, label, status = "ready") => ({ id, platformId, label, status, message: status === "ready" ? "已连接（浏览器测试替身）" : "请连接测试账号", updatedAt: date, credentialConfigured: platformId === "wechat" });
  const draft = (id, kind, title, body) => ({ id, kind, title, body, description: "", tags: [], author: "", videoAssetId: null, coverAssetId: null, imageAssetIds: [], platformOptions: {}, createdAt: date, updatedAt: date, revision: 1 });
  const state = {
    platforms,
    accounts: [account("a-wechat", "wechat", "品牌公众号"), account("a-zhihu", "zhihu", "创作笔记"), account("a-dayu", "dayu", "创作工作室"), account("a-douyin", "douyin", "品牌抖音"), account("a-xhs", "xhs", "品牌小红书"), account("a-channels", "channels", "品牌视频号", "login_required")],
    contents: [draft("c-article", "article", "把创作的时间，还给创作", "## 少一点重复，多一点表达\n\n每一篇好内容，都值得被更多人看见。\n\n当文章写完、视频剪好，接下来的分享应该简单一些。\n\n### 这一次，专注内容本身\n\n- 在一个工作空间整理素材\n- 为不同平台选择合适的账号\n- 清楚了解每个目标的发布进度\n\n**一份创作，更多可能。**"), draft("c-article-2", "article", "从灵感到第一篇文章", "写下你的第一个想法。")],
    batches: [],
    runtime: { browserReady: true, browserInstalling: false, browserMessage: "测试浏览器已就绪", vaultReady: true, vaultMessage: "测试凭据库已就绪", version: "0.1.1" },
  };
  const assets = {};
  const calls = [];
  let failNextBatch = false;
  let preflightError = false;
  let nextFile = null;
  window.__fixture = {
    state, calls,
    failBatchOnce: () => { failNextBatch = true; },
    blockPreflight: (value) => { preflightError = value; },
    pick: (value) => { nextFile = value; },
    seedTasks: () => {
      const statuses = ["queued", "running", "submitting", "draft_created", "submitted", "published", "needs_attention", "failed", "cancelled", "unknown"];
      state.batches = [{ id: "b-statuses", contentId: "c-article", title: "各目标回执状态测试", kind: "article", mode: "publish", createdAt: date, tasks: statuses.map((status) => ({ id: `t-${status}`, accountId: "a-zhihu", accountLabel: `测试 ${status}`, platformId: "zhihu", status, message: "页面状态验证替身", remoteId: null, url: null, canRetry: status === "failed" || status === "cancelled", startedAt: null, finishedAt: null })) }];
    },
  };
  const bridge = {
    async apiRequest(method, path, body) {
      // Keep method and payload for assertions; never print credential payloads.
      calls.push({ method, path, body: clone(body) });
      if (method === "GET" && path === "/api/state") return clone(state);
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
      if (method === "POST" && path.endsWith("/cancel")) {
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
      if (method === "POST" && (path.endsWith("/login") || path.endsWith("/check"))) {
        const account = state.accounts.find((entry) => entry.id === path.split("/")[3]);
        account.status = "ready"; account.message = "测试会话已连接";
        return { ok: true };
      }
      if (method === "DELETE" && path.startsWith("/api/accounts/")) { state.accounts = state.accounts.filter((item) => item.id !== path.split("/").pop()); return { ok: true }; }
      if (method === "POST" && path === "/api/runtime/browser/install") { state.runtime.browserReady = true; state.runtime.browserMessage = "测试浏览器已准备"; return { ok: true }; }
      throw { error: `未实现的测试路由 ${method} ${path}`, code: "fixture_route" };
    },
    async pickFile(kind) { calls.push({ method: "PICK", path: kind }); const value = nextFile; nextFile = null; return value; },
    async openExternal(url) { calls.push({ method: "OPEN", path: url }); },
  };
  window.easyPublish = bridge;
})();
