// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { shouldStartOverlayDrag } from "./overlayContentDrag";

function scrollableTimeline(): HTMLDivElement {
  const scroller = document.createElement("div");
  scroller.className = "overlay-timeline";
  Object.defineProperties(scroller, {
    scrollHeight: { configurable: true, value: 700 },
    clientHeight: { configurable: true, value: 300 },
  });
  scroller.getBoundingClientRect = () =>
    ({
      x: 0,
      y: 0,
      width: 600,
      height: 300,
      top: 0,
      right: 600,
      bottom: 300,
      left: 0,
      toJSON: () => ({}),
    }) as DOMRect;
  return scroller;
}

describe("overlayContentDrag", () => {
  afterEach(() => {
    delete (window as Window & { event?: MouseEvent }).event;
  });

  it("rejects the scrollbar track while keeping subtitle text draggable", () => {
    const scroller = scrollableTimeline();
    const text = document.createElement("span");
    scroller.appendChild(text);

    Object.defineProperty(window, "event", {
      configurable: true,
      value: { clientX: 590 },
    });
    expect(shouldStartOverlayDrag(text, 0)).toBe(false);

    Object.defineProperty(window, "event", {
      configurable: true,
      value: { clientX: 320 },
    });
    expect(shouldStartOverlayDrag(text, 0)).toBe(true);
    expect(shouldStartOverlayDrag(scroller, 0)).toBe(false);
  });
});
