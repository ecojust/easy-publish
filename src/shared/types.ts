export type ContentKind = "article" | "video";
export type PublishMode = "publish" | "draft";
export type ArticleTemplateId = "clean" | "editorial" | "journal" | "tech" | "forest" | "artdeco";
export interface ArticleTemplate { id: ArticleTemplateId; name: string; description: string; accent: string; }
export interface ArticlePreview { html: string; notes: string[]; }
export interface McpConfiguration { command: string; args: string[]; env?: Record<string, string>; }
export type TaskStatus = "queued" | "running" | "submitting" | "draft_created" | "submitted" | "published" | "needs_attention" | "failed" | "cancelled" | "unknown";
export interface PlatformField { key: string; label: string; type: "select" | "text"; required: boolean; kinds: ContentKind[]; choices?: { value: string; label: string }[]; dynamicChoices?: "bilibiliCategories"; hint?: string; }
export interface Platform { id: string; name: string; color: string; kinds: ContentKind[]; modes: PublishMode[]; auth: "browser" | "wechat_api"; titleLimit: number; description: string; homeUrl: string; fields: PlatformField[]; }
export interface Asset { id: string; name: string; kind: "video" | "image" | "article"; size: number; mime: string; text?: string; }
export interface ContentInput { inputFormat: "plain" | "markdown"; sourceText: string; kind: ContentKind; title: string; body: string; description: string; tags: string[]; author: string; videoAssetId: string | null; coverAssetId: string | null; imageAssetIds: string[]; platformOptions: Record<string, Record<string, string>>; articleTemplateId: ArticleTemplateId; }
export interface Content extends ContentInput { formatReady: boolean; formatModel: string | null; id: string; createdAt: string; updatedAt: string; revision: number; platformPublicationStates: Record<string, "submitted" | "published" | "unknown">; }
export interface Account { id: string; platformId: string; label: string; status: "login_required" | "logging_in" | "ready" | "expired" | "error"; message: string; updatedAt: string; credentialConfigured: boolean; defaultSelected: boolean; wechatTemplateId?: ArticleTemplateId; bilibiliCategories?: { value: string; label: string }[]; }
export interface PublishTask { id: string; accountId: string; accountLabel: string; platformId: string; status: TaskStatus; message: string; remoteId: string | null; url: string | null; canRetry: boolean; startedAt: string | null; finishedAt: string | null; }
export interface Batch { id: string; contentId: string; title: string; kind: ContentKind; mode: PublishMode; createdAt: string; tasks: PublishTask[]; }
export interface Runtime { browserReady: boolean; browserInstalling: boolean; browserMessage: string; vaultReady: boolean; vaultMessage: string; version: string; }
export interface AppState { platforms: Platform[]; accounts: Account[]; contents: Content[]; batches: Batch[]; runtime: Runtime; formatting: FormattingState; articleTemplates: ArticleTemplate[]; }
export interface Preflight { ok: boolean; targets: { accountId: string; errors: string[]; warnings: string[] }[]; }
export interface DesktopBridge { apiRequest<T>(method: string, path: string, body?: unknown): Promise<T>; pickFile(kind: "video" | "image" | "article"): Promise<string | null>; openExternal(url: string): Promise<void>; mcpConfiguration(): Promise<McpConfiguration>; }
declare global { interface Window { easyPublish?: DesktopBridge; } }

export interface FormattingState { available: boolean; version: string | null; modelId: string | null; modelName: string | null; free: boolean; message: string; }
export interface FormatJob { id: string; contentId: string; status: "running" | "succeeded" | "failed" | "cancelled"; message: string; content?: Content; }
