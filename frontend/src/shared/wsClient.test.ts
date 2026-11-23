// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import {
  TranscriptSessionRecorder,
  type TranscriptSessionSnapshot,
} from "./wsClient";

function fakeStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

describe("TranscriptSessionRecorder", () => {
  it("records final main events and applies a matching patch", () => {
    let clock = 1_000;
    const recorder = new TranscriptSessionRecorder({
      storage: fakeStorage(),
      now: () => clock++,
    });
    recorder.observe({
      type: "subtitle.final",
      window_id: "main",
      sent_id: 7,
      source_hash: "a1b2c3d4e5f6",
      source_text: "Hello world.",
      target_text: "你好，世界。",
      audio_source: "microphone",
      is_final: true,
    });
    recorder.observe({
      type: "transcript.patch",
      window_id: "main",
      sent_id: 7,
      source_hash: "a1b2c3d4e5f6",
      new_source_hash: "123456789abc",
      patched_source: "Hello, corrected world.",
      patched_target: "你好，修正后的世界。",
    });
    const snapshot = recorder.snapshot(2_000);
    expect(snapshot.entries).toHaveLength(1);
    expect(snapshot.entries[0].patched_target).toBe("你好，修正后的世界。");
    expect(snapshot.entries[0].source_hash).toBe("123456789abc");
    expect(snapshot.entries[0].window_id).toBe("main");
  });

  it("debounces a local save and posts only the current session", async () => {
    vi.useFakeTimers();
    try {
      let clock = 10_000;
      const fetcher = vi.fn().mockResolvedValue({ ok: true });
      const recorder = new TranscriptSessionRecorder({
        storage: fakeStorage(),
        now: () => clock++,
        fetcher: fetcher as unknown as typeof fetch,
        debounceMs: 2_000,
      });
      recorder.observe({
        type: "subtitle.final",
        window_id: "main",
        sent_id: 1,
        source_text: "Source",
        target_text: "译文",
        is_final: true,
      });
      expect(fetcher).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(2_000);
      await Promise.resolve();
      await Promise.resolve();
      expect(fetcher).toHaveBeenCalledTimes(1);
      const body = JSON.parse(
        String((fetcher.mock.calls[0][1] as RequestInit).body),
      ) as TranscriptSessionSnapshot;
      expect(body.entries).toHaveLength(1);
      const forbiddenMeterKey = ["usage", "stats"].join("_");
      expect(body).not.toHaveProperty(forbiddenMeterKey);
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts a fresh session after a reset", () => {
    const recorder = new TranscriptSessionRecorder({
      storage: fakeStorage(),
      now: () => Date.now(),
    });
    recorder.observe({
      type: "subtitle.final",
      window_id: "main",
      sent_id: 1,
      source_text: "one",
      target_text: "一",
      is_final: true,
    });
    const previous = recorder.currentSessionId;
    recorder.observe({ type: "session_reset" });
    expect(recorder.currentSessionId).not.toBe(previous);
    expect(recorder.count).toBe(0);
  });

  it("keeps the previous session snapshot when reset happens during a save", async () => {
    let resolveFetch: ((value: { ok: boolean }) => void) | undefined;
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const recorder = new TranscriptSessionRecorder({
      storage: fakeStorage(),
      now: () => Date.now(),
      fetcher: fetcher as unknown as typeof fetch,
    });
    recorder.observe({
      type: "subtitle.final",
      window_id: "main",
      sent_id: 1,
      source_text: "old",
      target_text: "旧",
      is_final: true,
    });
    const previous = recorder.currentSessionId;
    recorder.observe({ type: "session_reset" });
    await Promise.resolve();
    await Promise.resolve();
    const firstBody = JSON.parse(
      String((fetcher.mock.calls[0][1] as RequestInit).body),
    ) as TranscriptSessionSnapshot;
    expect(firstBody.id).toBe(previous);
    resolveFetch?.({ ok: true });
    await Promise.resolve();
    await Promise.resolve();
    expect(recorder.count).toBe(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("creates an export URL for the requested format", async () => {
    const recorder = new TranscriptSessionRecorder({
      storage: fakeStorage(),
      now: () => 1,
    });
    const url = await recorder.exportUrl("srt", "session_demo");
    expect(url).toContain("/api/export");
    expect(url).toContain("session_id=session_demo");
    expect(url).toContain("format=srt");
  });
});
