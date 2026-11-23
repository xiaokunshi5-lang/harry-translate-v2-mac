import { describe, expect, it } from "vitest";
import {
  TIMELINE_LIVE_EDGE_PX,
  distanceFromLive,
  isTimelineNearLive,
  preserveTimelineAnchor,
  rollupGlideShift,
  shouldShowReturnToLive,
} from "./timelineScroll";

describe("timelineScroll", () => {
  it("locks the historical viewport instead of pulling it to live", () => {
    const metrics = { scrollTop: 120, scrollHeight: 600, clientHeight: 400 };
    expect(distanceFromLive(metrics)).toBe(80);
    expect(isTimelineNearLive(metrics)).toBe(false);
    expect(shouldShowReturnToLive(metrics)).toBe(true);
    expect(preserveTimelineAnchor(120, 600, 660)).toBe(180);
  });

  it("returns to live at the bottom edge", () => {
    const metrics = { scrollTop: 180, scrollHeight: 600, clientHeight: 400 };
    expect(distanceFromLive(metrics)).toBe(20);
    expect(isTimelineNearLive(metrics)).toBe(true);
    expect(shouldShowReturnToLive(metrics)).toBe(false);
    expect(TIMELINE_LIVE_EDGE_PX).toBe(24);
  });

  it("only glides when a followed line crosses the viewport budget", () => {
    expect(rollupGlideShift(0, 80, 200, true)).toBe(0);
    expect(rollupGlideShift(120, 160, 200, true)).toBe(0);
    expect(rollupGlideShift(180, 220, 200, true)).toBe(40);
    expect(rollupGlideShift(180, 220, 200, false)).toBe(0);
  });
});
