import {
  isTauriRuntime,
  listenTauriEvent,
  tauriInvoke,
} from "../../../lib/backendBridge";
import type { SubtitleDisplayMode } from "../../../shared/types";

export type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export type OverlayMode = "expanded" | "collapsed";

export interface OverlayFrame {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface OverlaySnapshot {
  maxOverlayWindows: number;
  activeWindows: string[];
  mode: OverlayMode;
  displayMode?: SubtitleDisplayMode;
  locked: boolean;
  clickThrough: boolean;
  hoverUnlocked: boolean;
  immersive: boolean;
  userFrame: OverlayFrame;
  presentationFrame: OverlayFrame;
}

export const OVERLAY_METRICS = {
  MAX_OVERLAY_WINDOWS: 1,
  DEFAULT_WIDTH: 580,
  DEFAULT_HEIGHT: 240,
  MIN_WIDTH: 360,
  MIN_HEIGHT: 100,
  MAX_WIDTH: 1200,
  MAX_HEIGHT: 600,
  COLLAPSED_WIDTH: 280,
  COLLAPSED_HEIGHT: 54,
  ONLY_MODE_HEIGHT_RATIO: 0.62,
} as const;

export function computeResizeForOnlyMode(
  frame: OverlayFrame,
  previousMode: SubtitleDisplayMode,
  nextMode: SubtitleDisplayMode,
  bilingualBaseHeight: number,
): { frame: OverlayFrame; bilingualBaseHeight: number } {
  const wasOnly = previousMode !== "bilingual";
  const isOnly = nextMode !== "bilingual";
  if (wasOnly === isOnly) {
    return { frame, bilingualBaseHeight };
  }
  const bottomAnchor = frame.y + frame.height;
  const nextBaseHeight = isOnly ? frame.height : bilingualBaseHeight;
  const rawHeight = isOnly
    ? Math.round(nextBaseHeight * OVERLAY_METRICS.ONLY_MODE_HEIGHT_RATIO)
    : Math.round(nextBaseHeight);
  const clamped = clampExpandedDimensions(frame.width, rawHeight);
  return {
    bilingualBaseHeight: nextBaseHeight,
    frame: {
      ...frame,
      width: clamped.width,
      height: clamped.height,
      y: Math.max(0, Math.round(bottomAnchor - clamped.height)),
    },
  };
}

export function clampExpandedDimensions(
  width: number,
  height: number,
): { width: number; height: number } {
  return {
    width: Math.min(
      OVERLAY_METRICS.MAX_WIDTH,
      Math.max(OVERLAY_METRICS.MIN_WIDTH, Math.round(width)),
    ),
    height: Math.min(
      OVERLAY_METRICS.MAX_HEIGHT,
      Math.max(OVERLAY_METRICS.MIN_HEIGHT, Math.round(height)),
    ),
  };
}

export async function invokeOverlayState(): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_get_state");
}

export async function invokeOverlayMode(
  mode: OverlayMode,
): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_set_mode", { mode });
}

export async function invokeOverlayLocked(
  locked: boolean,
): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_set_locked", { locked });
}

export async function invokeOverlaySyncPresentation(
  immersive: boolean,
  locked: boolean,
  clickThrough: boolean,
  displayMode?: SubtitleDisplayMode,
  persist = false,
): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_sync_presentation", {
    immersive,
    locked,
    clickThrough,
    persist,
    ...(displayMode ? { displayMode } : {}),
  });
}

export async function invokeOverlayStartDrag(): Promise<void> {
  if (!isTauriRuntime()) return;
  await tauriInvoke<void>("overlay_start_drag");
}

export async function invokeOverlayResize(
  edge: ResizeEdge,
  dx: number,
  dy: number,
): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_resize", { edge, dx, dy });
}

export async function invokeOverlayCommitGeometry(): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_commit_geometry");
}

export async function invokeOverlayGeometry(
  frame: OverlayFrame,
): Promise<OverlaySnapshot | null> {
  if (!isTauriRuntime()) return null;
  return tauriInvoke<OverlaySnapshot>("overlay_set_geometry", { ...frame });
}

export function listenOverlayPresentationChanged(
  handler: (snapshot: OverlaySnapshot) => void,
): Promise<() => void> {
  if (!isTauriRuntime()) return Promise.resolve(() => undefined);
  return listenTauriEvent<OverlaySnapshot>("overlay-presentation-changed", handler);
}
