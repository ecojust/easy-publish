import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Clock3, LoaderCircle, X } from "lucide-react";
import type { Account, Platform, PublishMode, TaskStatus } from "./shared/types";

export function Spinner({ size = 16 }: { size?: number }) {
  return <LoaderCircle size={size} className="spinner" aria-hidden="true" />;
}

export function PlatformMark({ platform, size = "normal" }: { platform?: Platform; size?: "normal" | "small" }) {
  return <span className={`platform-mark ${size}`} style={{ "--platform-color": platform?.color || "#64748b" } as CSSProperties} aria-hidden="true">{platform?.name.slice(0, 1) || "?"}</span>;
}

const taskLabels: Record<TaskStatus, string> = {
  queued: "等待处理", running: "处理中", submitting: "正在提交", awaiting_user: "等待手动发布", draft_created: "已存平台草稿",
  submitted: "已提交，待平台处理", published: "已发布", needs_attention: "需要处理",
  failed: "失败", cancelled: "已取消", unknown: "结果待确认",
};

export function TaskBadge({ status }: { status: TaskStatus }) {
  const active = ["running", "submitting"].includes(status);
  const positive = ["published", "draft_created"].includes(status);
  const caution = ["awaiting_user", "needs_attention", "unknown"].includes(status);
  return <span className={`status-badge ${positive ? "positive" : status === "failed" ? "negative" : caution ? "caution" : active || status === "submitted" ? "accent" : "neutral"}`}>
    {active ? <Spinner size={12} /> : positive ? <CheckCircle2 size={12} /> : caution || status === "failed" ? <AlertCircle size={12} /> : <Clock3 size={12} />}{taskLabels[status]}
  </span>;
}

export function PublishMethodNotice({ platforms, mode }: { platforms: Platform[]; mode: PublishMode }) {
  if (mode !== "publish" || !platforms.length) return null;
  const browser = platforms.filter((platform) => platform.publishMethod === "browser").map((platform) => platform.name);
  const direct = platforms.filter((platform) => platform.publishMethod === "api").map((platform) => platform.name);
  return <div className="publish-method-notice" aria-label="本次发布方式">
    {browser.length > 0 && <p><strong>需在网页手动发布：{browser.join("、")}</strong><span>应用负责上传素材和填写内容。请在已打开的发布窗口中核对，再亲自点击发布及确认按钮。</span></p>}
    {direct.length > 0 && <p>{direct.join("、")}由应用直接提交，无需网页确认。</p>}
  </div>;
}

const accountLabels: Record<Account["status"], string> = {
  ready: "已连接", logging_in: "等待登录", login_required: "待连接", expired: "登录已失效", error: "连接异常",
};

export function AccountBadge({ status }: { status: Account["status"] }) {
  return <span className={`status-badge ${status === "ready" ? "positive" : status === "logging_in" ? "accent" : status === "error" ? "negative" : "neutral"}`}>
    {status === "logging_in" ? <Spinner size={12} /> : <span className="status-dot" />}{accountLabels[status]}
  </span>;
}

export function EmptyState({ icon, title, children, action }: { icon: ReactNode; title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{children}</p>{action}</div>;
}

export function Modal({ title, children, onClose, busy = false, className = "" }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean; className?: string }) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.querySelector<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)")?.focus();
    return () => previous?.focus();
  }, []);
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) { event.preventDefault(); onClose(); } }}>
    <section ref={dialog} className={`modal ${className}`.trim()} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} onKeyDown={(event) => {
      if (event.key === "Escape" && !busy) onClose();
      if (event.key === "Tab") {
        const focusable = [...(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]") || [])];
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!first) { event.preventDefault(); dialog.current?.focus(); }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }}>
      <div className="modal-heading"><h2>{title}</h2><button className="icon-button" title="关闭" aria-label="关闭" onClick={onClose} disabled={busy}><X size={20} /></button></div>{children}
    </section>
  </div>;
}

export function InlineError({ message }: { message: string | null }) {
  return message ? <div className="inline-error" role="alert"><AlertCircle size={17} /><span>{message}</span></div> : null;
}

export function dateLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

export function byteLabel(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
