import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CheckCheck, ChevronRight, Film, ImagePlus, Plus, Save, Search, Send, ShieldCheck, Trash2, X } from "lucide-react";
import { bridge, errorMessage, request } from "./api";
import { AssetImage, useAssetMetadata } from "./MarkdownPreview";
import ArticleWorkspace from "./ArticleWorkspace";
import { byteLabel, dateLabel, InlineError, Modal, PlatformMark, PublishMethodNotice, Spinner } from "./ui";
import type { Account, ArticleTemplate, Asset, Batch, Content, ContentInput, ContentKind, Platform, Preflight, PublishMode } from "./shared/types";

const newContent = (kind: ContentKind): ContentInput => ({ kind, videoOptionsSource: "account", inputFormat: "markdown", sourceText: "", title: "", body: "", description: "", tags: [], author: "", videoAssetId: null, coverAssetId: null, imageAssetIds: [], platformOptions: {}, articleTemplateId: "clean" });
const asInput = (content: Content): ContentInput => ({ kind: content.kind, videoOptionsSource: "account", inputFormat: content.inputFormat || "markdown", sourceText: content.sourceText || "", title: content.title, body: content.body, description: content.description, tags: content.tags, author: content.author, videoAssetId: content.videoAssetId, coverAssetId: content.coverAssetId, imageAssetIds: content.imageAssetIds, platformOptions: content.platformOptions || {}, articleTemplateId: content.articleTemplateId || "clean" });

const publicationLabels = { submitted: "此前已提交", published: "此前已发布", unknown: "结果待确认" };

interface Props {
  kind: ContentKind;
  contents: Content[];
  platforms: Platform[];
  accounts: Account[];
  articleTemplates: ArticleTemplate[];
  onRefresh: () => Promise<void>;
  onAccounts: () => void;
  onTasks: () => void;
  onNotice: (message: string) => void;
}

export default function DraftWorkspace(props: Props) {
  return props.kind === "article" ? <ArticleWorkspace {...props} /> : <VideoWorkspace {...props} />;
}

function VideoWorkspace({ kind, contents, platforms, accounts, onRefresh, onAccounts, onTasks, onNotice }: Props) {
  const entries = contents.filter((content) => content.kind === kind).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const initial = entries[0];
  const [contentId, setContentId] = useState<string | null>(initial?.id || null);
  const [form, setForm] = useState<ContentInput>(() => initial ? asInput(initial) : newContent(kind));
  const [savedSnapshot, setSavedSnapshot] = useState(() => JSON.stringify(initial ? asInput(initial) : newContent(kind)));
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<PublishMode>("publish");
  const [repeatIds, setRepeatIds] = useState<string[]>([]);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [checkedContent, setCheckedContent] = useState<string | null>(null);
  const [pendingSelection, setPendingSelection] = useState<Content | "new" | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [imported, setImported] = useState<Record<string, Asset>>({});
  const [tagInput, setTagInput] = useState("");
  const operationRef = useRef(false);
  const enqueueId = useRef<string | null>(null);
  const checkedSignature = useRef<string | null>(null);
  const formRef = useRef(form); formRef.current = form;
  const contentRef = useRef(contentId); contentRef.current = contentId;
  const videoMetadata = useAssetMetadata(form.videoAssetId);
  const dirty = JSON.stringify(form) !== savedSnapshot;
  const current = entries.find((entry) => entry.id === contentId);
  const publicationStates = current?.platformPublicationStates || {};
  const kindPlatforms = platforms.filter((platform) => platform.kinds.includes("video"));
  const enabled = accounts.filter((account) => account.defaultSelected !== false && account.status === "ready" && kindPlatforms.some((platform) => platform.id === account.platformId && platform.modes.includes(mode)));
  const targets = enabled.filter((account) => !publicationStates[account.platformId] || repeatIds.includes(account.id));
  const previous = enabled.filter((account) => !!publicationStates[account.platformId] && !repeatIds.includes(account.id));
  const targetKey = JSON.stringify(targets.map((account) => ({ id: account.id, options: Object.entries(account.videoOptions || {}).sort(([a], [b]) => a.localeCompare(b)) })).sort((a, b) => a.id.localeCompare(b.id)));
  const legacyVideoSettings = current?.videoOptionsSource !== "account" && kindPlatforms.some((platform) => (platform.fields || []).some((field) => field.kinds.includes("video") && current?.platformOptions?.[platform.id]?.[field.key]));
  const categoryAvailability = JSON.stringify(targets.filter((account) => account.bilibiliCategories).map((account) => ({ id: account.id, categories: account.bilibiliCategories?.map((choice) => choice.value).sort() })).sort((a, b) => a.id.localeCompare(b.id)));
  const signature = JSON.stringify({ contentId, form, targetKey, mode, categoryAvailability });
  const signatureRef = useRef(signature); signatureRef.current = signature;

  useEffect(() => {
    setPreflight(null);
    setCheckedContent(null);
    checkedSignature.current = null;
    enqueueId.current = null;
  }, [signature]);
  useEffect(() => { setRepeatIds((ids) => ids.filter((id) => enabled.some((account) => account.id === id))); }, [enabled.map((account) => account.id).join(",")]);
  useEffect(() => { setRepeatIds([]); }, [contentId, JSON.stringify(publicationStates)]);

  function update<K extends keyof ContentInput>(key: K, value: ContentInput[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
    setError(null);
  }

  async function perform(name: string, callback: () => Promise<void>) {
    if (operationRef.current) return;
    operationRef.current = true;
    setBusy(name);
    setError(null);
    try { await callback(); } catch (cause) { setError(errorMessage(cause)); }
    finally { operationRef.current = false; setBusy(null); }
  }

  function load(entry: Content | "new") {
    const input = entry === "new" ? newContent(kind) : asInput(entry);
    setContentId(entry === "new" ? null : entry.id);
    setForm(input);
    setSavedSnapshot(JSON.stringify(input));
    setPendingSelection(null);
    setError(null);
    setRepeatIds([]);
    setPreflight(null); setCheckedContent(null); checkedSignature.current = null; enqueueId.current = null;
    setTagInput("");
  }

  function choose(entry: Content | "new") {
    if (entry !== "new" && entry.id === contentId) return;
    if (dirty) setPendingSelection(entry);
    else load(entry);
  }

  async function persist() {
    const input = formRef.current;
    if (!input.title.trim()) throw new Error("请先填写内容标题。");
    const saved = await request<Content>(contentRef.current ? "PUT" : "POST", contentRef.current ? `/api/contents/${contentRef.current}` : "/api/contents", { ...input, title: input.title.trim() });
    setContentId(saved.id); contentRef.current = saved.id;
    setSavedSnapshot(JSON.stringify(asInput(saved)));
    setForm((latest) => JSON.stringify(latest) === JSON.stringify(input) ? asInput(saved) : latest);
    await onRefresh();
    return saved;
  }

  async function importFile(fileKind: "image" | "video") {
    await perform("import", async () => {
      const path = await bridge().pickFile(fileKind);
      if (!path) return;
      const asset = await request<Asset>("POST", "/api/assets/import", { path, kind: fileKind });
      setImported((previous) => ({ ...previous, [asset.id]: asset }));
      update(fileKind === "video" ? "videoAssetId" : "coverAssetId", asset.id);
    });
  }

  function addTag() {
    const values = tagInput.split(/[,，#\s]+/).map((value) => value.trim()).filter(Boolean);
    if (!values.length) return;
    update("tags", [...new Set([...form.tags, ...values])].slice(0, 20));
    setTagInput("");
  }

  async function checkTargets() {
    await perform("preflight", async () => {
      if (!targets.length) throw new Error("请在账号管理开启需要发布的视频账号。");
      const accountIds = targets.map((account) => account.id);
      const saved = await persist();
      const expected = JSON.stringify({ contentId: saved.id, form: asInput(saved), targetKey, mode, categoryAvailability });
      const result = await request<Preflight>("POST", "/api/preflight", { contentId: saved.id, accountIds, mode });
      if (signatureRef.current !== expected) throw new Error("内容或发布账号已变化（含账号视频设置），请重新检查。");
      setCheckedContent(saved.id);
      checkedSignature.current = expected;
      setPreflight(result);
      enqueueId.current = crypto.randomUUID();
    });
  }

  async function publish() {
    await perform("publish", async () => {
      if (!preflight?.ok || !checkedContent || !enqueueId.current || checkedSignature.current !== signatureRef.current) throw new Error("内容已变化，请重新检查后提交。");
      // Keep this id on network failure: repeating the click retries the same batch request.
      await request<Batch>("POST", "/api/batches", { contentId: checkedContent, accountIds: targets.map((account) => account.id), mode, requestId: enqueueId.current });
      setPreflight(null);
      enqueueId.current = null;
      setRepeatIds([]);
      await onRefresh();
      onNotice(`已创建${targets.length}个目标的${mode === "draft" ? "平台草稿" : "发布"}任务。`);
      onTasks();
    });
  }

  const video = form.videoAssetId ? imported[form.videoAssetId] || videoMetadata : null;
  const visibleEntries = entries.filter((entry) => entry.title.toLowerCase().includes(search.toLowerCase()));

  return <div className="workspace-page video-workspace">
    <div className="page-heading"><div><div className="eyebrow">CONTENT WORKSPACE</div><h1>视频<span className="heading-count">{entries.length}</span></h1><p>上传一支视频，让每个平台都看见你的创作。</p></div>
      <button className="button primary" onClick={() => choose("new")} disabled={!!busy}><Plus size={17} />新建视频</button>
    </div>

    <div className="workspace-grid">
      <aside className="draft-library panel">
        <div className="panel-heading"><h2>本地稿件</h2><span>{entries.length}</span></div>
        <label className="search-field"><Search size={15} /><input aria-label="搜索本地稿件" placeholder="搜索标题…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <div className="draft-items">
          {!contentId && <button className="draft-item selected" disabled={!!busy}><span className="draft-icon"><Film size={17} /></span><span><strong>{form.title || "未命名视频"}</strong><small>尚未保存</small></span><span className="unsaved-dot" /></button>}
          {visibleEntries.map((entry) => <button key={entry.id} className={`draft-item ${contentId === entry.id ? "selected" : ""}`} onClick={() => choose(entry)} disabled={!!busy}><span className="draft-icon"><Film size={17} /></span><span><strong>{contentId === entry.id ? form.title || entry.title : entry.title}</strong><small>{dateLabel(entry.updatedAt)}</small></span>{contentId === entry.id && dirty && <span className="unsaved-dot" />}</button>)}
          {!visibleEntries.length && entries.length > 0 && <p className="library-empty">没有找到相关稿件</p>}
        </div>
        <div className="library-footer"><span className="tiny-dot" />稿件保存在这台电脑</div>
      </aside>

      <div className="video-main">
      <section className="editor-panel panel" aria-label="视频编辑器">
        <div className="editor-toolbar"><div className="editor-breadcrumb"><Film size={16} /><span>视频</span><ChevronRight size={13} /><span>{contentId ? "编辑稿件" : "新稿件"}</span></div>
          <div className="toolbar-actions"><span className={`save-state ${dirty ? "dirty" : ""}`}>{dirty ? "有未保存修改" : contentId ? "已保存到本地" : "本地草稿"}</span>{contentId && <button className="icon-button danger-hover" aria-label="删除本地稿件" title="删除本地稿件" onClick={() => setDeleteOpen(true)} disabled={!!busy}><Trash2 size={15} /></button>}<button className="button small" onClick={() => perform("save", async () => { await persist(); onNotice("稿件已保存到本地。"); })} disabled={!!busy}>{busy === "save" ? <Spinner size={14} /> : <Save size={14} />}保存</button></div>
        </div>
        <InlineError message={error} />
        <fieldset className="editor-fields" disabled={!!busy}>
          <label className="title-label"><span className="sr-only">视频标题</span><input className="content-title" placeholder="为你的视频起个标题…" value={form.title} onChange={(event) => update("title", event.target.value)} maxLength={200} /></label>
          <div className="title-meta"><span>标题会同步到已开启的视频账号</span><span>{form.title.length} 字</span></div>

          <div className="video-section">
            <button className={`video-upload ${form.videoAssetId ? "has-video" : ""}`} onClick={() => importFile("video")}>
              <span className="upload-icon"><Film size={28} /></span><strong>{video?.name || (form.videoAssetId ? "已导入视频文件" : "点击导入视频")}</strong><span>{video ? `${byteLabel(video.size)} · 点击更换` : form.videoAssetId ? "文件已保存在本地素材库 · 点击更换" : "支持 MP4、MOV 等常见视频格式"}</span>{form.videoAssetId && <span className="video-imported"><Check size={14} />已导入</span>}
            </button>
            <label className="field"><span>视频描述</span><textarea placeholder="介绍视频内容，让观众更了解这次创作…" value={form.description} onChange={(event) => update("description", event.target.value)} rows={4} maxLength={5000} /></label>
          </div>

          <div className="metadata-section"><div className="section-label"><h3>内容信息</h3><span>按平台要求检查后发布</span></div>
            <div className="metadata-grid"><div className="cover-field"><span className="field-label">封面图片</span><button className="cover-upload" onClick={() => importFile("image")} aria-label={form.coverAssetId ? "更换封面" : "上传封面"}>{form.coverAssetId ? <AssetImage id={form.coverAssetId} alt="内容封面" /> : <><ImagePlus size={22} /><span>上传封面</span></>}</button>{form.coverAssetId && <button className="text-button muted" onClick={() => update("coverAssetId", null)}>移除封面</button>}</div>
              <div className="metadata-inputs">
                <div className="field"><label htmlFor={`${kind}-tags`}>话题标签 <small>回车添加</small></label><div className="tag-input">{form.tags.map((tag) => <span className="tag" key={tag}>{tag}<button aria-label={`移除标签 ${tag}`} onClick={() => update("tags", form.tags.filter((value) => value !== tag))}><X size={11} /></button></span>)}<input id={`${kind}-tags`} value={tagInput} placeholder={form.tags.length ? "添加标签" : "添加话题标签…"} onChange={(event) => setTagInput(event.target.value)} onBlur={addTag} onKeyDown={(event) => { if (event.key === "Enter" || event.key === "," || event.key === "，") { event.preventDefault(); addTag(); } }} /></div></div>
              </div>
            </div>
          </div>
        </fieldset>
        {current && <div className="local-revision">本地版本 {current.revision} · 更新于 {dateLabel(current.updatedAt)}</div>}
      </section>

      <section className="article-publishing video-publishing panel" aria-label="视频发布">
        <div className="article-preview-heading"><div><h2>发布视频</h2><p>使用账号管理中已开启的视频账号，共 {targets.length} 个发布目标。</p></div><button className="button small" onClick={onAccounts}>管理发布账号<ArrowRight size={14} /></button></div>
        <div className="article-publish-mode"><div className="segmented-control"><button className={mode === "publish" ? "active" : ""} onClick={() => setMode("publish")} disabled={!!busy}>发布内容</button><button className={mode === "draft" ? "active" : ""} onClick={() => setMode("draft")} disabled={!!busy}>平台草稿</button></div></div>
        {previous.length > 0 && <div className="article-history video-history">{previous.map((account) => <div key={account.id}><span>{account.label} · {publicationLabels[publicationStates[account.platformId]]}</span><button className="text-button" disabled={!!busy} onClick={() => setRepeatIds((ids) => [...ids, account.id])}>加入本次再次发布</button></div>)}</div>}
        {targets.length > 0 ? <div className="video-targets" aria-label="本次视频发布账号">{targets.map((account) => <div key={account.id} className="video-target-summary"><PlatformMark platform={platforms.find((platform) => platform.id === account.platformId)} size="small" /><span><strong>{account.label}</strong><small>{platforms.find((platform) => platform.id === account.platformId)?.name}{repeatIds.includes(account.id) ? " · 再次发布" : ""}</small></span>{repeatIds.includes(account.id) && <button className="text-button" disabled={!!busy} onClick={() => setRepeatIds((ids) => ids.filter((id) => id !== account.id))}>取消再次发布</button>}</div>)}</div> : <div className="article-preview-empty"><Send size={28} /><strong>暂无本次发布账号</strong><p>在账号管理开启支持{mode === "draft" ? "平台草稿" : "视频发布"}的已连接账号。</p><button className="button small" onClick={onAccounts}>前往账号管理</button></div>}
        <div className="video-account-settings-note"><p>视频分区、原创或转载等发布设置在账号管理中设置，每个账号分别使用自己的设置。</p>{legacyVideoSettings && <p>本次使用账号管理中的视频设置，旧稿设置已保留。</p>}<button className="text-button" onClick={onAccounts}>前往账号管理设置<ArrowRight size={13} /></button></div>
        <PublishMethodNotice platforms={kindPlatforms.filter((platform) => targets.some((account) => account.platformId === platform.id))} mode={mode} />
        {preflight && <div className={`preflight-result ${preflight.ok ? "passed" : "blocked"}`} role="status"><strong><ShieldCheck size={15} />{preflight.ok ? "发布前检查通过" : "需要完善以下内容"}</strong>{preflight.targets.map((target) => <div key={target.accountId}>{[...target.errors, ...target.warnings].map((message, index) => <p key={index}><b>{accounts.find((account) => account.id === target.accountId)?.label}：</b>{message}</p>)}</div>)}{preflight.ok && !preflight.targets.some((target) => target.warnings.length) && <p>已核对本次账号及平台内容要求。</p>}</div>}
        <div className="article-publication-actions"><span>本次发布到 <strong>{targets.length}</strong> 个账号</span><button className="button check-button" disabled={!!busy || !targets.length} onClick={checkTargets}>{busy === "preflight" ? <Spinner size={16} /> : <CheckCheck size={16} />}检查{mode === "draft" ? "草稿" : "发布"}内容</button><button className="button primary" disabled={!!busy || !preflight?.ok || checkedSignature.current !== signature} onClick={publish}>{busy === "publish" ? <Spinner size={16} /> : mode === "draft" ? <Save size={16} /> : <Send size={16} />}{mode === "draft" ? "保存平台草稿" : "创建发布任务"}</button></div>
      </section>
      </div>
    </div>

    {pendingSelection && <Modal title="保留当前修改？" onClose={() => setPendingSelection(null)}><p className="modal-description">当前稿件还有未保存的修改。继续切换会放弃这些修改。</p><div className="modal-actions"><button className="button" onClick={() => setPendingSelection(null)}>继续编辑</button><button className="button danger" onClick={() => load(pendingSelection)}>放弃修改并切换</button></div></Modal>}
    {deleteOpen && <Modal title="删除本地稿件" onClose={() => setDeleteOpen(false)} busy={!!busy}><p className="modal-description">删除“{form.title}”的本地稿件？平台内容和已有发布任务不会受到影响。</p><InlineError message={error} /><div className="modal-actions"><button className="button" onClick={() => setDeleteOpen(false)} disabled={!!busy}>取消</button><button className="button danger" disabled={!!busy} onClick={() => perform("delete", async () => { await request("DELETE", `/api/contents/${contentId}`); await onRefresh(); setDeleteOpen(false); load("new"); onNotice("本地稿件已删除。"); })}>{busy === "delete" && <Spinner />}删除稿件</button></div></Modal>}
  </div>;
}
