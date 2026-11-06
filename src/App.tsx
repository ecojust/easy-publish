import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowUpRight, CheckCircle2, ClipboardList, FileText, Film, RefreshCw, Settings2, Users, X } from "lucide-react";
import { errorMessage, request } from "./api";
import AccountsPage from "./AccountsPage";
import DraftWorkspace from "./DraftWorkspace";
import SettingsPage from "./SettingsPage";
import TasksPage from "./TasksPage";
import { Spinner } from "./ui";
import type { AppState } from "./shared/types";

type View = "article" | "video" | "tasks" | "accounts" | "settings";
const navigation = [
  { id: "article", label: "文章", icon: FileText },
  { id: "video", label: "视频", icon: Film },
  { id: "tasks", label: "发布任务", icon: ClipboardList },
  { id: "accounts", label: "账号管理", icon: Users },
] as const;

export default function App() {
  const [view, setView] = useState<View>("article");
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    if (inFlight.current) return inFlight.current;
    const operation = (async () => {
      try {
        const next = await request<AppState>("GET", "/api/state");
        if (mounted.current) { setState(next); setConnectionError(null); }
      } catch (cause) {
        if (mounted.current) setConnectionError(errorMessage(cause));
      } finally {
        if (mounted.current) setLoading(false);
      }
    })();
    inFlight.current = operation;
    try { await operation; } finally { inFlight.current = null; }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const pending = state?.batches.reduce((count, batch) => count + batch.tasks.filter((task) => ["queued", "running", "submitting"].includes(task.status)).length, 0) || 0;
  const connected = state?.accounts.filter((account) => account.status === "ready").length || 0;

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-icon">e<span>p</span></span><div><strong>Easy Publish</strong><span>多平台内容发布</span></div></div>
      <div className="nav-label">工作空间</div><nav aria-label="主要导航">{navigation.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${view === id ? "active" : ""}`} onClick={() => setView(id)} aria-current={view === id ? "page" : undefined}><Icon size={19} /><span>{label}</span>{id === "tasks" && pending > 0 && <span className="nav-count">{pending}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="workspace-status"><span className="local-device"><span /></span><div><strong>本地工作空间</strong><span>{state ? `${connected} 个账号已连接` : "连接本地引擎"}</span></div><span className={`connection-dot ${state && !connectionError ? "ready" : ""}`} /></div><button className={`nav-item ${view === "settings" ? "active" : ""}`} onClick={() => setView("settings")} aria-current={view === "settings" ? "page" : undefined}><Settings2 size={19} /><span>设置</span>{state && !state.runtime.browserReady && <span className="setup-dot" />}</button><div className="sidebar-footnote">一份创作 · 更多可能</div></div>
    </aside>
    <main className="main-content">
      <div className="topbar"><span>创作，让分享更简单</span><div><span className="local-mode"><span />本地桌面版</span><span className="topbar-divider" /><span className="topbar-version">{state ? `v${state.runtime.version}` : "Easy Publish"}</span></div></div>
      {!state ? <div className="startup-screen"><div className="startup-graphic">{loading ? <Spinner size={35} /> : <AlertCircle size={35} />}</div><h1>{loading ? "正在打开你的工作空间" : "暂时无法连接发布引擎"}</h1><p>{loading ? "正在读取本地稿件和账号状态…" : connectionError}</p>{!loading && <button className="button primary" onClick={() => { setLoading(true); void refresh(); }}><RefreshCw size={16} />重新连接</button>}</div> : <>
        {connectionError && <div className="connection-banner" role="alert"><AlertCircle size={16} /><span>本地引擎连接中断，当前显示上次状态。{connectionError}</span><button className="text-button" onClick={() => { void refresh(); }}>重试</button></div>}
        {/* Keep both composers mounted so navigation preserves unsaved edits. */}
        <div hidden={view !== "article"}><DraftWorkspace kind="article" contents={state.contents} platforms={state.platforms} accounts={state.accounts} articleTemplates={state.articleTemplates} onRefresh={refresh} onAccounts={() => setView("accounts")} onTasks={() => setView("tasks")} onNotice={setNotice} /></div>
        <div hidden={view !== "video"}><DraftWorkspace kind="video" contents={state.contents} platforms={state.platforms} accounts={state.accounts} articleTemplates={state.articleTemplates} onRefresh={refresh} onAccounts={() => setView("accounts")} onTasks={() => setView("tasks")} onNotice={setNotice} /></div>
        {view === "tasks" && <TasksPage batches={state.batches} platforms={state.platforms} onRefresh={refresh} onNotice={setNotice} />}
        {view === "accounts" && <AccountsPage accounts={state.accounts} platforms={state.platforms} runtime={state.runtime} onRefresh={refresh} onNotice={setNotice} onSettings={() => setView("settings")} />}
        {view === "settings" && <SettingsPage runtime={state.runtime} onRefresh={refresh} />}
      </>}
      {state && !state.runtime.browserReady && view !== "settings" && view !== "accounts" && <button className="runtime-shortcut" onClick={() => setView("settings")}><span className="setup-dot" />{state.runtime.browserInstalling ? "正在准备发布浏览器" : "完成首次环境准备"}<ArrowUpRight size={14} /></button>}
    </main>
    {notice && <div className="toast" role="status"><CheckCircle2 size={18} /><span>{notice}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setNotice(null)}><X size={16} /></button></div>}
  </div>;
}
