// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { UserSettings } from "../../shared/types";
import { SubtitlePanel } from "./SubtitlePanel";

const DRAFT = {
  overlay_immersive: false,
  is_overlay_locked: false,
  subtitle_display_mode: "bilingual",
  show_subtitle_dividers: true,
  line_height_multiplier: 1.32,
  subtitle_alignment: "left",
  source_font_size: 15,
  target_font_size: 18,
  subtitle_emphasis: "translation_primary",
  bilingual_layout_order: "source_top",
  background_opacity: 80,
  subtitle_color: "#FFFFFF",
  mic_subtitle_color: "#FFD54F",
  system_subtitle_color: "#4FD1C5",
  glossary_enabled: true,
  glossary_wire_budget_bytes: 4096,
  visible_subtitle_count: 3,
} as UserSettings;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

describe("SubtitlePanel history controls", () => {
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

  it("keeps the compatibility field but removes the display-count control", async () => {
    await act(async () => {
      root?.render(<SubtitlePanel draft={DRAFT} onImmediate={() => undefined} />);
    });

    expect(container?.textContent).not.toContain("显示条数");
    expect(container?.textContent).toContain("行距");
    expect(DRAFT.visible_subtitle_count).toBe(3);
  });
});
