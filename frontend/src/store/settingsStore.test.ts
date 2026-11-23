// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigResponse, UserSettings } from "../shared/types";

const api = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getHealth: vi.fn(),
  getProviderCatalog: vi.fn(),
  saveApiKey: vi.fn(),
  saveSettings: vi.fn(),
  testProvider: vi.fn(),
}));

vi.mock("../shared/apiClient", () => ({
  ApiError: class ApiError extends Error {
    readonly status = 400;
    readonly code = "request_failed";
  },
  getConfig: api.getConfig,
  getHealth: api.getHealth,
  getProviderCatalog: api.getProviderCatalog,
  saveApiKey: api.saveApiKey,
  saveSettings: api.saveSettings,
  testProvider: api.testProvider,
}));

const BASE_SETTINGS: UserSettings = {
  active_provider: "qwen",
  active_model: "qwen3.8-livetranslate-flash-realtime",
  provider: "qwen",
  model: "qwen3.8-livetranslate-flash-realtime",
  source_lang: "auto",
  target_lang: "zh",
  audio_input: "both",
  input_device: null,
  audio_device: null,
  workspace_id: "",
  font_size: 18,
  source_font_size: 15,
  target_font_size: 18,
  subtitle_emphasis: "translation_primary",
  bilingual_layout_order: "source_top",
  glossary_enabled: true,
  glossary_wire_budget_bytes: 4096,
  overlay_window_mode: "single",
  overlay_immersive: false,
  overlay_click_through: true,
  hud_geometry: {
    x: 200,
    y: 120,
    width: 580,
    height: 240,
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
  summary_mode: "bailian",
  summary_model: "qwen-turbo",
  summary_min_words: 120,
  summary_min_interval_sec: 30,
  ui_appearance: { density: "comfortable", accent: "teal" },
  windows: [],
};

function settings(overrides: Partial<UserSettings> = {}): UserSettings {
  return { ...BASE_SETTINGS, ...overrides };
}

function config(overrides: Partial<UserSettings> = {}): ConfigResponse {
  const next = settings(overrides);
  return {
    settings: next,
    user_settings: next,
    key_mask: "sk-****test",
    api_key_masked: "sk-****test",
    needs_setup: false,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function settlePromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

let moduleUnderTest: typeof import("./settingsStore");

describe("settingsStore realtime save coordinator", () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-23T12:00:00Z"));
    vi.clearAllMocks();
    vi.resetModules();
    api.getConfig.mockResolvedValue(config());
    api.getHealth.mockResolvedValue({
      status: "ok",
      port: 8000,
      python_version: "3.12",
      audio_backend: "sounddevice",
      audio_devices: [],
      audio_error: null,
      env_exists: true,
      needs_setup: false,
      key_mask: "sk-****test",
      permission_hint: "",
    });
    api.getProviderCatalog.mockResolvedValue(null);
    moduleUnderTest = await import("./settingsStore");
    await moduleUnderTest.useSettingsStore.getState().init();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("accumulates distinct patches while a request is in flight", async () => {
    const first = deferred<ConfigResponse>();
    const second = deferred<ConfigResponse>();
    api.saveSettings
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const store = moduleUnderTest.useSettingsStore;

    store.getState().applyImmediate({ source_font_size: 20 }, 60);
    await vi.advanceTimersByTimeAsync(60);
    expect(api.saveSettings).toHaveBeenCalledTimes(1);
    expect(api.saveSettings.mock.calls[0][0]).toEqual({
      source_font_size: 20,
    });

    store.getState().applyImmediate({ subtitle_color: "#4FD1C5" }, 0);
    store.getState().applyImmediate({ line_height_multiplier: 1.5 }, 60);
    await vi.advanceTimersByTimeAsync(60);
    expect(api.saveSettings).toHaveBeenCalledTimes(1);
    expect(store.getState().draft).toMatchObject({
      subtitle_color: "#4FD1C5",
      line_height_multiplier: 1.5,
    });

    first.resolve(config({ source_font_size: 20 }));
    await settlePromises();
    expect(api.saveSettings).toHaveBeenCalledTimes(2);
    expect(api.saveSettings.mock.calls[1][0]).toEqual({
      subtitle_color: "#4FD1C5",
      line_height_multiplier: 1.5,
    });

    second.resolve(
      config({
        source_font_size: 20,
        subtitle_color: "#4FD1C5",
        line_height_multiplier: 1.5,
      }),
    );
    await settlePromises();
    expect(store.getState().saveState).toBe("saved");
  });

  it("does not let an older response pull a newer slider draft backwards", async () => {
    const first = deferred<ConfigResponse>();
    const second = deferred<ConfigResponse>();
    api.saveSettings
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const store = moduleUnderTest.useSettingsStore;

    store.getState().applyImmediate({ source_font_size: 20 }, 60);
    await vi.advanceTimersByTimeAsync(60);
    store.getState().applyImmediate({ source_font_size: 28 }, 60);
    await vi.advanceTimersByTimeAsync(60);

    first.resolve(config({ source_font_size: 20 }));
    await settlePromises();
    expect(store.getState().draft?.source_font_size).toBe(28);
    expect(api.saveSettings.mock.calls[1][0]).toEqual({
      source_font_size: 28,
    });

    second.resolve(config({ source_font_size: 28 }));
    await settlePromises();
    expect(store.getState().draft?.source_font_size).toBe(28);
    expect(store.getState().saveState).toBe("saved");
  });

  it("applies external tray settings without overwriting a dirty local field", () => {
    const store = moduleUnderTest.useSettingsStore;
    store.getState().applyImmediate({ source_font_size: 20 }, 60);

    store.getState().syncExternalSettings(
      settings({
        source_font_size: 18,
        target_lang: "ja",
        subtitle_color: "#FFD54F",
      }),
    );

    expect(store.getState().draft).toMatchObject({
      source_font_size: 20,
      target_lang: "ja",
      subtitle_color: "#FFD54F",
    });
    expect(store.getState().config?.user_settings).toMatchObject({
      source_font_size: 20,
      target_lang: "ja",
    });
  });

  it("syncs key readiness and mask from the passive config event", () => {
    const store = moduleUnderTest.useSettingsStore;
    store.setState({
      config: {
        ...config(),
        key_mask: "",
        api_key_masked: "",
        needs_setup: true,
      },
    });

    store.getState().syncExternalSettings(settings(), {
      needsSetup: false,
      keyMask: "sk-****ready",
    });

    expect(store.getState().config).toMatchObject({
      needs_setup: false,
      key_mask: "sk-****ready",
      api_key_masked: "sk-****ready",
    });
  });

  it("keeps a failed patch available for explicit retry", async () => {
    api.saveSettings
      .mockRejectedValueOnce(new Error("network_down"))
      .mockResolvedValueOnce(config({ source_font_size: 20 }));
    const store = moduleUnderTest.useSettingsStore;

    store.getState().applyImmediate({ source_font_size: 20 }, 0);
    await vi.advanceTimersByTimeAsync(0);
    await settlePromises();
    expect(store.getState().saveState).toBe("error");
    expect(api.saveSettings).toHaveBeenCalledTimes(1);

    store.getState().flush();
    await settlePromises();
    expect(api.saveSettings).toHaveBeenCalledTimes(2);
    expect(api.saveSettings.mock.calls[1][0]).toEqual({
      source_font_size: 20,
    });
    expect(store.getState().draft?.source_font_size).toBe(20);
    expect(store.getState().saveState).toBe("saved");
  });

  it("honors immediate, slider, and text debounce deadlines", async () => {
    api.saveSettings.mockImplementation(async (patch: Partial<UserSettings>) =>
      config(patch),
    );
    const store = moduleUnderTest.useSettingsStore;

    store.getState().applyImmediate({ workspace_id: "alpha" }, 300);
    await vi.advanceTimersByTimeAsync(299);
    expect(api.saveSettings).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(api.saveSettings).toHaveBeenCalledTimes(1);
    await settlePromises();

    store.getState().applyImmediate({ show_subtitle_dividers: false }, 0);
    await vi.advanceTimersByTimeAsync(0);
    expect(api.saveSettings).toHaveBeenCalledTimes(2);
    await settlePromises();

    store.getState().applyImmediate({ source_font_size: 22 }, 60);
    await vi.advanceTimersByTimeAsync(59);
    expect(api.saveSettings).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.saveSettings).toHaveBeenCalledTimes(3);
  });
});
