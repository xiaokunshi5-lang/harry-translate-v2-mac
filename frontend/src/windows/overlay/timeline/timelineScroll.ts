export const TIMELINE_LIVE_EDGE_PX = 24;
export const TIMELINE_GLIDE_MS = 180;

export interface TimelineScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export function distanceFromLive(metrics: TimelineScrollMetrics): number {
  return Math.max(
    0,
    metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight,
  );
}

export function isTimelineNearLive(metrics: TimelineScrollMetrics): boolean {
  return distanceFromLive(metrics) <= TIMELINE_LIVE_EDGE_PX;
}

export function preserveTimelineAnchor(
  previousScrollTop: number,
  previousScrollHeight: number,
  nextScrollHeight: number,
): number {
  const growth = Math.max(0, nextScrollHeight - previousScrollHeight);
  return Math.max(0, previousScrollTop + growth);
}

export function shouldShowReturnToLive(
  metrics: TimelineScrollMetrics,
): boolean {
  return distanceFromLive(metrics) > TIMELINE_LIVE_EDGE_PX;
}

export function rollupGlideShift(
  previousContentHeight: number,
  nextContentHeight: number,
  viewportHeight: number,
  followLive: boolean,
): number {
  if (!followLive || previousContentHeight <= 0) return 0;
  if (nextContentHeight <= viewportHeight) return 0;
  return Math.max(0, nextContentHeight - previousContentHeight);
}
