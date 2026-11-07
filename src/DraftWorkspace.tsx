import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CheckCheck, ChevronRight, FileText, Film, ImagePlus, MoreHorizontal, Plus, Save, Search, Send, ShieldCheck, Trash2, Upload, X } from "lucide-react";
import { bridge, errorMessage, request } from "./api";
import MarkdownPreview, { ArticleTemplateThumbnail, AssetImage, useAssetMetadata } from "./MarkdownPreview";
import { byteLabel, dateLabel, InlineError, Modal, PlatformMark, Spinner } from "./ui";
import type { Account, ArticleTemplate, Asset, Batch, Content, ContentInput, ContentKind, Platform, PlatformField, Preflight, PublishMode } from "./shared/types";

const newContent = (kind: ContentKind): ContentInput => ({ kind, title: "", body: "", description: "", tags: [], author: "", videoAssetId: null, coverAssetId: null, imageAssetIds: [], platformOptions: {}, articleTemplateId: "clean" });
const asInput = (content: Content): ContentInput => ({ kind: content.kind, title: content.title, body: content.body, description: content.description, tags: content.tags, author: content.author, videoAssetId: content.videoAssetId, coverAssetId: content.coverAssetId, imageAssetIds: content.imageAssetIds, platformOptions: content.platformOptions || {}, articleTemplateId: content.articleTemplateId || "clean" });

function defaultTargets(content: Content | undefined, kind: ContentKind, accounts: Account[], platforms: Platform[], mode: PublishMode) {
  const history = content?.platformPublicationStates || {};
  return accounts.filter((account) => account.status === "ready" && account.defaultSelected !== false && !history[account.platformId] && platforms.some((platform) => platform.id === account.platformId && platform.kinds.includes(kind) && platform.modes.includes(mode))).map((account) => account.id);
}

const publicationLabels = { submitted: "此前已提交", published: "此前已发布", unknown: "结果待确认" };

function choicesFor(field: PlatformField, accounts: Account[]) {
  if (field.dynamicChoices !== "bilibiliCategories") return field.choices || [];
  const categories = accounts.map((account) => account.bilibiliCategories || []);
  return (categories[0] || []).filter((choice) => categories.every((items) => items.some((item) => item.value === choice.value)));
}

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

export default function DraftWorkspace({ kind, contents, platforms, accounts, articleTemplates, onRefresh, onAccounts, onTasks, onNotice }: Props) {
  const entries = contents.filter((content) => content.kind === kind).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const initial = entries[0];
  const [contentId, setContentId] = useState<string | null>(initial?.id || null);
  const [form, setForm] = useState<ContentInput>(() => initial ? asInput(initial) : newContent(kind));
  const [savedSnapshot, setSavedSnapshot] = useState(() => JSON.stringify(initial ? asInput(initial) : newContent(kind)));
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"edit" | "preview">("edit");
  const [previewPlatform, setPreviewPlatform] = useState("");
  const [mode, setMode] = useState<PublishMode>("publish");
  const [selected, setSelected] = useState<string[]>(() => defaultTargets(initial, kind, accounts, platforms, "publish"));
  const manualTargets = useRef<Record<string, boolean>>({});
  const previousPreferences = useRef<Record<string, boolean>>(Object.fromEntries(accounts.map((account) => [account.id, account.defaultSelected !== false])));
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [checkedContent, setCheckedContent] = useState<string | null>(null);
  const [pendingSelection, setPendingSelection] = useState<Content | "new" | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [imported, setImported] = useState<Record<string, Asset>>({});
  const [tagInput, setTagInput] = useState("");
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const operationRef = useRef(false);
  const enqueueId = useRef<string | null>(null);
  const videoMetadata = useAssetMetadata(form.videoAssetId);
  const dirty = JSON.stringify(form) !== savedSnapshot;
  const article = kind === "article";
  const current = entries.find((entry) => entry.id === contentId);
  const publicationStates = current?.platformPublicationStates || {};
  const publicationKey = JSON.stringify(publicationStates);
  const previousPublication = useRef({ contentId, key: publicationKey });
  const kindPlatforms = platforms.filter((platform) => platform.kinds.includes(kind));
  const relevantAccounts = accounts.filter((account) => kindPlatforms.some((platform) => platform.id === account.platformId));
  const readyAccounts = relevantAccounts.filter((account) => account.status === "ready" && platforms.find((platform) => platform.id === account.platformId)?.modes.includes(mode));
  const selectedPlatforms = kindPlatforms.filter((platform) => relevantAccounts.some((account) => selected.includes(account.id) && account.platformId === platform.id));
  const categoryAvailability = JSON.stringify(relevantAccounts.filter((account) => selected.includes(account.id) && account.bilibiliCategories).map((account) => ({ id: account.id, categories: account.bilibiliCategories?.map((choice) => choice.value) })));

  useEffect(() => {
    setPreflight(null);
    setCheckedContent(null);
    enqueueId.current = null;
  }, [form, selected, mode, categoryAvailability]);

  // Reconcile actual preference/availability changes without resetting manual
  // choices, preflight or unsaved text on every routine state poll.
  const availability = readyAccounts.map((account) => account.id).sort().join(",");
  const preferenceKey = JSON.stringify(accounts.map((account) => [account.id, account.defaultSelected !== false]).sort());
  useEffect(() => {
    const preferences = Object.fromEntries(accounts.map((account) => [account.id, account.defaultSelected !== false]));
    for (const account of accounts) {
      if (account.id in previousPreferences.current && previousPreferences.current[account.id] !== preferences[account.id]) {
        delete manualTargets.current[account.id];
      }
    }
    previousPreferences.current = preferences;
    const previous = previousPublication.current;
    previousPublication.current = { contentId, key: publicationKey };
    if (previous.contentId === contentId && previous.key !== publicationKey) {
      const oldStates = JSON.parse(previous.key) as Content["platformPublicationStates"];
      for (const account of accounts) {
        if (publicationStates[account.platformId] && publicationStates[account.platformId] !== oldStates[account.platformId]) delete manualTargets.current[account.id];
      }
    }
    const next = readyAccounts.filter((account) => manualTargets.current[account.id] ?? (preferences[account.id] && !publicationStates[account.platformId])).map((account) => account.id);
    setSelected((ids) => ids.length === next.length && ids.every((id) => next.includes(id)) ? ids : next);
  }, [availability, preferenceKey, contentId, publicationKey, mode]);

  function resetTargets(content: Content | undefined) {
    manualTargets.current = {};
    setSelected(defaultTargets(content, kind, accounts, platforms, mode));
  }

  function selectTarget(identifier: string, checked: boolean) {
    manualTargets.current[identifier] = checked;
    setSelected((ids) => checked ? [...new Set([...ids, identifier])] : ids.filter((id) => id !== identifier));
  }

  function selectAllTargets() {
    const checked = selected.length !== readyAccounts.length;
    for (const account of readyAccounts) manualTargets.current[account.id] = checked;
    setSelected(checked ? readyAccounts.map((account) => account.id) : []);
  }

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
    resetTargets(entry === "new" ? undefined : entry);
    setTagInput("");
  }

  function choose(entry: Content | "new") {
    if (entry !== "new" && entry.id === contentId) {
      if (Object.keys(entry.platformPublicationStates || {}).length) resetTargets(entry);
      return;
    }
    if (dirty) setPendingSelection(entry);
    else load(entry);
  }

  async function persist() {
    if (!form.title.trim()) throw new Error("请先填写内容标题。");
    const input = { ...form, title: form.title.trim() };
    const saved = await request<Content>(contentId ? "PUT" : "POST", contentId ? `/api/contents/${contentId}` : "/api/contents", input);
    setContentId(saved.id);
    setSavedSnapshot(JSON.stringify(form));
    await onRefresh();
    return saved;
  }

  async function importFile(fileKind: "image" | "video" | "article", purpose?: "body" | "cover") {
    await perform("import", async () => {
      const path = await bridge().pickFile(fileKind);
      if (!path) return;
      const asset = await request<Asset>("POST", "/api/assets/import", { path, kind: fileKind });
      setImported((previous) => ({ ...previous, [asset.id]: asset }));
      if (fileKind === "article") {
        setForm((previous) => ({ ...previous, body: asset.text || "", title: previous.title || asset.name.replace(/\.(?:md|markdown|txt)$/i, "") }));
      } else if (fileKind === "video") update("videoAssetId", asset.id);
      else if (purpose === "cover") update("coverAssetId", asset.id);
      else {
        const position = bodyRef.current?.selectionStart ?? form.body.length;
        const end = bodyRef.current?.selectionEnd ?? position;
        const alt = asset.name.replace(/[\[\]\\\n\r]/g, "");
        const insertion = `\n![${alt}](easy-asset://${asset.id})\n`;
        setForm((previous) => ({ ...previous, body: previous.body.slice(0, position) + insertion + previous.body.slice(end), imageAssetIds: [...new Set([...previous.imageAssetIds, asset.id])] }));
        setTab("edit");
      }
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
      if (!selected.length) throw new Error("请至少选择一个已连接的发布账号。");
      const saved = await persist();
      const result = await request<Preflight>("POST", "/api/preflight", { contentId: saved.id, accountIds: selected, mode });
      setCheckedContent(saved.id);
      setPreflight(result);
      enqueueId.current = crypto.randomUUID();
    });
  }

  async function publish() {
    await perform("publish", async () => {
      if (!preflight?.ok || !checkedContent || !enqueueId.current) throw new Error("内容已变化，请重新检查后提交。");
      // Keep this id on network failure: repeating the click retries the same batch request.
      await request<Batch>("POST", "/api/batches", { contentId: checkedContent, accountIds: selected, mode, requestId: enqueueId.current });
      setPreflight(null);
      enqueueId.current = null;
      await onRefresh();
      onNotice(`已创建${selected.length}个目标的${mode === "draft" ? "平台草稿" : "发布"}任务。`);
      onTasks();
    });
  }

  const video = form.videoAssetId ? imported[form.videoAssetId] || videoMetadata : null;
  const visibleEntries = entries.filter((entry) => entry.title.toLowerCase().includes(search.toLowerCase()));

  return <div className="workspace-page">
    <div className="page-heading"><div><div className="eyebrow">CONTENT WORKSPACE</div><h1>{article ? "文章" : "视频"}<span className="heading-count">{entries.length}</span></h1><p>{article ? "写好一篇文章，把内容带到更多平台。" : "上传一支视频，让每个平台都看见你的创作。"}</p></div>
      <button className="button primary" onClick={() => choose("new")} disabled={!!busy}><Plus size={17} />新建{article ? "文章" : "视频"}</button>
    </div>

    <div className="workspace-grid">
      <aside className="draft-library panel">
        <div className="panel-heading"><h2>本地稿件</h2><span>{entries.length}</span></div>
        <label className="search-field"><Search size={15} /><input aria-label="搜索本地稿件" placeholder="搜索标题…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <div className="draft-items">
          {!contentId && <button className="draft-item selected" disabled={!!busy}><span className="draft-icon">{article ? <FileText size={17} /> : <Film size={17} />}</span><span><strong>{form.title || `未命名${article ? "文章" : "视频"}`}</strong><small>尚未保存</small></span><span className="unsaved-dot" /></button>}
          {visibleEntries.map((entry) => <button key={entry.id} className={`draft-item ${contentId === entry.id ? "selected" : ""}`} onClick={() => choose(entry)} disabled={!!busy}><span className="draft-icon">{article ? <FileText size={17} /> : <Film size={17} />}</span><span><strong>{contentId === entry.id ? form.title || entry.title : entry.title}</strong><small>{dateLabel(entry.updatedAt)}</small></span>{contentId === entry.id && dirty && <span className="unsaved-dot" />}</button>)}
          {!visibleEntries.length && entries.length > 0 && <p className="library-empty">没有找到相关稿件</p>}
        </div>
        <div className="library-footer"><span className="tiny-dot" />稿件保存在这台电脑</div>
      </aside>

      <section className="editor-panel panel" aria-label={article ? "文章编辑器" : "视频编辑器"}>
        <div className="editor-toolbar"><div className="editor-breadcrumb">{article ? <FileText size={16} /> : <Film size={16} />}<span>{article ? "文章" : "视频"}</span><ChevronRight size={13} /><span>{contentId ? "编辑稿件" : "新稿件"}</span></div>
          <div className="toolbar-actions"><span className={`save-state ${dirty ? "dirty" : ""}`}>{dirty ? "有未保存修改" : contentId ? "已保存到本地" : "本地草稿"}</span>{contentId && <button className="icon-button danger-hover" aria-label="删除本地稿件" title="删除本地稿件" onClick={() => setDeleteOpen(true)} disabled={!!busy}><Trash2 size={15} /></button>}<button className="button small" onClick={() => perform("save", async () => { await persist(); onNotice("稿件已保存到本地。"); })} disabled={!!busy}>{busy === "save" ? <Spinner size={14} /> : <Save size={14} />}保存</button></div>
        </div>
        <InlineError message={error} />
        <fieldset className="editor-fields" disabled={!!busy}>
          <label className="title-label"><span className="sr-only">{article ? "文章标题" : "视频标题"}</span><input className="content-title" placeholder={article ? "为你的文章起个标题…" : "为你的视频起个标题…"} value={form.title} onChange={(event) => update("title", event.target.value)} maxLength={200} /></label>
          <div className="title-meta"><span>{article ? "好内容，从一个好标题开始" : "标题会同步到你选择的平台"}</span><span>{form.title.length} 字</span></div>

          {article ? <div className="article-body">
            <div className="article-template-picker"><div className="section-label"><h3>文章模板</h3><span>选择版式，实时预览</span></div><div className="template-options" role="group" aria-label="文章模板">{articleTemplates.map((template) => <button type="button" key={template.id} className={`template-option ${form.articleTemplateId === template.id ? "selected" : ""}`} aria-pressed={form.articleTemplateId === template.id} onClick={() => { update("articleTemplateId", template.id); setTab("preview"); }}><ArticleTemplateThumbnail templateId={template.id} /><span><strong>{template.name}</strong><small>{template.description}</small></span>{form.articleTemplateId === template.id && <Check size={14} />}</button>)}</div></div>
            <div className="body-toolbar"><div className="segmented-control"><button className={tab === "edit" ? "active" : ""} onClick={() => setTab("edit")}>编辑</button><button className={tab === "preview" ? "active" : ""} onClick={() => setTab("preview")}>预览</button></div><div className="toolbar-actions"><span className="markdown-hint">Markdown</span><button className="icon-button" aria-label="导入文章文件" title="导入 Markdown / 文本" onClick={() => importFile("article")}><Upload size={16} /></button><button className="icon-button" aria-label="插入正文图片" title="插入正文图片" onClick={() => importFile("image", "body")}><ImagePlus size={16} /></button></div></div>
            {tab === "edit" ? <textarea ref={bodyRef} className="markdown-editor" aria-label="文章正文" placeholder={"从这里开始写作…\n\n支持 Markdown 格式：\n## 小标题\n**重点内容**\n\n点击右上角图片按钮，插入本地图片。"} value={form.body} onChange={(event) => update("body", event.target.value)} spellCheck={false} /> : <><label className="preview-platform"><span>预览效果</span><select aria-label="文章预览平台" value={previewPlatform} onChange={(event) => setPreviewPlatform(event.target.value)}><option value="">模板效果</option>{kindPlatforms.map((platform) => <option key={platform.id} value={platform.id}>{platform.name}</option>)}</select></label><MarkdownPreview source={form.body} assetIds={form.imageAssetIds} templateId={form.articleTemplateId} platformId={previewPlatform || undefined} onError={setError} /></>}
            <div className="editor-footer"><span>{form.body.length.toLocaleString()} 字符</span><span>正文图片通过导入按钮添加</span></div>
          </div> : <div className="video-section">
            <button className={`video-upload ${form.videoAssetId ? "has-video" : ""}`} onClick={() => importFile("video")}>
              <span className="upload-icon"><Film size={28} /></span><strong>{video?.name || (form.videoAssetId ? "已导入视频文件" : "点击导入视频")}</strong><span>{video ? `${byteLabel(video.size)} · 点击更换` : form.videoAssetId ? "文件已保存在本地素材库 · 点击更换" : "支持 MP4、MOV 等常见视频格式"}</span>{form.videoAssetId && <span className="video-imported"><Check size={14} />已导入</span>}
            </button>
            <label className="field"><span>视频描述</span><textarea placeholder="介绍视频内容，让观众更了解这次创作…" value={form.description} onChange={(event) => update("description", event.target.value)} rows={4} maxLength={5000} /></label>
          </div>}

          <div className="metadata-section"><div className="section-label"><h3>内容信息</h3><span>按平台要求检查后发布</span></div>
            <div className="metadata-grid"><div className="cover-field"><span className="field-label">封面图片</span><button className="cover-upload" onClick={() => importFile("image", "cover")} aria-label={form.coverAssetId ? "更换封面" : "上传封面"}>{form.coverAssetId ? <AssetImage id={form.coverAssetId} alt="内容封面" /> : <><ImagePlus size={22} /><span>上传封面</span></>}</button>{form.coverAssetId && <button className="text-button muted" onClick={() => update("coverAssetId", null)}>移除封面</button>}</div>
              <div className="metadata-inputs">{article && <><label className="field"><span>作者 <small>可选</small></span><input placeholder="作者名称" value={form.author} onChange={(event) => update("author", event.target.value)} maxLength={100} /></label><label className="field"><span>摘要 <small>可选</small></span><textarea placeholder="用几句话概括文章内容" value={form.description} onChange={(event) => update("description", event.target.value)} rows={2} maxLength={5000} /></label></>}
                <div className="field"><label htmlFor={`${kind}-tags`}>话题标签 <small>回车添加</small></label><div className="tag-input">{form.tags.map((tag) => <span className="tag" key={tag}>{tag}<button aria-label={`移除标签 ${tag}`} onClick={() => update("tags", form.tags.filter((value) => value !== tag))}><X size={11} /></button></span>)}<input id={`${kind}-tags`} value={tagInput} placeholder={form.tags.length ? "添加标签" : "添加话题标签…"} onChange={(event) => setTagInput(event.target.value)} onBlur={addTag} onKeyDown={(event) => { if (event.key === "Enter" || event.key === "," || event.key === "，") { event.preventDefault(); addTag(); } }} /></div></div>
              </div>
            </div>
          </div>
        </fieldset>
        {current && <div className="local-revision">本地版本 {current.revision} · 更新于 {dateLabel(current.updatedAt)}</div>}
      </section>

      <aside className="publish-panel panel">
        <div className="panel-heading"><h2><Send size={16} />发布到平台</h2><span className="count-pill">{selected.length}</span></div>
        <div className="publish-mode"><button className={mode === "publish" ? "active" : ""} onClick={() => setMode("publish")} disabled={!!busy}>发布内容</button><button className={mode === "draft" ? "active" : ""} onClick={() => setMode("draft")} disabled={!!busy}>平台草稿</button></div>
        <p className="publish-description">{mode === "draft" ? "保存到支持草稿的平台，稍后到平台编辑发布。" : "选择账号后检查内容，为每个目标创建独立任务。"}</p>
        {Object.keys(publicationStates).length > 0 && <div className="republish-note"><strong>继续发布这份稿件</strong><p>已有提交记录的平台默认不选。可以手动勾选，再次发布新任务。</p><button className="text-button" disabled={!!busy} onClick={() => resetTargets(current)}>选择尚未发布的平台</button></div>}
        <div className="target-heading"><span>选择账号</span><button className="text-button" onClick={selectAllTargets} disabled={!!busy || !readyAccounts.length}>{selected.length && selected.length === readyAccounts.length ? "取消全选" : "全选"}</button></div>
        <div className="publish-targets">{relevantAccounts.map((account) => {
          const platform = platforms.find((item) => item.id === account.platformId)!;
          const supported = platform.modes.includes(mode);
          const available = account.status === "ready" && supported;
          const previousStatus = publicationStates[platform.id];
          return <label key={account.id} className={`target-card ${selected.includes(account.id) ? "selected" : ""} ${!available ? "unavailable" : ""}`}><input type="checkbox" checked={selected.includes(account.id)} disabled={!available || !!busy} onChange={(event) => selectTarget(account.id, event.target.checked)} /><PlatformMark platform={platform} size="small" /><span className="target-info"><strong>{account.label}</strong><small>{platform.name}{!supported ? ` · 不支持${mode === "draft" ? "草稿" : "直接发布"}` : account.status !== "ready" ? " · 请先连接" : ""}</small>{previousStatus && <span className={`publication-history ${previousStatus}`}>{publicationLabels[previousStatus]}</span>}</span></label>;
        })}
          {!relevantAccounts.length && <div className="no-targets"><span className="small-empty-icon"><Send size={22} /></span><strong>添加你的第一个账号</strong><p>连接支持{article ? "文章" : "视频"}的平台后，即可批量发布。</p><button className="button small" onClick={onAccounts}><Plus size={14} />添加账号</button></div>}
        </div>
        {relevantAccounts.length > 0 && <button className="manage-accounts text-button" onClick={onAccounts}>管理发布账号<ArrowRight size={13} /></button>}
        {selectedPlatforms.map((platform) => {
          const fields = (platform.fields || []).filter((field) => field.kinds.includes(kind));
          const targetAccounts = relevantAccounts.filter((account) => selected.includes(account.id) && account.platformId === platform.id);
          return fields.length > 0 && <div className="platform-options" key={platform.id}><h3>{platform.name}设置</h3>{fields.map((field) => {
            const choices = choicesFor(field, targetAccounts);
            const value = form.platformOptions[platform.id]?.[field.key] || "";
            const missingChoices = field.dynamicChoices && !choices.length;
            const invalidChoice = field.dynamicChoices && value && !choices.some((choice) => choice.value === value);
            const choiceHint = missingChoices
              ? targetAccounts.some((account) => !account.bilibiliCategories?.length) ? "连接或验证账号以获取分区。" : "所选账号没有共同可用的视频分区，请调整账号选择。"
              : invalidChoice ? "原选分区不适用于当前账号，请重新选择。" : null;
            return <label className="field" key={field.key}><span>{field.label}{field.required && <b className="required-mark">*</b>}</span>{field.type === "select" ? <select disabled={!!busy || !!missingChoices} value={invalidChoice ? "" : value} onChange={(event) => update("platformOptions", { ...form.platformOptions, [platform.id]: { ...form.platformOptions[platform.id], [field.key]: event.target.value } })}><option value="">请选择{field.label}</option>{choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select> : <input disabled={!!busy} value={value} onChange={(event) => update("platformOptions", { ...form.platformOptions, [platform.id]: { ...form.platformOptions[platform.id], [field.key]: event.target.value } })} placeholder={field.hint || field.label} />}{choiceHint && <small className="field-hint" role="status">{choiceHint}</small>}{field.hint && <small className="field-hint">{field.hint}</small>}</label>;
          })}</div>;
        })}
        <div className="supported-platforms"><span>支持{article ? "文章" : "视频"}的平台</span><div>{kindPlatforms.map((platform) => <span key={platform.id} title={platform.description}><PlatformMark platform={platform} size="small" />{platform.name}</span>)}</div></div>

        {preflight && <div className={`preflight-result ${preflight.ok ? "passed" : "blocked"}`} role="status"><strong>{preflight.ok ? <ShieldCheck size={15} /> : <MoreHorizontal size={15} />}{preflight.ok ? "发布前检查通过" : "需要完善以下内容"}</strong>{preflight.targets.map((target) => <div key={target.accountId}>{[...target.errors, ...target.warnings].map((message, index) => <p key={index}><b>{accounts.find((account) => account.id === target.accountId)?.label}：</b>{message}</p>)}</div>)}{preflight.ok && !preflight.targets.some((target) => target.warnings.length) && <p>已核对所选账号及平台内容要求。</p>}</div>}
        <div className="publish-actions"><div className="publish-summary"><span>已选择</span><strong>{selected.length} <small>个账号</small></strong></div><button className="button check-button" disabled={!!busy || !selected.length} onClick={checkTargets}>{busy === "preflight" ? <Spinner size={16} /> : <CheckCheck size={16} />}检查{mode === "draft" ? "草稿" : "发布"}内容</button><button className="button primary publish-button" disabled={!!busy || !preflight?.ok} onClick={publish}>{busy === "publish" ? <Spinner size={16} /> : mode === "draft" ? <Save size={16} /> : <Send size={16} />}{mode === "draft" ? "保存平台草稿" : "发布到所选账号"}</button><p>检查时会保存本地稿件。任务创建后，编辑稿件不会改变已提交的内容。</p></div>
      </aside>
    </div>

    {pendingSelection && <Modal title="保留当前修改？" onClose={() => setPendingSelection(null)}><p className="modal-description">当前稿件还有未保存的修改。继续切换会放弃这些修改。</p><div className="modal-actions"><button className="button" onClick={() => setPendingSelection(null)}>继续编辑</button><button className="button danger" onClick={() => load(pendingSelection)}>放弃修改并切换</button></div></Modal>}
    {deleteOpen && <Modal title="删除本地稿件" onClose={() => setDeleteOpen(false)} busy={!!busy}><p className="modal-description">删除“{form.title}”的本地稿件？平台内容和已有发布任务不会受到影响。</p><InlineError message={error} /><div className="modal-actions"><button className="button" onClick={() => setDeleteOpen(false)} disabled={!!busy}>取消</button><button className="button danger" disabled={!!busy} onClick={() => perform("delete", async () => { await request("DELETE", `/api/contents/${contentId}`); await onRefresh(); setDeleteOpen(false); load("new"); onNotice("本地稿件已删除。"); })}>{busy === "delete" && <Spinner />}删除稿件</button></div></Modal>}
  </div>;
}
