import type { EngineEvent } from "../../shared/types";

export const MAX_TIMELINE_ENTRIES = 1000;

export interface TimelineEntry {
  key: string;
  windowId: "main";
  audioSource: string;
  source: string;
  target: string;
  previousSource: string;
  previousTarget: string;
  final: boolean;
  sentId: number | null;
  sourceHash: string;
  mergedWithPrevId?: number;
  paraId: string;
  patchVersion: number;
}

export interface SubtitlePipelineState {
  entries: TimelineEntry[];
  draft: TimelineEntry | null;
  lastSentId: number;
  revision: number;
  stalePatchDropped: number;
}

export interface SubtitlePatchResult {
  state: SubtitlePipelineState;
  applied: boolean;
}

export interface LcpSegments {
  stable: string;
  tail: string;
  prefixLength: number;
}

export function createSubtitlePipelineState(): SubtitlePipelineState {
  return {
    entries: [],
    draft: null,
    lastSentId: 0,
    revision: 0,
    stalePatchDropped: 0,
  };
}

export function longestCommonPrefix(previous: string, next: string): number {
  const limit = Math.min(previous.length, next.length);
  let index = 0;
  while (index < limit && previous[index] === next[index]) index += 1;
  return index;
}

export function splitAtStablePrefix(
  previous: string,
  next: string,
): LcpSegments {
  const prefixLength = longestCommonPrefix(previous, next);
  return {
    stable: next.slice(0, prefixLength),
    tail: next.slice(prefixLength),
    prefixLength,
  };
}

function incomingText(
  event: EngineEvent,
  field: "source" | "target",
  fallback: string,
): string {
  const textKey = field === "source" ? "source_text" : "target_text";
  const value = event[textKey] ?? event[field];
  return value === undefined ? fallback : String(value);
}

function isFinalEvent(event: EngineEvent): boolean {
  return event.is_final === true || event.type === "subtitle.final";
}

export function applySubtitleEvent(
  state: SubtitlePipelineState,
  event: EngineEvent,
): SubtitlePipelineState {
  if (event.type !== "subtitle" && event.type !== "subtitle.final") return state;

  const source = incomingText(event, "source", state.draft?.source ?? "");
  const target = incomingText(event, "target", state.draft?.target ?? "");
  if (source.trim() === "" && target.trim() === "") return state;
  const audioSource = String(
    event.audio_source ?? state.draft?.audioSource ?? "microphone",
  );
  const final = isFinalEvent(event);

  if (!final) {
    const previous = state.draft;
    const draft: TimelineEntry = {
      key: "draft-main",
      windowId: "main",
      audioSource,
      source,
      target,
      previousSource: previous?.source ?? "",
      previousTarget: previous?.target ?? "",
      final: false,
      sentId: null,
      sourceHash: "",
      paraId: event.para_id ?? "",
      patchVersion: 0,
    };
    return {
      ...state,
      draft,
      revision: state.revision + 1,
    };
  }

  const explicitIdValue = event.sent_id ?? event.sentence_id ?? event.id;
  const hasExplicitId =
    explicitIdValue !== undefined &&
    Number.isFinite(Number(explicitIdValue));
  const candidateId = hasExplicitId
    ? Math.trunc(Number(explicitIdValue))
    : state.lastSentId + 1;
  const sentId = candidateId;
  const mergedWithPrevId =
    event.merged_with_prev_id === undefined
      ? undefined
      : Number(event.merged_with_prev_id);
  const sourceHash = event.source_hash ?? "";
  const paraId = event.para_id ?? "";

  if (mergedWithPrevId !== undefined) {
    const matchedIndex = state.entries.findIndex(
      (entry) => entry.sentId === mergedWithPrevId,
    );
    if (matchedIndex >= 0) {
      const entries = state.entries.slice();
      const matched = entries[matchedIndex];
      entries[matchedIndex] = {
        ...matched,
        source,
        target,
        previousSource: matched.source,
        previousTarget: matched.target,
        sourceHash,
        mergedWithPrevId,
        paraId,
        patchVersion:
          matched.sourceHash === sourceHash ? matched.patchVersion : 0,
      };
      return {
        entries,
        draft: null,
        lastSentId: sentId,
        revision: state.revision + 1,
        stalePatchDropped: state.stalePatchDropped,
      };
    }
  }

  if (hasExplicitId && candidateId <= state.lastSentId) {
    const matchedIndex = state.entries.findIndex(
      (entry) => entry.sentId === candidateId,
    );
    if (matchedIndex < 0) return state;
    const entries = state.entries.slice();
    entries[matchedIndex] = {
      ...entries[matchedIndex],
      source,
      target,
      sourceHash,
      paraId,
      patchVersion:
        entries[matchedIndex].sourceHash === sourceHash
          ? entries[matchedIndex].patchVersion
          : 0,
    };
    return {
      ...state,
      entries,
      draft: null,
      revision: state.revision + 1,
    };
  }

  const draft = state.draft;
  const entry: TimelineEntry = {
    key: `sent-main-${sentId}-${state.entries.length}`,
    windowId: "main",
    audioSource,
    source,
    target,
    previousSource: draft?.source ?? "",
    previousTarget: draft?.target ?? "",
    final: true,
    sentId,
    sourceHash,
    mergedWithPrevId,
    paraId,
    patchVersion: 0,
  };
  return {
    entries: [...state.entries, entry].slice(-MAX_TIMELINE_ENTRIES),
    draft: null,
    lastSentId: sentId,
    revision: state.revision + 1,
    stalePatchDropped: state.stalePatchDropped,
  };
}

function droppedPatch(state: SubtitlePipelineState): SubtitlePatchResult {
  return {
    state: {
      ...state,
      stalePatchDropped: state.stalePatchDropped + 1,
    },
    applied: false,
  };
}

export function applySubtitlePatch(
  state: SubtitlePipelineState,
  event: EngineEvent,
): SubtitlePatchResult {
  if (event.type !== "transcript.patch") return droppedPatch(state);

  const sentId = event.sent_id;
  const patchHashPattern = /^[0-9a-f]{12}$/;
  const hasPatch =
    event.window_id === "main" &&
    typeof sentId === "number" &&
    Number.isFinite(sentId) &&
    typeof event.source_hash === "string" &&
    patchHashPattern.test(event.source_hash) &&
    typeof event.patched_source === "string" &&
    event.patched_source.length > 0 &&
    typeof event.patched_target === "string" &&
    event.patched_target.length > 0 &&
    typeof event.new_source_hash === "string" &&
    patchHashPattern.test(event.new_source_hash);
  if (!hasPatch) return droppedPatch(state);

  const matchedIndex = state.entries.findIndex(
    (entry) =>
      entry.final &&
      entry.patchVersion === 0 &&
      entry.sentId === sentId &&
      entry.sourceHash === event.source_hash,
  );
  if (matchedIndex < 0) return droppedPatch(state);

  const entries = state.entries.slice();
  const matched = entries[matchedIndex];
  entries[matchedIndex] = {
    ...matched,
    source: event.patched_source as string,
    target: event.patched_target as string,
    previousSource: matched.source,
    previousTarget: matched.target,
    sourceHash: event.new_source_hash as string,
    patchVersion: matched.patchVersion + 1,
  };
  return {
    state: {
      ...state,
      entries,
      revision: state.revision + 1,
    },
    applied: true,
  };
}

export function timelineRevision(state: SubtitlePipelineState): number {
  return state.revision;
}
