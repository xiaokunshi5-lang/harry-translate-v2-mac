import { resolveApiUrl } from "../lib/backendBridge";
import type {
  ConfigResponse,
  HealthResponse,
  ProviderCatalog,
  AudioInputProbe,
  AudioInputMode,
  AudioInputSelectionResponse,
  OverlayControlResponse,
  OverlayStatus,
  ProviderTestResult,
  GlossaryAddSessionPayload,
  GlossaryAddSessionResponse,
  GlossaryClearSessionResponse,
  GlossaryPacksResponse,
  GlossaryPromotePayload,
  GlossaryPromoteResponse,
  GlossarySessionResponse,
  GlossaryTermsResponse,
  SessionSwitchResponse,
  SummarySnapshot,
  LatestSessionResponse,
  UserSettings,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message?: string) {
    super(message || code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = await resolveApiUrl(path);
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      String(payload.error || "request_failed"),
      String(payload.message || payload.error || `HTTP ${response.status}`),
    );
  }
  return payload as T;
}

export function getConfig(): Promise<ConfigResponse> {
  return request<ConfigResponse>("/api/config");
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

export function getAudioDevices(): Promise<AudioInputProbe> {
  return request<AudioInputProbe>("/api/audio/devices");
}

export function selectAudioInput(
  mode: AudioInputMode,
): Promise<AudioInputSelectionResponse> {
  return request<AudioInputSelectionResponse>("/api/audio/input", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

export function getProviderCatalog(): Promise<ProviderCatalog> {
  return request<ProviderCatalog>("/api/providers/catalog");
}

export function getOverlayStatus(): Promise<OverlayStatus> {
  return request<OverlayStatus>("/api/overlay/status");
}

export function launchOverlay(): Promise<OverlayControlResponse> {
  return request<OverlayControlResponse>("/api/overlay/launch", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function hideOverlay(): Promise<OverlayControlResponse> {
  return request<OverlayControlResponse>("/api/overlay/hide", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function getGlossaryPacks(): Promise<GlossaryPacksResponse> {
  return request<GlossaryPacksResponse>("/api/glossary/packs");
}

export function toggleGlossaryPack(
  packId: string,
  enabled: boolean,
): Promise<GlossaryPacksResponse> {
  return request<GlossaryPacksResponse>(
    `/api/glossary/packs/${encodeURIComponent(packId)}/toggle`,
    { method: "POST", body: JSON.stringify({ enabled }) },
  );
}

export function getGlossaryTerms(params: {
  category?: string;
  packId?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<GlossaryTermsResponse> {
  const search = new URLSearchParams();
  if (params.category) search.set("category", params.category);
  if (params.packId) search.set("pack_id", params.packId);
  if (params.q) search.set("q", params.q);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  return request<GlossaryTermsResponse>(
    `/api/glossary/terms/active${search.toString() ? `?${search.toString()}` : ""}`,
  );
}

export function getSessionGlossaryTerms(): Promise<GlossarySessionResponse> {
  return request<GlossarySessionResponse>("/api/glossary/session");
}

export function addSessionGlossaryTerm(
  payload: GlossaryAddSessionPayload,
): Promise<GlossaryAddSessionResponse> {
  return request<GlossaryAddSessionResponse>("/api/glossary/session/add", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function promoteGlossaryTerm(
  payload: GlossaryPromotePayload,
): Promise<GlossaryPromoteResponse> {
  return request<GlossaryPromoteResponse>("/api/glossary/promote-term", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function clearSessionGlossaryTerms(): Promise<GlossaryClearSessionResponse> {
  return request<GlossaryClearSessionResponse>("/api/glossary/session/clear", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function saveSettings(settings: Partial<UserSettings>): Promise<ConfigResponse> {
  return request<ConfigResponse>("/api/config", {
    method: "PUT",
    body: JSON.stringify({ settings }),
  });
}

export function getSummary(): Promise<SummarySnapshot> {
  return request<SummarySnapshot>("/api/summary");
}

export function refreshSummary(): Promise<SummarySnapshot> {
  return request<SummarySnapshot>("/api/summary/refresh", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function getLatestSession(): Promise<LatestSessionResponse> {
  return request<LatestSessionResponse>("/api/session/latest");
}

export async function getSessionExportUrl(
  sessionId: string,
  format: "md" | "srt" | "json",
): Promise<string> {
  const url = await resolveApiUrl("/api/export");
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}session_id=${encodeURIComponent(sessionId)}&format=${format}`;
}

export function switchSession(payload: {
  source_lang?: string;
  target_lang: string;
}): Promise<SessionSwitchResponse> {
  return request<SessionSwitchResponse>("/api/session/switch", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function saveApiKey(apiKey: string): Promise<{
  api_key_masked: string;
  key_mask: string;
  needs_setup: boolean;
}> {
  return request("/api/config/secrets", {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function testProvider(payload: {
  provider: string;
  source_lang: string;
  target_lang: string;
  workspace_id?: string;
}): Promise<ProviderTestResult> {
  return request<ProviderTestResult>("/api/providers/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
