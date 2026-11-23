export type LanguageCode =
  | "auto"
  | "zh"
  | "en"
  | "fr"
  | "es"
  | "it"
  | "ja"
  | "ko"
  | "de";

export type SubtitleDisplayMode = "translation" | "original" | "bilingual";
export type SubtitleAlignment = "left" | "center" | "right";
export type SubtitleEmphasis =
  | "translation_primary"
  | "source_primary"
  | "balanced";
export type BilingualLayoutOrder = "source_top" | "target_top";
export type CorrectionMode = "llm" | "rules";
export type CorrectionModel = "qwen-turbo" | "qwen-flash";
export type SummaryMode = "bailian" | "rules";
export type SummaryModel = "qwen-turbo" | "qwen-flash";
export type AudioInputMode = "microphone" | "system" | "both";

export type OverlayMode = "expanded" | "collapsed";

export interface HudGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
  mode: OverlayMode;
  locked: boolean;
}

export interface UserSettings {
  active_provider: string;
  active_model: string;
  provider: string;
  model: string;
  source_lang: LanguageCode;
  target_lang: Exclude<LanguageCode, "auto">;
  audio_input: AudioInputMode;
  input_device: string | null;
  audio_device: string | null;
  workspace_id: string;
  font_size: number;
  source_font_size: number;
  target_font_size: number;
  subtitle_emphasis: SubtitleEmphasis;
  bilingual_layout_order: BilingualLayoutOrder;
  glossary_enabled: boolean;
  glossary_wire_budget_bytes: number;
  overlay_window_mode: "single";
  overlay_immersive: boolean;
  overlay_click_through: boolean;
  hud_geometry?: HudGeometry;
  background_opacity: number;
  subtitle_display_mode: SubtitleDisplayMode;
  subtitle_color: string;
  mic_subtitle_color: string;
  system_subtitle_color: string;
  subtitle_alignment: SubtitleAlignment;
  line_height_multiplier: number;
  show_subtitle_dividers: boolean;
  visible_subtitle_count: number | "all";
  is_overlay_locked: boolean;
  correction_enabled: boolean;
  correction_mode: CorrectionMode;
  correction_model: CorrectionModel;
  correction_gap_ms: number;
  summary_enabled: boolean;
  summary_mode?: SummaryMode;
  summary_model?: SummaryModel;
  summary_min_words?: number;
  summary_min_interval_sec?: number;
  ui_appearance: {
    density: "comfortable" | "compact";
    accent: string;
  };
  windows: Array<Record<string, unknown>>;
}

export interface ConfigResponse {
  settings: UserSettings;
  user_settings: UserSettings;
  key_mask: string;
  api_key_masked: string;
  needs_setup: boolean;
  provider_catalog?: ProviderCatalog;
  engine_states?: Record<string, EngineEvent>;
}

export interface ProviderCatalogEntry {
  id: string;
  label: string;
  engine: string;
  model: string;
  default: boolean;
  connection_rpm: number;
  source_detection: string;
  source_languages: LanguageCode[];
  target_languages: Exclude<LanguageCode, "auto">[];
  output_modalities: string[];
  limitations: string[];
}

export interface ProviderCatalog {
  schema_version: number;
  default_provider: string;
  providers: Record<string, ProviderCatalogEntry>;
}

export interface GlossaryPack {
  id: string;
  name: string;
  icon: string;
  tier: string;
  priority: number;
  locked: boolean;
  default_enabled: boolean;
  enabled: boolean;
  terms_count: number;
  counts_by_category: Record<string, number>;
}

export type GlossaryBucket = "asr_fixes" | "terms_trans" | "target_fixes";

export interface GlossaryQuota {
  cloud_cap: number;
  provider_limit: number;
  active_terms: number;
  local_terms: number;
  session_terms?: number;
  wire_bytes: number;
  wire_budget_bytes: number;
}

export interface GlossaryPacksResponse {
  packs: GlossaryPack[];
  quota: GlossaryQuota;
  loading_stage: string;
  errors: string[];
}

export interface GlossaryEntry {
  source: string;
  target: string;
  category: string;
  priority: number;
  scope: string;
  pack_id: string;
  bucket?: GlossaryBucket;
  lang_key?: string;
}

export interface GlossarySessionResponse {
  items: GlossaryEntry[];
  count: number;
  quota: GlossaryQuota;
}

export interface GlossaryAddSessionPayload {
  source?: string;
  target?: string;
  expr?: string;
  bucket: GlossaryBucket;
  lang_key: string;
  direction?: string;
}

export interface GlossaryAddSessionResponse {
  ok: boolean;
  item: GlossaryEntry;
  session_items: GlossaryEntry[];
  pushed_channels: string[];
  elapsed_ms: number;
  quota: GlossaryQuota;
}

export interface GlossaryPromotePayload {
  source: string;
  target?: string;
  bucket: GlossaryBucket;
  lang_key: string;
  direction?: string;
}

export interface GlossaryPromoteResponse {
  ok: boolean;
  item: GlossaryEntry;
  session_items: GlossaryEntry[];
  quota: GlossaryQuota;
}

export interface GlossaryClearSessionResponse {
  ok: boolean;
  cleared: number;
  session_items: GlossaryEntry[];
  pushed_channels: string[];
  quota: GlossaryQuota;
}

export interface GlossaryTermsResponse {
  total: number;
  limit: number;
  offset: number;
  counts_by_category: Record<string, number>;
  loading_stage: string;
  items: GlossaryEntry[];
  quota: GlossaryQuota;
}

export interface AudioDevice {
  name: string;
  index?: number;
  channels?: number;
  default_samplerate?: number;
}

export interface HealthResponse {
  status: string;
  port: number;
  python_version: string;
  audio_backend: string;
  audio_devices: AudioDevice[];
  audio_error: { code: string; message: string; hint: string } | null;
  env_exists: boolean;
  needs_setup: boolean;
  key_mask: string;
  permission_hint: string;
  fast?: boolean;
  service?: string;
  pid?: number;
  project_root?: string;
  frontend_fingerprint?: string;
}

export interface AudioInputProbe {
  backend: string;
  status: "available" | "loopback_missing" | "unavailable" | string;
  loopback_available: boolean;
  loopback_devices: AudioDevice[];
  input_devices: AudioDevice[];
  hint: string;
}

export interface AudioInputSelectionResponse {
  ok: boolean;
  status: string;
  channel_b_status?: string;
  system_audio_idle?: boolean;
  hint?: string;
  settings?: UserSettings;
}

export interface OverlayStatus {
  running: boolean;
  visible?: boolean;
  registered?: boolean;
  binary_available?: boolean;
  fallback?: string | null;
  control_url?: string | null;
}

export interface OverlayControlResponse extends Partial<OverlayStatus> {
  ok?: boolean;
  action?: string;
  message?: string;
  fallback_url?: string | null;
}

export interface ProviderTestResult {
  ok: boolean;
  provider: string;
  routed_endpoint: string;
  latency_ms: number | null;
  error_code: string | null;
}

export interface SessionSwitchResponse {
  ok: boolean;
  settings: UserSettings;
  user_settings: UserSettings;
  engine_state: EngineEvent;
}

export interface EngineEvent {
  type: string;
  settings?: UserSettings;
  needs_setup?: boolean;
  key_mask?: string;
  state?: string;
  reason?: string;
  health_status?: string;
  corpus_suspended?: boolean;
  reconnect_attempt?: number;
  backoff_seconds?: number;
  channel?: string;
  source?: string;
  target?: string;
  source_text?: string;
  target_text?: string;
  source_lang?: string;
  target_lang?: string;
  is_final?: boolean;
  id?: number;
  sentence_id?: number;
  window_id?: string;
  audio_source?: string;
  sent_id?: number;
  source_hash?: string;
  merged_with_prev_id?: number;
  para_id?: string;
  patched_source?: string;
  patched_target?: string;
  new_source_hash?: string;
  ttft_ms?: number | null;
  latency_ms?: number | null;
  enabled?: boolean;
  mode?: SummaryMode;
  model?: SummaryModel;
  engine_used?: string;
  pending_words?: number;
  pinned?: SummaryItem[];
  archived?: SummaryItem[];
}

export interface SummaryItem {
  level: number;
  text: string;
  category: string;
}

export interface SummarySnapshot {
  enabled: boolean;
  mode?: SummaryMode;
  model?: SummaryModel;
  engine_used?: string;
  pending_words?: number;
  pinned: SummaryItem[];
  archived: SummaryItem[];
}

export interface StoredSessionEntry {
  window_id: "main";
  sent_id: number;
  source: string;
  target: string;
  patched_source?: string;
  patched_target?: string;
  start_ms?: number;
  end_ms?: number;
  channel?: string;
  target_lang?: string;
  source_hash?: string;
  para_id?: string;
  merged_with_prev_id?: number;
}

export interface StoredSession {
  id: string;
  session_id?: string;
  clientSavedAt: number;
  created_at?: string;
  audio_source?: string;
  source_lang?: string;
  target_lang?: string;
  entries: StoredSessionEntry[];
  summary_items?: SummaryItem[];
}

export interface LatestSessionResponse {
  ok: boolean;
  session: StoredSession | null;
}

export interface SubtitleHistoryItem {
  window_id: "main";
  audio_source: string;
  source: string;
  target: string;
  is_final: true;
  sent_id: number;
  source_hash: string;
  merged_with_prev_id?: number;
  para_id: string;
}
