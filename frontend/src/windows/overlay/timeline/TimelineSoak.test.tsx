// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applySubtitleEvent,
  applySubtitlePatch,
  createSubtitlePipelineState,
} from "../../../lib/subtitles/subtitlePipeline";
import type { UserSettings } from "../../../shared/types";
import { Timeline } from "./Timeline";

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
  show_subtitle_dividers: false,
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

let root: Root | null = null;
let container: HTMLDivElement | null = null;

describe("Timeline R17 soak", () => {
  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    root = null;
    container = null;
  });

  it("keeps 1000 finalized cards plus one draft after 1003 deliveries and a patch", () => {
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
      let pipeline = createSubtitlePipelineState();
      let lastHash = "";
      for (let index = 1; index <= 1003; index += 1) {
        lastHash = index.toString(16).padStart(12, "0").slice(-12);
        pipeline = applySubtitleEvent(pipeline, {
          type: "subtitle.final",
          source_text: `Source ${index}.`,
          target_text: `译文 ${index}。`,
          sent_id: index,
          source_hash: lastHash,
          para_id: `p${Math.ceil(index / 6)}`,
          window_id: "main",
          audio_source: "microphone",
        });
      }
      const patched = applySubtitlePatch(pipeline, {
        type: "transcript.patch",
        window_id: "main",
        sent_id: 1003,
        source_hash: lastHash,
        patched_source: "Source 1003 patched.",
        patched_target: "译文 1003 已纠偏。",
        new_source_hash: "abcdefabcdef",
      });
      expect(patched.applied).toBe(true);
      pipeline = applySubtitleEvent(patched.state, {
        type: "subtitle",
        source_text: "Live draft",
        target_text: "实时草稿",
        is_final: false,
        window_id: "main",
      });

      expect(pipeline.entries).toHaveLength(1000);
      expect(pipeline.entries[0].sentId).toBe(4);
      expect(pipeline.entries.at(-1)?.sentId).toBe(1003);
      expect(pipeline.draft?.final).toBe(false);

      act(() => {
        root?.render(
          <Timeline
            pipeline={pipeline}
            settings={SETTINGS}
            immersive={false}
            browserFallback={false}
          />,
        );
      });

      const surface = container as HTMLDivElement;
      const finals = surface.querySelectorAll(
        ".overlay-timeline-entry.is-final",
      );
      expect(finals).toHaveLength(1000);
      expect(
        surface.querySelectorAll(".overlay-timeline-entry"),
      ).toHaveLength(1001);
      expect((finals[0] as HTMLElement).dataset.sentId).toBe("4");
      expect((finals[999] as HTMLElement).dataset.sentId).toBe("1003");
      expect((finals[999] as HTMLElement).dataset.sourceHash).toBe(
        "abcdefabcdef",
      );
      expect((finals[999] as HTMLElement).textContent).toContain(
        "译文 1003 已纠偏。",
      );
      expect(surface.querySelector("audio")).toBeNull();
      const forbiddenMeter = ["us", "age"].join("");
      const forbiddenUntranslated = ["untranslated", "Words"].join("");
      const forbiddenTail = ["useSubtitle", "Tail"].join("");
      const forbiddenAudio = ["Audio", "Context"].join("");
      expect(surface.innerHTML).not.toMatch(
        new RegExp(
          [forbiddenMeter, forbiddenUntranslated, forbiddenTail, forbiddenAudio].join(
            "|",
          ),
          "i",
        ),
      );
    } finally {
      rect.mockRestore();
    }
  });
});
