import { resolveApiUrl, resolveWsUrl } from "../lib/backendBridge";
import type { EngineEvent, SummaryItem } from "./types";

export type SocketPhase = "idle" | "connecting" | "open" | "closed";

export interface ChannelSocketOptions {
  onEvent: (event: EngineEvent) => void;
  onPhase: (phase: SocketPhase) => void;
}

const RECOVERY_CONTINUITY_MS = 10_000;
const SESSION_DRAFT_KEY = "harry-translate-session-draft-v1";
const SESSION_SAVE_DEBOUNCE_MS = 2_000;

export type SessionExportFormat = "md" | "srt" | "json";

export interface TranscriptSessionEntry {
  window_id: "main";
  sent_id: number;
  source: string;
  target: string;
  patched_source?: string;
  patched_target?: string;
  start_ms: number;
  end_ms: number;
  channel: string;
  target_lang: string;
  source_hash: string;
  para_id?: string;
  merged_with_prev_id?: number;
}

export interface TranscriptSessionSnapshot {
  id: string;
  session_id: string;
  clientSavedAt: number;
  created_at: string;
  audio_source: string;
  source_lang: string;
  target_lang: string;
  entries: TranscriptSessionEntry[];
  summary_items: SummaryItem[];
}

type SessionRecorderOptions = {
  debounceMs?: number;
  fetcher?: typeof fetch;
  now?: () => number;
  storage?: Storage | null;
};

function createSessionId(): string {
  const suffix = Math.random().toString(36).slice(2, 8);
  return `session-${Date.now().toString(36)}-${suffix}`;
}

class InMemoryDraftStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

function safeStorage(storage?: Storage | null): Storage {
  return storage ?? new InMemoryDraftStorage();
}

function finiteNumber(value: unknown, fallback: number): number {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
}

export class TranscriptSessionRecorder {
  private readonly debounceMs: number;
  private readonly fetcher?: typeof fetch;
  private readonly now: () => number;
  private readonly storage: Storage;
  private sessionId = createSessionId();
  private createdAt = new Date().toISOString();
  private startedAt = 0;
  private audioSource = "microphone";
  private sourceLang = "auto";
  private targetLang = "zh";
  private entries = new Map<number, TranscriptSessionEntry>();
  private summaryItems: SummaryItem[] = [];
  private nextFallbackSentId = 1;
  private saveTimer: number | null = null;
  private saving = false;
  private pendingSnapshots = new Map<string, TranscriptSessionSnapshot>();
  private installedFlushHooks = false;

  constructor(options: SessionRecorderOptions = {}) {
    this.debounceMs = options.debounceMs ?? SESSION_SAVE_DEBOUNCE_MS;
    this.fetcher = options.fetcher;
    this.now = options.now ?? (() => Date.now());
    this.storage = safeStorage(options.storage);
    this.startedAt = this.now();
    this.restoreLocalDraft();
    this.installFlushHooks();
  }

  get currentSessionId(): string {
    return this.sessionId;
  }

  get count(): number {
    return this.entries.size;
  }

  observe(event: EngineEvent): void {
    if (event.type === "config_updated" && event.settings) {
      this.audioSource = String(event.settings.audio_input || this.audioSource);
      this.sourceLang = String(event.settings.source_lang || this.sourceLang);
      this.targetLang = String(event.settings.target_lang || this.targetLang);
      return;
    }
    if (event.type === "session_reset") {
      void this.flush();
      this.startNewSession();
      return;
    }
    if (event.type === "summary.update") {
      const next = [...(event.pinned || []), ...(event.archived || [])];
      this.summaryItems = next.filter((item) => item.text.trim().length > 0);
      this.scheduleSave();
      return;
    }
    if (event.type === "transcript.patch") {
      if (this.applyPatch(event)) this.scheduleSave();
      return;
    }
    if (event.type !== "subtitle" && event.type !== "subtitle.final") return;
    if (!(event.is_final === true || event.type === "subtitle.final")) return;

    const source = String(event.source_text ?? event.source ?? "");
    const target = String(event.target_text ?? event.target ?? "");
    if (!source.trim() && !target.trim()) return;
    const sentId = this.sentIdForEvent(event);
    const existing = this.entries.get(sentId);
    const mergedId = finiteNumber(event.merged_with_prev_id, -1);
    const targetId = mergedId >= 0 && this.entries.has(mergedId) ? mergedId : sentId;
    const previous = this.entries.get(targetId);
    const endMs = Math.max(0, this.now() - this.startedAt);
    const entry: TranscriptSessionEntry = {
      window_id: "main",
      sent_id: targetId,
      source,
      target,
      start_ms: previous?.start_ms ?? endMs,
      end_ms: Math.max(endMs, previous?.start_ms ?? 0),
      channel: "main",
      target_lang: this.targetLang,
      source_hash: String(event.source_hash || ""),
      ...(event.para_id ? { para_id: event.para_id } : {}),
      ...(mergedId >= 0 ? { merged_with_prev_id: mergedId } : {}),
    };
    this.entries.set(targetId, entry);
    if (existing && targetId !== sentId) this.entries.delete(sentId);
    this.audioSource = String(event.audio_source || this.audioSource);
    this.scheduleSave();
  }

  snapshot(clientSavedAt = this.now()): TranscriptSessionSnapshot {
    return {
      id: this.sessionId,
      session_id: this.sessionId,
      clientSavedAt,
      created_at: this.createdAt,
      audio_source: this.audioSource,
      source_lang: this.sourceLang,
      target_lang: this.targetLang,
      entries: [...this.entries.values()].sort((left, right) => left.sent_id - right.sent_id),
      summary_items: this.summaryItems,
    };
  }

  async flush(): Promise<void> {
    this.clearSaveTimer();
    if (this.entries.size === 0 && this.summaryItems.length === 0) return;
    await this.enqueueSave(this.snapshot());
  }

  private async enqueueSave(snapshot: TranscriptSessionSnapshot): Promise<void> {
    this.pendingSnapshots.set(snapshot.id, snapshot);
    if (this.saving) return;
    this.saving = true;
    try {
      while (this.pendingSnapshots.size > 0) {
        const first = this.pendingSnapshots.entries().next().value as
          | [string, TranscriptSessionSnapshot]
          | undefined;
        if (!first) break;
        const [sessionId, pending] = first;
        this.pendingSnapshots.delete(sessionId);
        const url = await resolveApiUrl("/api/session/save");
        const response = await (this.fetcher || fetch)(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(pending),
          keepalive: true,
        });
        if (!response.ok) throw new Error(`session_save_${response.status}`);
      }
    } catch {
      // The local draft remains recoverable and a later final event retries.
    } finally {
      this.saving = false;
      const next = this.pendingSnapshots.values().next().value as
        | TranscriptSessionSnapshot
        | undefined;
      if (next) void this.enqueueSave(next);
    }
  }

  async exportUrl(
    format: SessionExportFormat,
    sessionId = this.sessionId,
  ): Promise<string> {
    const url = await resolveApiUrl("/api/export");
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}session_id=${encodeURIComponent(sessionId)}&format=${format}`;
  }

  async download(format: SessionExportFormat, sessionId = this.sessionId): Promise<void> {
    if (typeof window === "undefined") return;
    const url = await this.exportUrl(format, sessionId);
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.rel = "noopener";
    anchor.click();
  }

  dispose(): void {
    this.clearSaveTimer();
  }

  private sentIdForEvent(event: EngineEvent): number {
    const candidate = finiteNumber(
      event.sent_id ?? event.sentence_id ?? event.id,
      this.nextFallbackSentId,
    );
    const sentId = Math.max(0, Math.trunc(candidate));
    this.nextFallbackSentId = Math.max(this.nextFallbackSentId + 1, sentId + 1);
    return sentId;
  }

  private applyPatch(event: EngineEvent): boolean {
    if (event.window_id !== "main" || typeof event.sent_id !== "number") return false;
    const entry = this.entries.get(event.sent_id);
    if (!entry || entry.source_hash !== event.source_hash) return false;
    if (typeof event.patched_source === "string") entry.patched_source = event.patched_source;
    if (typeof event.patched_target === "string") entry.patched_target = event.patched_target;
    if (typeof event.new_source_hash === "string") entry.source_hash = event.new_source_hash;
    return true;
  }

  private startNewSession(): void {
    this.sessionId = createSessionId();
    this.createdAt = new Date().toISOString();
    this.startedAt = this.now();
    this.entries.clear();
    this.summaryItems = [];
    this.nextFallbackSentId = 1;
    this.removeLocalDraft();
  }

  private scheduleSave(): void {
    this.persistLocalDraft();
    this.clearSaveTimer();
    if (typeof window === "undefined") return;
    this.saveTimer = window.setTimeout(() => {
      this.saveTimer = null;
      void this.flush();
    }, this.debounceMs);
  }

  private clearSaveTimer(): void {
    if (this.saveTimer === null) return;
    if (typeof window !== "undefined") window.clearTimeout(this.saveTimer);
    else clearTimeout(this.saveTimer);
    this.saveTimer = null;
  }

  private persistLocalDraft(): void {
    try {
      this.storage.setItem(SESSION_DRAFT_KEY, JSON.stringify(this.snapshot()));
    } catch {
      // A full or unavailable browser store must not affect the live stream.
    }
  }

  private restoreLocalDraft(): void {
    try {
      const raw = this.storage.getItem(SESSION_DRAFT_KEY);
      if (!raw) return;
      const value = JSON.parse(raw) as Partial<TranscriptSessionSnapshot>;
      if (!value.id || !Array.isArray(value.entries)) return;
      this.sessionId = String(value.id);
      this.createdAt = String(value.created_at || this.createdAt);
      this.audioSource = String(value.audio_source || this.audioSource);
      this.sourceLang = String(value.source_lang || this.sourceLang);
      this.targetLang = String(value.target_lang || this.targetLang);
      this.entries = new Map(
        value.entries
          .filter((entry): entry is TranscriptSessionEntry => Boolean(entry?.sent_id))
          .map((entry) => [entry.sent_id, entry]),
      );
      this.summaryItems = Array.isArray(value.summary_items) ? value.summary_items : [];
      this.startedAt = Math.min(
        ...[...this.entries.values()].map((entry) => entry.start_ms),
        this.now(),
      );
    } catch {
      this.removeLocalDraft();
    }
  }

  private removeLocalDraft(): void {
    try {
      this.storage.removeItem(SESSION_DRAFT_KEY);
    } catch {
      // Ignore private-mode storage failures.
    }
  }

  private installFlushHooks(): void {
    if (this.installedFlushHooks || typeof window === "undefined") return;
    this.installedFlushHooks = true;
    window.addEventListener("pagehide", () => {
      this.persistLocalDraft();
      void this.flush();
    });
    window.document.addEventListener("visibilitychange", () => {
      if (window.document.visibilityState === "hidden") {
        this.persistLocalDraft();
        void this.flush();
      }
    });
  }
}

export const transcriptSessionRecorder = new TranscriptSessionRecorder();

export class ChannelSocket {
  private socket: WebSocket | null = null;
  private retryTimer: number | null = null;
  private continuityTimer: number | null = null;
  private attempt = 0;
  private closedByUser = false;

  constructor(
    private readonly channel: string,
    private readonly options: ChannelSocketOptions,
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.clearRetry();
    this.clearContinuity();
    this.options.onPhase("connecting");
    void this.openResolvedSocket();
  }

  private async openResolvedSocket(): Promise<void> {
    let url: string;
    try {
      url = await resolveWsUrl(this.channel);
    } catch {
      this.options.onPhase("closed");
      if (!this.closedByUser) this.scheduleReconnect();
      return;
    }
    if (this.closedByUser) return;

    const socket = new WebSocket(url);
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.clearContinuity();
      this.continuityTimer = window.setTimeout(() => {
        this.attempt = 0;
        this.continuityTimer = null;
      }, RECOVERY_CONTINUITY_MS);
      this.options.onPhase("open");
    });
    socket.addEventListener("message", (message) => {
      try {
        const payload = JSON.parse(String(message.data)) as EngineEvent;
        if (payload && typeof payload.type === "string") {
          if (this.channel === "main") transcriptSessionRecorder.observe(payload);
          this.options.onEvent(payload);
        }
      } catch {
        // A malformed event is ignored; the stream remains usable.
      }
    });
    socket.addEventListener("close", () => {
      this.clearContinuity();
      if (this.socket === socket) this.socket = null;
      this.options.onPhase("closed");
      if (!this.closedByUser) this.scheduleReconnect();
    });
    socket.addEventListener("error", () => socket.close());
  }

  close(): void {
    this.closedByUser = true;
    this.clearRetry();
    this.clearContinuity();
    this.socket?.close();
    this.socket = null;
  }

  private scheduleReconnect(): void {
    this.clearRetry();
    const baseDelay = Math.min(8_000, 1_000 * 2 ** Math.min(this.attempt, 3));
    const jitter = ((this.attempt * 29) % 73) + 7;
    const delay = baseDelay + jitter;
    this.attempt += 1;
    this.retryTimer = window.setTimeout(() => this.connect(), delay);
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private clearContinuity(): void {
    if (this.continuityTimer !== null) {
      window.clearTimeout(this.continuityTimer);
      this.continuityTimer = null;
    }
  }
}
