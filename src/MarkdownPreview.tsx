import { useEffect, useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { bridge, errorMessage, request } from "./api";
import { Spinner } from "./ui";
import type { ArticleTemplateId, Asset } from "./shared/types";

const imageCache = new Map<string, string>();
const metadataCache = new Map<string, Asset>();

export function useAssetMetadata(assetId: string | null) {
  const [asset, setAsset] = useState<Asset | null>(() => assetId ? metadataCache.get(assetId) || null : null);
  useEffect(() => {
    let active = true;
    setAsset(assetId ? metadataCache.get(assetId) || null : null);
    if (assetId && !metadataCache.has(assetId)) {
      request<Asset>("GET", `/api/assets/${encodeURIComponent(assetId)}`)
        .then((value) => {
          if (!value?.id) return;
          metadataCache.set(assetId, value);
          if (active) setAsset(value);
        }).catch(() => { /* Existing content remains editable if its original asset was removed. */ });
    }
    return () => { active = false; };
  }, [assetId]);
  return asset;
}

export function useAssetPreview(assetId: string | null) {
  const [dataUrl, setDataUrl] = useState<string | null>(() => assetId ? imageCache.get(assetId) || null : null);
  useEffect(() => {
    let active = true;
    setDataUrl(assetId ? imageCache.get(assetId) || null : null);
    if (assetId && !imageCache.has(assetId)) {
      request<{ dataUrl: string }>("GET", `/api/assets/${encodeURIComponent(assetId)}/preview`)
        .then(({ dataUrl: value }) => {
          if (!/^data:image\/(?:png|jpeg|gif|webp);base64,/i.test(value)) return;
          imageCache.set(assetId, value);
          if (active) setDataUrl(value);
        }).catch(() => { /* The editor keeps a useful placeholder when an image is unavailable. */ });
    }
    return () => { active = false; };
  }, [assetId]);
  return dataUrl;
}

export function AssetImage({ id, alt, className }: { id: string; alt: string; className?: string }) {
  const preview = useAssetPreview(id);
  return preview ? <img src={preview} alt={alt} className={className} /> : <span className="image-loading">图片预览加载中</span>;
}

const templateStyle = /^(?:color|background-color|font-(?:family|size|weight|style)|line-height|letter-spacing|text-align|text-decoration(?:-(?:line|style|color|thickness))?|margin(?:-(?:top|right|bottom|left))?|padding(?:-(?:top|right|bottom|left))?|border(?:-(?:top|right|bottom|left))?(?:-(?:width|style|color))?|border-(?:(?:top|bottom)-(?:left|right)-)?radius|border-collapse|max-width|width|height|box-sizing|box-shadow|word-break|overflow-wrap|white-space(?:-collapse)?|text-wrap-mode|list-style-type)$/;
const assetIdentifier = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function sanitizeArticleHtml(html: string) {
  const clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel|easy-asset):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i, FORBID_TAGS: ["style", "form", "input", "iframe", "video", "audio", "svg", "math", "object", "embed", "link", "meta"], FORBID_ATTR: ["srcset"] });
  const document = new DOMParser().parseFromString(clean, "text/html");
  for (const element of document.querySelectorAll<HTMLElement>("[style]")) {
    for (const property of [...element.style]) {
      const value = element.style.getPropertyValue(property);
      const layout = (property === "display" && ["block", "inline-block"].includes(value)) || (property === "table-layout" && value === "fixed");
      if ((!templateStyle.test(property) && !layout) || /url\s*\(|expression|@|\/\*|\\|<|>/i.test(value)) element.style.removeProperty(property);
    }
    if (!element.style.length) element.removeAttribute("style");
  }
  return document.body.innerHTML;
}

const thumbnailSource = "# 灵感的形状\n\n每一份创作，都值得被认真阅读。\n\n## 把想法写成故事\n\n> 留一点空白，给思考与想象。\n\n**让好内容，被更多人看见。**";
const thumbnailCache = new Map<ArticleTemplateId, Promise<string>>();

export function ArticleTemplateThumbnail({ templateId }: { templateId: ArticleTemplateId }) {
  const [html, setHtml] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setFailed(false);
    if (!thumbnailCache.has(templateId)) {
      thumbnailCache.set(templateId, request<{ html: string }>("POST", "/api/preview/article", { body: thumbnailSource, articleTemplateId: templateId }).then((result) => {
        const document = new DOMParser().parseFromString(sanitizeArticleHtml(result.html), "text/html");
        // Fixed text sample only: miniature previews never load images or open links.
        for (const element of document.querySelectorAll("img, a")) element.replaceWith(document.createTextNode(element.textContent || ""));
        return document.body.innerHTML;
      }).catch((cause) => { thumbnailCache.delete(templateId); throw cause; }));
    }
    thumbnailCache.get(templateId)!.then((value) => { if (active) setHtml(value); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [templateId]);
  return <span className="template-swatch" aria-hidden="true">{html ? <span className="template-miniature" dangerouslySetInnerHTML={{ __html: html }} /> : <span className="template-miniature-placeholder">{failed ? "Aa" : <Spinner />}</span>}</span>;
}

export default function MarkdownPreview({ source, assetIds, templateId, platformId, accountId, onError }: { source: string; assetIds: string[]; templateId: ArticleTemplateId; platformId?: string; accountId?: string; onError: (message: string) => void }) {
  const [images, setImages] = useState<Record<string, string>>({});
  const [rendered, setRendered] = useState<{ html: string; notes: string[] }>({ html: "", notes: [] });
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const cleanHtml = useMemo(() => sanitizeArticleHtml(rendered.html), [rendered.html]);
  const idsKey = useMemo(() => {
    const ids = new Set(assetIds.filter((id) => assetIdentifier.test(id)).slice(0, 100));
    const document = new DOMParser().parseFromString(cleanHtml, "text/html");
    // MCP-created Markdown may refer to imported images without imageAssetIds.
    // Discover only local image references, with the same 100-image preview cap.
    for (const image of document.querySelectorAll("img[src]")) {
      if (ids.size >= 100) break;
      const source = image.getAttribute("src") || "";
      const id = source.startsWith("easy-asset://") ? source.slice("easy-asset://".length) : "";
      if (assetIdentifier.test(id)) ids.add(id);
    }
    return [...ids].sort().join(",");
  }, [assetIds, cleanHtml]);
  useEffect(() => {
    let active = true;
    setFailure(null);
    if (!source.trim()) { setRendered({ html: "", notes: [] }); setLoading(false); return; }
    setLoading(true);
    request<{ html: string; notes: string[] }>("POST", "/api/preview/article", { body: source, articleTemplateId: templateId, ...(platformId ? { platformId } : {}), ...(accountId ? { accountId } : {}) })
      .then((result) => { if (active) setRendered(result); })
      .catch((cause) => { if (active) setFailure(errorMessage(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [source, templateId, platformId, accountId, retry]);
  useEffect(() => {
    let active = true;
    const ids = idsKey ? idsKey.split(",") : [];
    Promise.all(ids.map(async (id) => {
      if (imageCache.has(id)) return [id, imageCache.get(id)!] as const;
      try {
        const { dataUrl } = await request<{ dataUrl: string }>("GET", `/api/assets/${encodeURIComponent(id)}/preview`);
        if (!/^data:image\/(?:png|jpeg|gif|webp);base64,/i.test(dataUrl)) return [id, ""] as const;
        imageCache.set(id, dataUrl);
        return [id, dataUrl] as const;
      } catch { return [id, ""] as const; }
    })).then((entries) => { if (active) setImages(Object.fromEntries(entries)); });
    return () => { active = false; };
  }, [idsKey]);

  const html = useMemo(() => {
    // Only the engine's shared article renderer supplies HTML and template styles.
    // Markdown source is never inserted directly, and preview images stay local.
    const document = new DOMParser().parseFromString(cleanHtml, "text/html");
    const allowedImages = new Set(idsKey ? idsKey.split(",") : []);
    for (const image of document.querySelectorAll("img")) {
      const src = image.getAttribute("src") || "";
      const id = src.startsWith("easy-asset://") ? src.slice("easy-asset://".length) : "";
      if (allowedImages.has(id) && images[id]) image.src = images[id];
      else {
        const placeholder = document.createElement("span");
        placeholder.className = "preview-image-placeholder";
        placeholder.textContent = `[${image.alt || "正文图片"} · 请导入本地图片]`;
        image.replaceWith(placeholder);
      }
    }
    return document.body.innerHTML;
  }, [cleanHtml, images, idsKey]);

  if (loading) return <div className="preview-loading" role="status"><Spinner />正在生成文章预览…</div>;
  if (failure) return <div className="preview-failure" role="alert"><p>文章预览暂时不可用：{failure}</p><button className="button small" type="button" onClick={() => setRetry((value) => value + 1)}>重新生成预览</button></div>;

  return <>{rendered.notes.length > 0 && <div className="preview-notes">{rendered.notes.map((note) => <p key={note}>{note}</p>)}</div>}<div className="markdown-preview" onClick={(event) => {
    const link = (event.target as HTMLElement).closest("a");
    if (link) {
      event.preventDefault();
      bridge().openExternal(link.href).catch(() => onError("该链接无法从应用内打开，请在浏览器中访问。"));
    }
  }} dangerouslySetInnerHTML={{ __html: html || '<p class="preview-empty">写下第一段内容，预览会显示在这里。</p>' }} /></>;
}
