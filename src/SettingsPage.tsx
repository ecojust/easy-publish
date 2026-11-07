import { useEffect, useRef, useState } from "react";
import { Check, Copy, Download, HardDrive, Info, Monitor, Plug, ShieldCheck } from "lucide-react";
import { bridge, errorMessage, request } from "./api";
import { InlineError, Spinner } from "./ui";
import type { Runtime } from "./shared/types";

export default function SettingsPage({ runtime, onRefresh }: { runtime: Runtime; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mcpConfig, setMcpConfig] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpLoading, setMcpLoading] = useState(true);
  const [mcpAttempt, setMcpAttempt] = useState(0);
  const [copyBusy, setCopyBusy] = useState(false);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const configurationText = useRef<HTMLTextAreaElement>(null);
  const operation = useRef(false);
  useEffect(() => {
    let active = true;
    setMcpLoading(true);
    setMcpError(null);
    setCopyMessage(null);
    Promise.resolve().then(() => bridge().mcpConfiguration()).then((configuration) => {
      if (active) setMcpConfig(JSON.stringify({ mcpServers: { "easy-publish": configuration } }, null, 2));
    }).catch((cause) => {
      if (active) { setMcpConfig(null); setMcpError(errorMessage(cause)); }
    }).finally(() => { if (active) setMcpLoading(false); });
    return () => { active = false; };
  }, [mcpAttempt]);

  async function copyConfiguration() {
    if (!mcpConfig || copyBusy) return;
    setCopyBusy(true);
    try {
      await navigator.clipboard.writeText(mcpConfig);
      setCopyMessage("MCP 配置已复制，请粘贴到 AI 助手的 MCP 设置中。");
    } catch {
      configurationText.current?.focus();
      configurationText.current?.select();
      setCopyMessage("自动复制不可用，配置已选中，请按 ⌘C 或 Ctrl+C 复制。");
    } finally { setCopyBusy(false); }
  }
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
    <section className="settings-section panel"><div className="settings-title"><ShieldCheck size={20} /><div><h2>账号与凭据</h2><p>账号会话和公众号凭据只在 Easy Publish 的应用目录内加密保存。</p></div><span className={`status-badge ${runtime.vaultReady ? "positive" : "caution"}`}>{runtime.vaultReady ? "已就绪" : "需要处理"}</span></div><div className="setting-content"><p>{runtime.vaultMessage || (runtime.vaultReady ? "应用账号存储已就绪，不访问系统钥匙串。AppSecret 不会在账号列表中显示。" : "应用账号存储暂不可用，请检查应用数据目录的读写权限。")}</p></div></section>
    <section className="settings-section panel"><div className="settings-title"><Plug size={20} /><div><h2>AI 助手与 MCP</h2><p>让支持 MCP 的 AI 助手读取本机稿件、导入素材并创建发布任务。</p></div></div><div className="setting-content">
      <p>使用时保持 Easy Publish 开启，并先在账号管理连接目标平台。助手会按文章或视频筛选可用账号，默认跳过这份稿件已成功提交的平台。</p>
      <p>将下方配置合并到 AI 助手的 MCP 设置。移动应用后，请重新复制配置。</p>
      <InlineError message={mcpError} />
      {mcpLoading ? <p role="status"><Spinner size={14} /> 正在读取本机 MCP 配置…</p> : mcpConfig ? <>
        <label className="field mcp-config"><span>MCP 配置</span><textarea ref={configurationText} className="mcp-config-text" aria-label="MCP 配置" readOnly value={mcpConfig} rows={12} spellCheck={false} /></label>
        <button className="button primary" onClick={copyConfiguration} disabled={copyBusy}>{copyBusy ? <Spinner size={16} /> : <Copy size={16} />}复制 MCP 配置</button>
        {copyMessage && <p role="status">{copyMessage}</p>}
      </> : <button className="button secondary" onClick={() => setMcpAttempt((value) => value + 1)}>重新生成 MCP 配置</button>}
    </div></section>
    <section className="settings-section panel"><div className="settings-title"><HardDrive size={20} /><div><h2>本地工作空间</h2><p>稿件、素材与发布记录保存在当前电脑。</p></div></div><div className="setting-content"><p>每次发布保存独立内容快照。平台账号需要联网登录，发布结果以各平台实际回执为准。</p></div></section>
    <div className="about-app"><span className="brand-icon small">e<span>p</span></span><div><strong>Easy Publish</strong><span>让好内容，被更多人看见。</span></div><span className="version-label"><Info size={13} />v{runtime.version}</span></div>
  </div>;
}
