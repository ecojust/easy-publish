import { useRef, useState } from "react";
import { ArrowUpRight, Check, ExternalLink, FileText, Film, KeyRound, Link2, Plus, RefreshCw, Settings2, ShieldCheck, Trash2, Users, X } from "lucide-react";
import { bridge, errorMessage, request } from "./api";
import { ArticleTemplateThumbnail } from "./MarkdownPreview";
import { AccountBadge, dateLabel, EmptyState, InlineError, Modal, PlatformMark, Spinner } from "./ui";
import type { Account, ArticleTemplate, ArticleTemplateId, ContentKind, Platform, Runtime } from "./shared/types";

interface Props {
  accounts: Account[];
  articleTemplates: ArticleTemplate[];
  platforms: Platform[];
  runtime: Runtime;
  onRefresh: () => Promise<void>;
  onNotice: (message: string) => void;
  onSettings: () => void;
}

function videoSettingsSummary(account: Account, platform: Platform) {
  const options = account.videoOptions || {};
  const fields = platform.fields.filter((field) => field.kinds.includes("video"));
  if (!Object.keys(options).length) return "待设置";
  if (fields.some((field) => field.required && !options[field.key]) || (account.platformId === "bilibili" && options.copyright === "2" && !options.source)) return "待完善";
  if (fields.some((field) => field.dynamicChoices === "bilibiliCategories" && !(account.bilibiliCategories || []).some((choice) => choice.value === options[field.key]))) return "分区需更新";
  return "已设置";
}

export default function AccountsPage({ accounts, articleTemplates, platforms, runtime, onRefresh, onNotice, onSettings }: Props) {
  const [filter, setFilter] = useState<ContentKind | "all">("all");
  const [showAdd, setShowAdd] = useState(false);
  const [platformId, setPlatformId] = useState("");
  const [label, setLabel] = useState("");
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleteAccount, setDeleteAccount] = useState<Account | null>(null);
  const [templateAccount, setTemplateAccount] = useState<Account | null>(null);
  const [templateId, setTemplateId] = useState<ArticleTemplateId>("wenyan-default");
  const [videoSettingsAccount, setVideoSettingsAccount] = useState<Account | null>(null);
  const [videoOptions, setVideoOptions] = useState<Record<string, string>>({});
  const operation = useRef(false);
  const platform = platforms.find((item) => item.id === platformId);
  const displayed = accounts.filter((account) => filter === "all" || platforms.find((item) => item.id === account.platformId)?.kinds.includes(filter));
  const videoAccount = accounts.find((account) => account.id === videoSettingsAccount?.id) || videoSettingsAccount;
  const videoPlatform = platforms.find((item) => item.id === videoAccount?.platformId);
  const videoFields = (videoPlatform?.fields || []).filter((field) => field.kinds.includes("video") && !(videoAccount?.platformId === "bilibili" && field.key === "source" && videoOptions.copyright !== "2"));

  async function perform(key: string, action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    setBusy(key);
    setError(null);
    try { await action(); } catch (cause) { setError(errorMessage(cause)); }
    finally { operation.current = false; setBusy(null); }
  }

  function closeAdd() {
    setShowAdd(false);
    setAppId("");
    setAppSecret("");
    setPlatformId("");
    setLabel("");
    setError(null);
  }

  async function addAccount() {
    await perform("add", async () => {
      if (!platform) throw new Error("请选择发布平台。");
      if (!label.trim()) throw new Error("请填写账号备注，方便区分多个账号。");
      if (platform.auth === "wechat_api" && (!appId.trim() || !appSecret.trim())) throw new Error("请填写该公众号的 AppID 和 AppSecret。");
      const payload = { platformId, label: label.trim(), ...(platform.auth === "wechat_api" ? { credentials: { appId: appId.trim(), appSecret: appSecret.trim() } } : {}) };
      setAppId("");
      setAppSecret("");
      await request<Account>("POST", "/api/accounts", payload);
      await onRefresh();
      closeAdd();
      onNotice("账号已添加，请连接账号后开始发布。");
    });
  }

  return <div className="standard-page">
    <div className="page-heading"><div><div className="eyebrow">YOUR CHANNELS</div><h1>账号管理<span className="heading-count">{accounts.length}</span></h1><p>连接你的创作平台，每个平台可以添加多个账号。</p></div><button className="button primary" onClick={() => { setError(null); setShowAdd(true); }}><Plus size={17} />添加账号</button></div>
    {!showAdd && !deleteAccount && !templateAccount && !videoSettingsAccount && <InlineError message={error} />}
    {!runtime.browserReady && <div className="setup-banner"><span className="setup-icon"><Link2 size={20} /></span><div><strong>{runtime.browserInstalling ? "正在准备发布浏览器" : "首次使用，准备发布浏览器"}</strong><p>{runtime.browserInstalling ? runtime.browserMessage : "扫码登录和网页发布需要独立浏览器，可以在设置中一键准备。"}</p></div><button className="button small" onClick={onSettings}>前往设置<ArrowUpRight size={14} /></button></div>}
    <div className="list-toolbar"><div className="filter-tabs">{([["all", "全部账号"], ["article", "文章平台"], ["video", "视频平台"]] as const).map(([value, title]) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{title}</button>)}</div><span className="list-count">{accounts.filter((account) => account.status === "ready").length} 个已连接</span></div>
    {displayed.length ? <div className="account-grid">{displayed.map((account) => {
      const item = platforms.find((entry) => entry.id === account.platformId);
      return <article className="account-card panel" key={account.id}>
        <div className="account-card-top"><PlatformMark platform={item} /><AccountBadge status={account.status} /></div>
        <h2>{account.label}</h2><p className="account-platform">{item?.name || account.platformId}</p>
        <div className="capability-tags">{item?.kinds.map((kind) => <span key={kind}>{kind === "article" ? <FileText size={12} /> : <Film size={12} />}{kind === "article" ? "文章" : "视频"}</span>)}{item?.modes.includes("draft") && <span>平台草稿</span>}</div>
        <div className="account-message">{account.message || (account.status === "ready" ? "账号连接正常，可以用于发布。" : "连接账号后，可在内容页面选择此账号。")}</div>
        <div className="account-updated">更新于 {dateLabel(account.updatedAt)}</div>
        <label className="account-default-option"><span>发布时默认选中</span><input type="checkbox" role="switch" aria-label={`${account.label} 发布时默认选中`} checked={account.defaultSelected !== false} disabled={!!busy} onChange={(event) => {
          const defaultSelected = event.target.checked;
          void perform(`preferences:${account.id}`, async () => {
            await request<Account>("PUT", `/api/accounts/${account.id}/preferences`, { defaultSelected });
            await onRefresh();
            onNotice(defaultSelected ? "已设为默认发布账号。" : "已关闭此账号的默认发布。");
          });
        }} /></label>
        {account.platformId === "wechat" && <button className="account-template-button" aria-label={`设置 ${account.label} 文章模板`} disabled={!!busy} onClick={() => { setTemplateAccount(account); setTemplateId(account.wechatTemplateId || "wenyan-default"); setError(null); }}><FileText size={14} /><span>文章模板</span><strong>{articleTemplates.find((template) => template.id === (account.wechatTemplateId || "wenyan-default"))?.name || "默认经典"}</strong><ArrowUpRight size={13} /></button>}
        {item?.fields.some((field) => field.kinds.includes("video")) && <button className="account-template-button" aria-label={`设置 ${account.label} 视频发布设置`} disabled={!!busy} onClick={() => { setVideoSettingsAccount(account); setVideoOptions({ ...(account.videoOptions || {}) }); setError(null); }}><Settings2 size={14} /><span>视频发布设置</span><strong>{videoSettingsSummary(account, item)}</strong><ArrowUpRight size={13} /></button>}
        <div className="account-card-actions">{account.status === "logging_in" ? <button className="button small" disabled={!!busy} onClick={() => perform(`cancel:${account.id}`, async () => { await request("POST", `/api/accounts/${account.id}/cancel`); await onRefresh(); onNotice("连接操作已结束。"); })}>{busy === `cancel:${account.id}` ? <Spinner size={14} /> : <X size={14} />}取消连接</button> : <button className={`button small ${account.status !== "ready" ? "primary" : ""}`} disabled={!!busy || (item?.auth === "browser" && !runtime.browserReady)} onClick={() => perform(`login:${account.id}`, async () => { await request("POST", `/api/accounts/${account.id}/login`); await onRefresh(); onNotice(item?.auth === "wechat_api" ? "已开始检查公众号连接。" : "正在检查应用保存的会话，需要登录时会打开浏览器。"); })}>{busy === `login:${account.id}` ? <Spinner size={14} /> : <Link2 size={14} />}{account.status === "ready" ? "重新连接" : item?.auth === "wechat_api" ? "检查连接" : "登录账号"}</button>}
          <div><button className="icon-button" aria-label={`检查 ${account.label} 连接`} title="检查连接状态" disabled={!!busy || account.status === "logging_in"} onClick={() => perform(`check:${account.id}`, async () => { await request("POST", `/api/accounts/${account.id}/check`); await onRefresh(); onNotice("已请求检查账号状态。"); })}>{busy === `check:${account.id}` ? <Spinner size={15} /> : <RefreshCw size={15} />}</button><button className="icon-button" aria-label={`打开 ${item?.name || "平台"}`} title="打开创作平台" onClick={() => perform(`open:${account.id}`, async () => { if (item) await bridge().openExternal(item.homeUrl); })} disabled={!!busy}><ExternalLink size={15} /></button><button className="icon-button danger-hover" aria-label={`移除账号 ${account.label}`} title="移除本地账号" onClick={() => { setDeleteAccount(account); setError(null); }} disabled={!!busy}><Trash2 size={15} /></button></div>
        </div>
      </article>;
    })}</div> : <div className="panel"><EmptyState icon={<Users size={30} />} title={accounts.length ? "还没有这类平台的账号" : "让你的内容连接更多平台"} action={<button className="button primary" onClick={() => setShowAdd(true)}><Plus size={16} />添加第一个账号</button>}>添加账号并完成连接后，就可以把同一篇文章或视频发布到多个平台。</EmptyState></div>}
    <div className="privacy-note"><ShieldCheck size={16} /><p>登录会话保存在本机。公众号凭据加密保存，界面不会回显 AppSecret。</p></div>

    {templateAccount && <Modal title={`${templateAccount.label} · 文章模板`} onClose={() => setTemplateAccount(null)} busy={!!busy}>
      <p className="modal-description">选择 Wenyan 文章模板，预览与微信公众号发送使用同一份排版。原有模板仍可选择。</p><InlineError message={error} />
      <div className="template-options" role="group" aria-label="公众号文章模板">{articleTemplates.map((template) => <button type="button" key={template.id} className={`template-option ${templateId === template.id ? "selected" : ""}`} aria-pressed={templateId === template.id} disabled={!!busy} onClick={() => setTemplateId(template.id)}><ArticleTemplateThumbnail templateId={template.id} /><span><strong>{template.name}</strong><small>{template.description}</small></span>{templateId === template.id && <Check size={14} />}</button>)}</div>
      <div className="modal-actions"><button className="button" disabled={!!busy} onClick={() => setTemplateAccount(null)}>取消</button><button className="button primary" disabled={!!busy} onClick={() => perform(`template:${templateAccount.id}`, async () => { await request<Account>("PUT", `/api/accounts/${templateAccount.id}/preferences`, { wechatTemplateId: templateId }); await onRefresh(); setTemplateAccount(null); onNotice("公众号模板已保存。"); })}>{busy && <Spinner size={16} />}保存模板</button></div>
    </Modal>}

    {videoAccount && videoPlatform && <Modal title={`${videoAccount.label} · 视频发布设置`} onClose={() => setVideoSettingsAccount(null)} busy={!!busy}>
      <form onSubmit={(event) => { event.preventDefault(); void perform(`video-settings:${videoAccount.id}`, async () => {
        const values = Object.fromEntries(videoPlatform.fields.filter((field) => field.kinds.includes("video")).map((field) => [field.key, (videoOptions[field.key] || "").trim()]).filter(([, value]) => value));
        await request<Account>("PUT", `/api/accounts/${videoAccount.id}/preferences`, { videoOptions: values });
        await onRefresh(); setVideoSettingsAccount(null); onNotice("视频发布设置已保存，之后的视频发布会使用此账号的设置。");
      }); }}>
        <p className="modal-description">这些设置用于此账号之后的视频发布，每个账号分别设置。可以先保存，未填完整的项目会在发布前检查时提示。</p>
        <InlineError message={error} />
        <fieldset className="account-video-fields" disabled={!!busy}>
          {videoFields.map((field) => {
            const choices = field.dynamicChoices === "bilibiliCategories" ? videoAccount.bilibiliCategories || [] : field.choices || [];
            const value = videoOptions[field.key] || "";
            const invalidChoice = field.type === "select" && !!value && !choices.some((choice) => choice.value === value);
            const hint = field.dynamicChoices === "bilibiliCategories" ? !choices.length ? "尚未获取此账号的视频分区，请连接或检查此账号。" : invalidChoice ? "原选分区已不可用，请为此账号重新选择。" : "仅显示此账号可用的分区，其他账号可以选择不同分区。" : videoAccount.platformId === "bilibili" && field.key === "source" ? "发布不同的转载视频前，请在这里更新原始视频链接。" : field.hint;
            const required = field.required || (videoAccount.platformId === "bilibili" && field.key === "source" && videoOptions.copyright === "2");
            return <label className="field" key={field.key}><span>{field.label}{required && <b className="required-mark">*</b>}</span>{field.type === "select" ? <select value={value} onChange={(event) => { setVideoOptions((current) => ({ ...current, [field.key]: event.target.value })); setError(null); }}><option value="">请选择{field.label}</option>{invalidChoice && <option value={value}>{value}（需重新选择）</option>}{choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select> : <input value={value} onChange={(event) => { setVideoOptions((current) => ({ ...current, [field.key]: event.target.value })); setError(null); }} placeholder={field.hint || field.label} maxLength={2000} />}{hint && <small className="field-hint" role={invalidChoice || (field.dynamicChoices && !choices.length) ? "status" : undefined}>{hint}</small>}</label>;
          })}
        </fieldset>
        {videoFields.some((field) => field.dynamicChoices === "bilibiliCategories") && <button className="text-button account-category-refresh" type="button" disabled={!!busy || videoAccount.status === "logging_in"} onClick={() => perform(`video-refresh:${videoAccount.id}`, async () => { await request("POST", `/api/accounts/${videoAccount.id}/check`); await onRefresh(); onNotice("已请求检查此账号，分区将在检查完成后更新。"); })}><RefreshCw size={13} />检查账号并刷新分区</button>}
        <div className="modal-actions"><button className="button" type="button" disabled={!!busy} onClick={() => setVideoSettingsAccount(null)}>取消</button><button className="button primary" type="submit" disabled={!!busy}>{busy === `video-settings:${videoAccount.id}` && <Spinner size={16} />}保存视频设置</button></div>
      </form>
    </Modal>}

    {showAdd && <Modal title="添加发布账号" onClose={closeAdd} busy={!!busy}><form onSubmit={(event) => { event.preventDefault(); void addAccount(); }}>
      <p className="modal-description">选择发布平台，为账号设置一个容易辨认的备注。</p><InlineError message={error} />
      <span className="field-label">选择平台</span><div className="platform-picker">{platforms.map((item) => <button type="button" key={item.id} className={platformId === item.id ? "selected" : ""} disabled={!!busy} onClick={() => { setPlatformId(item.id); setAppId(""); setAppSecret(""); setError(null); }}><PlatformMark platform={item} size="small" /><span><strong>{item.name}</strong><small>{item.kinds.map((kind) => kind === "article" ? "文章" : "视频").join(" / ")}</small></span>{platformId === item.id && <Check size={15} />}</button>)}</div>
      {platform && <p className="platform-description">{platform.description}</p>}
      <label className="field"><span>账号备注</span><input autoComplete="off" disabled={!!busy} value={label} onChange={(event) => setLabel(event.target.value)} placeholder="例如：品牌官方账号" maxLength={80} required /></label>
      {platform?.auth === "wechat_api" && <div className="credential-fields"><div className="credentials-heading"><KeyRound size={15} /><strong>公众号开发者凭据</strong></div><p>从微信公众平台获取。连接能力与发布权限以公众号实际授权为准。</p><label className="field"><span>AppID</span><input name="wechat-app-id" value={appId} disabled={!!busy} onChange={(event) => setAppId(event.target.value)} autoComplete="off" spellCheck={false} placeholder="填写公众号 AppID" required /></label><label className="field"><span>AppSecret</span><input name="wechat-app-secret" type="password" value={appSecret} disabled={!!busy} onChange={(event) => setAppSecret(event.target.value)} autoComplete="new-password" placeholder="填写 AppSecret，仅本次输入" required /></label></div>}
      <div className="modal-actions"><button className="button" type="button" onClick={closeAdd} disabled={!!busy}>取消</button><button className="button primary" type="submit" disabled={!!busy || !platformId}>{busy === "add" ? <Spinner size={16} /> : <Plus size={16} />}添加账号</button></div>
    </form></Modal>}
    {deleteAccount && <Modal title="移除本地账号" onClose={() => setDeleteAccount(null)} busy={!!busy}><p className="modal-description">将移除“{deleteAccount.label}”及其在本机保存的登录会话。平台账号和已发布内容不会被删除。</p><InlineError message={error} /><div className="modal-actions"><button className="button" onClick={() => setDeleteAccount(null)} disabled={!!busy}>取消</button><button className="button danger" disabled={!!busy} onClick={() => perform("delete", async () => { await request("DELETE", `/api/accounts/${deleteAccount.id}`); await onRefresh(); setDeleteAccount(null); onNotice("已移除本地账号。"); })}>{busy === "delete" && <Spinner size={16} />}移除账号</button></div></Modal>}
  </div>;
}
