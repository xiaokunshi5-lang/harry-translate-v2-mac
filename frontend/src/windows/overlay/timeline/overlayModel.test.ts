import { describe, expect, it } from "vitest";
import {
  applySubtitleEvent,
  createSubtitlePipelineState,
} from "../../../lib/subtitles/subtitlePipeline";
import {
  emphasisStyle,
  timelineEntries,
} from "./overlayModel";

describe("overlayModel", () => {
  it("returns all retained history regardless of visible_subtitle_count", () => {
    let state = createSubtitlePipelineState();
    for (let index = 1; index <= 5; index += 1) {
      state = applySubtitleEvent(state, {
        type: "subtitle.final",
        source_text: `Source ${index}.`,
        target_text: `译文 ${index}。`,
        sent_id: index,
        source_hash: index.toString(16).padStart(12, "0"),
        window_id: "main",
      });
    }

    expect(timelineEntries(state, 2)).toHaveLength(5);
    expect(timelineEntries(state, 1).map((entry) => entry.sentId)).toEqual([
      1, 2, 3, 4, 5,
    ]);
  });

  it("keeps the established emphasis weights unchanged", () => {
    expect(emphasisStyle("source", "translation_primary")).toEqual({
      fontWeight: 400,
      opacity: 0.74,
    });
    expect(emphasisStyle("target", "translation_primary")).toEqual({
      fontWeight: 600,
      opacity: 1,
    });
    expect(emphasisStyle("source", "balanced")).toEqual({
      fontWeight: 500,
      opacity: 0.92,
    });
  });
});
