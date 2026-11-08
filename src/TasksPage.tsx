import { useRef, useState } from "react";
import { ArrowUpRight, Ban, CheckCheck, ClipboardList, FileText, Film, RotateCcw } from "lucide-react";
import { bridge, errorMessage, request } from "./api";
import { dateLabel, EmptyState, InlineError, Modal, PlatformMark, Spinner, TaskBadge } from "./ui";
import type { Batch, Platform, TaskStatus } from "./shared/types";

const activeStatuses: TaskStatus[] = ["queued", "running", "submitting", "awaiting_user"];
const attentionStatuses: TaskStatus[] = ["awaiting_user", "unknown", "needs_attention", "failed"];
type Filter = "all" | "active" | "attention" | "done";

export default function TasksPage({ batches, platforms, onRefresh, onNotice }: { batches: Batch[]; platforms: Platform[]; onRefresh: () => Promise<void>; onNotice: (message: string) => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelBatch, setCancelBatch] = useState<Batch | null>(null);
  const operation = useRef(false);
  const displayed = [...batches].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).filter((batch) => filter === "all" || (filter === "active" ? batch.tasks.some((task) => activeStatuses.includes(task.status)) : filter === "attention" ? batch.tasks.some((task) => attentionStatuses.includes(task.status)) : batch.tasks.every((task) => !activeStatuses.includes(task.status) && !attentionStatuses.includes(task.status))));
  const activeCount = batches.filter((batch) => batch.tasks.some((task) => activeStatuses.includes(task.status))).length;
  const attentionCount = batches.filter((batch) => batch.tasks.some((task) => attentionStatuses.includes(task.status))).length;

  async function perform(key: string, action: () => Promise<void>) {
    if (operation.current) return;
    operation.current = true;
    setBusy(key);
    setError(null);
    try { await action(); } catch (cause) { setError(errorMessage(cause)); }
    finally { operation.current = false; setBusy(null); }
  }

  return <div className="standard-page">
    <div className="page-heading"><div><div className="eyebrow">PUBLISHING QUEUE</div><h1>发布任务<span className="heading-count">{batches.length}</span></h1><p>网页发布请在打开的窗口中手动点击发布，进度和结果会在这里更新。</p></div><span className="live-indicator"><span />状态自动更新</span></div>
    {!cancelBatch && <InlineError message={error} />}
    <div className="list-toolbar"><div className="filter-tabs">{([["all", "全部任务", batches.length], ["active", "进行中", activeCount], ["attention", "需要处理", attentionCount], ["done", "已结束", null]] as const).map(([key, name, count]) => <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{name}{count !== null && count > 0 && <span>{count}</span>}</button>)}</div><span className="list-count">按创建时间排序</span></div>
    {displayed.length ? <div className="batch-list">{displayed.map((batch) => {
      const active = batch.tasks.some((task) => activeStatuses.includes(task.status));
      const finished = batch.tasks.filter((task) => !activeStatuses.includes(task.status)).length;
      return <article key={batch.id} className="batch-card panel"><div className="batch-heading"><span className={`batch-kind ${batch.kind}`}>{batch.kind === "article" ? <FileText size={21} /> : <Film size={21} />}</span><div className="batch-title"><h2>{batch.title}</h2><p><span>{batch.kind === "article" ? "文章" : "视频"}</span><span>{batch.mode === "draft" ? "保存平台草稿" : "发布内容"}</span><span>{dateLabel(batch.createdAt)}</span></p></div><div className="batch-progress"><span>{finished}/{batch.tasks.length} 已结束</span><div><i style={{ width: `${batch.tasks.length ? finished / batch.tasks.length * 100 : 0}%` }} /></div></div>{active && <button className="button small" onClick={() => { setCancelBatch(batch); setError(null); }} disabled={!!busy}><Ban size={14} />取消任务</button>}</div>
        <div className="task-table"><div className="task-table-head"><span>目标账号</span><span>状态与回执</span><span>操作</span></div>{batch.tasks.map((task) => {
          const platform = platforms.find((item) => item.id === task.platformId);
          return <div key={task.id} className="task-row"><div className="task-account"><PlatformMark platform={platform} size="small" /><div><strong>{task.accountLabel}</strong><span>{platform?.name || task.platformId}</span></div></div><div className="task-result"><TaskBadge status={task.status} /><p>{task.message || (task.status === "queued" ? "等待前面的任务完成" : "等待引擎更新")}</p>{task.status === "awaiting_user" && <p className="attention-explanation">请切换到已打开的发布窗口，核对内容并亲自点击发布及后续确认按钮。等待期间请保持应用和窗口打开。</p>}{task.status === "unknown" && <p className="attention-explanation">请先到平台核对结果，避免重复发布。</p>}{task.status === "submitted" && <p className="task-explanation">平台已受理，最终是否公开以平台结果为准。</p>}</div><div className="task-actions">{task.canRetry && task.status !== "awaiting_user" && <button className="button small" disabled={!!busy} onClick={() => perform(`retry:${task.id}`, async () => { await request("POST", `/api/tasks/${task.id}/retry`); await onRefresh(); onNotice("已创建该目标的重试任务。"); })}>{busy === `retry:${task.id}` ? <Spinner size={13} /> : <RotateCcw size={13} />}重试</button>}{task.status !== "awaiting_user" && (task.url || (platform && ["unknown", "submitted", "needs_attention", "draft_created", "published"].includes(task.status))) && <button className="text-button" disabled={!!busy} onClick={() => perform(`open:${task.id}`, async () => { await bridge().openExternal(task.url || platform!.homeUrl); })}>{task.url ? "查看结果" : "前往平台"}<ArrowUpRight size={13} /></button>}</div></div>;
        })}</div><div className="batch-footer"><CheckCheck size={13} />任务使用创建时的内容快照，后续编辑不会影响本次发布。</div>
      </article>;
    })}</div> : <div className="panel"><EmptyState icon={<ClipboardList size={30} />} title={batches.length ? "这里暂时没有任务" : "第一篇内容，从这里出发"}>在账号管理开启目标账号，然后在文章或视频页面编辑内容并检查，就可以创建发布任务。</EmptyState></div>}
    {cancelBatch && <Modal title="取消未完成的任务" onClose={() => setCancelBatch(null)} busy={!!busy}><p className="modal-description">停止“{cancelBatch.title}”尚未完成的目标，并关闭对应发布窗口。已经提交到平台的内容无法通过此操作撤回；提交结果无法确定时，会标记为“结果待确认”。</p><InlineError message={error} /><div className="modal-actions"><button className="button" disabled={!!busy} onClick={() => setCancelBatch(null)}>继续执行</button><button className="button danger" disabled={!!busy} onClick={() => perform(`cancel:${cancelBatch.id}`, async () => { await request("POST", `/api/batches/${cancelBatch.id}/cancel`); await onRefresh(); setCancelBatch(null); onNotice("已请求取消未完成的任务。"); })}>{busy && <Spinner size={16} />}取消任务</button></div></Modal>}
  </div>;
}
