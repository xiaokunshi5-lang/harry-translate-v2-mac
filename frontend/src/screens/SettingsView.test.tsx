// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  launchOverlay,
  isOverlayRealtimeActive,
} from "../shared/apiClient";
import { resetBackendEndpointCache } from "../lib/backendBridge";
import type {
  ConfigResponse,
  OverlayStatus,
  UserSettings,
} from "../shared/types";
import {
  configSyncOptions,
  fallbackUrlForLaunch,
  openBrowserFallback,
  SettingsView,
} from "./SettingsView";

const viewMocks = vi.hoisted(() => ({
  store: {} as Record<string, unknown>,
  sockets: [] as Array<{
    channel: string;
    options: {
      onPhase: (phase: string) => void;
      onEvent: (event: Record<string, unknown>) => void;
    };
  }>,
}));

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: () => viewMocks.store,
}));

vi.mock("../shared/wsClient", () => ({
  ChannelSocket: class {
    private readonly channel: string;
    private readonly options: {
      onPhase: (phase: string) => void;
      onEvent: (event: Record<string, unknown>) => void;
    };

    constructor(
      channel: string,
      options: {
        onPhase: (phase: string) => void;
        onEvent: (event: Record<string, unknown>) => void;
      },
    ) {
      this.channel = channel;
      this.options = options;
      viewMocks.sockets.push({ channel, options });
    }

    connect() {
      this.options.onPhase("open");
    }

    close() {}
  },
  transcriptSessionRecorder: {
    count: 0,
    currentSessionId: "session-test",
  },
}));

vi.mock("../components/settings/AudioLanguagePanel", () => ({
  AudioLanguagePanel: () => null,
}));
vi.mock("../components/settings/GeneralDiagnosticsPanel", () => ({
  GeneralDiagnosticsPanel: () => null,
}));
vi.mock("../components/settings/GlossaryInsightsPanel", () => ({
  GlossaryInsightsPanel: () => null,
}));
vi.mock("../components/settings/PreviewStage", () => ({
  PreviewStage: () => null,
}));
vi.mock("../components/settings/ProvidersPanel", () => ({
  ProvidersPanel: () => null,
}));
vi.mock("../components/settings/SubtitlePanel", () => ({
  SubtitlePanel: () => null,
}));
vi.mock("../components/status/LanguageStatusCapsule", () => ({
  LanguageStatusCapsule: () => null,
}));

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

const BASE_SETTINGS: UserSettings = {
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
  target_font_size: 18,
  subtitle_emphasis: "translation_primary",
  bilingual_layout_order: "source_top",
  glossary_enabled: true,
  glossary_wire_budget_bytes: 4096,
  overlay_window_mode: "single",
  overlay_immersive: false,
  overlay_click_through: true,
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

function config(needsSetup = false): ConfigResponse {
  return {
    settings: BASE_SETTINGS,
    user_settings: BASE_SETTINGS,
    key_mask: needsSetup ? "" : "sk-****test",
    api_key_masked: needsSetup ? "" : "sk-****test",
    needs_setup: needsSetup,
  };
}

function overlayStatus(
  overrides: Partial<OverlayStatus> = {},
): OverlayStatus {
  return {
    ok: true,
    service: "harry-translate",
    running: true,
    visible: false,
    realtime_enabled: false,
    registered: true,
    binary_available: true,
    control_url: "http://127.0.0.1:43210",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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

type RouteResult = Response | Promise<Response>;

function installFetch(options: {
  statuses?: OverlayStatus[];
  launch?: RouteResult;
  hide?: RouteResult;
}): ReturnType<typeof vi.fn> {
  const statuses = [...(options.statuses || [overlayStatus()])];
  const fetchMock = vi.fn(
    (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path = String(input);
      if (path.endsWith("/api/overlay/status")) {
        const next = statuses.length > 1 ? statuses.shift() : statuses[0];
        return Promise.resolve(jsonResponse(next || overlayStatus()));
      }
      if (path.endsWith("/api/session/latest")) {
        return Promise.resolve(jsonResponse({ ok: true, session: null }));
      }
      if (path.endsWith("/api/overlay/launch")) {
        return Promise.resolve(
          options.launch ||
            jsonResponse({
              ok: true,
              action: "shown",
              running: true,
              visible: true,
              realtime_enabled: true,
            }),
        );
      }
      if (path.endsWith("/api/overlay/hide")) {
        return Promise.resolve(
          options.hide ||
            jsonResponse({
              ok: true,
              action: "hidden",
              running: true,
              visible: false,
              realtime_enabled: false,
            }),
        );
      }
      throw new Error(`unexpected_fetch:${init?.method || "GET"}:${path}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function installStore(needsSetup = false): void {
  viewMocks.store = {
    status: "ready",
    saveState: "idle",
    error: "",
    config: config(needsSetup),
    draft: BASE_SETTINGS,
    catalog: null,
    health: { port: 8123 },
    testState: "idle",
    testResult: null,
    init: vi.fn(),
    flush: vi.fn(),
    applyImmediate: vi.fn(),
    syncExternalSettings: vi.fn(),
    saveSecret: vi.fn().mockResolvedValue(true),
    runConnectionTest: vi.fn(),
  };
}

function installNativeRuntime(): ReturnType<typeof vi.fn> {
  const invoke = vi.fn(async (command: string) => {
    if (command === "get_backend_endpoint") {
      return {
        host: "127.0.0.1",
        port: 8123,
        baseUrl: "http://127.0.0.1:8123",
        wsBaseUrl: "ws://127.0.0.1:8123",
        pid: 42,
      };
    }
    return undefined;
  });
  resetBackendEndpointCache();
  window.__TAURI_INTERNALS__ = {
    invoke: invoke as unknown as <T>(
      command: string,
      args?: Record<string, unknown>,
    ) => Promise<T>,
  };
  return invoke;
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

async function flushPromises(times = 4): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await Promise.resolve();
  }
}

async function renderSettings(): Promise<HTMLDivElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<SettingsView />);
    await flushPromises();
  });
  return container;
}

function realtimeSwitch(): HTMLButtonElement {
  const element = container?.querySelector<HTMLButtonElement>(
    'button[role="switch"][aria-label="实时翻译"]',
  );
  if (!element) throw new Error("realtime_switch_missing");
  return element;
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

  it("derives realtime activity from visible and explicit realtime state", () => {
    expect(
      isOverlayRealtimeActive({
        visible: true,
        realtime_enabled: true,
      }),
    ).toBe(true);
    expect(
      isOverlayRealtimeActive({
        visible: true,
      }),
    ).toBe(true);
    expect(
      isOverlayRealtimeActive({
        visible: true,
        realtime_enabled: false,
      }),
    ).toBe(false);
    expect(
      isOverlayRealtimeActive({
        visible: false,
        realtime_enabled: true,
      }),
    ).toBe(false);
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

describe("SettingsView authoritative realtime switch", () => {
  beforeEach(() => {
    viewMocks.sockets.length = 0;
    installStore();
  });

  afterEach(async () => {
    await act(async () => {
      root?.unmount();
      await flushPromises(2);
    });
    container?.remove();
    root = null;
    container = null;
    delete window.__TAURI_INTERNALS__;
    resetBackendEndpointCache();
    vi.unstubAllGlobals();
  });

  it("renders exactly one realtime switch and no legacy action pair", async () => {
    installFetch({});
    const rendered = await renderSettings();

    const switches = rendered.querySelectorAll(
      'button[role="switch"][aria-label="实时翻译"]',
    );
    expect(switches).toHaveLength(1);
    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("false");
    expect(realtimeSwitch().disabled).toBe(false);
    expect(rendered.textContent).not.toContain("启动实时翻译");
    expect(rendered.textContent).not.toContain("隐藏暂停");
    expect(viewMocks.sockets).toHaveLength(1);
    expect(viewMocks.sockets[0].channel).toBe("config");
    expect(
      viewMocks.sockets.some((socket) => socket.channel === "main"),
    ).toBe(false);
  });

  it("keeps the switch unchanged while launch is pending", async () => {
    const launch = deferred<Response>();
    installFetch({
      statuses: [
        overlayStatus(),
        overlayStatus({ visible: true, realtime_enabled: true }),
      ],
      launch: launch.promise,
    });
    await renderSettings();

    await act(async () => {
      realtimeSwitch().click();
      await flushPromises(1);
    });

    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("false");
    expect(realtimeSwitch().disabled).toBe(true);
    expect(realtimeSwitch().getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      launch.resolve(
        jsonResponse({
          ok: true,
          action: "shown",
          running: true,
          visible: true,
          realtime_enabled: true,
        }),
      );
      await flushPromises(6);
    });

    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("true");
    expect(realtimeSwitch().disabled).toBe(false);
    expect(realtimeSwitch().getAttribute("aria-busy")).toBeNull();
  });

  it("reconciles the real state and shows an alert after launch failure", async () => {
    installFetch({
      statuses: [overlayStatus(), overlayStatus()],
      launch: jsonResponse(
        { error: "control_failed", message: "原生控制失败" },
        502,
      ),
    });
    const rendered = await renderSettings();

    await act(async () => {
      realtimeSwitch().click();
      await flushPromises(6);
    });

    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("false");
    expect(realtimeSwitch().disabled).toBe(false);
    const alert = rendered.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain("原生控制失败");
    expect(alert?.textContent).toContain("已回退到关闭状态");
  });

  it("follows a passive overlay_status event without a page refresh", async () => {
    installFetch({});
    const rendered = await renderSettings();
    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("false");

    await act(async () => {
      viewMocks.sockets[0].options.onEvent({
        type: "overlay_status",
        overlay: overlayStatus({
          visible: true,
          realtime_enabled: true,
        }),
      });
      await flushPromises(1);
    });

    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("true");
    expect(rendered.textContent).toContain("实时翻译已开启");
  });

  it("disables launch with a clear reason when setup is incomplete", async () => {
    installStore(true);
    installFetch({});
    const rendered = await renderSettings();

    expect(realtimeSwitch().disabled).toBe(true);
    expect(rendered.textContent).toContain("配置密钥后可开启实时翻译");
  });

  it("turns off only after hide and the authoritative status agree", async () => {
    installFetch({
      statuses: [
        overlayStatus({ visible: true, realtime_enabled: true }),
        overlayStatus(),
      ],
      hide: jsonResponse({
        ok: true,
        action: "hidden",
        running: true,
        visible: false,
        realtime_enabled: false,
      }),
    });
    await renderSettings();
    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("true");

    await act(async () => {
      realtimeSwitch().click();
      await flushPromises(6);
    });

    expect(realtimeSwitch().getAttribute("aria-checked")).toBe("false");
    expect(container?.textContent).toContain("已待机（悬浮窗已隐藏）");
  });

  it("opens the auxiliary monitor through Tauri without navigating settings", async () => {
    installFetch({});
    const invoke = installNativeRuntime();
    const rendered = await renderSettings();
    const initialUrl = window.location.href;

    const auxiliaryButton = rendered.querySelector<HTMLButtonElement>(
      "button.settings-back--auxiliary",
    );
    expect(auxiliaryButton).not.toBeNull();
    expect(
      rendered.querySelector('a.settings-back--auxiliary'),
    ).toBeNull();

    await act(async () => {
      auxiliaryButton?.click();
      await flushPromises(2);
    });

    expect(invoke).toHaveBeenCalledWith(
      "open_auxiliary_monitor_in_browser",
      {},
    );
    expect(window.location.href).toBe(initialUrl);
  });

  it("routes the native exit button through the controlled quit command", async () => {
    installFetch({});
    const invoke = installNativeRuntime();
    vi.stubGlobal("confirm", vi.fn(() => true));
    const rendered = await renderSettings();

    const quitButton = Array.from(
      rendered.querySelectorAll<HTMLButtonElement>("button"),
    ).find((button) => button.textContent?.includes("退出应用"));
    expect(quitButton).toBeDefined();

    await act(async () => {
      quitButton?.click();
      await flushPromises(3);
    });

    expect(invoke).toHaveBeenCalledWith("app_quit", {});
    expect(quitButton?.getAttribute("aria-busy")).toBe("true");
  });
});
