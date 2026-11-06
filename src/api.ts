import { invoke, isTauri } from "@tauri-apps/api/core";
import type { DesktopBridge, McpConfiguration } from "./shared/types";

const desktop: DesktopBridge = {
  apiRequest: <T,>(method: string, path: string, body?: unknown) =>
    invoke<T>("api_request", { method, path, body: body ?? null }),
  pickFile: (kind) => invoke<string | null>("pick_file", { kind }),
  openExternal: (url) => invoke<void>("open_external", { url }),
  mcpConfiguration: () => invoke<McpConfiguration>("mcp_configuration"),
};

export function bridge(): DesktopBridge {
  if (window.easyPublish) return window.easyPublish;
  if (isTauri()) return desktop;
  throw new Error("请在 Easy Publish 桌面应用中使用。浏览器预览没有连接本地发布引擎。");
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  return bridge().apiRequest<T>(method, path, body);
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error !== null && "error" in error) return String(error.error);
  if (typeof error === "string") {
    try {
      const parsed = JSON.parse(error) as { error?: string };
      if (parsed.error) return parsed.error;
    } catch { /* Native commands may return plain text. */ }
    return error;
  }
  return "操作未完成，请重试。";
}
