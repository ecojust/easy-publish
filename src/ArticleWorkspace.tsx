import { useEffect, useRef, useState } from "react";
import { ArrowRight, CheckCheck, Eye, FileText, ImagePlus, Plus, Save, Send, ShieldCheck, Sparkles, Trash2, Upload, X } from "lucide-react";
import MarkdownIt from "markdown-it";
import { bridge, errorMessage, request } from "./api";
import MarkdownPreview, { AssetImage } from "./MarkdownPreview";
import { dateLabel, InlineError, Modal, PlatformMark, Spinner } from "./ui";
import type { Account, ArticleTemplate, Asset, Batch, Content, ContentInput, FormatJob, FormattingState, Platform, Preflight, PublishMode } from "./shared/types";

const emptyArticle = (): ContentInput => ({ kind: "article", inputFormat: "plain", sourceText: "", title: "", body: "", description: "", tags: [], author: "", videoAssetId: null, coverAssetId: null, imageAssetIds: [], platformOptions: {}, articleTemplateId: "wenyan-default" });
const asInput = (content: Content): ContentInput => ({ kind: "article", inputFormat: content.inputFormat || "markdown", sourceText: content.sourceText || "", title: content.title, body: content.body, description: content.description, tags: content.tags, author: content.author, videoAssetId: null, coverAssetId: content.coverAssetId, imageAssetIds: content.imageAssetIds, platformOptions: content.platformOptions || {}, articleTemplateId: content.articleTemplateId || "clean" });
const sourceKey = (content: ContentInput) => JSON.stringify([content.inputFormat, content.sourceText, content.imageAssetIds]);
const historyLabels = { submitted: "此前已提交", published: "此前已发布", unknown: "结果待确认" };

function polishingSource(input: ContentInput) {
  const imageAssetIds = [...input.imageAssetIds];
  const markdown = new MarkdownIt("commonmark", { html: false }).enable(["table", "strikethrough"]);
  const tokens = markdown.parse(input.body, {});
  const text = (items: typeof tokens): string => {
    let link = "";
    const lists: Array<{ ordered: boolean; next: number }> = [];
    return items.map((token) => {
      if (token.type === "inline") return text(token.children || []);
      if (token.type === "image") {
        const source = token.attrGet("src") || "";
        const id = /^easy-asset:\/\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.exec(source)?.[1];
        if (id) {
          if (!imageAssetIds.includes(id)) imageAssetIds.push(id);
          return `【图片${imageAssetIds.indexOf(id) + 1}】`;
        }
        return `[${token.content || "图片"}${source ? `：${source}` : ""}]`;
      }
      if (token.type === "link_open") { link = token.attrGet("href") || ""; return ""; }
      if (token.type === "link_close") return link ? `（${link}）` : "";
      if (["softbreak", "hardbreak", "tr_close"].includes(token.type)) return "\n";
      if (["paragraph_close", "heading_close", "blockquote_close", "hr"].includes(token.type)) return "\n\n";
      if (["ordered_list_open", "bullet_list_open"].includes(token.type)) { lists.push({ ordered: token.type === "ordered_list_open", next: Number(token.attrGet("start") || 1) }); return ""; }
      if (["ordered_list_close", "bullet_list_close"].includes(token.type)) { lists.pop(); return ""; }
      if (token.type === "list_item_open") { const list = lists.at(-1); return list?.ordered ? `${list.next++}. ` : "• "; }
      if (["th_close", "td_close"].includes(token.type)) return "\t";
      if (["fence", "code_block"].includes(token.type)) return `${token.content}\n\n`;
      return token.nesting === 0 ? token.content : "";
    }).join("");
  };
  const sourceText = text(tokens).replace(/\n{3,}/g, "\n\n").trim();
  if (imageAssetIds.length > 100) throw new Error("正文图片超过 100 张，请减少图片后再复制润色。");
  return { sourceText, imageAssetIds };
}

interface Props {
  contents: Content[]; platforms: Platform[]; accounts: Account[]; articleTemplates: ArticleTemplate[]; formatting: FormattingState;
  onRefresh: () => Promise<void>; onAccounts: () => void; onSettings: () => void; onTasks: () => void; onNotice: (message: string) => void;
}

export default function ArticleWorkspace({ contents, platforms, accounts, articleTemplates, formatting, onRefresh, onAccounts, onSettings, onTasks, onNotice }: Props) {
  const entries = contents.filter((content) => content.kind === "article").sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const initial = entries[0];
  const [contentId, setContentId] = useState<string | null>(initial?.id || null);
  const [form, setForm] = useState<ContentInput>(() => initial ? asInput(initial) : emptyArticle());
  const [stored, setStored] = useState<Content | undefined>(initial);
  const [savedSnapshot, setSavedSnapshot] = useState(() => JSON.stringify(initial ? asInput(initial) : emptyArticle()));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<FormatJob | null>(null);
  const [mode, setMode] = useState<PublishMode>("publish");
  const [repeatIds, setRepeatIds] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [pendingSelection, setPendingSelection] = useState<Content | "new" | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const editor = useRef<HTMLTextAreaElement>(null);
  const operation = useRef(false);
  const enqueueId = useRef<string | null>(null);
  const checkedId = useRef<string | null>(null);
  const checkedSignature = useRef<string | null>(null);
  const formatRequest = useRef<{ contentId: string; revision: number; requestId: string; source: string } | null>(null);
  const formRef = useRef(form); formRef.current = form;
  const contentRef = useRef(contentId); contentRef.current = contentId;
  const current = entries.find((content) => content.id === contentId);
  const history = current?.platformPublicationStates || stored?.platformPublicationStates || {};
  const plain = form.inputFormat === "plain";
  const dirty = JSON.stringify(form) !== savedSnapshot;
  const running = job?.status === "running";
  const ready = plain ? !!stored?.formatReady && sourceKey(form) === sourceKey(asInput(stored)) && form.body === stored.body : !!form.body.trim();
  const articlePlatforms = platforms.filter((platform) => platform.kinds.includes("article"));
  const enabled = accounts.filter((account) => account.defaultSelected !== false && account.status === "ready" && articlePlatforms.some((platform) => platform.id === account.platformId && platform.modes.includes(mode)));
  const targets = enabled.filter((account) => !history[account.platformId] || repeatIds.includes(account.id));
  const previous = enabled.filter((account) => !!history[account.platformId] && !repeatIds.includes(account.id));
  const targetKey = targets.map((account) => `${account.id}:${account.wechatTemplateId || "clean"}`).sort().join(",");
  // Reading a result must not depend on whether its account will be published to.
  const previewAccounts = accounts.filter((account) => account.status === "ready" && articlePlatforms.some((platform) => platform.id === account.platformId));
  const activeAccount = previewAccounts.find((account) => account.id === previewId) || previewAccounts[0];
  const activePlatform = articlePlatforms.find((platform) => platform.id === activeAccount?.platformId);
  const activeTemplate = !activeAccount ? "wenyan-default" : activeAccount.platformId === "wechat" ? activeAccount.wechatTemplateId || "clean" : form.articleTemplateId;
  const selectedPlatforms = articlePlatforms.filter((platform) => targets.some((account) => account.platformId === platform.id));

  useEffect(() => { setPreviewError(null); }, [previewOpen, activeAccount?.id, activeTemplate, form.body]);

  const signature = JSON.stringify({ contentId, form, targetKey, mode });
  const signatureRef = useRef(signature); signatureRef.current = signature;
  useEffect(() => { setPreflight(null); checkedId.current = null; checkedSignature.current = null; enqueueId.current = null; }, [signature]);
  useEffect(() => { setRepeatIds((ids) => ids.filter((id) => enabled.some((account) => account.id === id))); }, [enabled.map((account) => account.id).join(",")]);
  useEffect(() => { setRepeatIds([]); }, [contentId, JSON.stringify(history)]);

  function update<K extends keyof ContentInput>(key: K, value: ContentInput[K]) { setForm((valueNow) => ({ ...valueNow, [key]: value })); setError(null); }
  async function perform(name: string, action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true; setBusy(name); setError(null);
    try { await action(); } catch (cause) { setError(errorMessage(cause)); }
    finally { operation.current = false; setBusy(null); }
  }
  async function persist() {
    const input = formRef.current;
    if (!input.title.trim()) throw new Error("请先填写文章标题。");
    const saved = await request<Content>(contentRef.current ? "PUT" : "POST", contentRef.current ? `/api/contents/${contentRef.current}` : "/api/contents", { ...input, title: input.title.trim() });
    setContentId(saved.id); contentRef.current = saved.id;
    setStored(saved); setSavedSnapshot(JSON.stringify(asInput(saved)));
    setForm((latest) => JSON.stringify(latest) === JSON.stringify(input) ? asInput(saved) : latest);
    await onRefresh();
    return saved;
  }
  function load(entry: Content | "new") {
    setPreflight(null); checkedId.current = null; checkedSignature.current = null; enqueueId.current = null;
    const next = entry === "new" ? emptyArticle() : asInput(entry);
    setContentId(entry === "new" ? null : entry.id); setForm(next); setStored(entry === "new" ? undefined : entry);
    setSavedSnapshot(JSON.stringify(next)); setPendingSelection(null); setRepeatIds([]); setTagInput(""); setError(null); setJob(null); setPreviewOpen(false);
  }
  function choose(entry: Content | "new") {
    if (entry !== "new" && entry.id === contentId) return;
    if (dirty) setPendingSelection(entry); else load(entry);
  }
  async function copyForPolishing() {
    await perform("copy-polish", async () => {
      const source = polishingSource(formRef.current);
      // Save the exact Markdown (including pending edits) before creating a copy.
      const saved = await persist();
      load("new");
      setForm({ ...asInput(saved), inputFormat: "plain", ...source, body: "" });
      onNotice("已复制为新的润色草稿，原 Markdown 稿件已保留。点击“润色生成”继续。");
    });
  }
  async function acceptJob(value: FormatJob) {
    setJob(value);
    if (value.status !== "running") formatRequest.current = null;
    if (value.status === "succeeded" && value.content) {
      const result = value.content;
      if (contentRef.current === result.id && sourceKey(formRef.current) === sourceKey(asInput(result))) {
        setStored(result); setForm((latest) => ({ ...latest, body: result.body })); setSavedSnapshot(JSON.stringify(asInput(result)));
        onNotice("润色已完成，点击右上角“预览”查看排版结果。");
      } else onNotice("润色已完成，原文有新的修改，请重新生成后预览。");
      await onRefresh();
    } else if (value.status === "failed") setError(value.message || "润色未完成，原文已保留，请稍后重试。");
  }
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let active = true;
    let timer: number;
    const poll = async () => {
      try {
        const result = await request<FormatJob>("GET", `/api/format-jobs/${job.id}`);
        if (!active) return;
        if (result.status !== "running") { await acceptJob(result); return; }
        setJob(result);
      } catch (cause) { if (active) setError(errorMessage(cause)); }
      if (active) timer = window.setTimeout(poll, 900);
    };
    timer = window.setTimeout(poll, 500);
    return () => { active = false; window.clearTimeout(timer); };
  }, [job?.id, job?.status]);
  async function formatArticle() {
    await perform("format", async () => {
      if (!formRef.current.sourceText.trim()) throw new Error("请先输入文章原文。");
      if (!formatting?.available) throw new Error(formatting?.message || "免费润色服务暂不可用，请在设置中检查。");
      let pending = formatRequest.current;
      if (pending && (pending.contentId !== contentRef.current || pending.source !== sourceKey(formRef.current))) {
        try { await request("POST", `/api/format-jobs/${pending.requestId}/cancel`); }
        catch (cause) { if (!(typeof cause === "object" && cause !== null && "code" in cause && cause.code === "not_found")) throw cause; }
        pending = null; formatRequest.current = null;
      }
      if (!pending) {
        const saved = await persist();
        pending = { contentId: saved.id, revision: saved.revision, requestId: crypto.randomUUID(), source: sourceKey(asInput(saved)) };
        formatRequest.current = pending;
      }
      try {
        await acceptJob(await request<FormatJob>("POST", `/api/contents/${pending.contentId}/format`, { revision: pending.revision, requestId: pending.requestId }));
      } catch (cause) {
        try { await acceptJob(await request<FormatJob>("GET", `/api/format-jobs/${pending.requestId}`)); }
        catch { setJob(null); throw cause; }
      }
    });
  }
  async function importFile(kind: "article" | "image", purpose?: "cover") {
    await perform("import", async () => {
      const path = await bridge().pickFile(kind);
      if (!path) return;
      const asset = await request<Asset>("POST", "/api/assets/import", { path, kind });
      if (kind === "article") {
        const markdown = /\.(?:md|markdown)$/i.test(asset.name);
        setForm((latest) => ({ ...latest, inputFormat: markdown ? "markdown" : "plain", sourceText: markdown ? "" : asset.text || "", body: markdown ? asset.text || "" : "", title: latest.title || asset.name.replace(/\.(?:md|markdown|txt)$/i, "") }));
      } else if (purpose === "cover") update("coverAssetId", asset.id);
      else {
        const latest = formRef.current;
        const body = latest.inputFormat === "plain" ? latest.sourceText : latest.body;
        const position = editor.current?.selectionStart ?? body.length;
        const end = editor.current?.selectionEnd ?? position;
        const ids = [...new Set([...latest.imageAssetIds, asset.id])];
        const insertion = latest.inputFormat === "plain" ? `\n【图片${ids.indexOf(asset.id) + 1}】\n` : `\n![${asset.name.replace(/[\[\]\\\n\r]/g, "")}](easy-asset://${asset.id})\n`;
        setForm({ ...latest, [latest.inputFormat === "plain" ? "sourceText" : "body"]: body.slice(0, position) + insertion + body.slice(end), imageAssetIds: ids });
      }
    });
  }
  function addTags() {
    const values = tagInput.split(/[,，#\s]+/).filter(Boolean);
    if (values.length) { update("tags", [...new Set([...form.tags, ...values])].slice(0, 20)); setTagInput(""); }
  }
  async function check() {
    await perform("preflight", async () => {
      if (!targets.length) throw new Error("请在账号管理开启需要发布的文章账号。");
      if (!ready) throw new Error("请先润色生成当前原文并检查预览。");
      const accountIds = targets.map((account) => account.id);
      const saved = await persist();
      const expected = JSON.stringify({ contentId: saved.id, form: asInput(saved), targetKey, mode });
      const result = await request<Preflight>("POST", "/api/preflight", { contentId: saved.id, accountIds, mode });
      if (signatureRef.current !== expected) throw new Error("内容或发布账号已变化，请重新检查。");
      setPreflight(result); checkedId.current = saved.id; checkedSignature.current = expected; enqueueId.current = crypto.randomUUID();
    });
  }
  async function publish() {
    await perform("publish", async () => {
      if (!preflight?.ok || !checkedId.current || !enqueueId.current || checkedSignature.current !== signatureRef.current) throw new Error("内容已变化，请重新检查后提交。");
      await request<Batch>("POST", "/api/batches", { contentId: checkedId.current, accountIds: targets.map((account) => account.id), mode, requestId: enqueueId.current });
      setPreflight(null); enqueueId.current = null; setRepeatIds([]); await onRefresh(); onNotice(`已创建${targets.length}个目标的${mode === "draft" ? "平台草稿" : "发布"}任务。`); onTasks();
    });
  }

  return <div className="workspace-page article-workspace">
    <div className="page-heading"><div><div className="eyebrow">CONTENT WORKSPACE</div><h1>文章<span className="heading-count">{entries.length}</span></h1><p>输入纯文本，让 OpenCode 润色，再预览排版后的文章。</p></div><div className="page-heading-actions"><button className="button" onClick={() => setPreviewOpen(true)}><Eye size={17} />预览</button><button className="button primary" disabled={!!busy || running} onClick={() => choose("new")}><Plus size={17} />新建文章</button></div></div>
    <div className="article-library" aria-label="本地文章">{!contentId && <button className="draft-item selected"><FileText size={16} /><span><strong>{form.title || "未命名文章"}</strong><small>尚未保存</small></span></button>}{entries.map((entry) => <button key={entry.id} className={`draft-item ${entry.id === contentId ? "selected" : ""}`} disabled={!!busy || running} onClick={() => choose(entry)}><FileText size={16} /><span><strong>{entry.id === contentId ? form.title || entry.title : entry.title}</strong><small>{dateLabel(entry.updatedAt)}</small></span>{entry.id === contentId && dirty && <span className="unsaved-dot" />}</button>)}</div>
    <section className="editor-panel panel" aria-label="文章编辑器">
      <div className="editor-toolbar"><div className="editor-breadcrumb"><FileText size={16} /><span>{plain ? "原文编辑" : "Markdown 稿件"}</span></div><div className="toolbar-actions"><span className={`save-state ${dirty ? "dirty" : ""}`}>{dirty ? "有未保存修改" : "已保存到本地"}</span>{contentId && <button className="icon-button danger-hover" aria-label="删除本地稿件" disabled={!!busy || running} onClick={() => setDeleteOpen(true)}><Trash2 size={15} /></button>}<button className="button small" disabled={!!busy || running} onClick={() => perform("save", async () => { await persist(); onNotice("稿件已保存到本地。"); })}>{busy === "save" ? <Spinner size={14} /> : <Save size={14} />}保存</button></div></div>
      <InlineError message={error} />
      <fieldset className="editor-fields" disabled={!!busy}>
        <div className="article-metadata"><div className="cover-field"><span className="field-label">封面图片</span><button className="cover-upload" aria-label={form.coverAssetId ? "更换封面" : "上传封面"} disabled={running} onClick={() => importFile("image", "cover")}>{form.coverAssetId ? <AssetImage id={form.coverAssetId} alt="内容封面" /> : <><ImagePlus size={22} /><span>上传封面</span></>}</button>{form.coverAssetId && <button className="text-button muted" onClick={() => update("coverAssetId", null)}>移除封面</button>}</div><div className="article-metadata-fields">
          <label className="title-label"><span className="sr-only">文章标题</span><input className="content-title" placeholder="为你的文章起个标题…" value={form.title} onChange={(event) => update("title", event.target.value)} maxLength={200} /></label>
          <div className="article-metadata-row"><label className="field"><span>作者 <small>可选</small></span><input placeholder="作者名称" value={form.author} onChange={(event) => update("author", event.target.value)} maxLength={100} /></label><label className="field"><span>摘要 <small>可选</small></span><input placeholder="用几句话概括文章内容" value={form.description} onChange={(event) => update("description", event.target.value)} maxLength={5000} /></label></div>
          <div className="field"><label htmlFor="article-tags">话题标签 <small>回车添加</small></label><div className="tag-input">{form.tags.map((tag) => <span className="tag" key={tag}>{tag}<button aria-label={`移除标签 ${tag}`} onClick={() => update("tags", form.tags.filter((value) => value !== tag))}><X size={11} /></button></span>)}<input id="article-tags" value={tagInput} placeholder="添加话题标签…" onChange={(event) => setTagInput(event.target.value)} onBlur={addTags} onKeyDown={(event) => { if (["Enter", ",", "，"].includes(event.key)) { event.preventDefault(); addTags(); } }} /></div></div>
        </div></div>
        <div className="article-body"><div className="body-toolbar"><span className="field-label">{plain ? "文章原文 · 纯文本" : "Markdown 正文"}</span><div className="toolbar-actions"><button className="icon-button" aria-label="导入文章文件" disabled={running} onClick={() => importFile("article")}><Upload size={16} /></button><button className="icon-button" aria-label="插入正文图片" disabled={running} onClick={() => importFile("image")}><ImagePlus size={16} /></button></div></div><textarea ref={editor} className="markdown-editor" aria-label="文章正文" placeholder={plain ? "粘贴或写下纯文本原文，点击“润色生成”，再点击右上角“预览”查看排版结果。" : "编辑 Markdown 正文…"} value={plain ? form.sourceText : form.body} onChange={(event) => update(plain ? "sourceText" : "body", event.target.value)} spellCheck={false} /><div className="editor-footer"><span>{(plain ? form.sourceText : form.body).length.toLocaleString()} 字符</span><span>{plain ? "原文会单独保留" : "Markdown 稿件直接预览"}</span></div></div>
      </fieldset>
      {plain ? <div className="article-format-actions"><div><button className="button primary" disabled={!!busy || running || !form.sourceText.trim()} onClick={formatArticle}>{busy === "format" || running ? <Spinner size={16} /> : <Sparkles size={16} />}{busy === "format" || running ? "正在润色" : "润色生成"}</button>{running && <button className="button" disabled={!!busy} onClick={() => perform("cancel-format", async () => { if (job) await acceptJob(await request<FormatJob>("POST", `/api/format-jobs/${job.id}/cancel`)); })}>取消生成</button>}<button className="text-button" onClick={onSettings}>{formatting?.modelName || "检查免费润色服务"}<ArrowRight size={13} /></button></div><p>OpenCode 会润色措辞、梳理结构，并保留原文事实和图片位置。完成后点击右上角“预览”，查看所选模板的排版结果。微信公众号可在账号管理选择 Wenyan 官方模板。</p>{job && <p role="status">{job.message}</p>}</div> : <div className="article-format-actions"><div><button className="button" disabled={!!busy || running || !form.body.trim()} onClick={copyForPolishing}>{busy === "copy-polish" ? <Spinner size={16} /> : <Sparkles size={16} />}复制为润色草稿</button></div><p>这篇 Markdown 稿件可以直接预览。需要 OpenCode 润色时，复制为新的纯文本草稿；当前原稿会先保存并保留。</p></div>}
    </section>

    <section className="article-publishing panel" aria-label="文章发布设置">
      <div className="article-preview-heading"><div><h2>发布文章</h2><p>使用账号管理中已开启的文章账号，共 {targets.length} 个发布目标。</p></div><button className="button small" onClick={onAccounts}>管理发布账号<ArrowRight size={14} /></button></div>
      <div className="article-publish-mode"><div className="segmented-control"><button className={mode === "publish" ? "active" : ""} disabled={!!busy} onClick={() => setMode("publish")}>发布内容</button><button className={mode === "draft" ? "active" : ""} disabled={!!busy} onClick={() => setMode("draft")}>平台草稿</button></div></div>
      {previous.length > 0 && <div className="article-history">{previous.map((account) => <div key={account.id}><span>{account.label} · {historyLabels[history[account.platformId]]}</span><button className="text-button" disabled={!!busy} onClick={() => setRepeatIds((ids) => [...ids, account.id])}>加入本次再次发布</button></div>)}</div>}
      {targets.length > 0 ? <><div className="article-target-summary" aria-label="本次文章发布账号">{targets.map((account) => <span className="article-target-chip" key={account.id}><PlatformMark platform={platforms.find((platform) => platform.id === account.platformId)} size="small" /><span><strong>{account.label}</strong><small>{platforms.find((platform) => platform.id === account.platformId)?.name}{repeatIds.includes(account.id) ? " · 再次发布" : ""}</small></span></span>)}</div>
        {selectedPlatforms.some((platform) => platform.fields.some((field) => field.kinds.includes("article"))) && <div className="article-platform-fields">{selectedPlatforms.map((platform) => platform.fields.filter((field) => field.kinds.includes("article")).map((field) => <label className="field" key={`${platform.id}:${field.key}`}><span>{platform.name} · {field.label}{field.required && <b className="required-mark">*</b>}</span>{field.type === "select" ? <select disabled={!!busy} value={form.platformOptions[platform.id]?.[field.key] || ""} onChange={(event) => update("platformOptions", { ...form.platformOptions, [platform.id]: { ...form.platformOptions[platform.id], [field.key]: event.target.value } })}><option value="">请选择{field.label}</option>{field.choices?.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select> : <input disabled={!!busy} value={form.platformOptions[platform.id]?.[field.key] || ""} onChange={(event) => update("platformOptions", { ...form.platformOptions, [platform.id]: { ...form.platformOptions[platform.id], [field.key]: event.target.value } })} placeholder={field.hint || field.label} />}</label>))}</div>}
      </> : <div className="article-preview-empty"><Send size={28} /><strong>暂无本次发布账号</strong><p>在账号管理开启支持{mode === "draft" ? "平台草稿" : "文章发布"}的已连接账号。</p><button className="button small" onClick={onAccounts}>前往账号管理</button></div>}
      {preflight && <div className={`preflight-result ${preflight.ok ? "passed" : "blocked"}`} role="status"><strong><ShieldCheck size={15} />{preflight.ok ? "发布前检查通过" : "需要完善以下内容"}</strong>{preflight.targets.map((target) => <div key={target.accountId}>{[...target.errors, ...target.warnings].map((message, index) => <p key={index}><b>{accounts.find((account) => account.id === target.accountId)?.label}：</b>{message}</p>)}</div>)}</div>}
      <div className="article-publication-actions"><span>本次发布到 <strong>{targets.length}</strong> 个账号</span><button className="button check-button" disabled={!!busy || running || !targets.length || !ready} onClick={check}>{busy === "preflight" ? <Spinner size={16} /> : <CheckCheck size={16} />}检查{mode === "draft" ? "草稿" : "发布"}内容</button><button className="button primary" disabled={!!busy || running || !preflight?.ok} onClick={publish}>{busy === "publish" ? <Spinner size={16} /> : mode === "draft" ? <Save size={16} /> : <Send size={16} />}{mode === "draft" ? "保存平台草稿" : "发布到已开启账号"}</button></div>
    </section>
    {previewOpen && <Modal title="文章最终预览" className="article-preview-modal" onClose={() => setPreviewOpen(false)}>
      <p className="modal-description">{previewAccounts.length ? "按账号模板查看排版结果；已关闭或已发布的账号也可以预览，发布范围仍以页面下方的发布账号为准。" : "使用 Wenyan 默认模板预览。连接微信公众号后，可在账号管理中选择它的模板。"}</p>
      <InlineError message={previewError || (job?.status === "failed" ? job.message || error : null)} />
      {previewAccounts.length > 0 && <div className="article-preview-tabs" role="tablist" aria-label="文章账号预览">{previewAccounts.map((account) => <button key={account.id} role="tab" aria-selected={account.id === activeAccount?.id} onClick={() => setPreviewId(account.id)}><PlatformMark platform={platforms.find((platform) => platform.id === account.platformId)} size="small" /><span><strong>{account.label}</strong><small>{account.platformId === "wechat" ? articleTemplates.find((template) => template.id === (account.wechatTemplateId || "clean"))?.name : platforms.find((platform) => platform.id === account.platformId)?.name}</small></span></button>)}</div>}
      <div className="article-final-preview" role={previewAccounts.length ? "tabpanel" : "region"} aria-label={`${activeAccount?.label || "文章"} 最终预览`}>{ready ? <><div className="article-preview-meta"><h3>{form.title || "文章标题"}</h3>{form.author && <span>{form.author}</span>}{form.coverAssetId && <AssetImage id={form.coverAssetId} alt="文章封面" />}</div><MarkdownPreview source={form.body} assetIds={form.imageAssetIds} templateId={activeTemplate} platformId={activePlatform?.id || "wechat"} accountId={activeAccount?.platformId === "wechat" ? activeAccount.id : undefined} onError={setPreviewError} /></> : <div className="article-preview-empty"><FileText size={28} /><strong>{running ? "正在润色文章" : plain ? "请先润色生成，再查看排版结果" : "写下正文，查看最终预览"}</strong><p>{running ? "原文已保留，润色完成后会显示排版结果。" : plain && form.body ? "原文已修改，请返回编辑器重新生成当前内容。" : plain ? "关闭预览，在编辑器输入纯文本并点击“润色生成”。" : "预览完成后，检查内容并发布到已开启的账号。"}</p></div>}</div>
    </Modal>}
    {pendingSelection && <Modal title="保留当前修改？" onClose={() => setPendingSelection(null)}><p className="modal-description">当前稿件还有未保存的修改。继续切换会放弃这些修改。</p><div className="modal-actions"><button className="button" onClick={() => setPendingSelection(null)}>继续编辑</button><button className="button danger" onClick={() => load(pendingSelection)}>放弃修改并切换</button></div></Modal>}
    {deleteOpen && <Modal title="删除本地稿件" onClose={() => setDeleteOpen(false)} busy={!!busy}><p className="modal-description">删除“{form.title}”的本地稿件？平台内容和已有发布任务不会受到影响。</p><InlineError message={error} /><div className="modal-actions"><button className="button" disabled={!!busy} onClick={() => setDeleteOpen(false)}>取消</button><button className="button danger" disabled={!!busy} onClick={() => perform("delete", async () => { await request("DELETE", `/api/contents/${contentId}`); await onRefresh(); setDeleteOpen(false); load("new"); onNotice("本地稿件已删除。"); })}>删除稿件</button></div></Modal>}
  </div>;
}
