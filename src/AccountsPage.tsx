import { useRef, useState } from "react";
import { ArrowUpRight, Check, ExternalLink, FileText, Film, KeyRound, Link2, Plus, RefreshCw, ShieldCheck, Trash2, Users, X } from "lucide-react";
import { bridge, errorMessage, request } from "./api";
import { AccountBadge, dateLabel, EmptyState, InlineError, Modal, PlatformMark, Spinner } from "./ui";
import type { Account, ContentKind, Platform, Runtime } from "./shared/types";

interface Props {
  accounts: Account[];
  platforms: Platform[];
  runtime: Runtime;
  onRefresh: () => Promise<void>;
  onNotice: (message: string) => void;
  onSettings: () => void;
}

export default function AccountsPage({ accounts, platforms, runtime, onRefresh, onNotice, onSettings }: Props) {
  const [filter, setFilter] = useState<ContentKind | "all">("all");
  const [showAdd, setShowAdd] = useState(false);
  const [platformId, setPlatformId] = useState("");
  const [label, setLabel] = useState("");
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleteAccount, setDeleteAccount] = useState<Account | null>(null);
  const operation = useRef(false);
  const platform = platforms.find((item) => item.id === platformId);
  const displayed = accounts.filter((account) => filter === "all" || platforms.find((item) => item.id === account.platformId)?.kinds.includes(filter));

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
    {!showAdd && !deleteAccount && <InlineError message={error} />}
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
            onNotice(defaultSelected ? "已设为默认发布账号。" : "已取消默认选中，发布时仍可手动选择。");
          });
        }} /></label>
        <div className="account-card-actions">{account.status === "logging_in" ? <button className="button small" disabled={!!busy} onClick={() => perform(`cancel:${account.id}`, async () => { await request("POST", `/api/accounts/${account.id}/cancel`); await onRefresh(); onNotice("连接操作已结束。"); })}>{busy === `cancel:${account.id}` ? <Spinner size={14} /> : <X size={14} />}取消连接</button> : <button className={`button small ${account.status !== "ready" ? "primary" : ""}`} disabled={!!busy || (item?.auth === "browser" && !runtime.browserReady)} onClick={() => perform(`login:${account.id}`, async () => { await request("POST", `/api/accounts/${account.id}/login`); await onRefresh(); onNotice(item?.auth === "wechat_api" ? "已开始检查公众号连接。" : "正在检查应用保存的会话，需要登录时会打开浏览器。"); })}>{busy === `login:${account.id}` ? <Spinner size={14} /> : <Link2 size={14} />}{account.status === "ready" ? "重新连接" : item?.auth === "wechat_api" ? "检查连接" : "登录账号"}</button>}
          <div><button className="icon-button" aria-label={`检查 ${account.label} 连接`} title="检查连接状态" disabled={!!busy || account.status === "logging_in"} onClick={() => perform(`check:${account.id}`, async () => { await request("POST", `/api/accounts/${account.id}/check`); await onRefresh(); onNotice("已请求检查账号状态。"); })}>{busy === `check:${account.id}` ? <Spinner size={15} /> : <RefreshCw size={15} />}</button><button className="icon-button" aria-label={`打开 ${item?.name || "平台"}`} title="打开创作平台" onClick={() => perform(`open:${account.id}`, async () => { if (item) await bridge().openExternal(item.homeUrl); })} disabled={!!busy}><ExternalLink size={15} /></button><button className="icon-button danger-hover" aria-label={`移除账号 ${account.label}`} title="移除本地账号" onClick={() => { setDeleteAccount(account); setError(null); }} disabled={!!busy}><Trash2 size={15} /></button></div>
        </div>
      </article>;
    })}</div> : <div className="panel"><EmptyState icon={<Users size={30} />} title={accounts.length ? "还没有这类平台的账号" : "让你的内容连接更多平台"} action={<button className="button primary" onClick={() => setShowAdd(true)}><Plus size={16} />添加第一个账号</button>}>添加账号并完成连接后，就可以把同一篇文章或视频发布到多个平台。</EmptyState></div>}
    <div className="privacy-note"><ShieldCheck size={16} /><p>登录会话保存在本机。公众号凭据加密保存，界面不会回显 AppSecret。</p></div>

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
