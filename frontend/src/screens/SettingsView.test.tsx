// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { launchOverlay } from "../shared/apiClient";
import {
  configSyncOptions,
  fallbackUrlForLaunch,
  openBrowserFallback,
} from "./SettingsView";

function fallbackHost(opened: Window | null) {
  return {
    open: vi.fn(
      (
        _url?: string | URL,
        _target?: string,
        _features?: string,
      ): Window | null => opened,
    ),
    location: { assign: vi.fn((_url: string) => undefined) },
  };
}

describe("settings config and overlay fallback", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("maps passive config metadata to settings-store options", () => {
    expect(
      configSyncOptions({
        needs_setup: false,
        key_mask: "sk-****ready",
      }),
    ).toEqual({
      needsSetup: false,
      keyMask: "sk-****ready",
    });
  });

  it("uses the backend fallback URL and defaults only for browser fallback", () => {
    expect(
      fallbackUrlForLaunch({
        action: "browser_fallback",
        fallback_url: "/custom-hud",
      }),
    ).toBe("/custom-hud");
    expect(
      fallbackUrlForLaunch({
        action: "browser_fallback",
      }),
    ).toBe("/hud?fallback=browser");
    expect(
      fallbackUrlForLaunch({
        action: "shown",
        fallback_url: "/must-not-open",
      }),
    ).toBeNull();
  });

  it("really opens a new fallback window and hardens opener access", () => {
    const opened = {
      opener: {},
      focus: vi.fn(),
    } as unknown as Window;
    const host = fallbackHost(opened);

    expect(openBrowserFallback("/hud?fallback=browser", host)).toBe("new_tab");
    expect(host.open).toHaveBeenCalledWith(
      "/hud?fallback=browser",
      "harry-translate-browser-fallback",
    );
    expect(opened.opener).toBeNull();
    expect(host.location.assign).not.toHaveBeenCalled();
  });

  it("falls back to same-tab navigation when the popup is blocked", () => {
    const host = fallbackHost(null);

    expect(openBrowserFallback("/hud?fallback=browser", host)).toBe("same_tab");
    expect(host.location.assign).toHaveBeenCalledWith(
      "/hud?fallback=browser",
    );
  });

  it("preserves fallback_url in the launch API contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: true,
            action: "browser_fallback",
            fallback_url: "/hud?fallback=browser",
            running: false,
            visible: false,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    const result = await launchOverlay();
    expect(result.action).toBe("browser_fallback");
    expect(result.fallback_url).toBe("/hud?fallback=browser");
  });
});
