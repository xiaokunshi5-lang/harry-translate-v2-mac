import { describe, expect, it } from "vitest";
import {
  IMMERSIVE_TEXT_SHADOW,
  subtitleBackgroundColor,
} from "./subtitleAppearance";

describe("subtitleAppearance", () => {
  it("maps every 1-100 opacity value to linear pure black", () => {
    for (let opacity = 1; opacity <= 100; opacity += 1) {
      expect(subtitleBackgroundColor(opacity)).toBe(
        `rgba(0, 0, 0, ${Number((opacity / 100).toFixed(2))})`,
      );
    }
  });

  it("clamps out-of-range values and exposes three immersive shadows", () => {
    expect(subtitleBackgroundColor(-20)).toBe("rgba(0, 0, 0, 0.01)");
    expect(subtitleBackgroundColor(900)).toBe("rgba(0, 0, 0, 1)");
    expect(IMMERSIVE_TEXT_SHADOW.split("), ")).toHaveLength(3);
    expect(IMMERSIVE_TEXT_SHADOW).toBe(
      "0 2px 5px rgba(0, 0, 0, 0.98), 0 0 2px rgba(0, 0, 0, 0.95), 0 0 12px rgba(0, 0, 0, 0.72)",
    );
  });
});
