const INTERACTIVE_SELECTOR = [
  "button",
  "a",
  "input",
  "select",
  "textarea",
  "label",
  "[role='button']",
  "[data-no-drag='true']",
  "[data-resize-edge]",
].join(",");

const SCROLLBAR_TRACK_PX = 16;

function pointerClientX(target: Element): number | null {
  const view = target.ownerDocument.defaultView as
    | (Window & { event?: MouseEvent })
    | null;
  const currentEvent = view?.event;
  return currentEvent && Number.isFinite(currentEvent.clientX)
    ? currentEvent.clientX
    : null;
}

function hitsTimelineScrollbar(target: Element): boolean {
  const scroller = target.closest(".overlay-timeline");
  if (!(scroller instanceof HTMLElement)) return false;
  if (scroller.scrollHeight <= scroller.clientHeight + 2) return false;

  const pointerX = pointerClientX(target);
  if (pointerX !== null) {
    const rect = scroller.getBoundingClientRect();
    const borderRight = Number.parseFloat(
      scroller.ownerDocument.defaultView?.getComputedStyle(scroller)
        .borderRightWidth || "0",
    );
    const trackStart =
      rect.right -
      (Number.isFinite(borderRight) ? borderRight : 0) -
      SCROLLBAR_TRACK_PX;
    if (pointerX >= trackStart) return true;
  }

  return target === scroller;
}

export function shouldStartOverlayDrag(
  target: EventTarget | null,
  button: number,
): boolean {
  if (button !== 0 || !(target instanceof Element)) {
    return false;
  }
  if (target.closest(INTERACTIVE_SELECTOR)) {
    return false;
  }
  if (hitsTimelineScrollbar(target)) return false;
  return true;
}
