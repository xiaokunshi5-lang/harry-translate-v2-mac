import { describe, expect, it } from "vitest";
import { sanitizeTargetForLang } from "./languagePurity";
import {
  computeResizeForOnlyMode,
  OVERLAY_METRICS,
  type OverlayFrame,
} from "../../windows/overlay/interaction/resizeIpc";

describe("sanitizeTargetForLang", () => {
  it("suppresses transient CJK echo for French while preserving accented characters", () => {
    expect(sanitizeTargetForLang("我们开始", "我们开始", "fr")).toBe("");
    expect(
      sanitizeTargetForLang("Commençons dès maintenant", "我们开始", "fr"),
    ).toBe("Commençons dès maintenant");
  });

  it("filters foreign echo for Chinese while retaining numeric technical terms", () => {
    expect(sanitizeTargetForLang("hello world", "hello world", "zh")).toBe("");
    expect(sanitizeTargetForLang("Qwen 3.8", "Qwen 3.8", "zh")).toBe("Qwen 3.8");
    expect(sanitizeTargetForLang("我们开始", "Let us begin", "zh")).toBe("我们开始");
  });

  it("preserves Japanese kana and Korean hangul scripts", () => {
    expect(sanitizeTargetForLang("こんにちは、開始します", "你好", "ja")).toBe(
      "こんにちは、開始します",
    );
    expect(sanitizeTargetForLang("안녕하세요", "你好", "ko")).toBe("안녕하세요");
    expect(sanitizeTargetForLang("España üöß", "西班牙", "es")).toBe("España üöß");
  });
});

describe("computeResizeForOnlyMode", () => {
  it("compensates window height around bottom anchor across 10 mode cycles without drift", () => {
    let frame: OverlayFrame = {
      x: 200,
      y: 120,
      width: OVERLAY_METRICS.DEFAULT_WIDTH,
      height: OVERLAY_METRICS.DEFAULT_HEIGHT,
    };
    const initialBottom = frame.y + frame.height;
    let baseHeight: number = frame.height;

    for (let cycle = 0; cycle < 10; cycle += 1) {
      const toTrans = computeResizeForOnlyMode(frame, "bilingual", "translation", baseHeight);
      frame = toTrans.frame;
      baseHeight = toTrans.bilingualBaseHeight;
      expect(frame.height).toBeLessThan(240);
      expect(frame.y + frame.height).toBe(initialBottom);

      const toOrig = computeResizeForOnlyMode(frame, "translation", "original", baseHeight);
      expect(toOrig.frame).toEqual(frame);
      frame = toOrig.frame;
      baseHeight = toOrig.bilingualBaseHeight;

      const backBi = computeResizeForOnlyMode(frame, "original", "bilingual", baseHeight);
      frame = backBi.frame;
      baseHeight = backBi.bilingualBaseHeight;
      expect(frame.height).toBe(240);
      expect(frame.y).toBe(120);
    }
  });
});
