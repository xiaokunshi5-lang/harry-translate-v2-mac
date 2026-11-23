import type {
  BilingualLayoutOrder,
  SubtitleEmphasis,
  SubtitleDisplayMode,
} from "../../../shared/types";
import {
  MAX_TIMELINE_ENTRIES,
  type SubtitlePipelineState,
  type TimelineEntry,
} from "../../../lib/subtitles/subtitlePipeline";

export type SubtitleRole = "source" | "target";
export { MAX_TIMELINE_ENTRIES };

export interface LaneBudget {
  source: number;
  target: number;
}

export function emphasisStyle(
  role: SubtitleRole,
  emphasis: SubtitleEmphasis,
): { fontWeight: number; opacity: number } {
  if (emphasis === "balanced") return { fontWeight: 500, opacity: 0.92 };
  const primary =
    (emphasis === "translation_primary" && role === "target") ||
    (emphasis === "source_primary" && role === "source");
  return primary
    ? { fontWeight: 600, opacity: 1 }
    : { fontWeight: 400, opacity: 0.74 };
}

export function orderedSubtitleRoles(
  layoutOrder: BilingualLayoutOrder,
): SubtitleRole[] {
  return layoutOrder === "target_top"
    ? ["target", "source"]
    : ["source", "target"];
}

export function timelineEntries(
  state: SubtitlePipelineState,
  _visibleCount: number | "all",
): TimelineEntry[] {
  const history = state.entries.slice(-MAX_TIMELINE_ENTRIES);
  return state.draft ? [...history, state.draft] : history;
}

export function timelineContentKey(state: SubtitlePipelineState): string {
  const last = state.draft ?? state.entries.at(-1);
  if (!last) return "empty";
  return [
    state.revision,
    last.sentId ?? "draft",
    last.source.length,
    last.target.length,
    last.sourceHash,
  ].join(":");
}

export function subtitleLaneBudget(
  displayMode: SubtitleDisplayMode,
  hasSource: boolean,
  hasTarget: boolean,
  availableLaneHeight: number | null,
  sourceFontSize: number,
  targetFontSize: number,
  lineHeightMultiplier: number,
  measured: { source: number; target: number } | null = null,
): LaneBudget {
  if (availableLaneHeight === null || availableLaneHeight <= 0) {
    return { source: 0, target: 0 };
  }

  const sourceLine = Math.max(1, sourceFontSize * lineHeightMultiplier);
  const targetLine = Math.max(1, targetFontSize * lineHeightMultiplier);
  const linesThatFit = (lineHeight: number, remaining: number) =>
    Math.max(1, Math.floor(remaining / lineHeight));
  const sourceVisible = hasSource && displayMode !== "translation";
  const targetVisible = hasTarget && displayMode !== "original";

  if (sourceVisible && !targetVisible) {
    return {
      source: linesThatFit(sourceLine, availableLaneHeight),
      target: 0,
    };
  }
  if (targetVisible && !sourceVisible) {
    return {
      source: 0,
      target: linesThatFit(targetLine, availableLaneHeight),
    };
  }
  if (!sourceVisible && !targetVisible) {
    return { source: 0, target: 0 };
  }

  const sourceAtHalf = linesThatFit(sourceLine, availableLaneHeight * 0.5);
  const targetAtHalf = linesThatFit(targetLine, availableLaneHeight * 0.5);
  if (!measured) {
    return {
      source: sourceAtHalf,
      target: linesThatFit(
        targetLine,
        availableLaneHeight - sourceAtHalf * sourceLine,
      ),
    };
  }

  const sourceNeeded = Math.max(
    1,
    Math.ceil(Math.max(0, measured.source - 1) / sourceLine),
  );
  const targetNeeded = Math.max(
    1,
    Math.ceil(Math.max(0, measured.target - 1) / targetLine),
  );

  if (sourceNeeded <= sourceAtHalf && targetNeeded > targetAtHalf) {
    return {
      source: sourceNeeded,
      target: linesThatFit(
        targetLine,
        availableLaneHeight - sourceNeeded * sourceLine,
      ),
    };
  }
  if (targetNeeded <= targetAtHalf && sourceNeeded > sourceAtHalf) {
    return {
      source: linesThatFit(
        sourceLine,
        availableLaneHeight - targetNeeded * targetLine,
      ),
      target: targetNeeded,
    };
  }

  return {
    source: sourceAtHalf,
    target: linesThatFit(
      targetLine,
      availableLaneHeight - sourceAtHalf * sourceLine,
    ),
  };
}

export function estimateWrappedHeight(
  text: string,
  fontSizePx: number,
  lineHeightMultiplier: number,
  containerWidthPx: number,
  minLineHeightPx = 1,
): number {
  if (!text) return 0;
  const glyphWidth = Math.max(1, fontSizePx * 0.58);
  const maxUnitsPerLine = Math.max(
    1,
    Math.floor(Math.max(1, containerWidthPx) / glyphWidth),
  );
  const lines = Math.max(1, Math.ceil(Array.from(text).length / maxUnitsPerLine));
  return Math.max(minLineHeightPx, lines * fontSizePx * lineHeightMultiplier);
}
