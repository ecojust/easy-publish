import { useEffect, useMemo, useState } from "react";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { bridge, request } from "./api";
import type { Asset } from "./shared/types";

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

export default function MarkdownPreview({ source, assetIds, onError }: { source: string; assetIds: string[]; onError: (message: string) => void }) {
  const [images, setImages] = useState<Record<string, string>>({});
  const idsKey = [...new Set(assetIds)].sort().join(",");
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
    const md = new MarkdownIt("commonmark", { html: false, breaks: true });
    md.renderer.rules.image = (tokens, index) => {
      const token = tokens[index];
      const src = token.attrGet("src") || "";
      const id = src.startsWith("easy-asset://") ? src.slice("easy-asset://".length) : "";
      const alt = md.utils.escapeHtml(token.content || "正文图片");
      return id && images[id] ? `<img src="${images[id]}" alt="${alt}" />` : `<span class="preview-image-placeholder">[${alt} · 请导入本地图片]</span>`;
    };
    return DOMPurify.sanitize(md.render(source), { USE_PROFILES: { html: true }, FORBID_TAGS: ["style", "form", "input", "iframe", "video", "audio"], FORBID_ATTR: ["style", "srcset"] });
  }, [source, images]);

  return <div className="markdown-preview" onClick={(event) => {
    const link = (event.target as HTMLElement).closest("a");
    if (link) {
      event.preventDefault();
      bridge().openExternal(link.href).catch(() => onError("该链接无法从应用内打开，请在浏览器中访问。"));
    }
  }} dangerouslySetInnerHTML={{ __html: html || '<p class="preview-empty">在左侧写下第一段内容，预览会显示在这里。</p>' }} />;
}
