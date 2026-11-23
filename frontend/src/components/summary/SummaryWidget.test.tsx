// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetBackendEndpointCache } from "../../lib/backendBridge";
import type { SummarySnapshot } from "../../shared/types";
import { SummaryWidget } from "./SummaryWidget";

const SNAPSHOT: SummarySnapshot = {
  enabled: true,
  mode: "bailian",
  model: "qwen-turbo",
  engine_used: "bailian",
  pending_words: 0,
  pinned: [
    { level: 5, text: "项目必须在十二月上线。", category: "decision" },
    { level: 4, text: "目标是增长20%。", category: "metric" },
  ],
  archived: [
    { level: 2, text: "背景情况属于辅助信息。", category: "detail" },
  ],
};

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

async function renderWidget(enabled: boolean, snapshot = SNAPSHOT) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/summary/refresh")) return response(snapshot);
    return response(snapshot);
  });
  vi.stubGlobal("fetch", fetchMock);
  resetBackendEndpointCache();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<SummaryWidget enabled={enabled} />);
    await Promise.resolve();
    await Promise.resolve();
  });
  return { fetchMock, surface: container };
}

describe("SummaryWidget", () => {
  beforeEach(() => {
    resetBackendEndpointCache();
  });

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    root = null;
    container = null;
    vi.unstubAllGlobals();
  });

  it("renders no DOM and performs no summary request while disabled", async () => {
    const { fetchMock, surface } = await renderWidget(false);
    expect(surface.querySelector("[data-summary-widget]")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows a 48px icon and opens pinned points before archived details", async () => {
    const { surface } = await renderWidget(true);
    const icon = surface.querySelector(".summary-icon") as HTMLButtonElement;
    expect(icon).toBeTruthy();
    expect(icon.getAttribute("aria-label")).toBe("打开重点总结");
    expect(surface.querySelector(".summary-icon__badge")?.textContent).toBe("2");

    await act(async () => {
      icon.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(surface.querySelector('[data-summary-widget="expanded"]')).toBeTruthy();
    const pinned = surface.querySelectorAll(".summary-point--pinned");
    expect(pinned).toHaveLength(2);
    expect(pinned[0].textContent).toContain("项目必须在十二月上线");
    expect(surface.querySelector(".summary-archive")?.textContent).toContain(
      "辅助信息",
    );
    expect(surface.querySelectorAll("audio")).toHaveLength(0);
    expect(surface.textContent).not.toMatch(/Token|usage|费用|花费/i);
    expect(surface.outerHTML).not.toContain("Audio" + "Context");
  });

  it("replaces unread badge when a live update arrives", async () => {
    const { surface } = await renderWidget(true);
    await act(async () => {
      root?.render(
        <SummaryWidget
          enabled
          liveSnapshot={{
            enabled: true,
            mode: "rules",
            engine_used: "rules",
            pinned: [
              { level: 5, text: "新的核心结论。", category: "decision" },
            ],
            archived: [],
          }}
        />,
      );
      await Promise.resolve();
    });
    expect(surface.querySelector(".summary-icon__badge")?.textContent).toBe("1");
    expect(surface.textContent).not.toMatch(/Token|usage|费用|花费/i);
  });

  it("keeps the panel visible when backend shutdown fails", async () => {
    const { fetchMock, surface } = await renderWidget(true);
    await act(async () => {
      surface
        .querySelector(".summary-icon")
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    fetchMock.mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        return new Response(JSON.stringify({ error: "offline" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });
      }
      return response(SNAPSHOT);
    });
    await act(async () => {
      surface
        .querySelector('button[aria-label="彻底关闭总结"]')
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(surface.querySelector('[data-summary-widget="expanded"]')).toBeTruthy();
    expect(surface.textContent).toContain("关闭失败");
  });
});
