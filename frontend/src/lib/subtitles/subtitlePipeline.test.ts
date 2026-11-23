import { describe, expect, it } from "vitest";
import {
  applySubtitleEvent,
  applySubtitlePatch,
  createSubtitlePipelineState,
  MAX_TIMELINE_ENTRIES,
  longestCommonPrefix,
  splitAtStablePrefix,
} from "./subtitlePipeline";

describe("subtitlePipeline", () => {
  it("keeps the stable character prefix and exposes only the tail delta", () => {
    expect(longestCommonPrefix("Hello wor", "Hello world")).toBe(9);
    expect(splitAtStablePrefix("Hello wor", "Hello world")).toEqual({
      stable: "Hello wor",
      tail: "ld",
      prefixLength: 9,
    });
    expect(splitAtStablePrefix("", "首字")).toEqual({
      stable: "",
      tail: "首字",
      prefixLength: 0,
    });
  });

  it("keeps a single main draft and stores the previous text for LCP", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Hel",
      target_text: "你",
      is_final: false,
    });
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Hello",
      target_text: "你好",
      is_final: false,
    });
    expect(state.entries).toHaveLength(0);
    expect(state.draft?.source).toBe("Hello");
    expect(state.draft?.previousSource).toBe("Hel");
    expect(state.draft?.previousTarget).toBe("你");
  });

  it("merges a final fragment into the matched main card", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "We talked about the",
      target_text: "我们谈到了",
      is_final: true,
      sent_id: 1,
      source_hash: "111111111111",
      para_id: "p1",
      window_id: "main",
      audio_source: "microphone",
    });
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "We talked about the project scope.",
      target_text: "我们谈到了项目范围。",
      is_final: true,
      sent_id: 2,
      source_hash: "222222222222",
      merged_with_prev_id: 1,
      para_id: "p1",
      window_id: "main",
      audio_source: "microphone",
    });

    expect(state.entries).toHaveLength(1);
    expect(state.entries[0].sentId).toBe(1);
    expect(state.entries[0].sourceHash).toBe("222222222222");
    expect(state.entries[0].source).toContain("project scope");
    expect(state.lastSentId).toBe(2);
  });

  it("stamps final entries as main without metering fields", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Fresh sentence.",
      target_text: "新句子。",
      is_final: true,
      sent_id: 3,
      source_hash: "abcdef123456",
      para_id: "p2",
      window_id: "main",
      audio_source: "system",
    });
    const entry = state.entries[0];
    expect(entry.windowId).toBe("main");
    expect(entry.audioSource).toBe("system");
    expect(entry.sentId).toBe(3);
    expect(entry.sourceHash).toBe("abcdef123456");
    expect(entry.paraId).toBe("p2");
    expect("usage" in entry).toBe(false);
  });

  it("treats duplicate final ids as replays instead of new rows", () => {
    const event = {
      type: "subtitle",
      source_text: "Fresh sentence.",
      target_text: "新句子。",
      is_final: true,
      sent_id: 3,
      source_hash: "abcdef123456",
      para_id: "p1",
      window_id: "main",
      audio_source: "microphone",
    };
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, event);
    state = applySubtitleEvent(state, event);
    expect(state.entries).toHaveLength(1);
    expect(state.entries[0].sentId).toBe(3);
    expect(state.lastSentId).toBe(3);
  });

  it("applies a matching patch to a finalized card without touching the draft", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "We use the old term.",
      target_text: "我们使用旧术语。",
      is_final: true,
      sent_id: 42,
      source_hash: "a1b2c3d4e5f6",
      para_id: "p4",
      window_id: "main",
      audio_source: "microphone",
    });
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "The next sentence is still",
      target_text: "下一句仍在",
      is_final: false,
    });
    const draft = state.draft;
    const revision = state.revision;

    const result = applySubtitlePatch(state, {
      type: "transcript.patch",
      window_id: "main",
      sent_id: 42,
      source_hash: "a1b2c3d4e5f6",
      patched_source: "We use the corrected term.",
      patched_target: "我们使用修正后的术语。",
      new_source_hash: "fedcba654321",
    });

    expect(result.applied).toBe(true);
    expect(result.state.entries[0]).toMatchObject({
      sentId: 42,
      source: "We use the corrected term.",
      target: "我们使用修正后的术语。",
      previousSource: "We use the old term.",
      previousTarget: "我们使用旧术语。",
      sourceHash: "fedcba654321",
    });
    expect(result.state.revision).toBe(revision + 1);
    expect(result.state.draft).toBe(draft);
    expect(result.state.stalePatchDropped).toBe(0);
    expect(state.entries[0].source).toBe("We use the old term.");
  });

  it("silently counts wrong hashes, missing cards, and draft matches", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Final source.",
      target_text: "定稿译文。",
      is_final: true,
      sent_id: 5,
      source_hash: "111111111111",
      para_id: "p1",
      window_id: "main",
      audio_source: "microphone",
    });
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Draft source.",
      target_text: "草稿译文。",
      is_final: false,
    });
    const entries = state.entries;
    const draft = state.draft;
    const revision = state.revision;

    const wrongHash = applySubtitlePatch(state, {
      type: "transcript.patch",
      window_id: "main",
      sent_id: 5,
      source_hash: "000000000000",
      patched_source: "Wrong.",
      patched_target: "错误。",
      new_source_hash: "222222222222",
    });
    expect(wrongHash.applied).toBe(false);
    expect(wrongHash.state.entries).toBe(entries);
    expect(wrongHash.state.draft).toBe(draft);
    expect(wrongHash.state.revision).toBe(revision);
    expect(wrongHash.state.stalePatchDropped).toBe(1);

    const missingCard = applySubtitlePatch(wrongHash.state, {
      type: "transcript.patch",
      window_id: "main",
      sent_id: 999,
      source_hash: "111111111111",
      patched_source: "Missing.",
      patched_target: "不存在。",
      new_source_hash: "333333333333",
    });
    expect(missingCard.applied).toBe(false);
    expect(missingCard.state.entries).toBe(entries);
    expect(missingCard.state.draft).toBe(draft);
    expect(missingCard.state.revision).toBe(revision);
    expect(missingCard.state.stalePatchDropped).toBe(2);

    const wrongWindow = applySubtitlePatch(missingCard.state, {
      type: "transcript.patch",
      window_id: "secondary",
      sent_id: 5,
      source_hash: "111111111111",
      patched_source: "Wrong window.",
      patched_target: "错误窗口。",
      new_source_hash: "444444444444",
    });
    expect(wrongWindow.applied).toBe(false);
    expect(wrongWindow.state.stalePatchDropped).toBe(3);

    const draftMatch = applySubtitlePatch(wrongWindow.state, {
      type: "transcript.patch",
      window_id: "main",
      sent_id: 0,
      source_hash: "",
      patched_source: "Draft changed.",
      patched_target: "草稿被改。",
      new_source_hash: "555555555555",
    });
    expect(draftMatch.applied).toBe(false);
    expect(draftMatch.state.entries).toBe(entries);
    expect(draftMatch.state.draft).toBe(draft);
    expect(draftMatch.state.revision).toBe(revision);
    expect(draftMatch.state.stalePatchDropped).toBe(4);
    expect(JSON.stringify(draftMatch.state)).not.toContain("usage");
  });

  it("accepts only one target-only patch for a stable source hash", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Stable source.",
      target_text: "旧译文。",
      is_final: true,
      sent_id: 8,
      source_hash: "aaaaaaaaaaaa",
      para_id: "p1",
      window_id: "main",
      audio_source: "microphone",
    });
    const first = applySubtitlePatch(state, {
      type: "transcript.patch",
      window_id: "main",
      sent_id: 8,
      source_hash: "aaaaaaaaaaaa",
      patched_source: "Stable source.",
      patched_target: "新译文。",
      new_source_hash: "aaaaaaaaaaaa",
    });
    expect(first.applied).toBe(true);
    expect(first.state.entries[0].patchVersion).toBe(1);

    const second = applySubtitlePatch(first.state, {
      type: "transcript.patch",
      window_id: "main",
      sent_id: 8,
      source_hash: "aaaaaaaaaaaa",
      patched_source: "Stable source.",
      patched_target: "更晚但过期的译文。",
      new_source_hash: "aaaaaaaaaaaa",
    });
    expect(second.applied).toBe(false);
    expect(second.state.entries[0].target).toBe("新译文。");
    expect(second.state.stalePatchDropped).toBe(1);
  });

  it("keeps finalized entries hard bounded for long sessions", () => {
    let state = createSubtitlePipelineState();
    for (let index = 1; index <= MAX_TIMELINE_ENTRIES + 3; index += 1) {
      state = applySubtitleEvent(state, {
        type: "subtitle",
        source_text: `Source ${index}.`,
        target_text: `译文 ${index}。`,
        is_final: true,
        sent_id: index,
        source_hash: index.toString(16).padStart(12, "0").slice(-12),
        para_id: "p1",
        window_id: "main",
        audio_source: "microphone",
      });
    }
    expect(state.entries).toHaveLength(MAX_TIMELINE_ENTRIES);
    expect(state.entries[0].sentId).toBe(4);
    expect(state.entries.at(-1)?.sentId).toBe(MAX_TIMELINE_ENTRIES + 3);
  });

  it("ignores events whose source and target are both blank", () => {
    let state = createSubtitlePipelineState();
    state = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "Still speaking",
      target_text: "仍在说话",
      is_final: false,
    });
    const draft = state.draft;
    const revision = state.revision;

    const blankDraft = applySubtitleEvent(state, {
      type: "subtitle",
      source_text: "   ",
      target_text: "",
      is_final: false,
    });
    expect(blankDraft).toBe(state);
    expect(blankDraft.draft).toBe(draft);
    expect(blankDraft.revision).toBe(revision);

    const blankFinal = applySubtitleEvent(state, {
      type: "subtitle.final",
      source_text: "",
      target_text: "  ",
    });
    expect(blankFinal).toBe(state);
    expect(blankFinal.entries).toHaveLength(0);
    expect(blankFinal.lastSentId).toBe(0);
  });
});
