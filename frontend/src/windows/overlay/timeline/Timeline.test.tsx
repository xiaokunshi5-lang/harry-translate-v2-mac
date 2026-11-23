// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applySubtitleEvent,
  applySubtitlePatch,
  createSubtitlePipelineState,
  type SubtitlePipelineState,
} from "../../../lib/subtitles/subtitlePipeline";
import type { UserSettings } from "../../../shared/types";
import { Timeline, preserveBottomAppendAnchor } from "./Timeline";
import { subtitleLaneBudget } from "./overlayModel";

const SETTINGS: UserSettings = {
  active_provider: "qwen",
  active_model: "qwen3.8-livetranslate-flash-realtime",
  provider: "qwen",
  model: "qwen3.8-livetranslate-flash-realtime",
  source_lang: "auto",
  target_lang: "zh",
  audio_input: "microphone",
  input_device: null,
  audio_device: null,
  workspace_id: "",
  font_size: 18,
  source_font_size: 15,
  target_font_size: 22,
  subtitle_emphasis: "translation_primary",
  bilingual_layout_order: "source_top",
  glossary_enabled: true,
  glossary_wire_budget_bytes: 4096,
  overlay_window_mode: "single",
  overlay_immersive: false,
  overlay_click_through: true,
  hud_geometry: {
    x: 0,
    y: 0,
    width: 600,
    height: 320,
    mode: "expanded",
    locked: false,
  },
  background_opacity: 35,
  subtitle_display_mode: "bilingual",
  subtitle_color: "#FFFFFF",
  mic_subtitle_color: "#FFD54F",
  system_subtitle_color: "#4FD1C5",
  subtitle_alignment: "left",
  line_height_multiplier: 1.32,
  show_subtitle_dividers: true,
  visible_subtitle_count: 3,
  is_overlay_locked: false,
  correction_enabled: true,
  correction_mode: "llm",
  correction_model: "qwen-turbo",
  correction_gap_ms: 1500,
  summary_enabled: false,
  ui_appearance: { density: "comfortable", accent: "teal" },
  windows: [],
};

function finalPipeline(): SubtitlePipelineState {
  let pipeline = createSubtitlePipelineState();
  pipeline = applySubtitleEvent(pipeline, {
    type: "subtitle",
    source_text: "Hel",
    target_text: "你",
    is_final: false,
  });
  pipeline = applySubtitleEvent(pipeline, {
    type: "subtitle",
    source_text: "Hello world.",
    target_text: "你好，世界。",
    is_final: true,
    sent_id: 7,
    source_hash: "abcdef123456",
    para_id: "p2",
    window_id: "main",
    audio_source: "microphone",
  });
  return pipeline;
}

function finalizedPipeline(count: number): SubtitlePipelineState {
  let pipeline = createSubtitlePipelineState();
  for (let index = 1; index <= count; index += 1) {
    pipeline = applySubtitleEvent(pipeline, {
      type: "subtitle.final",
      source_text: `Source ${index}.`,
      target_text: `译文 ${index}。`,
      sent_id: index,
      source_hash: index.toString(16).padStart(12, "0"),
      window_id: "main",
    });
  }
  return pipeline;
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function renderTimeline(
  browserFallback: boolean,
  pipeline = finalPipeline(),
  settings = SETTINGS,
  immersive = false,
): HTMLDivElement {
  if (!container) {
    container = document.createElement("div");
    document.body.appendChild(container);
  }
  root = createRoot(container);
  act(() => {
    root?.render(
      <Timeline
        pipeline={pipeline}
        settings={settings}
        immersive={immersive}
        browserFallback={browserFallback}
      />,
    );
  });
  return container;
}

function rerenderTimeline(
  pipeline: SubtitlePipelineState,
  browserFallback = false,
  settings = SETTINGS,
  immersive = false,
): HTMLDivElement {
  if (!container || !root) {
    return renderTimeline(browserFallback, pipeline, settings, immersive);
  }
  act(() => {
    root?.render(
      <Timeline
        pipeline={pipeline}
        settings={settings}
        immersive={immersive}
        browserFallback={browserFallback}
      />,
    );
  });
  return container;
}

describe("Timeline", () => {
  beforeEach(() => {
    container = null;
    root = null;
  });

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    root = null;
    container = null;
  });

  it("renders LCP spans and final main identity without native controls", () => {
    const surface = renderTimeline(false);
    const card = surface.querySelector(
      ".overlay-timeline-entry.is-final",
    ) as HTMLElement;
    expect(card.dataset.windowId).toBe("main");
    expect(card.dataset.sentId).toBe("7");
    expect(card.dataset.sourceHash).toBe("abcdef123456");
    expect(card.dataset.paraId).toBe("p2");
    expect(surface.querySelector(".lcp-stable")?.textContent).toBe("Hel");
    expect(surface.querySelector(".lcp-tail")?.textContent).toBe("lo world.");
    expect(surface.querySelectorAll("button")).toHaveLength(0);
    expect(surface.querySelectorAll("a")).toHaveLength(0);
    expect(card.outerHTML).not.toContain("usage");
  });

  it("shows a browser-only return control after scrolling history", async () => {
    const surface = renderTimeline(true);
    const scroller = surface.querySelector(".overlay-timeline") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", {
      configurable: true,
      value: 700,
    });
    Object.defineProperty(scroller, "clientHeight", {
      configurable: true,
      value: 300,
    });
    scroller.scrollTop = 120;
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll"));
    });
    const button = surface.querySelector(
      ".timeline-return-live--button",
    ) as HTMLButtonElement;
    expect(button?.textContent).toContain("回到实时");
    await act(async () => {
      button.click();
    });
    expect(surface.querySelector(".timeline-return-live")).toBeNull();
  });

  it("patches a finalized card in place with a stable key and no warning UI", () => {
    const rect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        x: 0,
        y: 0,
        width: 560,
        height: 64,
        top: 0,
        right: 560,
        bottom: 64,
        left: 0,
        toJSON: () => ({}),
      });
    try {
      const surface = renderTimeline(false);
      const beforeCard = surface.querySelector(
        ".overlay-timeline-entry.is-final",
      ) as HTMLElement;
      const result = applySubtitlePatch(finalPipeline(), {
        type: "transcript.patch",
        window_id: "main",
        sent_id: 7,
        source_hash: "abcdef123456",
        patched_source: "Hello, corrected world.",
        patched_target: "你好，修正后的世界。",
        new_source_hash: "123456abcdef",
      });
      expect(result.applied).toBe(true);

      rerenderTimeline(result.state);
      const afterCard = surface.querySelector(
        ".overlay-timeline-entry.is-final",
      ) as HTMLElement;
      expect(afterCard).toBe(beforeCard);
      expect(afterCard.dataset.sourceHash).toBe("123456abcdef");
      expect(afterCard.textContent).toContain("Hello, corrected world.");
      expect(afterCard.textContent).toContain("你好，修正后的世界。");
      expect(afterCard.style.minHeight).toBe("64px");
      expect(afterCard.style.transition).toContain("opacity 150ms");

      rerenderTimeline(result.state, false, {
        ...SETTINGS,
        target_font_size: 28,
      });
      const resizedCard = surface.querySelector(
        ".overlay-timeline-entry.is-final",
      ) as HTMLElement;
      expect(resizedCard.style.minHeight).toBe("");
      expect(afterCard.outerHTML).not.toMatch(
        /usage|suspect|badge|warning|highlight-red/i,
      );
    } finally {
      rect.mockRestore();
    }
  });

  it("keeps a clean empty surface without placeholder copy", () => {
    const surface = renderTimeline(
      false,
      createSubtitlePipelineState(),
    );
    expect(surface.querySelectorAll(".overlay-subtitle-line")).toHaveLength(0);
    expect(surface.querySelector(".overlay-timeline__content")?.textContent).toBe(
      "",
    );
    expect(surface.textContent).not.toContain("原文将在这里实时出现");
    expect(surface.textContent).not.toContain("译文字幕等待音频输入");
  });

  it("keeps all history visible and does not snap down on wheel", async () => {
    let pipeline = createSubtitlePipelineState();
    for (let index = 1; index <= 5; index += 1) {
      pipeline = applySubtitleEvent(pipeline, {
        type: "subtitle.final",
        source_text: `Source ${index}.`,
        target_text: `译文 ${index}。`,
        sent_id: index,
        source_hash: index.toString(16).padStart(12, "0"),
        window_id: "main",
      });
    }
    const surface = renderTimeline(false, pipeline, {
      ...SETTINGS,
      visible_subtitle_count: 2,
    });
    expect(
      surface.querySelectorAll(".overlay-timeline-entry.is-final"),
    ).toHaveLength(5);

    const scroller = surface.querySelector(".overlay-timeline") as HTMLDivElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 700 },
      clientHeight: { configurable: true, value: 300 },
    });
    scroller.scrollTop = 120;
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll"));
      scroller.dispatchEvent(
        new WheelEvent("wheel", { deltaY: 80, bubbles: true }),
      );
    });
    expect(scroller.scrollTop).toBe(120);
  });

  it("shows dividers only between non-final-surface rows when enabled", () => {
    let pipeline = createSubtitlePipelineState();
    for (let index = 1; index <= 2; index += 1) {
      pipeline = applySubtitleEvent(pipeline, {
        type: "subtitle.final",
        source_text: `Source ${index}.`,
        target_text: `译文 ${index}。`,
        sent_id: index,
        source_hash: index.toString(16).padStart(12, "0"),
        window_id: "main",
      });
    }
    const surface = renderTimeline(false, pipeline, SETTINGS);
    let cards = surface.querySelectorAll(".overlay-timeline-entry");
    expect(cards[0].classList.contains("has-divider")).toBe(true);
    expect(cards[1].classList.contains("has-divider")).toBe(false);

    rerenderTimeline(pipeline, false, {
      ...SETTINGS,
      show_subtitle_dividers: false,
    });
    cards = surface.querySelectorAll(".overlay-timeline-entry");
    expect(
      surface.querySelectorAll(".overlay-timeline-entry.has-divider"),
    ).toHaveLength(0);

    rerenderTimeline(
      pipeline,
      false,
      { ...SETTINGS, show_subtitle_dividers: true },
      true,
    );
    expect(
      surface.querySelectorAll(".overlay-timeline-entry.has-divider"),
    ).toHaveLength(0);
  });

  it("keeps the same historical row in place when appending at the bottom", async () => {
    const surface = renderTimeline(false, finalizedPipeline(5));
    const scroller = surface.querySelector(".overlay-timeline") as HTMLDivElement;
    const content = surface.querySelector(
      ".overlay-timeline__content",
    ) as HTMLDivElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, writable: true, value: 700 },
      clientHeight: { configurable: true, value: 300 },
    });
    scroller.scrollTop = 120;
    await act(async () => {
      scroller.dispatchEvent(new Event("scroll"));
    });
    const historicalRow = surface.querySelector('[data-sent-id="1"]');
    expect(preserveBottomAppendAnchor(120)).toBe(120);

    Object.defineProperty(content, "scrollHeight", {
      configurable: true,
      writable: true,
      value: 600,
    });
    Object.defineProperty(scroller, "scrollHeight", {
      configurable: true,
      writable: true,
      value: 780,
    });
    Object.defineProperty(content, "scrollHeight", {
      configurable: true,
      writable: true,
      value: 680,
    });
    const appended = applySubtitleEvent(finalizedPipeline(5), {
      type: "subtitle.final",
      source_text: "Source 6.",
      target_text: "译文 6。",
      sent_id: 6,
      source_hash: "000000000006",
      window_id: "main",
    });
    rerenderTimeline(appended);

    expect(scroller.scrollTop).toBe(120);
    expect(surface.querySelector('[data-sent-id="1"]')).toBe(historicalRow);
  });

  it("does not remeasure 1000 historical cards for styling or deltas", () => {
    const pipeline = finalizedPipeline(1000);
    const settings = {
      ...SETTINGS,
      show_subtitle_dividers: false,
    };
    const rect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        x: 0,
        y: 0,
        width: 560,
        height: 64,
        top: 0,
        right: 560,
        bottom: 64,
        left: 0,
        toJSON: () => ({}),
      });
    try {
      const surface = renderTimeline(false, pipeline, settings);
      expect(
        surface.querySelectorAll(".overlay-timeline-entry.is-final"),
      ).toHaveLength(1000);
      rect.mockClear();

      const restyled = { ...settings, subtitle_color: "#EEEEEE" };
      rerenderTimeline(pipeline, false, restyled);
      expect(rect).toHaveBeenCalledTimes(0);

      const withDelta = applySubtitleEvent(pipeline, {
        type: "subtitle",
        source_text: "Live delta",
        target_text: "实时增量",
        is_final: false,
      });
      rerenderTimeline(withDelta, false, restyled);
      expect(rect).toHaveBeenCalledTimes(1);
    } finally {
      rect.mockRestore();
    }
  });
});

function livePipeline(source: string, target: string): SubtitlePipelineState {
  return applySubtitleEvent(createSubtitlePipelineState(), {
    type: "subtitle",
    source_text: source,
    target_text: target,
    is_final: false,
    window_id: "main",
  });
}

function finalizedTextPipeline(
  source: string,
  target: string,
  sentId = 1,
): SubtitlePipelineState {
  return applySubtitleEvent(createSubtitlePipelineState(), {
    type: "subtitle.final",
    source_text: source,
    target_text: target,
    sent_id: sentId,
    source_hash: sentId.toString(16).padStart(12, "0"),
    window_id: "main",
  });
}

async function measureTimelineViewport(
  surface: HTMLElement,
  clientHeight = 205,
): Promise<HTMLDivElement> {
  const scroller = surface.querySelector(".overlay-timeline") as HTMLDivElement;
  Object.defineProperties(scroller, {
    clientHeight: { configurable: true, value: clientHeight },
    scrollHeight: { configurable: true, value: clientHeight },
  });
  await act(async () => {
    window.dispatchEvent(new Event("resize"));
    await Promise.resolve();
  });
  return scroller;
}

function mockLaneGeometry(
  sourceHeight: number,
  targetHeight: number,
  shortLineHeight = 20,
) {
  return vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      let height = 64;
      const textLength = this.textContent?.length ?? 0;
      if (this.classList.contains("overlay-subtitle-line--source")) {
        height = textLength > 80 ? sourceHeight : shortLineHeight;
      } else if (this.classList.contains("overlay-subtitle-line--target")) {
        height = textLength > 80 ? targetHeight : shortLineHeight;
      }
      return {
        x: 0,
        y: 0,
        width: 400,
        height,
        top: 0,
        right: 400,
        bottom: height,
        left: 0,
        toJSON: () => ({}),
      };
    });
}

const R156_SETTINGS: UserSettings = {
  ...SETTINGS,
  hud_geometry: {
    ...SETTINGS.hud_geometry!,
    width: 437,
    height: 233,
  },
  source_font_size: 22,
  target_font_size: 33,
  line_height_multiplier: 1.32,
};

describe("subtitleLaneBudget & CompactLane overflow scroll", () => {
  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    root = null;
    container = null;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("derives the 437x233 bilingual budget and yields space to a short side", () => {
    expect(
      subtitleLaneBudget(
        "bilingual",
        true,
        true,
        205,
        22,
        33,
        1.32,
        { source: 29, target: 348 },
      ),
    ).toEqual({ source: 1, target: 4 });
    expect(
      subtitleLaneBudget(
        "bilingual",
        true,
        true,
        205,
        22,
        33,
        1.32,
        { source: 232, target: 44 },
      ),
    ).toEqual({ source: 5, target: 1 });
    expect(
      subtitleLaneBudget(
        "bilingual",
        true,
        true,
        205,
        22,
        33,
        1.32,
        { source: 232, target: 348 },
      ),
    ).toEqual({ source: 3, target: 2 });
    expect(
      subtitleLaneBudget("translation", true, true, 205, 22, 33, 1.32),
    ).toEqual({ source: 0, target: 4 });
    expect(
      subtitleLaneBudget("original", true, true, 205, 22, 33, 1.32),
    ).toEqual({ source: 7, target: 0 });
  });

  it("keeps a short source visible while only the long target lane scrolls", async () => {
    mockLaneGeometry(29, 200);
    const surface = renderTimeline(
      false,
      livePipeline("Short source.", "Long target ".repeat(40)),
      R156_SETTINGS,
    );
    await measureTimelineViewport(surface);
    await act(async () => {
      await Promise.resolve();
    });

    const source = surface.querySelector(
      '.overlay-lane-viewport[data-lane-role="source"]',
    ) as HTMLElement;
    const target = surface.querySelector(
      '.overlay-lane-viewport[data-lane-role="target"]',
    ) as HTMLElement;
    const targetLine = target.querySelector(
      ".overlay-subtitle-line--target",
    ) as HTMLElement;

    expect(source.dataset.laneOverflowed).toBe("false");
    expect(target.dataset.laneOverflowed).toBe("true");
    expect(target.dataset.laneMaxHeight).toBe("174");
    expect(targetLine.style.position).toBe("absolute");
    expect(targetLine.style.bottom).toBe("0px");
  });

  it("gives both long bilingual lanes independent bounded viewports", async () => {
    mockLaneGeometry(200, 200);
    const surface = renderTimeline(
      false,
      livePipeline("Long source ".repeat(40), "Long target ".repeat(40)),
      R156_SETTINGS,
    );
    await measureTimelineViewport(surface);
    await act(async () => {
      await Promise.resolve();
    });

    const source = surface.querySelector(
      '.overlay-lane-viewport[data-lane-role="source"]',
    ) as HTMLElement;
    const target = surface.querySelector(
      '.overlay-lane-viewport[data-lane-role="target"]',
    ) as HTMLElement;

    expect(source.dataset.laneOverflowed).toBe("true");
    expect(target.dataset.laneOverflowed).toBe("true");
    expect(source.dataset.laneMaxHeight).toBe("87");
    expect(target.dataset.laneMaxHeight).toBe("87");
  });

  it("expands every compact lane when the user wheels up for history", async () => {
    mockLaneGeometry(200, 200);
    const surface = renderTimeline(
      false,
      livePipeline("Long source ".repeat(40), "Long target ".repeat(40)),
      R156_SETTINGS,
    );
    const scroller = await measureTimelineViewport(surface);
    await act(async () => {
      await Promise.resolve();
    });
    expect(surface.querySelectorAll(".overlay-lane-viewport")).toHaveLength(2);

    await act(async () => {
      scroller.dispatchEvent(
        new WheelEvent("wheel", { deltaY: -24, bubbles: true }),
      );
    });

    expect(surface.querySelectorAll(".overlay-lane-viewport")).toHaveLength(0);
    expect(surface.querySelectorAll(".overlay-subtitle-line")).toHaveLength(2);
    expect(surface.querySelector(".timeline-return-live")).not.toBeNull();
  });

  it("expands compact lanes from the keyboard when the outer list cannot scroll", async () => {
    mockLaneGeometry(200, 200);
    const surface = renderTimeline(
      false,
      livePipeline("Long source ".repeat(40), "Long target ".repeat(40)),
      R156_SETTINGS,
    );
    const scroller = await measureTimelineViewport(surface);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      scroller.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }),
      );
    });

    expect(surface.querySelectorAll(".overlay-lane-viewport")).toHaveLength(0);
    expect(surface.querySelectorAll(".overlay-subtitle-line")).toHaveLength(2);
  });

  it("keeps the previous long row bounded when a new short row arrives", async () => {
    mockLaneGeometry(200, 200);
    const first = finalizedTextPipeline(
      "Long first source ".repeat(40),
      "Long first target ".repeat(40),
      1,
    );
    const surface = renderTimeline(false, first, R156_SETTINGS);
    await measureTimelineViewport(surface);
    await act(async () => {
      await Promise.resolve();
    });

    const next = applySubtitleEvent(first, {
      type: "subtitle.final",
      source_text: "Short next source.",
      target_text: "Short next target.",
      sent_id: 2,
      source_hash: "000000000002",
      window_id: "main",
    });
    rerenderTimeline(next, false, R156_SETTINGS);
    await act(async () => {
      window.dispatchEvent(new Event("resize"));
      await Promise.resolve();
    });

    const cards = surface.querySelectorAll(".overlay-timeline-entry");
    expect(cards).toHaveLength(2);
    const historicalSource = cards[0].querySelector(
      '[data-lane-role="source"]',
    ) as HTMLElement;
    expect(historicalSource.dataset.laneMeasuredHeight).toBe("200");
    expect(historicalSource.dataset.laneMaxHeight).toBe("87");
    expect(
      cards[0].querySelectorAll('[data-lane-overflowed="true"]').length,
    ).toBeGreaterThan(0);
    expect(
      cards[1].querySelectorAll('[data-lane-overflowed="true"]').length,
    ).toBe(0);
  });

  it("observes only the viewport and the current sentence lanes", async () => {
    let observerCount = 0;
    class CountingResizeObserver {
      constructor() {
        observerCount += 1;
      }

      observe() {}

      unobserve() {}

      disconnect() {}
    }
    vi.stubGlobal(
      "ResizeObserver",
      CountingResizeObserver as unknown as typeof ResizeObserver,
    );

    const surface = renderTimeline(
      false,
      finalizedPipeline(50),
      R156_SETTINGS,
    );
    await measureTimelineViewport(surface);
    await act(async () => {
      await Promise.resolve();
    });

    expect(surface.querySelectorAll(".overlay-timeline-entry")).toHaveLength(50);
    expect(observerCount).toBe(3);
  });
});
