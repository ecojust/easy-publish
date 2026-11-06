import { useRef, useState } from "react";
import { Check, Download, HardDrive, Info, Monitor, ShieldCheck } from "lucide-react";
import { errorMessage, request } from "./api";
import { InlineError, Spinner } from "./ui";
import type { Runtime } from "./shared/types";

export default function SettingsPage({ runtime, onRefresh }: { runtime: Runtime; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef(false);
  async function installBrowser() {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError(null);
    try { await request("POST", "/api/runtime/browser/install"); await onRefresh(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); operation.current = false; }
  }
  return <div className="standard-page settings-page"><div className="page-heading"><div><div className="eyebrow">PREFERENCES</div><h1>设置</h1><p>准备好本地环境，专注每一次创作。</p></div></div><InlineError message={error} />
    <section className="settings-section panel"><div className="settings-title"><Monitor size={20} /><div><h2>发布浏览器</h2><p>用于扫码登录和在各平台的创作中心发布内容。</p></div><span className={`status-badge ${runtime.browserReady ? "positive" : runtime.browserInstalling ? "accent" : "neutral"}`}>{runtime.browserInstalling ? <Spinner size={12} /> : runtime.browserReady ? <Check size={12} /> : <Download size={12} />}{runtime.browserReady ? "已就绪" : runtime.browserInstalling ? "正在准备" : "尚未准备"}</span></div><div className="setting-content"><p>{runtime.browserMessage || "首次使用时下载独立浏览器，后续使用会复用本机环境。"}</p><button className="button primary" disabled={busy || runtime.browserInstalling || runtime.browserReady} onClick={installBrowser}>{busy || runtime.browserInstalling ? <Spinner size={16} /> : runtime.browserReady ? <Check size={16} /> : <Download size={16} />}{runtime.browserReady ? "浏览器已就绪" : runtime.browserInstalling ? "准备中，请稍候" : "下载并准备浏览器"}</button></div></section>
    <section className="settings-section panel"><div className="settings-title"><ShieldCheck size={20} /><div><h2>账号与凭据</h2><p>账号会话和公众号凭据使用本机凭据库加密保存。</p></div><span className={`status-badge ${runtime.vaultReady ? "positive" : "caution"}`}>{runtime.vaultReady ? "已就绪" : "需要处理"}</span></div><div className="setting-content"><p>{runtime.vaultMessage || (runtime.vaultReady ? "系统凭据服务可用。AppSecret 不会在账号列表中显示。" : "系统凭据服务暂不可用，请检查系统钥匙串或凭据管理器。")}</p></div></section>
    <section className="settings-section panel"><div className="settings-title"><HardDrive size={20} /><div><h2>本地工作空间</h2><p>稿件、素材与发布记录保存在当前电脑。</p></div></div><div className="setting-content"><p>每次发布保存独立内容快照。平台账号需要联网登录，发布结果以各平台实际回执为准。</p></div></section>
    <div className="about-app"><span className="brand-icon small">e<span>p</span></span><div><strong>Easy Publish</strong><span>让好内容，被更多人看见。</span></div><span className="version-label"><Info size={13} />v{runtime.version}</span></div>
  </div>;
}
